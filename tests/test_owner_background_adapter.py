"""Tests for the provisional OwnerBackgroundAdapter.

Pin down: instructions() points at the real, confirmed IGRS URL, adapts
based on whether a POA document number is already known, and parse()
admits it has no real sample to learn from yet rather than guessing.
"""

from pathlib import Path

import pytest

from tests.conftest import ScriptedProvider

from bhoomicheck.adapters.owner_background import OwnerBackgroundAdapter
from bhoomicheck.schemas.owner_background import OwnerBackgroundQuery
from bhoomicheck.schemas.owner_background import OwnerBackgroundExtract


def test_instructions_reference_the_seller_and_the_real_url() -> None:
    query = OwnerBackgroundQuery(seller_name="K. Rajaiah")
    text = OwnerBackgroundAdapter().instructions(query)
    assert "K. Rajaiah" in text
    assert "https://registration.telangana.gov.in/" in text
    assert "Suraj Lamp" in text


def test_instructions_include_poa_document_number_when_known() -> None:
    query = OwnerBackgroundQuery(seller_name="K. Rajaiah", poa_document_no="GPA/456/2020")
    text = OwnerBackgroundAdapter().instructions(query)
    assert "GPA/456/2020" in text


def test_parse_requires_a_provider(upload) -> None:
    # instructions() needs no model, so the provider is optional at
    # construction -- but parse() must say plainly that it needs one
    # rather than failing somewhere deeper.
    with pytest.raises(RuntimeError, match="LLMProvider"):
        OwnerBackgroundAdapter().parse(upload)


def test_parse_extracts_a_validated_record(upload) -> None:
    provider = ScriptedProvider('{"is_poa_sale": true, "poa_registered": false}')
    extract = OwnerBackgroundAdapter(provider).parse(upload)

    assert isinstance(extract, OwnerBackgroundExtract)
    assert extract.is_poa_sale is True
    assert extract.poa_registered is False
    # The uploaded file itself is what gets sent to the model.
    assert provider.files == [[upload]]


def test_parse_retries_with_the_validation_error(upload) -> None:
    # First response violates the schema; the retry prompt must carry the
    # specific error so the model can fix that field, not guess.
    bad = '{"is_poa_sale": "maybe"}'  # "maybe" is not a boolean
    provider = ScriptedProvider(bad, '{"is_poa_sale": true, "poa_registered": false}')
    extract = OwnerBackgroundAdapter(provider).parse(upload)

    assert isinstance(extract, OwnerBackgroundExtract)
    assert len(provider.prompts) == 2
    assert "failed validation" in provider.prompts[1]
