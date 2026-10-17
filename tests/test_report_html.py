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
from bhoomicheck.schemas.report import BaseAgentReport
from bhoomicheck.schemas.risk_report import DISCLAIMER, RiskReport

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


def make_report(findings: list[Finding] | None = None) -> RiskReport:
    specialist = BaseAgentReport(
        agent_name="zoning",
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
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
