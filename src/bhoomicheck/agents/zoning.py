"""Zoning Agent: KUDA master-plan extracts -> ZoningReport.

Deterministic, same reasoning as LitigationAgent: by the time this runs,
KudaMasterPlanAdapter.parse() has already turned a screenshot into a
structured KudaMasterPlanExtract, so there's no free text left to
interpret -- just a fixed mapping from fields to Findings.

Every finding's confidence explicitly carries the caveat noted in
adapters/kuda.py: the source is 1acre.in's third-party overlay of the
KUDA Master Plan 2041, not an official KUDA-issued certificate. That
caveat has to live here, at the point a Finding is actually created --
noting it in the adapter's docstring alone would be easy to forget by
the time this agent (or a report reader) makes a decision from it.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract, ZoningReport

SOURCE_NAME = "1acre.in (third-party overlay of KUDA Master Plan 2041)"

THIRD_PARTY_CAVEAT = (
    "based on 1acre.in's third-party overlay of KUDA Master Plan 2041, "
    "not an official KUDA zonal certificate -- confirm with KUDA before "
    "relying on this for a purchase decision"
)

NOT_LEGIBLE_REASON = "not legible in the provided screenshot"


class ZoningAgent:
    """Builds a validated ZoningReport from one or more master-plan extracts."""

    def report(
        self,
        parcel: ParcelIdentifier,
        extracts: list[tuple[Path, KudaMasterPlanExtract]],
    ) -> ZoningReport:
        """Assemble a report from (uploaded screenshot, parsed extract) pairs.

        Args:
            parcel: The parcel this zoning check is for.
            extracts: One entry per screenshot examined -- the file the
                human uploaded (for provenance) paired with what
                KudaMasterPlanAdapter.parse() produced from it. The
                filename is a system-known fact fed in by the caller,
                same as DocumentAnalysisAgent's PDF paths -- never
                guessed here.
        """
        fetched_at = datetime.now(timezone.utc).date()
        findings = [
            finding
            for upload_path, extract in extracts
            for finding in self._findings_for(upload_path, extract, fetched_at)
        ]
        return ZoningReport(
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
            self._zoning_classification_finding(extract, provenance),
            self._road_widening_finding(extract, provenance),
            self._ftl_buffer_finding(extract, provenance),
        ]

    def _zoning_classification_finding(
        self, extract: KudaMasterPlanExtract, provenance: Provenance
    ) -> Finding:
        if extract.zoning_classification is None:
            return Finding(
                check_id="zoning.classification",
                claim="parcel's zoning classification per KUDA Master Plan 2041",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        return Finding(
            check_id="zoning.classification",
            claim=(
                f'parcel falls under zoning classification '
                f'"{extract.zoning_classification}" per KUDA Master Plan 2041'
            ),
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason=THIRD_PARTY_CAVEAT),
        )

    def _road_widening_finding(
        self, extract: KudaMasterPlanExtract, provenance: Provenance
    ) -> Finding:
        if extract.road_widening_affects_parcel is None:
            return Finding(
                check_id="zoning.road_widening_setback",
                claim="whether a master-plan road requires a setback surrender from this parcel",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if extract.road_widening_affects_parcel:
            setback = (
                f" (approx. {extract.setback_required_meters} m)"
                if extract.setback_required_meters is not None
                else ""
            )
            return Finding(
                check_id="zoning.road_widening_setback",
                claim=(
                    "a master-plan road affects this parcel, requiring a "
                    f"setback surrender{setback}"
                ),
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(level="medium", reason=THIRD_PARTY_CAVEAT),
            )
        return Finding(
            check_id="zoning.road_widening_setback",
            claim="no master-plan road affecting this parcel was visible",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason=THIRD_PARTY_CAVEAT),
        )

    def _ftl_buffer_finding(
        self, extract: KudaMasterPlanExtract, provenance: Provenance
    ) -> Finding:
        if extract.within_ftl_buffer is None:
            return Finding(
                check_id="zoning.ftl_buffer",
                claim="whether this parcel falls within a lake FTL buffer zone",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if extract.within_ftl_buffer:
            return Finding(
                check_id="zoning.ftl_buffer",
                claim="parcel falls within a lake Full Tank Level buffer zone",
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(level="medium", reason=THIRD_PARTY_CAVEAT),
            )
        return Finding(
            check_id="zoning.ftl_buffer",
            claim="parcel does not fall within a lake Full Tank Level buffer zone",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason=THIRD_PARTY_CAVEAT),
        )
