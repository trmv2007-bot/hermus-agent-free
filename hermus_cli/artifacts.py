"""artifacts — Artifact-Centric Workspace Explorer."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    art_parser = subparsers.add_parser("artifacts", help="Artifact-Centric Workspace Explorer")
    art_sub = art_parser.add_subparsers(dest="artifact_action")
    art_list = art_sub.add_parser("list", help="List registered artifacts")
    art_list.add_argument("--mission", default=None, help="Filter by mission ID")
    art_export = art_sub.add_parser("export", help="Export artifacts to ZIP bundle")
    art_export.add_argument("output_zip", help="Destination ZIP file")
    art_export.add_argument("--mission", default=None)


def run(args, ctx: CLIContext) -> None:

    from core.artifact_manager import artifact_manager

    if args.artifact_action == "list":
        arts = artifact_manager.list_artifacts(mission_id=args.mission)
        print(f"Artifacts ({len(arts)}):")
        for a in arts:
            print(f" - [{a.artifact_type}] {a.name} ({a.size_bytes} B) -> {a.path}")
    elif args.artifact_action == "export":
        p = artifact_manager.export_bundle(args.output_zip, mission_id=args.mission)
        print(f"Exported bundle to: {p}")
    else:
        ctx.parser.parse_args(["artifacts", "--help"])
