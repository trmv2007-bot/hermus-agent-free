"""bootstrap — One-command idempotent setup: venv, deps, layout, migration, health."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    bootstrap_parser = subparsers.add_parser(
        "bootstrap", help="One-command idempotent setup: venv, deps, layout, migration, health"
    )
    bootstrap_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    bootstrap_parser.add_argument(
        "--verify-only", action="store_true", help="Verify the existing installation without installing"
    )
    bootstrap_parser.add_argument("--repair", action="store_true", help="Repair broken runtime pieces without deleting user data")
    bootstrap_parser.add_argument("--skip-browser", action="store_true", help="Do not download Chromium")
    bootstrap_parser.add_argument("--skip-optional", action="store_true", help="Do not install optional integrations")


def run(args, ctx: CLIContext) -> None:
    import json as _json

    from bootstrap import doctor as bootstrap_doctor
    from bootstrap import run as bootstrap_run

    has_extended_flags = any(getattr(args, flag, False) for flag in ("verify_only", "repair", "skip_browser", "skip_optional"))
    if getattr(args, "json", False) and not has_extended_flags:
        report = bootstrap_doctor()
        print(_json.dumps(report, indent=2, default=str))
        raise SystemExit(report.get("exit", 1))
    raise SystemExit(
        bootstrap_run(
            verify_only=bool(getattr(args, "verify_only", False)),
            repair=bool(getattr(args, "repair", False)),
            skip_browser=bool(getattr(args, "skip_browser", False)),
            skip_optional=bool(getattr(args, "skip_optional", False)),
            json_output=bool(getattr(args, "json", False)),
        )
    )
