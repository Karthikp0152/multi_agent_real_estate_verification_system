"""LandRecordsAdapter: manual-mode adapter for Bhu Bharati land status lookups.

Real government portal, confirmed via search (not guessed):
  https://bhubharati.telangana.gov.in/knowLandStatus

Bhu Bharati is the platform that replaced Dharani -- exactly the
migration CLAUDE.md's own problem statement calls out as a source of
messy records. Searched by District -> Mandal -> Village -> Survey
Number, which maps directly onto ParcelIdentifier (unlike litigation,
which needed ManualAdapter to become generic over the query type at all).

A direct fetch to verify page details (captcha/OTP specifics) was
refused by the domain -- same as kuda.in. That doesn't change the design:
every source starts in manual mode regardless (see adapters/base.py), so
this stays manual whether or not a captcha turns out to be present.

STILL UNVALIDATED AGAINST A REAL SAMPLE: parse() is implemented -- a
vision model reads the upload into the schema below, and anything it
cannot read stays null, which becomes a "not_verified" finding rather
than a guess. What is NOT yet confirmed is whether these field names
match what the real page actually shows. Until someone runs this against
a genuine screenshot, treat the field set as a best-effort guess that
may need correcting, not as verified.
"""

from pathlib import Path

from bhoomicheck.adapters.base import ManualAdapter
from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.land_records import LandRecordExtract
from bhoomicheck.schemas.parcel import ParcelIdentifier

_PARSE_CONTEXT = """\
This is a screenshot of a "Know Land Status" result from Bhu Bharati,
Telangana's land-records portal (the system that replaced Dharani).

Read off, where legible:
- the pattadar (owner) name or names, exactly as printed; there may be
  several for one survey number
- the pattadar passbook number
- the extent/area, copied verbatim with its units (acres-guntas, sq yds)
- the land type/classification as the portal words it (e.g. patta land,
  assigned land, government land)
- whether the record flags this parcel as prohibited or assigned land
  (the Section 22-A prohibited-lands register). Set prohibited_status
  true only if the screen says it IS prohibited/assigned, false only if
  it explicitly says it is not, and null if the screen does not address
  it at all. Absence of a flag on screen is not proof of absence."""


class LandRecordsAdapter(ManualAdapter[ParcelIdentifier, LandRecordExtract]):
    """Manual-mode adapter: Bhu Bharati "Know Land Status" lookup.

    `instructions()` points at the real, confirmed portal URL. `parse()`
    reads the screenshot with a vision model; its field names have not
    yet been validated against a real sample.
    """

    source_name = "Bhu Bharati (Telangana Integrated Land Records Management System)"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """
        Args:
            provider: Model backend used to read the screenshot. Optional
                so instructions() works without one; parse() raises if it
                was never supplied.
        """
        self.provider = provider

    def instructions(self, parcel: ParcelIdentifier) -> str:
        return (
            "1. Go to https://bhubharati.telangana.gov.in/knowLandStatus\n"
            f"2. Select district {parcel.district}, mandal {parcel.mandal}, "
            f"village {parcel.village}.\n"
            f"3. Enter survey no. {parcel.survey_no} and fetch the land "
            "status.\n"
            "4. Note the pattadar name(s), extent, land type, and "
            "especially whether the parcel is flagged as prohibited/"
            "assigned land -- this is the single most important field on "
            "this source.\n"
            "5. Take a screenshot of the full result and upload it here."
        )

    def parse(self, upload_path: Path) -> LandRecordExtract:
        if self.provider is None:
            raise RuntimeError(
                "LandRecordsAdapter.parse() needs an LLMProvider; "
                "construct it as LandRecordsAdapter(provider)."
            )
        extract = extract_structured(
            self.provider, upload_path, LandRecordExtract, _PARSE_CONTEXT
        )
        assert isinstance(extract, LandRecordExtract)
        return extract
