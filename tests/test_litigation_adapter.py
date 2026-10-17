"""Tests for the provisional LitigationAdapter.

Pin down two things: instructions() points at the real, confirmed eCourts
URLs and covers every name variant given, and parse() admits it has no
real sample to learn from yet rather than guessing. Also locks in the
hard-guardrail behaviour that a case extract can't silently claim to be a
confirmed identity match.
"""

from pathlib import Path

import pytest

from tests.conftest import ScriptedProvider
from pydantic import ValidationError

from bhoomicheck.adapters.litigation import LitigationAdapter
from bhoomicheck.schemas.litigation import LitigationCaseExtract, LitigationSearchQuery
from bhoomicheck.schemas.litigation import LitigationSearchResult

QUERY = LitigationSearchQuery(full_name="K. Rajaiah", also_known_as=["Rajaiah Kondapaka"])


def test_instructions_cover_both_courts_and_every_name_variant() -> None:
    text = LitigationAdapter().instructions(QUERY)
    assert "K. Rajaiah" in text
    assert "Rajaiah Kondapaka" in text
    assert "warangal.dcourts.gov.in" in text
    assert "hcservices.ecourts.gov.in" in text


def test_parse_requires_a_provider(upload) -> None:
    # instructions() needs no model, so the provider is optional at
    # construction -- but parse() must say plainly that it needs one
    # rather than failing somewhere deeper.
    with pytest.raises(RuntimeError, match="LLMProvider"):
        LitigationAdapter().parse(upload)


def test_parse_extracts_a_validated_record(upload) -> None:
    provider = ScriptedProvider('{"searched_name": "K. Rajaiah", "cases": [{"court": "District Court, Warangal", "status": "pending", "matched_party_name": "K. Rajaiah"}]}')
    extract = LitigationAdapter(provider).parse(upload)

    assert isinstance(extract, LitigationSearchResult)
    assert extract.searched_name == "K. Rajaiah"
    assert len(extract.cases) == 1
    # Still only a name match, structurally declared as such.
    assert extract.cases[0].match_basis == "name_search_unconfirmed"
    # The uploaded file itself is what gets sent to the model.
    assert provider.files == [[upload]]


def test_parse_retries_with_the_validation_error(upload) -> None:
    # First response violates the schema; the retry prompt must carry the
    # specific error so the model can fix that field, not guess.
    bad = '{"searched_name": 12345}'  # a number where a string belongs
    provider = ScriptedProvider(bad, '{"searched_name": "K. Rajaiah", "cases": [{"court": "District Court, Warangal", "status": "pending", "matched_party_name": "K. Rajaiah"}]}')
    extract = LitigationAdapter(provider).parse(upload)

    assert isinstance(extract, LitigationSearchResult)
    assert len(provider.prompts) == 2
    assert "failed validation" in provider.prompts[1]

def test_case_extract_match_basis_cannot_be_overridden() -> None:
    # match_basis is pinned to "name_search_unconfirmed" -- asserting the
    # opposite value must fail validation, not silently be accepted.
    with pytest.raises(ValidationError):
        LitigationCaseExtract(
            court="District Court, Warangal",
            status="pending",
            matched_party_name="K. Rajaiah",
            match_basis="confirmed",  # type: ignore[arg-type]
        )


def test_case_extract_defaults_to_unconfirmed_match_basis() -> None:
    case = LitigationCaseExtract(
        court="District Court, Warangal",
        status="pending",
        matched_party_name="K. Rajaiah",
    )
    assert case.match_basis == "name_search_unconfirmed"
