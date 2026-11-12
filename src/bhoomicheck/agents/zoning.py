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
from bhoomicheck.schemas.geo import RoadNetworkAssessment
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

# Clearing the road corridor is necessary but not sufficient: building
# rules impose a further setback from the road boundary, varying by road
# width and building use. We do not model those, and a "clear" result
# must not be allowed to imply we did.
SETBACK_RULES_CAVEAT = (
    "measured against the proposed road corridor only -- building-rule "
    "setbacks from the road boundary are additional and are not modelled "
    "here, and the measurement is only as accurate as the digitised plan "
    "it is computed from"
)


class ZoningAgent:
    """Builds a validated ZoningReport from one or more master-plan extracts."""

    def report(
        self,
        parcel: ParcelIdentifier,
        extracts: list[tuple[Path, KudaMasterPlanExtract]],
        road_assessment: RoadNetworkAssessment | None = None,
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
            road_assessment: Computed proximity to the master plan's
                proposed road network, when parcel coordinates were
                available. When this can answer the setback question it
                REPLACES the screenshot-derived answer rather than
                supplementing it -- a report carrying both a measured
                and an eyeballed answer to one question makes the reader
                arbitrate, and would also double-count in scoring.
        """
        fetched_at = datetime.now(timezone.utc).date()
        # Geometry answers only for a parcel inside the area the network
        # covers; outside it, the computed distances describe the edge of
        # the map rather than the parcel (see RoadNetworkAssessment).
        geometry_answers = (
            road_assessment is not None and road_assessment.within_mapped_area
        )
        findings = [
            finding
            for upload_path, extract in extracts
            for finding in self._findings_for(
                upload_path, extract, fetched_at, skip_road=geometry_answers
            )
        ]
        if geometry_answers:
            assert road_assessment is not None  # narrowed by geometry_answers
            findings.append(
                self._computed_road_finding(road_assessment, extracts, fetched_at)
            )
        return ZoningReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            extracts=[extract for _, extract in extracts],
            road_assessment=road_assessment,
            findings=findings,
        )

    def _findings_for(
        self,
        upload_path: Path,
        extract: KudaMasterPlanExtract,
        fetched_at: date,
        skip_road: bool = False,
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
        findings = [self._zoning_classification_finding(extract, provenance)]
        # Zone classification and FTL buffers are polygons the road
        # network does not contain, so those stay screenshot-derived
        # whatever the geometry can do for roads.
        if not skip_road:
            findings.append(self._road_widening_finding(extract, provenance))
        findings.append(self._ftl_buffer_finding(extract, provenance))
        return findings

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

    def _computed_road_finding(
        self,
        assessment: RoadNetworkAssessment,
        extracts: list[tuple[Path, KudaMasterPlanExtract]],
        fetched_at: date,
    ) -> Finding:
        """The flagship check, answered by measurement rather than by eye.

        This is the one finding in the whole system that states a number
        a surveyor can go and disprove. Its confidence is "high" not
        because the underlying plan is beyond doubt -- it is a
        digitisation, and SETBACK_RULES_CAVEAT says so -- but because
        the step this project previously got wrong, turning a map into a
        yes/no, is no longer a judgement call.
        """
        provenance = Provenance(
            source_name=assessment.network_label,
            # "manual" of the three adapter modes: the network came from a
            # file a human supplied, not a live API or a scrape.
            source_type="manual",
            document=f"{assessment.network_label} (computed geometry)",
            fetched_at=fetched_at,
        )

        lat, lon = assessment.location.lat, assessment.location.lon
        if assessment.measured_from_boundary:
            where = (
                f"measured from the parcel boundary, {len(assessment.boundary)} "
                f"vertices, centroid {lat:.6f}, {lon:.6f}"
            )
            # A boundary measurement answers the question a setback
            # actually asks, so it carries no extra caveat.
            geometry_caveat = ""
        else:
            where = f"measured from a single point at {lat:.6f}, {lon:.6f}"
            # Honest limitation, not boilerplate: a corridor can clip a
            # parcel's corner while sitting comfortably clear of its
            # centre. On the synthetic fixture, a road reported "clear by
            # 77.5 m" from the centre was 2.5 m inside the parcel once
            # its real edges were used. A point-based clearance must not
            # be read as strongly as a boundary-based one.
            geometry_caveat = (
                "; measured from one point rather than the parcel boundary, so a "
                "corridor clipping an edge or corner would not be detected -- "
                "supply the parcel boundary for a conclusive answer"
            )

        # A measured result that contradicts the screenshot reading is
        # worth surfacing: it is the clearest evidence of why this check
        # stopped being a vision task, and it tells a reader which of the
        # two numbers in their notes to throw away.
        eyeballed = {
            extract.road_widening_affects_parcel
            for _, extract in extracts
            if extract.road_widening_affects_parcel is not None
        }
        disagreement = ""
        if eyeballed and (True in eyeballed) != bool(assessment.encroaching):
            read_as = "affected" if True in eyeballed else "unaffected"
            disagreement = (
                f"; note this supersedes the master-plan screenshot, which was "
                f"read as {read_as}"
            )

        if assessment.encroaching:
            worst = assessment.encroaching[0]
            descriptions = []
            for r in assessment.encroaching:
                if r.distance_m == 0.0:
                    # Distance zero means the centreline itself touches or
                    # crosses the parcel -- a band the full proposed width
                    # runs through the land, which is materially worse
                    # than a corridor reaching over one edge.
                    descriptions.append(
                        f"{r.road_label} (centreline passes through the parcel; "
                        f"the full {r.proposed_width_m:.0f} m corridor crosses it)"
                    )
                else:
                    descriptions.append(
                        f"{r.road_label} (centreline {r.distance_m:.1f} m away, corridor "
                        f"reaches {r.encroachment_m:.1f} m into the parcel)"
                    )
            roads = ", ".join(descriptions)
            return Finding(
                check_id="zoning.road_widening_setback",
                claim=(
                    f"a proposed road corridor reaches this parcel: {roads}. "
                    f"The {worst.road_label} widening alone implies surrendering "
                    f"land up to {worst.encroachment_m:.1f} m inside the boundary "
                    f"({where})"
                ),
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(
                    level="high",
                    reason=f"computed from plan geometry{disagreement}"
                    f"{geometry_caveat} -- " + SETBACK_RULES_CAVEAT,
                ),
            )

        nearest = assessment.nearest
        if nearest is None:
            # Guarded rather than assumed: within_mapped_area is only true
            # when a nearest road was found, so this is unreachable today
            # and must stay an honest "not verified" if that ever changes.
            return Finding(
                check_id="zoning.road_widening_setback",
                claim="whether a proposed road requires a setback from this parcel",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(
                    level="low",
                    reason="the road network returned no roads for this parcel",
                ),
            )
        return Finding(
            check_id="zoning.road_widening_setback",
            claim=(
                f"no proposed road corridor reaches this parcel: the nearest is "
                f"the {nearest.road_label} road, centreline {nearest.distance_m:.1f} m "
                f"away, whose corridor extends {nearest.half_width_m:.1f} m either "
                f"side -- clear by {nearest.clearance_m:.1f} m ({where})"
            ),
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(
                # A point-based clearance is a weaker claim than a
                # boundary-based one, so it does not get to call itself
                # "high": the measurement is exact, but it answers a
                # slightly different question than the one asked.
                level="high" if assessment.measured_from_boundary else "medium",
                reason=f"computed from plan geometry{disagreement}"
                f"{geometry_caveat} -- " + SETBACK_RULES_CAVEAT,
            ),
        )
