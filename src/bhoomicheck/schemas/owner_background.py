"""Owner Background schemas: raw IGRS extract and the agent's report.

Same placement reasoning as litigation.py/land_records.py/zoning.py:
schemas stay the dependency-free foundation, so the raw query and extract
types live here, and adapters/owner_background.py imports them rather
than defining them locally.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from bhoomicheck.schemas.report import BaseAgentReport


class OwnerBackgroundQuery(BaseModel):
    """The seller/owner being checked, and any known Power-of-Attorney lead.

    Attributes:
        seller_name: The name being transacted with, as it appears on the
            document under review (e.g. a sale deed's executant, or a GPA
            holder's name if the sale is being conducted via GPA).
        poa_document_no: If the seller is acting under a Power of Attorney
            rather than as the titleholder themselves, its registration
            document number, if known (e.g. from a document the buyer
            was shown). Optional -- often not known until closer to
            closing.
    """

    seller_name: str
    poa_document_no: str | None = None


class EncumbranceEntry(BaseModel):
    """One mortgage or charge listed on an IGRS encumbrance search.

    A crucial limitation belongs with this type rather than buried
    elsewhere: an encumbrance certificate lists only *registered*
    documents. An equitable mortgage -- created by depositing title
    deeds with a lender, with no registered deed at all -- is legally
    valid and completely invisible here. So an empty encumbrance list is
    evidence of "nothing registered", never evidence of "no mortgage",
    and the agent must never report it as the latter.

    Attributes:
        document_no: The registered document's number, as printed.
        registered_on: Date of registration, if shown.
        nature: What the entry is, in the portal's own words (e.g.
            "mortgage", "sale deed", "gift") -- vocabulary varies, so
            free text rather than a Literal.
        parties: Names shown against the entry, copied verbatim.
        amount: Any sum shown, as printed, including its currency marker.
        discharged: Whether the listing shows this charge as discharged
            or released. Null when the page does not say -- which is the
            common case, and materially different from "not discharged".
    """

    document_no: str | None = None
    registered_on: date | None = None
    nature: str | None = None
    parties: list[str] = []
    amount: str | None = None
    discharged: bool | None = None


class OwnerBackgroundExtract(BaseModel):
    """Raw fields expected off an IGRS Telangana registered-document lookup.

    PROVISIONAL -- see adapters/owner_background.py module docstring.
    Every field is optional because nobody has supplied a real sample
    yet; field names are a best-effort guess at what the portal shows.

    Attributes:
        is_poa_sale: Whether the transaction is being conducted via a
            Power of Attorney rather than a direct sale by the recorded
            titleholder. This alone is a known risk pattern: *Suraj Lamp
            & Industries v. State of Haryana* (Supreme Court, 2011) held
            that GPA/Agreement-to-Sell/Will transactions do not validly
            transfer title -- only a registered sale deed does.
        poa_registered: Whether a genuinely registered Power of Attorney
            matching poa_document_no was found on IGRS. An unregistered
            or unfindable POA is a strong fraud signal, not just a
            paperwork gap.
        poa_execution_date: Date the POA was executed, if found.
        poa_status: Free text as shown on the portal (e.g. "active",
            "revoked") -- exact vocabulary is unconfirmed, so not a
            Literal yet.
        seller_name_matches_title: Whether the searched seller name
            matches the name(s) appearing in IGRS's registered document
            chain for this property. A mismatch is a serious red flag
            (possible impersonation), separate from whether a POA is
            involved at all.
        encumbrances_found: Registered mortgages/charges visible in the
            encumbrance chain. True means at least one is listed, false
            means the search ran and showed none, null means the
            screenshot does not establish either way. See
            EncumbranceEntry for why "none listed" is still not "no
            mortgage".
        encumbrances: One entry per mortgage/charge listed.
        notes: Anything else shown that doesn't fit a structured field.
    """

    is_poa_sale: bool | None = None
    poa_registered: bool | None = None
    poa_execution_date: date | None = None
    poa_status: str | None = None
    seller_name_matches_title: bool | None = None
    encumbrances_found: bool | None = None
    encumbrances: list[EncumbranceEntry] = []
    notes: str | None = None


class OwnerBackgroundReport(BaseAgentReport):
    """Full output contract of the Owner Background Agent.

    Attributes:
        agent_name: Pinned to "owner_background" -- a report claiming
            this shape under another agent's name is a validation error.
        extracts: One entry per IGRS lookup examined. At least one is
            required: this agent only runs when a lookup was actually
            performed, so an empty list means a malfunction, not a clean
            result.
    """

    agent_name: Literal["owner_background"] = "owner_background"
    extracts: list[OwnerBackgroundExtract] = Field(min_length=1)
