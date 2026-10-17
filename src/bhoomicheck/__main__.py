"""Entry point: run the full BhoomiCheck pipeline from the terminal.

Start a new check:
    python -m bhoomicheck --survey-no 123/A --village Hasanparthy \
        --mandal Hasanparthy --district Warangal \
        --seller-name "K. Rajaiah" [--seller-aka "Rajaiah Kondapaka"] \
        [--poa-document-no GPA/456/2020] \
        deed.pdf ec.pdf

Resume one you started earlier:
    python -m bhoomicheck --resume <check-id>

Runs Document Analysis immediately (its PDFs are supplied up front), then
walks through zoning, litigation, land_records, and owner_background one
at a time: each prints its adapter's instructions() text, asks for the
path to the file you fetched by hand, and continues once you provide it.
growth_potential and risk_report run automatically once their inputs are
ready -- no separate prompt.

State persists in a SQLite file (default ~/.bhoomicheck/checks.db), so a
check genuinely survives closing the terminal. That matters because the
real workflow is not one sitting: a KUDA screenshot today, Bhu Bharati
tomorrow, eCourts captchas next week. Stop any time with Ctrl-C and come
back with --resume <check-id>.

Known limitation, stated honestly rather than hidden: the manual
adapters read your uploaded screenshots with a vision model, but their
field sets have never been checked against a real KUDA / eCourts / Bhu
Bharati / IGRS page. Expect the first real run to need field
corrections. Anything the model cannot read comes back null and is
reported as "not verified" rather than guessed -- so a wrong-looking
report is more likely to be under-filled than confidently wrong.

Prints the validated RiskReport as JSON to stdout once every lookup is in.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from bhoomicheck.agents.document_analysis import DocumentAnalysisAgent
from bhoomicheck.llm.gemini import GeminiProvider
from bhoomicheck.orchestrator.graph import Orchestrator, PendingUpload, sqlite_checkpointer
from bhoomicheck.report.html import render_html
from bhoomicheck.schemas.litigation import LitigationSearchQuery
from bhoomicheck.schemas.owner_background import OwnerBackgroundQuery
from bhoomicheck.schemas.parcel import ParcelIdentifier
from bhoomicheck.schemas.risk_report import RiskReport

DEFAULT_DB = Path.home() / ".bhoomicheck" / "checks.db"


def _prompt_for_upload(pending: PendingUpload) -> Path | None:
    """Show one lookup's instructions and take a file path. None = stop for now."""
    print(f"\n=== {pending.agent} needs a manual lookup ===")
    print(pending.instructions)
    print("\n(press Enter with no path to stop here and resume later)")
    while True:
        path_str = input(f"\nPath to the uploaded file for {pending.agent}: ").strip()
        if not path_str:
            return None
        upload_path = Path(path_str).expanduser()
        if upload_path.is_file():
            return upload_path
        print(f'"{path_str}" is not a file -- try again.')


def _work_through_pending(
    orchestrator: Orchestrator, check_id: str, pending: list[PendingUpload]
) -> RiskReport | None:
    """Walk the outstanding lookups. Returns None if the user stopped early."""
    result: list[PendingUpload] | RiskReport = pending
    while isinstance(result, list) and result:
        upload_path = _prompt_for_upload(result[0])
        if upload_path is None:
            print(f"\nStopped. Resume with:  python -m bhoomicheck --resume {check_id}")
            return None
        result = orchestrator.provide_upload(check_id, result[0].interrupt_id, upload_path)
    return result if isinstance(result, RiskReport) else None


def main() -> int:
    """Parse arguments, run or resume a check, print the report when complete."""
    # Entry point owns process setup: .env is loaded here, once, and
    # nowhere else — library code never mutates the environment.
    load_dotenv()

    parser = argparse.ArgumentParser(prog="bhoomicheck")
    parser.add_argument("--resume", metavar="CHECK_ID", help="continue a check started earlier")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="where checks are stored")
    parser.add_argument("--survey-no")
    parser.add_argument("--village")
    parser.add_argument("--mandal")
    parser.add_argument("--district", default="Warangal")
    parser.add_argument(
        "--seller-name", help="name to search for litigation/owner-background checks"
    )
    parser.add_argument(
        "--seller-aka", nargs="*", default=[], help="spelling variants/short forms to also search"
    )
    parser.add_argument(
        "--poa-document-no", default=None, help="Power of Attorney document number, if already known"
    )
    parser.add_argument(
        "--html", type=Path, metavar="PATH", help="also write the report as an HTML file"
    )
    parser.add_argument(
        "pdfs", nargs="*", type=Path, help="document PDF(s) for Document Analysis"
    )
    args = parser.parse_args()

    # One provider, shared: the document agent reads uploaded PDFs with
    # it, and the manual adapters read portal screenshots with it.
    provider = GeminiProvider()
    orchestrator = Orchestrator(
        document_agent=DocumentAnalysisAgent(provider),
        provider=provider,
        checkpointer=sqlite_checkpointer(args.db),
    )

    try:
        if args.resume:
            check_id = args.resume
            pending = orchestrator.pending_uploads(check_id)
            if not pending:
                # Either finished already, or the id doesn't exist -- tell
                # them which, rather than silently printing nothing.
                done = orchestrator.final_report(check_id)
                if done is None:
                    print(f"No check in progress with id {check_id!r}.", file=sys.stderr)
                    return 1
                print(done.model_dump_json(indent=2))
                return 0
            print(f"Resuming check {check_id} -- {len(pending)} lookup(s) still outstanding.")
            report = _work_through_pending(orchestrator, check_id, pending)
        else:
            required = ["survey_no", "village", "mandal", "seller_name"]
            missing_args = [f"--{name.replace('_', '-')}" for name in required
                            if not getattr(args, name)]
            if missing_args:
                parser.error(f"starting a new check needs: {', '.join(missing_args)}")
            if not args.pdfs:
                parser.error("starting a new check needs at least one document PDF")
            missing_files = [p for p in args.pdfs if not p.is_file()]
            if missing_files:
                parser.error(f"file(s) not found: {', '.join(map(str, missing_files))}")

            parcel = ParcelIdentifier(
                survey_no=args.survey_no,
                village=args.village,
                mandal=args.mandal,
                district=args.district,
            )
            check_id = f"{args.survey_no}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
            print(f"Starting check {check_id}")
            print(f"(stop any time; resume with: python -m bhoomicheck --resume {check_id})")
            started = orchestrator.start(
                thread_id=check_id,
                parcel=parcel,
                pdf_paths=args.pdfs,
                litigation_query=LitigationSearchQuery(
                    full_name=args.seller_name, also_known_as=args.seller_aka
                ),
                owner_background_query=OwnerBackgroundQuery(
                    seller_name=args.seller_name, poa_document_no=args.poa_document_no
                ),
            )
            if isinstance(started, RiskReport):
                report = started
            else:
                report = _work_through_pending(orchestrator, check_id, started)
    except (NotImplementedError, ValidationError) as e:
        # Loud, but without a raw traceback. ValidationError here means a
        # model could not produce a record matching the schema even after
        # retries -- usually an unreadable screenshot, or field names
        # that don't match the real page (see module docstring). The run
        # is checkpointed, so fixing the input and resuming works.
        print(f"\nStopped: {e}", file=sys.stderr)
        print(f"Resume with:  python -m bhoomicheck --resume {check_id}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n\nStopped. Resume with:  python -m bhoomicheck --resume {check_id}")
        return 130

    if report is None:
        return 0
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(report), encoding="utf-8")
        print(f"\nHTML report written to {args.html}", file=sys.stderr)
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
