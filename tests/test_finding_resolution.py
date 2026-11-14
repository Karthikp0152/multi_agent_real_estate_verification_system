"""Tests for reducing multiple answers to one question down to one finding.

The motivating case is real: Bhu Bharati's land-details page cannot
report Section 22-A prohibited status, so LandRecordsAgent emits
"not_verified" for check_id land_records.prohibited_land. IGRS has a
dedicated Prohibited Property search that CAN answer it. Both carry the
same check_id, and that check is weighted 40 -- so scoring both would
deduct for one question twice.
"""

from datetime import date, datetime, timezone

from bhoomicheck.agents.risk_report import (
    RiskReportAgent,
    conflicting_check_ids,
    resolve_findings,
)
from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract, ZoningReport

PARCEL = ParcelIdentifier(
    survey_no="16/D", village="Chennaram", mandal="Wardhannapet", district="Warangal"
)

PROHIBITED = "land_records.prohibited_land"


def finding(
    check_id: str = PROHIBITED,
    status: str = "not_verified",
    level: str = "low",
    source: str = "Bhu Bharati",
) -> Finding:
    return Finding(
        check_id=check_id,
        claim=f"{source} says {status}",
        status=status,
        provenance=Provenance(
            source_name=source,
            source_type="manual",
            document="shot.png",
            fetched_at=date(2026, 11, 12),
        ),
        confidence=Confidence(level=level, reason="test"),
    )


class TestResolution:
    def test_a_check_answered_once_passes_through_unchanged(self) -> None:
        only = [finding(check_id="a"), finding(check_id="b")]
        authoritative, superseded = resolve_findings(only)
        assert authoritative == only
        assert superseded == []

    def test_a_real_answer_beats_a_source_that_could_not_answer(self) -> None:
        """The Bhu Bharati / IGRS case exactly."""
        cannot = finding(status="not_verified", source="Bhu Bharati")
        can = finding(status="verified_ok", level="medium", source="IGRS")
        authoritative, superseded = resolve_findings([cannot, can])

        assert len(authoritative) == 1
        assert authoritative[0].provenance.source_name == "IGRS"
        assert [f.provenance.source_name for f in superseded] == ["Bhu Bharati"]

    def test_order_of_arrival_does_not_change_the_winner(self) -> None:
        cannot = finding(status="not_verified", source="Bhu Bharati")
        can = finding(status="verified_ok", level="medium", source="IGRS")
        first, _ = resolve_findings([cannot, can])
        second, _ = resolve_findings([can, cannot])
        assert first[0].provenance.source_name == second[0].provenance.source_name

    def test_an_issue_outranks_a_clean_answer(self) -> None:
        """Safety direction: never quietly keep the reassuring answer.

        If one source says the parcel is in the 22-A register and another
        says it is not, the buyer must see the problem.
        """
        clean = finding(status="verified_ok", level="high", source="A")
        problem = finding(status="issue_found", level="low", source="B")
        authoritative, _ = resolve_findings([clean, problem])
        assert authoritative[0].status == "issue_found"

    def test_higher_confidence_wins_among_equal_statuses(self) -> None:
        low = finding(status="verified_ok", level="low", source="A")
        high = finding(status="verified_ok", level="high", source="B")
        authoritative, _ = resolve_findings([low, high])
        assert authoritative[0].provenance.source_name == "B"

    def test_nothing_is_discarded(self) -> None:
        """Superseded findings are kept so a reader can see them."""
        findings = [
            finding(status="not_verified", source="A"),
            finding(status="verified_ok", level="medium", source="B"),
            finding(status="verified_ok", level="low", source="C"),
        ]
        authoritative, superseded = resolve_findings(findings)
        assert len(authoritative) + len(superseded) == len(findings)


class TestConflictDetection:
    def test_two_sources_answering_differently_is_a_conflict(self) -> None:
        clean = finding(status="verified_ok", level="high", source="A")
        problem = finding(status="issue_found", level="high", source="B")
        authoritative, superseded = resolve_findings([clean, problem])
        assert conflicting_check_ids(authoritative, superseded) == {PROHIBITED}

    def test_a_source_that_could_not_answer_is_not_a_conflict(self) -> None:
        """The ordinary case, and it must not cry wolf.

        Bhu Bharati being unable to answer is not disagreement with IGRS.
        """
        cannot = finding(status="not_verified", source="Bhu Bharati")
        can = finding(status="verified_ok", level="medium", source="IGRS")
        authoritative, superseded = resolve_findings([cannot, can])
        assert conflicting_check_ids(authoritative, superseded) == set()

    def test_two_sources_agreeing_is_not_a_conflict(self) -> None:
        a = finding(status="verified_ok", level="high", source="A")
        b = finding(status="verified_ok", level="low", source="B")
        authoritative, superseded = resolve_findings([a, b])
        assert conflicting_check_ids(authoritative, superseded) == set()


class TestScoringIsNotDoubleCounted:
    """The defect this whole mechanism exists to prevent."""

    def _report(self, findings: list[Finding]):
        zoning = ZoningReport(
            parcel=PARCEL,
            generated_at=datetime.now(timezone.utc),
            extracts=[KudaMasterPlanExtract()],
            findings=findings,
        )
        return RiskReportAgent().assemble(PARCEL, [zoning])

    def test_one_question_is_deducted_for_once(self) -> None:
        single = self._report([finding(status="issue_found", level="medium")])
        doubled = self._report(
            [
                finding(status="issue_found", level="medium", source="A"),
                finding(status="issue_found", level="medium", source="B"),
            ]
        )
        # Same question, two sources, identical verdict -> identical score.
        assert doubled.overall_score == single.overall_score
        assert len(doubled.scored_findings) == 1
        assert len(doubled.superseded_findings) == 1

    def test_the_superseded_answer_reaches_the_report(self) -> None:
        report = self._report(
            [
                finding(status="not_verified", source="Bhu Bharati"),
                finding(status="verified_ok", level="medium", source="IGRS"),
            ]
        )
        assert [f.provenance.source_name for f in report.superseded_findings] == [
            "Bhu Bharati"
        ]
        assert report.conflicting_checks == []

    def test_a_conflict_is_recorded_on_the_report(self) -> None:
        report = self._report(
            [
                finding(status="verified_ok", level="high", source="A"),
                finding(status="issue_found", level="high", source="B"),
            ]
        )
        assert report.conflicting_checks == [PROHIBITED]
        # And the issue is what counted.
        assert report.scored_findings[0].status == "issue_found"

    def test_specialist_reports_keep_every_finding(self) -> None:
        """The audit trail is untouched; only scoring is reduced."""
        report = self._report(
            [
                finding(status="not_verified", source="Bhu Bharati"),
                finding(status="verified_ok", level="medium", source="IGRS"),
            ]
        )
        assert len(report.specialist_reports[0].findings) == 2
