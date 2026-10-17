"""extract_structured: one uploaded file -> one validated Pydantic model.

Every manual adapter's parse() has the same shape: a human hands back a
screenshot of a government portal, and we need a typed record out of it.
Rather than four near-identical retry loops, that logic lives here once.

This is the same validate-and-retry pattern DocumentAnalysisAgent uses
(architecture principle 1): ask for JSON, validate it against the target
schema, and on failure feed the exact validation errors back so the model
fixes the specific fields instead of guessing at what went wrong.

The important instruction this sends is "leave it null if you can't read
it". A model that invents a plausible-looking survey number from a blurry
screenshot is far more dangerous here than one that admits it couldn't
read the field: a null flows into a "not_verified" finding (principle 6),
while an invention becomes a confident lie in a report someone buys land
on.
"""

import json

from pathlib import Path

from pydantic import BaseModel, ValidationError

from bhoomicheck.llm.provider import LLMProvider

PROMPT_TEMPLATE = """\
You are reading a screenshot or document from an Indian land-records or
court portal, for land due diligence in Telangana.

{context}

Respond with JSON only -- no prose, no markdown fences. Your JSON must
match this schema exactly:
{schema}

Rules:
- Report ONLY what is actually visible in the attached file.
- If a field is not visible, unreadable, blurry, cropped, or you are not
  confident of it, use null. Do NOT guess, infer, or fill in a plausible
  value. A null is handled correctly downstream as "not verified"; a
  wrong value becomes a false statement in a report someone may buy land
  based on.
- Do not translate or normalise names and identifiers -- copy them
  exactly as printed, including spelling and punctuation.
"""

RETRY_TEMPLATE = """\
Your previous response failed validation with these errors:
{errors}

Produce corrected JSON matching the schema. Respond with JSON only.
"""


def extract_structured(
    provider: LLMProvider,
    upload_path: Path,
    model: type[BaseModel],
    context: str,
    max_retries: int = 2,
) -> BaseModel:
    """Read one uploaded file into a validated instance of `model`.

    Args:
        provider: The model backend to call.
        upload_path: The file the human supplied.
        model: The Pydantic schema to extract into.
        context: Source-specific orientation for the model, e.g. which
            portal this screenshot came from and what to look for.
        max_retries: How many *additional* attempts after the first, each
            fed the previous validation errors. Small on purpose: if the
            model can't fix its output in two corrections, more tries
            usually just burn tokens.

    Returns:
        A validated instance of `model`.

    Raises:
        ValidationError: If the model still produces invalid output after
            all retries -- surfaced loudly, never papered over.
    """
    prompt = PROMPT_TEMPLATE.format(
        context=context,
        schema=json.dumps(model.model_json_schema(), indent=2),
    )

    for attempt in range(max_retries + 1):
        raw = provider.generate(prompt, [upload_path])
        try:
            return model.model_validate_json(raw)
        except ValidationError as e:
            if attempt == max_retries:
                raise
            prompt = prompt + "\n\n" + RETRY_TEMPLATE.format(errors=e)

    raise AssertionError("unreachable")  # loop always returns or raises
