"""Owner Background Agent: IGRS extracts -> OwnerBackgroundReport.

Deterministic, same reasoning as the other specialist agents built after
DocumentAnalysisAgent: by the time this runs, OwnerBackgroundAdapter.
parse() has already turned a screenshot into a structured
OwnerBackgroundExtract, so there's no free text left to interpret -- just
a fixed mapping from fields to Findings.
"""

from datetime import date, datetime, timezone
from pathlib import Path

from bhoomicheck.schemas.confidence import Confidence
from bhoomicheck.schemas.finding import Finding
from bhoomicheck.schemas.owner_background import OwnerBackgroundExtract, OwnerBackgroundReport
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.provenance import Provenance

SOURCE_NAME = "IGRS Telangana (registration.telangana.gov.in)"

NOT_LEGIBLE_REASON = "not legible in the provided screenshot"

POA_SALE_REASON = (
    "a Power-of-Attorney-based transaction is a known risk pattern -- per "
    "Suraj Lamp & Industries v. State of Haryana (Supreme Court, 2011), a "
    "GPA/Agreement-to-Sell/Will alone does not validly transfer title; "
    "confirm a proper registered sale deed exists before relying on this"
)


class OwnerBackgroundAgent:
    """Builds a validated OwnerBackgroundReport from one or more IGRS extracts."""

    def report(
        self,
        parcel: ParcelIdentifier,
        extracts: list[tuple[Path, OwnerBackgroundExtract]],
    ) -> OwnerBackgroundReport:
        """Assemble a report from (uploaded screenshot, parsed extract) pairs.

        Args:
            parcel: The parcel this owner-background check is for.
            extracts: One entry per screenshot examined -- the file the
                human uploaded (for provenance) paired with what
                OwnerBackgroundAdapter.parse() produced from it. The
                filename is a system-known fact fed in by the caller,
                never guessed here.
        """
        fetched_at = datetime.now(timezone.utc).date()
        findings = [
            finding
            for upload_path, extract in extracts
            for finding in self._findings_for(upload_path, extract, fetched_at)
        ]
        return OwnerBackgroundReport(
            parcel=parcel,
            generated_at=datetime.now(timezone.utc),
            extracts=[extract for _, extract in extracts],
            findings=findings,
        )

    def _findings_for(
        self, upload_path: Path, extract: OwnerBackgroundExtract, fetched_at: date
    ) -> list[Finding]:
        provenance = Provenance(
            source_name=SOURCE_NAME,
            source_type="manual",
            document=upload_path.name,
            fetched_at=fetched_at,
        )
        findings = [
            self._poa_sale_finding(extract, provenance),
            self._seller_identity_finding(extract, provenance),
            self._encumbrance_finding(extract, provenance),
            self._equitable_mortgage_finding(provenance),
        ]
        # Only meaningful when a POA is actually involved -- see docstring
        # on _poa_registered_finding.
        if extract.is_poa_sale:
            findings.append(self._poa_registered_finding(extract, provenance))
        return findings

    def _encumbrance_finding(
        self, extract: OwnerBackgroundExtract, provenance: Provenance
    ) -> Finding:
        if extract.encumbrances_found is None:
            return Finding(
                check_id="owner_background.encumbrances",
                claim="whether any registered mortgage or charge is recorded against this property",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if extract.encumbrances_found:
            undischarged = [e for e in extract.encumbrances if not e.discharged]
            summary = "; ".join(
                f"{e.nature or 'charge'} {e.document_no or '(unnumbered)'}"
                + (f" dated {e.registered_on.isoformat()}" if e.registered_on else "")
                for e in extract.encumbrances
            )
            return Finding(
                check_id="owner_background.encumbrances",
                claim=(
                    f"{len(extract.encumbrances)} registered encumbrance(s) recorded "
                    f"against this property: {summary}"
                    if summary
                    else "registered encumbrance(s) are recorded against this property"
                ),
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(
                    level="medium",
                    reason=(
                        f"{len(undischarged)} of {len(extract.encumbrances)} show no "
                        "discharge on this screen -- a charge the listing does not mark "
                        "discharged is not proof it is still live, nor proof it is not; "
                        "confirm with the sub-registrar"
                        if extract.encumbrances
                        else "as displayed by the portal"
                    ),
                ),
            )
        return Finding(
            check_id="owner_background.encumbrances",
            claim="no registered mortgage or charge was listed against this property",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(
                level="medium",
                reason=(
                    "covers registered documents only -- see the separate finding on "
                    "equitable mortgages, which never appear in this search"
                ),
            ),
        )

    def _equitable_mortgage_finding(self, provenance: Provenance) -> Finding:
        """A standing finding: a risk this source structurally cannot see.

        Always not_verified, regardless of input, and that is the point.
        An equitable mortgage -- title deeds deposited with a lender, no
        registered deed -- is legally valid and leaves no trace in any
        registered-document search. A clean encumbrance certificate
        therefore does not mean "no mortgage", and a report that goes
        quiet on the distinction invites exactly the wrong conclusion.
        Principle 6 says an un-performable check is reported, not
        omitted; this is that rule applied to a permanent blind spot
        rather than a temporary outage.
        """
        return Finding(
            check_id="owner_background.equitable_mortgage",
            claim=(
                "whether an equitable mortgage (title deeds deposited with a lender, "
                "with no registered deed) exists against this property"
            ),
            status="not_verified",
            provenance=provenance,
            confidence=Confidence(
                level="low",
                reason=(
                    "not knowable from registered records by design -- an equitable "
                    "mortgage creates no registered document, so no encumbrance "
                    "search can rule it out; ask the seller for an original title "
                    "deed and have an advocate verify it is not deposited with a lender"
                ),
            ),
        )

    def _poa_sale_finding(
        self, extract: OwnerBackgroundExtract, provenance: Provenance
    ) -> Finding:
        if extract.is_poa_sale is None:
            return Finding(
                check_id="owner_background.poa_sale",
                claim="whether this transaction is being conducted via Power of Attorney",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if extract.is_poa_sale:
            return Finding(
                check_id="owner_background.poa_sale",
                claim="the seller is transacting via a Power of Attorney, not as the direct titleholder",
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(level="medium", reason=POA_SALE_REASON),
            )
        return Finding(
            check_id="owner_background.poa_sale",
            claim="the seller is transacting directly as the recorded titleholder, not via Power of Attorney",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(level="medium", reason="as shown in IGRS's registered document chain"),
        )

    def _poa_registered_finding(
        self, extract: OwnerBackgroundExtract, provenance: Provenance
    ) -> Finding:
        """Only called when is_poa_sale is True -- asking "is the POA
        registered" is meaningless when there's no POA in the picture."""
        if extract.poa_registered is None:
            return Finding(
                check_id="owner_background.poa_registered",
                claim="whether the Power of Attorney used for this sale is genuinely registered",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if not extract.poa_registered:
            return Finding(
                check_id="owner_background.poa_registered",
                claim="the Power of Attorney used for this sale could not be found as a registered document on IGRS",
                status="issue_found",
                provenance=provenance,
                confidence=Confidence(
                    level="medium",
                    reason="an unregistered or unfindable POA is a strong fraud "
                    "signal, not just a paperwork gap -- do not proceed without "
                    "legal verification",
                ),
            )
        return Finding(
            check_id="owner_background.poa_registered",
            claim="the Power of Attorney used for this sale was found as a registered document on IGRS",
            status="verified_ok",
            provenance=provenance,
            confidence=Confidence(
                level="medium",
                reason="registered does not confirm it hasn't since been revoked "
                "or that a proper sale deed will follow -- see poa_sale finding",
            ),
        )

    def _seller_identity_finding(
        self, extract: OwnerBackgroundExtract, provenance: Provenance
    ) -> Finding:
        if extract.seller_name_matches_title is None:
            return Finding(
                check_id="owner_background.seller_identity",
                claim="whether the seller's name matches IGRS's registered document chain for this property",
                status="not_verified",
                provenance=provenance,
                confidence=Confidence(level="low", reason=NOT_LEGIBLE_REASON),
            )
        if extract.seller_name_matches_title:
            return Finding(
                check_id="owner_background.seller_identity",
                claim="the seller's name matches IGRS's registered document chain for this property",
                status="verified_ok",
                provenance=provenance,
                confidence=Confidence(level="medium", reason="as shown in IGRS's registered document chain"),
            )
        return Finding(
            check_id="owner_background.seller_identity",
            claim="the seller's name does not match IGRS's registered document chain for this property",
            status="issue_found",
            provenance=provenance,
            confidence=Confidence(
                level="medium",
                reason="a name mismatch against the registered chain is a serious "
                "red flag (possible impersonation or an unclean chain) requiring "
                "human verification before proceeding",
            ),
        )
