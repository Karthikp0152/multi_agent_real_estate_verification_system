"""Litigation schemas: raw eCourts search records and the agent's report.

LitigationSearchQuery/CaseExtract/SearchResult were originally defined in
adapters/litigation.py, but LitigationReport needs to embed them, and
schemas should never depend on adapters (the reverse is already true --
e.g. KudaMasterPlanAdapter imports ParcelIdentifier from here). Moving
them here mirrors schemas/document.py, where ExtractedDocument and
DocumentAnalysisReport live together for the same reason.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from bhoomicheck.schemas.report import BaseAgentReport


class LitigationSearchQuery(BaseModel):
    """The person being checked, and name variants worth trying.

    Attributes:
        full_name: The name to search, as it appears on the land record
            being checked (e.g. a sale deed's executant).
        court_complex: Which court complex to search, e.g. "Warangal,
            PDJ Court Complex". The portal requires one and will not
            search across all of them, so a search is only ever evidence
            about the complex it was run in.
        years: Which filing years to search. The portal requires a year
            and searches exactly one at a time, so screening a person
            properly means repeating the search per year. Empty means
            nobody has decided a range yet, which the agent reports as
            a gap rather than guessing one.
        also_known_as: Spelling variants/short forms worth trying too --
            eCourts matches partial names (3+ characters), and
            transliterated Indian names vary a lot across documents.
    """

    full_name: str
    also_known_as: list[str] = []
    court_complex: str | None = None
    years: list[int] = []


class LitigationCaseExtract(BaseModel):
    """One case record as printed on an eCourts party-name search result.

    Every field reflects what the listing showed, not a conclusion about
    the parcel or its owner -- see match_basis.

    Attributes:
        court: Which court this case is in, e.g. "District Court,
            Warangal" or "High Court for the State of Telangana".
        case_no: The case's registration number, if shown.
        cnr_number: eCourts' own unique case identifier, if shown.
        case_type: Free text (e.g. "Original Suit", "Civil Appeal") --
            vocabulary varies by court, so not a Literal.
        status: Pending/disposed, mirroring the portal's own search filter.
        filing_date: Date the case was filed, if shown.
        matched_party_name: The exact name string the portal displayed
            for the party that matched the search -- may differ from the
            searched name (partial match, different transliteration).
        party_role: e.g. "Petitioner", "Respondent", if shown.
        match_basis: Always "name_search_unconfirmed". Fixed rather than
            left to convention so no downstream code can silently treat
            this as a confirmed identity match (CLAUDE.md hard guardrail).
        notes: Anything else shown that doesn't fit a structured field.
    """

    court: str
    case_no: str | None = None
    cnr_number: str | None = None
    case_type: str | None = None
    status: Literal["pending", "disposed", "unknown"]
    filing_date: date | None = None
    matched_party_name: str
    party_role: str | None = None
    match_basis: Literal["name_search_unconfirmed"] = "name_search_unconfirmed"
    notes: str | None = None


class LitigationSearchResult(BaseModel):
    """Everything found when searching one person's name across eCourts.

    Attributes:
        searched_name: The name actually typed into the search box (one
            of query.full_name / query.also_known_as).
        year_searched: The filing year this search covered. The portal
            requires a year and searches one at a time, so a result
            without one cannot say how much was covered.
        court_complex: The complex this search ran in.
        status_filter: Which of the Pending / Disposed / Both radio
            buttons was used. Recorded because the portal does NOT put
            a status column on the results table -- a row's status is
            whatever filter produced it, so searching "Pending" and
            reading the rows as "all cases" would badly understate.
        cases: Every case the search returned. Empty is a valid, useful
            result (no matches found) -- it is not the same as
            "not_verified"; the agent decides which finding status that
            maps to, this just reports what the search actually returned.
    """

    searched_name: str
    year_searched: int | None = None
    court_complex: str | None = None
    status_filter: Literal["pending", "disposed", "both"] | None = None
    cases: list[LitigationCaseExtract] = []


class LitigationReport(BaseAgentReport):
    """Full output contract of the Litigation Agent.

    Inherits the base contract (parcel, findings, timestamp) and adds the
    raw per-name search results the findings were built from.

    Attributes:
        agent_name: Pinned to "litigation" -- a report claiming this shape
            under another agent's name is a validation error.
        searches: One entry per name variant searched. At least one is
            required: this agent only runs when a search was actually
            performed, so an empty list means a malfunction, not a clean
            result.
    """

    agent_name: Literal["litigation"] = "litigation"
    searches: list[LitigationSearchResult] = Field(min_length=1)
