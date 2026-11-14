"""Shared test doubles.

No test in this suite may call a live model or a live portal (CLAUDE.md
§5). ScriptedProvider satisfies the LLMProvider Protocol by replaying
queued responses, which is what lets adapter parse() and agent retry
loops be tested deterministically.
"""

from pathlib import Path

import pytest


class ScriptedProvider:
    """An LLMProvider that returns queued responses and records its calls."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.files: list[list[Path]] = []

    def generate(self, prompt: str, file_paths: list[Path]) -> str:
        self.prompts.append(prompt)
        self.files.append(list(file_paths))
        return self.responses[len(self.prompts) - 1]


@pytest.fixture
def upload(tmp_path: Path) -> Path:
    """A stand-in uploaded screenshot on disk."""
    path = tmp_path / "screenshot.png"
    path.write_bytes(b"\x89PNG fake")
    return path
