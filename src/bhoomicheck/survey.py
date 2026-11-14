"""Comparing survey numbers.

Its own module because this is the single piece of logic standing
between "this parcel is in the prohibited register" and "it is not",
and getting it wrong is silent in both directions: too strict and a
listed parcel is reported clean, too loose and a clean parcel is
accused.

Telangana survey numbers are hierarchical. "49" is a survey number;
"49/53" is a sub-division of it; "49/53/B/1" a sub-division of that.
A buyer looking at 16/D needs to know about all three of:

  * an entry for "16/D" -- their exact parcel;
  * an entry for "16" -- the whole survey number is restricted, which
    necessarily includes their part of it;
  * an entry for "16/D/2" -- part of their parcel is restricted, which
    is still their problem.

So relatedness, not equality, is the test. Unrelated branches like
"16/A" against "16/D" must NOT match: flagging a neighbour's
restriction as the buyer's would be a false accusation about a named
person, which CLAUDE.md's guardrails forbid.
"""

import re

# A segment written as digits-then-letters, e.g. "279A", which the
# published lists use interchangeably with "279/A".
_NUMBER_THEN_LETTERS = re.compile(r"(\d+)\s*([A-Z]+)")


def segments(survey_no: str) -> tuple[str, ...]:
    """Break a survey number into its hierarchy, most significant first.

    Normalises the notations the registers actually mix:

        "16/D"      -> ("16", "D")
        "16-D"      -> ("16", "D")
        "279A"      -> ("279", "A")     -- same parcel as "279/A"
        "49/53/B/1" -> ("49", "53", "B", "1")

    Returns an empty tuple for anything unusable, which callers must
    treat as "no match possible" rather than "matches everything".
    """
    out: list[str] = []
    cleaned = survey_no.upper().replace("-", "/").replace("\\", "/")
    for raw in cleaned.split("/"):
        token = raw.strip()
        if not token:
            continue
        combined = _NUMBER_THEN_LETTERS.fullmatch(token)
        if combined:
            # "279A" and "279/A" denote the same parcel, and the lists
            # use both. Splitting them makes the two forms compare equal
            # instead of looking like unrelated parcels.
            out.extend([combined.group(1), combined.group(2)])
        else:
            out.append(token)
    return tuple(out)


def related(a: str, b: str) -> bool:
    """Whether two survey numbers refer to overlapping land.

    True when they are equal, or when one is an ancestor of the other.
    False for unrelated branches, and False when either is unparseable
    -- an unreadable survey number is a gap to report, never a match to
    assert.
    """
    sa, sb = segments(a), segments(b)
    if not sa or not sb:
        return False
    shortest = min(len(sa), len(sb))
    return sa[:shortest] == sb[:shortest]
