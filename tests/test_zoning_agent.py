"""Tests for the Zoning Agent's deterministic Finding-building logic.

No fake provider needed here (unlike DocumentAnalysisAgent's tests) --
this agent takes already-structured KudaMasterPlanExtract objects and
maps them to Findings with plain code, so the tests just check that
mapping directly, including the third-party-source caveat that has to
appear on every finding built from this source.
"""

from pathlib import Path

from bhoomicheck.agents.zoning import ZoningAgent
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def test_fully_populated_extract_produces_three_findings() -> None:
    extract = KudaMasterPlanExtract(
        zoning_classification="residential",
        road_widening_affects_parcel=True,
        setback_required_meters=3.0,
        within_ftl_buffer=False,
    )
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    assert report.agent_name == "zoning"
    assert len(report.findings) == 3
    assert len(report.extracts) == 1


def test_road_widening_true_is_issue_found_with_setback_and_caveat() -> None:
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=True, setback_required_meters=3.0)
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    road_finding = next(f for f in report.findings if "setback" in f.claim)
    assert road_finding.status == "issue_found"
    assert "3.0" in road_finding.claim
    assert road_finding.confidence.level == "medium"
    assert "third-party" in road_finding.confidence.reason


def test_road_widening_false_is_verified_ok() -> None:
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=False)
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    road_finding = next(f for f in report.findings if "road" in f.claim)
    assert road_finding.status == "verified_ok"


def test_missing_field_is_not_verified_not_silently_ok() -> None:
    extract = KudaMasterPlanExtract()  # nothing legible
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    assert all(f.status == "not_verified" for f in report.findings)
    assert all("not legible" in f.confidence.reason for f in report.findings)


def test_ftl_buffer_true_is_issue_found() -> None:
    extract = KudaMasterPlanExtract(within_ftl_buffer=True)
    report = ZoningAgent().report(PARCEL, [(Path("screenshot.png"), extract)])

    ftl_finding = next(f for f in report.findings if "FTL" in f.claim or "Full Tank Level" in f.claim)
    assert ftl_finding.status == "issue_found"


def test_provenance_document_matches_uploaded_filename() -> None:
    extract = KudaMasterPlanExtract(zoning_classification="residential")
    report = ZoningAgent().report(PARCEL, [(Path("my_screenshot.png"), extract)])

    for finding in report.findings:
        assert finding.provenance is not None
        assert finding.provenance.document == "my_screenshot.png"


def test_plan_approval_date_flows_into_provenance() -> None:
    from datetime import date

    extract = KudaMasterPlanExtract(
        zoning_classification="residential", plan_approved_on=date(2024, 11, 17)
    )
    report = ZoningAgent().report(PARCEL, [(Path("kuda.png"), extract)])
    for finding in report.findings:
        assert finding.provenance is not None
        assert finding.provenance.record_updated_at == date(2024, 11, 17)


# --- geometry-backed setback check --------------------------------------
#
# These use the synthetic fixture (see tests/test_road_network.py for why
# it is synthetic and how its round distances were chosen). The parcel is
# at 17.0 N, 79.0 E, where a 60 M corridor encroaches by 10 m and the
# nearest clear road is 45 M at 100 m.

from bhoomicheck.geo.road_network import RoadNetwork  # noqa: E402
from bhoomicheck.schemas.geo import LatLon  # noqa: E402

NETWORK = Path(__file__).parent / "fixtures" / "demo_road_network.json"
ON_PARCEL = LatLon(lat=17.0, lon=79.0, source="surveyed")
FAR_AWAY = LatLon(lat=12.9716, lon=77.5946, source="geocoded")


def _road_finding(report):
    return next(f for f in report.findings if f.check_id == "zoning.road_widening_setback")


def test_geometry_replaces_the_screenshot_answer_rather_than_adding_to_it() -> None:
    """One question must yield one finding, or scoring double-counts it."""
    network = RoadNetwork.from_json(NETWORK)
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=False)
    report = ZoningAgent().report(
        PARCEL, [(Path("map.png"), extract)], road_assessment=network.assess(ON_PARCEL)
    )
    setback_findings = [
        f for f in report.findings if f.check_id == "zoning.road_widening_setback"
    ]
    assert len(setback_findings) == 1
    assert setback_findings[0].confidence.level == "high"


