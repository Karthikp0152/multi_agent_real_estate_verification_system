"""KudaMasterPlanAdapter: manual-mode adapter for KUDA master-plan zoning data.

kuda.in itself blocks automated access (confirmed: it 403s even a plain
read-only fetch), and its site offers no documented per-parcel lookup
anyway. The actual source is 1acre.in, a third-party commercial site that
overlays the KUDA Master Plan 2041 on an interactive map:
  https://1acre.in/map-layers/telangana/warangal-masterplan
  https://1acre.in/map-layers/telangana/survey-numbers-telangana

That distinction matters for provenance (architecture principle 3): this
is a third-party visualization of the government master plan, not the
master plan itself. If 1acre's overlay is stale or mis-plotted, that's a
third-party error, not a KUDA one -- so source_name says so explicitly,
and any agent consuming this should weight its confidence accordingly
rather than treating it as an official record.

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
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.zoning import KudaMasterPlanExtract

_PARSE_CONTEXT = """\
This is a screenshot of the KUDA Master Plan 2041 for Warangal, shown on
1acre.in's interactive map overlay. The parcel of interest is the one the
user centred/zoomed on.

Read off, where legible:
- the land-use zone the parcel sits in (the legend names zones like
  Residential, Commercial, Mixed Use, Industrial, Agriculture, Public and
  Semi-Public, Village Expansion, Growth Corridor 1/2)
- whether a proposed road (Outer Ring Road, Arterial, Sub-Arterial,
  Collector) passes THROUGH or along the parcel itself, which would force
  a setback surrender -- this is distinct from a road merely being nearby
- the width in metres of any such road, if labelled
- whether the parcel falls inside a lake/water-body (FTL) buffer
- whether the parcel is inside a Growth Corridor or Village Expansion Zone
- whether a major road upgrade is NEAR the parcel without crossing it
- whether an area marked "Approved Layout" is nearby

If you cannot tell whether a road crosses the parcel versus merely passes
nearby, leave both fields null rather than choosing one."""


class KudaMasterPlanAdapter(ManualAdapter[ParcelIdentifier, KudaMasterPlanExtract]):
    """Manual-mode adapter: KUDA Master Plan 2041, via 1acre.in's overlay.

    PROVISIONAL -- see module docstring. The lookup workflow and URLs are
    confirmed real. `parse()` reads the screenshot with a vision model;
    its field names have not yet been validated against a real sample.
    """

    source_name = "1acre.in (third-party overlay of KUDA Master Plan 2041)"

    def __init__(self, provider: LLMProvider | None = None) -> None:
        """
        Args:
            provider: Model backend used to read the screenshot. Optional
                so instructions() -- which needs no model at all -- works
                without one; parse() raises if it was never supplied.
        """
        self.provider = provider

    def instructions(self, parcel: ParcelIdentifier) -> str:
        return (
            "1. Go to https://1acre.in/map-layers/telangana/warangal-masterplan "
            "and sign in with your mobile number if prompted.\n"
            "2. Use the survey-number layer "
            "(https://1acre.in/map-layers/telangana/survey-numbers-telangana) "
            f"or navigate manually to locate survey no. {parcel.survey_no}, "
            f"{parcel.village} village, {parcel.mandal} mandal, "
            f"{parcel.district} district.\n"
            "3. Zoom in until the parcel and its surrounding zone "
            "colors/road lines are clearly visible.\n"
            "4. Also note, using the map's legend: is the parcel inside a "
            "\"Growth Corridor 1/2\" or \"Village Expansion Zone\"? Is a "
            "proposed Outer Ring Road / Arterial Road upgrade nearby "
            "(without directly crossing the parcel)? Is it near an area "
            "marked \"Approved Layout\"? This same screenshot is used for "
            "both the setback/zoning check and the growth-potential check "
            "-- no separate lookup needed.\n"
            "5. Take a screenshot showing the parcel and its surrounding "
            "zone/road markings, and upload it here."
        )

    def parse(self, upload_path: Path) -> KudaMasterPlanExtract:
        if self.provider is None:
            raise RuntimeError(
                "KudaMasterPlanAdapter.parse() needs an LLMProvider; "
                "construct it as KudaMasterPlanAdapter(provider)."
            )
        extract = extract_structured(
            self.provider, upload_path, KudaMasterPlanExtract, _PARSE_CONTEXT
        )
        assert isinstance(extract, KudaMasterPlanExtract)
        return extract
