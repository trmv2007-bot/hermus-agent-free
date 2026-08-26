#!/usr/bin/env python3
"""
Hermus Agent Free - CLI Entry Point
Mimics original Hermes CLI: hermes, hermes gateway setup, hermes gateway start, etc.

This free version uses `hermus` instead of `hermes` to avoid conflict, but you can alias:
alias hermes="python -m hermus"

Free stack: Ollama local, no paywall
"""

import sys
import argparse
from pathlib import Path

# Ensure imports work
sys.path.insert(0, str(Path(__file__).parent))

from core.config import config

def main():
    parser = argparse.ArgumentParser(
        description="Hermus Agent Free - The agent that grows with you, 100% free, no paywall",
        prog="hermus"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Default: start TUI chat
    parser.add_argument("--model", default=config.model, help="Model: ollama/llama3.1:8b (free offline), groq/... (free tier), hf/... (free), mock/mock")
    parser.add_argument("--mode", default=None, help="Mode: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys - persisted to user_model.json")
    parser.add_argument("--profile", default=None, help="Persona profile (hermus profile list) - gives the agent an independent memory + system prompt")

    # gateway subcommand
    gateway_parser = subparsers.add_parser("gateway", help="Gateway - single process for Telegram/Discord/CLI")
    gateway_sub = gateway_parser.add_subparsers(dest="gateway_action")
    gateway_setup = gateway_sub.add_parser("setup", help="Setup gateway for platform")
    gateway_setup.add_argument("--platform", default="telegram", help="telegram, discord, slack, etc.")
    gateway_start = gateway_sub.add_parser("start", help="Start gateway")
    gateway_start.add_argument("--port", type=int, default=config.gateway_port)

    # doctor subcommand - install/health wizard (Phase D)
    doctor_parser = subparsers.add_parser("doctor", help="Health/installation check for Hermus")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    # cron subcommand
    cron_parser = subparsers.add_parser("cron", help="Cron scheduler - natural language")
    cron_sub = cron_parser.add_subparsers(dest="cron_action")
    cron_add = cron_sub.add_parser("add", help="Add cron job from natural language")
    cron_add.add_argument("text", help="Natural language schedule: 'daily at 9am send report'")
    cron_add.add_argument("--task", help="Task to execute (defaults to text)")
    cron_add.add_argument("--platform", default="cli")
    cron_add.add_argument("--user-id", default="default")
    cron_list = cron_sub.add_parser("list", help="List cron jobs")
    cron_remove = cron_sub.add_parser("remove", help="Remove cron job")
    cron_remove.add_argument("job_id")

    # subagent subcommand
    subagent_parser = subparsers.add_parser("subagent", help="Subagents - parallel work")
    subagent_parser.add_argument("action", choices=["spawn"], help="spawn")
    subagent_parser.add_argument("task", help="Task for subagent")

    # skill subcommand
    skill_parser = subparsers.add_parser("skill", help="Skills")
    skill_parser.add_argument("action", choices=["list", "improve"], help="list or improve")
    skill_parser.add_argument("--name", help="Skill name for improve")

    # multi-key subcommand - any AI API key works
    multikey_parser = subparsers.add_parser(
        "multikey",
        help="Multi-API Keys - ANY OpenAI-compatible key (groq/openai/openrouter/gemini/custom/...). Health, models, rate limits, parallel.",
    )
    multikey_sub = multikey_parser.add_subparsers(dest="multikey_action")
    multikey_add = multikey_sub.add_parser("add", help="Add ANY API key (auto model discover + health)")
    multikey_add.add_argument(
        "--provider",
        required=True,
        help="Provider id: groq, openai, openrouter, together, gemini, deepseek, mistral, cerebras, hf, custom, ...",
    )
    multikey_add.add_argument("--key", required=True, help="API key")
    multikey_add.add_argument("--name", help="Key name")
    multikey_add.add_argument("--base-url", help="OpenAI-compatible base URL e.g. https://api.groq.com/openai/v1")
    multikey_add.add_argument("--model", help="Default model id for this key")
    multikey_add.add_argument("--rpm", type=int, help="Requests-per-minute budget")
    multikey_add.add_argument("--tpm", type=int, help="Tokens-per-minute budget")
    multikey_add.add_argument("--no-probe", action="store_true", help="Skip auto health/model probe")
    multikey_list = multikey_sub.add_parser("list", help="List keys per provider (redacted + health)")
    multikey_list.add_argument("--provider", help="Provider filter")
    multikey_remove = multikey_sub.add_parser("remove", help="Remove key")
    multikey_remove.add_argument("--provider", required=True)
    multikey_remove.add_argument("--key", required=True, help="Key or name to remove")
    multikey_parallel = multikey_sub.add_parser("parallel", help="Run tasks in parallel on different keys")
    multikey_parallel.add_argument("--provider", default="groq")
    multikey_parallel.add_argument("--tasks", nargs="+", help="Tasks to run in parallel with different keys")
    multikey_health = multikey_sub.add_parser("health", help="Check API key health + rate limits + models")
    multikey_health.add_argument("--provider", help="Provider filter (default: all)")
    multikey_models = multikey_sub.add_parser("models", help="Discover models for a provider/key")
    multikey_models.add_argument("--provider", required=True)
    multikey_models.add_argument("--key", help="Specific key (optional)")
    multikey_models.add_argument("--base-url", help="Override base URL")
    multikey_rates = multikey_sub.add_parser("rates", help="Show RPM/TPM usage vs limits")
    multikey_rates.add_argument("--provider", help="Provider filter")
    multikey_providers = multikey_sub.add_parser("providers", help="List known AI providers")

    # fleet - multi-model task distribution
    fleet_parser = subparsers.add_parser(
        "fleet",
        help="Model fleet - distribute tasks across multiple AI models + API keys",
    )
    fleet_sub = fleet_parser.add_subparsers(dest="fleet_action")
    fleet_workers = fleet_sub.add_parser("workers", help="List available model/key workers")
    fleet_workers.add_argument("--providers", help="Comma-separated providers")
    fleet_workers.add_argument("--models", help="Comma-separated provider/model")
    fleet_run = fleet_sub.add_parser("run", help="Distribute a goal (auto|fanout|map|race)")
    fleet_run.add_argument("goal", help="Goal / prompt")
    fleet_run.add_argument("--strategy", default="auto", choices=["auto", "fanout", "map", "race"])
    fleet_run.add_argument("--models", help="Comma-separated provider/model")
    fleet_run.add_argument("--providers", help="Comma-separated providers")
    fleet_run.add_argument("--workers", type=int, default=4)
    fleet_fanout = fleet_sub.add_parser("fanout", help="Same prompt → many models → consensus")
    fleet_fanout.add_argument("prompt")
    fleet_fanout.add_argument("--models", help="Comma-separated")
    fleet_fanout.add_argument("--providers", help="Comma-separated")
    fleet_fanout.add_argument("--workers", type=int, default=4)
    fleet_map = fleet_sub.add_parser("map", help="Split goal into subtasks across models")
    fleet_map.add_argument("goal")
    fleet_map.add_argument("--models", help="Comma-separated")
    fleet_map.add_argument("--providers", help="Comma-separated")
    fleet_map.add_argument("--workers", type=int, default=4)

    # multi-ai subcommand - multiple AIs talk to each other
    multiai_parser = subparsers.add_parser("multiai", help="Multi-AI - multiple AIs talk to each other for anything")
    multiai_sub = multiai_parser.add_subparsers(dest="multiai_action")
    multiai_debate = multiai_sub.add_parser("debate", help="Multi-AI debate on topic")
    multiai_debate.add_argument("topic", help="Topic for debate")
    multiai_debate.add_argument("--rounds", type=int, default=2, help="Rounds")
    multiai_debate.add_argument("--model", default=None, help="Model for all agents")
    multiai_debate.add_argument("--agents", nargs="+", default=None, help="Agent personas: researcher coder reviewer writer planner debater optimist pessimist")

    multiai_chat = multiai_sub.add_parser("chat", help="Multi-AI collaborative chat")
    multiai_chat.add_argument("task", help="Task for collaborative chat")
    multiai_chat.add_argument("--rounds", type=int, default=3)
    multiai_chat.add_argument("--model", default=None)

    multiai_personas = multiai_sub.add_parser("personas", help="List persona presets")

    # counsel subcommand - Council of AIs: talk, plan, execute, and upgrade itself
    counsel_parser = subparsers.add_parser(
        "counsel",
        help="Counsel System - council of AIs plans together, then upgrades itself",
    )
    counsel_sub = counsel_parser.add_subparsers(dest="counsel_action")
    counsel_run = counsel_sub.add_parser("run", help="Convene the council for a task")
    counsel_run.add_argument("goal", help="The task/goal for the council")
    counsel_run.add_argument("--rounds", type=int, default=None, help="Override deliberation rounds")
    counsel_run.add_argument("--difficulty", type=int, default=None, choices=[1, 2, 3, 4, 5], help="Override difficulty 1-5")
    counsel_run.add_argument("--members", type=int, default=None, help="Override max members")
    counsel_run.add_argument("--no-execute", action="store_true", help="Plan only, skip tool execution")
    counsel_run.add_argument("--model", default=None, help="Base model for members (auto-diversified)")
    counsel_status = counsel_sub.add_parser("status", help="Council status: constitution version, roster, upgrades")
    counsel_amend = counsel_sub.add_parser("amend", help="Self-upgrade amendments (Meta-Counsel)")
    counsel_amend_sub = counsel_amend.add_subparsers(dest="counsel_amend_action")
    counsel_amend_list = counsel_amend_sub.add_parser("list", help="List pending amendments + upgrade history")
    counsel_amend_approve = counsel_amend_sub.add_parser("approve", help="Approve a pending high-risk amendment")
    counsel_amend_approve.add_argument("amendment_id")
    counsel_amend_reject = counsel_amend_sub.add_parser("reject", help="Reject a pending amendment")
    counsel_amend_reject.add_argument("amendment_id")
    counsel_amend_rollback = counsel_amend_sub.add_parser("rollback", help="Roll back constitution to a previous version")
    counsel_amend_rollback.add_argument("version", type=int)
    counsel_review = counsel_sub.add_parser("review", help="Run Meta-Counsel review on the last council session")
    counsel_review.add_argument("--session-id", default=None, help="Specific session id (default: latest)")

    # eval subcommand - benchmark harness (Phase 4)
    eval_parser = subparsers.add_parser("eval", help="Eval harness - measure thinking strategies")
    eval_sub = eval_parser.add_subparsers(dest="eval_action")
    eval_run = eval_sub.add_parser("run", help="Run benchmark tasks under a strategy")
    eval_run.add_argument("--strategy", default="auto", help="auto|none|reflexion|self_consistency|verify")
    eval_run.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    eval_run.add_argument("--category", default=None, help="Only one category: fact|research|code|extraction|math")
    eval_run.add_argument("--model", default=None, help="Model for the solver")
    eval_list = eval_sub.add_parser("list", help="List benchmark tasks")
    eval_compare = eval_sub.add_parser("compare", help="A/B two strategies on the same tasks")
    eval_compare.add_argument("--a", required=True, help="Strategy A: auto|none|reflexion|self_consistency|verify")
    eval_compare.add_argument("--b", required=True, help="Strategy B")
    eval_compare.add_argument("--limit", type=int, default=None)
    eval_compare.add_argument("--model", default=None)
    eval_history = eval_sub.add_parser("history", help="Show eval run history")
    eval_history.add_argument("--limit", type=int, default=10)

    # plan subcommand - plan persistence & resume (Phase 4, P1)
    plan_parser = subparsers.add_parser("plan", help="Plans - DeepThink plan persistence & resume")
    plan_sub = plan_parser.add_subparsers(dest="plan_action")
    plan_list = plan_sub.add_parser("list", help="List saved plans")
    plan_show = plan_sub.add_parser("show", help="Show a plan")
    plan_show.add_argument("session_id")
    plan_resume = plan_sub.add_parser("resume", help="Resume a plan (runs remaining steps)")
    plan_resume.add_argument("session_id")
    plan_resume.add_argument("--model", default=None)

    # api subcommand - custom API feature free
    api_parser = subparsers.add_parser("api", help="Custom API - add any API as tool (free)")
    api_sub = api_parser.add_subparsers(dest="api_action")
    api_add = api_sub.add_parser("add", help="Add custom API")
    api_add.add_argument("--name", required=True, help="API tool name, e.g., weather_api")
    api_add.add_argument("--description", required=True, help="Description for LLM")
    api_add.add_argument("--url", required=True, help="URL with optional {param} placeholders, e.g., https://api.example.com/weather/{city}")
    api_add.add_argument("--method", default="GET", choices=["GET", "POST", "PUT", "DELETE"], help="HTTP method")
    api_add.add_argument("--header", action="append", help="Headers as Key:Value, can repeat")
    api_add.add_argument("--param", action="append", help="Parameters as name:description, e.g., city:City name, can repeat")
    api_add.add_argument("--auth-type", choices=["none", "bearer", "apikey", "basic"], default="none")
    api_add.add_argument("--auth-token", help="Bearer token or API key value")
    api_add.add_argument("--auth-key", help="API key header name (for apikey type) or username (for basic)")
    api_add.add_argument("--auth-password", help="Password for basic auth")

    api_list = api_sub.add_parser("list", help="List custom APIs")
    api_remove = api_sub.add_parser("remove", help="Remove custom API")
    api_remove.add_argument("name", help="API name or id")
    api_test = api_sub.add_parser("test", help="Test custom API")
    api_test.add_argument("name", help="API name")
    api_test.add_argument("--args", help="JSON args for test, e.g., '{\"city\": \"London\"}'")

    api_discover = api_sub.add_parser(
        "discover",
        help="Find useful APIs in the public-apis catalog (offline snapshot)",
    )
    api_discover.add_argument("query", nargs="?", default="", help="Task/API keywords, e.g. weather or threat intelligence")
    api_discover.add_argument("--category", default="", help="Category filter, e.g. Security")
    api_discover.add_argument("--auth", default="any", help="any, No, apiKey, OAuth, ...")
    api_discover.add_argument("--allow-http", action="store_true", help="Include APIs without confirmed HTTPS")
    api_discover.add_argument("--cors", default="any", choices=["any", "Yes", "No", "Unknown"])
    api_discover.add_argument("--limit", type=int, default=10)
    api_discover.add_argument("--refresh", action="store_true", help="Refresh the runtime catalog from GitHub first")
    api_sub.add_parser("categories", help="List public API categories and free/HTTPS counts")
    api_sub.add_parser("refresh-catalog", help="Refresh public API catalog from GitHub")

    # update subcommand - check for GitHub updates and show in dashboard and CLI too
    update_parser = subparsers.add_parser("update", help="Update from GitHub - shows update in dashboard and CLI too - like hermes update")
    update_parser.add_argument("--check", action="store_true", help="Only check for updates, don't pull")

    # mcp subcommand - Model Context Protocol servers
    mcp_parser = subparsers.add_parser("mcp", help="MCP servers - connect external tool servers (stdio)")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")
    mcp_list = mcp_sub.add_parser("list", help="List MCP servers")
    mcp_add = mcp_sub.add_parser("add", help="Add MCP server")
    mcp_add.add_argument("--name", required=True)
    mcp_add.add_argument("--command", required=True, help="Executable e.g. npx or python3")
    mcp_add.add_argument("--arg", action="append", default=[], help="Repeatable arg")
    mcp_add.add_argument("--disabled", action="store_true", help="Add but leave disabled")
    mcp_remove = mcp_sub.add_parser("remove", help="Remove MCP server")
    mcp_remove.add_argument("name")
    mcp_connect = mcp_sub.add_parser("connect", help="Connect enabled MCP servers and register tools")
    mcp_call = mcp_sub.add_parser("call", help="Call MCP tool")
    mcp_call.add_argument("--server", required=True)
    mcp_call.add_argument("--tool", required=True)
    mcp_call.add_argument("--args", default="{}", help="JSON arguments")

    # embeddings / semantic memory
    emb_parser = subparsers.add_parser("embed", help="Semantic memory - ingest docs + vector search (free local)")
    emb_sub = emb_parser.add_subparsers(dest="embed_action")
    emb_status = emb_sub.add_parser("status", help="Backend status")
    emb_ingest = emb_sub.add_parser("ingest", help="Ingest file or directory")
    emb_ingest.add_argument("path")
    emb_ingest.add_argument("--source", default=None)
    emb_search = emb_sub.add_parser("search", help="Semantic or hybrid search")
    emb_search.add_argument("query")
    emb_search.add_argument("--limit", type=int, default=5)
    emb_search.add_argument("--hybrid", action="store_true", default=True)
    emb_search.add_argument("--semantic-only", action="store_true")
    emb_clear = emb_sub.add_parser("clear", help="Clear embeddings")
    emb_clear.add_argument("--source", default=None)

    # tools list
    tools_parser = subparsers.add_parser("tools", help="List registered tools (auto registry)")

    # ---- architecture upgrades (foundation) ---------------------------------
    # workspace — per-project isolation
    ws_parser = subparsers.add_parser("workspace", help="Workspace - per-project isolation (agent OS)")
    ws_sub = ws_parser.add_subparsers(dest="ws_action")
    ws_sub.add_parser("layout", help="Show workspace layout + paths")
    ws_create = ws_sub.add_parser("create", help="Create a project")
    ws_create.add_argument("name")
    ws_create.add_argument("--description", default="")
    ws_sub.add_parser("list", help="List projects")
    ws_use = ws_sub.add_parser("use", help="Set the current project")
    ws_use.add_argument("name")

    # memory 2.0 — typed + scored memory
    mem2_parser = subparsers.add_parser("mem2", help="Memory 2.0 - typed long-term memory with scoring")
    mem2_sub = mem2_parser.add_subparsers(dest="mem2_action")
    mem2_remember = mem2_sub.add_parser("remember", help="Persist a typed memory")
    mem2_remember.add_argument("kind", choices=["working", "episodic", "semantic", "procedural", "project"])
    mem2_remember.add_argument("content")
    mem2_remember.add_argument("--importance", type=float, default=5.0)
    mem2_remember.add_argument("--success", choices=["true", "false", "none"], default="none")
    mem2_recall = mem2_sub.add_parser("recall", help="Ranked recall")
    mem2_recall.add_argument("query")
    mem2_recall.add_argument("--limit", type=int, default=10)

    # model router 2.0
    router_parser = subparsers.add_parser("router", help="Model Router 2.0 - per-step model selection")
    router_sub = router_parser.add_subparsers(dest="router_action")
    router_choose = router_sub.add_parser("choose", help="Choose the best model for a step")
    router_choose.add_argument("text")

    # autonomous loop
    run_parser = subparsers.add_parser("run", help="Autonomous task loop - plan/execute/verify/repair")
    run_parser.add_argument("task", help="Goal to drive through the verify/repair loop")
    run_parser.add_argument("--model", default=None, help="Model to run with (default: config.model)")
    run_parser.add_argument("--max-repairs", type=int, default=2, help="Max diagnose/repair cycles")

    # persistent background agents
    agent_parser = subparsers.add_parser("agent", help="Persistent background agents")
    agent_sub = agent_parser.add_subparsers(dest="agent_action")
    agent_create = agent_sub.add_parser("create", help="Create a named agent")
    agent_create.add_argument("name")
    agent_create.add_argument("--role", default="generic",
                              choices=["researcher", "coder", "system-monitor", "scheduler", "memory-manager",
                                       "watchdog", "computer-operator", "coordinator", "generic"])
    agent_create.add_argument("--model", default=None)
    agent_start = agent_sub.add_parser("start", help="Start a background agent worker")
    agent_start.add_argument("name")
    agent_status = agent_sub.add_parser("status", help="Inspect an agent")
    agent_status.add_argument("name")
    agent_stop = agent_sub.add_parser("stop", help="Stop an agent")
    agent_stop.add_argument("name")
    agent_job = agent_sub.add_parser("job", help="Queue a task for a background agent")
    agent_job.add_argument("name")
    agent_job.add_argument("task", nargs="+")
    agent_job.add_argument("--wait", action="store_true")
    agent_job.add_argument("--timeout", type=float, default=180.0)
    agent_result = agent_sub.add_parser("result", help="Inspect a background job")
    agent_result.add_argument("name")
    agent_result.add_argument("job_id")
    agent_sub.add_parser("list", help="List all agents")

    # permissions
    perms_parser = subparsers.add_parser("perms", help="Permission manager - ALLOW/ASK/DENY")
    perms_sub = perms_parser.add_subparsers(dest="perms_action")
    perms_check = perms_sub.add_parser("check", help="Check a tool's permission decision")
    perms_check.add_argument("tool")
    perms_set = perms_sub.add_parser("set", help="Override a tool's policy")
    perms_set.add_argument("tool")
    perms_set.add_argument("decision", choices=["allow", "ask", "deny"])
    perms_set.add_argument("--agent", default=None)
    perms_sub.add_parser("list", help="Recent audit log")

    # research pipeline
    research_parser = subparsers.add_parser("research", help="Web research - multi-source with citations")
    research_parser.add_argument("query")

    # screen recording (computer control)
    screen_parser = subparsers.add_parser("screen", help="Screen recording, visual timelines, verification and watching")
    screen_sub = screen_parser.add_subparsers(dest="screen_action")

    def add_screen_start_args(command_parser):
        command_parser.add_argument("--fps", type=float, default=10.0)
        command_parser.add_argument("--buffer-seconds", type=float, default=30.0)
        command_parser.add_argument("--format", choices=["mp4", "webm"], default="mp4")

    # Backward-compatible short form: hermus screen start|stop|status|save
    screen_start = screen_sub.add_parser("start", help="Start the background recorder")
    add_screen_start_args(screen_start)
    screen_sub.add_parser("stop", help="Stop and finalize the background recorder")
    screen_sub.add_parser("status", help="Background recorder status")
    screen_save = screen_sub.add_parser("save", help="Save the last recording as a video or task bundle")
    screen_save.add_argument("target", help="Filename (.mp4/.webm) or task id")

    # Explicit form from the Computer Agent architecture:
    # hermus screen record start|stop|status|save
    screen_record = screen_sub.add_parser("record", help="Conventional MP4/WebM screen recording")
    record_sub = screen_record.add_subparsers(dest="record_action")
    record_start = record_sub.add_parser("start", help="Start recording in a detached local service")
    add_screen_start_args(record_start)
    record_sub.add_parser("stop", help="Stop and finalize recording")
    record_sub.add_parser("status", help="Show recording status")
    record_save = record_sub.add_parser("save", help="Save the latest video or complete task bundle")
    record_save.add_argument("target", help="Filename (.mp4/.webm) or task id")

    screen_analyze = screen_sub.add_parser("analyze", help="Generate a visual event timeline from a recording")
    screen_analyze.add_argument("video", nargs="?", default="", help="MP4/WebM path (default: latest recording)")
    screen_analyze.add_argument("--task", default="Screen recording")
    screen_analyze.add_argument("--sample-fps", type=float, default=2.0)
    screen_analyze.add_argument("--max-seconds", type=float, default=3600.0)
    screen_analyze.add_argument("--max-events", type=int, default=12)
    screen_analyze.add_argument("--model", default="llava:7b")
    screen_analyze.add_argument("--no-vision", action="store_true", help="Detect changes without running a vision model")

    screen_watch = screen_sub.add_parser("watch", help="Wait until a visual condition becomes true")
    screen_watch.add_argument("condition")
    screen_watch.add_argument("--timeout", type=float, default=60.0)
    screen_watch.add_argument("--fps", type=float, default=2.0)
    screen_watch.add_argument("--model", default="llava:7b")

    # computer agent (autonomous desktop control)
    computer_parser = subparsers.add_parser("computer", help="Autonomous computer agent - plan/act/record/verify/repair")
    computer_sub = computer_parser.add_subparsers(dest="computer_action")

    def add_computer_task_args(command_parser):
        command_parser.add_argument("task", nargs="+", help="Natural-language desktop task")
        command_parser.add_argument("--task-id", default=None)
        command_parser.add_argument("--model", default=None, help="Ollama vision model for semantic verification")
        command_parser.add_argument("--retries", type=int, default=2)
        command_parser.add_argument("--no-skill", action="store_true", help="Do not learn/update a skill")
        command_parser.add_argument("--dry-run", action="store_true", help="Plan and simulate without touching the machine")

    computer_task = computer_sub.add_parser("task", help="Run a desktop task autonomously")
    add_computer_task_args(computer_task)
    computer_run = computer_sub.add_parser("run", help="Run and persist a resumable desktop task")
    add_computer_task_args(computer_run)
    computer_resume = computer_sub.add_parser("resume", help="Resume a persisted desktop task")
    computer_resume.add_argument("task_id")
    computer_resume.add_argument("--model", default=None)
    computer_resume.add_argument("--retries", type=int, default=2)
    computer_resume.add_argument("--dry-run", action="store_true")
    computer_sub.add_parser("tasks", help="List persisted desktop tasks")
    computer_show = computer_sub.add_parser("show", help="Show a persisted desktop task checkpoint")
    computer_show.add_argument("task_id")
    computer_delegate = computer_sub.add_parser("delegate", help="Delegate a task across persistent agents")
    computer_delegate.add_argument("task", nargs="+")
    computer_delegate.add_argument("--no-wait", action="store_true")
    computer_delegate.add_argument("--timeout", type=float, default=180.0)
    computer_delegate.add_argument("--dry-run", action="store_true")
    computer_sub.add_parser("stop", help="Emergency stop - halt all mouse/keyboard/autonomous control")
    computer_sub.add_parser("status", help="Show the computer control center")
    computer_target = computer_sub.add_parser("target", help="Vision-driven find-on-screen for a UI element")
    computer_target.add_argument("target", nargs="+")
    computer_target.add_argument("--model", default="llava:7b")
    computer_click = computer_sub.add_parser("click", help="Vision-driven click: locate a UI element, then click it")
    computer_click.add_argument("target", nargs="+")
    computer_click.add_argument("--model", default="llava:7b")
    computer_wait = computer_sub.add_parser("wait", help="Wait until a visual condition is true")
    computer_wait.add_argument("condition", nargs="+")
    computer_wait.add_argument("--timeout", type=float, default=60.0)
    computer_wait.add_argument("--model", default="llava:7b")
    computer_sub.add_parser("skills", help="List learned computer skills")

    # jcode-inspired agent harness
    harness_parser = subparsers.add_parser("harness", help="Agent harness - sessions, swarm bus, file-shift, compaction")
    harness_sub = harness_parser.add_subparsers(dest="harness_action")
    harness_sub.add_parser("sessions", help="List server-owned sessions")
    h_attach = harness_sub.add_parser("attach", help="Attach a surface to a session")
    h_attach.add_argument("session_id")
    h_detach = harness_sub.add_parser("detach", help="Detach a surface from a session")
    h_detach.add_argument("session_id")
    h_msg = harness_sub.add_parser("send", help="Send a swarm message")
    h_msg.add_argument("body")
    h_msg.add_argument("--from", dest="sender", default="cli")
    h_msg.add_argument("--to", default="")
    h_msg.add_argument("--channel", default="")
    h_msg.add_argument("--kind", default="broadcast", choices=["dm", "broadcast", "channel"])
    h_inbox = harness_sub.add_parser("inbox", help="Read swarm inbox")
    h_inbox.add_argument("session_id")
    h_spawn = harness_sub.add_parser("spawn", help="Register swarm workers (no LLM)")
    h_spawn.add_argument("task")
    h_spawn.add_argument("--parent", default="cli")
    h_spawn.add_argument("--count", type=int, default=2)
    h_recall = harness_sub.add_parser("recall", help="Cascade memory recall")
    h_recall.add_argument("query")

    # watchdog (self-healing)
    watchdog_parser = subparsers.add_parser("watchdog", help="Self-healing watchdog - classify/repair errors")
    watchdog_parser.add_argument("error", nargs="?", default="", help="Error text to classify/repair")

    # profiles
    profile_parser = subparsers.add_parser("profile", help="Agent profiles - personas with independent memory")
    profile_sub = profile_parser.add_subparsers(dest="profile_action")
    profile_create = profile_sub.add_parser("create", help="Create a profile")
    profile_create.add_argument("name")
    profile_create.add_argument("--persona", default=None)
    profile_sub.add_parser("list", help="List profiles")
    profile_use = profile_sub.add_parser("use", help="Show a profile's system prompt")
    profile_use.add_argument("name")

    args = parser.parse_args()

    if args.command == "doctor":
        from core.diagnostics import run_diagnostics, print_diagnostics
        report = run_diagnostics()
        if getattr(args, "json", False):
            print(__import__("json").dumps(report, indent=2, default=str))
        else:
            print_diagnostics(report)
        raise SystemExit(0 if report["overall"]["ok"] else 1)

    if args.command == "gateway":
        if args.gateway_action == "setup":
            from gateway.gateway import setup
            setup(args.platform)
        elif args.gateway_action == "start":
            from gateway.gateway import start
            start(args.port)
        else:
            parser.parse_args(["gateway", "--help"])

    elif args.command == "cron":
        from scheduler.cron import cron_manager
        if args.cron_action == "add":
            job = cron_manager.add_job(args.text, task=args.task, platform=args.platform, user_id=args.user_id)
            print(f"Cron job added: {job['id']} - {job['cron']} - {job['task']}")
        elif args.cron_action == "list":
            jobs = cron_manager.list_jobs()
            print(f"Cron jobs ({len(jobs)}):")
            for j in jobs:
                print(f" - {j['id']}: {j['cron']} - {j['natural']} -> {j['platform']}:{j['user_id']}")
        elif args.cron_action == "remove":
            ok = cron_manager.remove_job(args.job_id)
            print(f"Removed {args.job_id}: {ok}")
        else:
            parser.parse_args(["cron", "--help"])

    elif args.command == "subagent":
        if args.action == "spawn":
            from subagents.subagent import spawn_subagent
            result = spawn_subagent(args.task)
            print(f"Subagent result: {result}")

    elif args.command == "skill":
        from core.skill_manager import skill_manager
        if args.action == "list":
            skills = skill_manager.list_skills()
            print(f"Skills ({len(skills)}):")
            for s in skills:
                print(f" - {s['name']}: {s['description'][:100]}")
        elif args.action == "improve":
            if not args.name:
                print("Need --name for improve")
            else:
                result = skill_manager.improve_skill(args.name)
                print(f"Improve result: {result}")

    elif args.command == "multikey":
        from core.multi_key import multi_key_manager
        from core.providers import list_providers
        import json as json_lib
        if args.multikey_action == "add":
            result = multi_key_manager.add_key(
                args.provider,
                args.key,
                name=args.name,
                base_url=getattr(args, "base_url", None),
                default_model=getattr(args, "model", None),
                rpm_limit=getattr(args, "rpm", None),
                tpm_limit=getattr(args, "tpm", None),
                auto_discover=not getattr(args, "no_probe", False),
            )
            if result.get("success"):
                print(
                    f"\n✅ Added {args.provider} key '{result.get('key_name')}'. "
                    f"Total keys for provider: {result.get('total_keys')}"
                )
                print(f"   Model       : {args.provider}/{result.get('default_model') or 'MODEL'}")
                print(f"   Base URL    : {result.get('base_url') or '(provider default)'}")
                print(f"   Preset      : {result.get('preset') or args.provider}")
                h = result.get("health") or {}
                if h:
                    print(
                        f"   Health      : {h.get('status')} healthy={h.get('healthy')} "
                        f"latency={h.get('latency_ms')}ms models={h.get('models_count')}"
                    )
                    if h.get("models_sample"):
                        print(f"   Models      : {', '.join(h['models_sample'][:8])}")
                    if h.get("error"):
                        print(f"   Probe error : {str(h['error'])[:200]}")
            else:
                print(f"❌ {result.get('error', 'Failed to add key')}")
        elif args.multikey_action == "list":
            apis = multi_key_manager.list_keys(args.provider, redact=True)
            print("Multi-API Keys (redacted):")
            for provider, keys in apis.items():
                print(f" {provider}: {len(keys)} keys")
                for k in keys:
                    print(
                        f"   - {k.get('name')}: {k.get('preview')} | model={k.get('default_model')} "
                        f"| healthy={k.get('healthy')} status={k.get('health_status')} "
                        f"| models={k.get('models_count')} rpm={k.get('rpm_limit')} "
                        f"| avg_rt={k.get('avg_response_time')} usage={k.get('usage_count')}"
                    )
                    if k.get("models_sample"):
                        print(f"     models: {', '.join(k['models_sample'][:6])}")
        elif args.multikey_action == "remove":
            result = multi_key_manager.remove_key(args.provider, args.key)
            print(f"Remove result: {result}")
        elif args.multikey_action == "parallel":
            tasks = []
            if args.tasks:
                for t in args.tasks:
                    tasks.append({"prompt": t, "messages": [{"role": "user", "content": t}]})
            else:
                tasks = [
                    {"prompt": "What is Python async?", "messages": [{"role": "user", "content": "What is Python async?"}]},
                    {"prompt": "What is Rust async?", "messages": [{"role": "user", "content": "What is Rust async?"}]},
                    {"prompt": "What is Go concurrency?", "messages": [{"role": "user", "content": "What is Go concurrency?"}]},
                ]
            results = multi_key_manager.execute_parallel_with_keys(args.provider, tasks)
            print(f"Parallel results with {args.provider} ({len(results)} tasks):")
            for r in results:
                print(
                    f" - Task {r.get('task_id')}: success={r.get('success')} "
                    f"model={r.get('model')} key={r.get('api_key')} "
                    f"response={str(r.get('response') or r.get('error',''))[:150]}"
                )
        elif args.multikey_action == "health":
            results = multi_key_manager.check_all_health(args.provider)
            ok = sum(1 for r in results if r.get("healthy"))
            print(f"\nKey health — {ok}/{len(results)} healthy\n")
            for r in results:
                mark = "✅" if r.get("healthy") else "❌"
                mp = r.get("models_probe") or {}
                print(
                    f" {mark} {r.get('provider')}/{r.get('key_name','')} — {r.get('status', 'ok' if r.get('healthy') else 'bad')}"
                    f" | latency={r.get('latency_ms')}ms | model={r.get('model_tested')}"
                )
                print(f"     base_url={r.get('base_url') or '(preset)'} | models found={mp.get('count', 0)}"
                      f"{(' | sample: ' + ', '.join((mp.get('sample') or [])[:6])) if mp.get('sample') else ''}")
                if r.get("error"):
                    print(f"     error: {str(r['error'])[:200]}")
        elif args.multikey_action == "models":
            result = multi_key_manager.discover_models(
                args.provider,
                api_key=args.key,
                base_url=getattr(args, "base_url", None),
            )
            if result.get("success"):
                print(f"✅ {result.get('count', 0)} models for {args.provider} (base_url={result.get('base_url') or '(preset)'}):")
                for m in (result.get("models") or [])[:80]:
                    print(f"   - {m.get('id') if isinstance(m, dict) else m}")
            else:
                print(f"❌ Could not list models: {result.get('error', 'unknown error')}")
        elif args.multikey_action == "rates":
            rates = multi_key_manager.rate_status(args.provider)
            print("\nRate limits (RPM/TPM used vs limit):")
            for k in rates.get("keys") or []:
                mark = "✅" if k.get("healthy") else ("❌" if k.get("healthy") is False else "❓")
                rt = f"{k['avg_response_time']:.2f}s" if k.get("avg_response_time") is not None else "—"
                print(
                    f" {mark} {k.get('provider')}/{k.get('name','')} | RPM {k.get('rpm_used',0)}/{k.get('rpm_limit') or '∞'}"
                    f" | TPM {k.get('tpm_used',0)}/{k.get('tpm_limit') or '∞'}"
                    f" | avg response {rt} | model={k.get('default_model')}"
                )
        elif args.multikey_action == "providers":
            for p in list_providers():
                print(f" - {p['id']}: {p['name']} | default={p.get('default_model')} | {p.get('base_url')}")
                if p.get("notes"):
                    print(f"     {p['notes']}")
        else:
            parser.parse_args(["multikey", "--help"])

    elif args.command == "fleet":
        from core.model_fleet import model_fleet
        import json as json_lib

        def _split(s):
            return [x.strip() for x in (s or "").split(",") if x.strip()] or None

        if args.fleet_action == "workers":
            w = model_fleet.list_workers(models=_split(args.models), providers=_split(args.providers))
            print(f"\nFleet workers ({w.get('count', 0)}):")
            for worker in w.get("workers") or []:
                print(f" - {worker.get('name')}: {worker.get('provider')}/{worker.get('model')}"
                      f" | key={'yes' if worker.get('has_key') else 'no'}"
                      f" | base_url={worker.get('base_url') or '(preset)'}")
            if w.get("providers_configured"):
                print(f"Providers configured: {', '.join(w['providers_configured'])}")
        elif args.fleet_action == "run":
            result = model_fleet.auto_distribute(
                args.goal,
                strategy=args.strategy,
                models=_split(args.models),
                providers=_split(args.providers),
                max_workers=args.workers,
            )
            print(f"\nFleet run: mode={result.get('mode')} strategy={result.get('strategy') or args.strategy}"
                  f" | success={result.get('success')} workers_used={result.get('workers_used') or len(result.get('results') or [])}")
            if result.get("subtasks"):
                print("Subtasks:")
                for i, s in enumerate(result["subtasks"], 1):
                    print(f"   {i}. {s}")
            if result.get("consensus"):
                print("\n=== CONSENSUS ===\n", result["consensus"][:3000])
            elif result.get("merged"):
                print("\n=== MERGED ===\n", result["merged"][:3000])
            elif result.get("winner"):
                print("\n=== WINNER ===\n", (result["winner"].get("response") or "")[:3000])
            else:
                for r in (result.get("results") or [])[:5]:
                    print(f"\n--- {r.get('model')} success={r.get('success')} ---")
                    print((r.get("response") or r.get("error") or "")[:800])
            if result.get("error"):
                print(f"\n⚠️ {result['error']}")
        elif args.fleet_action == "fanout":
            result = model_fleet.fanout(args.prompt, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers)
            print("Workers:", result.get("workers_used"), "success:", result.get("success"))
            if result.get("consensus"):
                print("\n=== CONSENSUS ===\n", result["consensus"][:4000])
        elif args.fleet_action == "map":
            result = model_fleet.map_goal(args.goal, models=_split(args.models), providers=_split(args.providers), max_workers=args.workers)
            print("Subtasks:")
            for i, s in enumerate(result.get("subtasks") or [], 1):
                print(f"   {i}. {s}")
            if result.get("merged"):
                print("\n=== MERGED ===\n", result["merged"][:4000])
        else:
            parser.parse_args(["fleet", "--help"])

    elif args.command == "multiai":
        from core.multi_ai import multi_ai_manager, PERSONA_PRESETS
        if args.multiai_action == "debate":
            from core.multi_ai import MultiAIChat
            chat = MultiAIChat()
            # Add agents based on personas or default team
            if args.agents:
                for persona_name in args.agents:
                    persona_desc = PERSONA_PRESETS.get(persona_name, f"You are a {persona_name}")
                    chat.add_agent(persona_name, persona_desc, model=args.model)
            else:
                chat.add_default_team(model=args.model)
            result = chat.debate(args.topic, rounds=args.rounds, model=args.model)
            print(f"\n=== Multi-AI Debate: {result['topic']} ===")
            print(f"Agents: {', '.join(result['agents'])} | Rounds: {result['rounds']}")
            for turn in result['history']:
                print(f"\n[{turn['agent']} - Round {turn['round']}]: {turn['content'][:500]}")
            print(f"\n=== Final Answer ===\n{result['final_answer']}")
        elif args.multiai_action == "chat":
            from core.multi_ai import MultiAIChat
            chat = MultiAIChat()
            chat.add_default_team(model=args.model)
            result = chat.collaborate_on_task(args.task, rounds=args.rounds)
            print(f"\n=== Multi-AI Collaborative Chat: {result['task']} ===")
            for turn in result['history']:
                print(f"\n[{turn['agent']} - R{turn['round']}]: {turn['content'][:500]}")
            print(f"\n=== Final ===\n{result['final']}")
        elif args.multiai_action == "personas":
            print("Persona presets free:")
            for name, desc in PERSONA_PRESETS.items():
                print(f" - {name}: {desc}")
        else:
            parser.parse_args(["multiai", "--help"])

    elif args.command == "counsel":
        import json as _json

        from core.counsel.constitution import constitution
        from core.counsel.council import CouncilSession
        from core.counsel.meta import meta_counsel

        if args.counsel_action == "run":
            result = CouncilSession(
                args.goal,
                model=args.model,
                difficulty=args.difficulty,
                max_members=args.members,
                max_rounds=args.rounds,
                execute=not args.no_execute,
            ).run()
            print(f"\n{'='*60}")
            print(f"⚖️ COUNSEL SESSION: {result['session_id']}")
            print(f"   Difficulty: {result['difficulty']} | Members: "
                  f"{', '.join(m['name'] for m in result['members'])}")
            votes = result.get("votes")
            if votes:
                print("   Votes:")
                for k, v in votes.items():
                    print(f"     - {k}: {str(v)[:100]}")
            print(f"{'='*60}")
            if result.get("plan") and result["plan"].get("steps"):
                print("\n📋 VOTED PLAN:")
                for i, s in enumerate(result["plan"]["steps"], 1):
                    print(f"   {i}. [{s.get('status','pending')}] {s.get('goal','')}")
            print(f"\n✅ FINAL ANSWER:\n{result['final_answer']}")
            if result.get("errors"):
                print(f"\n⚠️  Council errors: {result['errors']}")
        elif args.counsel_action == "status":
            st = meta_counsel.status()
            print(f"Council status — constitution v{st['constitution']['version']} ({st['constitution']['name']})")
            print(f"  Members: {', '.join(st['constitution']['members'])}")
            rules = st.get("constitution", {}).get("rules", {})
            if rules:
                print("  Rules:")
                for k, v in rules.items():
                    print(f"    - {k}: {str(v)[:120]}")
            budget = st.get("constitution", {}).get("budget", {})
            if budget:
                print(f"  Budget: max_members={budget.get('max_members')}, max_rounds={budget.get('max_rounds')}")
            print(f"  Pending amendments: {st['pending_amendments']}")
            print(f"  Meta reviews logged: {st['reviews_logged']}")
            print(f"  Upgrade events: {st['constitution']['upgrade_events']}")
            for ev in constitution.upgrade_log()[-5:]:
                print(f"    - {ev.get('event')} v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} {ev.get('timestamp','')[:19]}")
        elif args.counsel_action == "amend":
            if args.counsel_amend_action == "list":
                pending = constitution.pending_amendments()
                print(f"Pending amendments ({len(pending)}):")
                for p in pending:
                    print(f"  - [{p['id']}] target={p.get('target')} risk=high | {p.get('change','')[:120]}")
                    print(f"      reason: {p.get('reason','')[:180]}")
                print(f"\nUpgrade history (last 10):")
                for ev in constitution.upgrade_log()[-10:]:
                    print(f"  - {ev.get('event')} | v{ev.get('new_version') or ev.get('version') or ev.get('to_version')} | {ev.get('reason','')[:100]} | {ev.get('timestamp','')[:19]}")
                print("\nUse: hermus counsel amend approve <id> | reject <id> | rollback <version>")
            elif args.counsel_amend_action == "approve":
                res = constitution.approve(args.amendment_id)
                print(f"Approve result: {res}")
            elif args.counsel_amend_action == "reject":
                res = constitution.reject(args.amendment_id)
                print(f"Reject result: {res}")
            elif args.counsel_amend_action == "rollback":
                res = constitution.rollback(args.version)
                print(f"Rollback result: {res}")
            else:
                parser.parse_args(["counsel", "amend", "--help"])
        elif args.counsel_action == "review":
            session_id = args.session_id
            if not session_id:
                import glob

                files = sorted(glob.glob(str(config.resolve_path("data/counsel/sessions/*.json"))))
                if not files:
                    print("No council sessions yet — run `hermus counsel run \"task\"` first")
                    sys.exit(1)
                session_id = Path(files[-1]).stem
            from pathlib import Path as _P

            summary = _json.loads(_P(config.resolve_path(f"data/counsel/sessions/{session_id}.json")).read_text())
            res = meta_counsel.review_session(summary)
            print(f"Meta-Counsel review of {session_id}: proposed {res['proposed']} amendment(s)")
            for r in res.get("results", []):
                print(f"  - {r}")
        else:
            parser.parse_args(["counsel", "--help"])

    elif args.command == "eval":
        import json as _json

        from core.reasoning.eval import eval_harness

        if args.eval_action == "run":
            tasks = eval_harness.load_tasks()
            if args.category:
                tasks = [t for t in tasks if t.get("category") == args.category]
                print(f"Eval tasks filtered to category '{args.category}': {len(tasks)}")
            res = eval_harness.run(strategy=args.strategy, tasks=tasks, limit=args.limit, model=args.model)
            print(f"\n=== Eval run: strategy={res.get('strategy')} ===")
            print(f"  {res.get('success')}/{res.get('runs')} passed | success_rate={res.get('success_rate')} | "
                  f"avg_steps={res.get('avg_steps')} | avg_tool_failures={res.get('avg_tool_failures')}")
            for cat, c in (res.get("by_category") or {}).items():
                print(f"  {cat:12s} {c['success']}/{c['runs']}")
            for r in res.get("results", []):
                print(f"  [{'✅' if r['success'] else '❌'}] {r['id']:14s} ({r['strategy']:16s}) steps={r['steps']} fails={r['tool_failures']}")
        elif args.eval_action == "list":
            for t in eval_harness.load_tasks():
                print(f"  {t['id']:16s} {t['category']:10s} {t['prompt'][:70]}")
        elif args.eval_action == "compare":
            res = eval_harness.compare(args.a, args.b, limit=args.limit, model=args.model)
            print(f"Compare {args.a} vs {args.b}: WINNER = {res['winner']}")
            print(f"  {args.a}: {res['a']}")
            print(f"  {args.b}: {res['b']}")
            print("Tip: rerun with different strategies or --limit to get stable results on real models.")
        elif args.eval_action == "history":
            h = eval_harness.history(limit=args.limit)
            if not h:
                print("No eval runs yet — `hermus eval run`")
            for run in h:
                print(f"  {run.get('timestamp','')[:19]} | {str(run.get('strategy'))[:16]:16s} | "
                      f"rate={run.get('success_rate')} | runs={run.get('runs')} | tag={run.get('tag','')}")
        else:
            parser.parse_args(["eval", "--help"])

    elif args.command == "plan":
        import json as _json

        from core.reasoning.scaffold import list_plans, resume_plan, show_plan

        if args.plan_action == "list":
            plans = list_plans()
            if not plans:
                print("No plans saved yet — ask a multi-step task (DeepThink on) or run `hermus counsel run`.")
            for p in plans:
                print(f"  {p['session_id'][:38]:38s} steps={p['steps']} done={p['done']} "
                      f"status={p['status']} | {p['goal']}")
        elif args.plan_action == "show":
            plan = show_plan(args.session_id)
            if not plan:
                print(f"No plan found for '{args.session_id}'. Try `hermus plan list`")
            else:
                print(plan.to_prompt())
        elif args.plan_action == "resume":
            res = resume_plan(args.session_id, model=args.model)
            print(f"Resume result: success={res.get('success')} remaining_before={res.get('remaining_before')}")
            print(f"Response: {str(res.get('response'))[:400]}")
            if res.get("plan") and res["plan"].get("steps"):
                print("Plan state:")
                for i, s in enumerate(res["plan"]["steps"], 1):
                    print(f"   {i}. [{s.get('status', '?')}] {s.get('goal', '')[:60]}")
        else:
            parser.parse_args(["plan", "--help"])

    elif args.command == "api":
        from core.custom_api import custom_api_manager
        import json
        if args.api_action == "add":
            # Parse headers
            headers = {}
            if args.header:
                for h in args.header:
                    if ":" in h:
                        k, v = h.split(":", 1)
                        headers[k.strip()] = v.strip()
            # Parse params
            params = {}
            if args.param:
                for p in args.param:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        params[k.strip()] = {"type": "string", "description": v.strip()}
                    else:
                        params[p.strip()] = {"type": "string", "description": f"Parameter {p}"}
            # Auth
            auth = {"type": args.auth_type}
            if args.auth_type == "bearer":
                auth["token"] = args.auth_token
            elif args.auth_type == "apikey":
                auth["key"] = args.auth_key or "X-API-Key"
                auth["value"] = args.auth_token
            elif args.auth_type == "basic":
                auth["username"] = args.auth_key
                auth["password"] = args.auth_password

            api_def = {
                "name": args.name,
                "description": args.description,
                "url": args.url,
                "method": args.method,
                "headers": headers,
                "parameters": params,
                "auth": auth
            }
            result = custom_api_manager.add_api(api_def)
            print(f"Add custom API result: {result}")
            if result.get("success"):
                print(f"✅ Custom API '{args.name}' added! Agent can now use it as tool.")
                print(f"   Try: hermus --model mock/mock and say 'Use {args.name} with ...'")

        elif args.api_action == "list":
            apis = custom_api_manager.list_apis()
            print(f"Custom APIs ({len(apis)}):")
            for api in apis:
                print(f" - {api['name']}: {api['description']} | {api['method']} {api['url']} | enabled={api.get('enabled',True)}")
                if api.get("parameters"):
                    print(f"    Params: {list(api['parameters'].keys())}")

        elif args.api_action == "remove":
            result = custom_api_manager.remove_api(args.name)
            print(f"Remove result: {result}")

        elif args.api_action == "test":
            import json as json_lib
            test_args = {}
            if args.args:
                try:
                    test_args = json_lib.loads(args.args)
                except:
                    print(f"Failed to parse --args JSON: {args.args}, using empty")
            result = custom_api_manager.execute_api(args.name, test_args)
            print(f"Test {args.name} with {test_args}:")
            if not result.get("success"):
                print(f"❌ error: {result.get('error')}")
                print(f"   url: {result.get('url')}")
            else:
                print(f"✅ HTTP {result.get('status_code')}")
                print(f"   url: {result.get('url')}")
                data = result.get("data_str") or result.get("data")
                if data:
                    print(f"   data: {str(data)[:1000]}")
                if result.get("used_key"):
                    print(f"   key used: {result['used_key']}")
                    print(f"   keys for this API: {result.get('total_keys_for_this_api')}")

        elif args.api_action == "discover":
            from tools.public_apis import public_api_catalog

            result = public_api_catalog.search(
                query=args.query,
                category=args.category,
                auth=args.auth,
                https_only=not args.allow_http,
                cors=args.cors,
                limit=args.limit,
                refresh=args.refresh,
            )
            refresh = result.get("refresh")
            if refresh and not refresh.get("success"):
                print(f"⚠️ Refresh failed; using {refresh.get('using_fallback')}: {refresh.get('error')}")
            print(
                f"Public APIs matching '{args.query or '*'}': "
                f"{result.get('total_matched', 0)} found, showing {result.get('count', 0)}"
            )
            for item in result.get("results", []):
                print(f"\n - {item['name']} [{item['category']}]")
                print(f"   {item['description']}")
                print(
                    f"   auth={item['auth']} https={item['https']} cors={item['cors']} "
                    f"| {item['documentation_url']}"
                )
            catalog = result.get("catalog", {})
            print(
                f"\nSource: {catalog.get('source')} ({catalog.get('loaded_from')}, "
                f"{catalog.get('total_apis')} APIs)"
            )
            print("Note: links are documentation, not trusted endpoints. Review the docs, then use `hermus api add`.")

        elif args.api_action == "categories":
            from tools.public_apis import public_api_catalog

            result = public_api_catalog.categories()
            print(f"Public API categories ({result.get('count', 0)}):")
            for item in result.get("categories", []):
                print(
                    f" - {item['category']}: total={item['total']} "
                    f"no-auth={item['no_auth']} https={item['https']} cors={item['cors_yes']}"
                )

        elif args.api_action == "refresh-catalog":
            from tools.public_apis import public_api_catalog

            result = public_api_catalog.refresh()
            if result.get("success"):
                print(
                    f"✅ Refreshed {result.get('count')} APIs across "
                    f"{result.get('categories')} categories from {result.get('source')}"
                )
                print(f"   Runtime cache: {result.get('cache_path')}")
            else:
                print(f"❌ Refresh failed: {result.get('error')}")
                print(
                    f"   Continuing with {result.get('using_fallback')} "
                    f"({result.get('fallback_count')} APIs)"
                )

        else:
            parser.parse_args(["api", "--help"])

    elif args.command == "mcp":
        from core.mcp_client import mcp_manager
        import json as json_lib
        if args.mcp_action == "list":
            servers = mcp_manager.list_servers()
            print(f"MCP servers ({len(servers)}):")
            for s in servers:
                print(f" - {s.get('name')}: cmd={s.get('command')} enabled={s.get('enabled')} running={s.get('running')} tools={s.get('tool_count')}")
                if s.get("last_error"):
                    print(f"   error: {s['last_error']}")
        elif args.mcp_action == "add":
            result = mcp_manager.add_server(
                args.name,
                args.command,
                args=args.arg or [],
                enabled=not args.disabled,
            )
            print(result)
            print("Tip: hermus mcp connect  # register tools on agent")
        elif args.mcp_action == "remove":
            print(mcp_manager.remove_server(args.name))
        elif args.mcp_action == "connect":
            result = mcp_manager.connect_enabled()
            from core.tool_registry import tool_registry
            tool_registry.load(force=True)
            info = tool_registry.list_tools()
            mcp_tools = [t for t in info.get("tools", []) if t.startswith("mcp_")]
            print(result)
            print(f"Registered MCP tools ({len(mcp_tools)}): {mcp_tools}")
        elif args.mcp_action == "call":
            try:
                call_args = json_lib.loads(args.args)
            except Exception:
                call_args = {}
            result = mcp_manager.call(args.server, args.tool, call_args)
            print(f"MCP call {args.server}/{args.tool} {call_args}:")
            if isinstance(result, dict):
                if result.get("error"):
                    print(f"❌ {result['error']}")
                else:
                    for k, v in result.items():
                        print(f"   {k}: {str(v)[:300]}")
            else:
                print(str(result)[:2000])
        else:
            parser.parse_args(["mcp", "--help"])

    elif args.command == "embed":
        from core.embeddings import embedding_store
        if args.embed_action == "status":
            info = embedding_store.backend_info()
            print(f"Embeddings backend: {info.get('backend')}")
            print(f"  model: {info.get('model')} | dim: {info.get('dim')}")
            print(f"  stored embeddings: {info.get('count')}")
            print(f"  db: {info.get('db')}")
        elif args.embed_action == "ingest":
            result = embedding_store.ingest_path(args.path, source=args.source)
            if result.get("success") is False or result.get("error"):
                print(f"❌ {result.get('error', 'ingest failed')}")
            else:
                print(f"✅ Ingested {args.path}")
                print(f"  files: {result.get('files') or result.get('ingested_files')}")
                print(f"  chunks added: {result.get('chunks') or result.get('total_chunks')}")
                print(f"  total embeddings: {result.get('count') or result.get('total')}")
                for err in (result.get("errors") or [])[:5]:
                    print(f"  ⚠️ {err}")
        elif args.embed_action == "search":
            if args.semantic_only:
                result = embedding_store.search(args.query, limit=args.limit)
            else:
                result = embedding_store.hybrid_search(args.query, limit=args.limit)
            print(f"Results for '{args.query}' (mode={result.get('mode', 'semantic')}):")
            for i, r in enumerate(result.get("results") or [], 1):
                score = r.get("score")
                score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
                print(f"\n #{i} score={score_txt} source={r.get('source', '?')}")
                print(f"    {(r.get('content') or '')[:400]}")
            if result.get("summary"):
                print(f"\nSummary: {result['summary'][:500]}")
            if result.get("error"):
                print(f"⚠️ {result['error']}")
        elif args.embed_action == "clear":
            print(embedding_store.clear(source=args.source))
        else:
            parser.parse_args(["embed", "--help"])

    elif args.command == "tools":
        from core.tool_registry import tool_registry
        info = tool_registry.list_tools()
        print(f"Registered tools: {info['count']} defs={info['definitions']}")
        for t in info.get("tools", []):
            src = info.get("sources", {}).get(t, "")
            print(f" - {t}  [{src}]")
        if info.get("errors"):
            print("Load errors:")
            for e in info["errors"]:
                print(f"   ! {e}")

    elif args.command == "workspace":
        from core.workspace import workspace as ws
        if args.ws_action == "layout":
            print(f"Workspace base: {ws.base_dir}")
            for name, p in ws.dirs.items():
                print(f"  {name:12s} {p}")
        elif args.ws_action == "create":
            r = ws.create_project(args.name, description=args.description)
            print(f"{'✅' if r.get('success') else '❌'} {r.get('name') or r.get('error')} -> {r.get('path','')}")
        elif args.ws_action == "list":
            projects = ws.list_projects()
            print(f"Projects ({len(projects)}):")
            for p in projects:
                cur = " *" if p.get("name") == ws.current_project() else ""
                print(f" - {p.get('name')}{cur} | {p.get('description','')}")
        elif args.ws_action == "use":
            r = ws.set_current_project(args.name)
            print(f"{'✅' if r.get('success') else '❌'} current project: {r.get('name') or r.get('error')}")
        else:
            parser.parse_args(["workspace", "--help"])

    elif args.command == "mem2":
        from core.memory2 import memory2
        if args.mem2_action == "remember":
            success = None if args.success == "none" else (args.success == "true")
            r = memory2.remember(args.kind, args.content, importance=args.importance, success=success)
            print(f"{'✅' if r.get('success') else '❌'} {args.kind} memory {'merged' if r.get('merged') else 'saved'} id={r.get('id')}")
        elif args.mem2_action == "recall":
            res = memory2.recall(args.query, limit=args.limit)
            if not res:
                print("No memories found.")
            for m in res:
                print(f" [{m['kind']:10s}] score={m['score']:.3f} | {m['content'][:120]}")
        else:
            parser.parse_args(["mem2", "--help"])

    elif args.command == "router":
        from core.router2 import router2
        sel = router2.select(args.text)
        print(f"Task type : {sel['task_type']} (difficulty {sel['difficulty']}, ~{sel['context_tokens']} tokens)")
        print(f"Model     : {sel['model']}")
        print(f"Reason    : {sel['reason']}")
        if sel.get("alternatives"):
            print(f"Alt       : {', '.join(sel['alternatives'])}")

    elif args.command == "run":
        from core.agent import HermusAgent

        agent = HermusAgent(model=args.model)
        report = agent.autonomous(args.task, max_repairs=args.max_repairs)
        print(f"Autonomous run: status={report['status']} verified={report['verified']} repairs={report['repairs']}")
        for s in report["steps"]:
            print(f"  [{s['status']}] {s['goal'][:70]} (attempts={s['attempts']})")
        print(f"\nResult:\n{str(report['final_answer'])[:1500]}")

    elif args.command == "agent":
        from core.agent_manager import agent_manager
        if args.agent_action == "create":
            r = agent_manager.create(args.name, role=args.role, model=args.model)
            print(f"{'✅' if r.get('success') else '❌'} {r.get('name') or r.get('error')} (role={r.get('role','')})")
        elif args.agent_action == "start":
            r = agent_manager.start(args.name)
            print(f"{'✅' if r.get('success') else '❌'} {args.name} started (pid={r.get('pid')})" if r.get("success") else f"❌ {r.get('error')}")
        elif args.agent_action == "status":
            s = agent_manager.status(args.name)
            if not s.get("success"):
                print(f"❌ {s.get('error')}")
            else:
                print(f" {args.name} | role={s.get('role')} status={s.get('status')} pid={s.get('pid')} alive={s.get('alive')}")
                print(f"   heartbeat={s.get('heartbeat')} jobs_done={s.get('jobs_done')} last={s.get('last_result')}")
        elif args.agent_action == "stop":
            r = agent_manager.stop(args.name)
            print(f"{'✅' if r.get('success') else '❌'} {args.name} stopped")
        elif args.agent_action == "job":
            r = agent_manager.submit_job(args.name, {"task": " ".join(args.task)})
            if r.get("success") and args.wait:
                r = agent_manager.wait_job(args.name, r["job_id"], timeout=args.timeout)
            print(__import__("json").dumps(r, indent=2, default=str))
        elif args.agent_action == "result":
            print(__import__("json").dumps(
                agent_manager.job_status(args.name, args.job_id), indent=2, default=str
            ))
        elif args.agent_action == "list":
            agents = agent_manager.list()
            if not agents:
                print("No agents. Create one: hermus agent create researcher --role researcher")
            for a in agents:
                print(f" - {a.get('name')} | role={a.get('role')} status={a.get('status')} alive={a.get('alive')}")
        else:
            parser.parse_args(["agent", "--help"])

    elif args.command == "perms":
        from core.permissions import permission_manager
        if args.perms_action == "check":
            r = permission_manager.check(args.tool)
            print(f"{args.tool}: risk={r['risk']} decision={r['decision']}")
        elif args.perms_action == "set":
            r = permission_manager.set_policy(args.tool, args.decision, agent=args.agent)
            print(f"{'✅' if r.get('success') else '❌'} {args.tool} -> {args.decision}" + (f" (agent={args.agent})" if args.agent else ""))
        elif args.perms_action == "list":
            for e in permission_manager.recent():
                print(f" {e.get('ts','')[:19]} {e.get('tool')} -> {e.get('decision')} (risk={e.get('risk')}, agent={e.get('agent')})")
        else:
            parser.parse_args(["perms", "--help"])

    elif args.command == "research":
        from core.research import research_pipeline
        out = research_pipeline.run(args.query)
        print(f"\n=== RESEARCH: {args.query} ===\n{out['answer']}\n")
        print(f"Confidence: {out['confidence']}")
        print(f"Sources ({len(out['sources'])}):")
        for s in out["sources"]:
            print(f" - [{s['rank']}] {s['title']} ({s['url']})")
        if out.get("contradictions"):
            print(f"\n⚠️ Contradictions ({len(out['contradictions'])}):")
            for c in out["contradictions"]:
                print(f"   A: {c['a'][:90]} [{c['source_a']}]")
                print(f"   B: {c['b'][:90]} [{c['source_b']}]")
        if out.get("uncertain"):
            print(f"\nUncertain claims: {out['uncertain'][:3]}")

    elif args.command == "screen":
        import json
        from core.computer.service import ScreenRecordingService

        service = ScreenRecordingService()
        # Normalize short and explicit record forms to one action.
        action = args.record_action if args.screen_action == "record" else args.screen_action
        if action == "start":
            result = service.start(
                fps=args.fps,
                max_seconds=args.buffer_seconds,
                container=args.format,
            )
            print(json.dumps(result, indent=2, default=str))
        elif action == "stop":
            print(json.dumps(service.stop(), indent=2, default=str))
        elif action == "status":
            print(json.dumps(service.status(), indent=2, default=str))
        elif action == "save":
            print(json.dumps(service.save(args.target), indent=2, default=str))
        elif action == "analyze":
            from core.computer import VideoAnalyzer

            state = service.status()
            video = args.video or state.get("output_path") or ""
            analyzer = VideoAnalyzer() if args.no_vision else VideoAnalyzer.with_ollama(args.model)
            result = analyzer.analyze_video(
                video,
                task=args.task,
                sample_fps=args.sample_fps,
                max_seconds=args.max_seconds,
                max_events=args.max_events,
            )
            if result.get("success") and video:
                source = Path(video).expanduser().resolve()
                timeline_path = source.parent / "timeline.json"
                events_path = source.parent / "events.json"
                timeline_path.write_text(json.dumps(result.get("timeline", {}), indent=2), encoding="utf-8")
                events_path.write_text(json.dumps(result.get("events", []), indent=2), encoding="utf-8")
                result["timeline_path"] = str(timeline_path)
                result["events_path"] = str(events_path)
            print(json.dumps(result, indent=2, default=str))
        elif action == "watch":
            from core.computer import ScreenRecorder, ScreenWatcher, VideoAnalyzer

            recorder = ScreenRecorder(fps=args.fps, max_seconds=max(10.0, args.timeout))
            analyzer = VideoAnalyzer.with_ollama(args.model)
            result = ScreenWatcher(recorder, analyzer=analyzer).watch(
                args.condition,
                timeout=args.timeout,
                start_if_needed=True,
            )
            print(json.dumps(result, indent=2, default=str))
        else:
            parser.parse_args(["screen", "--help"])

    elif args.command == "computer":
        import json
        from core.computer import (
            ComputerActionController,
            ComputerAgent,
            ControlCenter,
            ScreenRecorder,
            ScreenWatcher,
            TargetDetector,
            TaskStore,
            VideoAnalyzer,
            emergency_stop,
        )

        def build_computer_agent(model=None, retries=2, learn_skills=True):
            analyzer = VideoAnalyzer.with_ollama(model) if model else None
            recorder = ScreenRecorder(fps=2.0, max_seconds=120.0)
            controller = ComputerActionController(
                frame_provider=recorder.latest,
                target_detector=TargetDetector(vision_model=analyzer.vision_model if analyzer else None),
            )
            return ComputerAgent(
                controller=controller,
                recorder=recorder,
                analyzer=analyzer,
                learn_skills=learn_skills,
                max_retries=retries,
            )

        if args.computer_action in ("task", "run"):
            task = " ".join(args.task)
            computer_agent = build_computer_agent(
                model=args.model,
                retries=args.retries,
                learn_skills=not args.no_skill,
            )
            result = computer_agent.run(task, task_id=args.task_id, dry_run=args.dry_run)
            print(json.dumps(result, indent=2, default=str))
        elif args.computer_action == "resume":
            computer_agent = build_computer_agent(model=args.model, retries=args.retries)
            print(json.dumps(computer_agent.resume(args.task_id, dry_run=args.dry_run), indent=2, default=str))
        elif args.computer_action == "tasks":
            print(json.dumps(TaskStore().list(), indent=2, default=str))
        elif args.computer_action == "show":
            checkpoint = TaskStore().load(args.task_id)
            print(json.dumps(checkpoint.to_dict() if checkpoint else {
                "success": False, "error": f"task '{args.task_id}' not found"
            }, indent=2, default=str))
        elif args.computer_action == "delegate":
            from core.computer import MultiAgentDelegator

            delegator = MultiAgentDelegator()
            delegation_plan = delegator.plan(" ".join(args.task))
            result = delegator.execute(
                delegation_plan,
                wait=not args.no_wait,
                timeout_per_unit=args.timeout,
                dry_run=args.dry_run,
            )
            print(json.dumps(result, indent=2, default=str))
        elif args.computer_action == "stop":
            emergency_stop.halt()
            print(json.dumps({"success": True, "halted": True,
                              "note": "mouse/keyboard/autonomous control halted. Release in-process or restart."}, indent=2))
        elif args.computer_action == "status":
            print(ControlCenter(ComputerActionController()).render())
        elif args.computer_action == "target":
            target = " ".join(args.target)
            analyzer = VideoAnalyzer.with_ollama(args.model)
            recorder = ScreenRecorder(fps=2.0, max_seconds=10.0)
            recorder.start()
            try:
                detector = TargetDetector(vision_model=analyzer.vision_model)
                controller = ComputerActionController(frame_provider=recorder.latest, target_detector=detector)
                print(json.dumps(controller.find_on_screen(target), indent=2, default=str))
            finally:
                recorder.stop()
        elif args.computer_action == "click":
            target = " ".join(args.target)
            analyzer = VideoAnalyzer.with_ollama(args.model)
            recorder = ScreenRecorder(fps=2.0, max_seconds=10.0)
            recorder.start()
            try:
                detector = TargetDetector(vision_model=analyzer.vision_model)
                controller = ComputerActionController(frame_provider=recorder.latest, target_detector=detector)
                print(json.dumps(controller.click_target(target), indent=2, default=str))
            finally:
                recorder.stop()
        elif args.computer_action == "wait":
            condition = " ".join(args.condition)
            analyzer = VideoAnalyzer.with_ollama(args.model)
            recorder = ScreenRecorder(fps=2.0, max_seconds=max(10.0, args.timeout))
            result = ScreenWatcher(recorder, analyzer=analyzer).watch(
                condition, timeout=args.timeout, start_if_needed=True
            )
            print(json.dumps(result, indent=2, default=str))
        elif args.computer_action == "skills":
            from core.computer import ComputerSkillStore

            skills = ComputerSkillStore().list_skills()
            if not skills:
                print("No computer skills learned yet.")
            for skill in skills:
                print(
                    f" - {skill['name']}: {skill['task']} "
                    f"({skill['steps']} steps, {skill['successes']}/{skill['runs']} successful, "
                    f"rate={skill['success_rate']:.1%}, avg={skill['average_duration']:.1f}s, "
                    f"repairs={skill['known_repairs']})"
                )
        else:
            parser.parse_args(["computer", "--help"])

    elif args.command == "harness":
        import json as _json
        from core.harness import bus, sessions
        from core.harness.memory_graph import cascade_recall
        from core.harness.swarm import spawn as swarm_spawn

        if args.harness_action == "sessions":
            items = sessions.list_sessions()
            print(f"Sessions ({len(items)}):")
            for s in items:
                print(f" - {s.get('id')} | {s.get('status')} | role={s.get('role')} | {str(s.get('task') or '')[:70]}")
        elif args.harness_action == "attach":
            print(_json.dumps(sessions.attach(args.session_id), indent=2))
        elif args.harness_action == "detach":
            print(_json.dumps(sessions.detach(args.session_id), indent=2))
        elif args.harness_action == "send":
            kind = args.kind if not args.channel else "channel"
            print(_json.dumps(bus.send(args.body, args.sender, to=args.to or None,
                                       channel=args.channel or None, kind=kind), indent=2))
        elif args.harness_action == "inbox":
            print(_json.dumps(bus.inbox(args.session_id), indent=2, default=str))
        elif args.harness_action == "spawn":
            print(_json.dumps(swarm_spawn(args.task, args.parent, count=args.count), indent=2, default=str))
        elif args.harness_action == "recall":
            print(_json.dumps(cascade_recall(args.query), indent=2, default=str))
        else:
            parser.parse_args(["harness", "--help"])

    elif args.command == "watchdog":
        from core.watchdog import watchdog as wd
        err = args.error or "JSONDecodeError: expecting value"
        r = wd.handle(err)
        print(f"Known={r['known']} action={r['action']} ok={r.get('ok')} fix={r.get('fix','')}")

    elif args.command == "profile":
        from core.profiles import profile_manager
        if args.profile_action == "create":
            r = profile_manager.create(args.name, persona=args.persona)
            print(f"{'✅' if r.get('success') else '❌'} {r.get('name') or r.get('error')}")
            if r.get("success"):
                print(f"   persona: {r['persona']}")
        elif args.profile_action == "list":
            profiles = profile_manager.list()
            if not profiles:
                print("No profiles. Create one: hermus profile create coder")
            for p in profiles:
                print(f" - {p.get('name')} | {p.get('persona','')[:70]}")
        elif args.profile_action == "use":
            print(profile_manager.system_prompt(args.name))
        else:
            parser.parse_args(["profile", "--help"])

    elif args.command == "update":
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        if args.check:
            print("🔍 Checking for updates from GitHub...")
            result = updater.check_for_updates()
            if result.get("update_available"):
                print(f"\n🚀 Update available! {result.get('message')}")
                print(f"Local: {result.get('local',{}).get('short')} - {result.get('local',{}).get('message','')[:80]}")
                print(f"Remote: {result.get('remote',{}).get('short')} - {result.get('remote',{}).get('message','')[:80]} by {result.get('remote',{}).get('author','')} on {result.get('remote',{}).get('date','')[:10]}")
                print(f"Behind by: {result.get('behind_by',1)} commit(s)")
                print(f"Remote URL: {result.get('remote_url','')}")
                print("\nTo update: Run 'hermus update' without --check or 'git pull' - shows in dashboard and CLI")
                print("Dashboard will show banner if update available at http://localhost:8000/dashboard")
            elif result.get("up_to_date"):
                print(f"\n✅ Up to date! {result.get('message')}")
            else:
                print(f"\nUpdate check result: {result}")
        else:
            print("🔄 Updating from GitHub via git pull origin main + pip install -r requirements.txt...")
            result = updater.update()
            if result.get("success"):
                print(f"\n✅ Update success! {result.get('message')}")
                print(f"New commit: {result.get('new_commit',{}).get('short')} - {result.get('new_commit',{}).get('message','')[:80]}")
                print("Pull output:", result.get("pull_stdout","")[:500])
                print("\nDashboard and CLI will now show up to date - refresh dashboard to see new version")
            else:
                print(f"\n❌ Update failed: {result.get('error', result.get('pull_stderr',''))[:500]}")

    else:
        # Default: start TUI - show update check on startup
        if args.profile:
            config.profile = args.profile
        print(f"Hermus Agent Free - Model {args.model} | Mode {args.mode}" + (f" | Profile {args.profile}" if args.profile else ""))
        # Check for updates on startup and show in CLI
        try:
            from core.updater import get_updater_for_current_repo
            updater = get_updater_for_current_repo()
            result = updater.check_for_updates()
            if result.get("update_available"):
                print(f"\n🚀 Update available! {result.get('message')}")
                print(f"   Run 'hermus update' to update - shows in dashboard and CLI")
                print(f"   Dashboard http://localhost:8000/dashboard will show banner")
        except:
            pass
        print("Starting TUI (full terminal interface with slash commands)...")
        print(f"Modes: agent can control everything, chat let's u chat, multi-agent can use multiple keys at once and reach goal no matter how difficult, multi-chat can get accurate reliable info with multiple ai models and api keys")
        try:
            from tui.tui import HermusTUI
            tui = HermusTUI(model=args.model, mode=args.mode)
            tui.run()
        except ImportError as e:
            print(f"TUI deps missing: {e} - pip install prompt_toolkit rich - falling back to simple agent")
            from core.agent import HermusAgent
            agent = HermusAgent(model=args.model, mode=args.mode)
            print(f"Simple chat in {args.mode} mode (type /exit to quit, /new for new session, /skills to list skills, /mode to switch)")
            while True:
                try:
                    text = input(f"\nYou [{args.mode}]> ").strip()
                    if not text:
                        continue
                    if text.lower() in ("/exit", "exit", "quit"):
                        break
                    if text.lower().startswith("/new"):
                        agent.new_session()
                        continue
                    if text.lower().startswith("/skills"):
                        from core.skill_manager import skill_manager
                        skills = skill_manager.list_skills()
                        print(f"Skills: {skills}")
                        continue
                    if text.lower().startswith("/mode"):
                        parts = text.split()
                        if len(parts) > 1:
                            new_mode = parts[1]
                            agent = HermusAgent(model=args.model, mode=new_mode)
                            print(f"Switched to {new_mode} mode: {agent.mode_config.description[:100]}")
                        else:
                            from core.modes import list_modes
                            modes = list_modes()
                            print(f"Current mode: {agent.mode.value} - {agent.mode_config.name}")
                            print("Available modes:")
                            for m, cfg in modes.items():
                                print(f" - {m}: {cfg['name']} - {cfg['description'][:80]}")
                        continue
                    result = agent.chat(text)
                    print(f"\nHermus [{args.mode}]> {result['response']}")
                    if result.get("skill_created", {}).get("created"):
                        print(f"[New skill: {result['skill_created']['name']}]")
                except KeyboardInterrupt:
                    print("\nUse /exit to quit")
                except Exception as e:
                    print(f"Error: {e}")

if __name__ == "__main__":
    main()
