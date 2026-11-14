"""Tests for RiskReport (schema) and RiskReportAgent (assembly logic).

Pins down the guardrails this schema exists to enforce: the disclaimer
is always present and always exactly the required text, and reports for
mismatched parcels are rejected outright rather than silently combined.
"""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from bhoomicheck.agents.litigation import LitigationAgent
from bhoomicheck.agents.risk_report import RiskReportAgent
from bhoomicheck.agents.zoning import ZoningAgent
from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.litigation import LitigationSearchResult
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract, ZoningReport
from bhoomicheck.schemas.risk_report import DISCLAIMER, RiskReport
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)
OTHER_PARCEL = ParcelIdentifier(
    survey_no="999/Z", village="Elsewhere", mandal="Elsewhere", district="Warangal"
)


def make_generic_report(parcel: ParcelIdentifier) -> ZoningReport:
    finding = Finding(
        check_id="test.check",
        claim="test claim",
        status="verified_ok",
        provenance=Provenance(
            source_name="test", source_type="manual", document="x.pdf", fetched_at=date(2026, 7, 19)
        ),
        confidence=Confidence(level="high", reason="test"),
    )
    return ZoningReport(
        parcel=parcel,
        generated_at=datetime.now(timezone.utc),
        extracts=[KudaMasterPlanExtract()],
        findings=[finding],
    )


class TestRiskReportSchema:
    def test_disclaimer_is_always_the_required_text(self) -> None:
        report = RiskReport(
            parcel=PARCEL,
            generated_at=datetime.now(timezone.utc),
            overall_score=100,
            scored_findings=[],
            specialist_reports=[make_generic_report(PARCEL)],
        )
        assert report.disclaimer == DISCLAIMER
        assert "Claude" not in report.disclaimer
        assert "Anthropic" not in report.disclaimer

    def test_disclaimer_appears_in_serialized_output(self) -> None:
        report = RiskReport(
            parcel=PARCEL,
            generated_at=datetime.now(timezone.utc),
            overall_score=100,
            scored_findings=[],
            specialist_reports=[make_generic_report(PARCEL)],
        )
        assert report.model_dump()["disclaimer"] == DISCLAIMER

    def test_disclaimer_cannot_be_overridden_via_constructor(self) -> None:
        # computed_field means "disclaimer" isn't a real constructor kwarg
        # at all -- Pydantic silently discards the unrecognized argument,
        # and the computed value wins regardless of what was passed.
        report = RiskReport(
            parcel=PARCEL,
            generated_at=datetime.now(timezone.utc),
            overall_score=100,
            scored_findings=[],
            specialist_reports=[make_generic_report(PARCEL)],
            disclaimer="a different disclaimer",  # type: ignore[call-arg]
        )
        assert report.disclaimer == DISCLAIMER

    def test_rejects_specialist_report_with_mismatched_parcel(self) -> None:
        with pytest.raises(ValidationError, match="different"):
            RiskReport(
                parcel=PARCEL,
                generated_at=datetime.now(timezone.utc),
                overall_score=100,
                scored_findings=[],
                specialist_reports=[make_generic_report(OTHER_PARCEL)],
            )

    def test_rejects_empty_specialist_reports(self) -> None:
        with pytest.raises(ValidationError):
            RiskReport(
                parcel=PARCEL,
                generated_at=datetime.now(timezone.utc),
                overall_score=100,
                scored_findings=[],
                specialist_reports=[],
            )


