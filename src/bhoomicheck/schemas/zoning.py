"""Zoning schemas: raw master-plan extract and the Zoning Agent's report.

KudaMasterPlanExtract was originally defined in adapters/kuda.py, but
ZoningReport needs to embed it, and schemas should never depend on
adapters (the reverse is already true -- e.g. KudaMasterPlanAdapter
imports ParcelIdentifier from here). Moving it here mirrors
schemas/document.py and schemas/litigation.py, which do the same for the
same reason.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from bhoomicheck.schemas.geo import RoadNetworkAssessment
from bhoomicheck.schemas.report import BaseAgentReport


class KudaMasterPlanExtract(BaseModel):
    """Raw fields expected off a zoomed-in 1acre.in master-plan screenshot.

    PROVISIONAL -- see adapters/kuda.py module docstring. Every field is
    optional because we don't yet know which of these are actually
    legible on a screenshot; nobody has supplied a real sample yet.

    One screenshot of the KUDA Master Plan 2041 serves two different
    agents: ZoningAgent reads the risk-framed fields (does a road cut
    through *this* parcel, forcing a setback), while GrowthPotentialAgent
    reads the opportunity-framed fields below (is a major upgrade nearby
    without directly affecting the parcel) -- the map's own legend
    ("Growth Corridor 1/2", "Village Expansion Zone", "Approved Layouts")
    is what grounds those fields, not a second lookup on a second source.

    Attributes:
        zoning_classification: e.g. "residential", "commercial" -- exact
            vocabulary 1acre/KUDA uses is unconfirmed, so this stays free
            text rather than a Literal for now.
        road_widening_affects_parcel: Whether a master-plan road passes
            through or borders the parcel, forcing a setback surrender --
            this is the central risk from CLAUDE.md's problem statement.
        setback_required_meters: Width of land the owner would have to
            surrender, if visible/labeled on the map.
        within_ftl_buffer: Whether the parcel falls inside a lake Full
            Tank Level buffer zone.
        in_growth_corridor: Whether the parcel falls inside a "Growth
            Corridor 1/2" or "Village Expansion Zone" area per the map's
            legend -- an opportunity signal, not a risk one.
        near_major_road_upgrade: Whether a proposed Outer Ring Road /
            Arterial Road widening passes near (but not through) the
            parcel -- nearby infrastructure investment without the
            setback risk road_widening_affects_parcel already captures.
        near_approved_layout: Whether the parcel is near an area the map
            marks as an "Approved Layout" -- a proxy for nearby
            development activity/demand.
        plan_approved_on: The G.O. date the map itself carries ("Approved
            vide G.O. Ms. No. 202 ... dated 17-11-2024"). Same idea as Bhu
            Bharati's record vintage: a master plan approved years ago may
            since have been revised, so the plan's own date belongs in the
            finding's provenance rather than being discarded.
        notes: Anything else visible that doesn't fit a structured field.
    """

    zoning_classification: str | None = None
    road_widening_affects_parcel: bool | None = None
    setback_required_meters: float | None = None
    within_ftl_buffer: bool | None = None
    in_growth_corridor: bool | None = None
    near_major_road_upgrade: bool | None = None
    near_approved_layout: bool | None = None
    plan_approved_on: date | None = None
    notes: str | None = None


class ZoningReport(BaseAgentReport):
    """Full output contract of the Zoning Agent.

    Inherits the base contract (parcel, findings, timestamp) and adds the
    raw master-plan extracts the findings were built from.

    Attributes:
        agent_name: Pinned to "zoning" -- a report claiming this shape
            under another agent's name is a validation error.
        extracts: One entry per master-plan screenshot examined. At least
            one is required: this agent only runs when a lookup was
            actually performed, so an empty list means a malfunction, not
            a clean result.
        road_assessment: The computed measurement against the proposed
            road network, when parcel coordinates were available. Carried
            on the report (not just folded into a finding) so a reader
            can see every road class and its distance, not only the one
            that drove the verdict -- a parcel clear by 3 m and one clear
            by 300 m produce the same finding but are not the same buy.
            None when no coordinate was supplied, in which case the
            setback finding fell back to the screenshot reading.
    """

    agent_name: Literal["zoning"] = "zoning"
    extracts: list[KudaMasterPlanExtract] = Field(min_length=1)
    road_assessment: RoadNetworkAssessment | None = None
