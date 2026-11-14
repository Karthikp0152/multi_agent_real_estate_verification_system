"""Parse a parcel boundary typed or pasted by a human.

Separate from road_network.py because this is input handling, not
geometry: the maths module should not care how a coordinate list reached
it. Both the HTTP API and the CLI parse the same text format through
here, so they cannot drift apart in what they accept.

The format is whatever someone reading vertices off a GIS viewer would
naturally type: "lat,lon" pairs separated by semicolons or newlines.

    17.856513,79.630249; 17.856600,79.630400; 17.856400,79.630500

Errors are loud and name the offending token. A boundary quietly parsed
wrong would move the parcel, and the setback answer would be confidently
about the wrong piece of land.
"""

from bhoomicheck.schemas.geo import LatLon

# Minimum for an enclosed area. Two points are a line, not a parcel.
MIN_VERTICES = 3


class BoundaryParseError(ValueError):
    """Raised when boundary text cannot be read as coordinates.

    A ValueError subclass so callers that already handle bad input
    uniformly keep working, but distinct enough to catch on its own.
    """


def parse_boundary(text: str, *, source: str = "gis_readout") -> list[LatLon]:
    """Read "lat,lon" pairs into a boundary ring.

    Args:
        text: Pairs separated by semicolons, newlines or commas-and-
            spaces. Surrounding whitespace and trailing separators are
            tolerated, because pasted text usually has them.
        source: How these coordinates were obtained, recorded on every
            vertex so a report can say how much to trust them.

    Returns:
        The vertices in the order given. The ring is NOT closed here --
        RoadNetwork closes it, so a caller repeating the first vertex at
        the end would create a zero-length edge.

    Raises:
        BoundaryParseError: on anything unreadable, out of range, or
            with fewer than three vertices.
    """
    chunks = [
        chunk.strip()
        for chunk in text.replace("\n", ";").replace("\r", ";").split(";")
        if chunk.strip()
    ]
    if not chunks:
        raise BoundaryParseError("no coordinates found")

    vertices: list[LatLon] = []
    for chunk in chunks:
        parts = [part.strip() for part in chunk.split(",") if part.strip()]
        if len(parts) != 2:
            raise BoundaryParseError(
                f'expected "lat,lon" but got "{chunk}" -- separate vertices with '
                "semicolons or newlines, and lat from lon with a comma"
            )
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise BoundaryParseError(
                f'"{chunk}" is not a pair of decimal degrees'
            ) from exc
        try:
            # LatLon's own validators enforce the ranges; re-raising as a
            # BoundaryParseError keeps callers to one error type and lets
            # the message name which vertex was wrong.
            vertices.append(LatLon(lat=lat, lon=lon, source=source))
        except ValueError as exc:
            raise BoundaryParseError(f'vertex "{chunk}" is out of range: {exc}') from exc

    if len(vertices) < MIN_VERTICES:
        raise BoundaryParseError(
            f"a parcel boundary needs at least {MIN_VERTICES} vertices, "
            f"got {len(vertices)}"
        )
    return vertices