def test_an_encroaching_corridor_is_an_issue_with_the_measured_overlap() -> None:
    network = RoadNetwork.from_json(NETWORK)
    report = ZoningAgent().report(
        PARCEL,
        [(Path("map.png"), KudaMasterPlanExtract())],
        road_assessment=network.assess(ON_PARCEL),
    )
    finding = _road_finding(report)
    assert finding.status == "issue_found"
    assert "60 M" in finding.claim
    assert "10.0 m" in finding.claim  # the hand-computed encroachment


def test_a_clear_parcel_states_its_margin_instead_of_just_passing() -> None:
    """The difference between this and the old vision check.

    "no road was visible" and "clear by 77.5 m" are both verified_ok, but
    only one of them can be checked by a surveyor.
    """
    network = RoadNetwork.from_json(NETWORK)
    # Move 200 m north: the 60 M road is then 180 m away and clear, and
    # nothing else reaches the parcel either.
    north = LatLon(lat=17.0 + 200 / 111194.9266, lon=79.0, source="surveyed")
    report = ZoningAgent().report(
        PARCEL,
        [(Path("map.png"), KudaMasterPlanExtract())],
        road_assessment=network.assess(north),
    )
    finding = _road_finding(report)
    assert finding.status == "verified_ok"
    assert "clear by" in finding.claim
    # "medium", not "high": the measurement is exact, but it was taken
    # from one point, and a corridor could still clip an edge that
    # measurement never sees. See the boundary tests below.
    assert finding.confidence.level == "medium"
    assert "rather than the parcel boundary" in finding.confidence.reason


def test_a_measurement_contradicting_the_screenshot_says_so() -> None:
    network = RoadNetwork.from_json(NETWORK)
    # The screenshot was read as "no road affects this parcel"; the
    # geometry finds a 60 M corridor overlapping it.
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=False)
    report = ZoningAgent().report(
        PARCEL, [(Path("map.png"), extract)], road_assessment=network.assess(ON_PARCEL)
    )
    assert "supersedes the master-plan screenshot" in _road_finding(report).confidence.reason


def test_an_agreeing_measurement_does_not_claim_a_contradiction() -> None:
    network = RoadNetwork.from_json(NETWORK)
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=True)
    report = ZoningAgent().report(
        PARCEL, [(Path("map.png"), extract)], road_assessment=network.assess(ON_PARCEL)
    )
    assert "supersedes" not in _road_finding(report).confidence.reason


def test_every_computed_finding_keeps_the_unmodelled_setback_caveat() -> None:
    """A measured corridor clearance must not read as "you can build"."""
    network = RoadNetwork.from_json(NETWORK)
    report = ZoningAgent().report(
        PARCEL,
        [(Path("map.png"), KudaMasterPlanExtract())],
        road_assessment=network.assess(ON_PARCEL),
    )
    assert "not modelled" in _road_finding(report).confidence.reason


def test_a_parcel_outside_the_mapped_area_falls_back_to_the_screenshot() -> None:
    """Geometry that cannot answer must not pretend to.

    Out-of-area distances describe the edge of the map, so the agent
    keeps the screenshot-derived finding rather than reporting a
    comfortable clearance it has no basis for.
    """
    network = RoadNetwork.from_json(NETWORK)
    extract = KudaMasterPlanExtract(road_widening_affects_parcel=True, setback_required_meters=3.0)
    report = ZoningAgent().report(
        PARCEL, [(Path("map.png"), extract)], road_assessment=network.assess(FAR_AWAY)
    )
    finding = _road_finding(report)
    assert finding.confidence.level == "medium"  # the third-party caveat level
    assert "computed from plan geometry" not in finding.confidence.reason


def test_zoning_classification_and_ftl_stay_screenshot_derived() -> None:
    """The road network holds roads, not zone or lake polygons.

    So a report with geometry is honestly mixed: one measured finding
    beside two estimated ones, at their own confidence levels.
    """
    network = RoadNetwork.from_json(NETWORK)
    extract = KudaMasterPlanExtract(
        zoning_classification="residential", within_ftl_buffer=False
    )
    report = ZoningAgent().report(
        PARCEL, [(Path("map.png"), extract)], road_assessment=network.assess(ON_PARCEL)
    )
    levels = {f.check_id: f.confidence.level for f in report.findings}
    assert levels["zoning.road_widening_setback"] == "high"
    assert levels["zoning.classification"] == "medium"
    assert levels["zoning.ftl_buffer"] == "medium"


