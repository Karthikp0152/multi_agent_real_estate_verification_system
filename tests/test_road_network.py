"""Tests for the proposed-road-network geometry.

These run against tests/fixtures/demo_road_network.json, which is
SYNTHETIC -- invented roads at invented coordinates. That is deliberate,
and better than real data here for two reasons:

  1. No real master-plan geometry is redistributed in this repo.
  2. The fixture's offsets are chosen so every expected distance is a
     round number, checkable by hand: at 17.0 N one degree of latitude
     is 111194.93 m and one of longitude is 106336.24 m, so a road
     placed at +0.00094043 degrees of longitude is 100.0 m east. That
     makes these tests independent of the implementation rather than a
     recording of whatever it happened to output.

The parcel under test is always (17.0 N, 79.0 E).
"""

from pathlib import Path

import pytest

from bhoomicheck.geo.road_network import RoadNetwork, RoadNetworkError
from bhoomicheck.schemas.geo import LatLon

FIXTURE = Path(__file__).parent / "fixtures" / "demo_road_network.json"
PARCEL = LatLon(lat=17.0, lon=79.0, source="surveyed")

# Distances are rounded to 0.1 m by the engine; the projection itself is
# exact to far better than that over these ranges, so a 0.15 m tolerance
# is slack for float noise, not for sloppiness.
TOL = 0.15


@pytest.fixture
def network() -> RoadNetwork:
    return RoadNetwork.from_json(FIXTURE)


def _by_label(network: RoadNetwork) -> dict:
    return {r.road_label: r for r in network.assess(PARCEL).roads}


def test_a_road_whose_corridor_reaches_the_parcel_is_reported_as_encroaching(
    network: RoadNetwork,
) -> None:
    # 60 M centreline 20 m away: the corridor extends 30 m either side,
    # so it covers the parcel and overshoots it by 10 m.
    road = _by_label(network)["60 M"]
    assert road.distance_m == pytest.approx(20.0, abs=TOL)
    assert road.half_width_m == 30.0
    assert road.encroaches is True
    assert road.encroachment_m == pytest.approx(10.0, abs=TOL)
    # Clearance must be absent, not zero -- see RoadProximity.clearance_m.
    assert road.clearance_m is None


def test_a_road_clear_of_the_parcel_reports_clearance_not_encroachment(
    network: RoadNetwork,
) -> None:
    # 45 M centreline 100 m away, corridor reaches 22.5 m: clear by 77.5.
    road = _by_label(network)["45 M"]
    assert road.distance_m == pytest.approx(100.0, abs=TOL)
    assert road.encroaches is False
    assert road.clearance_m == pytest.approx(77.5, abs=TOL)
    assert road.encroachment_m is None


def test_distance_is_to_the_segment_not_the_infinite_line(network: RoadNetwork) -> None:
    """The 24 M stub runs east from 300 m east of the parcel.

    Its nearest point is therefore its western END, 300 m away. An
    implementation that forgot to clamp the projection parameter to
    [0, 1] would measure to the infinite line through the stub -- which
    passes straight through the parcel -- and report 0 m, turning a road
    300 m away into a fictional encroachment.
    """
    road = _by_label(network)["24 M"]
    assert road.distance_m == pytest.approx(300.0, abs=TOL)
    assert road.encroaches is False


def test_remaining_distances_match_hand_computed_offsets(network: RoadNetwork) -> None:
    roads = _by_label(network)
    assert roads["30 M"].distance_m == pytest.approx(500.0, abs=TOL)
    assert roads["30 M"].clearance_m == pytest.approx(485.0, abs=TOL)
    assert roads["90 M"].distance_m == pytest.approx(1500.0, abs=TOL)
    assert roads["90 M"].clearance_m == pytest.approx(1455.0, abs=TOL)


def test_roads_are_returned_nearest_first(network: RoadNetwork) -> None:
    labels = [r.road_label for r in network.assess(PARCEL).roads]
    assert labels == ["60 M", "45 M", "24 M", "30 M", "90 M"]


def test_nearest_and_encroaching_summarise_the_assessment(network: RoadNetwork) -> None:
    assessment = network.assess(PARCEL)
    assert assessment.nearest is not None
    assert assessment.nearest.road_label == "60 M"
    # Only the 60 M corridor reaches this parcel.
    assert [r.road_label for r in assessment.encroaching] == ["60 M"]


def test_a_segment_with_no_width_is_dropped_not_defaulted(network: RoadNetwork) -> None:
    """A road of unknown width cannot support a setback claim.

    The fixture holds one width-less segment only 5 m from the parcel.
    If it were defaulted to any width it would dominate every result and
    manufacture an encroachment, so it must vanish instead.
    """
    assert len(network.segments) == 5
    assert all(seg.width_m > 0 for seg in network.segments)


def test_a_parcel_outside_the_mapped_area_is_not_reported_as_clear() -> None:
    """The failure mode this flag exists to prevent.

    A parcel in another district has no roads near it, so the raw
    numbers would read as a very comfortable clearance. That is not
    "no problem found", it is "no information" -- and must be reported
    as such (architecture principle 6).
    """
    network = RoadNetwork.from_json(FIXTURE)
    far = LatLon(lat=12.9716, lon=77.5946, source="geocoded")  # Bengaluru
    assessment = network.assess(far)
    assert assessment.within_mapped_area is False
    # The distances are still computed and still honest -- they are just
    # not evidence about this parcel.
    assert assessment.nearest is not None
    assert assessment.nearest.distance_m > 100_000


def test_a_parcel_inside_the_box_but_beyond_coverage_is_not_mapped(
    network: RoadNetwork,
) -> None:
    """Inside the bounding box is not the same as covered by the plan."""
    tight = network.assess(PARCEL, coverage_radius_m=10.0)
    assert tight.within_mapped_area is False


def test_a_missing_network_file_raises_a_typed_error(tmp_path: Path) -> None:
    # Typed so callers can turn it into a "not_verified" finding rather
    # than letting a missing file crash a whole due-diligence run.
    with pytest.raises(RoadNetworkError, match="not found"):
        RoadNetwork.from_json(tmp_path / "nope.json")


def test_an_unparseable_network_file_raises_a_typed_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(RoadNetworkError, match="not valid JSON"):
        RoadNetwork.from_json(broken)


def test_an_empty_network_is_rejected_at_construction(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text('{"label": "x", "source": "y", "segments": []}', encoding="utf-8")
    with pytest.raises(RoadNetworkError, match="no usable segments"):
        RoadNetwork.from_json(empty)
