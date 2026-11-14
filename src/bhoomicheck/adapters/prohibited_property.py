"""ProhibitedPropertyAdapter: the Section 22-A register, per village.

Real government page, confirmed by eye against the live site:
  https://registration.telangana.gov.in/openProhibitedProperties.htm

Public -- no login, unlike the rest of the IGRS citizen dashboard.
Select District -> Mandal -> Village/Town, press Fetch, and the result
is NOT an answer about your parcel. It is a set of PDF lists for the
whole village: two columns (Agriculture and Non-Agriculture), seven
sub-clause lists each, published as FORM-I.

WHY THESE ARE READ WITH VISION, NOT TEXT SEARCH. Measured on the real
Chennaram 22A(1)(a) Agriculture list: 15 pages, 15 JPEG page scans, one
font, with an OCR layer of very poor quality laid over them. In that
layer the mandal name appeared as seven different strings --
WARDHANNAPET, WARDHANNAPEI, WARDHANNAPFT, WARDHANNAPTT, "!?ARDHANNAPEI",
"/ARDHANNAPEI", "lVARDHANNAPET" -- and "TG" appeared as "T6" as often as
"TG". Searching that text for a survey number and finding nothing would
be indistinguishable from the OCR having mangled it, and the tool would
report a listed parcel as clean. That is the exact false-clean this
project exists to prevent, so the scans are transcribed instead.

VALIDATED AGAINST A REAL LIST. Run against the Chennaram 22A(1)(a)
Agriculture list (15 scanned pages): 372 rows transcribed, matching the
document's own final Sl.No., with total_extent reading "232.16" exactly
as the form's footer prints it, and nothing flagged unreadable. Rows
spot-checked across the range -- 8/1, 10/A/3, 49/53/B/2, 254/110/E,
279A, 464 -- each appeared exactly once. Truncation is the failure mode
that would matter most here (a model that stops at row 40 reports a
listed parcel as absent), and it did not occur.

The form's columns, confirmed against a real list rather than guessed:
Sl.No | District | Mandal | Village/Town | Sy. No. & Sub-Division No. |
Plot No | Town Survey No. | House No. | Name of the Assignee / Allotee /
Owner | Extent | Act under which prohibited.
"""

from pathlib import Path

from bhoomicheck.adapters.base import ManualAdapter
from bhoomicheck.llm.extraction import extract_structured
from bhoomicheck.llm.provider import LLMProvider
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.prohibited import ProhibitedListExtract

_PARSE_CONTEXT = """\
This is a Section 22-A prohibition record from the Telangana
Registration & Stamps Department. It comes in one of three shapes, and
which one you are looking at matters more than anything else here:

A. A scanned proforma table listing prohibited properties in one
   village. Set availability "published". The proforma differs by
   sub-clause and the heading says which:
     FORM-I   -> 22 A (1) (a)   columns incl. "Act under which prohibited"
     FORM-II  -> 22 A (1) (b)   columns incl. "State Govt. / Central Govt."
     FORM-V   -> 22 A (1) (d)   columns incl. "Act under which declared
                                 surplus UL (C&R)/LR (COAH)" and "C.C. No."
     FORM-VI  -> 22 A (1) (e)   columns incl. "Reasons to include in POB"
   Read the sub-clause from the heading text ("under section 22-A(1)(d)
   ..."), not from the form number alone. Some of these documents open
   with a Government Order (G.O.Ms.No...) before the table -- the table
   is what you transcribe, but note the G.O. number and date in
   order_reference if the rows do not carry their own.

   A TABLE WHOSE ONLY DATA ROW READS "Nil" OR "NIL" IS AN EMPTY
   REGISTER. Set availability "empty" and return NO entries. Do not
   emit a row with a null survey number: that would look like a line
   this system failed to read, when in fact the government is stating
   there is nothing to read.
B. A live results table headed "Prohibited Properties (Court cases &
   Others)". Set availability "published" if it lists properties, or
   "empty" if it says "No property is found to be prohibited in this
   village with given details". An empty register is a real answer, not
   a failure -- do not report it as unreadable.
C. A notice reading "Prohibition data is under review. it will be
   published soon...". Set availability "under_review", leave entries
   empty, and still fill sub_clause if the page says which register it
   is. This means the department has not published that register at
   all, which is reported very differently from a list nobody fetched.

Transcribe EVERY data row you can see, across every page. Do not
summarise, do not stop early, and do not skip rows that look like
repeats -- adjacent rows genuinely do repeat owner names for different
sub-divisions.

Heading fields:
- sub_clause: from the heading, exactly one of: "22 A (1) (a)",
  "22 A (1) (b)", "22 A (1) (c)- Waqf", "22 A (1) (c)- Endowment",
  "22 A (1) (d)", "22 A (1) (e)", "Court cases & Others". The heading
  usually reads like "...under Section 22-A (1) (a) of the Registration
  Act, 1908 for prohibition". If you cannot read which one it is, use
  null -- do not guess, because a mis-labelled list would be credited
  against the wrong sub-clause and overstate how much was checked.
- land_category: "Agriculture" or "Non-Agriculture", from the heading
  (e.g. "(AGRICULTURE LANDS)").
- district, mandal, village: as printed in the row columns.
- total_extent: the "TOTAL EXTENT:" figure in the footer, if present.

Each row -> one entry:
- survey_no: on a FORM-I scan this is the single column "Sy. No. &
  Sub-Division No. (if any)" -- copy EXACTLY as printed, keeping
  slashes and letters: "49/53/B/1", "38/A/2", "279A", "10/A/3".
  On the "Court cases & Others" table the same information is SPLIT
  across "Survey No." and "Sub Sy No.". Join them with a slash into
  survey_no (survey "16" + sub "D" -> "16/D"), and also keep the
  sub-division in sub_survey_no. If there is no sub-division, survey_no
  is just the survey number.
  This is the only field used to decide whether a parcel is prohibited,
  so transcription accuracy here matters more than anywhere else in
  this system. If a row's survey number is unreadable, still emit the
  row with survey_no null rather than dropping it, and say so in notes
  -- a dropped row is an invisible gap.
- sub_survey_no, revenue_survey_no, plot_no: the "Sub Sy No.",
  "Rev Sy No." / "Rev Sub Sy No." and "Plot No." columns, where the
  source has them. Leave null on a FORM-I scan that does not.
- act: whichever column states the legal basis -- "Act under which
  prohibited" (FORM-I), "Act under which declared surplus UL
  (C&R)/LR (COAH)" (FORM-V), or "State Govt. / Central Govt."
  (FORM-II).
- reason: the "Reason" column on the court-cases table, or "Reasons to
  include in POB" on FORM-VI.
- order_reference: whichever column identifies the instrument behind
  the entry -- "Letter/Memo/Order/Notification No." and date on the
  court-cases table, "C.C. No." on FORM-V, or the G.O. number and date
  from a covering Government Order.
- owner_name: column "Name of the Assignee / Allotee / Owner".
- extent: column "Extent", exactly as printed (acres-guntas).
- act: column "Act under which prohibited", e.g. "TG Assigned Lands
  (Prohibition of Transfers) Act, 1977". Note the leading token is
  "TG"; poor scans can make it look like "T6" or "TC".

In notes, record anything that limits what you read: pages you could
not read, columns cut off, or rows you were unsure of."""


