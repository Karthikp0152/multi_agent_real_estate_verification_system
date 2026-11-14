"""Tests for the Litigation Agent's deterministic Finding-building logic.

No fake provider needed here (unlike DocumentAnalysisAgent's tests) --
this agent takes already-structured LitigationSearchResult objects and
maps them to Findings with plain code, so the tests just check that
mapping directly.
"""

from pathlib import Path

from bhoomicheck.agents.litigation import LitigationAgent
from bhoomicheck.schemas.litigation import LitigationCaseExtract, LitigationSearchResult
from bhoomicheck.schemas.parcel import ParcelIdentifier

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def test_no_cases_found_is_not_verified_not_a_clean_bill() -> None:
    """An empty eCourts search clears almost nothing.

    Confirmed against the live Warangal portal: the party-name search
    makes **Year** a required field and runs against one court complex
    at a time. So "no cases found" is a statement about one year in one
    court, not about a person. This previously reported `verified_ok`,
    which was the most dangerous sentence this system could have
    printed -- a buyer reading "no litigation found" would not guess it
    meant "in 2025, in this one complex".
    """
    result = LitigationSearchResult(
        searched_name="K. Rajaiah",
        year_searched=2025,
        court_complex="Warangal, PDJ Court Complex",
        status_filter="both",
        cases=[],
    )
    report = LitigationAgent().report(PARCEL, [(Path("k_rajaiah.png"), result)])

    assert report.agent_name == "litigation"
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.status == "not_verified"
    assert finding.provenance is not None
    assert finding.provenance.document == "k_rajaiah.png"
    # The claim must carry the scope, not just the absence.
    assert "filing year 2025" in finding.claim
    assert "Warangal, PDJ Court Complex" in finding.claim
    assert "one filing year and one court complex" in finding.confidence.reason
    # And the name-based weakness still stands alongside the scope one.
    assert "misspellings" in finding.confidence.reason


def test_a_search_with_no_recorded_year_says_so_loudly() -> None:
    """The portal cannot be searched without a year.

    So a result lacking one means the year went unrecorded somewhere in
    our own pipeline, and the claim must not imply broader coverage than
    a single unknown year.
    """
    result = LitigationSearchResult(searched_name="K. Rajaiah", cases=[])
    finding = LitigationAgent().report(PARCEL, [(Path("x.png"), result)]).findings[0]
    assert "unrecorded filing year" in finding.claim


def test_a_pending_only_search_does_not_imply_disposed_cases_were_checked() -> None:
    """The results table has no status column.

    A row's status is whatever radio produced it, so reading a
    Pending-only search as "all cases" would silently drop every
    disposed matter.
    """
    result = LitigationSearchResult(
        searched_name="K. Rajaiah", year_searched=2024,
        court_complex="Warangal, PDJ Court Complex",
        status_filter="pending", cases=[],
    )
    finding = LitigationAgent().report(PARCEL, [(Path("x.png"), result)]).findings[0]
    assert "pending cases only" in finding.claim


def test_cases_found_produce_issue_found_with_low_confidence() -> None:
    case = LitigationCaseExtract(
        court="District Court, Warangal",
        case_no="OS/45/2019",
        status="pending",
        matched_party_name="K. Rajaiah",
    )
    result = LitigationSearchResult(searched_name="K. Rajaiah", cases=[case])
    report = LitigationAgent().report(PARCEL, [(Path("k_rajaiah.png"), result)])

    finding = report.findings[0]
    assert finding.status == "issue_found"
    assert "OS/45/2019" in finding.claim
    # A name match alone must never be reported as a confirmed verdict.
    assert finding.confidence.level == "low"
    assert "not confirmed" in finding.confidence.reason


def test_multiple_name_variants_each_produce_their_own_finding() -> None:
    result_a = LitigationSearchResult(searched_name="K. Rajaiah", cases=[])
    result_b = LitigationSearchResult(searched_name="Rajaiah Kondapaka", cases=[])
    report = LitigationAgent().report(
        PARCEL,
        [
            (Path("variant_a.png"), result_a),
            (Path("variant_b.png"), result_b),
        ],
    )

    assert len(report.findings) == 2
    assert len(report.searches) == 2


def test_results_without_a_recorded_search_term_are_not_attributed() -> None:
    """Seen for real, from a Case Type search screenshot.

    The results table was populated but the name box was empty, and the
    agent rendered `cases naming "" as a party were found` -- a finding
    that reads like a name match while identifying nobody. Rows that
    cannot be attributed are not evidence about this seller.
    """
    result = LitigationSearchResult(
        searched_name="",
        year_searched=2025,
        court_complex="Warangal, PDJ Court Complex",
        status_filter="pending",
        cases=[
            LitigationCaseExtract(
                court="District Courts, Warangal",
                case_no="SC.NDPS/34/2025",
                status="pending",
                matched_party_name="SHO PS WARDHANNAPET Versus SAMSAN DALABEHARA",
            )
        ],
    )
    finding = LitigationAgent().report(PARCEL, [(Path("x.png"), result)]).findings[0]

    assert finding.status == "not_verified"
    assert 'naming ""' not in finding.claim
    assert "cannot be attributed" in finding.claim
    assert "Party Name tab" in finding.confidence.reason
    # The scope is still reported -- it is real information.
    assert "filing year 2025" in finding.claim


def test_a_whitespace_only_search_term_is_treated_the_same() -> None:
    result = LitigationSearchResult(searched_name="   ", cases=[])
    finding = LitigationAgent().report(PARCEL, [(Path("x.png"), result)]).findings[0]
    assert finding.status == "not_verified"
    assert "cannot be attributed" in finding.claim
