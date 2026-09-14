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


from typing import Annotated, Any

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def csv_list(value: str) -> list:
    """Parse a comma-separated env value into a clean list of strings.

    Blank entries are dropped: a trailing comma or an unset variable must not
    produce a wake-word alias of "" (which would match everything).
    """
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


#: Env values that mean "false" for every boolean flag. This replicates the
#: legacy expression ``os.getenv(...) not in ("0", "false", "False")`` exactly:
#: anything else — including "true", "yes", "2" or even "" — means True.
_FLAG_FALSE_TOKENS = frozenset({"0", "false", "False"})


def _parse_env_flag(value: Any) -> Any:
    """Before-validator preserving the legacy boolean-flag semantics."""
    if isinstance(value, str):
        return value not in _FLAG_FALSE_TOKENS
    return value


#: A boolean setting read from the environment with legacy flag semantics.
EnvFlag = Annotated[bool, BeforeValidator(_parse_env_flag)]


def _parse_csv_value(value: Any) -> Any:
    """Before-validator preserving the legacy ``csv_list(os.getenv(...))`` semantics."""
    if isinstance(value, str):
        return csv_list(value)
    return value


#: A comma-separated list setting (blank entries dropped, see :func:`csv_list`).
#: ``NoDecode`` keeps settings from JSON-decoding the raw env string before
#: the validator splits it (``"a, b"`` is not JSON).
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_parse_csv_value)]


def _parse_ack_mode(value: Any) -> Any:
    """Before-validator for ``voice_ack_mode`` (defaults, strips, lowercases)."""
    if isinstance(value, str):
        return (value or "canned").strip().lower()
    return value


#: Voice acknowledgement mode (``canned`` | ``live``), normalized like before.
AckMode = Annotated[str, BeforeValidator(_parse_ack_mode)]