class ProhibitedPropertyAdapter(ManualAdapter[ParcelIdentifier, ProhibitedListExtract]):
    """Manual-mode adapter for the per-village 22-A prohibition lists."""

    source_name = "IGRS Telangana - Section 22-A prohibited property register"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """
        Args:
            provider: Model backend used to transcribe the scans.
                Optional so instructions() works without one.
        """
        self.provider = provider

    def instructions(self, parcel: ParcelIdentifier) -> str:
        return (
            "1. Go to https://registration.telangana.gov.in/"
            "openProhibitedProperties.htm -- this one needs no login.\n"
            f"2. Select district {parcel.district}, mandal {parcel.mandal}, "
            f"village {parcel.village}, then press Fetch.\n"
            "3. You will get two columns of PDF links, Agriculture and "
            "Non-Agriculture, with seven lists each: 22 A (1) (a), (b), "
            "(c)- Waqf, (c)- Endowment, (d), (e), and Court cases & "
            "Others.\n"
            "4. Open ALL SEVEN from the column matching this parcel's "
            "land type, and capture whatever each one gives you.\n"
            "   They do not all behave the same way, and all three "
            "outcomes are worth capturing:\n"
            "     - a PDF list: download it;\n"
            '     - a results table (usually "Court cases & Others"): '
            "screenshot it, including when it says no property is "
            "prohibited -- an empty register is a real answer;\n"
            '     - a notice saying "Prohibition data is under review": '
            "screenshot that too. It records that the department has "
            "not published that register, which is reported as the "
            "department's gap rather than yours.\n"
            "   Seven, not one: these are separate registers, and a survey "
            "number absent from three of them has not been cleared. "
            "Whatever you leave out is reported as unchecked, not as "
            "clean, so a partial upload weakens the result rather than "
            "breaking it.\n"
            "   If you are unsure of the land type, take both columns.\n"
            f"5. The parcel being checked is survey no. {parcel.survey_no}. "
            "You do not need to search the PDFs yourself -- upload them "
            "whole and the matching is done here."
        )

    def parse(self, upload_path: Path) -> ProhibitedListExtract:
        if self.provider is None:
            raise RuntimeError(
                "ProhibitedPropertyAdapter.parse() needs an LLMProvider; "
                "construct it as ProhibitedPropertyAdapter(provider)."
            )
        extract = extract_structured(
            self.provider, upload_path, ProhibitedListExtract, _PARSE_CONTEXT
        )
        assert isinstance(extract, ProhibitedListExtract)
        return extract
