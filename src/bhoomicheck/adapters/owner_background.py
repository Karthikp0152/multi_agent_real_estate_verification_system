"""OwnerBackgroundAdapter: manual-mode adapter for IGRS Telangana lookups.

Real government portal, confirmed against the live site (not guessed):
  https://registration.telangana.gov.in/ (IGRS Telangana -- Registration
  and Stamps Department).

ACCESS, CONFIRMED BY EYE: the Citizen Service Dashboard is behind a
login (User Type + Mobile/Email + Password + captcha). Both the login
and the EC search itself are captcha-gated, so this source is
manual-only permanently, under CLAUDE.md's hard guardrail -- not until
somebody writes a scraper.

The dashboard offers ten services. Four bear on due diligence:
  - "Encumbrance Search(EC)"  -- the registered charge/mortgage chain
  - "Prohibited Property"     -- the Section 22-A register
  - "GPA Search"              -- registered Powers of Attorney
  - "Registered Document Details"

ENCUMBRANCE SEARCH IS PARTLY BROKEN, CONFIRMED BY EYE (Nov 2026). The
search form at
  https://tgigrs.telangana.gov.in/EncumbranceCertificate/Search_Document.htm
offers "By Document Number" and "By Property". Selecting "By Property"
does not change the form -- it still renders Document Number, Year of
Registration and Registered at SRO. So the survey-number path, which is
the one this project actually wants (due diligence starts from a survey
number, not a document number), is unusable at present.

That is a real constraint, not a temporary annoyance to code around: it
means an EC lookup currently requires already holding a document number,
which in practice comes off the sale deed the seller produces. The
instructions below say so rather than sending a user to a dead form.

Two further limitations the portal states about itself, which belong in
any finding built from it:
  - Online encumbrance covers transactions from 01-01-1983 only. Older
    charges need the SRO office in person.
  - The property search is explicitly "probabilistic ... may result in
    display of multiple results some of which may not be of any interest
    to the user". So a result set needs human selection and cannot be
    treated as an exact match on this parcel.

This agent exists specifically because of a well-established legal trap:
*Suraj Lamp & Industries v. State of Haryana* (Supreme Court, 2011) held
that Power-of-Attorney/Agreement-to-Sell/Will transactions do not validly
transfer title -- only a registered sale deed does. A seller transacting
via GPA, or a GPA that turns out to be unregistered or revoked, is a real
risk this project's problem statement is built to catch, not a
hypothetical.

Query is keyed by a person's name (like litigation), not a parcel -- see
adapters/base.py, which is why ManualAdapter is generic over the query
type at all.

PARTLY VALIDATED. The portal's navigation, access model and search
form have been confirmed against the live site. What is still NOT
validated is the *result* page: nobody has yet run an actual EC result
through parse(), so the extract field names below remain a best-effort
guess and may need correcting the way Bhu Bharati's did.
"""

from pathlib import Path

from bhoomicheck.adapters.base import ManualAdapter
from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.owner_background import OwnerBackgroundExtract, OwnerBackgroundQuery

_PARSE_CONTEXT = """\
This is a screenshot from IGRS Telangana (registration.telangana.gov.in),
the Registration and Stamps department portal -- either an Encumbrance
Search result showing a property's registered document chain, or a
Certified Copy lookup of one registered document.

Read off, where legible:
- is_poa_sale: whether the transaction is being conducted through a Power
  of Attorney / GPA rather than directly by the recorded titleholder
- poa_registered: whether a Power of Attorney actually appears as a
  REGISTERED document here. False means the portal was searched and the
  POA was not found; null means the screenshot does not establish either
  way. That distinction matters -- "not found" is a fraud signal,
  "not shown" is not.
- poa_document_no / poa_execution_date / poa_status: as printed
- seller_name_matches_title: whether the seller's name matches the name
  on the registered chain shown here

- encumbrances_found / encumbrances: if this is an Encumbrance Search
  result, list every registered mortgage or charge shown, copying the
  document number, registration date, the nature of the entry in the
  portal's own words, the party names, and any amount as printed. Set
  discharged true only if the listing explicitly shows the charge as
  discharged or released; null if it simply does not say. Set
  encumbrances_found false only if the search clearly ran and returned
  no entries -- null if the screenshot does not establish that.

Leave anything the screenshot does not clearly establish as null. Do not
reason about what is likely -- only report what is on screen."""


