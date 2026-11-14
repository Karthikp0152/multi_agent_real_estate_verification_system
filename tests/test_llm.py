"""Tests for the shared LLM layer: media typing and structured extraction.

No live model calls: ScriptedProvider (tests/conftest.py) replays queued
responses, so the retry loop is pinned down deterministically.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.gemini import media_type_for
from tests.conftest import ScriptedProvider


class Sample(BaseModel):
    name: str | None = None
    count: int | None = None


VALID = json.dumps({"name": "K. Rajaiah", "count": 2})


class TestMediaType:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("deed.pdf", "application/pdf"),
            ("shot.png", "image/png"),
            ("shot.jpg", "image/jpeg"),
            ("shot.jpeg", "image/jpeg"),
            ("shot.webp", "image/webp"),
            ("SHOT.PNG", "image/png"),  # extension case must not matter
        ],
    )
    def test_known_types(self, filename: str, expected: str) -> None:
        assert media_type_for(Path(filename)) == expected

    def test_unknown_type_fails_loudly(self) -> None:
        # Guessing a media type makes the model fail confusingly rather
        # than loudly, so an unknown extension is refused outright.
        with pytest.raises(ValueError, match="don't know how to send"):
            media_type_for(Path("notes.docx"))


class TestExtractStructured:
    def test_returns_a_validated_model(self, upload: Path) -> None:
        provider = ScriptedProvider(VALID)
        result = extract_structured(provider, upload, Sample, "context here")

        assert isinstance(result, Sample)
        assert result.name == "K. Rajaiah"
        assert provider.files == [[upload]]

    def test_prompt_carries_schema_and_the_do_not_guess_rule(self, upload: Path) -> None:
        provider = ScriptedProvider(VALID)
        extract_structured(provider, upload, Sample, "this is a KUDA screenshot")
        prompt = provider.prompts[0]

        assert "this is a KUDA screenshot" in prompt
        assert "count" in prompt  # the schema itself is included
        # The single most important instruction: a null is safe, an
        # invented value is a lie in a report someone buys land on.
        assert "null" in prompt
        assert "Do NOT guess" in prompt

    def test_invalid_response_is_retried_with_the_error(self, upload: Path) -> None:
        provider = ScriptedProvider(json.dumps({"count": "not a number"}), VALID)
        result = extract_structured(provider, upload, Sample, "ctx")

        assert isinstance(result, Sample)
        assert len(provider.prompts) == 2
        assert "failed validation" in provider.prompts[1]
        # The retry must name the offending field, not just say "invalid".
        assert "count" in provider.prompts[1]

    def test_raises_after_retries_are_exhausted(self, upload: Path) -> None:
        bad = json.dumps({"count": "not a number"})
        provider = ScriptedProvider(bad, bad, bad)

        with pytest.raises(ValidationError):
            extract_structured(provider, upload, Sample, "ctx", max_retries=2)

        assert len(provider.prompts) == 3  # initial + 2 retries, then loud failure

    def test_missing_fields_become_none_not_invented(self, upload: Path) -> None:
        # The whole safety story: unreadable fields come back null and
        # flow into "not_verified" findings downstream.
        provider = ScriptedProvider(json.dumps({"name": None, "count": None}))
        result = extract_structured(provider, upload, Sample, "ctx")

        assert result.name is None
        assert result.count is None