def test_the_full_assessment_is_carried_on_the_report() -> None:
    """Every road class, not just the one that drove the verdict."""
    network = RoadNetwork.from_json(NETWORK)
    report = ZoningAgent().report(
        PARCEL,
        [(Path("map.png"), KudaMasterPlanExtract())],
        road_assessment=network.assess(ON_PARCEL),
    )
    assert report.road_assessment is not None
    assert len(report.road_assessment.roads) == 5


def test_without_coordinates_the_report_carries_no_assessment() -> None:
    report = ZoningAgent().report(PARCEL, [(Path("map.png"), KudaMasterPlanExtract())])
    assert report.road_assessment is None


# --- boundary-measured findings -----------------------------------------


def square_boundary(size_m: float = 80.0) -> list[LatLon]:
    """A square parcel centred on the fixture's test point."""
    dlat = size_m / 111194.9266
    dlon = size_m / 106336.2372
    return [
        LatLon(lat=17.0 - dlat, lon=79.0 - dlon, source="surveyed"),
        LatLon(lat=17.0 - dlat, lon=79.0 + dlon, source="surveyed"),
        LatLon(lat=17.0 + dlat, lon=79.0 + dlon, source="surveyed"),
        LatLon(lat=17.0 + dlat, lon=79.0 - dlon, source="surveyed"),
    ]


def _boundary_report(size_m: float = 80.0):
    network = RoadNetwork.from_json(NETWORK)
    return ZoningAgent().report(
        PARCEL,
        [(Path("map.png"), KudaMasterPlanExtract())],
        road_assessment=network.assess(boundary=square_boundary(size_m)),
    )


def test_a_boundary_measurement_says_so_and_cites_the_vertex_count() -> None:
    finding = _road_finding(_boundary_report())
    assert "measured from the parcel boundary, 4 vertices" in finding.claim
    # The point-only caveat must NOT appear: it would be false here.
    assert "rather than the parcel boundary" not in finding.confidence.reason


def test_a_centreline_crossing_the_parcel_is_described_as_such() -> None:
    """Worse than clipping an edge, and the claim must distinguish them.

    The 60 M road runs through an 80 m square, so the full 60 m corridor
    crosses the land rather than merely reaching over a boundary.
    """
    finding = _road_finding(_boundary_report())
    assert finding.status == "issue_found"
    assert "passes through the parcel" in finding.claim
    assert "full 60 m corridor" in finding.claim


def test_an_edge_encroachment_is_quantified_separately() -> None:
    """The 45 M road reaches 2.5 m over the eastern edge, not through it."""
    finding = _road_finding(_boundary_report())
    assert "45 M (centreline 20.0 m away, corridor reaches 2.5 m into the parcel)" in finding.claim


def test_a_boundary_clear_result_earns_high_confidence() -> None:
    """A small parcel well clear of everything.

    Measured from real edges there is no unseen corner, so this is the
    one case the system is entitled to call high confidence.
    """
    network = RoadNetwork.from_json(NETWORK)
    # A 5 m square 200 m north of centre: the 60 M road is then ~180 m
    # away and nothing reaches it.
    dlat = 5 / 111194.9266
    dlon = 5 / 106336.2372
    north = 17.0 + 200 / 111194.9266
    boundary = [
        LatLon(lat=north - dlat, lon=79.0 - dlon, source="surveyed"),
        LatLon(lat=north - dlat, lon=79.0 + dlon, source="surveyed"),
        LatLon(lat=north + dlat, lon=79.0 + dlon, source="surveyed"),
        LatLon(lat=north + dlat, lon=79.0 - dlon, source="surveyed"),
    ]
    report = ZoningAgent().report(
        PARCEL,
        [(Path("map.png"), KudaMasterPlanExtract())],
        road_assessment=network.assess(boundary=boundary),
    )
    finding = _road_finding(report)
    assert finding.status == "verified_ok"
    assert finding.confidence.level == "high"


def test_the_unmodelled_setback_caveat_survives_boundary_mode() -> None:
    assert "not modelled" in _road_finding(_boundary_report()).confidence.reason
