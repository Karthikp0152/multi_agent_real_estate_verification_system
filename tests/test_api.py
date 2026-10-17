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
    assert agents == {"zoning", "litigation", "land_records", "owner_background"}
    # Instructions must come through -- they're what a human acts on.
    assert all(p["instructions"] for p in body["pending"])


def test_a_check_can_be_resumed_by_id(client: TestClient) -> None:
    check_id = _start(client)["check_id"]
    # Simulating "come back days later": fetch state by id alone.
    body = client.get(f"/api/checks/{check_id}").json()
    assert body["complete"] is False
    assert len(body["pending"]) == 4


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
