"""Tests for the Orchestrator's real LangGraph interrupt/resume wiring.

No live model calls, and no live adapter calls: FakeProvider satisfies
LLMProvider (same pattern as test_document_analysis_agent.py), and
FakeAdapter satisfies the ManualAdapter shape with a scripted parse()
result -- the real adapters' parse() still raises NotImplementedError
(no real samples yet), so testing the interrupt/resume mechanism itself
needs a stand-in that actually works, same idea as FakeProvider.
"""

import json
from pathlib import Path

from bhoomicheck.agents.document_analysis import DocumentAnalysisAgent
from bhoomicheck.orchestrator.graph import Orchestrator, PendingUpload, sqlite_checkpointer
from bhoomicheck.schemas.land_records import LandRecordExtract
from bhoomicheck.schemas.litigation import LitigationSearchQuery, LitigationSearchResult
from bhoomicheck.schemas.owner_background import OwnerBackgroundExtract, OwnerBackgroundQuery
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.risk_report import RiskReport
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)
LITIGATION_QUERY = LitigationSearchQuery(full_name="K. Rajaiah")
OWNER_BACKGROUND_QUERY = OwnerBackgroundQuery(seller_name="K. Rajaiah")

VALID_DOCUMENT_PAYLOAD = json.dumps(
    {
        "documents": [
            {
                "source_document": "sale_deed_2015_1234.pdf",
                "doc_type": "sale_deed",
                "reference_no": "1234/2015",
                "execution_date": "2015-03-12",
                "executants": ["K. Rajaiah"],
                "claimants": ["B. Swapna"],
                "extraction_confidence": {"level": "medium", "reason": "scanned copy"},
            }
        ],
        "findings": [
            {
                "check_id": "document.deed_present",
                "claim": "sale deed 1234/2015 names B. Swapna as claimant",
                "status": "verified_ok",
                "provenance": {
                    "source_name": "uploaded sale deed",
                    "source_type": "manual",
                    "document": "sale_deed_2015_1234.pdf",
                    "fetched_at": "2026-07-19",
                },
                "confidence": {"level": "high", "reason": "clearly printed party names"},
            }
        ],
    }
)


