"""Measure a parcel against a master plan's proposed road network.

This module exists to replace a guess with a measurement. Before it, the
"does a master-plan road force a setback on this parcel" check -- the
single risk CLAUDE.md names as the reason the project exists -- was
answered by a vision model looking at a map screenshot and returning a
boolean. That answer could not be checked, reproduced, or shown to be
wrong. Here the same question produces "the nearest proposed road is a
30 M sub-arterial whose centreline is 207.7 m away; the corridor reaches
15 m either side; the parcel is clear by 192.7 m", which a surveyor can
confirm or refute.

No new dependencies: the mathematics is a local map projection and
point-to-segment distance, both short enough to read. A geospatial
library (shapely/pyproj/geopandas) would do this too, but it would also
pull in GEOS and PROJ as binary dependencies for two functions, and
hide the only part of this file worth understanding.

ACCURACY, HONESTLY: distances are computed on a flat local projection
centred on the parcel, which is accurate to well under a metre over the
few kilometres that matter here, so the projection is not the weak link.
The weak link is the source network's own alignment -- a digitised plan
is only as good as its digitisation. This module gives precision, not
accuracy; it can tell you a centreline is 207.7 m away but cannot tell
you the centreline was drawn in the right place.

WHAT IS NOT MODELLED: building setback rules. Clearing the road corridor
is necessary but not sufficient -- building rules impose a further
setback from the road boundary, which varies by road width and building
use, and this module does not invent those numbers. A parcel reported
"clear" here has cleared the widening, not the whole permission problem.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from bhoomicheck.schemas.geo import LatLon, RoadNetworkAssessment, RoadProximity

# Mean earth radius (metres). The local projection below is insensitive
# to the choice between this and a proper ellipsoid at these distances.
_EARTH_RADIUS_M = 6_371_000.0

# Beyond this, a "nearest road" result stops being information about the
# parcel and becomes information about the edge of the map. 5 km is
# chosen because a town master plan maps roads densely: any parcel truly
# inside the planning area has *something* proposed within a few km, so a
# larger gap means the parcel is outside what this network covers.
_DEFAULT_COVERAGE_RADIUS_M = 5_000.0


class RoadNetworkError(RuntimeError):
    """Raised when a network file is missing or unusable.

    A distinct type so callers can turn it into an honest "not verified"
    finding instead of a crash -- missing data must never read as "no
    problem found" (architecture principle 6).
    """


@dataclass(frozen=True)
class _Segment:
    """One polyline of road centreline, with its proposed width.

    Deliberately a plain dataclass of tuples rather than a Pydantic
    model: there are tens of thousands of these, they are internal, and
    they are validated once at load time rather than per access.
    """

    width_m: float
    points: tuple[tuple[float, float], ...]  # (lon, lat) pairs
    min_lon: float
    max_lon: float
    min_lat: float
    max_lat: float


def _point_to_segment_m(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Shortest distance from point P to the line *segment* AB, all in metres.

    The clamping of `t` to [0, 1] is the whole point: without it this
    measures distance to the infinite line through A and B, which would
    happily report that a parcel is 5 m from a road whose nearest actual
    tarmac is a kilometre away up the same bearing.
    """
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _range_gap(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
    """Separation between two intervals; 0 when they overlap."""
    if a_high < b_low:
        return b_low - a_high
    if b_high < a_low:
        return a_low - b_high
    return 0.0


def _orientation(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    """Cross product sign: >0 if C is left of AB, <0 if right, 0 if collinear."""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _segments_intersect(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> bool:
    """Whether segments AB and CD cross.

    Needed because a road centreline that passes *through* a parcel is
    at distance zero from it, and no endpoint-to-segment measurement
    would ever discover that -- the four endpoint distances can all be
    large while the lines cross in the middle.
    """
    d1 = _orientation(cx, cy, dx, dy, ax, ay)
    d2 = _orientation(cx, cy, dx, dy, bx, by)
    d3 = _orientation(ax, ay, bx, by, cx, cy)
    d4 = _orientation(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    # Collinear touching cases. Treated as intersecting: a centreline
    # running along a parcel's edge does reach it.
    def on_segment(px, py, qx, qy, rx, ry) -> bool:
        return (
            min(px, qx) <= rx <= max(px, qx)
            and min(py, qy) <= ry <= max(py, qy)
        )
    if d1 == 0 and on_segment(cx, cy, dx, dy, ax, ay):
        return True
    if d2 == 0 and on_segment(cx, cy, dx, dy, bx, by):
        return True
    if d3 == 0 and on_segment(ax, ay, bx, by, cx, cy):
        return True
    if d4 == 0 and on_segment(ax, ay, bx, by, dx, dy):
        return True
    return False


def _segment_to_segment_m(
    ax: float, ay: float, bx: float, by: float,
    cx: float, cy: float, dx: float, dy: float,
) -> float:
    """Shortest distance between two line segments, in metres."""
    if _segments_intersect(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    # Not crossing: the closest approach is from some endpoint to the
    # other segment, so four point-to-segment measurements suffice.
    return min(
        _point_to_segment_m(ax, ay, cx, cy, dx, dy),
        _point_to_segment_m(bx, by, cx, cy, dx, dy),
        _point_to_segment_m(cx, cy, ax, ay, bx, by),
        _point_to_segment_m(dx, dy, ax, ay, bx, by),
    )


class RoadNetwork:
    """A proposed road network, queryable by parcel location.

    Load once and reuse: parsing is the expensive part, querying is not.

    Attributes:
        label: Human name of the network, used in findings and reports.
        source: Where the data came from, for provenance.
        segments: Parsed centrelines.
    """

    def __init__(self, label: str, source: str, segments: list[_Segment]) -> None:
        if not segments:
            raise RoadNetworkError("road network contains no usable segments")
        self.label = label
        self.source = source
        self.segments = segments
        self.min_lon = min(s.min_lon for s in segments)
        self.max_lon = max(s.max_lon for s in segments)
        self.min_lat = min(s.min_lat for s in segments)
        self.max_lat = max(s.max_lat for s in segments)

    @classmethod
    def from_json(cls, path: Path) -> RoadNetwork:
        """Load a distilled road-network file.

        The expected shape is produced by scripts/distil_road_network.py:
        a `label`, a `source`, and `segments` of {"w": width_m, "p":
        [[lon, lat], ...]}. Segments with no width are dropped rather
        than defaulted -- a road of unknown width cannot support a
        setback claim, and inventing a width would manufacture exactly
        the kind of confident wrong number this project exists to catch.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RoadNetworkError(f"road network file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise RoadNetworkError(f"road network file is not valid JSON: {path}") from exc

        segments: list[_Segment] = []
        for entry in raw.get("segments", []):
            width = entry.get("w")
            pts = entry.get("p") or []
            if width is None or len(pts) < 2:
                continue
            points = tuple((float(lon), float(lat)) for lon, lat in pts)
            lons = [p[0] for p in points]
            lats = [p[1] for p in points]
            segments.append(
                _Segment(
                    width_m=float(width),
                    points=points,
                    min_lon=min(lons),
                    max_lon=max(lons),
                    min_lat=min(lats),
                    max_lat=max(lats),
                )
            )
        return cls(
            label=raw.get("label") or path.stem,
            source=raw.get("source") or "unspecified",
            segments=segments,
        )

    def assess(
        self,
        location: LatLon | None = None,
        coverage_radius_m: float = _DEFAULT_COVERAGE_RADIUS_M,
        boundary: Sequence[LatLon] | None = None,
    ) -> RoadNetworkAssessment:
        """Measure one parcel against every proposed road.

        Returns the nearest approach per width class, nearest first.

        Args:
            location: A single parcel coordinate. Simpler to obtain (a
                GIS cursor readout gives one), but it measures from a
                point: a corridor can clip a parcel's corner while
                being comfortably far from its centre, so a point-based
                result can understate the risk.
            coverage_radius_m: How far the nearest road may be before we
                conclude the parcel is outside this network's coverage
                rather than comfortably clear of its roads.
            boundary: The parcel's boundary as an ordered ring of at
                least three vertices. Preferred when available: distance
                is then measured from the parcel's actual edges, which
                is the question a setback asks. The ring is closed
                automatically -- do not repeat the first vertex.

        Exactly one of `location` or `boundary` is required.
        """
        if boundary is not None and len(boundary) >= 3:
            return self._assess_boundary(list(boundary), coverage_radius_m)
        if boundary is not None:
            raise RoadNetworkError(
                f"a parcel boundary needs at least 3 vertices, got {len(boundary)}"
            )
        if location is None:
            raise RoadNetworkError("assess() needs either a location or a boundary")
        return self._assess_point(location, coverage_radius_m)

    def _projection(self, lat: float):
        """Local equirectangular projection: x east, y north, both metres.

        Centred on the parcel so distortion is smallest exactly where the
        measurement happens.
        """
        lon_scale = math.cos(math.radians(lat)) * _EARTH_RADIUS_M
        return lon_scale, _EARTH_RADIUS_M

    def _assess_point(
        self, location: LatLon, coverage_radius_m: float
    ) -> RoadNetworkAssessment:
        lon_scale, lat_scale = self._projection(location.lat)
        deg = math.radians

        def measure(seg: _Segment) -> float:
            xs = [deg(lon - location.lon) * lon_scale for lon, _ in seg.points]
            ys = [deg(lat - location.lat) * lat_scale for _, lat in seg.points]
            return min(
                _point_to_segment_m(0.0, 0.0, xs[i], ys[i], xs[i + 1], ys[i + 1])
                for i in range(len(xs) - 1)
            )

        best, counts = self._nearest_per_width(
            measure,
            query_bounds=(location.lon, location.lon, location.lat, location.lat),
            origin=(location.lon, location.lat),
            scales=(lon_scale, lat_scale),
        )
        return self._build_assessment(
            location=location,
            boundary=[],
            best=best,
            counts=counts,
            coverage_radius_m=coverage_radius_m,
            inside_test=(location.lat, location.lon),
        )

    def _assess_boundary(
        self, boundary: list[LatLon], coverage_radius_m: float
    ) -> RoadNetworkAssessment:
        # Centroid of the vertices, used as the projection origin and as
        # the representative point the report quotes. Not an
        # area-weighted centroid: this only has to sit inside the parcel
        # to keep projection error negligible, and for a survey parcel
        # the vertex mean does.
        centre_lat = sum(p.lat for p in boundary) / len(boundary)
        centre_lon = sum(p.lon for p in boundary) / len(boundary)
        lon_scale, lat_scale = self._projection(centre_lat)
        deg = math.radians

        def to_xy(lon: float, lat: float) -> tuple[float, float]:
            return (deg(lon - centre_lon) * lon_scale, deg(lat - centre_lat) * lat_scale)

        ring = [to_xy(p.lon, p.lat) for p in boundary]
        # Close the ring: the final edge from last vertex back to first is
        # a real parcel edge and a road can encroach across it.
        edges = list(zip(ring, ring[1:] + ring[:1]))

        def measure(seg: _Segment) -> float:
            pts = [to_xy(lon, lat) for lon, lat in seg.points]
            nearest = math.inf
            for i in range(len(pts) - 1):
                (rx1, ry1), (rx2, ry2) = pts[i], pts[i + 1]
                for (ex1, ey1), (ex2, ey2) in edges:
                    d = _segment_to_segment_m(ex1, ey1, ex2, ey2, rx1, ry1, rx2, ry2)
                    if d < nearest:
                        nearest = d
                        if nearest == 0.0:
                            return 0.0  # crosses the boundary; cannot do better
            return nearest

        best, counts = self._nearest_per_width(
            measure,
            query_bounds=(
                min(p.lon for p in boundary),
                max(p.lon for p in boundary),
                min(p.lat for p in boundary),
                max(p.lat for p in boundary),
            ),
            origin=(centre_lon, centre_lat),
            scales=(lon_scale, lat_scale),
        )
        return self._build_assessment(
            location=LatLon(
                lat=centre_lat, lon=centre_lon, source=boundary[0].source
            ),
            boundary=boundary,
            best=best,
            counts=counts,
            coverage_radius_m=coverage_radius_m,
            inside_test=(centre_lat, centre_lon),
        )

    def _nearest_per_width(
        self,
        measure,
        query_bounds: tuple[float, float, float, float],
        origin: tuple[float, float],
        scales: tuple[float, float],
    ) -> tuple[dict[float, float], dict[float, int]]:
        """Nearest distance and segment count per width class.

        Shared by point and boundary modes: only `measure` differs, which
        keeps the bounding-box prefilter and the per-class bookkeeping in
        one place rather than duplicated and able to drift apart.
        """
        q_min_lon, q_max_lon, q_min_lat, q_max_lat = query_bounds
        lon_scale, lat_scale = scales
        one_degree = math.radians(1.0)

        best: dict[float, float] = {}
        counts: dict[float, int] = {}

        for seg in self.segments:
            counts[seg.width_m] = counts.get(seg.width_m, 0) + 1
            current = best.get(seg.width_m)

            # Cheap rejection: the closest this segment could possibly be,
            # judging only by bounding boxes. If even that lower bound is
            # worse than what we have for this width class, skip it.
            if current is not None:
                gap_x = (
                    _range_gap(q_min_lon, q_max_lon, seg.min_lon, seg.max_lon)
                    * one_degree
                    * lon_scale
                )
                gap_y = (
                    _range_gap(q_min_lat, q_max_lat, seg.min_lat, seg.max_lat)
                    * one_degree
                    * lat_scale
                )
                if math.hypot(gap_x, gap_y) >= current:
                    continue

            d = measure(seg)
            if current is None or d < current:
                best[seg.width_m] = d

        return best, counts

    def _build_assessment(
        self,
        location: LatLon,
        boundary: list[LatLon],
        best: dict[float, float],
        counts: dict[float, int],
        coverage_radius_m: float,
        inside_test: tuple[float, float],
    ) -> RoadNetworkAssessment:
        roads = sorted(
            (
                RoadProximity(
                    road_label=self._label_for(width),
                    proposed_width_m=width,
                    distance_m=round(distance, 1),
                    segment_count=counts[width],
                )
                for width, distance in best.items()
            ),
            key=lambda r: r.distance_m,
        )

        nearest_m = roads[0].distance_m if roads else math.inf
        lat, lon = inside_test
        in_bbox = (
            self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon
        )
        # Both tests, because each catches a case the other misses: the
        # bounding box rejects a parcel in another district outright,
        # while the radius catches one technically inside the box but in
        # a corner the plan does not actually cover.
        within = in_bbox and nearest_m <= coverage_radius_m

        return RoadNetworkAssessment(
            location=location,
            boundary=boundary,
            network_label=self.label,
            within_mapped_area=within,
            roads=roads,
        )

    @staticmethod
    def _label_for(width_m: float) -> str:
        """Render a width the way the plan's own legend does, e.g. "45 M"."""
        whole = int(width_m)
        return f"{whole} M" if whole == width_m else f"{width_m:g} M"


# --- locating the network file ------------------------------------------
#
# The geometry data is NOT shipped in this repository: it lives wherever
# the operator put it, and the repo carries only the code plus a
# synthetic fixture. That keeps real plan data out of git history (which
# cannot be un-published once pushed) and makes the data location a
# one-line default rather than an architectural commitment.

_ENV_NETWORK_PATH = "BHOOMICHECK_ROAD_NETWORK"
_ENV_DATA_ROOT = "BHOOMICHECK_DATA"
_DEFAULT_FILENAME = "road_network.json"


def default_network_path() -> Path:
    """Where to look for the distilled road network.

    `BHOOMICHECK_ROAD_NETWORK` names the file outright. Otherwise it is
    `road_network.json` under the same data root the HTTP API already
    uses (`BHOOMICHECK_DATA`, defaulting to ~/.bhoomicheck), so an
    operator has one place to put runtime data rather than two.
    """
    explicit = os.environ.get(_ENV_NETWORK_PATH)
    if explicit:
        return Path(explicit)
    root = Path(os.environ.get(_ENV_DATA_ROOT, Path.home() / ".bhoomicheck"))
    return root / _DEFAULT_FILENAME


def load_default_network() -> RoadNetwork | None:
    """Load the configured network, or None if none is configured.

    The two failure modes are deliberately treated differently:

      * Absent file -> None. This is a normal, documented state (a fresh
        clone has no plan data), and the caller degrades to the
        screenshot-derived setback answer, which shows up in the report
        as a lower-confidence finding. The gap is visible to the report's
        reader rather than fatal to the run.
      * Present but unreadable -> raises RoadNetworkError. A corrupt or
        truncated file is a misconfiguration, and silently falling back
        to eyeballing a map because the geometry failed to parse is
        exactly the kind of quiet degradation this project exists to
        avoid (architecture principle 6).
    """
    path = default_network_path()
    if not path.exists():
        return None
    return RoadNetwork.from_json(path)
