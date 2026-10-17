"""Render a RiskReport as a self-contained HTML document.

The JSON a RiskReport serializes to is the machine contract; this is the
artifact a human actually reads before deciding whether to buy land. No
templating library: it is one template, and Jinja2 would be a dependency
earning its keep only once there are several.

Everything interpolated here is escaped. That is not boilerplate caution
-- finding claims originate from an LLM reading uploaded documents, and
party names come off government records, so both are untrusted text as
far as this file is concerned.

Three of the architecture principles are load-bearing in the layout:
- Principle 6 (fail loudly): "not verified" findings get their own
  visually distinct treatment and are never folded in with the clean
  ones. A reader must not be able to skim this and mistake "we could not
  check" for "we checked and it is fine".
- Principle 3 (provenance): every finding shows its source and fetch
  date inline, not in an appendix.
- Principle 4 (confidence): every finding shows its level and the reason
  given for it.
And the hard guardrail: the disclaimer ships with the report, at the top
where it cannot be missed, not only in a footer.
"""

from html import escape

from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.report import BaseAgentReport
from bhoomicheck.schemas.risk_report import RiskReport

# Worst first: a reader scanning only the top of a section should hit the
# problems, then the gaps, then the clean results.
_STATUS_ORDER = {"issue_found": 0, "not_verified": 1, "verified_ok": 2}

_STATUS_LABEL = {
    "issue_found": "Issue found",
    "not_verified": "Not verified",
    "verified_ok": "Verified OK",
}

_AGENT_LABEL = {
    "document_analysis": "Documents",
    "zoning": "Zoning &amp; master plan",
    "litigation": "Litigation",
    "land_records": "Land records",
    "owner_background": "Owner background",
    "growth_potential": "Growth potential",
}

_STYLES = """
  :root {
    --ink: #1a1a1a; --muted: #6b6b6b; --line: #e0ddd6; --bg: #faf9f7;
    --issue: #b3261e; --issue-bg: #fdf0ef;
    --unverified: #8a6100; --unverified-bg: #fdf6e8;
    --ok: #2e6b4f; --ok-bg: #f0f7f3;
  }
  * { box-sizing: border-box; }
  body {
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, sans-serif;
    color: var(--ink); background: var(--bg);
    margin: 0; padding: 40px 20px;
  }
  main { max-width: 820px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin: 0 0 4px; }
  h2 {
    font-size: 1.05rem; text-transform: uppercase; letter-spacing: .06em;
    color: var(--muted); margin: 36px 0 12px;
    border-bottom: 1px solid var(--line); padding-bottom: 6px;
  }
  .parcel { color: var(--muted); margin: 0 0 24px; }
  .disclaimer {
    border: 1px solid var(--line); border-left: 4px solid var(--muted);
    background: #fff; padding: 14px 18px; margin: 0 0 28px;
    font-size: .88rem; color: #444;
  }
  .score { display: flex; align-items: baseline; gap: 14px; margin: 0 0 8px; }
  .score-value { font-size: 2.6rem; font-weight: 600; line-height: 1; }
  .score-note { color: var(--muted); font-size: .9rem; }
  .summary { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 0; }
  .pill {
    border-radius: 999px; padding: 4px 12px; font-size: .82rem; font-weight: 600;
    border: 1px solid transparent;
  }
  .pill.issue { color: var(--issue); background: var(--issue-bg); border-color: #f0c9c6; }
  .pill.unverified { color: var(--unverified); background: var(--unverified-bg); border-color: #ecd9ab; }
  .pill.ok { color: var(--ok); background: var(--ok-bg); border-color: #c2dccf; }
  .finding {
    background: #fff; border: 1px solid var(--line);
    border-left: 4px solid var(--line);
    padding: 14px 18px; margin: 0 0 10px; border-radius: 3px;
  }
  .finding.issue_found { border-left-color: var(--issue); }
  .finding.not_verified { border-left-color: var(--unverified); }
  .finding.verified_ok { border-left-color: var(--ok); }
  .status { font-size: .76rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
  .finding.issue_found .status { color: var(--issue); }
  .finding.not_verified .status { color: var(--unverified); }
  .finding.verified_ok .status { color: var(--ok); }
  .claim { margin: 6px 0 10px; }
  .meta { font-size: .82rem; color: var(--muted); margin: 3px 0; }
  .meta b { color: #444; font-weight: 600; }
  footer { margin: 44px 0 0; padding-top: 14px; border-top: 1px solid var(--line);
           font-size: .8rem; color: var(--muted); }
  @media print { body { background: #fff; padding: 0; } .finding { break-inside: avoid; } }
"""


