"""Tests for the CLI's pending-upload prompt loop.

No live orchestrator/adapter/model calls: a stub orchestrator scripts
provide_upload() responses, and monkeypatched input() supplies file
paths, so this pins down the prompt-loop logic itself -- not what any
real agent/adapter does, which is covered elsewhere.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.__main__ import _work_through_pending
from bhoomicheck.orchestrator.graph import PendingUpload
from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.schemas.report import BaseAgentReport
from bhoomicheck.schemas.risk_report import RiskReport

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def make_risk_report() -> RiskReport:
    finding = Finding(
        check_id="test.check",
        claim="test claim",
        status="verified_ok",
        provenance=Provenance(
            source_name="test", source_type="manual", document="x.pdf", fetched_at=date(2026, 7, 19)
        ),
        confidence=Confidence(level="high", reason="test"),
    )
    specialist = BaseAgentReport(
        agent_name="zoning",
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
        findings=[finding],
    )
    return RiskReport(
        parcel=PARCEL,
        generated_at=datetime.now(timezone.utc),
        overall_score=100,
        scored_findings=[],
        specialist_reports=[specialist],
    )


class StubOrchestrator:
    """Scripts provide_upload() to return a queued sequence of results."""

    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Path]] = []

    def provide_upload(self, thread_id: str, interrupt_id: str, upload_path: Path):
        self.calls.append((thread_id, interrupt_id, upload_path))
        return self.responses[len(self.calls) - 1]


def test_resolves_a_single_pending_upload(monkeypatch, tmp_path) -> None:
    upload = tmp_path / "kuda.png"
    upload.write_bytes(b"fake")
    monkeypatch.setattr("builtins.input", lambda _: str(upload))

    final = make_risk_report()
    orchestrator = StubOrchestrator([final])
    pending = [PendingUpload(agent="zoning", instructions="fetch kuda", interrupt_id="id-1")]

    result = _work_through_pending(orchestrator, "thread-1", pending)

    assert result is final
    assert orchestrator.calls == [("thread-1", "id-1", upload)]


def test_reprompts_when_file_does_not_exist(monkeypatch, tmp_path, capsys) -> None:
    real_upload = tmp_path / "real.png"
    real_upload.write_bytes(b"fake")
    responses = iter(["/does/not/exist", str(real_upload)])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    final = make_risk_report()
    orchestrator = StubOrchestrator([final])
    pending = [PendingUpload(agent="zoning", instructions="fetch kuda", interrupt_id="id-1")]

    result = _work_through_pending(orchestrator, "thread-1", pending)

    assert result is final
    assert orchestrator.calls == [("thread-1", "id-1", real_upload)]
    assert "is not a file" in capsys.readouterr().out


def test_resolves_multiple_sequential_pending_uploads(monkeypatch, tmp_path) -> None:
    upload_a = tmp_path / "a.png"
    upload_a.write_bytes(b"fake")
    upload_b = tmp_path / "b.png"
    upload_b.write_bytes(b"fake")
    responses = iter([str(upload_a), str(upload_b)])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    final = make_risk_report()
    second_pending = [
        PendingUpload(agent="litigation", instructions="fetch ecourts", interrupt_id="id-2")
    ]
    orchestrator = StubOrchestrator([second_pending, final])
    pending = [PendingUpload(agent="zoning", instructions="fetch kuda", interrupt_id="id-1")]

    result = _work_through_pending(orchestrator, "thread-1", pending)

    assert result is final
    assert orchestrator.calls == [
        ("thread-1", "id-1", upload_a),
        ("thread-1", "id-2", upload_b),
    ]


def test_stopping_early_returns_none_and_prints_resume_hint(monkeypatch, capsys) -> None:
    # Empty input means "stop here, I'll come back later" -- the whole
    # point of persistence, so it must not be mistaken for a bad path.
    monkeypatch.setattr("builtins.input", lambda _: "")
    orchestrator = StubOrchestrator([])
    pending = [PendingUpload(agent="zoning", instructions="fetch kuda", interrupt_id="id-1")]

    result = _work_through_pending(orchestrator, "check-42", pending)

    assert result is None
    assert orchestrator.calls == []
    assert "--resume check-42" in capsys.readouterr().out
