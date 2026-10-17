"""Tests for the provisional KudaMasterPlanAdapter.

The lookup source (1acre.in) and its URLs are confirmed real (see module
docstring in adapters/kuda.py), but parse() still has no real screenshot
to learn its shape from. These tests check that instructions() points at
the real workflow and that parse() admits its own limitation plainly
instead of guessing.
"""

from pathlib import Path

import pytest

from tests.conftest import ScriptedProvider

from bhoomicheck.adapters.kuda import KudaMasterPlanAdapter
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def test_instructions_reference_the_parcel_and_the_real_url() -> None:
    text = KudaMasterPlanAdapter().instructions(PARCEL)
    assert "123/A" in text
    assert "Hasanparthy" in text
    assert "https://1acre.in/map-layers/telangana/warangal-masterplan" in text


def test_parse_requires_a_provider(upload) -> None:
    # instructions() needs no model, so the provider is optional at
    # construction -- but parse() must say plainly that it needs one
    # rather than failing somewhere deeper.
    with pytest.raises(RuntimeError, match="LLMProvider"):
        KudaMasterPlanAdapter().parse(upload)


def test_parse_extracts_a_validated_record(upload) -> None:
    provider = ScriptedProvider('{"zoning_classification": "residential", "road_widening_affects_parcel": true, "setback_required_meters": 9.0}')
    extract = KudaMasterPlanAdapter(provider).parse(upload)

    assert isinstance(extract, KudaMasterPlanExtract)
    assert extract.zoning_classification == "residential"
    assert extract.road_widening_affects_parcel is True
    # The uploaded file itself is what gets sent to the model.
    assert provider.files == [[upload]]


def test_parse_retries_with_the_validation_error(upload) -> None:
    # First response violates the schema; the retry prompt must carry the
    # specific error so the model can fix that field, not guess.
    bad = '{"zoning_classification": []}'  # a list where a string belongs
    provider = ScriptedProvider(bad, '{"zoning_classification": "residential", "road_widening_affects_parcel": true, "setback_required_meters": 9.0}')
    extract = KudaMasterPlanAdapter(provider).parse(upload)

    assert isinstance(extract, KudaMasterPlanExtract)
    assert len(provider.prompts) == 2
    assert "failed validation" in provider.prompts[1]
