"""Tests for the provisional LandRecordsAdapter.

Pin down: instructions() points at the real, confirmed Bhu Bharati URL
and references the parcel's own fields, and parse() admits it has no
real sample to learn from yet rather than guessing.
"""

from pathlib import Path

import pytest

from tests.conftest import ScriptedProvider

from bhoomicheck.adapters.land_records import LandRecordsAdapter
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.land_records import LandRecordExtract

PARCEL = ParcelIdentifier(
    survey_no="123/A", village="Hasanparthy", mandal="Hasanparthy", district="Warangal"
)


def test_instructions_reference_the_parcel_and_the_real_url() -> None:
    text = LandRecordsAdapter().instructions(PARCEL)
    assert "123/A" in text
    assert "Hasanparthy" in text
    assert "Warangal" in text
    assert "https://bhubharati.telangana.gov.in/knowLandStatus" in text


def test_parse_requires_a_provider(upload) -> None:
    # instructions() needs no model, so the provider is optional at
    # construction -- but parse() must say plainly that it needs one
    # rather than failing somewhere deeper.
    with pytest.raises(RuntimeError, match="LLMProvider"):
        LandRecordsAdapter().parse(upload)


def test_parse_extracts_a_validated_record(upload) -> None:
    provider = ScriptedProvider('{"owner_names": ["K. Rajaiah"], "prohibited_status": false, "land_type": "patta land"}')
    extract = LandRecordsAdapter(provider).parse(upload)

    assert isinstance(extract, LandRecordExtract)
    assert extract.owner_names == ["K. Rajaiah"]
    assert extract.prohibited_status is False
    # The uploaded file itself is what gets sent to the model.
    assert provider.files == [[upload]]


def test_parse_retries_with_the_validation_error(upload) -> None:
    # First response violates the schema; the retry prompt must carry the
    # specific error so the model can fix that field, not guess.
    bad = '{"prohibited_status": "maybe"}'  # "maybe" is not a boolean
    provider = ScriptedProvider(bad, '{"owner_names": ["K. Rajaiah"], "prohibited_status": false, "land_type": "patta land"}')
    extract = LandRecordsAdapter(provider).parse(upload)

    assert isinstance(extract, LandRecordExtract)
    assert len(provider.prompts) == 2
    assert "failed validation" in provider.prompts[1]
