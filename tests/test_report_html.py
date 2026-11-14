"""Tests for the HTML report renderer.

These check the guarantees the report has to make, not its styling:
the disclaimer is present, not-verified findings stay visually distinct
from clean ones (principle 6), provenance and confidence appear per
finding (principles 3 and 4), and untrusted text is escaped.
"""

from datetime import date, datetime, timezone

from bhoomicheck.report.html import render_html
from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.schemas.geo import LatLon, RoadNetworkAssessment, RoadProximity
from bhoomicheck.schemas.risk_report import DISCLAIMER, RiskReport
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract, ZoningReport

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def make_finding(**overrides) -> Finding:
    fields = {
        "check_id": "zoning.road_widening_setback",
        "claim": "a master-plan road affects this parcel",
        "status": "issue_found",
        "provenance": Provenance(
            source_name="1acre.in",
            source_type="manual",
            document="kuda.png",
            fetched_at=date(2026, 7, 19),
        ),
        "confidence": Confidence(level="medium", reason="third-party overlay"),
    }
    fields.update(overrides)
    return Finding(**fields)


def make_report(
    findings: list[Finding] | None = None,
    assessment: "RoadNetworkAssessment | None" = None,
) -> RiskReport:
    # A real ZoningReport, not a bare BaseAgentReport: RiskReport's
    # specialist_reports is a discriminated union, so the base class is no
    # longer a valid member -- and was never a shape production produced.
    specialist = ZoningReport(
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
        extracts=[KudaMasterPlanExtract()],
        road_assessment=assessment,
        findings=findings or [make_finding()],
    )
    return RiskReport(
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
        overall_score=70,
        scored_findings=[],
        specialist_reports=[specialist],
    )


def test_disclaimer_is_present() -> None:
    html = render_html(make_report())
    # Hard guardrail: it ships with the report, and prominently enough to
    # appear before the findings, not only in the footer.
    assert DISCLAIMER in html
    assert html.index(DISCLAIMER) < html.index("a master-plan road")


def test_parcel_and_score_appear() -> None:
    html = render_html(make_report())
    assert "123/A" in html
    assert "Hasanparthy" in html
    assert "70" in html


def test_not_verified_is_visually_distinct_from_verified_ok() -> None:
    findings = [
        make_finding(status="verified_ok", claim="clean thing"),
        make_finding(
            check_id="zoning.ftl_buffer",
            status="not_verified",
            claim="unknown thing",
            provenance=None,
            confidence=Confidence(level="low", reason="not legible"),
        ),
    ]
    html = render_html(make_report(findings))
    # Separate CSS classes, so a reader cannot mistake "couldn't check"
    # for "checked and fine".
    assert 'class="finding not_verified"' in html
    assert 'class="finding verified_ok"' in html
    assert "Not verified" in html


def test_finding_without_provenance_says_so_explicitly() -> None:
    finding = make_finding(status="not_verified", provenance=None)
    html = render_html(make_report([finding]))
    assert "this check could not be performed" in html


def test_provenance_and_confidence_render_per_finding() -> None:
    html = render_html(make_report())
    assert "1acre.in" in html
    assert "kuda.png" in html
    assert "2026-07-19" in html
    assert "third-party overlay" in html


def test_issue_found_sorts_above_verified_ok() -> None:
    findings = [
        make_finding(status="verified_ok", claim="CLEAN ENTRY"),
        make_finding(status="issue_found", claim="PROBLEM ENTRY"),
    ]
    html = render_html(make_report(findings))
    assert html.index("PROBLEM ENTRY") < html.index("CLEAN ENTRY")


def test_untrusted_text_is_escaped() -> None:
    # Claims come from an LLM reading uploaded documents; party names come
    # off government records. Neither is trusted markup.
    finding = make_finding(claim='<script>alert("xss")</script> & co')
    html = render_html(make_report([finding]))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp; co" in html


def test_record_vintage_is_shown_separately_from_fetch_date() -> None:
    # "We looked today" and "the registry was last updated in 2019" are
    # different claims; collapsing them would hide staleness.
    finding = make_finding(
        provenance=Provenance(
            source_name="Bhu Bharati",
            source_type="manual",
            document="bhubharati.png",
            fetched_at=date(2026, 10, 17),
            record_updated_at=date(2019, 4, 2),
        )
    )
    html = render_html(make_report([finding]))
    assert "2026-10-17" in html
    assert "2019-04-02" in html
    assert "last updated" in html


