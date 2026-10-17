"""Tests for the Land Records Agent's deterministic Finding-building logic.

Same style as test_zoning_agent.py: no fake provider needed, this agent
maps already-structured LandRecordExtract objects to Findings with plain
code, so the tests check that mapping directly.
"""

from pathlib import Path

from bhoomicheck.agents.land_records import LandRecordsAgent
from bhoomicheck.schemas.land_records import LandRecordExtract
from bhoomicheck.schemas.parcel import ParcelIdentifier

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def test_fully_populated_extract_produces_three_findings() -> None:
    extract = LandRecordExtract(
        owner_names=["K. Rajaiah"],
        pattadar_passbook_no="PB123",
        extent="1 acre 20 guntas",
        land_type="patta land",
        prohibited_status=False,
    )
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    assert report.agent_name == "land_records"
    assert len(report.findings) == 3
    assert len(report.extracts) == 1


def test_owner_names_present_is_verified_ok() -> None:
    extract = LandRecordExtract(owner_names=["K. Rajaiah"])
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])
    ownership = next(f for f in report.findings if f.check_id == "land_records.ownership")
    assert ownership.status == "verified_ok"
    assert "K. Rajaiah" in ownership.claim


def test_missing_owner_names_is_not_verified() -> None:
    extract = LandRecordExtract()
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])
    ownership = next(f for f in report.findings if f.check_id == "land_records.ownership")
    assert ownership.status == "not_verified"


def test_prohibited_status_true_is_issue_found() -> None:
    extract = LandRecordExtract(prohibited_status=True)
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])
    prohibited = next(f for f in report.findings if f.check_id == "land_records.prohibited_land")
    assert prohibited.status == "issue_found"


def test_prohibited_status_false_is_verified_ok() -> None:
    extract = LandRecordExtract(prohibited_status=False)
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])
    prohibited = next(f for f in report.findings if f.check_id == "land_records.prohibited_land")
    assert prohibited.status == "verified_ok"


def test_missing_land_type_is_not_verified_not_silently_ok() -> None:
    extract = LandRecordExtract()
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])
    assert all(f.status == "not_verified" for f in report.findings)


def test_provenance_document_matches_uploaded_filename() -> None:
    extract = LandRecordExtract(owner_names=["K. Rajaiah"])
    report = LandRecordsAgent().report(PARCEL, [(Path("my_screenshot.png"), extract)])
    for finding in report.findings:
        assert finding.provenance is not None
        assert finding.provenance.document == "my_screenshot.png"


def test_record_vintage_flows_into_provenance() -> None:
    from datetime import date

    extract = LandRecordExtract(
        owner_names=["K. Rajaiah"], records_updated_as_on=date(2026, 10, 17)
    )
    report = LandRecordsAgent().report(PARCEL, [(Path("bhubharati.png"), extract)])
    for finding in report.findings:
        assert finding.provenance is not None
        assert finding.provenance.record_updated_at == date(2026, 10, 17)
