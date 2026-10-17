"""Land Records schemas: raw Bhu Bharati extract and the agent's report.

Same placement reasoning as zoning.py/litigation.py: schemas stay the
dependency-free foundation, so the raw extract type lives here, and
adapters/land_records.py imports it rather than defining it locally.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from bhoomicheck.schemas.report import BaseAgentReport


class LandRecordExtract(BaseModel):
    """Raw fields expected off a Bhu Bharati "Know Land Status" result.

    PROVISIONAL -- see adapters/land_records.py module docstring. Every
    field is optional because nobody has supplied a real sample screenshot
    yet; field names/vocabulary are a best-effort guess from public
    knowledge of what the portal shows, not read off a real result.

    Attributes:
        owner_names: Pattadar name(s) on record. A list because Telangana
            records commonly show joint/multiple pattadars for one survey
            number.
        pattadar_passbook_no: The passbook number, if shown.
        extent: Land area as displayed (units vary -- acres-guntas, sq.
            yards -- so this stays free text rather than a parsed float).
        land_type: e.g. "patta land", "assigned land", "government land"
            -- exact vocabulary Bhu Bharati uses is unconfirmed, so this
            stays free text rather than a Literal for now.
        prohibited_status: Whether the parcel is flagged in the
            prohibited-lands (Section 22-A) register -- this is the
            central "assigned/prohibited land" risk from CLAUDE.md's
            problem statement.
        records_updated_as_on: The vintage the portal states for its own
            data ("Records are updated as on - ..."). Carried into the
            finding's Provenance, because when the registry was last
            updated matters as much as when we looked: a lookup today
            against a record untouched for years is weak evidence.
        notes: Anything else shown that doesn't fit a structured field.
    """

    owner_names: list[str] = []
    pattadar_passbook_no: str | None = None
    extent: str | None = None
    land_type: str | None = None
    prohibited_status: bool | None = None
    records_updated_as_on: date | None = None
    notes: str | None = None


class LandRecordsReport(BaseAgentReport):
    """Full output contract of the Land Records Agent.

    Attributes:
        agent_name: Pinned to "land_records" -- a report claiming this
            shape under another agent's name is a validation error.
        extracts: One entry per Bhu Bharati lookup examined. At least one
            is required: this agent only runs when a lookup was actually
            performed, so an empty list means a malfunction, not a clean
            result.
    """

    agent_name: Literal["land_records"] = "land_records"
    extracts: list[LandRecordExtract] = Field(min_length=1)
