"""Litigation Agent: eCourts search results -> LitigationReport.

Deterministic, unlike DocumentAnalysisAgent: by the time this runs,
LitigationAdapter.parse() has already turned unstructured eCourts output
into LitigationSearchResult objects (0 or more cases, each with real
fields). There's no free text left to interpret, so mapping that
structured data to Findings needs no model call and no retry loop --
just the guardrail-honoring logic below, which is also easier to test
exactly because it's plain code.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.litigation import LitigationReport, LitigationSearchResult
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance

SOURCE_NAME = "eCourts (Warangal District Court + Telangana High Court, party-name search)"


def _scope_of(result: LitigationSearchResult) -> str:
    """Describe exactly what one search covered.

    Every claim this agent makes is scoped by it, because the portal's
    own form decides the scope: a required Year field and a required
    Court Complex mean one search is never a statement about a person,
    only about a person in one year in one court.
    """
    parts = []
    if result.court_complex:
        parts.append(f"in {result.court_complex}")
    else:
        parts.append("in the court complex searched")
    if result.year_searched is not None:
        parts.append(f"for filing year {result.year_searched}")
    else:
        # Loud rather than silent: without a year we cannot say what was
        # covered, and the portal cannot have been searched without one.
        parts.append("for an unrecorded filing year")
    if result.status_filter == "pending":
        parts.append("among pending cases only")
    elif result.status_filter == "disposed":
        parts.append("among disposed cases only")
    elif result.status_filter == "both":
        parts.append("among both pending and disposed cases")
    return " ".join(parts)


class LitigationAgent:
    """Builds a validated LitigationReport from one or more eCourts searches."""

    def report(
        self,
        parcel: ParcelIdentifier,
        searches: list[tuple[Path, LitigationSearchResult]],
    ) -> LitigationReport:
        """Assemble a report from (uploaded file, parsed search result) pairs.

        Args:
            parcel: The parcel this litigation check is for.
            searches: One entry per name variant searched -- the file the
                human uploaded (for provenance) paired with what
                LitigationAdapter.parse() produced from it. The filename
                is a system-known fact fed in by the caller, same as
                DocumentAnalysisAgent's PDF paths -- never guessed here.
        """
        fetched_at = datetime.now(timezone.utc).date()
        findings = [
            self._finding_for(upload_path, result, fetched_at)
            for upload_path, result in searches
        ]
        return LitigationReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            searches=[result for _, result in searches],
            findings=findings,
        )

    def _finding_for(
        self, upload_path: Path, result: LitigationSearchResult, fetched_at: date
    ) -> Finding:
        provenance = Provenance(
            source_name=SOURCE_NAME,
            source_type="manual",
            document=upload_path.name,
            fetched_at=fetched_at,
        )

        scope = _scope_of(result)

        if not result.searched_name.strip():
            # Without a name, these rows cannot be attributed to anyone.
            # Seen for real: a screenshot of a Case Type search has a
            # populated results table and an empty name box, and the
            # agent rendered 'cases naming "" as a party' -- a finding
            # that reads like a name match while identifying nobody.
            # Whatever the rows are, they are not evidence about this
            # seller until someone says which name was searched.
            return Finding(
                check_id="litigation.name_match",
                claim=(
                    f"{len(result.cases)} case(s) were listed {scope}, but "
                    "the search term was not recorded, so they cannot be "
                    "attributed to the seller"
                ),
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(
                    level="low",
                    reason=(
                        "the uploaded result does not show which name was "
                        "searched -- re-run the search from the Party Name "
                        "tab and capture the form along with the results"
                    ),
                ),
            )

        if not result.cases:
            # NOT a blanket "no litigation". The eCourts party-name search
            # requires a filing year and a court complex, and searches one
            # of each at a time -- confirmed against the live Warangal
            # portal, where Year is a required field. So an empty result
            # is evidence about that year and that complex only, and
            # saying otherwise would be the most dangerous sentence this
            # system could print.
            return Finding(
                check_id="litigation.name_match",
                claim=(
                    f'no cases naming "{result.searched_name}" as a party '
                    f"were found {scope}"
                ),
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(
                    level="low",
                    reason=(
                        "the eCourts party-name search covers one filing "
                        "year and one court complex per search, so this "
                        "clears neither other years nor other courts; and "
                        "being name-based, misspellings, untried variants "
                        "or a filing under a different name would not "
                        "surface here either"
                    ),
                ),
            )

        case_summary = "; ".join(
            f"{case.case_no or case.cnr_number or 'unnumbered case'} "
            f"({case.court}, {case.status})"
            for case in result.cases
        )
        return Finding(
            check_id="litigation.name_match",
            claim=(
                f'{len(result.cases)} case(s) naming "{result.searched_name}" '
                f"as a party were found {scope}: {case_summary}"
            ),
            status="issue_found",
            provenance=provenance,
            confidence=Confidence(
                level="low",
                reason=(
                    "name match only, per CLAUDE.md's hard guardrail -- not "
                    "confirmed to be the parcel's actual owner/seller; "
                    "requires human verification before treating as a real "
                    "litigation risk"
                ),
            ),
        )
