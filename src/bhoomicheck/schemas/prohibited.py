"""Schemas for the Section 22-A prohibited-property register.

This is the check Bhu Bharati cannot answer (see schemas/land_records.py
-- prohibited_status is confirmed absent from its land-details page),
and it is one of the named deal-killers in CLAUDE.md's problem
statement: assigned land often cannot be legally sold at all.

The source is unlike every other one in this project. IGRS does not
answer "is survey 16 prohibited?" -- it publishes, per village, a set of
scanned PDF lists, one per 22-A sub-clause, split into Agriculture and
Non-Agriculture. The question is answered by membership in those lists,
which makes two things load-bearing:

  1. COVERAGE. There are seven sub-clauses per category. A survey number
     absent from three of them has not been cleared. Partial coverage
     must produce "not_verified", never "verified_ok".
  2. EXTRACTION FIDELITY. The published PDFs are page scans with an OCR
     layer of very poor quality -- on a real Warangal list the mandal
     name "WARDHANNAPET" appeared as seven different strings including
     "!?ARDHANNAPEI", and "TG" appeared as "T6" as often as "TG". A text
     search over that layer would return "not found" for a parcel that
     is in fact listed. So these are read with vision, and a reader is
     told the entry count so a silently truncated page is visible.
"""

from typing import Literal

from pydantic import BaseModel, Field

from bhoomicheck.schemas.report import BaseAgentReport

# The seven lists IGRS publishes per land category, labelled as the
# portal labels them. Kept here rather than in the adapter because the
# agent needs them to judge coverage, and a mismatch between the two
# would silently understate how much was checked.
SUB_CLAUSES: tuple[str, ...] = (
    "22 A (1) (a)",
    "22 A (1) (b)",
    "22 A (1) (c)- Waqf",
    "22 A (1) (c)- Endowment",
    "22 A (1) (d)",
    "22 A (1) (e)",
    "Court cases & Others",
)

LandCategory = Literal["Agriculture", "Non-Agriculture"]


class ProhibitedListEntry(BaseModel):
    """One row of a FORM-I prohibition list.

    Column names are the form's own, confirmed against a real Warangal
    list rather than guessed.

    Attributes:
        survey_no: From "Sy. No. & Sub-Division No. (if any)", copied
            exactly as printed (e.g. "49/53/B/1", "279A"). The only
            field used for matching; everything else is for the reader.
        owner_name: From "Name of the Assignee / Allotee / Owner".
            Carried because a match should be checkable against the
            pattadar name Bhu Bharati gave, not taken on trust.
        extent: From "Extent", as printed. Acres-guntas, not decimal.
        act: From "Act under which prohibited", e.g. "TG Assigned Lands
            (Prohibition of Transfers) Act, 1977". This is what tells a
            reader *why* the land is restricted.
    """

    survey_no: str | None = None
    owner_name: str | None = None
    extent: str | None = None
    act: str | None = None


class ProhibitedListExtract(BaseModel):
    """One prohibition list, as read off its scanned pages.

    Attributes:
        sub_clause: Which of SUB_CLAUSES this list is, from the FORM-I
            heading. None if unreadable -- which makes the list count
            as covering nothing, because a list we cannot identify
            cannot be credited against any sub-clause.
        land_category: Agriculture or Non-Agriculture, from the heading.
        district / mandal / village: As printed on the rows, so a list
            fetched for the wrong village can be caught rather than
            silently believed.
        entries: Every row read. Order preserved.
        total_extent: The form's own "TOTAL EXTENT" footer, when
            present. A cheap integrity signal: it is stated on the last
            page and does not depend on reading every row correctly.
        notes: Anything else worth recording, including any page the
            model could not read.
    """

    sub_clause: str | None = None
    land_category: LandCategory | None = None
    district: str | None = None
    mandal: str | None = None
    village: str | None = None
    entries: list[ProhibitedListEntry] = []
    total_extent: str | None = None
    notes: str | None = None


class ProhibitedMatchResult(BaseModel):
    """The matching outcome an agent turns into a Finding.

    Deliberately separate from the agent's report: the membership
    decision is pure, testable logic over extracted rows, and keeping
    it in its own type means it can be asserted on directly rather than
    only through the prose of a Finding.

    Attributes:
        matches: Rows whose survey number relates to the parcel's.
        sub_clauses_checked: Which of SUB_CLAUSES were actually supplied.
        rows_examined: Total rows read across every list.
    """

    matches: list[ProhibitedListEntry] = []
    sub_clauses_checked: list[str] = []
    rows_examined: int = Field(ge=0, default=0)

    @property
    def missing_sub_clauses(self) -> list[str]:
        """Sub-clauses nobody supplied; the gap a clean result depends on."""
        checked = set(self.sub_clauses_checked)
        return [c for c in SUB_CLAUSES if c not in checked]

    @property
    def fully_covered(self) -> bool:
        return not self.missing_sub_clauses


class ProhibitedPropertyReport(BaseAgentReport):
    """Full output contract of the Prohibited Property Agent.

    Attributes:
        agent_name: Pinned to "prohibited_property".
        extracts: One entry per prohibition list examined. At least one
            is required -- this agent only runs when lists were
            actually supplied, so an empty list means a malfunction.
        result: What the membership check concluded, including which
            sub-clauses were covered. Carried on the report so a reader
            can see the coverage behind the verdict rather than having
            to infer it from the finding's wording.
    """

    agent_name: Literal["prohibited_property"] = "prohibited_property"
    extracts: list[ProhibitedListExtract] = Field(min_length=1)
    result: ProhibitedMatchResult
