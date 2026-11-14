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


# --- registers that are not all alike -----------------------------------
#
# Confirmed against the live portal (Warangal, Nov 2026): the seven
# sub-clause registers behave in three different ways, and conflating
# them misreports whose gap is outstanding.


def under_review(sub_clause: str) -> ProhibitedListExtract:
    """What Waqf and Endowment actually return for Warangal."""
    return ProhibitedListExtract(
        sub_clause=sub_clause, availability="under_review", entries=[]
    )


def empty_register(sub_clause: str = "Court cases & Others") -> ProhibitedListExtract:
    """The live court-cases table reporting nothing for this village."""
    return ProhibitedListExtract(
        sub_clause=sub_clause, availability="empty", entries=[]
    )


class TestUnpublishedRegisters:
    def test_an_unreviewed_register_is_not_counted_as_checked(self) -> None:
        result = match_against_lists(PARCEL, [under_review("22 A (1) (c)- Waqf")])
        assert result.sub_clauses_checked == []
        assert result.sub_clauses_unpublished == ["22 A (1) (c)- Waqf"]

    def test_an_empty_register_is_a_real_answer(self) -> None:
        """"No property is found to be prohibited" is information.

        Treating it as a failure would nag the user to re-fetch a page
        that already answered the question.
        """
        result = match_against_lists(PARCEL, [empty_register()])
        assert result.sub_clauses_checked == ["Court cases & Others"]
        assert result.sub_clauses_unpublished == []

    def test_a_real_answer_beats_an_unavailable_one(self) -> None:
        """If one upload reads a register another reported as pending,
        the register was readable after all."""
        result = match_against_lists(
            PARCEL, [under_review("22 A (1) (a)"), real_list("22 A (1) (a)")]
        )
        assert result.sub_clauses_checked == ["22 A (1) (a)"]
        assert result.sub_clauses_unpublished == []

    def test_unpublished_registers_are_not_demanded_from_the_user(self) -> None:
        """They cannot be fetched, so they are not listed as missing."""
        extracts = [real_list(c) for c in SUB_CLAUSES if "Waqf" not in c]
        extracts.append(under_review("22 A (1) (c)- Waqf"))
        result = match_against_lists(PARCEL, extracts)
        assert result.missing_sub_clauses == []
        assert result.fully_covered is True

    def test_an_unpublished_register_still_blocks_a_clean_verdict(self) -> None:
        """The parcel cannot be cleared against a register that does not
        publicly exist, however diligent the buyer was."""
        extracts = [real_list(c) for c in SUB_CLAUSES if "Waqf" not in c]
        extracts.append(under_review("22 A (1) (c)- Waqf"))
        report = ProhibitedPropertyAgent().report(
            PARCEL, [(Path(f"{i}.pdf"), e) for i, e in enumerate(extracts)]
        )
        finding = next(f for f in report.findings if f.check_id == CHECK_ID)
        assert finding.status == "not_verified"
        # Medium, not low: six of seven registers really were searched.
        assert finding.confidence.level == "medium"
        assert "under review" in finding.confidence.reason
        assert "Waqf" in finding.confidence.reason

    def test_the_blocker_is_named_as_the_department_not_the_user(self) -> None:
        extracts = [real_list(c) for c in SUB_CLAUSES if "Waqf" not in c]
        extracts.append(under_review("22 A (1) (c)- Waqf"))
        report = ProhibitedPropertyAgent().report(
            PARCEL, [(Path(f"{i}.pdf"), e) for i, e in enumerate(extracts)]
        )
        reason = report.findings[0].confidence.reason
        assert "until the department publishes" in reason


class TestCourtCasesTable:
    def test_a_split_survey_number_still_matches(self) -> None:
        """The court-cases table splits "16/D" across two columns.

        The adapter rejoins them, so matching sees one field whatever
        the source's layout was.
        """
        table = ProhibitedListExtract(
            sub_clause="Court cases & Others",
            availability="published",
            entries=[
                ProhibitedListEntry(
                    survey_no="16/D",
                    sub_survey_no="D",
                    reason="Court attachment",
                    order_reference="Memo No. 123 dated 01-01-2020",
                )
            ],
        )
        result = match_against_lists(PARCEL, [table])
        assert len(result.matches) == 1
        assert result.matches[0].reason == "Court attachment"


class TestChennaramAsItStandsToday:
    """The real state of survey 16/D's 22-A check, Nov 2026.

    (a) published and searched, 372 rows, no match.
    Court cases & Others: published, empty for this village.
    Waqf and Endowment: "prohibition data is under review".
    (b), (d), (e): not yet fetched.
    """

    def _report(self):
        extracts = [
            (Path("22a_a.pdf"), real_list("22 A (1) (a)")),
            (Path("court.png"), empty_register()),
            (Path("waqf.png"), under_review("22 A (1) (c)- Waqf")),
            (Path("endow.png"), under_review("22 A (1) (c)- Endowment")),
        ]
        return ProhibitedPropertyAgent().report(PARCEL, extracts)

    def test_two_registers_searched_two_unpublished_three_outstanding(self) -> None:
        result = self._report().result
        assert result.sub_clauses_checked == ["22 A (1) (a)", "Court cases & Others"]
        assert result.sub_clauses_unpublished == [
            "22 A (1) (c)- Waqf",
            "22 A (1) (c)- Endowment",
        ]
        assert result.missing_sub_clauses == [
            "22 A (1) (b)",
            "22 A (1) (d)",
            "22 A (1) (e)",
        ]

    def test_the_verdict_is_still_outstanding_on_the_user(self) -> None:
        """Three registers remain fetchable, so the user's gap dominates."""
        finding = next(
            f for f in self._report().findings if f.check_id == CHECK_ID
        )
        assert finding.status == "not_verified"
        assert "only partly checked" in finding.claim
