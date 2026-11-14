"""Document Analysis Agent: uploaded land documents -> DocumentAnalysisReport.

Works offline on uploaded PDFs, no portal dependency (Phase 1). The LLM
extracts per-document structure and cross-document findings; this module
owns validation, the retry loop, and assembly of the final report.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.document import DocumentAnalysisReport, ExtractedDocument
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier


class _LLMPayload(BaseModel):
    """The slice of the report the model is asked to produce.

    Deliberately not the whole DocumentAnalysisReport: parcel identity and
    the timestamp are facts the system already knows, and asking a model
    to echo known facts is asking it to hallucinate them.
    """

    documents: list[ExtractedDocument]
    findings: list[Finding]


PROMPT_TEMPLATE = """\
You are a land-records document analyst for parcels in Telangana, India.
Read the attached document(s) for the parcel below and respond with JSON
only — no prose, no markdown fences.

Parcel: survey no. {survey_no}, village {village}, mandal {mandal}, \
district {district}.
Today's date: {today}. The attached files, in order, are: {filenames}.

Your JSON must match this schema exactly:
{schema}

Rules:
- One entry in "documents" per attached file; source_document must be the
  exact filename from the list above (never invent a filename).
- Every finding needs a check_id: a short, stable, lowercase identifier
  for what kind of check this is, dotted like "document.<topic>", e.g.
  "document.deed_chain_break", "document.mortgage_undischarged",
  "document.party_mismatch". Use the same check_id for every finding
  about the same kind of issue, even across different documents or runs
  — scoring rules match on this id, not on the wording of "claim".
- Every finding needs provenance (source_type "manual", document = the
  filename it came from, fetched_at = today's date given above) and a
  confidence with a concrete reason.
- If something cannot be read or verified, report it as a finding with
  status "not_verified" — never omit it silently.
- Express extraction uncertainty honestly in extraction_confidence.
"""

RETRY_TEMPLATE = """\
Your previous response failed validation with these errors:
{errors}

Produce corrected JSON matching the schema. Respond with JSON only.
"""


class DocumentAnalysisAgent:
    """Runs document analysis for one parcel over its uploaded PDFs."""

    def __init__(self, provider: LLMProvider, max_retries: int = 2) -> None:
        """
        Args:
            provider: The model backend to call.
            max_retries: How many *additional* attempts after the first,
                each fed the previous validation errors. Small on purpose:
                if the model can't fix its output in two corrections, more
                tries usually just burn tokens.
        """
        self.provider = provider
        self.max_retries = max_retries

    def analyze(
        self, parcel: ParcelIdentifier, pdf_paths: list[Path]
    ) -> DocumentAnalysisReport:
        """Analyze the uploaded documents and return a validated report.

        Raises:
            ValidationError: If the model still produces invalid output
                after all retries — surfaced loudly, never papered over.
        """
        prompt = PROMPT_TEMPLATE.format(
            survey_no=parcel.survey_no,
            village=parcel.village,
            mandal=parcel.mandal,
            district=parcel.district,
            # Facts the system knows go into the prompt, never guessed by
            # the model: real filenames and the actual date (provenance
            # principle — a hallucinated filename is untraceable).
            today=datetime.now(timezone.utc).date().isoformat(),
            filenames=", ".join(p.name for p in pdf_paths),
            schema=json.dumps(_LLMPayload.model_json_schema(), indent=2),
        )

        for attempt in range(self.max_retries + 1):
            raw = self.provider.generate(prompt, pdf_paths)
            try:
                payload = _LLMPayload.model_validate_json(raw)
            except ValidationError as e:
                if attempt == self.max_retries:
                    raise
                # Feed the exact validation errors back so the model can
                # fix the specific fields, not guess at what went wrong.
                prompt = prompt + "\n\n" + RETRY_TEMPLATE.format(errors=e)
                continue
            return DocumentAnalysisReport(
                parcel=parcel,
                generated_at=datetime.now(timezone.utc),
                documents=payload.documents,
                findings=payload.findings,
            )

        raise AssertionError("unreachable")  # loop always returns or raises
