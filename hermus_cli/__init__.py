"""Hermus CLI — one module per command group (see ``hermus_cli/<command>.py``)."""

from __future__ import annotations

import argparse

from core.config import config
from core.log import setup_logging

from . import agent as agent_cmd
from . import api as api_cmd
from . import artifacts as artifacts_cmd
from . import bootstrap as bootstrap_cmd
from . import computer as computer_cmd
from . import counsel as counsel_cmd
from . import cron as cron_cmd
from . import delegate as delegate_cmd
from . import doctor as doctor_cmd
from . import embed as embed_cmd
from . import emergency as emergency_cmd
from . import engine as engine_cmd
from . import eval as eval_cmd
from . import fleet as fleet_cmd
from . import forge as forge_cmd
from . import gateway as gateway_cmd
from . import harness as harness_cmd
from . import jobs as jobs_cmd
from . import mcp as mcp_cmd
from . import mem2 as mem2_cmd
from . import mission as mission_cmd
from . import multiai as multiai_cmd
from . import multikey as multikey_cmd
from . import perms as perms_cmd
from . import plan as plan_cmd
from . import powers as powers_cmd
from . import presence as presence_cmd
from . import profile as profile_cmd
from . import repl as repl_cmd
from . import research as research_cmd
from . import rollback as rollback_cmd
from . import router as router_cmd
from . import run as run_cmd
from . import safety as safety_cmd
from . import sandbox as sandbox_cmd
from . import screen as screen_cmd
from . import skill as skill_cmd
from . import subagent as subagent_cmd
from . import swe as swe_cmd
from . import tools as tools_cmd
from . import update as update_cmd
from . import verify as verify_cmd
from . import watchdog as watchdog_cmd
from . import workspace as workspace_cmd
from ._common import CLIContext

COMMANDS = {
    "gateway": gateway_cmd,
    "bootstrap": bootstrap_cmd,
    "doctor": doctor_cmd,
    "engine": engine_cmd,
    "cron": cron_cmd,
    "subagent": subagent_cmd,
    "skill": skill_cmd,
    "multikey": multikey_cmd,
    "fleet": fleet_cmd,
    "multiai": multiai_cmd,
    "counsel": counsel_cmd,
    "eval": eval_cmd,
    "plan": plan_cmd,
    "api": api_cmd,
    "update": update_cmd,
    "mcp": mcp_cmd,
    "embed": embed_cmd,
    "tools": tools_cmd,
    "workspace": workspace_cmd,
    "mem2": mem2_cmd,
    "forge": forge_cmd,
    "sandbox": sandbox_cmd,
    "delegate": delegate_cmd,
    "jobs": jobs_cmd,
    "router": router_cmd,
    "run": run_cmd,
    "agent": agent_cmd,
    "perms": perms_cmd,
    "powers": powers_cmd,
    "safety": safety_cmd,
    "emergency": emergency_cmd,
    "research": research_cmd,
    "screen": screen_cmd,
    "computer": computer_cmd,
    "harness": harness_cmd,
    "watchdog": watchdog_cmd,
    "profile": profile_cmd,
    "presence": presence_cmd,
    "mission": mission_cmd,
    "swe": swe_cmd,
    "verify": verify_cmd,
    "artifacts": artifacts_cmd,
    "rollback": rollback_cmd,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermus Agent Free - The agent that grows with you, 100% free, no paywall", prog="hermus"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Default: start TUI chat
    parser.add_argument(
        "--model",
        default=config.model,
        help="Model: ollama/llama3.1:8b (free offline), groq/... (free tier), hf/... (free), mock/mock",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Mode: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys - persisted to user_model.json",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Persona profile (hermus profile list) - gives the agent an independent memory + system prompt",
    )

    for _name, _mod in COMMANDS.items():
        _mod.register(subparsers)
    return parser


def main() -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()
    ctx = CLIContext(parser=parser)
    if args.command is None:
        repl_cmd.run_default(args, ctx)
        return
    COMMANDS[args.command].run(args, ctx)


__all__ = ["COMMANDS", "build_parser", "main"]
