"""Prohibited Property Agent: 22-A list extracts -> a membership verdict.

Answers the check Bhu Bharati structurally cannot (see
schemas/land_records.py): is this parcel in the Section 22-A prohibited
register? Emits check_id "land_records.prohibited_land", the same
question LandRecordsAgent reports as unanswerable -- the risk report's
precedence rule keeps the real answer and supersedes the gap, so the
check is scored once.

Two rules carry the weight here:

1. THE MODEL NEVER DECIDES MEMBERSHIP. It transcribes survey numbers;
   survey.related() decides whether any of them touch this parcel. A
   model asked "is 16 in this list?" gives an answer that cannot be
   unit-tested, cannot be audited, and fails quietly. The comparison
   is 20 lines of code with 25 tests behind it instead.

2. PARTIAL COVERAGE IS NOT A CLEAN RESULT. IGRS publishes seven lists
   per land category. Absent from one of them is not absent from the
   register, so anything short of full coverage reports "not_verified"
   with the gap named (architecture principle 6).
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.prohibited import (
    SUB_CLAUSES,
    ProhibitedListExtract,
    ProhibitedMatchResult,
    ProhibitedPropertyReport,
)
from bhoomicheck.schemas.provenance import Provenance
from bhoomicheck.survey import related

SOURCE_NAME = "IGRS Telangana - Section 22-A prohibited property register"

CHECK_ID = "land_records.prohibited_land"

# Stated on every finding from this source. The lists are page scans
# published by the Tahsildar and countersigned up to the District
# Collector; they are authoritative, but they are also a point-in-time
# publication, and a transcription step sits between them and us.
SOURCE_CAVEAT = (
    "read from the scanned FORM-I lists IGRS publishes per village; these "
    "are periodic publications, so confirm with the Sub-Registrar before "
    "relying on this for a purchase decision"
)


def match_against_lists(
    parcel: ParcelIdentifier, extracts: list[ProhibitedListExtract]
) -> ProhibitedMatchResult:
    """Decide whether the parcel appears in any supplied prohibition list.

    Pure over its inputs so it can be tested without a model, a file or
    a graph.
    """
    matches = []
    rows = 0
    covered: list[str] = []
    unpublished: list[str] = []
    for extract in extracts:
        rows += len(extract.entries)
        # A list whose heading could not be read covers nothing: we
        # cannot credit it against a sub-clause we cannot name, and
        # guessing which one it was would overstate the coverage.
        if extract.sub_clause in SUB_CLAUSES:
            if extract.availability == "under_review":
                # The department has not published this register. It was
                # attempted and is unobtainable -- recorded separately so
                # the report can say whose gap it is.
                if extract.sub_clause not in unpublished:
                    unpublished.append(extract.sub_clause)
            elif extract.sub_clause not in covered:
                covered.append(extract.sub_clause)
        for entry in extract.entries:
            if entry.survey_no and related(parcel.survey_no, entry.survey_no):
                matches.append(entry)
    # A register that is both read somewhere and under review elsewhere
    # counts as read: a real answer beats an unavailable one.
    unpublished = [c for c in unpublished if c not in covered]
    return ProhibitedMatchResult(
        matches=matches,
        sub_clauses_checked=covered,
        sub_clauses_unpublished=unpublished,
        rows_examined=rows,
    )


class ProhibitedPropertyAgent:
    """Builds a ProhibitedPropertyReport from 22-A list extracts."""

    def report(
        self,
        parcel: ParcelIdentifier,
        extracts: list[tuple[Path, ProhibitedListExtract]],
    ) -> ProhibitedPropertyReport:
        """Assemble a report from (uploaded list, parsed extract) pairs."""
        fetched_at = datetime.now(timezone.utc).date()
        parsed = [extract for _, extract in extracts]
        result = match_against_lists(parcel, parsed)
        documents = ", ".join(sorted({path.name for path, _ in extracts}))
        return ProhibitedPropertyReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            extracts=parsed,
            result=result,
            findings=[self._finding(parcel, result, documents, fetched_at)],
        )

    def _finding(
        self,
        parcel: ParcelIdentifier,
        result: ProhibitedMatchResult,
        documents: str,
        fetched_at: date,
    ) -> Finding:
        provenance = Provenance(
            source_name=SOURCE_NAME,
            source_type="manual",
            document=documents or "prohibited property lists",
            fetched_at=fetched_at,
        )
        scope = (
            f"{result.rows_examined} rows across "
            f"{len(result.sub_clauses_checked)} of {len(SUB_CLAUSES)} "
            "sub-clause lists"
        )
        if result.sub_clauses_unpublished:
            scope += (
                f", {len(result.sub_clauses_unpublished)} not published by "
                "the department"
            )

        if result.matches:
            rows = "; ".join(
                " ".join(
                    part
                    for part in (
                        f"survey {entry.survey_no}",
                        f"held by {entry.owner_name}" if entry.owner_name else "",
                        f"extent {entry.extent}" if entry.extent else "",
                        f"under {entry.act}" if entry.act else "",
                    )
                    if part
                )
                for entry in result.matches
            )
            return Finding(
                check_id=CHECK_ID,
                claim=(
                    f"survey no. {parcel.survey_no} appears in the Section 22-A "
                    f"prohibited register for {parcel.village}: {rows}"
                ),
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(
                    level="high",
                    reason=(
                        "the parcel's survey number matches a published "
                        f"prohibition entry -- {SOURCE_CAVEAT}"
                    ),
                ),
            )

        if not result.fully_covered:
            missing = ", ".join(result.missing_sub_clauses)
            return Finding(
                check_id=CHECK_ID,
                claim=(
                    f"survey no. {parcel.survey_no} was not found in the "
                    f"prohibition lists supplied ({scope}), but the register "
                    "was only partly checked"
                ),
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(
                    level="low",
                    reason=(
                        "absent from some lists is not absent from the "
                        f"register -- still unchecked: {missing}"
                    ),
                ),
            )

        if result.sub_clauses_unpublished:
            # Every obtainable register was read, and the parcel is in
            # none of them -- but some registers do not exist publicly
            # yet, so the parcel cannot honestly be called cleared.
            # Nothing the buyer does closes this; only the department
            # publishing can. Stated as such rather than as a chore.
            pending = ", ".join(result.sub_clauses_unpublished)
            return Finding(
                check_id=CHECK_ID,
                claim=(
                    f"survey no. {parcel.survey_no} does not appear in any "
                    f"published Section 22-A list for {parcel.village} "
                    f"({scope}), but the department has not yet published "
                    f"every register"
                ),
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(
                    level="medium",
                    reason=(
                        "every register that is published was checked and the "
                        f"parcel is in none of them; {pending} "
                        'still reports "prohibition data is under review", so '
                        "no search can clear the parcel against it until the "
                        f"department publishes -- {SOURCE_CAVEAT}"
                    ),
                ),
            )

        return Finding(
            check_id=CHECK_ID,
            claim=(
                f"survey no. {parcel.survey_no} does not appear in any Section "
                f"22-A prohibited list for {parcel.village} ({scope})"
            ),
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="high", reason=SOURCE_CAVEAT),
        )
