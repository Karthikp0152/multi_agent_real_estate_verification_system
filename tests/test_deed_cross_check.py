"""Tests for the code-level survey-number cross-check on uploaded deeds.

Salvaged from an early branch (PR #2) that compared survey numbers with
`==`. That would have raised a false alarm on the most ordinary case in
Indian conveyancing: an older deed covering survey "16" when the parcel
being bought is "16/D". Relatedness, not equality, is the test -- see
bhoomicheck/survey.py.
"""

from datetime import date

import pytest

from bhoomicheck.agents.document_analysis import (
    SURVEY_MISMATCH_CHECK_ID,
    cross_check_survey_numbers,
)
from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.document import Boundaries, ExtractedDocument
from bhoomicheck.schemas.parcel import ParcelIdentifier

PARCEL = ParcelIdentifier(
    survey_no="16/D", village="Chennaram", mandal="Wardhannapet", district="Warangal"
)
TODAY = date(2026, 11, 13)


def doc(survey_number: str | None, name: str = "sale_deed.pdf") -> ExtractedDocument:
    return ExtractedDocument(
        source_document=name,
        doc_type="sale_deed",
        extraction_confidence=Confidence(level="high", reason="legible scan"),
        survey_number=survey_number,
    )


def check(*survey_numbers: str | None):
    docs = [doc(s, f"doc{i}.pdf") for i, s in enumerate(survey_numbers)]
    return cross_check_survey_numbers(PARCEL, docs, TODAY)


class TestWhatCounts:
    @pytest.mark.parametrize("stated", ["16/D", "16", "16/D/2"])
    def test_land_that_overlaps_the_parcel_is_not_a_mismatch(self, stated) -> None:
        """Exact, ancestor and descendant all describe this buyer's land.

        An older deed for the whole of survey 16 legitimately conveys
        today's 16/D -- a sub-division sale is the normal case, not an
        anomaly, and flagging it would train the reader to ignore this
        check.
        """
        assert check(stated) == []

    @pytest.mark.parametrize("stated", ["16/A", "72/B/1/1/1", "160", "261"])
    def test_land_that_is_not_the_parcel_is_flagged(self, stated) -> None:
        findings = check(stated)
        assert len(findings) == 1
        assert findings[0].check_id == SURVEY_MISMATCH_CHECK_ID
        assert findings[0].status == "issue_found"

    def test_a_document_stating_no_survey_number_is_skipped(self) -> None:
        """A gap is not a mismatch.

        The model already reports unreadable fields as not_verified
        findings of its own; inventing a second one here would
        double-count the same silence.
        """
        assert check(None) == []
        assert check("") == []
        assert check("   ") == []

    def test_each_offending_document_is_flagged_separately(self) -> None:
        findings = check("16/D", "72/B/1/1/1", "16/A")
        assert len(findings) == 2
        assert {f.provenance.document for f in findings} == {"doc1.pdf", "doc2.pdf"}


class TestHowItReports:
    def test_the_claim_names_both_numbers_and_the_document(self) -> None:
        finding = check("72/B/1/1/1")[0]
        assert "doc0.pdf" in finding.claim
        assert "72/B/1/1/1" in finding.claim
        assert "16/D" in finding.claim

    def test_a_mismatch_is_not_presented_as_fraud(self) -> None:
        """Resurveys genuinely renumber parcels.

        High confidence that the numbers differ is not high confidence
        that something is wrong, and the finding must not blur the two.
        """
        finding = check("72/B/1/1/1")[0]
        assert finding.confidence.level == "high"
        assert "not automatically fraud" in finding.confidence.reason
        assert "resurvey" in finding.confidence.reason

    def test_provenance_points_at_the_document_and_the_run_date(self) -> None:
        finding = check("16/A")[0]
        assert finding.provenance is not None
        assert finding.provenance.document == "doc0.pdf"
        assert finding.provenance.fetched_at == TODAY


class TestDocumentSchemaAdditions:
    def test_a_deed_can_carry_its_extent_and_chatur_seema(self) -> None:
        """The chatur seema is how Indian deeds identify land.

        Four adjoining descriptions, named instead of (or alongside)
        measurements -- useful for confirming a parcel's identity when
        survey numbers have been renumbered.
        """
        d = ExtractedDocument(
            source_document="deed.pdf",
            doc_type="sale_deed",
            extraction_confidence=Confidence(level="medium", reason="partial scan"),
            survey_number="16/D",
            extent_raw="0 Ac 12 Guntas",
            extent_sq_yards=1452.0,
            boundaries=Boundaries(north="Adduri Babu's land", south="village road"),
        )
        assert d.extent_raw == "0 Ac 12 Guntas"
        # Unreadable sides stay missing rather than being guessed.
        assert d.boundaries.east is None

    def test_extent_in_sq_yards_is_optional_when_units_cannot_be_parsed(self) -> None:
        d = ExtractedDocument(
            source_document="deed.pdf",
            doc_type="sale_deed",
            extraction_confidence=Confidence(level="low", reason="faint"),
            extent_raw="about twelve guntas",
        )
        assert d.extent_sq_yards is None
