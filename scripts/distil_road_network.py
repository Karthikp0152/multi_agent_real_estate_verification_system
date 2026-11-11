"""Turn a master-plan road-network KML into the distilled JSON we query.

Why this exists rather than reading the KML directly at runtime:

  * Size. A CAD-exported KML of the Warangal proposed road network is
    4.7 MB, of which ~45% is HTML tables of CAD metadata (pen colour,
    linetype, entity handle) from which we read exactly one integer --
    the proposed width out of the layer name -- and discard the rest.
  * Precision. CAD exports carry coordinates to ~14 decimal places,
    sub-micron, which is meaningless next to a town plan's own drafting
    accuracy. Rounding to 6 dp (~11 cm here) shifts a real computed
    setback distance by 10 cm.
  * Honesty. The distilled file records where it came from, so a report
    built on it can name its source instead of asserting geometry from
    nowhere.

Together those take 4.7 MB to ~1.4 MB (~0.4 MB once git compresses it).

Usage:
    python scripts/distil_road_network.py INPUT.kml OUTPUT.json \
        --label "KUDA Master Plan 2041 - proposed road network" \
        --source "describe where the KML came from"
"""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

_KML_NS = {"k": "http://www.opengis.net/kml/2.2"}

# 6 dp is ~11 cm at Warangal's latitude -- far finer than the plan was
# drawn, and coarse enough to drop two thirds of the coordinate bytes.
_COORD_DP = 6


def _width_from_description(text: str) -> int | None:
    """Pull the proposed width out of a CAD layer name.

    The KML's per-placemark description is an HTML table whose "Layer"
    row carries the road class, e.g. "Layer 45 M Color ...". That layer
    name is the *only* thing in the description worth keeping, and it is
    how the plan itself classifies the road.

    Returns None when no width can be read. Callers must not default a
    missing width to anything: a road of unknown width cannot support a
    setback claim.
    """
    stripped = re.sub(r"<[^>]+>", " ", text)
    layer = re.search(r"\bLayer\s+(.+?)\s+Color\b", stripped)
    if not layer:
        return None
    width = re.search(r"(\d+)\s*M\b", layer.group(1), re.I)
    return int(width.group(1)) if width else None


def distil(kml_path: Path, label: str, source: str) -> dict:
    raw = kml_path.read_text(encoding="utf-8", errors="replace")
    # This particular export uses the `xsi:` prefix without declaring the
    # namespace, which is malformed XML and makes ElementTree refuse the
    # file ("unbound prefix"). Declaring it in memory keeps the parser
    # happy without modifying the source file on disk.
    if "xmlns:xsi" not in raw:
        raw = raw.replace(
            "<kml ", '<kml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ', 1
        )

    root = ET.fromstring(raw)
    segments: list[dict] = []
    skipped = 0

    for placemark in root.findall(".//k:Placemark", _KML_NS):
        desc = placemark.find("k:description", _KML_NS)
        width = _width_from_description(desc.text or "") if desc is not None else None

        for coords in placemark.findall(".//k:coordinates", _KML_NS):
            points = []
            for token in (coords.text or "").split():
                parts = token.split(",")
                if len(parts) < 2:
                    continue
                try:
                    points.append(
                        [round(float(parts[0]), _COORD_DP), round(float(parts[1]), _COORD_DP)]
                    )
                except ValueError:
                    continue
            if len(points) < 2:
                continue
            if width is None:
                skipped += 1
                continue
            segments.append({"w": width, "p": points})

    return {
        "label": label,
        "source": source,
        "distilled_on": date.today().isoformat(),
        "distilled_from": kml_path.name,
        "segments_skipped_no_width": skipped,
        "segments": segments,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kml", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()

    data = distil(args.kml, args.label, args.source)
    args.out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    widths: dict[int, int] = {}
    for seg in data["segments"]:
        widths[seg["w"]] = widths.get(seg["w"], 0) + 1

    print(f"segments kept    {len(data['segments']):,}")
    print(f"vertices         {sum(len(s['p']) for s in data['segments']):,}")
    print(f"skipped (width?) {data['segments_skipped_no_width']:,}")
    print(f"width classes    {dict(sorted(widths.items(), key=lambda kv: -kv[0]))}")
    print(f"written          {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
