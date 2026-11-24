"""Self commands — what Hermus knows about itself, and what it may change.

``hermus improve "<goal>"``            plan, edit in a worktree, run the gates, print the diff
``hermus improve "<goal>" --plan-only``  stop after the plan
``hermus understand``                  the section the chat prompt carries, with the probe behind it

Nothing here applies a change to the live checkout. The boundary and the
refusals live in :mod:`core.self_edit`; this module only prints what it decided.
"""

from __future__ import annotations

import json
import sys

from ._common import CLIContext
from ._spec import Command


def _utf8_stdout() -> None:
    """Print real text on a cp1252 console instead of dying on a middot."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _configure_improve(subparsers) -> None:
    parser = subparsers.add_parser("improve", help="Draft and test a change to yourself in an isolated worktree (never applies it)")
    parser.add_argument("goal", help="What should be better, in one sentence")
    parser.add_argument("--plan-only", dest="plan_only", action="store_true", help="Show the plan and stop there")
    parser.add_argument("--model", default=None, help="Model that drafts the plan (default: the configured model)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Print the whole run record as JSON")
    parser.add_argument("--repo", default=None, help="Checkout to read (default: the current directory's)")


def _run_improve(args, ctx: CLIContext) -> None:
    from core.self_edit import run

    _utf8_stdout()

    def say(message: str) -> None:
        print(message, flush=True)

    result = run(args.goal, repo=args.repo, plan_only=bool(args.plan_only), model=args.model, progress=say)

    if args.as_json:
        print(json.dumps(result, indent=2, default=str))
        return

    plan = result.get("plan") or {}
    print()
    print(f"status: {result.get('status')}")
    if plan:
        print(f"plan:   {plan.get('summary')}")
        if plan.get("why"):
            print(f"why:    {plan.get('why')}")
        for edit in plan.get("edits") or []:
            print(f"  edits {edit.get('path')}")
    if result.get("worktree"):
        print(f"branch: {result.get('branch')}")
        print(f"tree:   {result.get('worktree')}")
    for gate in result.get("gates") or []:
        print(f"gate    {'ok  ' if gate.get('ok') else 'FAIL'}  {gate.get('command')}")
        if not gate.get("ok") and gate.get("tail"):
            print("        " + str(gate["tail"]).splitlines()[-1][:200])
    if result.get("diff"):
        print()
        print(result["diff"])
    if result.get("error"):
        print(f"\nrefused: {result['error']}")
    print()
    print(
        "live checkout unchanged: "
        + ("yes" if result.get("live_tree_unchanged") else "NO — check git status")
        + f" · record: {result.get('record') or '(not saved)'}"
    )
    if result.get("patch"):
        print(f"patch: {result['patch']}  (apply with: git apply <patch>)")
    print("Nothing was applied, committed, pushed or merged. Accepting a change is the person's call.")


def _configure_understand(subparsers) -> None:
    parser = subparsers.add_parser("understand", help="Show what Hermus knows it can do right now, and what it cannot")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Machine-readable form")


def _run_understand(args, ctx: CLIContext) -> None:
    from core.self_model import MAX_SECTION_CHARS, SelfModel, diagnose, enabled

    _utf8_stdout()

    model = SelfModel()
    section = model.section()
    capabilities = model.capabilities()
    diagnosis = diagnose()
    if args.as_json:
        print(
            json.dumps(
                {
                    "enabled": enabled(),
                    "section": section,
                    "chars": len(section),
                    "limit": MAX_SECTION_CHARS,
                    "available": capabilities["available"],
                    "unavailable": capabilities["unavailable"],
                    "diagnosis": {"broken": diagnosis["broken"], "worth": diagnosis["worth"]},
                },
                indent=2,
                default=str,
            )
        )
        return
    print(section)
    print()
    print(f"section: {len(section)}/{MAX_SECTION_CHARS} chars (HERMUS_SELF_MODEL={'on' if enabled() else 'off'})")
    print(f"usable now:    {len(capabilities['available'])} capabilities")
    print(f"not usable:    {len(capabilities['unavailable'])}")
    print(f"broken now:    {len(diagnosis['broken'])}")
    for item in diagnosis["broken"]:
        print(f"  - {item['item']}  [{item['fix']}]")
    print(f"worth adding:  {len(diagnosis['worth'])}")
    for item in diagnosis["worth"]:
        print(f"  - {item['item']}  [{item['fix']}]")


COMMANDS = (
    Command("improve", "Draft and test a change to yourself in an isolated worktree (never applies it)", _configure_improve, _run_improve),
    Command("understand", "Show what Hermus knows it can do right now, and what it cannot", _configure_understand, _run_understand),
)
