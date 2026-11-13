"""Tests for the HTTP API.

The orchestrator is injected with scripted adapters, so no live model or
portal is touched -- the same discipline the rest of the suite follows.

Several of these are security tests rather than behaviour tests: an
endpoint that takes a file and a path component from the client is the
classic place to introduce path traversal, so that is pinned down here.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bhoomicheck.agents.document_analysis import DocumentAnalysisAgent
from bhoomicheck.api.app import create_app
from bhoomicheck.api.storage import MAX_UPLOAD_BYTES
from bhoomicheck.orchestrator.graph import Orchestrator, sqlite_checkpointer
from bhoomicheck.schemas.land_records import LandRecordExtract
from bhoomicheck.schemas.litigation import LitigationSearchResult
from bhoomicheck.schemas.owner_background import OwnerBackgroundExtract
from bhoomicheck.schemas.prohibited import SUB_CLAUSES, ProhibitedListExtract
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract
from tests.test_orchestrator import VALID_DOCUMENT_PAYLOAD, FakeAdapter, FakeProvider


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    orchestrator = Orchestrator(
        document_agent=DocumentAnalysisAgent(FakeProvider([VALID_DOCUMENT_PAYLOAD] * 10)),
        kuda_adapter=FakeAdapter(KudaMasterPlanExtract(road_widening_affects_parcel=True)),
        litigation_adapter=FakeAdapter(LitigationSearchResult(searched_name="K. Rajaiah")),
        land_records_adapter=FakeAdapter(LandRecordExtract(prohibited_status=False)),
        owner_background_adapter=FakeAdapter(OwnerBackgroundExtract(is_poa_sale=False)),
        prohibited_property_adapter=FakeAdapter(
            ProhibitedListExtract(
                sub_clause=SUB_CLAUSES[0], land_category="Agriculture", entries=[]
            )
        ),
        checkpointer=sqlite_checkpointer(tmp_path / "checks.db"),
    )
    return TestClient(create_app(orchestrator=orchestrator, data_root=tmp_path))


def _start(client: TestClient) -> dict:
    response = client.post(
        "/api/checks",
        data={
            "survey_no": "123/A",
            "village": "Hasanparthy",
            "mandal": "Hasanparthy",
            "seller_name": "K. Rajaiah",
        },
        files=[("documents", ("deed.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_start_returns_pending_lookups_with_instructions(client: TestClient) -> None:
    body = _start(client)
    assert body["complete"] is False
    agents = {p["agent"] for p in body["pending"]}
    assert agents == {
        "zoning",
        "litigation",
        "land_records",
        "owner_background",
        "prohibited_property",
    }
    # Instructions must come through -- they're what a human acts on.
    assert all(p["instructions"] for p in body["pending"])


def test_a_check_can_be_resumed_by_id(client: TestClient) -> None:
    check_id = _start(client)["check_id"]
    # Simulating "come back days later": fetch state by id alone.
    body = client.get(f"/api/checks/{check_id}").json()
    assert body["complete"] is False
    assert len(body["pending"]) == 5


def test_unknown_check_id_is_404(client: TestClient) -> None:
    assert client.get("/api/checks/does-not-exist").status_code == 404


def test_full_run_through_uploads_produces_a_report(client: TestClient) -> None:
    body = _start(client)
    check_id = body["check_id"]

    while not body["complete"]:
        pending = body["pending"][0]
        response = client.post(
            f"/api/checks/{check_id}/uploads/{pending['interrupt_id']}",
            files={"file": ("shot.png", b"\x89PNG fake", "image/png")},
        )
        assert response.status_code == 200, response.text
        body = response.json()

    report = client.get(f"/api/checks/{check_id}/report.json")
    assert report.status_code == 200
    assert {r["agent_name"] for r in report.json()["specialist_reports"]} == {
        "document_analysis",
        "zoning",
        "litigation",
        "land_records",
        "owner_background",
        "growth_potential",
        "prohibited_property",
    }

    html = client.get(f"/api/checks/{check_id}/report.html")
    assert html.status_code == 200
    assert "Land due-diligence report" in html.text
    # The disclaimer ships with the rendered report, always.
    assert "not constitute legal advice" in html.text


def test_report_is_409_until_the_check_is_complete(client: TestClient) -> None:
    check_id = _start(client)["check_id"]
    assert client.get(f"/api/checks/{check_id}/report.json").status_code == 409
    assert client.get(f"/api/checks/{check_id}/report.html").status_code == 409


def test_uploading_against_an_unknown_lookup_is_404(client: TestClient) -> None:
    check_id = _start(client)["check_id"]
    response = client.post(
        f"/api/checks/{check_id}/uploads/not-a-real-interrupt",
        files={"file": ("shot.png", b"\x89PNG fake", "image/png")},
    )
    assert response.status_code == 404


class TestUploadSecurity:
    def test_rejects_disallowed_file_types(self, client: TestClient) -> None:
        body = _start(client)
        response = client.post(
            f"/api/checks/{body['check_id']}/uploads/{body['pending'][0]['interrupt_id']}",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
        )
        assert response.status_code == 415

    def test_rejects_empty_files(self, client: TestClient) -> None:
        body = _start(client)
        response = client.post(
            f"/api/checks/{body['check_id']}/uploads/{body['pending'][0]['interrupt_id']}",
            files={"file": ("shot.png", b"", "image/png")},
        )
        assert response.status_code == 400

    def test_rejects_oversized_files(self, client: TestClient) -> None:
        body = _start(client)
        too_big = b"\x89PNG" + b"0" * MAX_UPLOAD_BYTES
        response = client.post(
            f"/api/checks/{body['check_id']}/uploads/{body['pending'][0]['interrupt_id']}",
            files={"file": ("shot.png", too_big, "image/png")},
        )
        assert response.status_code == 413

    def test_traversal_filename_cannot_escape_the_upload_directory(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # The classic attack: a filename that climbs out of the upload
        # dir. The stored name is generated, so the client's name is
        # never used to build the path.
        body = _start(client)
        response = client.post(
            f"/api/checks/{body['check_id']}/uploads/{body['pending'][0]['interrupt_id']}",
            files={"file": ("../../../../pwned.png", b"\x89PNG fake", "image/png")},
        )
        assert response.status_code == 200
        assert not (tmp_path.parent / "pwned.png").exists()
        assert not Path("/tmp/pwned.png").exists()

    def test_traversal_check_id_is_rejected(self, client: TestClient) -> None:
        # check_id also arrives from the URL and is used as a directory
        # name, so it gets the same treatment.
        response = client.get("/api/checks/..%2F..%2Fetc")
        assert response.status_code in (400, 404)


def test_index_page_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "BhoomiCheck" in response.text
    assert "Start check" in response.text


# --- optional parcel coordinates ----------------------------------------

from bhoomicheck.geo.road_network import RoadNetwork  # noqa: E402

NETWORK_FIXTURE = Path(__file__).parent / "fixtures" / "demo_road_network.json"


def _post(client: TestClient, **extra: str):
    data = {
        "survey_no": "123/A",
        "village": "Hasanparthy",
        "mandal": "Hasanparthy",
        "seller_name": "K. Rajaiah",
    }
    data.update(extra)
    return client.post(
        "/api/checks",
        data=data,
        files=[("documents", ("deed.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )


def test_blank_coordinate_fields_are_accepted(client: TestClient) -> None:
    """The default path, and the one an earlier version broke.

    An HTML form submits "" for a field the user left empty. Typing the
    parameters as floats made FastAPI reject "" with a 422, which would
    have failed every check started without coordinates -- i.e. almost
    all of them.
    """
    response = _post(client, latitude="", longitude="")
    assert response.status_code == 200, response.text


def test_omitting_coordinates_entirely_is_accepted(client: TestClient) -> None:
    assert _post(client).status_code == 200


def test_half_a_coordinate_is_rejected_rather_than_ignored(client: TestClient) -> None:
    """Silently dropping it would downgrade the setback check unnoticed."""
    response = _post(client, latitude="17.856513")
    assert response.status_code == 400
    assert "together" in response.json()["detail"]


def test_a_non_numeric_coordinate_is_rejected_with_a_clear_message(
    client: TestClient,
) -> None:
    response = _post(client, latitude="north a bit", longitude="79.63")
    assert response.status_code == 400
    assert "decimal degrees" in response.json()["detail"]


def test_an_out_of_range_coordinate_is_rejected(client: TestClient) -> None:
    """LatLon's own validators do this; the endpoint must not bypass them."""
    response = _post(client, latitude="917.0", longitude="79.63")
    assert response.status_code in (400, 422)