def test_record_vintage_is_omitted_when_the_source_does_not_state_it() -> None:
    html = render_html(make_report())  # provenance has no record_updated_at
    assert "last updated" not in html


# --- the measured road-proximity table ----------------------------------

def make_assessment(*, within: bool = True, encroach: bool = False) -> RoadNetworkAssessment:
    roads = [
        RoadProximity(
            road_label="60 M",
            proposed_width_m=60,
            distance_m=12.0 if encroach else 940.0,
            segment_count=102,
        ),
        RoadProximity(
            road_label="30 M", proposed_width_m=30, distance_m=207.7, segment_count=394
        ),
    ]
    roads.sort(key=lambda r: r.distance_m)
    return RoadNetworkAssessment(
        location=LatLon(lat=17.856513, lon=79.630249, source="gis_readout"),
        network_label="KUDA Master Plan 2041 - proposed road network",
        within_mapped_area=within,
        roads=roads,
    )


def test_every_road_class_is_shown_not_just_the_deciding_one() -> None:
    html = render_html(make_report(assessment=make_assessment()))
    assert "60 M" in html
    assert "30 M" in html
    assert "207.7" in html


def test_a_clear_road_shows_its_margin() -> None:
    html = render_html(make_report(assessment=make_assessment()))
    # 30 M centreline 207.7 m away, corridor 15 m: clear by 192.7.
    assert "clear by 192.7 m" in html


def test_an_encroaching_road_is_marked_and_quantified() -> None:
    html = render_html(make_report(assessment=make_assessment(encroach=True)))
    # 60 M centreline 12 m away, corridor reaches 30 m: 18 m of overlap.
    assert "encroaches 18.0 m" in html
    assert 'class="hit"' in html


def test_the_table_states_where_it_measured_from() -> None:
    """Provenance, principle 3: the coordinate is part of the claim."""
    html = render_html(make_report(assessment=make_assessment()))
    assert "17.856513" in html
    assert "79.630249" in html
    assert "gis readout" in html
    assert "KUDA Master Plan 2041" in html


def test_an_out_of_area_table_warns_instead_of_reassuring() -> None:
    """Distances from outside the plan area must not read as clearance."""
    html = render_html(make_report(assessment=make_assessment(within=False)))
    assert "outside the area this network covers" in html


def test_no_table_is_rendered_without_an_assessment() -> None:
    html = render_html(make_report(assessment=None))
    assert 'class="roads"' not in html


# --- multi-source disagreement ------------------------------------------

from bhoomicheck.schemas.scoring import ScoredFinding  # noqa: E402


def make_scored(status: str = "issue_found", check_id: str = "land_records.prohibited_land"):
    return ScoredFinding(
        check_id=check_id,
        status=status,
        confidence_level="high",
        points_deducted=40,
        rule_matched=check_id,
    )


def report_with(scored: list, conflicts: list[str]) -> RiskReport:
    zoning = ZoningReport(
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
        extracts=[KudaMasterPlanExtract()],
        findings=[make_finding()],
    )
    return RiskReport(
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
        overall_score=60,
        scored_findings=scored,
        conflicting_checks=conflicts,
        specialist_reports=[zoning],
    )


def test_a_source_disagreement_is_shown_prominently() -> None:
    """Above the score, because it undermines the number."""
    html = render_html(report_with([make_scored()], ["land_records.prohibited_land"]))
    assert "Sources disagree" in html
    assert "land_records.prohibited_land" in html
    # Compare against the rendered div, not "score-value" alone -- that
    # string also appears in the stylesheet, far above the body.
    assert html.index("Sources disagree") < html.index('<div class="score-value">')


def test_no_banner_when_sources_agree() -> None:
    html = render_html(report_with([make_scored()], []))
    assert "Sources disagree" not in html


def test_the_summary_counts_what_was_scored_not_every_finding() -> None:
    """Where two sources answer one question, only one counts.

    Counting raw specialist findings would print a total the score
    contradicts -- "2 issues found" beside a score docked only once.
    """
    html = render_html(
        report_with([make_scored("issue_found"), make_scored("verified_ok", "zoning.ftl_buffer")], [])
    )
    assert "1 issue(s) found" in html
    assert "1 verified OK" in html