def _finding_html(finding: Finding) -> str:
    """One finding card: status, claim, confidence, provenance."""
    parts = [
        f'<div class="finding {escape(finding.status)}">',
        f'<div class="status">{_STATUS_LABEL.get(finding.status, escape(finding.status))}</div>',
        f'<div class="claim">{escape(finding.claim)}</div>',
        '<div class="meta"><b>Confidence:</b> '
        f"{escape(finding.confidence.level)} &mdash; {escape(finding.confidence.reason)}</div>",
    ]

    if finding.provenance is None:
        # Principle 6 again: say plainly that nothing backs this, rather
        # than leaving a blank space a reader fills in optimistically.
        parts.append('<div class="meta"><b>Source:</b> none &mdash; this check could not be performed</div>')
    else:
        source = escape(finding.provenance.source_name)
        where = finding.provenance.url or finding.provenance.document or ""
        parts.append(
            f'<div class="meta"><b>Source:</b> {source}'
            + (f" ({escape(str(where))})" if where else "")
            + f" &mdash; retrieved {escape(finding.provenance.fetched_at.isoformat())}</div>"
        )

    parts.append(f'<div class="meta"><b>Check:</b> {escape(finding.check_id)}</div>')
    parts.append("</div>")
    return "\n".join(parts)


def _section_html(report: BaseAgentReport) -> str:
    """One specialist agent's findings, worst status first."""
    heading = _AGENT_LABEL.get(report.agent_name, escape(report.agent_name))
    findings = sorted(report.findings, key=lambda f: _STATUS_ORDER.get(f.status, 99))
    cards = "\n".join(_finding_html(f) for f in findings)
    return f"<h2>{heading}</h2>\n{cards}"


def render_html(report: RiskReport) -> str:
    """Render the full report as one standalone HTML document."""
    parcel = report.parcel
    counts = {"issue_found": 0, "not_verified": 0, "verified_ok": 0}
    for specialist in report.specialist_reports:
        for finding in specialist.findings:
            if finding.status in counts:
                counts[finding.status] += 1

    sections = "\n".join(_section_html(r) for r in report.specialist_reports)
    title = f"Due diligence: survey no. {parcel.survey_no}, {parcel.village}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{_STYLES}</style>
</head>
<body>
<main>
  <h1>Land due-diligence report</h1>
  <p class="parcel">
    Survey no. <b>{escape(parcel.survey_no)}</b> &middot;
    {escape(parcel.village)} village &middot; {escape(parcel.mandal)} mandal &middot;
    {escape(parcel.district)} district<br>
    Generated {escape(report.generated_at.strftime("%d %B %Y, %H:%M UTC"))}
  </p>

  <div class="disclaimer">{escape(report.disclaimer)}</div>

  <div class="score">
    <div class="score-value">{report.overall_score}</div>
    <div class="score-note">out of 100 &mdash; starts at 100, with points deducted per finding
      by the rules in <code>scoring/rules.yaml</code></div>
  </div>
  <div class="summary">
    <span class="pill issue">{counts["issue_found"]} issue(s) found</span>
    <span class="pill unverified">{counts["not_verified"]} not verified</span>
    <span class="pill ok">{counts["verified_ok"]} verified OK</span>
  </div>

{sections}

  <footer>{escape(report.disclaimer)}</footer>
</main>
</body>
</html>
"""
