"""Config for Hermus Free - No paywalls"""
import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

class Config(BaseModel):
    # LLM Provider - free options
    model: str = "ollama/llama3.1:8b"  # ollama/..., groq/..., hf/..., mock/...
    ollama_base_url: str = "http://localhost:11434"
    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    hf_token: Optional[str] = os.getenv("HF_TOKEN")
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    openrouter_api_key: Optional[str] = os.getenv("OPENROUTER_API_KEY")
    gemini_api_key: Optional[str] = os.getenv("GEMINI_API_KEY")
    anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")

    # Agent loop
    max_tool_steps: int = int(os.getenv("HERMUS_MAX_TOOL_STEPS", "8"))

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