class FakeProvider:
    """Scripted LLMProvider, same pattern as test_document_analysis_agent.py."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate(self, prompt: str, pdf_paths: list[Path]) -> str:
        self.prompts.append(prompt)
        return self.responses[len(self.prompts) - 1]


class FakeAdapter:
    """Scripted ManualAdapter: satisfies the (instructions, parse) shape
    with a fixed result, since the real adapters' parse() isn't
    implemented yet."""

    def __init__(self, result: object, instructions_text: str = "fake instructions") -> None:
        self._result = result
        self._instructions_text = instructions_text

    def instructions(self, query: object) -> str:
        return self._instructions_text

    def parse(self, upload_path: Path) -> object:
        return self._result


def make_orchestrator(
    *,
    road_widening: bool = False,
    prohibited: bool = False,
    poa_sale: bool = False,
) -> Orchestrator:
    return Orchestrator(
        document_agent=DocumentAnalysisAgent(FakeProvider([VALID_DOCUMENT_PAYLOAD])),
        kuda_adapter=FakeAdapter(
            KudaMasterPlanExtract(road_widening_affects_parcel=road_widening), "fetch kuda"
        ),
        litigation_adapter=FakeAdapter(
            LitigationSearchResult(searched_name="K. Rajaiah", cases=[]), "fetch ecourts"
        ),
        land_records_adapter=FakeAdapter(
            LandRecordExtract(prohibited_status=prohibited), "fetch bhu bharati"
        ),
        owner_background_adapter=FakeAdapter(
            OwnerBackgroundExtract(is_poa_sale=poa_sale), "fetch igrs"
        ),
    )


def test_start_pauses_on_four_manual_agents() -> None:
    orchestrator = make_orchestrator()
    pending = orchestrator.start(
        thread_id="t1",
        parcel=PARCEL,
        pdf_paths=[Path("sale_deed_2015_1234.pdf")],
        litigation_query=LITIGATION_QUERY,
        owner_background_query=OWNER_BACKGROUND_QUERY,
    )

    assert isinstance(pending, list)
    agents_waiting = {p.agent for p in pending}
    # document_analysis needs no interrupt (PDFs supplied up front);
    # growth_potential hasn't started yet (waits on zoning).
    assert agents_waiting == {"zoning", "litigation", "land_records", "owner_background"}
    assert all(isinstance(p, PendingUpload) for p in pending)


def test_instructions_text_comes_from_the_adapter() -> None:
    orchestrator = make_orchestrator()
    pending = orchestrator.start(
        thread_id="t2",
        parcel=PARCEL,
        pdf_paths=[Path("sale_deed_2015_1234.pdf")],
        litigation_query=LITIGATION_QUERY,
        owner_background_query=OWNER_BACKGROUND_QUERY,
    )
    zoning_pending = next(p for p in pending if p.agent == "zoning")
    assert zoning_pending.instructions == "fetch kuda"


def test_resolving_zoning_triggers_growth_potential_automatically() -> None:
    orchestrator = make_orchestrator()
    pending = orchestrator.start(
        thread_id="t3",
        parcel=PARCEL,
        pdf_paths=[Path("sale_deed_2015_1234.pdf")],
        litigation_query=LITIGATION_QUERY,
        owner_background_query=OWNER_BACKGROUND_QUERY,
    )
    zoning_pending = next(p for p in pending if p.agent == "zoning")

    result = orchestrator.provide_upload("t3", zoning_pending.interrupt_id, Path("kuda.png"))

    # zoning resolved -> growth_potential runs immediately behind it (no
    # interrupt of its own) -> three manual agents still pending, and
    # growth_potential/zoning are no longer in the pending list.
    assert isinstance(result, list)
    agents_still_waiting = {p.agent for p in result}
    assert agents_still_waiting == {"litigation", "land_records", "owner_background"}


def test_resolving_all_four_produces_the_final_risk_report() -> None:
    orchestrator = make_orchestrator()
    pending = orchestrator.start(
        thread_id="t4",
        parcel=PARCEL,
        pdf_paths=[Path("sale_deed_2015_1234.pdf")],
        litigation_query=LITIGATION_QUERY,
        owner_background_query=OWNER_BACKGROUND_QUERY,
    )

    result: list[PendingUpload] | RiskReport = pending
    uploads = {
        "zoning": Path("kuda.png"),
        "litigation": Path("ecourts.png"),
        "land_records": Path("bhubharati.png"),
        "owner_background": Path("igrs.png"),
    }
    # Resolve one at a time, in an arbitrary order, to also pin down that
    # partial resume across separate calls works.
    for pending_upload in list(pending):
        assert isinstance(result, list)
        result = orchestrator.provide_upload(
            "t4", pending_upload.interrupt_id, uploads[pending_upload.agent]
        )

    assert isinstance(result, RiskReport)
    assert result.parcel == PARCEL
    assert len(result.specialist_reports) == 6
    agent_names = {r.agent_name for r in result.specialist_reports}
    assert agent_names == {
        "document_analysis",
        "zoning",
        "litigation",
        "land_records",
        "owner_background",
        "growth_potential",
    }


def test_final_score_reflects_an_issue_from_one_specialist() -> None:
    orchestrator = make_orchestrator(road_widening=True)
    pending = orchestrator.start(
        thread_id="t5",
        parcel=PARCEL,
        pdf_paths=[Path("sale_deed_2015_1234.pdf")],
        litigation_query=LITIGATION_QUERY,
        owner_background_query=OWNER_BACKGROUND_QUERY,
    )
    uploads = {
        "zoning": Path("kuda.png"),
        "litigation": Path("ecourts.png"),
        "land_records": Path("bhubharati.png"),
        "owner_background": Path("igrs.png"),
    }
    result: list[PendingUpload] | RiskReport = pending
    for pending_upload in list(pending):
        assert isinstance(result, list)
        result = orchestrator.provide_upload(
            "t5", pending_upload.interrupt_id, uploads[pending_upload.agent]
        )

    assert isinstance(result, RiskReport)
    assert result.overall_score < 100


class TestPersistence:
    """A check must survive the process that started it.

    These build a *second* Orchestrator against the same SQLite file,
    with its own connection and freshly compiled graph -- the closest
    in-process stand-in for closing the terminal and coming back
    tomorrow, which is the actual workflow this project needs.
    """

    def _orchestrator(self, db_path: Path) -> Orchestrator:
        return Orchestrator(
            document_agent=DocumentAnalysisAgent(FakeProvider([VALID_DOCUMENT_PAYLOAD])),
            kuda_adapter=FakeAdapter(KudaMasterPlanExtract(road_widening_affects_parcel=False)),
            litigation_adapter=FakeAdapter(
                LitigationSearchResult(searched_name="K. Rajaiah", cases=[])
            ),
            land_records_adapter=FakeAdapter(LandRecordExtract(prohibited_status=False)),
            owner_background_adapter=FakeAdapter(OwnerBackgroundExtract(is_poa_sale=False)),
            checkpointer=sqlite_checkpointer(db_path),
        )

    def _start(self, orchestrator: Orchestrator, check_id: str) -> list[PendingUpload]:
        return orchestrator.start(
            thread_id=check_id,
            parcel=PARCEL,
            pdf_paths=[Path("sale_deed_2015_1234.pdf")],
            litigation_query=LITIGATION_QUERY,
            owner_background_query=OWNER_BACKGROUND_QUERY,
        )

    def test_a_check_resumes_in_a_new_orchestrator(self, tmp_path: Path) -> None:
        db = tmp_path / "checks.db"
        first = self._orchestrator(db)
        pending = self._start(first, "check-1")
        zoning = next(p for p in pending if p.agent == "zoning")
        first.provide_upload("check-1", zoning.interrupt_id, Path("kuda.png"))

        # "Next day": brand new Orchestrator, new connection, same file.
        second = self._orchestrator(db)
        still_pending = second.pending_uploads("check-1")

        # zoning was already supplied, so it must NOT be asked for again.
        assert {p.agent for p in still_pending} == {
            "litigation",
            "land_records",
            "owner_background",
        }

    def test_resumed_check_runs_to_a_final_report(self, tmp_path: Path) -> None:
        db = tmp_path / "checks.db"
        first = self._orchestrator(db)
        pending = self._start(first, "check-2")
        zoning = next(p for p in pending if p.agent == "zoning")
        first.provide_upload("check-2", zoning.interrupt_id, Path("kuda.png"))

        second = self._orchestrator(db)
        result: list[PendingUpload] | RiskReport = second.pending_uploads("check-2")
        for pending_upload in list(result):
            result = second.provide_upload(
                "check-2", pending_upload.interrupt_id, Path(f"{pending_upload.agent}.png")
            )

        assert isinstance(result, RiskReport)
        assert {r.agent_name for r in result.specialist_reports} == {
            "document_analysis",
            "zoning",
            "litigation",
            "land_records",
            "owner_background",
            "growth_potential",
        }
        # And the finished report is retrievable by id alone afterwards.
        assert second.final_report("check-2") is not None

    def test_pending_uploads_is_empty_for_an_unknown_check(self, tmp_path: Path) -> None:
        orchestrator = self._orchestrator(tmp_path / "checks.db")
        assert orchestrator.pending_uploads("never-started") == []
        assert orchestrator.final_report("never-started") is None
