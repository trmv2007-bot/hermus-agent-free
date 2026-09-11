"""update — Update from GitHub - shows update in dashboard and CLI too - like hermes update."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    update_parser = subparsers.add_parser(
        "update", help="Update from GitHub - shows update in dashboard and CLI too - like hermes update"
    )
    update_parser.add_argument("--check", action="store_true", help="Only check for updates, don't pull")


def run(args, ctx: CLIContext) -> None:
    from core.updater import get_updater_for_current_repo

    updater = get_updater_for_current_repo()
    if args.check:
        print("🔍 Checking for updates from GitHub...")
        result = updater.check_for_updates()
        if result.get("update_available"):
            print(f"\n🚀 Update available! {result.get('message')}")
            print(f"Local: {result.get('local', {}).get('short')} - {result.get('local', {}).get('message', '')[:80]}")
            print(
                f"Remote: {result.get('remote', {}).get('short')} - {result.get('remote', {}).get('message', '')[:80]} by {result.get('remote', {}).get('author', '')} on {result.get('remote', {}).get('date', '')[:10]}"
            )
            print(f"Behind by: {result.get('behind_by', 1)} commit(s)")
            print(f"Remote URL: {result.get('remote_url', '')}")
            print("\nTo update: Run 'hermus update' without --check or 'git pull' - shows in dashboard and CLI")
            print("Dashboard will show banner if update available at http://localhost:8000/control")
        elif result.get("up_to_date"):
            print(f"\n✅ Up to date! {result.get('message')}")
        else:
            print(f"\nUpdate check result: {result}")
    else:
        print("🔄 Updating from GitHub via git pull origin main + pip install -r requirements.txt...")
        result = updater.update()
        if result.get("success"):
            print(f"\n✅ Update success! {result.get('message')}")
            print(
                f"New commit: {result.get('new_commit', {}).get('short')} - {result.get('new_commit', {}).get('message', '')[:80]}"
            )
            print("Pull output:", result.get("pull_stdout", "")[:500])
            print("\nDashboard and CLI will now show up to date - refresh dashboard to see new version")
        else:
            print(f"\n❌ Update failed: {result.get('error', result.get('pull_stderr', ''))[:500]}")
