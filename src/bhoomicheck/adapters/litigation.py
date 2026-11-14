"""LitigationAdapter: manual-mode adapter for eCourts party-name searches.

Both real government portals confirmed via search (not guessed):
  https://warangal.dcourts.gov.in/case-status-search-by-petitioner-respondent/
  https://hcservices.ecourts.gov.in/ (Case Status -> Search by
  Petitioner/Respondent Name -> Telangana)

Both require solving a captcha to search, which is exactly the situation
CLAUDE.md's hard guardrail exists for: we never automate around a
captcha, so this stays manual-only -- a human runs the search themselves
and hands back what the portal showed.

Litigation search is keyed by a person's name, not a parcel (unlike KUDA
master plan) -- that mismatch is why ManualAdapter became generic over
the query type, not just the raw-record type. See adapters/base.py.

The other hard guardrail this file exists to enforce: a name match on a
court listing is never proof the case involves the parcel's actual owner
-- common Indian names produce false positives constantly. `match_basis`
on LitigationCaseExtract (schemas/litigation.py) is pinned to a fixed
literal so every case extracted here structurally declares itself an
unconfirmed name match, not a verdict about a person.

FORM AND RESULTS CONFIRMED against the live Warangal portal, and the
form changes what this check can honestly claim:

  * The party-name search makes **Year** a required field and runs
    against ONE court complex at a time. There is no "all years" or
    "all courts" option. So a single search is evidence about one year
    in one complex -- never about a person. The agent scopes every
    claim accordingly, and an empty result is "not_verified", not a
    clean bill.
  * The results table carries only Serial Number, "Case Type/Case
    Number/Case Year", "Petitioner versus Respondent", and a View
    button. CNR number and filing date are NOT on it -- they sit behind
    a click per case, so a name returning forty rows would mean forty
    clicks. Those fields stay optional and usually null.
  * There is no status column. A row's status is whichever of the
    Pending / Disposed / Both radios produced it, which is why
    LitigationSearchResult records the filter itself.
"""

from pathlib import Path

from bhoomicheck.adapters.base import ManualAdapter
from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.litigation import LitigationSearchQuery, LitigationSearchResult

_PARSE_CONTEXT = """\
This is a screenshot of an eCourts case-status search result, from the
Warangal District Court portal or the Telangana High Court portal.

THE SEARCH FORM IS USUALLY VISIBLE ABOVE THE RESULTS. Read it, because
it is what tells us how much the search actually covered:
- year_searched: the "Year" field. The portal REQUIRES a year and
  searches one at a time, so this is the single most important thing on
  the page after the cases themselves. Null only if truly not visible.
- court_complex: the "Court Complex" dropdown, e.g. "Warangal, PDJ
  Court Complex". A search covers only that complex.
- status_filter: which radio is selected -- "pending", "disposed" or
  "both". The results table has NO status column, so this is the only
  thing that says what the rows represent.
- searched_name: the "Petitioner/Respondent" box.

The results table is headed by the court, e.g. "District Courts,
Warangal", with these columns:
    Serial Number | Case Type/Case Number/Case Year | Petitioner versus
    Respondent | View

One entry per row:
- case_no: the whole "Case Type/Case Number/Case Year" cell, exactly as
  printed, e.g. "SC.NDPS/34/2025".
- case_type: just the leading type from that cell, e.g. "SC.NDPS".
- court: from the table heading plus the court complex.
- matched_party_name: the "Petitioner versus Respondent" cell holds TWO
  names separated by "Versus". Copy the one that matches the searched
  name. If neither obviously matches (the portal matches on partial
  names), copy the whole cell and say so in notes rather than guessing.
- party_role: "Petitioner" if the matched name is above "Versus",
  "Respondent" if below.
- status: take it from the status_filter, because the table does not
  print it per row. If the filter was "both", use "unknown" -- do not
  guess which side a row falls on.
- cnr_number and filing_date: these are NOT on the results list. They
  sit behind the "View" button. Leave them null unless you are looking
  at a case detail page.

If the search returned no cases, return an empty cases list -- that is a
real and useful result, not an error, and the year/court/filter fields
still matter because they say what was covered. Never invent a case, and
never merge two listed cases into one."""


class LitigationAdapter(ManualAdapter[LitigationSearchQuery, LitigationSearchResult]):
    """Manual-mode adapter: eCourts party-name search, District + High Court.

    `instructions()` points at real, confirmed portal URLs. `parse()`
    reads the uploaded search result with a vision model -- note that a
    case it extracts is still only a NAME match; LitigationCaseExtract
    pins match_basis to say so structurally.
    """

    source_name = "eCourts (Warangal District Court + Telangana High Court, party-name search)"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """
        Args:
            provider: Model backend used to read the search result.
                Optional so instructions() works without one; parse()
                raises if it was never supplied.
        """
        self.provider = provider

    def instructions(self, query: LitigationSearchQuery) -> str:
        names = ", ".join([query.full_name, *query.also_known_as])
        complex_line = (
            f"   Court Complex: {query.court_complex}\n"
            if query.court_complex
            else "   Court Complex: pick the one covering this parcel; note "
            "which you chose, because the search only covers that one.\n"
        )
        if query.years:
            years = ", ".join(str(y) for y in sorted(query.years))
            years_line = f"   Years to search, one at a time: {years}\n"
        else:
            years_line = (
                "   Years: no range has been decided yet. Pick one that "
                "covers the period you care about -- the deed chain is a "
                "reasonable guide -- and record every year you searched.\n"
            )

        return (
            "Search for cases naming this person as a party. Try each of "
            f"these name variants: {names} -- eCourts matches on partial "
            "names (3+ characters), so short forms and spelling variants "
            "can surface different results.\n\n"
            "IMPORTANT, and the reason this is more work than it looks: "
            "the party-name search makes **Year** a required field and "
            "runs against ONE court complex at a time. A single search "
            "therefore proves nothing about other years or other courts, "
            "and the report will say so. Repeat it per year.\n\n"
            "1. Warangal District Court:\n"
            "   https://warangal.dcourts.gov.in/case-status-search-by-petitioner-respondent/\n"
            "   Open the 'Party Name' tab.\n"
            + complex_line
            + years_line
            + "   Select 'Both' so pending and disposed are covered: the "
            "results table has no status column, so a Pending-only search "
            "silently omits every disposed case.\n"
            "   Solve the captcha and search.\n\n"
            "2. Telangana High Court:\n"
            "   https://hcservices.ecourts.gov.in/\n"
            "   Case Status -> Search by Petitioner/Respondent Name -> "
            "select Telangana, enter a name, solve the captcha.\n\n"
            "Screenshot every results table in full, including the column "
            "headers and including searches that return nothing -- an "
            "empty year is a real data point. Record which name, year, "
            "court complex and status filter produced each screenshot; "
            "without that the result cannot be scoped and is close to "
            "useless.\n\n"
            "Do not filter the results yourself: upload cases you do not "
            "recognise too. Deciding which matches are plausible is a "
            "human-review step later, not part of running the search."
        )

    def parse(self, upload_path: Path) -> LitigationSearchResult:
        if self.provider is None:
            raise RuntimeError(
                "LitigationAdapter.parse() needs an LLMProvider; "
                "construct it as LitigationAdapter(provider)."
            )
        result = extract_structured(
            self.provider, upload_path, LitigationSearchResult, _PARSE_CONTEXT
        )
        assert isinstance(result, LitigationSearchResult)
        return result
