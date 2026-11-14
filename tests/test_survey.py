"""Tests for survey-number comparison.

Both directions of error are silent in production, so both are pinned
down here: a missed match reports a prohibited parcel as clean, and a
spurious match accuses a named person of holding restricted land.
"""

import pytest

from bhoomicheck.survey import related, segments


class TestSegments:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("16", ("16",)),
            ("16/D", ("16", "D")),
            ("49/53/B/1", ("49", "53", "B", "1")),
            # Whitespace and case as they appear in real lists.
            (" 16 / d ", ("16", "D")),
            # The registers write the same parcel both ways.
            ("279A", ("279", "A")),
            ("279/A", ("279", "A")),
            # Hyphen is used occasionally instead of a slash.
            ("16-D", ("16", "D")),
        ],
    )
    def test_parses_the_notations_the_registers_actually_use(self, raw, expected):
        assert segments(raw) == expected

    def test_unusable_input_yields_nothing(self) -> None:
        assert segments("") == ()
        assert segments("   ") == ()
        assert segments("///") == ()


class TestRelated:
    def test_an_exact_match_relates(self) -> None:
        assert related("16/D", "16/D")

    def test_the_whole_survey_number_covers_its_subdivisions(self) -> None:
        """A prohibition on survey 16 necessarily covers 16/D."""
        assert related("16", "16/D")
        assert related("16/D", "16")

    def test_a_restricted_part_of_the_parcel_still_matters(self) -> None:
        """An entry for 16/D/2 is part of the buyer's 16/D."""
        assert related("16/D", "16/D/2")

    def test_the_two_notations_for_one_parcel_match(self) -> None:
        assert related("279A", "279/A")

    def test_sibling_subdivisions_do_not_match(self) -> None:
        """The false-accusation case.

        16/A is someone else's land. Reporting their restriction as the
        buyer's would be a claim about a named person that the evidence
        does not support.
        """
        assert not related("16/D", "16/A")

    def test_a_numeric_prefix_is_not_a_match(self) -> None:
        """160 is not a sub-division of 16, and 16 is not part of 160."""
        assert not related("16", "160")
        assert not related("160", "16")
        assert not related("16", "161/A")

    def test_unrelated_numbers_do_not_match(self) -> None:
        assert not related("16", "49")
        assert not related("16/D", "254/125/B/2")

    def test_an_unreadable_number_never_matches(self) -> None:
        """A gap must be reported as a gap, not resolved as a match."""
        assert not related("", "16")
        assert not related("16", "")
        assert not related("   ", "16/D")

    @pytest.mark.parametrize(
        "listed",
        # Real rows from the Chennaram 22A(1)(a) Agriculture list.
        ["8/1", "10/A/3", "20", "22", "31/1", "49/53/B/2", "254/110/E", "279A", "464"],
    )
    def test_survey_16_matches_none_of_the_real_chennaram_rows(self, listed) -> None:
        """Pins the real-world result this was built to produce."""
        assert not related("16/D", listed)
