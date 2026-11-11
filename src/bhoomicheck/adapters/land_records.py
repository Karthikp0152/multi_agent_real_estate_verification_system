"""LandRecordsAdapter: manual-mode adapter for Bhu Bharati land status lookups.

Real government portal, confirmed via search (not guessed):
  https://bhubharati.telangana.gov.in/knowLandStatus

Bhu Bharati is the platform that replaced Dharani -- exactly the
migration CLAUDE.md's own problem statement calls out as a source of
messy records. Searched by District -> Mandal -> Village -> Survey
Number, which maps directly onto ParcelIdentifier (unlike litigation,
which needed ManualAdapter to become generic over the query type at all).

The search form is captcha-gated -- confirmed by eye against the live
page, after a direct fetch to check was refused by the domain. That puts
this squarely under CLAUDE.md's hard guardrail: we never automate around
a captcha, so this source is manual-only, permanently, not just until
someone gets around to writing a scraper.

The form itself: a "Search By" choice of Survey No./Sub-Division No. or
Pattadar Passbook Number, then District -> Mandal -> Village dropdowns,
then the captcha, then Fetch.

VALIDATED AGAINST A REAL SAMPLE (Warangal / Wardhannapet / Chennaram,
survey 16/D). A vision model reads the upload into the schema below,
and anything it cannot read stays null, which becomes a "not_verified"
finding rather than a guess. On that sample 13 of 15 fields were read
correctly and both nulls were correct -- so the field names below are
the page's own labels, not a guess. See schemas/land_records.py for the
three schema corrections this validation produced.
"""

from pathlib import Path

from bhoomicheck.adapters.base import ManualAdapter
from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.land_records import LandRecordExtract
from bhoomicheck.schemas.parcel import ParcelIdentifier

_PARSE_CONTEXT = """\
This is a Bhu Bharati "Land Details Search" result page (Telangana's
land-records portal, successor to Dharani). The field labels below are
the page's own wording, confirmed against a real result -- match on
these labels exactly rather than inferring.

Pattadar Details section:
- owner_names: "Pattadar / Authorised Person Name". Often Telugu script,
  sometimes "<telugu> / <telugu>". Copy verbatim, do not transliterate
  or translate.
- father_or_husband_name: "Father / Husband's Name", copied verbatim.
- khata_no: "Khata Number"
- pattadar_passbook_no: "PPB Number" -- usually masked like
  "T22150******". Copy including the asterisks; do not reconstruct it.
- ekyc_status: "eKYC Status"

Land Details section -- these three are SEPARATE fields, do not merge
them or copy one into another:
- nature_of_land: "Nature of Land" (e.g. "Patta")
- classification_of_land: "Classification of Land" (e.g.
  "Magani/ Thari - Ek Fasla")
- land_type: "Land Type" (e.g. "Agriculture Lands")

Also from Land Details:
- extent: "Total Extent (Ac. Gts)", copied exactly as printed. These are
  acres-guntas, not decimal acres -- copy the string, do not convert.
- land_status: "Land Status" (e.g. "Digital Signed")
- market_value: "Market value of Survey no.(in INR)"
- transaction_status: "Transaction Status" -- may be blank or "--".

Footer:
- records_updated_as_on: "Records are updated as on - DD-MM-YYYY". This
  is when the REGISTRY was updated, not when the screenshot was taken.

- prohibited_status: this page does not normally show anything about the
  prohibited-lands (Section 22-A) register. Set it true or false ONLY if
  this particular page explicitly says so; otherwise leave it null.
  Do not infer it from "Nature of Land"."""


class LandRecordsAdapter(ManualAdapter[ParcelIdentifier, LandRecordExtract]):
    """Manual-mode adapter: Bhu Bharati "Know Land Status" lookup.

    `instructions()` points at the real, confirmed portal URL. `parse()`
    reads the screenshot with a vision model, using field labels taken
    from a real result page rather than guessed.
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
            f"3. Enter survey no. {parcel.survey_no}.\n"
            # The portal derives the khata from the preceding selections,
            # so it is not part of ParcelIdentifier. It is still called
            # out as a step, because more than one entry appearing means
            # the survey number is split across holdings -- and silently
            # picking the wrong one yields a confident report about
            # somebody else's land.
            "4. Select the Khata Number offered. Normally there is only "
            "one. If several are listed, this survey number is shared "
            "between pattadars -- pick the khata for the parcel you are "
            "actually buying, and say which one you chose.\n"
            "5. Fetch the land status.\n"
            "6. Note the pattadar name(s), extent, land type, and "
            "especially whether the parcel is flagged as prohibited/"
            "assigned land -- this is the single most important field on "
            "this source.\n"
            "7. Take a screenshot of the full result and upload it here."
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