def test_supplied_coordinates_make_the_setback_check_measured(tmp_path: Path) -> None:
    """End to end over HTTP: a coordinate in, a measured finding out."""
    orchestrator = Orchestrator(
        road_network=RoadNetwork.from_json(NETWORK_FIXTURE),
        document_agent=DocumentAnalysisAgent(FakeProvider([VALID_DOCUMENT_PAYLOAD] * 10)),
        kuda_adapter=FakeAdapter(KudaMasterPlanExtract(road_widening_affects_parcel=False)),
        litigation_adapter=FakeAdapter(LitigationSearchResult(searched_name="K. Rajaiah")),
        land_records_adapter=FakeAdapter(LandRecordExtract(prohibited_status=False)),
        owner_background_adapter=FakeAdapter(OwnerBackgroundExtract(is_poa_sale=False)),
        prohibited_property_adapter=FakeAdapter(
            ProhibitedListExtract(
                sub_clause=SUB_CLAUSES[0], land_category="Agriculture", entries=[]
            )
        ),
        checkpointer=sqlite_checkpointer(tmp_path / "geo.db"),
    )
    client = TestClient(create_app(orchestrator=orchestrator, data_root=tmp_path))
    # The synthetic fixture's parcel, where a 60 M corridor encroaches.
    started = _post(client, latitude="17.0", longitude="79.0")
    assert started.status_code == 200, started.text
    check_id = started.json()["check_id"]

    for pending in started.json()["pending"]:
        client.post(
            f"/api/checks/{check_id}/uploads/{pending['interrupt_id']}",
            files=[("file", ("shot.png", b"\x89PNG fake", "image/png"))],
        )

    html = client.get(f"/api/checks/{check_id}/report.html").text
    assert "60 M" in html
    assert "encroaches" in html


