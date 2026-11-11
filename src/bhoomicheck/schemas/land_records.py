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
    """Fields off a Bhu Bharati "Land Details Search" result.

    Corrected against a real result page (Warangal / Wardhannapet /
    Chennaram, survey 16/D) rather than guessed. Three corrections came
    out of that, each worth knowing:

    1. The page carries THREE distinct land-classification fields --
       "Nature of Land", "Classification of Land" and "Land Type" -- not
       one. An earlier single `land_type` field forced the model to pick
       among them, and it picked "Patta" while the page's own "Land
       Type" said "Agriculture Lands". They are separate questions and
       are kept separate here.
    2. `prohibited_status` is NOT on this page at all. See its docstring
       below; the field is kept, but this source cannot fill it.
    3. Father/Husband's name IS on the page, and is the standard
       disambiguator for common Indian names -- directly useful for
       deciding whether an eCourts name match is the same person.

    Every field stays optional: a parcel's page may omit any of them,
    and a null becomes an honest "not verified" downstream.

    Attributes:
        owner_names: Pattadar name(s), from "Pattadar / Authorised
            Person Name". A list because joint pattadars are common.
            Often printed in Telugu script, sometimes with a
            transliteration -- copied verbatim either way.
        father_or_husband_name: From "Father / Husband's Name". Indian
            land records use this to tell apart people sharing a name,
            which is exactly the weakness in name-based court searches.
        khata_no: The khata (account) number, which is also one of the
            search inputs on this page.
        pattadar_passbook_no: From "PPB Number". Usually partially
            masked on screen (e.g. "T22150******"), so treat it as a
            display value, not a full identifier.
        ekyc_status: From "eKYC Status", e.g. "Aadhaar available & e-KYC
            completed" -- an identity-assurance signal about the record.
        extent: From "Total Extent (Ac. Gts)", copied verbatim. The
            units are acres-guntas, so "0.0200" is not a decimal acreage
            and must not be reasoned about as one.
        nature_of_land: From "Nature of Land", e.g. "Patta". This is the
            closest thing on this page to the assigned-land question:
            "Patta" is privately held, whereas assigned or government
            land carries transfer restrictions.
        classification_of_land: From "Classification of Land", e.g.
            "Magani/ Thari - Ek Fasla" -- irrigation/revenue
            classification.
        land_type: From "Land Type", e.g. "Agriculture Lands". Distinct
            from both fields above, and load-bearing: agricultural land
            is excluded from SARFAESI security interests, so a CERSAI
            mortgage search would likely be empty for such a parcel
            regardless of whether a mortgage exists.
        land_status: From "Land Status", e.g. "Digital Signed".
        market_value: From "Market value of Survey no.(in INR)", copied
            as shown. A government valuation, not a market price.
        transaction_status: From "Transaction Status". Blank on a parcel
            with nothing in progress; a value here may indicate a
            pending transaction on the land.
        prohibited_status: Whether the parcel sits in the prohibited-
            lands (Section 22-A) register.

            CONFIRMED ABSENT from this page. It is kept because the risk
            is real and named in CLAUDE.md's problem statement, but Bhu
            Bharati's land-details result does not answer it, so this
            stays null from this source and the matching finding stays
            "not_verified". Answering it needs a different source.
        records_updated_as_on: From "Records are updated as on - ...".
            Carried into Provenance, because when the registry was last
            updated matters as much as when we looked.
        notes: Anything else shown that doesn't fit a field above.
    """

    owner_names: list[str] = []
    father_or_husband_name: str | None = None
    khata_no: str | None = None
    pattadar_passbook_no: str | None = None
    ekyc_status: str | None = None
    extent: str | None = None
    nature_of_land: str | None = None
    classification_of_land: str | None = None
    land_type: str | None = None
    land_status: str | None = None
    market_value: str | None = None
    transaction_status: str | None = None
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