class Config(BaseSettings):
    """Hermus configuration — every field overridable via its documented env var.

    Previously a plain ``BaseModel`` with ``os.getenv(...)`` defaults frozen at
    import time; now a ``BaseSettings`` model so values are validated, typed
    (ints/floats/flags/lists parse instead of crashing the import), and a
    fresh ``Config()`` honors the *current* environment. The module-level
    ``config`` singleton below still reflects the import-time environment.
    """

    model_config = SettingsConfigDict(env_prefix="HERMUS_", case_sensitive=True, populate_by_name=True, extra="ignore")
    # LLM Provider - free options
    # ollama/..., groq/..., hf/..., mock/...  (HERMUS_MODEL also scopes a whole
    # process tree — sub-agent workers inherit it.)
    model: str = Field(default="ollama/llama3.1:8b", validation_alias="HERMUS_MODEL")
    ollama_base_url: str = "http://localhost:11434"
    # Default model Ollama is asked for when the accelerator router picks it
    # (GPU reasoning / CPU-only boxes).
    ollama_default_model: str = Field(default="llama3.1:8b", validation_alias="HERMUS_OLLAMA_MODEL")
    ollama_vision_model: str = Field(default="llava:7b", validation_alias="HERMUS_OLLAMA_VISION_MODEL")

    # ---- Local engine routing (NPU via NoLlama / GPU via Ollama) -----------
    # auto | pipelined | npu | gpu | cpu | off
    #   auto       → detect hardware and pick (NPU+GPU = pipelined)
    #   pipelined  → NPU keeps background work, GPU does heavy reasoning
    #   npu / gpu  → force every role onto one accelerator
    #   cpu        → Ollama on CPU (NoLlama is Intel-only and slower there)
    local_engine_mode: str = Field(default="auto", validation_alias="HERMUS_LOCAL_ENGINE")
    # NoLlama (https://github.com/aweussom/NoLlama) — OpenVINO server for the
    # Intel NPU / Arc iGPU. Port 8010, NOT its 8000 default: the Hermus gateway
    # already serves 8000.
    nollama_port: int = Field(default=8010, validation_alias="HERMUS_NOLLAMA_PORT")
    nollama_dir: str = Field(default="~/.hermus/nollama", validation_alias="HERMUS_NOLLAMA_DIR")
    nollama_models_dir: str = Field(default="~/models", validation_alias="HERMUS_NOLLAMA_MODELS")
    nollama_state_path: str = Field(default="data/nollama_state.json", validation_alias="HERMUS_NOLLAMA_STATE")
    nollama_log_path: str = Field(default="data/nollama.log", validation_alias="HERMUS_NOLLAMA_LOG")
    # Start the local engine with the gateway when the hardware supports it.
    nollama_autostart: EnvFlag = Field(default=False, validation_alias="HERMUS_NOLLAMA_AUTOSTART")
    # Models the router asks NoLlama to serve (OpenVINO IR directory names).
    nollama_npu_model: str = Field(default="Qwen3-8B-int4-cw-ov", validation_alias="HERMUS_NOLLAMA_NPU_MODEL")
    nollama_gpu_model: str = Field(default="MiniCPM5-1B-int4-g128-ov", validation_alias="HERMUS_NOLLAMA_GPU_MODEL")
    nollama_vision_model: str = Field(default="Qwen3-VL-8B-Instruct-int8-ov", validation_alias="HERMUS_NOLLAMA_VISION_MODEL")

    # ---- Optional speech / avatar integrations -----------------------------
    omnivoice_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_OMNIVOICE_ENABLED")
    omnivoice_model: str = Field(default="k2-fsa/OmniVoice", validation_alias="HERMUS_OMNIVOICE_MODEL")
    omnivoice_device: str = Field(default="auto", validation_alias="HERMUS_OMNIVOICE_DEVICE")
    omnivoice_prompt_dir: str = Field(default="data/speech/prompts", validation_alias="HERMUS_OMNIVOICE_PROMPTS")
    heygem_tts_url: str = Field(default="http://127.0.0.1:18180", validation_alias="HERMUS_HEYGEM_TTS_URL")
    heygem_face2face_url: str = Field(default="http://127.0.0.1:8383/easy", validation_alias="HERMUS_HEYGEM_FACE2FACE_URL")
    heygem_timeout_s: float = Field(default=120.0, validation_alias="HERMUS_HEYGEM_TIMEOUT")
    avatar_output_dir: str = Field(default="data/avatar", validation_alias="HERMUS_AVATAR_DIR")
    handy_model_dirs: str = Field(default="", validation_alias="HERMUS_HANDY_MODELS_DIRS")
    stt_normalize_default: EnvFlag = Field(default=True, validation_alias="HERMUS_STT_NORMALIZE")
    stt_strip_fillers_default: EnvFlag = Field(default=False, validation_alias="HERMUS_STT_STRIP_FILLERS")

    # ---- Hermus doctor: the small model that repairs Hermus itself ---------
    doctor_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_DOCTOR_ENABLED")
    # Auto-triage when a run/job fails (bounded by cooldown + daily cap).
    doctor_auto: EnvFlag = Field(default=False, validation_alias="HERMUS_DOCTOR_AUTO")
    # Let the doctor look things up online when it does not recognise a failure.
    doctor_ask_internet: EnvFlag = Field(default=True, validation_alias="HERMUS_DOCTOR_INTERNET")
    # "" = follow the accelerator plan's "doctor" role.
    doctor_model: str = Field(default="", validation_alias="HERMUS_DOCTOR_MODEL")
    doctor_cooldown_minutes: int = Field(default=15, validation_alias="HERMUS_DOCTOR_COOLDOWN_MIN")
    doctor_daily_cap: int = Field(default=12, validation_alias="HERMUS_DOCTOR_DAILY_CAP")
    # Anything still "running"/"queued" after this many minutes is treated as
    # stuck work and reported (and optionally reaped) — nothing is left in a
    # processing state forever.
    doctor_stuck_minutes: int = Field(default=20, validation_alias="HERMUS_DOCTOR_STUCK_MIN")
    doctor_reports_dir: str = Field(default="data/doctor", validation_alias="HERMUS_DOCTOR_REPORTS")

    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    hf_token: str | None = Field(default=None, validation_alias="HF_TOKEN")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    # Agent loop
    # Default tool-step budget per turn. 8 was far too tight for "build this
    # app, test it, fix every error, and keep going until it works" style
    # goals — the loop hit the cap and force-synthesized an answer mid-work.
    # 32 keeps simple chat cheap (the governor scales the per-task budget
    # down for easy tasks) while giving real work room to finish.
    max_tool_steps: int = Field(default=32, validation_alias="HERMUS_MAX_TOOL_STEPS")

    # Chat/multi-chat turns are held to a much tighter tool budget than agent
    # turns, because a chat message is usually a question rather than a job.
    # Raise this (or set it equal to max_tool_steps) to let chat turns run long
    # tool chains too.
    chat_max_steps: int = Field(default=2, validation_alias="HERMUS_CHAT_MAX_STEPS")
    # The reasoning governor normally hands a task only a *share* of
    # max_tool_steps, scaled by classified difficulty (6.25% .. 100%), so easy
    # tasks stay cheap. Set to 1 to grant the full max_tool_steps budget to every
    # task regardless of difficulty.
    step_budget_full: EnvFlag = Field(default=False, validation_alias="HERMUS_STEP_BUDGET_FULL")

    # ---- Universal mission runtime -----------------------------------------
    # Route every execution surface (agent.autonomous(), /command?autonomous,
    # /stream/command, queue jobs, CLI, channels, scheduler) through the
    # MissionEngine runtime so behavior no longer depends on the entry point.
    mission_runtime_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_MISSION_RUNTIME")
    # Auto-promote goal-like messages ("build … and keep going until it works")
    # to full missions even when the caller did not set autonomous=true.
    mission_auto_classify: EnvFlag = Field(default=True, validation_alias="HERMUS_MISSION_AUTO_CLASSIFY")
    # Default step budget for a mission. A mission owns the whole lifecycle
    # (plan -> implement -> test -> inspect -> repair -> retest), so it must be
    # larger than a single agent turn (max_tool_steps), not smaller.
    mission_budget_steps: int = Field(default=48, validation_alias="HERMUS_MISSION_BUDGET_STEPS")
    # When the mission runtime crashes, report MISSION FAILED with diagnostics.
    # Opt in to the old "answer with a chat turn instead" behaviour only for
    # interactive demos — it hides failures behind plausible prose.
    mission_fallback_to_chat: EnvFlag = Field(default=False, validation_alias="HERMUS_MISSION_FALLBACK_TO_CHAT")
    # Pre-flight model capability negotiation (tools/vision/context/...).
    model_capability_check: EnvFlag = Field(default=True, validation_alias="HERMUS_MODEL_CAPABILITY_CHECK")
    # Auto-select a compatible model when the selected one cannot do the job.
    # On by default: a tool-required request must recover to a tool-capable
    # provider instead of silently failing with "no model providers".
    auto_select_model: EnvFlag = Field(default=True, validation_alias="HERMUS_AUTO_SELECT_MODEL")

    # DeepThink — plan-first thinking (Phase 0)
    think_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_THINK_ENABLED")

    # DeepThink strategies + lessons loop (Phase 3)
    # auto | none | reflexion | self_consistency | verify
    think_strategy: str = Field(default="auto", validation_alias="HERMUS_STRATEGY")
    self_consistency_k: int = Field(default=3, validation_alias="HERMUS_SELF_CONSISTENCY_K")
    verify_threshold: int = Field(default=4, validation_alias="HERMUS_VERIFY_THRESHOLD")
    lessons_in_prompt: int = Field(default=8, validation_alias="HERMUS_LESSONS_IN_PROMPT")

    # Project-scoped memory (Phase 4)
    project: str = Field(default="default", validation_alias="HERMUS_PROJECT")

    # Counsel System — council of AIs that plans together and upgrades itself (Phases 0-2)
    counsel_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_COUNSEL_ENABLED")
    counsel_min_difficulty: int = Field(default=4, validation_alias="HERMUS_COUNSEL_MIN_DIFFICULTY")
    counsel_max_members: int = Field(default=6, validation_alias="HERMUS_COUNSEL_MAX_MEMBERS")
    counsel_max_rounds: int = Field(default=3, validation_alias="HERMUS_COUNSEL_MAX_ROUNDS")
    # Meta-Counsel reviews each session and proposes self-upgrades
    counsel_auto_review: EnvFlag = Field(default=True, validation_alias="HERMUS_COUNSEL_AUTO_REVIEW")

    # Memory
    memory_db_path: str = "data/memory.db"
    memory2_db_path: str = "data/memory2.db"
    user_model_path: str = "data/user_model.json"
    trajectory_path: str = "data/trajectories.jsonl"

    # Workspace — per-project isolation (agent OS layout)
    workspace_dir: str = Field(default="~/.hermus", validation_alias="HERMUS_HOME")

    # ---- Architecture upgrades (full wiring) -------------------------------
    # Permissions: enforce ALLOW/ASK/DENY on every tool call (always audited).
    permissions_enforce: EnvFlag = Field(default=True, validation_alias="HERMUS_PERMISSIONS_ENFORCE")
    # How an ASK decision resolves when no interactive prompt is attached.
    # "allow" (default, backward-compatible) | "deny" (strict / fail-safe).
    ask_policy: str = Field(default="allow", validation_alias="HERMUS_ASK_POLICY")
    # Memory 2.0: typed + scored recall injected into the system prompt + auto-persist.
    memory2_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_MEMORY2_ENABLED")
    # Model Router 2.0: per-turn model selection.
    router2_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_ROUTER2_ENABLED")
    # Autonomous verify/repair gate applied after the ReAct loop.
    autonomous_enabled: EnvFlag = Field(default=False, validation_alias="HERMUS_AUTONOMOUS_ENABLED")
    # Self-healing watchdog on task/tool failures (gateway + agent).
    watchdog_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_WATCHDOG_ENABLED")
    # Keep persistent background agents alive (gateway watchdog tick).
    background_agents_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_BG_AGENTS_ENABLED")
    # Active persona / profile name (independent memory + system prompt).
    profile: str = Field(default="", validation_alias="HERMUS_PROFILE")

    # ---- Presence / continuity layer ---------------------------------------
    # Gives Hermus a durable identity, visible operational state, ongoing goals
    # and a lightweight heartbeat. The heartbeat is observability only: it never
    # calls a model or performs an action without an explicit request.
    presence_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_PRESENCE_ENABLED")
    presence_state_path: str = Field(default="data/presence.json", validation_alias="HERMUS_PRESENCE_STATE")
    presence_heartbeat_seconds: int = Field(default=30, validation_alias="HERMUS_PRESENCE_HEARTBEAT_SECONDS")
    # Emit one heartbeat event every N beats (state changes always emit).
    presence_event_every: int = Field(default=5, validation_alias="HERMUS_PRESENCE_EVENT_EVERY")
    # An ongoing goal becomes eligible for a visible check-in suggestion after
    # this many minutes without a user/agent touch. By default this is only a
    # visible suggestion; set HERMUS_PRESENCE_PROACTIVE_CHECKINS=1 to queue one
    # read-only status check through the normal runtime path.
    presence_checkin_after_minutes: int = Field(default=240, validation_alias="HERMUS_PRESENCE_CHECKIN_AFTER_MINUTES")
    presence_proactive_checkins: EnvFlag = Field(default=False, validation_alias="HERMUS_PRESENCE_PROACTIVE_CHECKINS")

    # Semantic memory / embeddings (free local)
    embeddings_db_path: str = "data/embeddings.db"
    embedding_model: str = Field(default="nomic-embed-text", validation_alias="HERMUS_EMBED_MODEL")
    # auto | ollama | hash  (hash = deterministic offline fallback, no probing)
    embedding_backend: str = Field(default="auto", validation_alias="HERMUS_EMBED_BACKEND")

    # ---- Hybrid memory + decay (retrieval upgrades) ------------------------
    # FTS5 BM25 + dense vectors fused with Reciprocal Rank Fusion for recall.
    memory_hybrid_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_MEMORY_HYBRID")
    # Store per-memory float32 embeddings (sqlite-vec if installed, else cosine scan).
    memory_vectors_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_MEMORY_VECTORS")
    # Recency half-life (days) for exponential decay; lengthens with access count.
    memory_half_life_days: float = Field(default=30.0, validation_alias="HERMUS_MEMORY_HALF_LIFE_DAYS")
    # Never silence a memory completely: decay multiplier is floored here.
    memory_decay_floor: float = Field(default=0.35, validation_alias="HERMUS_MEMORY_DECAY_FLOOR")
    # Token budget for the memories injected into the system prompt.
    memory_budget_tokens: int = Field(default=600, validation_alias="HERMUS_MEMORY_BUDGET_TOKENS")
    memory_archive_below: float = Field(default=0.08, validation_alias="HERMUS_MEMORY_ARCHIVE_BELOW")
    memory_purge_below: float = Field(default=0.02, validation_alias="HERMUS_MEMORY_PURGE_BELOW")
    memory_working_ttl_hours: float = Field(default=48.0, validation_alias="HERMUS_MEMORY_WORKING_TTL_HOURS")
    memory_rrf_k: int = Field(default=60, validation_alias="HERMUS_MEMORY_RRF_K")
    memory_prior_weight: float = Field(default=0.35, validation_alias="HERMUS_MEMORY_PRIOR_WEIGHT")
    memory_sweep_minutes: int = Field(default=60, validation_alias="HERMUS_MEMORY_SWEEP_MINUTES")

    # ---- Skill forge: post-task trajectory → SKILL.md ----------------------
    skill_forge_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_SKILL_FORGE")
    skill_forge_min_tools: int = Field(default=3, validation_alias="HERMUS_SKILL_FORGE_MIN_TOOLS")
    # LLM distillation is optional; a deterministic template is used without it.
    skill_forge_use_llm: EnvFlag = Field(default=True, validation_alias="HERMUS_SKILL_FORGE_LLM")
    skill_forge_dedupe_similarity: float = Field(default=0.72, validation_alias="HERMUS_SKILL_FORGE_DEDUPE")
    skill_forge_max_skills: int = Field(default=200, validation_alias="HERMUS_SKILL_FORGE_MAX")
    # Self-improvement safety: a procedure is only distilled into a skill after
    # it has been observed to succeed this many times in *independent* runs
    # (1 = learn from a single run, 2 = require a repeat — the default).
    skill_forge_min_repeats: int = Field(default=2, validation_alias="HERMUS_SKILL_FORGE_MIN_REPEATS")
    # Require verified success (verifier verdict or clean independent evidence)
    # before harvesting — never learn a procedure from a failed/bogus run.
    skill_forge_require_verified: EnvFlag = Field(default=True, validation_alias="HERMUS_SKILL_FORGE_REQUIRE_VERIFIED")

    # ---- Gateway async queue + streaming ----------------------------------
    gateway_queue_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_QUEUE_ENABLED")
    gateway_queue_workers: int = Field(default=4, validation_alias="HERMUS_QUEUE_WORKERS")
    gateway_queue_maxsize: int = Field(default=500, validation_alias="HERMUS_QUEUE_MAXSIZE")
    gateway_queue_timeout: float = Field(default=300.0, validation_alias="HERMUS_QUEUE_TIMEOUT")
    # inprocess | redis (redis is optional; falls back to inprocess)
    gateway_queue_retry_backoff: float = Field(default=1.5, validation_alias="HERMUS_QUEUE_RETRY_BACKOFF")
    gateway_queue_cancel_grace: float = Field(default=15.0, validation_alias="HERMUS_QUEUE_CANCEL_GRACE")
    gateway_queue_backend: str = Field(default="inprocess", validation_alias="HERMUS_QUEUE_BACKEND")
    # durable job log (results/ lives next to it); relative paths anchor to the repo
    gateway_jobs_log: str = Field(default="data/jobs/jobs.jsonl", validation_alias="HERMUS_QUEUE_LOG")
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    gateway_stream_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_STREAM_ENABLED")
    gateway_stream_tokens: EnvFlag = Field(default=True, validation_alias="HERMUS_STREAM_TOKENS")

    # ---- Per-turn tool selection -------------------------------------------
    # Every agent call otherwise ships all ~179 tool schemas: measured at ~18.3K
    # of a ~19.9K prompt (93%), re-sent on every step of the ReAct loop. Sending
    # only the plausibly-relevant tools cuts that dramatically, which lowers both
    # cost and time-to-first-token. The model can always call `expand_tools` to
    # get the full catalog back if the subset is missing something.
    tool_subset_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_TOOL_SUBSET")
    # 0 disables subsetting. Values at or above the catalog size are a no-op.
    tool_subset_limit: int = Field(default=40, validation_alias="HERMUS_TOOL_SUBSET_LIMIT")

    # ---- Hands-free voice loop ---------------------------------------------
    # OFF by default and deliberately so: an always-hot microphone is a standing
    # privacy commitment, not a convenience setting. Turning this on means the
    # browser keeps the mic open for as long as the Voice tab is armed, and audio
    # is sent to the speech-to-text backend on every detected utterance.
    voice_handsfree: EnvFlag = Field(default=False, validation_alias="HERMUS_VOICE_HANDSFREE")
    # Word that must start an utterance before it is acted on. With the wake word
    # required, ambient speech is transcribed and then discarded without ever
    # reaching the model.
    voice_wake_word: str = Field(default="jarvis", validation_alias="HERMUS_VOICE_WAKE_WORD")
    # Comma-separated mistranscriptions of the wake word that speech-to-text
    # actually produces on this install. The matcher tolerates ~2 edits; anything
    # further out has to be declared here rather than by loosening the budget,
    # which would let ordinary words un-gate the microphone.
    voice_wake_aliases: CsvList = Field(default_factory=list, validation_alias="HERMUS_VOICE_WAKE_ALIASES")
    voice_wake_required: EnvFlag = Field(default=True, validation_alias="HERMUS_VOICE_WAKE_REQUIRED")
    # Trailing silence that ends an utterance.
    voice_silence_ms: int = Field(default=900, validation_alias="HERMUS_VOICE_SILENCE_MS")
    # Leading voice needed before we believe someone is talking (kills key clicks).
    voice_speech_ms: int = Field(default=140, validation_alias="HERMUS_VOICE_SPEECH_MS")
    # Hard cap so a noisy room cannot record forever.
    voice_max_utterance_ms: int = Field(default=20000, validation_alias="HERMUS_VOICE_MAX_UTTERANCE_MS")
    # Shorter than this is a cough, not a command.
    voice_min_utterance_ms: int = Field(default=350, validation_alias="HERMUS_VOICE_MIN_UTTERANCE_MS")
    # Interrupt the assistant's own speech when the user starts talking.
    voice_barge_in: EnvFlag = Field(default=True, validation_alias="HERMUS_VOICE_BARGE_IN")

    # ---- Voice-first (Jarvis) mode -----------------------------------------
    # A normal agent turn sends the whole tool catalog (~20K prompt tokens) and
    # takes seconds to tens of seconds. A voice conversation cannot sit in
    # silence that long, so /voice/command speaks a short acknowledgment first
    # and queues the real work behind it. See gateway/routes_voice.py.
    voice_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_VOICE_ENABLED")
    # How the immediate acknowledgment is produced:
    #   canned -> local phrase pool, no model call at all (fastest: local TTS only)
    #   llm    -> tools-free model call (personalised, but pays model latency)
    #   off    -> no acknowledgment; just transcribe and queue
    voice_ack_mode: AckMode = Field(default="canned", validation_alias="HERMUS_VOICE_ACK_MODE")
    # Pipe-separated acknowledgment pool used by the canned mode.
    voice_ack_phrases: str = Field(
        default="On it.|Give me a second.|Working on that now.|Sure, one moment.|Right away.",
        validation_alias="HERMUS_VOICE_ACK_PHRASES",
    )
    # Synthesize the final answer for speech when the job finishes.
    voice_speak_answer: EnvFlag = Field(default=True, validation_alias="HERMUS_VOICE_SPEAK_ANSWER")
    # Speech-only truncation. Long answers stay complete in the transcript and
    # job result; only the spoken clip is shortened so replies stay listenable.
    voice_answer_max_chars: int = Field(default=900, validation_alias="HERMUS_VOICE_ANSWER_MAX_CHARS")
    # Whisper model for inbound microphone audio.
    voice_stt_model: str = Field(default="base", validation_alias="HERMUS_VOICE_STT_MODEL")

    # ---- Tool sandboxing ---------------------------------------------------
    # auto | docker | podman | gvisor | local | off
    sandbox_mode: str = Field(default="auto", validation_alias="HERMUS_SANDBOX")
    sandbox_image: str = Field(default="python:3.11-alpine", validation_alias="HERMUS_SANDBOX_IMAGE")
    sandbox_cpus: float = Field(default=1.0, validation_alias="HERMUS_SANDBOX_CPUS")
    sandbox_memory_mb: int = Field(default=1024, validation_alias="HERMUS_SANDBOX_MEMORY_MB")
    sandbox_pids: int = Field(default=128, validation_alias="HERMUS_SANDBOX_PIDS")
    sandbox_timeout: int = Field(default=60, validation_alias="HERMUS_SANDBOX_TIMEOUT")
    sandbox_disk_mb: int = Field(default=256, validation_alias="HERMUS_SANDBOX_DISK_MB")
    # 0 = no network inside sandboxes (default, safest)
    sandbox_network: EnvFlag = Field(default=False, validation_alias="HERMUS_SANDBOX_NETWORK")
    sandbox_read_only: EnvFlag = Field(default=True, validation_alias="HERMUS_SANDBOX_RO_ROOTFS")
    sandbox_workspace_rw: EnvFlag = Field(default=True, validation_alias="HERMUS_SANDBOX_WORKSPACE_RW")
    sandbox_runtime: str = Field(default="", validation_alias="HERMUS_SANDBOX_RUNTIME")  # e.g. runsc / kata

    # ---- Hierarchical sub-agent delegation --------------------------------
    delegation_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_DELEGATION")
    delegation_max_workers: int = Field(default=4, validation_alias="HERMUS_DELEGATION_WORKERS")
    delegation_max_depth: int = Field(default=2, validation_alias="HERMUS_DELEGATION_MAX_DEPTH")
    delegation_timeout: float = Field(default=120.0, validation_alias="HERMUS_DELEGATION_TIMEOUT")
    delegation_rpc: EnvFlag = Field(default=True, validation_alias="HERMUS_DELEGATION_RPC")

    # ---- Web acquisition (Scrapling-backed, canonical core.web gateway) -----
    # All production web actions flow through core.web.WebGateway -> strategy
    # router -> Scrapling. Scrapling is an OPTIONAL dependency: with it absent
    # the subsystem degrades to typed "not installed" results, never crashes.
    web_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_WEB_ENABLED")
    # auto | static | dynamic | stealth — what the router prefers when the
    # agent does not name a strategy. AUTO = cheapest sufficient (static first).
    web_default_strategy: str = Field(default="auto", validation_alias="HERMUS_WEB_STRATEGY")
    # JS-rendered fetching (Playwright Chromium). Needs `scrapling install`.
    web_dynamic_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_WEB_DYNAMIC")
    # Stealth/anti-bot fetching is OFF by default: it is only used when a
    # normal acquisition genuinely fails AND an operator turned it on.
    web_stealth_enabled: EnvFlag = Field(default=False, validation_alias="HERMUS_WEB_STEALTH")
    # Whether stealth may solve Cloudflare-style interstitials (needs
    # HERMUS_WEB_STEALTH=1 too; still requires an explicit per-call opt-in).
    web_stealth_solve_cloudflare: EnvFlag = Field(default=False, validation_alias="HERMUS_WEB_STEALTH_CF")
    # On Android/Termux, keep browser strategies off until explicitly enabled
    # AND verified (Hermus never claims untested browser support).
    web_termux_restrict: EnvFlag = Field(default=True, validation_alias="HERMUS_WEB_TERMUX_RESTRICT")

    # Timeouts / sizes (resource control — spec §11)
    web_request_timeout: float = Field(default=20.0, validation_alias="HERMUS_WEB_TIMEOUT")
    web_browser_timeout: float = Field(default=45.0, validation_alias="HERMUS_WEB_BROWSER_TIMEOUT")
    web_max_response_bytes: int = Field(default=5242880, validation_alias="HERMUS_WEB_MAX_RESPONSE_BYTES")
    web_max_redirects: int = Field(default=10, validation_alias="HERMUS_WEB_MAX_REDIRECTS")
    # Budget of page text kept per result / handed toward the model.
    web_max_content_chars: int = Field(default=20000, validation_alias="HERMUS_WEB_MAX_CONTENT_CHARS")

    # SSRF posture. Private/loopback/link-local targets are ALWAYS blocked
    # unless this is explicitly set — for tests or self-hosted intranets.
    web_allow_private_addresses: EnvFlag = Field(default=False, validation_alias="HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES")
    # Optional domain policy (comma-separated; `*.example.com` wildcards ok).
    # Empty allow list = all (non-blocked) public domains permitted.
    web_allowed_domains: CsvList = Field(default_factory=list, validation_alias="HERMUS_WEB_ALLOWED_DOMAINS")
    web_blocked_domains: CsvList = Field(default_factory=list, validation_alias="HERMUS_WEB_BLOCKED_DOMAINS")

    # Crawl ceilings (background crawls go through the canonical JobQueue).
    web_crawl_max_pages: int = Field(default=100, validation_alias="HERMUS_WEB_CRAWL_MAX_PAGES")
    web_crawl_max_depth: int = Field(default=4, validation_alias="HERMUS_WEB_CRAWL_MAX_DEPTH")
    web_crawl_concurrency: int = Field(default=8, validation_alias="HERMUS_WEB_CRAWL_CONCURRENCY")
    web_crawl_wall_clock: float = Field(default=600.0, validation_alias="HERMUS_WEB_CRAWL_WALL_CLOCK")
    web_crawl_per_domain_delay_ms: int = Field(default=500, validation_alias="HERMUS_WEB_CRAWL_DELAY_MS")

    # Sessions: in-memory cookie jars pinned to explicit domains, never
    # serialized and never shown to the model.
    web_max_sessions: int = Field(default=8, validation_alias="HERMUS_WEB_MAX_SESSIONS")
    web_session_ttl: float = Field(default=1800.0, validation_alias="HERMUS_WEB_SESSION_TTL")

    # Page cache (canonical core.cache LRUCache). Only unauthenticated GETs.
    web_cache_enabled: EnvFlag = Field(default=True, validation_alias="HERMUS_WEB_CACHE")
    web_cache_size: int = Field(default=128, validation_alias="HERMUS_WEB_CACHE_SIZE")
    web_cache_ttl: int = Field(default=600, validation_alias="HERMUS_WEB_CACHE_TTL")

    # MCP servers config
    mcp_servers_path: str = "data/mcp_servers.json"

    # Skills
    skills_dir: str = "skills"
    auto_skill_threshold: int = 3  # auto-create skill after 3+ tool calls

    # Gateway + channels
    telegram_bot_token: str | None = Field(default=None, validation_alias="TELEGRAM_BOT_TOKEN")
    discord_bot_token: str | None = Field(default=None, validation_alias="DISCORD_BOT_TOKEN")
    # Optional secret for the Telegram *webhook* route (setWebhook secret_token).
    # When set, /webhook/telegram requires the matching X-Telegram-Bot-Api-Secret-Token
    # header; when unset the webhook stays open (legacy poll/webhook setups).
    telegram_webhook_secret: str | None = Field(default=None, validation_alias="HERMUS_TELEGRAM_WEBHOOK_SECRET")
    gateway_port: int = 8000
    # auto | polling | webhook
    telegram_mode: str = Field(default="auto", validation_alias="HERMUS_TELEGRAM_MODE")
    # Start Discord/Telegram listeners with gateway
    auto_start_channels: EnvFlag = Field(default=True, validation_alias="HERMUS_AUTO_CHANNELS")
    gateway_api_token: str | None = Field(default=None, validation_alias="HERMUS_GATEWAY_TOKEN")

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
