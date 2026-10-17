"""Land Records Agent: Bhu Bharati extracts -> LandRecordsReport.

Deterministic, same reasoning as ZoningAgent/LitigationAgent: by the time
this runs, LandRecordsAdapter.parse() has already turned a screenshot
into a structured LandRecordExtract, so there's no free text left to
interpret -- just a fixed mapping from fields to Findings.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.land_records import LandRecordExtract, LandRecordsReport
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance

SOURCE_NAME = "Bhu Bharati (Telangana Integrated Land Records Management System)"

NOT_LEGIBLE_REASON = "not legible in the provided screenshot"


class LandRecordsAgent:
    """Builds a validated LandRecordsReport from one or more Bhu Bharati extracts."""

    def report(
        self,
        parcel: ParcelIdentifier,
        extracts: list[tuple[Path, LandRecordExtract]],
    ) -> LandRecordsReport:
        """Assemble a report from (uploaded screenshot, parsed extract) pairs.

        Args:
            parcel: The parcel this land-records check is for.
            extracts: One entry per screenshot examined -- the file the
                human uploaded (for provenance) paired with what
                LandRecordsAdapter.parse() produced from it. The filename
                is a system-known fact fed in by the caller, same as
                every other deterministic agent -- never guessed here.
        """
        fetched_at = datetime.now(timezone.utc).date()
        findings = [
            finding
            for upload_path, extract in extracts
            for finding in self._findings_for(upload_path, extract, fetched_at)
        ]
        return LandRecordsReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            extracts=[extract for _, extract in extracts],
            findings=findings,
        )

    def _findings_for(
        self, upload_path: Path, extract: LandRecordExtract, fetched_at: date
    ) -> list[Finding]:
        provenance = Provenance(
            source_name=SOURCE_NAME,
            source_type="manual",
            document=upload_path.name,
            fetched_at=fetched_at,
            # The portal states its own data vintage; carrying it through
            # means a reader can tell a fresh record from a stale one,
            # rather than only seeing when we happened to look.
            record_updated_at=extract.records_updated_as_on,
        )
        return [
            self._ownership_finding(extract, provenance),
            self._land_type_finding(extract, provenance),
            self._prohibited_status_finding(extract, provenance),
        ]

    def _ownership_finding(
        self, extract: LandRecordExtract, provenance: Provenance
    ) -> Finding:
        if not extract.owner_names:
            return Finding(
                check_id="land_records.ownership",
                claim="parcel's pattadar name(s) per Bhu Bharati",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        names = ", ".join(extract.owner_names)
        passbook = (
            f" (passbook no. {extract.pattadar_passbook_no})"
            if extract.pattadar_passbook_no
            else ""
        )
        return Finding(
            check_id="land_records.ownership",
            claim=f"Bhu Bharati lists {names} as pattadar(s) for this parcel{passbook}",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(
                level="medium",
                reason="Telangana records migrated from Dharani to Bhu Bharati "
                "are often messy -- treat as a starting point, not final proof of ownership",
            ),
        )

    def _land_type_finding(
        self, extract: LandRecordExtract, provenance: Provenance
    ) -> Finding:
        if extract.land_type is None:
            return Finding(
                check_id="land_records.land_type",
                claim="parcel's land type/classification per Bhu Bharati",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        extent = f", extent {extract.extent}" if extract.extent else ""
        return Finding(
            check_id="land_records.land_type",
            claim=f'parcel is recorded as "{extract.land_type}"{extent} per Bhu Bharati',
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason="as displayed by the portal"),
        )

    def _prohibited_status_finding(
        self, extract: LandRecordExtract, provenance: Provenance
    ) -> Finding:
        if extract.prohibited_status is None:
            return Finding(
                check_id="land_records.prohibited_land",
                claim="whether this parcel is flagged in the prohibited-lands (22-A) register",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if extract.prohibited_status:
            return Finding(
                check_id="land_records.prohibited_land",
                claim="parcel is flagged in the prohibited-lands (22-A) register",
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(
                    level="medium",
                    reason="as displayed by the portal -- confirm with the local "
                    "Tahsildar before relying on this for a purchase decision",
                ),
            )
        return Finding(
            check_id="land_records.prohibited_land",
            claim="parcel is not flagged in the prohibited-lands (22-A) register",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason="as displayed by the portal"),
        )
