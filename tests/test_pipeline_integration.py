"""End-to-end: the real adapters, driven by a scripted model.

Every other test swaps the adapters out for fakes to isolate one piece.
This one keeps the *real* KudaMasterPlanAdapter, LitigationAdapter,
LandRecordsAdapter and OwnerBackgroundAdapter in place and only replaces
the model at the very bottom of the stack -- so it exercises each
adapter's real prompt, its real schema, the real extraction retry loop,
the real agents, scoring, and the final report.

The provider dispatches on prompt content rather than call order,
because the specialist nodes run in parallel and finish in whatever
order LangGraph schedules them.
"""

import json
from pathlib import Path

from bhoomicheck.agents.document_analysis import DocumentAnalysisAgent
from bhoomicheck.orchestrator.graph import Orchestrator
from bhoomicheck.report.html import render_html
from bhoomicheck.schemas.litigation import LitigationSearchQuery
from bhoomicheck.schemas.owner_background import OwnerBackgroundQuery
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.risk_report import RiskReport

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)

DOCUMENT_PAYLOAD = json.dumps(
    {
        "documents": [
            {
                "source_document": "deed.pdf",
                "doc_type": "sale_deed",
                "extraction_confidence": {"level": "medium", "reason": "scanned copy"},
            }
        ],
        "findings": [
            {
                "check_id": "document.deed_chain",
                "claim": "sale deed names the seller as claimant",
                "status": "verified_ok",
                "provenance": {
                    "source_name": "uploaded sale deed",
                    "source_type": "manual",
                    "document": "deed.pdf",
                    "fetched_at": "2026-10-17",
                },
                "confidence": {"level": "high", "reason": "clearly printed"},
            }
        ],
    }
)

# A deliberately mixed bag: one hard issue (setback), one unreadable
# field (FTL buffer -> null -> not_verified), and clean results elsewhere.
KUDA_PAYLOAD = json.dumps(
    {
        "zoning_classification": "residential",
        "road_widening_affects_parcel": True,
        "setback_required_meters": 9.0,
        "within_ftl_buffer": None,
        "in_growth_corridor": True,
    }
)
LITIGATION_PAYLOAD = json.dumps({"searched_name": "K. Rajaiah", "cases": []})
LAND_RECORDS_PAYLOAD = json.dumps(
    {"owner_names": ["K. Rajaiah"], "land_type": "patta land", "prohibited_status": False}
)
OWNER_BACKGROUND_PAYLOAD = json.dumps(
    {"is_poa_sale": False, "seller_name_matches_title": True}
)


class ContextAwareProvider:
    """Answers based on which adapter's prompt it's given.

    Order-independent on purpose: the specialist nodes run in parallel,
    so a queue keyed on call order would be flaky.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, prompt: str, file_paths: list[Path]) -> str:
        if "land-records document analyst" in prompt:
            self.calls.append("document")
            return DOCUMENT_PAYLOAD
        if "KUDA Master Plan 2041" in prompt:
            self.calls.append("kuda")
            return KUDA_PAYLOAD
        if "eCourts case-status search" in prompt:
            self.calls.append("litigation")
            return LITIGATION_PAYLOAD
        if "Bhu Bharati" in prompt:
            self.calls.append("land_records")
            return LAND_RECORDS_PAYLOAD
        if "IGRS Telangana" in prompt:
            self.calls.append("owner_background")
            return OWNER_BACKGROUND_PAYLOAD
        raise AssertionError(f"unrecognised prompt:\n{prompt[:400]}")


def _run_full_pipeline(tmp_path: Path) -> RiskReport:
    provider = ContextAwareProvider()
    orchestrator = Orchestrator(
        document_agent=DocumentAnalysisAgent(provider),
        provider=provider,
    )
    upload = tmp_path / "screenshot.png"
    upload.write_bytes(b"\x89PNG fake")

    result = orchestrator.start(
        thread_id="integration",
        parcel=PARCEL,
        pdf_paths=[tmp_path / "deed.pdf"],
        litigation_query=LitigationSearchQuery(full_name="K. Rajaiah"),
        owner_background_query=OwnerBackgroundQuery(seller_name="K. Rajaiah"),
    )
    while isinstance(result, list) and result:
        result = orchestrator.provide_upload("integration", result[0].interrupt_id, upload)

    assert isinstance(result, RiskReport)
    return result


def test_real_adapters_produce_a_complete_report(tmp_path: Path) -> None:
    report = _run_full_pipeline(tmp_path)

    assert {r.agent_name for r in report.specialist_reports} == {
        "document_analysis",
        "zoning",
        "litigation",
        "land_records",
        "owner_background",
        "growth_potential",
        "prohibited_property",
    }
    assert 0 <= report.overall_score <= 100


def test_unreadable_field_becomes_not_verified_not_a_guess(tmp_path: Path) -> None:
    # within_ftl_buffer came back null from the model. That must surface
    # as "not verified" -- never as "no problem found" (principle 6).
    report = _run_full_pipeline(tmp_path)
    ftl = [
        f
        for r in report.specialist_reports
        for f in r.findings
        if f.check_id == "zoning.ftl_buffer"
    ]
    assert ftl and all(f.status == "not_verified" for f in ftl)


def test_the_setback_issue_survives_all_the_way_to_the_report(tmp_path: Path) -> None:
    # The flagship risk this whole project exists to catch, traced from a
    # model response through adapter, agent, scoring and into the report.
    report = _run_full_pipeline(tmp_path)
    setback = [
        f
        for r in report.specialist_reports
        for f in r.findings
        if f.check_id == "zoning.road_widening_setback"
    ]
    assert setback and setback[0].status == "issue_found"
    assert "9.0" in setback[0].claim
    assert report.overall_score < 100

    html = render_html(report)
    assert "setback" in html
    assert report.disclaimer in html