class OwnerBackgroundAdapter(ManualAdapter[OwnerBackgroundQuery, OwnerBackgroundExtract]):
    """Manual-mode adapter: IGRS Telangana registered-document lookup.

    `instructions()` points at the real, confirmed portal. `parse()`
    reads the screenshot with a vision model; its field names have not
    yet been validated against a real sample.
    """

    source_name = "IGRS Telangana (registration.telangana.gov.in)"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """
        Args:
            provider: Model backend used to read the screenshot. Optional
                so instructions() works without one; parse() raises if it
                was never supplied.
        """
        self.provider = provider

    def instructions(self, query: OwnerBackgroundQuery) -> str:
        """Steps for a human to run the IGRS lookups and upload results.

        Written against the live dashboard rather than from memory: the
        service names below are the portal's own, and the EC caveat
        reflects a form that is currently broken (see module docstring).
        """
        if query.poa_document_no:
            poa_step = (
                f'A Power of Attorney document number is already known '
                f'("{query.poa_document_no}"). Use "GPA Search" to pull it up '
                "and confirm: is it genuinely registered? What does it "
                "actually authorise? Any sign it has been revoked?"
            )
        else:
            poa_step = (
                'Use "GPA Search" to check whether any registered Power of '
                f'Attorney exists naming "{query.seller_name}". Do this even '
                "if nobody has mentioned a POA -- the absence of one is as "
                "useful to record as its presence, and a GPA-based sale the "
                "buyer was not told about is exactly the risk here."
            )

        return (
            "Sign in at https://registration.telangana.gov.in/ (Login -> "
            "User Type, Mobile/Email, Password, captcha). All of the "
            "following are on the Citizen Service Dashboard.\n"
            "\n"
            f"1. GPA Search. {poa_step}\n"
            "\n"
            '2. "Prohibited Property" (no login needed -- '
            "registration.telangana.gov.in/openProhibitedProperties.htm). "
            "Select district, mandal and village, then Fetch.\n"
            "   The result is NOT a yes/no for your survey number: it is a "
            "set of PDF lists for the whole village, in two columns "
            "(Agriculture and Non-Agriculture), one PDF per 22-A "
            "sub-clause -- (a), (b), (c)-Waqf, (c)-Endowment, (d), (e), and "
            '"Court cases & Others".\n'
            "   Download the PDFs for the column matching this parcel's "
            "land type, and upload them. Note which ones you took: a "
            "survey number absent from three of seven lists has not been "
            "cleared, and this check is only as good as its coverage.\n"
            "\n"
            '3. "Encumbrance Search(EC)". NOTE: the "By Property" option is '
            "currently broken -- selecting it leaves the Document Number "
            "form in place -- so a survey-number search is not possible "
            "right now. If you have a document number from the sale deed, "
            'use "By Document Number" (document number + year of '
            "registration + SRO). If you do not, record that the "
            "encumbrance check could not be performed; do NOT treat that "
            "as a clean result.\n"
            "   Two limits the portal states about itself, worth noting on "
            "whatever you find: online records start at 01-01-1983, and "
            "the property search is explicitly probabilistic, so several "
            "results may appear and only some will relate to this "
            "parcel.\n"
            "\n"
            '4. "Registered Document Details". Check whether the name on '
            "any registered sale deed for this property actually matches "
            f'"{query.seller_name}". A mismatch is a red flag independent '
            "of any POA question.\n"
            "\n"
            "5. Remember: per Suraj Lamp v. State of Haryana (Supreme "
            "Court), a GPA/Agreement-to-Sell/Will alone never transfers "
            "title -- only a registered sale deed does. If the chain "
            "relies on a GPA with no proper sale deed after it, that is "
            "itself the finding, not something to explain away.\n"
            "\n"
            "6. Screenshot every result page in full, including the ones "
            "that come back empty, and upload them here."
        )

    def parse(self, upload_path: Path) -> OwnerBackgroundExtract:
        if self.provider is None:
            raise RuntimeError(
                "OwnerBackgroundAdapter.parse() needs an LLMProvider; "
                "construct it as OwnerBackgroundAdapter(provider)."
            )
        extract = extract_structured(
            self.provider, upload_path, OwnerBackgroundExtract, _PARSE_CONTEXT
        )
        assert isinstance(extract, OwnerBackgroundExtract)
        return extract
