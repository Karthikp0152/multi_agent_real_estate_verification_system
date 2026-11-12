"""Tests for parsing a human-typed parcel boundary.

The risk being guarded against is silent misreading: a boundary parsed
wrong moves the parcel, and the setback answer becomes confidently about
the wrong land. So every failure mode here must raise, not coerce.
"""

import pytest

from bhoomicheck.geo.boundary import MIN_VERTICES, BoundaryParseError, parse_boundary


def test_semicolon_separated_pairs_parse() -> None:
    vertices = parse_boundary("17.0,79.0; 17.1,79.1; 17.2,79.0")
    assert len(vertices) == 3
    assert vertices[0].lat == 17.0
    assert vertices[0].lon == 79.0


def test_newline_separated_pairs_parse() -> None:
    """Pasting a column of coordinates is the obvious thing to do."""
    vertices = parse_boundary("17.0,79.0\n17.1,79.1\n17.2,79.0\n")
    assert len(vertices) == 3


def test_untidy_whitespace_and_trailing_separators_are_tolerated() -> None:
    vertices = parse_boundary("  17.0 , 79.0 ;\n 17.1,79.1 ;; 17.2,79.0 ;  ")
    assert len(vertices) == 3
    assert vertices[2].lon == 79.0


def test_the_recorded_source_is_carried_onto_every_vertex() -> None:
    vertices = parse_boundary("17.0,79.0;17.1,79.1;17.2,79.0", source="surveyed")
    assert {v.source for v in vertices} == {"surveyed"}


def test_vertex_order_is_preserved() -> None:
    """Order defines the ring; reordering would reshape the parcel."""
    vertices = parse_boundary("17.0,79.0; 17.5,79.5; 17.2,79.9")
    assert [v.lat for v in vertices] == [17.0, 17.5, 17.2]


def test_too_few_vertices_is_rejected() -> None:
    with pytest.raises(BoundaryParseError, match=f"at least {MIN_VERTICES}"):
        parse_boundary("17.0,79.0; 17.1,79.1")


def test_empty_text_is_rejected() -> None:
    with pytest.raises(BoundaryParseError, match="no coordinates"):
        parse_boundary("   \n  ")


def test_a_three_number_vertex_is_rejected_not_truncated() -> None:
    """Altitude from a KML export would otherwise be read as a longitude."""
    with pytest.raises(BoundaryParseError, match='expected "lat,lon"'):
        parse_boundary("17.0,79.0,0; 17.1,79.1; 17.2,79.0")


def test_a_non_numeric_vertex_is_rejected() -> None:
    with pytest.raises(BoundaryParseError, match="decimal degrees"):
        parse_boundary("17.0,79.0; north,79.1; 17.2,79.0")


def test_an_out_of_range_vertex_is_rejected() -> None:
    with pytest.raises(BoundaryParseError, match="out of range"):
        parse_boundary("17.0,79.0; 917.0,79.1; 17.2,79.0")


def test_a_single_pair_on_one_line_is_rejected_rather_than_treated_as_a_point() -> None:
    """Falling back to a point would silently weaken the answer."""
    with pytest.raises(BoundaryParseError):
        parse_boundary("17.856513,79.630249")