class TestRiskReportAgent:
    def test_assembles_from_multiple_specialist_reports(self) -> None:
        zoning_report = ZoningAgent().report(
            PARCEL, [(Path("kuda.png"), KudaMasterPlanExtract(road_widening_affects_parcel=True))]
        )
        litigation_report = LitigationAgent().report(
            PARCEL, [(Path("ecourts.png"), LitigationSearchResult(searched_name="K. Rajaiah", cases=[]))]
        )

        final = RiskReportAgent().assemble(PARCEL, [zoning_report, litigation_report])

        assert final.parcel == PARCEL
        assert len(final.specialist_reports) == 2
        assert final.disclaimer == DISCLAIMER
        # setback issue (issue_found, medium confidence) must cost points
        assert final.overall_score < 100
        assert len(final.scored_findings) == len(zoning_report.findings) + len(
            litigation_report.findings
        )

    def test_no_issues_across_reports_scores_100(self) -> None:
        # All three fields set (not left None) so every finding resolves
        # to verified_ok -- a None field would score as not_verified, not
        # a clean 100, which is the behaviour test_missing_field_is_not_verified_not_silently_ok
        # (test_zoning_agent.py) already pins down separately.
        extract = KudaMasterPlanExtract(
            zoning_classification="residential",
            road_widening_affects_parcel=False,
            within_ftl_buffer=False,
        )
        zoning_report = ZoningAgent().report(PARCEL, [(Path("kuda.png"), extract)])
        final = RiskReportAgent().assemble(PARCEL, [zoning_report])
        assert final.overall_score == 100


class TestSpecialistReportTypesSurviveSerialization:
    """Regression tests for a bug that silently truncated reports.

    specialist_reports was annotated list[BaseAgentReport]. Pydantic
    validates and serializes by the annotated type, so a ZoningReport
    round-tripping through a checkpoint came back as a BaseAgentReport
    with every subclass field dropped and no error raised. The HTTP API
    checkpoints to SQLite, so it lost the measured road table from every
    report it produced, while in-memory tests passed because nothing was
    ever deserialized.

    The fix is a discriminated union keyed on agent_name. These tests
    exist so it cannot regress quietly.
    """

    def _risk_report(self, specialist) -> RiskReport:
        return RiskReport(
            parcel=PARCEL,
            generated_at=datetime.now(timezone.utc),
            overall_score=70,
            scored_findings=[],
            specialist_reports=[specialist],
        )

    def test_a_zoning_report_round_trips_with_its_own_fields(self) -> None:
        from bhoomicheck.schemas.geo import LatLon, RoadNetworkAssessment, RoadProximity

        assessment = RoadNetworkAssessment(
            location=LatLon(lat=17.856513, lon=79.630249, source="gis_readout"),
            network_label="test network",
            within_mapped_area=True,
            roads=[
                RoadProximity(
                    road_label="30 M",
                    proposed_width_m=30,
                    distance_m=207.7,
                    segment_count=394,
                )
            ],
        )
        original = make_generic_report(PARCEL)
        original.road_assessment = assessment

        revived = RiskReport.model_validate_json(
            self._risk_report(original).model_dump_json()
        )
        zoning = revived.specialist_reports[0]

        assert isinstance(zoning, ZoningReport)
        assert zoning.road_assessment is not None
        assert zoning.road_assessment.roads[0].road_label == "30 M"
        # The computed fields come back consistent, not as stored junk.
        assert zoning.road_assessment.roads[0].clearance_m == 192.7
        assert isinstance(zoning.road_assessment.location, LatLon)

    def test_extracts_also_survive(self) -> None:
        """Not just the new field -- this truncation predated the geometry."""
        revived = RiskReport.model_validate_json(
            self._risk_report(make_generic_report(PARCEL)).model_dump_json()
        )
        assert len(revived.specialist_reports[0].extracts) == 1

    def test_a_bare_base_report_is_rejected_rather_than_truncated(self) -> None:
        """Failing loudly is the point (architecture principle 6).

        A report that is not one of the six known specialist types is a
        programming error. Previously it was accepted and quietly
        stripped; now it cannot get in at all.
        """
        from pydantic import ValidationError

        from bhoomicheck.schemas.report import BaseAgentReport

        bare = BaseAgentReport(
            agent_name="zoning",
            parcel=PARCEL,
            generated_at=datetime.now(timezone.utc),
            findings=make_generic_report(PARCEL).findings,
        )
        with pytest.raises(ValidationError):
            self._risk_report(bare)

    def test_an_unknown_agent_name_is_rejected(self) -> None:
        with pytest.raises(Exception):
            RiskReport.model_validate(
                {
                    "parcel": PARCEL.model_dump(),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "overall_score": 70,
                    "scored_findings": [],
                    "specialist_reports": [{"agent_name": "made_up_agent"}],
                }
            )
