"""Run one real screenshot through one real adapter and show what the model read.

This is the tool for the question that actually decides the project: when
a human hands back a real portal screenshot, does the vision model read
it correctly? Unit tests can't answer that -- they script the model's
response. Only a real file and a real model call can.

Usage:
    python scripts/verify_extraction.py kuda ~/Desktop/kuda.png
    python scripts/verify_extraction.py land_records ~/Desktop/bhu.png
    python scripts/verify_extraction.py litigation ~/Desktop/ecourts.png
    python scripts/verify_extraction.py owner_background ~/Desktop/igrs.png
    python scripts/verify_extraction.py prohibited ~/Desktop/22a.pdf \
        --survey-no 16/D --village Chennaram --mandal Wardhannapet

What to look for in the output:
- EVERY FIELD NULL usually means the schema's field names don't match
  what the page actually shows, or the screenshot is unreadable. That is
  a schema problem to fix, not a model failure.
- A WRONG value is far more serious than a null. A null becomes a
  "not verified" finding; a wrong value becomes a confident false claim.
  Check values against the screenshot by eye, every time.
- For kuda, the single most important line is ROAD CROSSES PARCEL. The
  whole premise of this project is telling "a road cuts through this
  plot" apart from "a road runs nearby". If the model can't do that
  reliably, that needs to be known now.

This script makes real, billable API calls. It is a development tool and
is not imported by the package.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from bhoomicheck.adapters.kuda import KudaMasterPlanAdapter
from bhoomicheck.adapters.land_records import LandRecordsAdapter
from bhoomicheck.adapters.litigation import LitigationAdapter
from bhoomicheck.adapters.owner_background import OwnerBackgroundAdapter
from bhoomicheck.adapters.prohibited_property import ProhibitedPropertyAdapter
from bhoomicheck.agents.growth_potential import GrowthPotentialAgent
from bhoomicheck.agents.land_records import LandRecordsAgent
from bhoomicheck.agents.litigation import LitigationAgent
from bhoomicheck.agents.owner_background import OwnerBackgroundAgent
from bhoomicheck.agents.prohibited_property import ProhibitedPropertyAgent
from bhoomicheck.agents.zoning import ZoningAgent
from bhoomicheck.llm.gemini import GeminiProvider
from bhoomicheck.schemas.parcel import ParcelIdentifier

SOURCES = {
    "kuda": (KudaMasterPlanAdapter, [ZoningAgent, GrowthPotentialAgent]),
    "land_records": (LandRecordsAdapter, [LandRecordsAgent]),
    "litigation": (LitigationAdapter, [LitigationAgent]),
    "owner_background": (OwnerBackgroundAdapter, [OwnerBackgroundAgent]),
    "prohibited": (ProhibitedPropertyAdapter, [ProhibitedPropertyAgent]),
}

GREEN, YELLOW, RED, DIM, BOLD, OFF = (
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


def _show_record(record: BaseModel) -> int:
    """Print every field and its value. Returns how many came back null."""
    print(f"\n{BOLD}What the model read:{OFF}")
    nulls = 0
    for name, value in record.model_dump().items():
        empty = value is None or value == [] or value == ""
        nulls += empty
        marker = f"{DIM}null{OFF}" if empty else f"{GREEN}{value!r}{OFF}"
        print(f"  {name:28} {marker}")
    return nulls


def _show_entry_sample(record: BaseModel) -> None:
    """For list transcriptions, show how many rows came back and a sample.

    A truncated transcription is the failure mode that matters here: a
    model that reads 40 rows of a 372-row list and stops would report a
    listed parcel as absent, so the count is the headline.
    """
    entries = getattr(record, "entries", None)
    if entries is None:
        return
    print(f"\n{BOLD}Rows transcribed: {len(entries)}{OFF}")
    for entry in entries[:5]:
        print(f"  {DIM}{entry.survey_no!r:16} {entry.owner_name}{OFF}")
    if len(entries) > 5:
        print(f"  {DIM}... and {len(entries) - 5} more{OFF}")


def _show_findings(agent_classes, parcel: ParcelIdentifier, upload: Path, record) -> None:
    print(f"\n{BOLD}Findings this produces:{OFF}")
    for agent_class in agent_classes:
        report = agent_class().report(parcel, [(upload, record)])
        for finding in report.findings:
            colour = {
                "issue_found": RED,
                "not_verified": YELLOW,
                "verified_ok": GREEN,
            }[finding.status]
            print(f"  {colour}{finding.status:13}{OFF} {finding.check_id}")
            print(f"  {DIM}{'':13} {finding.claim}{OFF}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="verify_extraction")
    parser.add_argument("source", choices=sorted(SOURCES))
    parser.add_argument("screenshot", type=Path)
    parser.add_argument("--survey-no", default="123/A")
    parser.add_argument("--village", default="Hasanparthy")
    parser.add_argument("--mandal", default="Hasanparthy")
    parser.add_argument("--district", default="Warangal")
    args = parser.parse_args()

    if not args.screenshot.is_file():
        parser.error(f"not a file: {args.screenshot}")

    adapter_class, agent_classes = SOURCES[args.source]
    adapter = adapter_class(GeminiProvider())
    parcel = ParcelIdentifier(
        survey_no=args.survey_no,
        village=args.village,
        mandal=args.mandal,
        district=args.district,
    )

    print(f"{BOLD}Source:{OFF} {adapter.source_name}")
    print(f"{BOLD}File:{OFF}   {args.screenshot}")
    print(f"{DIM}Calling the model...{OFF}")

    started = datetime.now(timezone.utc)
    try:
        record = adapter.parse(args.screenshot)
    except Exception as e:  # noqa: BLE001 - this is a diagnostic tool
        print(f"\n{RED}EXTRACTION FAILED:{OFF} {type(e).__name__}")
        print(str(e)[:2000])
        print(
            f"\n{YELLOW}If this is a ValidationError, the model could not produce a record "
            f"matching the schema even after retries.{OFF}"
        )
        return 1
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    total = len(record.model_dump())
    nulls = _show_record(record)
    _show_entry_sample(record)
    print(f"\n{DIM}{total - nulls}/{total} fields populated in {elapsed:.1f}s{OFF}")

    if nulls == total:
        print(
            f"\n{RED}Every field came back null.{OFF} That usually means the schema's "
            "field names don't match this page, or the screenshot is unreadable -- "
            "not that the model failed. Compare the field names above against what "
            "the page actually shows."
        )

    if args.source == "kuda":
        crosses = getattr(record, "road_widening_affects_parcel", None)
        verdict = {
            True: f"{RED}YES -- a road crosses/borders the parcel (setback risk){OFF}",
            False: f"{GREEN}NO -- no road crossing the parcel{OFF}",
            None: f"{YELLOW}COULD NOT TELL{OFF}",
        }[crosses]
        print(f"\n{BOLD}ROAD CROSSES PARCEL:{OFF} {verdict}")
        print(
            f"{DIM}This is the project's flagship check. Verify it against the "
            f"screenshot by eye -- a wrong answer here is worse than no answer.{OFF}"
        )

    _show_findings(agent_classes, parcel, args.screenshot, record)

    print(
        f"\n{BOLD}Now check by eye:{OFF} does every non-null value above actually "
        "match the screenshot? Report back anything that is wrong, or any field the "
        "page shows that isn't listed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
