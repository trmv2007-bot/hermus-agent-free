"""Config for Hermus Free - No paywalls"""
import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

class Config(BaseModel):
    # LLM Provider - free options
    # ollama/..., groq/..., hf/..., mock/...  (HERMUS_MODEL also scopes a whole
    # process tree — sub-agent workers inherit it.)
    model: str = os.getenv("HERMUS_MODEL", "ollama/llama3.1:8b")
    ollama_base_url: str = "http://localhost:11434"
    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    hf_token: Optional[str] = os.getenv("HF_TOKEN")
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    openrouter_api_key: Optional[str] = os.getenv("OPENROUTER_API_KEY")
    gemini_api_key: Optional[str] = os.getenv("GEMINI_API_KEY")
    anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")

    # Agent loop
    # Default tool-step budget per turn. 8 was far too tight for "build this
    # app, test it, fix every error, and keep going until it works" style
    # goals — the loop hit the cap and force-synthesized an answer mid-work.
    # 32 keeps simple chat cheap (the governor scales the per-task budget
    # down for easy tasks) while giving real work room to finish.
    max_tool_steps: int = int(os.getenv("HERMUS_MAX_TOOL_STEPS", "32"))

    # ---- Universal mission runtime -----------------------------------------
    # Route every execution surface (agent.autonomous(), /command?autonomous,
    # /stream/command, queue jobs, CLI, channels, scheduler) through the
    # MissionEngine runtime so behavior no longer depends on the entry point.
    mission_runtime_enabled: bool = os.getenv("HERMUS_MISSION_RUNTIME", "1") not in ("0", "false", "False")
    # Auto-promote goal-like messages ("build … and keep going until it works")
    # to full missions even when the caller did not set autonomous=true.
    mission_auto_classify: bool = os.getenv("HERMUS_MISSION_AUTO_CLASSIFY", "1") not in ("0", "false", "False")
    # Default step budget for a mission. A mission owns the whole lifecycle
    # (plan -> implement -> test -> inspect -> repair -> retest), so it must be
    # larger than a single agent turn (max_tool_steps), not smaller.
    mission_budget_steps: int = int(os.getenv("HERMUS_MISSION_BUDGET_STEPS", "48"))
    # When the mission runtime crashes, report MISSION FAILED with diagnostics.
    # Opt in to the old "answer with a chat turn instead" behaviour only for
    # interactive demos — it hides failures behind plausible prose.
    mission_fallback_to_chat: bool = os.getenv("HERMUS_MISSION_FALLBACK_TO_CHAT", "0") not in ("0", "false", "False")
    # Pre-flight model capability negotiation (tools/vision/context/...).
    model_capability_check: bool = os.getenv("HERMUS_MODEL_CAPABILITY_CHECK", "1") not in ("0", "false", "False")
    # Auto-select a compatible model when the selected one cannot do the job.
    auto_select_model: bool = os.getenv("HERMUS_AUTO_SELECT_MODEL", "0") not in ("0", "false", "False")


    # DeepThink — plan-first thinking (Phase 0)
    think_enabled: bool = os.getenv("HERMUS_THINK_ENABLED", "1") not in ("0", "false", "False")

    # DeepThink strategies + lessons loop (Phase 3)
    # auto | none | reflexion | self_consistency | verify
    think_strategy: str = os.getenv("HERMUS_STRATEGY", "auto")
    self_consistency_k: int = int(os.getenv("HERMUS_SELF_CONSISTENCY_K", "3"))
    verify_threshold: int = int(os.getenv("HERMUS_VERIFY_THRESHOLD", "4"))
    lessons_in_prompt: int = int(os.getenv("HERMUS_LESSONS_IN_PROMPT", "8"))

    # Project-scoped memory (Phase 4)
    project: str = os.getenv("HERMUS_PROJECT", "default")

    # Counsel System — council of AIs that plans together and upgrades itself (Phases 0-2)
    counsel_enabled: bool = os.getenv("HERMUS_COUNSEL_ENABLED", "1") not in ("0", "false", "False")
    counsel_min_difficulty: int = int(os.getenv("HERMUS_COUNSEL_MIN_DIFFICULTY", "4"))
    counsel_max_members: int = int(os.getenv("HERMUS_COUNSEL_MAX_MEMBERS", "6"))
    counsel_max_rounds: int = int(os.getenv("HERMUS_COUNSEL_MAX_ROUNDS", "3"))
    # Meta-Counsel reviews each session and proposes self-upgrades
    counsel_auto_review: bool = os.getenv("HERMUS_COUNSEL_AUTO_REVIEW", "1") not in ("0", "false", "False")

    # Memory
    memory_db_path: str = "data/memory.db"
    memory2_db_path: str = "data/memory2.db"
    user_model_path: str = "data/user_model.json"
    trajectory_path: str = "data/trajectories.jsonl"

    # Workspace — per-project isolation (agent OS layout)
    workspace_dir: str = os.getenv("HERMUS_HOME", "~/.hermus")

    # ---- Architecture upgrades (full wiring) -------------------------------
    # Permissions: enforce ALLOW/ASK/DENY on every tool call (always audited).
    permissions_enforce: bool = os.getenv("HERMUS_PERMISSIONS_ENFORCE", "1") not in ("0", "false", "False")
    # How an ASK decision resolves when no interactive prompt is attached.
    # "allow" (default, backward-compatible) | "deny" (strict / fail-safe).
    ask_policy: str = os.getenv("HERMUS_ASK_POLICY", "allow")
    # Memory 2.0: typed + scored recall injected into the system prompt + auto-persist.
    memory2_enabled: bool = os.getenv("HERMUS_MEMORY2_ENABLED", "1") not in ("0", "false", "False")
    # Model Router 2.0: per-turn model selection.
    router2_enabled: bool = os.getenv("HERMUS_ROUTER2_ENABLED", "1") not in ("0", "false", "False")
    # Autonomous verify/repair gate applied after the ReAct loop.
    autonomous_enabled: bool = os.getenv("HERMUS_AUTONOMOUS_ENABLED", "0") not in ("0", "false", "False")
    # Self-healing watchdog on task/tool failures (gateway + agent).
    watchdog_enabled: bool = os.getenv("HERMUS_WATCHDOG_ENABLED", "1") not in ("0", "false", "False")
    # Keep persistent background agents alive (gateway watchdog tick).
    background_agents_enabled: bool = os.getenv("HERMUS_BG_AGENTS_ENABLED", "1") not in ("0", "false", "False")
    # Active persona / profile name (independent memory + system prompt).
    profile: str = os.getenv("HERMUS_PROFILE", "")

    # Semantic memory / embeddings (free local)
    embeddings_db_path: str = "data/embeddings.db"
    embedding_model: str = os.getenv("HERMUS_EMBED_MODEL", "nomic-embed-text")
    # auto | ollama | hash  (hash = deterministic offline fallback, no probing)
    embedding_backend: str = os.getenv("HERMUS_EMBED_BACKEND", "auto")

    # ---- Hybrid memory + decay (retrieval upgrades) ------------------------
    # FTS5 BM25 + dense vectors fused with Reciprocal Rank Fusion for recall.
    memory_hybrid_enabled: bool = os.getenv("HERMUS_MEMORY_HYBRID", "1") not in ("0", "false", "False")
    # Store per-memory float32 embeddings (sqlite-vec if installed, else cosine scan).
    memory_vectors_enabled: bool = os.getenv("HERMUS_MEMORY_VECTORS", "1") not in ("0", "false", "False")
    # Recency half-life (days) for exponential decay; lengthens with access count.
    memory_half_life_days: float = float(os.getenv("HERMUS_MEMORY_HALF_LIFE_DAYS", "30"))
    # Never silence a memory completely: decay multiplier is floored here.
    memory_decay_floor: float = float(os.getenv("HERMUS_MEMORY_DECAY_FLOOR", "0.35"))
    # Token budget for the memories injected into the system prompt.
    memory_budget_tokens: int = int(os.getenv("HERMUS_MEMORY_BUDGET_TOKENS", "600"))
    memory_archive_below: float = float(os.getenv("HERMUS_MEMORY_ARCHIVE_BELOW", "0.08"))
    memory_purge_below: float = float(os.getenv("HERMUS_MEMORY_PURGE_BELOW", "0.02"))
    memory_working_ttl_hours: float = float(os.getenv("HERMUS_MEMORY_WORKING_TTL_HOURS", "48"))
    memory_rrf_k: int = int(os.getenv("HERMUS_MEMORY_RRF_K", "60"))
    memory_prior_weight: float = float(os.getenv("HERMUS_MEMORY_PRIOR_WEIGHT", "0.35"))
    memory_sweep_minutes: int = int(os.getenv("HERMUS_MEMORY_SWEEP_MINUTES", "60"))

    # ---- Skill forge: post-task trajectory → SKILL.md ----------------------
    skill_forge_enabled: bool = os.getenv("HERMUS_SKILL_FORGE", "1") not in ("0", "false", "False")
    skill_forge_min_tools: int = int(os.getenv("HERMUS_SKILL_FORGE_MIN_TOOLS", "3"))
    # LLM distillation is optional; a deterministic template is used without it.
    skill_forge_use_llm: bool = os.getenv("HERMUS_SKILL_FORGE_LLM", "1") not in ("0", "false", "False")
    skill_forge_dedupe_similarity: float = float(os.getenv("HERMUS_SKILL_FORGE_DEDUPE", "0.72"))
    skill_forge_max_skills: int = int(os.getenv("HERMUS_SKILL_FORGE_MAX", "200"))
    # Self-improvement safety: a procedure is only distilled into a skill after
    # it has been observed to succeed this many times in *independent* runs
    # (1 = learn from a single run, 2 = require a repeat — the default).
    skill_forge_min_repeats: int = int(os.getenv("HERMUS_SKILL_FORGE_MIN_REPEATS", "2"))
    # Require verified success (verifier verdict or clean independent evidence)
    # before harvesting — never learn a procedure from a failed/bogus run.
    skill_forge_require_verified: bool = os.getenv("HERMUS_SKILL_FORGE_REQUIRE_VERIFIED", "1") not in ("0", "false", "False")

    # ---- Gateway async queue + streaming ----------------------------------
    gateway_queue_enabled: bool = os.getenv("HERMUS_QUEUE_ENABLED", "1") not in ("0", "false", "False")
    gateway_queue_workers: int = int(os.getenv("HERMUS_QUEUE_WORKERS", "4"))
    gateway_queue_maxsize: int = int(os.getenv("HERMUS_QUEUE_MAXSIZE", "500"))
    gateway_queue_timeout: float = float(os.getenv("HERMUS_QUEUE_TIMEOUT", "300"))
    # inprocess | redis (redis is optional; falls back to inprocess)
    gateway_queue_retry_backoff: float = float(os.getenv("HERMUS_QUEUE_RETRY_BACKOFF", "1.5"))
    gateway_queue_cancel_grace: float = float(os.getenv("HERMUS_QUEUE_CANCEL_GRACE", "15"))
    gateway_queue_backend: str = os.getenv("HERMUS_QUEUE_BACKEND", "inprocess")
    # durable job log (results/ lives next to it); relative paths anchor to the repo
    gateway_jobs_log: str = os.getenv("HERMUS_QUEUE_LOG", "data/jobs/jobs.jsonl")
    redis_url: Optional[str] = os.getenv("REDIS_URL")
    gateway_stream_enabled: bool = os.getenv("HERMUS_STREAM_ENABLED", "1") not in ("0", "false", "False")
    gateway_stream_tokens: bool = os.getenv("HERMUS_STREAM_TOKENS", "1") not in ("0", "false", "False")

    # ---- Tool sandboxing ---------------------------------------------------
    # auto | docker | podman | gvisor | local | off
    sandbox_mode: str = os.getenv("HERMUS_SANDBOX", "auto")
    sandbox_image: str = os.getenv("HERMUS_SANDBOX_IMAGE", "python:3.11-alpine")
    sandbox_cpus: float = float(os.getenv("HERMUS_SANDBOX_CPUS", "1.0"))
    sandbox_memory_mb: int = int(os.getenv("HERMUS_SANDBOX_MEMORY_MB", "1024"))
    sandbox_pids: int = int(os.getenv("HERMUS_SANDBOX_PIDS", "128"))
    sandbox_timeout: int = int(os.getenv("HERMUS_SANDBOX_TIMEOUT", "60"))
    sandbox_disk_mb: int = int(os.getenv("HERMUS_SANDBOX_DISK_MB", "256"))
    # 0 = no network inside sandboxes (default, safest)
    sandbox_network: bool = os.getenv("HERMUS_SANDBOX_NETWORK", "0") not in ("0", "false", "False")
    sandbox_read_only: bool = os.getenv("HERMUS_SANDBOX_RO_ROOTFS", "1") not in ("0", "false", "False")
    sandbox_workspace_rw: bool = os.getenv("HERMUS_SANDBOX_WORKSPACE_RW", "1") not in ("0", "false", "False")
    sandbox_runtime: str = os.getenv("HERMUS_SANDBOX_RUNTIME", "")  # e.g. runsc / kata

    # ---- Hierarchical sub-agent delegation --------------------------------
    delegation_enabled: bool = os.getenv("HERMUS_DELEGATION", "1") not in ("0", "false", "False")
    delegation_max_workers: int = int(os.getenv("HERMUS_DELEGATION_WORKERS", "4"))
    delegation_max_depth: int = int(os.getenv("HERMUS_DELEGATION_MAX_DEPTH", "2"))
    delegation_timeout: float = float(os.getenv("HERMUS_DELEGATION_TIMEOUT", "120"))
    delegation_rpc: bool = os.getenv("HERMUS_DELEGATION_RPC", "1") not in ("0", "false", "False")


    # MCP servers config
    mcp_servers_path: str = "data/mcp_servers.json"

    # Skills
    skills_dir: str = "skills"
    auto_skill_threshold: int = 3  # auto-create skill after 3+ tool calls

    # Gateway + channels
    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    discord_bot_token: Optional[str] = os.getenv("DISCORD_BOT_TOKEN")
    gateway_port: int = 8000
    # auto | polling | webhook
    telegram_mode: str = os.getenv("HERMUS_TELEGRAM_MODE", "auto")
    # Start Discord/Telegram listeners with gateway
    auto_start_channels: bool = os.getenv("HERMUS_AUTO_CHANNELS", "1") not in ("0", "false", "False")
    gateway_api_token: Optional[str] = os.getenv("HERMUS_GATEWAY_TOKEN")

    # Scheduler
    scheduler_db: str = "data/scheduler.db"

    # TUI
    history_file: str = "data/tui_history.txt"

    # Paths
    @property
    def base_dir(self) -> Path:
        # Find project root (where this file's parent's parent has README)
        return Path(__file__).parent.parent

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        return self.base_dir / path

config = Config()
