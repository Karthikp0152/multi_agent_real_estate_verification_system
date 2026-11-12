"""HTTP API for running a land check from a browser.

This suits the workflow better than the CLI does. A real check is not
one sitting: you start it, fetch a KUDA screenshot today, sit through
eCourts captchas next week, and come back. A terminal prompt cannot
span that; a URL plus a persistent checkpoint can.

The endpoints mirror the orchestrator one-for-one:
    POST /api/checks                      -> start one, get a check id
    GET  /api/checks/{id}                 -> what it's still waiting on
    POST /api/checks/{id}/uploads/{iid}   -> supply one file, continue
    GET  /api/checks/{id}/report.json     -> the finished report
    GET  /api/checks/{id}/report.html     -> the readable version

Known limitation, deliberate for now: extraction runs inline, and a
model call takes ~20s, so an upload request blocks for about that long.
Correct but not pleasant. The fix is a background worker returning
202 Accepted, which is a bigger change than it looks (job state, polling
or websockets) and is not worth building before the extraction itself is
validated against real pages.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from bhoomicheck.agents.document_analysis import DocumentAnalysisAgent
from bhoomicheck.api.storage import check_dir_for, save_upload
from bhoomicheck.api.ui import PAGE
from bhoomicheck.llm.gemini import GeminiProvider
from bhoomicheck.orchestrator.graph import Orchestrator, PendingUpload, sqlite_checkpointer
from bhoomicheck.report.html import render_html
from bhoomicheck.schemas.litigation import LitigationSearchQuery
from bhoomicheck.schemas.owner_background import OwnerBackgroundQuery
from bhoomicheck.geo.boundary import BoundaryParseError, parse_boundary
from bhoomicheck.schemas.geo import LatLon
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.risk_report import RiskReport

DATA_ROOT = Path(os.environ.get("BHOOMICHECK_DATA", Path.home() / ".bhoomicheck"))


class CheckStatus(BaseModel):
    """What a caller needs to know to drive a check forward.

    Attributes:
        check_id: Pass this back to resume; it is the whole handle.
        complete: True once every lookup is in and the report exists.
        pending: The outstanding lookups, each with the instructions text
            to show a human and the id to upload against.
    """

    check_id: str
    complete: bool
    pending: list[PendingUpload]


def create_app(
    orchestrator: Orchestrator | None = None, data_root: Path | None = None
) -> FastAPI:
    """Build the app.

    Args:
        orchestrator: Injectable so tests can supply one with scripted
            adapters -- otherwise this would need a live API key and
            ~20s per request to test at all.
        data_root: Where the checkpoint database and uploads live.
    """
    root = data_root or DATA_ROOT
    uploads_root = root / "uploads"

    if orchestrator is None:
        # Process setup belongs at the entry point, same as the CLI does
        # it. Without this, running under uvicorn would ignore .env and
        # fail on a missing API key for no obvious reason.
        load_dotenv()
        provider = GeminiProvider()
        orchestrator = Orchestrator(
            document_agent=DocumentAnalysisAgent(provider),
            provider=provider,
            checkpointer=sqlite_checkpointer(root / "checks.db"),
        )

    app = FastAPI(
        title="BhoomiCheck",
        description="Automated due diligence for land parcels in Telangana, India.",
    )

    def _status(check_id: str, outcome: list[PendingUpload] | RiskReport) -> CheckStatus:
        if isinstance(outcome, RiskReport):
            return CheckStatus(check_id=check_id, complete=True, pending=[])
        return CheckStatus(check_id=check_id, complete=False, pending=outcome)

    @app.post("/api/checks", response_model=CheckStatus)
    def start_check(
        survey_no: str = Form(...),
        village: str = Form(...),
        mandal: str = Form(...),
        seller_name: str = Form(...),
        district: str = Form("Warangal"),
        latitude: str | None = Form(None),
        longitude: str | None = Form(None),
        boundary: str | None = Form(None),
        documents: list[UploadFile] = [],  # noqa: B006 - FastAPI reads this signature
    ) -> CheckStatus:
        """Start a check. Deed PDFs go in here; portal screenshots come later.

        Coordinates are optional but change the quality of the headline
        answer. With none, the master-plan setback check falls back to a
        model reading a map screenshot. With latitude/longitude it is
        measured from that point. With a `boundary` it is measured from
        the parcel's real edges, which is the only conclusive form --
        a corridor can clip a corner while sitting clear of the centre.
        """
        if not documents:
            raise HTTPException(
                status_code=400, detail="at least one document (deed/EC) is required"
            )

        check_id = f"{uuid_for(survey_no)}"
        saved = [save_upload(f, check_dir_for(uploads_root, check_id)) for f in documents]

        lat_text = (latitude or "").strip()
        lon_text = (longitude or "").strip()
        # Half a coordinate is a mistake, not a partial input: silently
        # dropping it would quietly downgrade the setback check to the
        # screenshot reading while the user believed they had supplied a
        # position.
        if bool(lat_text) != bool(lon_text):
            raise HTTPException(
                status_code=400,
                detail="latitude and longitude must be supplied together",
            )
        parsed_boundary = None
        if boundary and boundary.strip():
            try:
                parsed_boundary = parse_boundary(boundary)
            except BoundaryParseError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        location = None
        if lat_text and lon_text:
            try:
                # LatLon's own validators handle range; this only handles
                # "the user typed something that isn't a number".
                location = LatLon(
                    lat=float(lat_text), lon=float(lon_text), source="gis_readout"
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"latitude/longitude must be decimal degrees: {exc}",
                ) from exc

        outcome = orchestrator.start(
            thread_id=check_id,
            parcel=ParcelIdentifier(
                survey_no=survey_no, village=village, mandal=mandal, district=district
            ),
            pdf_paths=saved,
            litigation_query=LitigationSearchQuery(full_name=seller_name),
            owner_background_query=OwnerBackgroundQuery(seller_name=seller_name),
            location=location,
            boundary=parsed_boundary,
        )
        return _status(check_id, outcome)

    @app.get("/api/checks/{check_id}", response_model=CheckStatus)
    def get_check(check_id: str) -> CheckStatus:
        """What this check is still waiting on -- days later, in a new process."""
        pending = orchestrator.pending_uploads(check_id)
        if pending:
            return CheckStatus(check_id=check_id, complete=False, pending=pending)
        if orchestrator.final_report(check_id) is None:
            raise HTTPException(status_code=404, detail=f"no check with id {check_id!r}")
        return CheckStatus(check_id=check_id, complete=True, pending=[])

    @app.post("/api/checks/{check_id}/uploads/{interrupt_id}", response_model=CheckStatus)
    def provide_upload(check_id: str, interrupt_id: str, file: UploadFile) -> CheckStatus:
        """Supply one portal screenshot and let the pipeline continue."""
        if not any(p.interrupt_id == interrupt_id for p in orchestrator.pending_uploads(check_id)):
            raise HTTPException(
                status_code=404,
                detail="that lookup is not pending -- it may already have been supplied",
            )
        saved = save_upload(file, check_dir_for(uploads_root, check_id))
        try:
            outcome = orchestrator.provide_upload(check_id, interrupt_id, saved)
        except ValidationError as e:
            # The model could not read this file into the schema even
            # after retries. The run stays checkpointed, so the caller
            # can simply upload a clearer screenshot.
            raise HTTPException(
                status_code=422,
                detail=f"could not read that file into a usable record: {e}",
            ) from e
        return _status(check_id, outcome)

    @app.get("/api/checks/{check_id}/report.json", response_model=RiskReport)
    def report_json(check_id: str) -> RiskReport:
        report = orchestrator.final_report(check_id)
        if report is None:
            raise HTTPException(status_code=409, detail="check is not complete yet")
        return report

    @app.get("/api/checks/{check_id}/report.html", response_class=HTMLResponse)
    def report_html(check_id: str) -> HTMLResponse:
        report = orchestrator.final_report(check_id)
        if report is None:
            raise HTTPException(status_code=409, detail="check is not complete yet")
        return HTMLResponse(render_html(report))

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(PAGE)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def uuid_for(survey_no: str) -> str:
    """A check id that is readable but can't collide across runs."""
    import re
    import uuid

    # Keep a hint of the parcel for humans, but strip anything that
    # isn't plainly safe -- this ends up in a URL and a directory name.
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", survey_no)[:20]
    return f"{slug}-{uuid.uuid4().hex[:8]}"


app = create_app  # uvicorn factory: `uvicorn bhoomicheck.api.app:app --factory`
