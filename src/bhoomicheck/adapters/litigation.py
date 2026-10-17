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

STILL UNVALIDATED AGAINST A REAL SAMPLE: parse() is implemented -- a
vision model reads the uploaded search result into the schema, and
anything it cannot read stays null rather than being guessed. What is
NOT yet confirmed is whether the extraction holds up against a genuine
eCourts result page, which is laid out quite differently on the District
Court and High Court portals.
"""

from pathlib import Path

from bhoomicheck.adapters.base import ManualAdapter
from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.litigation import LitigationSearchQuery, LitigationSearchResult

_PARSE_CONTEXT = """\
This is a screenshot (or written note) of an eCourts case-status search
result, from either the Warangal District Court portal or the Telangana
High Court portal. The search was by party name.

Read off, where legible:
- searched_name: the name that was typed into the search box
- one entry per case listed, copying the court name, case number, CNR
  number, case type, filing date, the party name as the listing prints
  it, and that party's role (petitioner/respondent) where shown
- status: "pending" or "disposed" as the listing states; "unknown" only
  if the listing genuinely does not say

If the search returned no cases, return an empty cases list -- that is a
real and useful result, not an error. Never invent a case, and never
merge two listed cases into one."""


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
        return (
            "Search for pending or disposed cases naming this person as a "
            f"party. Try each of these name variants: {names} -- eCourts "
            "matches on partial names (3+ characters), so short forms and "
            "spelling variants can surface different results.\n\n"
            "1. Warangal District Court:\n"
            "   https://warangal.dcourts.gov.in/case-status-search-by-petitioner-respondent/\n"
            "   Select Both (pending and disposed), enter a name, solve "
            "the captcha, note every case listed.\n\n"
            "2. Telangana High Court:\n"
            "   https://hcservices.ecourts.gov.in/\n"
            "   Case Status -> Search by Petitioner/Respondent Name -> "
            "select Telangana, enter a name, solve the captcha, note "
            "every case listed.\n\n"
            "Upload a screenshot (or written note) of every case shown "
            "for each name variant tried, including cases you don't "
            "recognize -- do not filter results yourself. Deciding which "
            "matches are plausible is a human-review step later, not "
            "part of running the search."
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
