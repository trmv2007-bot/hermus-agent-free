"""forge — Skill forge - harvest skills, validate, quarantine."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    forge_parser = subparsers.add_parser("forge", help="Skill forge - harvest skills, validate, quarantine")
    forge_sub = forge_parser.add_subparsers(dest="forge_action")
    forge_sub.add_parser("list", help="Installed skills + health")
    forge_sub.add_parser("stats", help="Harvest stats (created/quarantined/outcome rate)")
    forge_validate = forge_sub.add_parser("validate", help="Validate one skill (import + replay + smoke test)")
    forge_validate.add_argument("name")
    forge_run = forge_sub.add_parser("run", help="Run a harvested skill")
    forge_run.add_argument("name")
    forge_run.add_argument("--task", default="")
    forge_run.add_argument("--execute", action="store_true", help="Actually execute the replay plan")
    forge_sub.add_parser("quarantine", help="List quarantined skills")
    forge_log = forge_sub.add_parser("log", help="Recent forge decisions")
    forge_log.add_argument("--limit", type=int, default=15)


def run(args, ctx: CLIContext) -> None:
    from core.skill_forge import skill_forge

    action = args.forge_action
    if action == "list":
        reg = skill_forge.index()
        st = skill_forge.stats()
        print(f"skills: {reg['count']} (harvested={st['harvested']}, quarantined={st['quarantined']})")
        print(f"registry: {reg['path']}")
        for name, entry in reg["skills"].items():
            print(f" - {name:30s} v{entry.get('version', 1)} :: {str(entry.get('title', ''))[:52]}")
            print(f"     tools={','.join(entry.get('tools') or [])[:70]} status={entry.get('status')}")
    elif action == "stats":
        print(__import__("json").dumps(skill_forge.stats(), indent=2, default=str))
    elif action == "validate":
        print(__import__("json").dumps(skill_forge.validate(Path(skill_forge.skills_dir) / args.name), indent=2, default=str))
    elif action == "run":
        print(__import__("json").dumps(skill_forge.run(args.name, task=args.task, execute=args.execute), indent=2, default=str))
    elif action == "quarantine":
        q = Path(skill_forge.skills_dir) / ".quarantine"
        names = sorted(p.name for p in q.iterdir()) if q.exists() else []
        print(f"quarantined ({len(names)}): " + (", ".join(names) or "none"))
        for n in names:
            rep = q / n / "report.json"
            if rep.exists():
                try:
                    print(f"  - {n}: {__import__('json').loads(rep.read_text()).get('error', '')[:120]}")
                except Exception:
                    pass
    elif action == "log":
        log = Path(skill_forge.skills_dir) / "forge_log.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        if not lines:
            print(f"no forge log yet ({log})")
        for line in lines[-args.limit :]:
            try:
                e = __import__("json").loads(line)
            except Exception:
                continue
            print(
                f" {e.get('ts', '')} {e.get('action'):12s} {e.get('stage', '')} "
                f"{e.get('name', '')} score={e.get('evaluation', {}).get('score')} "
                f"{str(e.get('reasons') or e.get('report') or '')[:90]}"
            )
    else:
        ctx.parser.parse_args(["forge", "--help"])
