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

# What state a sub-clause's register is actually in. Confirmed by eye
# against the live portal, where the seven lists do NOT all behave the
# same way:
#
#   published    - a FORM-I scan, or a populated live table.
#   under_review - the portal answers "Prohibition data is under review.
#                  it will be published soon..." (seen for 22 A (1)(c)
#                  Waqf and Endowment, Warangal, Nov 2026). The
#                  department has not published this register at all.
#   empty        - the register is published and contains no entry for
#                  this village ("No property is found to be prohibited
#                  in this village with given details").
#
# The distinction is load-bearing. "Under review" is a gap the
# government owns and nobody can close by fetching harder, while a list
# simply not supplied is a gap the user can close. Reporting them
# identically would either nag the user for something unobtainable or
# quietly imply a register was consulted when it does not exist yet.
ListAvailability = Literal["published", "under_review", "empty"]


class ProhibitedListEntry(BaseModel):
    """One row of a FORM-I prohibition list.

    Column names are the form's own, confirmed against a real Warangal
    list rather than guessed.

    Attributes:
        survey_no: The parcel's survey number including any
            sub-division, e.g. "49/53/B/1" or "279A". The only field
            used for matching; everything else is for the reader.

            The FORM-I scans print this as one column ("Sy. No. &
            Sub-Division No."). The live "Court cases & Others" table
            splits it across "Survey No." and "Sub Sy No." instead, so
            the adapter recombines them here and keeps the raw parts
            below. One matching field, however the source lays it out.
        sub_survey_no: The "Sub Sy No." column, when the source has one.
        revenue_survey_no: "Rev Sy No." -- a revenue survey number can
            differ from the registration one for the same land, so it
            is kept rather than merged.
        plot_no: "Plot No.", for laid-out plots.
        reason: The "Reason" column on the court-cases table, e.g. the
            nature of the attachment. The FORM-I lists carry `act`
            instead; both answer "why is this restricted".
        order_reference: "Letter/Memo/Order/Notification No." and its
            date, as printed. This is the instrument behind the
            prohibition and the thing a lawyer would ask for.
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
    sub_survey_no: str | None = None
    revenue_survey_no: str | None = None
    plot_no: str | None = None
    reason: str | None = None
    order_reference: str | None = None


class ProhibitedListExtract(BaseModel):
    """One prohibition list, as read off its scanned pages.

    Attributes:
        availability: Whether this register is published at all. A
            list the portal reports as "under review" conveys no
            information about the parcel and must never be credited as
            a check that passed.
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
    availability: ListAvailability | None = None
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
        sub_clauses_checked: Sub-clauses whose register was published
            and actually read -- including ones that came back empty,
            which is a real answer.
        sub_clauses_unpublished: Sub-clauses the portal itself reports
            as not yet published. Attempted, unobtainable, and not the
            user's fault.
        rows_examined: Total rows read across every list.
    """

    matches: list[ProhibitedListEntry] = []
    sub_clauses_checked: list[str] = []
    sub_clauses_unpublished: list[str] = []
    rows_examined: int = Field(ge=0, default=0)

    @property
    def missing_sub_clauses(self) -> list[str]:
        """Sub-clauses nobody supplied, and which the user could still fetch."""
        accounted = set(self.sub_clauses_checked) | set(self.sub_clauses_unpublished)
        return [c for c in SUB_CLAUSES if c not in accounted]

    @property
    def fully_covered(self) -> bool:
        """Every register that exists has been read.

        Deliberately true when the only gaps are unpublished registers:
        the user has done everything that can be done, so nagging them
        for a list the department has not released would be useless.
        The finding still states those gaps -- see the agent -- so this
        is a judgement about whose action is outstanding, not a claim
        that the parcel is fully cleared.
        """
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
