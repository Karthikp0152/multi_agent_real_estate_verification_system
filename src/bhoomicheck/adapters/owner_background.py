"""OwnerBackgroundAdapter: manual-mode adapter for IGRS Telangana lookups.

Real government portal, confirmed via search (not guessed):
  https://registration.telangana.gov.in/ (IGRS Telangana -- Registration
  and Stamps Department). Online Services offers "Certified Copies" (pull
  up a specific registered document by its number, including a Power of
  Attorney) and "Encumbrance Search" (search by property details to see
  the registered document chain for a survey number).

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
        poa_step = (
            f'2. Since a Power of Attorney document number is known ("{query.poa_document_no}"), '
            "use Online Services -> Certified Copies, enter that document "
            "number, and confirm: is it a genuinely registered document? "
            "What does it actually authorize? Is there any indication "
            "it's been revoked?\n"
            if query.poa_document_no
            else "2. Ask whether this transaction involves a Power of "
            "Attorney at all -- if so, get its registration document "
            "number and repeat this lookup with it.\n"
        )
        return (
            "https://registration.telangana.gov.in/\n"
            f'1. Search for registered documents naming "{query.seller_name}" '
            "as a party, using Online Services -> Encumbrance Search "
            "(by property details) or Certified Copies (by document "
            "number, if known).\n"
            f"{poa_step}"
            "3. Check whether the name on any registered sale deed for "
            f'this property actually matches "{query.seller_name}" -- a '
            "mismatch is a red flag independent of any POA question.\n"
            "4. Remember: per Suraj Lamp v. State of Haryana (Supreme "
            "Court), a GPA/Agreement-to-Sell/Will alone never transfers "
            "title -- only a registered sale deed does. If the chain "
            "relies on a GPA with no proper sale deed after it, that is "
            "itself the finding, not something to explain away.\n"
            "5. Take a screenshot of every result and upload it here."
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
