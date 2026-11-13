"""Tests for the Section 22-A prohibited-property check.

The rows used here are real ones from the Chennaram 22A(1)(a)
Agriculture list (Warangal / Wardhannapet), so the headline test
reproduces an actual result rather than a convenient fiction.
"""

from pathlib import Path

import pytest

from bhoomicheck.adapters.prohibited_property import ProhibitedPropertyAdapter
from bhoomicheck.agents.prohibited_property import (
    CHECK_ID,
    ProhibitedPropertyAgent,
    match_against_lists,
)
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.prohibited import (
    SUB_CLAUSES,
    ProhibitedListEntry,
    ProhibitedListExtract,
)

PARCEL = ParcelIdentifier(
    survey_no="16/D", village="Chennaram", mandal="Wardhannapet", district="Warangal"
)

# Verbatim rows from the real list.
REAL_ROWS = [
    ("8/1", "Adduri Prabhakar S/W/o Komraiah", "0.1300"),
    ("10/A/3", "Thakallapelli muralidhar rao", "0.0700"),
    ("20", "Thorri janardhan rao", "0.0700"),
    ("22", "Nyalagiri Venkateshwarlu S/W/o Mallaiah", "0.2300"),
    ("49/53/B/2", "Nampalli Narsamma S/W/o Kommulu", "0.0850"),
    ("254/110/E", "Manda Ayilaiah S/W/o Sri", "0.0200"),
    ("279A", "Mandha komuraiah", "1.1500"),
    ("464", "Katta Janardhan S/W/o Ellaiah", "0.1700"),
]

ACT = "TG Assigned Lands (Prohibition of Transfers) Act, 1977"


def real_list(sub_clause: str = "22 A (1) (a)") -> ProhibitedListExtract:
    return ProhibitedListExtract(
        sub_clause=sub_clause,
        land_category="Agriculture",
        district="WARANGAL",
        mandal="WARDHANNAPET",
        village="CHENNARAM",
        entries=[
            ProhibitedListEntry(survey_no=s, owner_name=o, extent=e, act=ACT)
            for s, o, e in REAL_ROWS
        ],
        total_extent="232.16",
    )


def all_seven() -> list[tuple[Path, ProhibitedListExtract]]:
    return [(Path(f"{c}.pdf"), real_list(c)) for c in SUB_CLAUSES]


class TestMatching:
    def test_survey_16_is_absent_from_the_real_chennaram_rows(self) -> None:
        """The real-world result, pinned.

        The list runs 10/A/3 -> 20 -> 22 consecutively; 16 would sit
        between them and does not.
        """
        result = match_against_lists(PARCEL, [real_list()])
        assert result.matches == []
        assert result.rows_examined == len(REAL_ROWS)

    def test_a_listed_parcel_is_found(self) -> None:
        listed = ParcelIdentifier(
            survey_no="49/53/B/2", village="Chennaram",
            mandal="Wardhannapet", district="Warangal",
        )
        result = match_against_lists(listed, [real_list()])
        assert len(result.matches) == 1
        assert result.matches[0].owner_name == "Nampalli Narsamma S/W/o Kommulu"

    def test_a_parcel_under_a_wholly_prohibited_survey_number_is_found(self) -> None:
        """An entry for "20" covers a buyer's 20/B."""
        sub = ParcelIdentifier(
            survey_no="20/B", village="Chennaram",
            mandal="Wardhannapet", district="Warangal",
        )
        assert len(match_against_lists(sub, [real_list()]).matches) == 1

    def test_coverage_counts_only_identifiable_lists(self) -> None:
        """A list whose heading could not be read covers nothing."""
        unknown = real_list()
        unknown.sub_clause = None
        result = match_against_lists(PARCEL, [unknown])
        assert result.sub_clauses_checked == []
        assert len(result.missing_sub_clauses) == len(SUB_CLAUSES)

    def test_duplicate_sub_clauses_are_counted_once(self) -> None:
        result = match_against_lists(PARCEL, [real_list(), real_list()])
        assert result.sub_clauses_checked == ["22 A (1) (a)"]
        # Rows still accumulate -- both uploads were read.
        assert result.rows_examined == len(REAL_ROWS) * 2

    def test_a_row_with_an_unreadable_survey_number_never_matches(self) -> None:
        blank = ProhibitedListExtract(
            sub_clause="22 A (1) (a)",
            entries=[ProhibitedListEntry(survey_no=None, owner_name="illegible")],
        )
        assert match_against_lists(PARCEL, [blank]).matches == []