# --- parcel boundary over HTTP ------------------------------------------
#
# A square around the synthetic fixture's parcel, whose eastern edge sits
# inside the 45 M corridor even though its centre is 77.5 m clear of it.

_SQUARE = (
    "16.99928055,78.99924767\n"
    "16.99928055,79.00075233\n"
    "17.00071945,79.00075233\n"
    "17.00071945,78.99924767"
)


def _geo_client(tmp_path: Path) -> TestClient:
    orchestrator = Orchestrator(
        road_network=RoadNetwork.from_json(NETWORK_FIXTURE),
        document_agent=DocumentAnalysisAgent(FakeProvider([VALID_DOCUMENT_PAYLOAD] * 10)),
        kuda_adapter=FakeAdapter(KudaMasterPlanExtract(road_widening_affects_parcel=False)),
        litigation_adapter=FakeAdapter(LitigationSearchResult(searched_name="K. Rajaiah")),
        land_records_adapter=FakeAdapter(LandRecordExtract(prohibited_status=False)),
        owner_background_adapter=FakeAdapter(OwnerBackgroundExtract(is_poa_sale=False)),
        prohibited_property_adapter=FakeAdapter(
            ProhibitedListExtract(
                sub_clause=SUB_CLAUSES[0], land_category="Agriculture", entries=[]
            )
        ),
        checkpointer=sqlite_checkpointer(tmp_path / "geo.db"),
    )
    return TestClient(create_app(orchestrator=orchestrator, data_root=tmp_path))


def _run_to_report(client: TestClient, **extra: str) -> str:
    started = _post(client, **extra)
    assert started.status_code == 200, started.text
    check_id = started.json()["check_id"]
    for pending in started.json()["pending"]:
        client.post(
            f"/api/checks/{check_id}/uploads/{pending['interrupt_id']}",
            files=[("file", ("shot.png", b"\x89PNG fake", "image/png"))],
        )
    return client.get(f"/api/checks/{check_id}/report.html").text


def test_a_boundary_is_measured_from_the_parcel_edges(tmp_path: Path) -> None:
    html = _run_to_report(_geo_client(tmp_path), boundary=_SQUARE)
    assert "Measured from the parcel boundary (4 vertices" in html
    # The 45 M road is clear of the centre but 2.5 m inside the edge.
    assert "encroaches 2.5 m" in html


def test_a_boundary_takes_precedence_over_a_point(tmp_path: Path) -> None:
    """Both supplied: the boundary is the better evidence, so it decides."""
    html = _run_to_report(
        _geo_client(tmp_path), latitude="17.0", longitude="79.0", boundary=_SQUARE
    )
    assert "Measured from the parcel boundary" in html
    assert "Measured from a single point" not in html


def test_a_point_only_report_warns_that_it_is_not_conclusive(tmp_path: Path) -> None:
    html = _run_to_report(_geo_client(tmp_path), latitude="17.0", longitude="79.0")
    assert "Measured from a single point" in html
    assert "would not appear here" in html


def test_a_blank_boundary_field_is_ignored(tmp_path: Path) -> None:
    """The default path: the textarea is submitted empty."""
    assert _post(_geo_client(tmp_path), boundary="   \n ").status_code == 200


def test_an_unreadable_boundary_is_rejected_with_a_clear_message(
    tmp_path: Path,
) -> None:
    response = _post(_geo_client(tmp_path), boundary="17.0,79.0\nnorth,79.1\n17.2,79.0")
    assert response.status_code == 400
    assert "decimal degrees" in response.json()["detail"]


def test_a_two_vertex_boundary_is_rejected(tmp_path: Path) -> None:
    response = _post(_geo_client(tmp_path), boundary="17.0,79.0\n17.1,79.1")
    assert response.status_code == 400
    assert "at least 3" in response.json()["detail"]
