"""doctor — Health/installation check for Hermus."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    doctor_parser = subparsers.add_parser("doctor", help="Health/installation check for Hermus")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    doctor_parser.add_argument(
        "--self-repair",
        action="store_true",
        help="Run the Hermus doctor: diagnose Hermus itself, report what went wrong and how to manage it",
    )
    doctor_parser.add_argument("--no-internet", action="store_true", help="Do not look unknown failures up online")
    doctor_parser.add_argument("--no-llm", action="store_true", help="Deterministic triage only (no model call)")
    doctor_parser.add_argument("--reap", action="store_true", help="Close out runs/jobs stuck in a non-terminal state")


def run(args, ctx: CLIContext) -> None:
    if getattr(args, "self_repair", False):
        # The Hermus doctor's patient is Hermus itself: runtime errors,
        # stuck runs/jobs, engine health — explained with a management plan.
        from core.doctor import doctor as hermus_doctor
        from core.doctor import to_markdown

        report = hermus_doctor.run(
            ask_internet=not getattr(args, "no_internet", False),
            use_llm=not getattr(args, "no_llm", False),
            reap=bool(getattr(args, "reap", False)),
        )
        if getattr(args, "json", False):
            print(__import__("json").dumps(report, indent=2, default=str))
        else:
            print(to_markdown(report))
        raise SystemExit(0 if report.get("status") == "ok" else 1)
    from core.diagnostics import print_diagnostics, run_diagnostics

    report = run_diagnostics()
    if getattr(args, "json", False):
        print(__import__("json").dumps(report, indent=2, default=str))
    else:
        print_diagnostics(report)
    raise SystemExit(0 if report["overall"]["ok"] else 1)
