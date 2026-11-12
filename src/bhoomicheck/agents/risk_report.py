"""Risk/Report Agent: combines every specialist report into one RiskReport.

Deterministic, same reasoning as LitigationAgent and ZoningAgent: by the
time this runs, every specialist report already exists and validated
itself against its own schema. This agent's only job is to pool their
findings, score them with RiskScorer, and assemble the result -- no LLM,
no free text, nothing to interpret.
"""

from datetime import datetime, timezone

from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.report import BaseAgentReport
from bhoomicheck.schemas.risk_report import RiskReport
from bhoomicheck.scoring.engine import RiskScorer

# When two agents answer the same question, which answer counts.
#
# This exists because a check_id is a *question*, not an agent's
# property, and some questions have more than one possible source. The
# Section 22-A prohibited register is the worked example: Bhu Bharati's
# land-details page cannot answer it, so LandRecordsAgent reports
# "not_verified", while IGRS has a dedicated Prohibited Property search
# that can. Both findings carry check_id "land_records.prohibited_land".
#
# Scoring every finding would then deduct for that check twice -- the
# same defect already found and fixed in the zoning setback check, where
# a measured and an eyeballed answer to one question both counted.
#
# issue_found outranks verified_ok deliberately. If two sources
# disagree, a due-diligence tool must surface the problem and let a
# human adjudicate, never quietly keep the reassuring answer.
_STATUS_PRECEDENCE = {"issue_found": 0, "verified_ok": 1, "not_verified": 2}
_CONFIDENCE_PRECEDENCE = {"high": 0, "medium": 1, "low": 2}


def _rank(finding: Finding) -> tuple[int, int]:
    return (
        _STATUS_PRECEDENCE.get(finding.status, 99),
        _CONFIDENCE_PRECEDENCE.get(finding.confidence.level, 99),
    )


def resolve_findings(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Reduce findings to one per check_id.

    Returns:
        (authoritative, superseded). The authoritative list is what gets
        scored and drives the headline. The superseded list is kept
        rather than discarded: a reader is entitled to know that a
        second source was consulted and what it said, and silently
        dropping a dissenting answer would be exactly the kind of
        invisible judgement this project exists to avoid.

    Input order is preserved for checks answered only once, so a report
    with no multi-source checks is byte-for-byte unchanged by this.
    """
    by_check: dict[str, list[Finding]] = {}
    for finding in findings:
        by_check.setdefault(finding.check_id, []).append(finding)

    authoritative: list[Finding] = []
    superseded: list[Finding] = []
    for check_findings in by_check.values():
        if len(check_findings) == 1:
            authoritative.append(check_findings[0])
            continue
        # Stable: ties keep the order the agents ran in, so the result is
        # reproducible rather than dependent on dict iteration luck.
        ranked = sorted(check_findings, key=_rank)
        authoritative.append(ranked[0])
        superseded.extend(ranked[1:])
    return authoritative, superseded


def conflicting_check_ids(
    authoritative: list[Finding], superseded: list[Finding]
) -> set[str]:
    """check_ids where two sources gave genuinely different answers.

    A source that could not answer ("not_verified") does not conflict
    with one that could -- that is the normal case this precedence
    handles. A real disagreement is two sources that both answered and
    answered differently, which is a reason to distrust both until a
    human looks.
    """
    chosen = {f.check_id: f for f in authoritative}
    return {
        f.check_id
        for f in superseded
        if f.status != "not_verified"
        and f.check_id in chosen
        and chosen[f.check_id].status != f.status
    }


class RiskReportAgent:
    """Assembles a RiskReport from every specialist report produced so far."""

    def __init__(self, scorer: RiskScorer | None = None) -> None:
        """
        Args:
            scorer: Defaults to a RiskScorer loaded from the real
                rules.yaml. Overridable so tests can score against a
                fixture rules file instead of the shipped one.
        """
        self.scorer = scorer or RiskScorer()

    def assemble(
        self, parcel: ParcelIdentifier, specialist_reports: list[BaseAgentReport]
    ) -> RiskReport:
        """Build the final report a buyer reads, from every specialist report.

        Args:
            parcel: The parcel being checked. Every specialist_reports
                entry must share this same parcel (enforced by
                RiskReport's own validator) -- mixing parcels here would
                be a serious, silent bug.
            specialist_reports: Every ZoningReport/LitigationReport/
                DocumentAnalysisReport (and any future specialist report)
                produced for this parcel.
        """
        all_findings = [
            finding for report in specialist_reports for finding in report.findings
        ]
        # One question, one score. specialist_reports keeps every agent's
        # own output untouched as the audit trail; only what counts
        # toward the score is reduced.
        authoritative, superseded = resolve_findings(all_findings)
        risk_score = self.scorer.score(authoritative)
        return RiskReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            overall_score=risk_score.score,
            scored_findings=risk_score.scored_findings,
            superseded_findings=superseded,
            conflicting_checks=sorted(
                conflicting_check_ids(authoritative, superseded)
            ),
            specialist_reports=specialist_reports,
        )
