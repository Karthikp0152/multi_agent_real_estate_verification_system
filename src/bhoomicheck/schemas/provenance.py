"""Provenance: where a fact came from, how it was obtained, and when.

Every finding produced by any agent must carry one of these. A report claim
without provenance is a bug (architecture principle 3).
"""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, model_validator


class Provenance(BaseModel):
    """Record of the origin of a single fact.

    Attributes:
        source_name: Human-readable name of the source, e.g. "Bhu Bharati",
            "IGRS Telangana", "KUDA master plan 2041".
        source_type: How the fact was obtained. Mirrors the three adapter
            modes so every fact records its retrieval method, not just its
            origin.
        url: Where the fact was fetched from, when it came over the network.
        document: Reference to the file the fact was extracted from (e.g. an
            uploaded PDF's filename), when there is no URL.
        fetched_at: The date the fact was retrieved. Government records go
            stale; a report must show how fresh each fact is.
        record_updated_at: When the *source* says its own record was last
            updated, which is a different and often far more important
            date than fetched_at. Looking a parcel up today tells you
            nothing if the registry behind it was last touched years ago.
            Bhu Bharati prints this on its search page ("Records are
            updated as on - ..."), so it is real data we would otherwise
            discard. Optional because not every source states it, and a
            guess here would be worse than an honest absence.
    """

    source_name: str
    source_type: Literal["api", "scrape", "manual"]
    url: str | None = None
    document: str | None = None
    fetched_at: date
    record_updated_at: date | None = None

    @model_validator(mode="after")
    def require_url_or_document(self) -> Self:
        # url and document are each optional (an API fact has no file, an
        # uploaded PDF has no URL), but a fact traceable to neither is
        # untraceable — reject it here rather than let it reach a report.
        if self.url is None and self.document is None:
            raise ValueError("provenance needs at least one of url or document")
        return self