class TestFindings:
    def _finding(self, extracts):
        report = ProhibitedPropertyAgent().report(PARCEL, extracts)
        return next(f for f in report.findings if f.check_id == CHECK_ID)

    def test_partial_coverage_is_not_verified_not_clean(self) -> None:
        """The central honesty rule of this agent.

        Absent from one of seven lists is not absent from the register.
        """
        finding = self._finding([(Path("a.pdf"), real_list())])
        assert finding.status == "not_verified"
        assert "only partly checked" in finding.claim
        assert "22 A (1) (b)" in finding.confidence.reason

    def test_full_coverage_with_no_match_is_a_clean_result(self) -> None:
        finding = self._finding(all_seven())
        assert finding.status == "verified_ok"
        assert finding.confidence.level == "high"
        assert "7 of 7" in finding.claim

    def test_a_match_is_an_issue_naming_the_row(self) -> None:
        listed = ParcelIdentifier(
            survey_no="254/110/E", village="Chennaram",
            mandal="Wardhannapet", district="Warangal",
        )
        report = ProhibitedPropertyAgent().report(listed, [(Path("a.pdf"), real_list())])
        finding = next(f for f in report.findings if f.check_id == CHECK_ID)
        assert finding.status == "issue_found"
        assert "Manda Ayilaiah" in finding.claim
        assert "1977" in finding.claim

    def test_a_match_outranks_missing_coverage(self) -> None:
        """Finding the parcel listed settles it; the unchecked lists
        cannot make a confirmed prohibition less true."""
        listed = ParcelIdentifier(
            survey_no="20", village="Chennaram",
            mandal="Wardhannapet", district="Warangal",
        )
        report = ProhibitedPropertyAgent().report(listed, [(Path("a.pdf"), real_list())])
        assert report.findings[0].status == "issue_found"

    def test_every_finding_carries_provenance_and_the_source_caveat(self) -> None:
        finding = self._finding(all_seven())
        assert finding.provenance is not None
        assert "IGRS" in finding.provenance.source_name
        assert "Sub-Registrar" in finding.confidence.reason

    def test_the_report_carries_the_coverage_behind_the_verdict(self) -> None:
        report = ProhibitedPropertyAgent().report(PARCEL, [(Path("a.pdf"), real_list())])
        assert report.agent_name == "prohibited_property"
        assert report.result.sub_clauses_checked == ["22 A (1) (a)"]
        assert len(report.result.missing_sub_clauses) == 6


class TestAdapter:
    def test_instructions_name_the_real_public_url_and_the_parcel(self) -> None:
        text = ProhibitedPropertyAdapter().instructions(PARCEL)
        assert "openProhibitedProperties.htm" in text
        assert "Chennaram" in text
        assert "16/D" in text
        assert "no login" in text

    def test_instructions_insist_on_all_seven_lists(self) -> None:
        text = ProhibitedPropertyAdapter().instructions(PARCEL)
        assert "ALL SEVEN" in text
        assert "has not been cleared" in text

    def test_parse_requires_a_provider(self, tmp_path: Path) -> None:
        upload = tmp_path / "list.pdf"
        upload.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(RuntimeError, match="needs an LLMProvider"):
            ProhibitedPropertyAdapter().parse(upload)
