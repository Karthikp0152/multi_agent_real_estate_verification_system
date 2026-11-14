"""GeminiProvider: LLMProvider implementation backed by Google's Gemini API.

Deliberately thin -- read files, one SDK call, return text. All parsing,
validation, and retry logic lives in the caller, where it is tested
against a fake provider. Swapping this class out never touches that code.
"""

import mimetypes
from pathlib import Path

from google import genai
from google.genai import types

# Screenshots of government portals are the main input for the manual
# adapters, alongside uploaded deed PDFs. mimetypes covers the common
# cases from the file extension; anything it can't identify is rejected
# rather than sent with a guessed type, because a wrong media type makes
# the model fail in confusing ways rather than loudly.
_SUPPORTED_SUFFIXES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def media_type_for(path: Path) -> str:
    """Media type for a file we're sending to the model.

    Raises:
        ValueError: If the extension isn't one we know how to send.
            Loud failure beats silently mislabeling a file.
    """
    suffix = path.suffix.lower()
    if suffix in _SUPPORTED_SUFFIXES:
        return _SUPPORTED_SUFFIXES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and (guessed.startswith("image/") or guessed == "application/pdf"):
        return guessed
    raise ValueError(
        f"don't know how to send {path.name!r} to the model "
        f"(supported: {', '.join(sorted(_SUPPORTED_SUFFIXES))})"
    )


class GeminiProvider:
    """Satisfies the LLMProvider Protocol using the Gemini API.

    Requires GEMINI_API_KEY in the environment; genai.Client() reads it
    automatically, so no key ever appears in code or configuration files
    that could be committed.
    """

    def __init__(
        self,
        model: str = "gemini-3.5-flash",
        timeout_seconds: float = 600.0,
    ) -> None:
        """
        Args:
            model: Gemini model name. Flash is the free-tier workhorse;
                configurable so a quality comparison later is a one-line
                change at the call site.
            timeout_seconds: How long to wait for one response. Ten
                minutes is deliberately generous: transcribing a
                multi-page scanned register is slow and legitimately
                takes minutes, so a tight timeout would fail real work.
                The point is only that the wait is bounded.
        """
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.client = genai.Client()

    def generate(self, prompt: str, file_paths: list[Path]) -> str:
        """Send the prompt and files to Gemini, return its raw text response."""
        parts: list[types.Part | str] = [
            types.Part.from_bytes(
                data=path.read_bytes(), mime_type=media_type_for(path)
            )
            for path in file_paths
        ]
        parts.append(prompt)
        response = self.client.models.generate_content(
            model=self.model,
            contents=parts,
            # Constrain output to bare JSON: without this, models often wrap
            # valid JSON in markdown fences, which would burn our validation
            # retries on formatting noise instead of real schema errors.
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                # A bounded wait, because without one there is none.
                # Transcribing a long scanned register legitimately takes
                # minutes -- a 15-page, 372-row list took several -- but a
                # 2-page document once ran for twenty, and under the HTTP
                # API an unbounded call holds a request open indefinitely
                # with no way for the caller to tell a slow page from a
                # hung one. Erroring is recoverable: the run is
                # checkpointed, so the user simply uploads again.
                http_options=types.HttpOptions(
                    timeout=self.timeout_seconds * 1000  # SDK wants ms
                ),
            ),
        )
        # .text is None if the model returned no usable candidates (e.g.
        # safety-blocked). Fail loudly rather than hand "" to the validator.
        if response.text is None:
            raise RuntimeError(
                f"Gemini returned no text (prompt_feedback: {response.prompt_feedback})"
            )
        return response.text
