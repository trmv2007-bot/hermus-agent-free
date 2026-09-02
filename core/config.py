"""Config for Hermus Free - No paywalls"""

import os
from pathlib import Path

# Load the repository-local .env before Config reads any env var. Without this
# a provider configured only through ``.env`` (OPENROUTER_API_KEY,
# GEMINI_API_KEY, NVIDIA_API_KEY, ...) is invisible to the provider resolver,
# model fleet and fallback logic even though it is "configured". ``override``
# stays False so real exported environment variables still win.
#
# HERMUS_NO_DOTENV=1 skips the load. The test suite sets it: a developer's
# personal .env (raised step budgets, doctor caps, verify thresholds, ...) would
# otherwise change what the tests assert, making the suite depend on whoever's
# machine it runs on.
if os.getenv("HERMUS_NO_DOTENV", "") not in ("1", "true", "True"):
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    except Exception:  # python-dotenv is optional until setup.sh installs it
        pass

from pydantic import BaseModel, Field
from typing import Optional


def csv_list(value: str) -> list:
    """Parse a comma-separated env value into a clean list of strings.

    Blank entries are dropped: a trailing comma or an unset variable must not
    produce a wake-word alias of "" (which would match everything).
    """
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


class Config(BaseModel):
    # LLM Provider - free options
    # ollama/..., groq/..., hf/..., mock/...  (HERMUS_MODEL also scopes a whole
    # process tree — sub-agent workers inherit it.)
    model: str = os.getenv("HERMUS_MODEL", "ollama/llama3.1:8b")
    ollama_base_url: str = "http://localhost:11434"
    # Default model Ollama is asked for when the accelerator router picks it
    # (GPU reasoning / CPU-only boxes).
    ollama_default_model: str = os.getenv("HERMUS_OLLAMA_MODEL", "llama3.1:8b")
    ollama_vision_model: str = os.getenv("HERMUS_OLLAMA_VISION_MODEL", "llava:7b")

    # ---- Local engine routing (NPU via NoLlama / GPU via Ollama) -----------
    # auto | pipelined | npu | gpu | cpu | off
    #   auto       → detect hardware and pick (NPU+GPU = pipelined)
    #   pipelined  → NPU keeps background work, GPU does heavy reasoning
    #   npu / gpu  → force every role onto one accelerator
    #   cpu        → Ollama on CPU (NoLlama is Intel-only and slower there)
    local_engine_mode: str = os.getenv("HERMUS_LOCAL_ENGINE", "auto")
    # NoLlama (https://github.com/aweussom/NoLlama) — OpenVINO server for the
    # Intel NPU / Arc iGPU. Port 8010, NOT its 8000 default: the Hermus gateway
    # already serves 8000.
    nollama_port: int = int(os.getenv("HERMUS_NOLLAMA_PORT", "8010"))
    nollama_dir: str = os.getenv("HERMUS_NOLLAMA_DIR", "~/.hermus/nollama")
    nollama_models_dir: str = os.getenv("HERMUS_NOLLAMA_MODELS", "~/models")
    nollama_state_path: str = os.getenv("HERMUS_NOLLAMA_STATE", "data/nollama_state.json")
    nollama_log_path: str = os.getenv("HERMUS_NOLLAMA_LOG", "data/nollama.log")
    # Start the local engine with the gateway when the hardware supports it.
    nollama_autostart: bool = os.getenv("HERMUS_NOLLAMA_AUTOSTART", "0") not in ("0", "false", "False")
    # Models the router asks NoLlama to serve (OpenVINO IR directory names).
    nollama_npu_model: str = os.getenv("HERMUS_NOLLAMA_NPU_MODEL", "Qwen3-8B-int4-cw-ov")
    nollama_gpu_model: str = os.getenv("HERMUS_NOLLAMA_GPU_MODEL", "MiniCPM5-1B-int4-g128-ov")
    nollama_vision_model: str = os.getenv("HERMUS_NOLLAMA_VISION_MODEL", "Qwen3-VL-8B-Instruct-int8-ov")

    # ---- Optional speech / avatar integrations -----------------------------
    omnivoice_enabled: bool = os.getenv("HERMUS_OMNIVOICE_ENABLED", "1") not in ("0", "false", "False")
    omnivoice_model: str = os.getenv("HERMUS_OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
    omnivoice_device: str = os.getenv("HERMUS_OMNIVOICE_DEVICE", "auto")
    omnivoice_prompt_dir: str = os.getenv("HERMUS_OMNIVOICE_PROMPTS", "data/speech/prompts")
    heygem_tts_url: str = os.getenv("HERMUS_HEYGEM_TTS_URL", "http://127.0.0.1:18180")
    heygem_face2face_url: str = os.getenv("HERMUS_HEYGEM_FACE2FACE_URL", "http://127.0.0.1:8383/easy")
    heygem_timeout_s: float = float(os.getenv("HERMUS_HEYGEM_TIMEOUT", "120"))
    avatar_output_dir: str = os.getenv("HERMUS_AVATAR_DIR", "data/avatar")
    handy_model_dirs: str = os.getenv("HERMUS_HANDY_MODELS_DIRS", "")
    stt_normalize_default: bool = os.getenv("HERMUS_STT_NORMALIZE", "1") not in ("0", "false", "False")
    stt_strip_fillers_default: bool = os.getenv("HERMUS_STT_STRIP_FILLERS", "0") not in ("0", "false", "False")

    # ---- Hermus doctor: the small model that repairs Hermus itself ---------
    doctor_enabled: bool = os.getenv("HERMUS_DOCTOR_ENABLED", "1") not in ("0", "false", "False")
    # Auto-triage when a run/job fails (bounded by cooldown + daily cap).
    doctor_auto: bool = os.getenv("HERMUS_DOCTOR_AUTO", "0") not in ("0", "false", "False")
    # Let the doctor look things up online when it does not recognise a failure.
    doctor_ask_internet: bool = os.getenv("HERMUS_DOCTOR_INTERNET", "1") not in ("0", "false", "False")
    # "" = follow the accelerator plan's "doctor" role.
    doctor_model: str = os.getenv("HERMUS_DOCTOR_MODEL", "")
    doctor_cooldown_minutes: int = int(os.getenv("HERMUS_DOCTOR_COOLDOWN_MIN", "15"))
    doctor_daily_cap: int = int(os.getenv("HERMUS_DOCTOR_DAILY_CAP", "12"))
    # Anything still "running"/"queued" after this many minutes is treated as
    # stuck work and reported (and optionally reaped) — nothing is left in a
    # processing state forever.
    doctor_stuck_minutes: int = int(os.getenv("HERMUS_DOCTOR_STUCK_MIN", "20"))
    doctor_reports_dir: str = os.getenv("HERMUS_DOCTOR_REPORTS", "data/doctor")

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

    # Chat/multi-chat turns are held to a much tighter tool budget than agent
    # turns, because a chat message is usually a question rather than a job.
    # Raise this (or set it equal to max_tool_steps) to let chat turns run long
    # tool chains too.
    chat_max_steps: int = int(os.getenv("HERMUS_CHAT_MAX_STEPS", "2"))
    # The reasoning governor normally hands a task only a *share* of
    # max_tool_steps, scaled by classified difficulty (6.25% .. 100%), so easy
    # tasks stay cheap. Set to 1 to grant the full max_tool_steps budget to every
    # task regardless of difficulty.
    step_budget_full: bool = os.getenv("HERMUS_STEP_BUDGET_FULL", "0") not in ("0", "false", "False")

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
    # On by default: a tool-required request must recover to a tool-capable
    # provider instead of silently failing with "no model providers".
    auto_select_model: bool = os.getenv("HERMUS_AUTO_SELECT_MODEL", "1") not in ("0", "false", "False")


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

    # ---- Presence / continuity layer ---------------------------------------
    # Gives Hermus a durable identity, visible operational state, ongoing goals
    # and a lightweight heartbeat. The heartbeat is observability only: it never
    # calls a model or performs an action without an explicit request.
    presence_enabled: bool = os.getenv("HERMUS_PRESENCE_ENABLED", "1") not in ("0", "false", "False")
    presence_state_path: str = os.getenv("HERMUS_PRESENCE_STATE", "data/presence.json")
    presence_heartbeat_seconds: int = int(os.getenv("HERMUS_PRESENCE_HEARTBEAT_SECONDS", "30"))
    # Emit one heartbeat event every N beats (state changes always emit).
    presence_event_every: int = int(os.getenv("HERMUS_PRESENCE_EVENT_EVERY", "5"))
    # An ongoing goal becomes eligible for a visible check-in suggestion after
    # this many minutes without a user/agent touch. By default this is only a
    # visible suggestion; set HERMUS_PRESENCE_PROACTIVE_CHECKINS=1 to queue one
    # read-only status check through the normal runtime path.
    presence_checkin_after_minutes: int = int(os.getenv("HERMUS_PRESENCE_CHECKIN_AFTER_MINUTES", "240"))
    presence_proactive_checkins: bool = os.getenv("HERMUS_PRESENCE_PROACTIVE_CHECKINS", "0") not in ("0", "false", "False")

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

    # ---- Per-turn tool selection -------------------------------------------
    # Every agent call otherwise ships all ~179 tool schemas: measured at ~18.3K
    # of a ~19.9K prompt (93%), re-sent on every step of the ReAct loop. Sending
    # only the plausibly-relevant tools cuts that dramatically, which lowers both
    # cost and time-to-first-token. The model can always call `expand_tools` to
    # get the full catalog back if the subset is missing something.
    tool_subset_enabled: bool = os.getenv("HERMUS_TOOL_SUBSET", "1") not in ("0", "false", "False")
    # 0 disables subsetting. Values at or above the catalog size are a no-op.
    tool_subset_limit: int = int(os.getenv("HERMUS_TOOL_SUBSET_LIMIT", "40"))

    # ---- Hands-free voice loop ---------------------------------------------
    # OFF by default and deliberately so: an always-hot microphone is a standing
    # privacy commitment, not a convenience setting. Turning this on means the
    # browser keeps the mic open for as long as the Voice tab is armed, and audio
    # is sent to the speech-to-text backend on every detected utterance.
    voice_handsfree: bool = os.getenv("HERMUS_VOICE_HANDSFREE", "0") not in ("0", "false", "False")
    # Word that must start an utterance before it is acted on. With the wake word
    # required, ambient speech is transcribed and then discarded without ever
    # reaching the model.
    voice_wake_word: str = os.getenv("HERMUS_VOICE_WAKE_WORD", "jarvis")
    # Comma-separated mistranscriptions of the wake word that speech-to-text
    # actually produces on this install. The matcher tolerates ~2 edits; anything
    # further out has to be declared here rather than by loosening the budget,
    # which would let ordinary words un-gate the microphone.
    voice_wake_aliases: list[str] = csv_list(os.getenv("HERMUS_VOICE_WAKE_ALIASES", ""))
    voice_wake_required: bool = os.getenv("HERMUS_VOICE_WAKE_REQUIRED", "1") not in ("0", "false", "False")
    # Trailing silence that ends an utterance.
    voice_silence_ms: int = int(os.getenv("HERMUS_VOICE_SILENCE_MS", "900"))
    # Leading voice needed before we believe someone is talking (kills key clicks).
    voice_speech_ms: int = int(os.getenv("HERMUS_VOICE_SPEECH_MS", "140"))
    # Hard cap so a noisy room cannot record forever.
    voice_max_utterance_ms: int = int(os.getenv("HERMUS_VOICE_MAX_UTTERANCE_MS", "20000"))
    # Shorter than this is a cough, not a command.
    voice_min_utterance_ms: int = int(os.getenv("HERMUS_VOICE_MIN_UTTERANCE_MS", "350"))
    # Interrupt the assistant's own speech when the user starts talking.
    voice_barge_in: bool = os.getenv("HERMUS_VOICE_BARGE_IN", "1") not in ("0", "false", "False")

    # ---- Voice-first (Jarvis) mode -----------------------------------------
    # A normal agent turn sends the whole tool catalog (~20K prompt tokens) and
    # takes seconds to tens of seconds. A voice conversation cannot sit in
    # silence that long, so /voice/command speaks a short acknowledgment first
    # and queues the real work behind it. See gateway/routes_voice.py.
    voice_enabled: bool = os.getenv("HERMUS_VOICE_ENABLED", "1") not in ("0", "false", "False")
    # How the immediate acknowledgment is produced:
    #   canned -> local phrase pool, no model call at all (fastest: local TTS only)
    #   llm    -> tools-free model call (personalised, but pays model latency)
    #   off    -> no acknowledgment; just transcribe and queue
    voice_ack_mode: str = (os.getenv("HERMUS_VOICE_ACK_MODE", "canned") or "canned").strip().lower()
    # Pipe-separated acknowledgment pool used by the canned mode.
    voice_ack_phrases: str = os.getenv(
        "HERMUS_VOICE_ACK_PHRASES",
        "On it.|Give me a second.|Working on that now.|Sure, one moment.|Right away.",
    )
    # Synthesize the final answer for speech when the job finishes.
    voice_speak_answer: bool = os.getenv("HERMUS_VOICE_SPEAK_ANSWER", "1") not in ("0", "false", "False")
    # Speech-only truncation. Long answers stay complete in the transcript and
    # job result; only the spoken clip is shortened so replies stay listenable.
    voice_answer_max_chars: int = int(os.getenv("HERMUS_VOICE_ANSWER_MAX_CHARS", "900"))
    # Whisper model for inbound microphone audio.
    voice_stt_model: str = os.getenv("HERMUS_VOICE_STT_MODEL", "base")

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


    # ---- Web acquisition (Scrapling-backed, canonical core.web gateway) -----
    # All production web actions flow through core.web.WebGateway -> strategy
    # router -> Scrapling. Scrapling is an OPTIONAL dependency: with it absent
    # the subsystem degrades to typed "not installed" results, never crashes.
    web_enabled: bool = os.getenv("HERMUS_WEB_ENABLED", "1") not in ("0", "false", "False")
    # auto | static | dynamic | stealth — what the router prefers when the
    # agent does not name a strategy. AUTO = cheapest sufficient (static first).
    web_default_strategy: str = os.getenv("HERMUS_WEB_STRATEGY", "auto")
    # JS-rendered fetching (Playwright Chromium). Needs `scrapling install`.
    web_dynamic_enabled: bool = os.getenv("HERMUS_WEB_DYNAMIC", "1") not in ("0", "false", "False")
    # Stealth/anti-bot fetching is OFF by default: it is only used when a
    # normal acquisition genuinely fails AND an operator turned it on.
    web_stealth_enabled: bool = os.getenv("HERMUS_WEB_STEALTH", "0") not in ("0", "false", "False")
    # Whether stealth may solve Cloudflare-style interstitials (needs
    # HERMUS_WEB_STEALTH=1 too; still requires an explicit per-call opt-in).
    web_stealth_solve_cloudflare: bool = os.getenv("HERMUS_WEB_STEALTH_CF", "0") not in ("0", "false", "False")
    # On Android/Termux, keep browser strategies off until explicitly enabled
    # AND verified (Hermus never claims untested browser support).
    web_termux_restrict: bool = os.getenv("HERMUS_WEB_TERMUX_RESTRICT", "1") not in ("0", "false", "False")

    # Timeouts / sizes (resource control — spec §11)
    web_request_timeout: float = float(os.getenv("HERMUS_WEB_TIMEOUT", "20"))
    web_browser_timeout: float = float(os.getenv("HERMUS_WEB_BROWSER_TIMEOUT", "45"))
    web_max_response_bytes: int = int(os.getenv("HERMUS_WEB_MAX_RESPONSE_BYTES", str(5 * 1024 * 1024)))
    web_max_redirects: int = int(os.getenv("HERMUS_WEB_MAX_REDIRECTS", "10"))
    # Budget of page text kept per result / handed toward the model.
    web_max_content_chars: int = int(os.getenv("HERMUS_WEB_MAX_CONTENT_CHARS", "20000"))

    # SSRF posture. Private/loopback/link-local targets are ALWAYS blocked
    # unless this is explicitly set — for tests or self-hosted intranets.
    web_allow_private_addresses: bool = os.getenv("HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES", "0") not in ("0", "false", "False")
    # Optional domain policy (comma-separated; `*.example.com` wildcards ok).
    # Empty allow list = all (non-blocked) public domains permitted.
    web_allowed_domains: list = Field(default_factory=lambda: csv_list(os.getenv("HERMUS_WEB_ALLOWED_DOMAINS", "")))
    web_blocked_domains: list = Field(default_factory=lambda: csv_list(os.getenv("HERMUS_WEB_BLOCKED_DOMAINS", "")))

    # Crawl ceilings (background crawls go through the canonical JobQueue).
    web_crawl_max_pages: int = int(os.getenv("HERMUS_WEB_CRAWL_MAX_PAGES", "100"))
    web_crawl_max_depth: int = int(os.getenv("HERMUS_WEB_CRAWL_MAX_DEPTH", "4"))
    web_crawl_concurrency: int = int(os.getenv("HERMUS_WEB_CRAWL_CONCURRENCY", "8"))
    web_crawl_wall_clock: float = float(os.getenv("HERMUS_WEB_CRAWL_WALL_CLOCK", "600"))
    web_crawl_per_domain_delay_ms: int = int(os.getenv("HERMUS_WEB_CRAWL_DELAY_MS", "500"))

    # Sessions: in-memory cookie jars pinned to explicit domains, never
    # serialized and never shown to the model.
    web_max_sessions: int = int(os.getenv("HERMUS_WEB_MAX_SESSIONS", "8"))
    web_session_ttl: float = float(os.getenv("HERMUS_WEB_SESSION_TTL", "1800"))

    # Page cache (canonical core.cache LRUCache). Only unauthenticated GETs.
    web_cache_enabled: bool = os.getenv("HERMUS_WEB_CACHE", "1") not in ("0", "false", "False")
    web_cache_size: int = int(os.getenv("HERMUS_WEB_CACHE_SIZE", "128"))
    web_cache_ttl: int = int(os.getenv("HERMUS_WEB_CACHE_TTL", "600"))

    # MCP servers config
    mcp_servers_path: str = "data/mcp_servers.json"

    # Skills
    skills_dir: str = "skills"
    auto_skill_threshold: int = 3  # auto-create skill after 3+ tool calls

    # Gateway + channels
    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    discord_bot_token: Optional[str] = os.getenv("DISCORD_BOT_TOKEN")
    # Optional secret for the Telegram *webhook* route (setWebhook secret_token).
    # When set, /webhook/telegram requires the matching X-Telegram-Bot-Api-Secret-Token
    # header; when unset the webhook stays open (legacy poll/webhook setups).
    telegram_webhook_secret: Optional[str] = os.getenv("HERMUS_TELEGRAM_WEBHOOK_SECRET")
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
