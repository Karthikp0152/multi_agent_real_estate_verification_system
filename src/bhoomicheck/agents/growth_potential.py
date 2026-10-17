"""Growth Potential Agent: KUDA master-plan extracts -> GrowthPotentialReport.

Deterministic, same pattern as ZoningAgent -- reads the same
KudaMasterPlanExtract, different fields (see schemas/zoning.py
docstring). One real difference from every other specialist agent built
so far: growth potential is informational/upside, not risk. Neither
"in a growth corridor" nor "not in a growth corridor" is a problem, so
these findings only ever use verified_ok/not_verified -- issue_found is
never produced here, because there's nothing to flag as wrong with a
parcel simply having lower growth potential.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.growth_potential import GrowthPotentialReport
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

SOURCE_NAME = "1acre.in (third-party overlay of KUDA Master Plan 2041)"

THIRD_PARTY_CAVEAT = (
    "based on 1acre.in's third-party overlay of KUDA Master Plan 2041, "
    "not an official KUDA record -- treat as a directional signal, not a "
    "guarantee of future development"
)

NOT_LEGIBLE_REASON = "not legible in the provided screenshot"


class GrowthPotentialAgent:
    """Builds a validated GrowthPotentialReport from master-plan extracts."""

    def report(
        self,
        parcel: ParcelIdentifier,
        extracts: list[tuple[Path, KudaMasterPlanExtract]],
    ) -> GrowthPotentialReport:
        """Assemble a report from (uploaded screenshot, parsed extract) pairs.

        Args:
            parcel: The parcel this growth-potential check is for.
            extracts: One entry per screenshot examined -- the same kind
                of (upload_path, KudaMasterPlanExtract) pair ZoningAgent
                takes, since it's the same underlying lookup.
        """
        fetched_at = datetime.now(timezone.utc).date()
        findings = [
            finding
            for upload_path, extract in extracts
            for finding in self._findings_for(upload_path, extract, fetched_at)
        ]
        return GrowthPotentialReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            extracts=[extract for _, extract in extracts],
            findings=findings,
        )

    def _findings_for(
        self, upload_path: Path, extract: KudaMasterPlanExtract, fetched_at: date
    ) -> list[Finding]:
        provenance = Provenance(
            source_name=SOURCE_NAME,
            source_type="manual",
            document=upload_path.name,
            fetched_at=fetched_at,
            # The map states the G.O. date it was approved under; a plan
            # from years ago may since have been revised.
            record_updated_at=extract.plan_approved_on,
        )
        return [
            self._opportunity_finding(
                extract.in_growth_corridor,
                provenance,
                check_id="growth_potential.growth_corridor",
                what="whether this parcel falls within a designated Growth Corridor or Village Expansion Zone",
                present="parcel falls within a designated Growth Corridor / Village Expansion Zone",
                absent="parcel does not fall within a designated Growth Corridor / Village Expansion Zone",
            ),
            self._opportunity_finding(
                extract.near_major_road_upgrade,
                provenance,
                check_id="growth_potential.road_upgrade_proximity",
                what="whether a proposed major road upgrade is near this parcel",
                present="a proposed Outer Ring Road / Arterial Road upgrade is near this parcel",
                absent="no proposed major road upgrade was visible near this parcel",
            ),
            self._opportunity_finding(
                extract.near_approved_layout,
                provenance,
                check_id="growth_potential.approved_layout_proximity",
                what="whether this parcel is near an area marked as an Approved Layout",
                present="parcel is near an area marked as an Approved Layout",
                absent="no Approved Layout area was visible near this parcel",
            ),
        ]

    def _opportunity_finding(
        self,
        value: bool | None,
        provenance: Provenance,
        *,
        check_id: str,
        what: str,
        present: str,
        absent: str,
    ) -> Finding:
        if value is None:
            return Finding(
                check_id=check_id,
                claim=what,
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        return Finding(
            check_id=check_id,
            claim=present if value else absent,
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason=THIRD_PARTY_CAVEAT),
        )
