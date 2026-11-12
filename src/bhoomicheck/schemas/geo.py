"""Geographic schemas: parcel location and computed road proximity.

Separate from parcel.py because a parcel's *revenue identity* (survey
number, village) and its *position on the earth* come from different
sources and are trusted differently. ParcelIdentifier comes off a deed
or a Bhu Bharati lookup; a coordinate comes off a GIS viewer and is only
as good as whoever read the cursor position.

This module holds no maths -- geo/road_network.py does the computing.
Schemas stay the dependency-free foundation, same as everywhere else.
"""

from typing import Literal

from pydantic import BaseModel, Field, computed_field


class LatLon(BaseModel):
    """A WGS-84 coordinate.

    Attributes:
        lat: Latitude in decimal degrees, north positive.
        lon: Longitude in decimal degrees, east positive.
        source: How this coordinate was obtained. Recorded because a
            cursor readout off a GIS viewer and a surveyed corner are
            both "a coordinate" but deserve very different trust, and a
            finding built on one should not read like a finding built on
            the other.
    """

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    source: Literal["gis_readout", "surveyed", "geocoded", "unknown"] = "unknown"


class RoadProximity(BaseModel):
    """How close one proposed road comes to a parcel, and whether it bites.

    Only `distance_m` and `proposed_width_m` are stored. Half-width,
    encroachment and clearance are computed from those two, so a
    RoadProximity cannot be constructed whose clearance disagrees with
    its distance -- the inconsistency is unrepresentable rather than
    merely untested.

    All distances are to the road's *centreline*, because that is what
    the master plan draws and what the width is measured about.

    Attributes:
        road_label: The road's class as the plan labels it, e.g.
            "45 M". Taken from the source data, never invented.
        proposed_width_m: Full corridor width the plan proposes, in
            metres. The corridor extends half this each side of the
            centreline.
        distance_m: Shortest distance from the parcel to the centreline.
        segment_count: How many centreline segments of this road class
            the network held. Carried so a reader can tell a well-mapped
            class from one represented by three stray lines.
    """

    road_label: str = Field(min_length=1)
    proposed_width_m: float = Field(gt=0.0)
    distance_m: float = Field(ge=0.0)
    segment_count: int = Field(ge=1)

    @computed_field
    @property
    def half_width_m(self) -> float:
        """Distance the corridor reaches either side of the centreline."""
        return self.proposed_width_m / 2.0

    @computed_field
    @property
    def encroaches(self) -> bool:
        """Whether the proposed corridor reaches the parcel at all."""
        return self.distance_m < self.half_width_m

    @computed_field
    @property
    def encroachment_m(self) -> float | None:
        """How far the corridor reaches past the parcel boundary, if it does.

        None rather than 0.0 when the road is clear: "no encroachment"
        and "encroaches by zero metres" would render identically in a
        report, and only one of them is a real measurement.
        """
        if not self.encroaches:
            return None
        return round(self.half_width_m - self.distance_m, 1)

    @computed_field
    @property
    def clearance_m(self) -> float | None:
        """Metres between the parcel and the corridor edge, if clear."""
        if self.encroaches:
            return None
        return round(self.distance_m - self.half_width_m, 1)


class RoadNetworkAssessment(BaseModel):
    """The result of measuring one parcel against a proposed road network.

    Attributes:
        location: The coordinate measured from, or -- when a boundary
            was supplied -- the centroid of that boundary, kept as the
            parcel's representative point for reporting.
        boundary: The parcel's boundary ring, when one was supplied.
            Empty when only a single coordinate was available.
        network_label: Which network answered, e.g. "KUDA Master Plan
            2041 - proposed road network". Carried so a finding can name
            its source instead of saying "the geometry".
        within_mapped_area: Whether the parcel falls inside the area the
            network actually covers.

            Load-bearing, and the reason this is not just a list of
            roads: a parcel 80 km outside the plan area would otherwise
            produce "nearest proposed road is 80 km away -- clear",
            which reads as reassurance when the truth is that we have no
            information about that parcel at all. False here must become
            a "not_verified" finding, never a "verified_ok" one.
        roads: Nearest approach per road class, nearest first. One entry
            per width class rather than per segment, because "there is a
            30 M road 207 m away" is the useful fact and the 394
            individual polyline segments of that class are not.
    """

    location: LatLon
    boundary: list[LatLon] = []
    network_label: str = Field(min_length=1)
    within_mapped_area: bool
    roads: list[RoadProximity] = []

    @computed_field
    @property
    def measured_from_boundary(self) -> bool:
        """Whether distances are to the parcel's edges or to one point.

        Reported because it changes what a clearance means. Measured from
        a single point, "clear by 12 m" says nothing about a corner 30 m
        away that the corridor may already cover.
        """
        return len(self.boundary) >= 3

    @computed_field
    @property
    def nearest(self) -> RoadProximity | None:
        """The closest proposed road of any class, if any was found."""
        return self.roads[0] if self.roads else None

    @computed_field
    @property
    def encroaching(self) -> list[RoadProximity]:
        """Every road whose proposed corridor reaches the parcel.

        Plural because a corner plot can sit inside two widenings at
        once, and reporting only the worst would understate the loss.
        """
        return [r for r in self.roads if r.encroaches]
