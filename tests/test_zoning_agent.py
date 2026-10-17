"""Tests for the Zoning Agent's deterministic Finding-building logic.

No fake provider needed here (unlike DocumentAnalysisAgent's tests) --
this agent takes already-structured KudaMasterPlanExtract objects and
maps them to Findings with plain code, so the tests just check that
mapping directly, including the third-party-source caveat that has to
appear on every finding built from this source.
"""

from pathlib import Path

from bhoomicheck.agents.zoning import ZoningAgent
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def test_fully_populated_extract_produces_three_findings() -> None:
    extract = KudaMasterPlanExtract(
        zoning_classification="residential",
        road_widening_affects_parcel=True,
        setback_required_meters=3.0,
        within_ftl_buffer=False,
    )
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    assert report.agent_name == "zoning"
    assert len(report.findings) == 3
    assert len(report.extracts) == 1


def test_road_widening_true_is_issue_found_with_setback_and_caveat() -> None:
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=True, setback_required_meters=3.0)
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    road_finding = next(f for f in report.findings if "setback" in f.claim)
    assert road_finding.status == "issue_found"
    assert "3.0" in road_finding.claim
    assert road_finding.confidence.level == "medium"
    assert "third-party" in road_finding.confidence.reason


def test_road_widening_false_is_verified_ok() -> None:
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=False)
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    road_finding = next(f for f in report.findings if "road" in f.claim)
    assert road_finding.status == "verified_ok"


def test_missing_field_is_not_verified_not_silently_ok() -> None:
    extract = KudaMasterPlanExtract()  # nothing legible
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    assert all(f.status == "not_verified" for f in report.findings)
    assert all("not legible" in f.confidence.reason for f in report.findings)


def test_ftl_buffer_true_is_issue_found() -> None:
    extract = KudaMasterPlanExtract(within_ftl_buffer=True)
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    ftl_finding = next(f for f in report.findings if "FTL" in f.claim or "Full Tank Level" in f.claim)
    assert ftl_finding.status == "issue_found"


def test_provenance_document_matches_uploaded_filename() -> None:
    extract = KudaMasterPlanExtract(zoning_classification="residential")
    report = ZoningAgent().report(PARCEL, [(Path("my_screenshot.png"), extract)])

    for finding in report.findings:
        assert finding.provenance is not None
        assert finding.provenance.document == "my_screenshot.png"


def test_plan_approval_date_flows_into_provenance() -> None:
    from datetime import date

    extract = KudaMasterPlanExtract(
        zoning_classification="residential", plan_approved_on=date(2024, 11, 17)
    )
    report = ZoningAgent().report(PARCEL, [(Path("kuda.png"), extract)])
    for finding in report.findings:
        assert finding.provenance is not None
        assert finding.provenance.record_updated_at == date(2024, 11, 17)
