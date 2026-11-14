"""LLMProvider: the interface between this system and whichever model serves it.

Agents and adapters depend on this Protocol, never on a concrete SDK.
Swapping Gemini for Claude (or a local model) means writing one new class
with this shape and changing nothing that calls it -- the same adapter
idea used for government data sources, applied to the model.

Lives in `llm/` rather than `agents/` because both agents (document
analysis) and adapters (turning a portal screenshot into a structured
record) need it, and an adapter importing from `agents/` would invert
the dependency direction every other part of this codebase follows.
"""

from pathlib import Path
from typing import Protocol


class LLMProvider(Protocol):
    """Anything that can turn a prompt plus attached files into text.

    Implementations return the model's raw text output (callers parse
    and validate it). They must not swallow errors: network failures and
    API errors should propagate, per the fail-loudly principle.
    """

    def generate(self, prompt: str, file_paths: list[Path]) -> str:
        """Send the prompt and the given files to the model.

        Args:
            prompt: Full instruction text for the model.
            file_paths: Files the model should read alongside the prompt
                -- uploaded deed PDFs, or screenshots of a government
                portal. Implementations are responsible for sending each
                with the right media type.

        Returns:
            The model's raw text response, expected (but not guaranteed)
            to be JSON -- validation is the caller's job.
        """
        ...
