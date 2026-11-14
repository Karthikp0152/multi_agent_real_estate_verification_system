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


def test_fully_populated_extract_produces_a_finding_per_check() -> None:
    # Field values here mirror a real Bhu Bharati result page.
    extract = LandRecordExtract(
        owner_names=["\u0aaa\u0ac7..."],
        father_or_husband_name="Kommayya",
        khata_no="60188",
        pattadar_passbook_no="T22150******",
        extent="0.0200",
        nature_of_land="Patta",
        classification_of_land="Magani/ Thari - Ek Fasla",
        land_type="Agriculture Lands",
        prohibited_status=None,
    )
    report = LandRecordsAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    assert report.agent_name == "land_records"
    assert {f.check_id for f in report.findings} == {
        "land_records.ownership",
        "land_records.land_type",
        "land_records.assigned_land",
        "land_records.prohibited_land",
    }
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


def test_patta_land_is_verified_ok_for_the_assigned_check() -> None:
    extract = LandRecordExtract(nature_of_land="Patta")
    report = LandRecordsAgent().report(PARCEL, [(Path("bhu.png"), extract)])
    finding = next(f for f in report.findings if f.check_id == "land_records.assigned_land")
    assert finding.status == "verified_ok"
    # Must not imply it cleared the separate 22-A register.
    assert "Section 22-A" in finding.confidence.reason


def test_non_patta_nature_of_land_is_issue_found() -> None:
    extract = LandRecordExtract(nature_of_land="Assigned")
    report = LandRecordsAgent().report(PARCEL, [(Path("bhu.png"), extract)])
    finding = next(f for f in report.findings if f.check_id == "land_records.assigned_land")
    assert finding.status == "issue_found"
    assert "transfer restrictions" in finding.confidence.reason


def test_prohibited_check_explains_the_source_cannot_answer_it() -> None:
    # Confirmed against a real page: Bhu Bharati simply doesn't carry
    # this. The reason must say so, not blame the screenshot -- otherwise
    # someone retakes a screenshot that can never contain the answer.
    extract = LandRecordExtract(nature_of_land="Patta", prohibited_status=None)
    report = LandRecordsAgent().report(PARCEL, [(Path("bhu.png"), extract)])
    finding = next(f for f in report.findings if f.check_id == "land_records.prohibited_land")
    assert finding.status == "not_verified"
    assert "does not report Section 22-A" in finding.confidence.reason
    assert "not legible" not in finding.confidence.reason


def test_land_type_finding_keeps_the_three_classifications_distinct() -> None:
    extract = LandRecordExtract(
        nature_of_land="Patta",
        classification_of_land="Magani/ Thari - Ek Fasla",
        land_type="Agriculture Lands",
    )
    report = LandRecordsAgent().report(PARCEL, [(Path("bhu.png"), extract)])
    land_type = next(f for f in report.findings if f.check_id == "land_records.land_type")
    assigned = next(f for f in report.findings if f.check_id == "land_records.assigned_land")
    # The page's "Land Type" must not be confused with "Nature of Land".
    assert "Agriculture Lands" in land_type.claim
    assert "Magani" in land_type.claim
    assert "Patta" in assigned.claim
