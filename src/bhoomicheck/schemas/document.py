"""ExtractedDocument: structured contents of one examined land document.

Produced by the Document Analysis Agent, one per uploaded file. Cross-
document conclusions (deed-chain breaks, undischarged mortgages) are not
stored here — they are Findings on the agent's report.
"""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.report import BaseAgentReport

DocType = Literal[
    "sale_deed",
    "gift_deed",
    "partition_deed",
    "encumbrance_certificate",
    "pattadar_passbook",
    "other",
]


class Boundaries(BaseModel):
    """The chatur seema: a deed's four-boundary description of a plot.

    Indian deeds traditionally describe an extent by naming the adjoining
    plots/owners on each side, alongside or instead of survey measurements.

    Attributes:
        north: Description of what lies to the north, verbatim from the
            deed. Optional, like the other three sides: scans are often
            only partially legible, and a missing side must stay missing
            rather than be guessed.
        south: See north.
        east: See north.
        west: See north.
    """

    north: str | None = None
    south: str | None = None
    east: str | None = None
    west: str | None = None


class ExtractedDocument(BaseModel):
    """What the agent read out of a single uploaded document.

    Attributes:
        source_document: Filename of the upload this was extracted from —
            the provenance anchor for every field below.
        doc_type: Classification of the document.
        doc_type_detail: Free-text description, required when doc_type is
            "other" so the escape hatch can never be used silently.
        reference_no: Registration number, EC application number, passbook
            number — whatever the document's own identifier is. Optional
            because not every document type carries one.
        execution_date: Date the document was executed/issued. Optional
            for the same reason.
        survey_number: Survey number as written in this document, which
            may differ from the parcel's current survey_no (resurveys and
            subdivisions mean an older deed can cite a superseded number).
            Optional: not every document states it legibly.
        extent_raw: The extent exactly as printed in the document, e.g.
            "0 Ac 12 Guntas". Kept verbatim as the audit trail behind
            extent_sq_yards. Optional: not every document type states an
            extent at all.
        extent_sq_yards: extent_raw normalised to square yards for
            cross-document comparison. Optional because it is derived from
            extent_raw -- when that text is missing or its units cannot be
            parsed with confidence, there is nothing reliable to normalise,
            and a required float would pressure a fabricated guess instead.
        boundaries: The chatur seema, when the document states one.
            Optional because non-deed documents (EC, passbook) typically
            don't carry it.
        executants: Party names on the transferring side of a deed.
            Empty for non-deed documents (EC, passbook).
        claimants: Party names on the receiving side of a deed.
        extraction_confidence: How reliably this was read. Scanned and
            handwritten records make extraction uncertainty the central
            fact about this agent's output (principle 4).
    """

    source_document: str = Field(min_length=1)
    doc_type: DocType
    doc_type_detail: str | None = None
    reference_no: str | None = None
    execution_date: date | None = None
    survey_number: str | None = None
    extent_raw: str | None = None
    extent_sq_yards: float | None = None
    boundaries: Boundaries | None = None
    executants: list[str] = []
    claimants: list[str] = []
    extraction_confidence: Confidence

    @model_validator(mode="after")
    def require_detail_for_other(self) -> Self:
        # "other" is an escape hatch for unanticipated document types, but
        # an unexplained "other" hides information — force a description.
        if self.doc_type == "other" and not self.doc_type_detail:
            raise ValueError('doc_type "other" requires doc_type_detail')
        return self


class DocumentAnalysisReport(BaseAgentReport):
    """Full output contract of the Document Analysis Agent.

    Inherits the base contract (parcel, findings, timestamp) and adds the
    per-document extractions. Cross-document conclusions — deed-chain
    breaks, undischarged mortgages, party mismatches — belong in the
    inherited findings list, each with provenance pointing back at the
    source_document(s) involved.

    Attributes:
        agent_name: Pinned to "document_analysis" — a report claiming this
            shape under another agent's name is a validation error.
        documents: One entry per uploaded file examined. At least one is
            required: this agent only runs when documents were uploaded,
            so an empty list means a malfunction, not a clean result.
    """

    agent_name: Literal["document_analysis"] = "document_analysis"
    documents: list[ExtractedDocument] = Field(min_length=1)
