# Hermus Agent - Free Version (Hermes Clone)

**Completely free, open-source, self-improving AI agent - No paywalls, no OpenRouter fees**

> Original: Hermes Agent by Nous Research (64k+ stars, Feb 2026) - The agent that grows with you
> This is a **100% free clone** built from scratch with free models and tools

## Why Free Version?

Original Hermes uses:
- OpenRouter (paid API for hundreds of models)
- Some premium features / hosted gateways
- Honcho paid user modeling

**This free version uses:**
- **Ollama** local LLMs (llama3, mistral, phi3) - 100% free, runs on your laptop/$5 VPS, no API key
- **Groq free tier** + **HuggingFace Inference free** as optional cloud free fallbacks
- **DuckDuckGo** free search (no API key)
- **SQLite FTS5** for memory (built-in, no vector DB cost)
- **File-based skills** compatible with agentskills.io open standard
- **All gateways free** - Telegram Bot API free, Discord free, CLI free

## Features (Parity with Original)

### 1. Real Terminal Interface - `hermus` CLI
- Full TUI with multiline editing (prompt_toolkit)
- Slash-command autocomplete: `/new`, `/model`, `/skills`, `/platforms`, `/compress`, `/usage`
- Conversation history, interrupt-and-redirect (Ctrl+C), streaming tool output
- Voice memo transcription (Whisper via faster-whisper free)

### 2. Lives Where You Do - Gateway
- Single gateway process: `hermus gateway start`
- Platforms: Telegram, Discord, Slack (webhook), WhatsApp (via free bridge), Signal (via signal-cli), CLI
- Cross-platform continuity - start on Telegram, continue on CLI
- File upload handling

### 3. Closed Learning Loop (The Magic)
- **Agent-curated memory:** After each task, agent decides what to remember
- **Periodic nudges:** Cron that asks "Anything from yesterday you want to persist?"
- **Autonomous skill creation:** After complex task (3+ tool calls), LLM analyzes trajectory and creates reusable skill in `skills/` as Python + markdown
- **Skills self-improve:** Each time skill is used, it logs success/failure and LLM improves it
- **FTS5 session search + LLM summarization:** `SELECT * FROM sessions WHERE sessions MATCH ?` then LLM summarizes for recall
- **User modeling (free Honcho alternative):** Builds `data/user_model.json` with preferences, project structure, workflows via dialectic questions
- **Compatible with agentskills.io:** Skills are `SKILL.md` + `skill.py` format

### 4. Scheduled Automations
- Built-in cron scheduler with APScheduler
- Natural language: "daily at 9am send me report" -> parsed to cron via LLM or simple parser
- Delivery to any platform: Telegram, Discord, etc.
- `hermus cron add "every Monday 8am weekly audit"`

### 5. Delegates and Parallelizes
- Spawn isolated subagents: `hermus subagent spawn "research X and Y in parallel"`
- Subagents write Python scripts that call tools via RPC (zero-context-cost turns)
- Collapsing multi-step pipelines: `write_python_tool()` that chains search+read+write in one turn

### 6. Runs Anywhere
- Seven backends abstracted:
  - `local` - your laptop
  - `docker` - container
  - `ssh` - remote VPS
  - `singularity` / `modal` / `daytona` / `vercel` - serverless persistence (hibernate when idle, wake on demand) - implemented as free Docker + SSH fallback
- Works on $5 VPS (Hetzner, DigitalOcean) or GPU cluster

### 7. 40+ Free Tools
- `web_search` - DuckDuckGo free (no API key)
- `file_read`, `file_write`, `file_edit`, `file_search`
- `shell` - safe subprocess with timeout
- `browser` - Playwright optional free
- `vision` - LLaVA via Ollama free vision
- `python_exec` - run python
- `memory_search`, `memory_add`
- `skill_create`, `skill_use`
- `cron_add`, `cron_list`
- `subagent_spawn`

### 8. Research-Ready
- Batch trajectory generation
- Trajectory compression for training next-gen tool-calling models
- Logs in `data/trajectories.jsonl`

## Quick Start (100% Free, No API Keys)

### Option 1: Ollama (Fully Free, Offline)

```bash
# Install Ollama https://ollama.com
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b  # or mistral, phi3:mini

# Install Hermus Free
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
pip install -r requirements.txt

# Start chatting (uses Ollama local, no API key)
python -m core.agent --model ollama/llama3.1:8b

# Or TUI
python -m tui.tui
```

### Option 2: Groq Free Tier (Free Cloud, Fast)

```bash
# Get free Groq key https://console.groq.com/keys (free tier 30 req/min)
export GROQ_API_KEY=gsk_...
python -m core.agent --model groq/llama-3.1-70b-versatile
```

### Option 3: HuggingFace Free Inference

```bash
export HF_TOKEN=hf_...  # free token https://huggingface.co/settings/tokens
python -m core.agent --model hf/mistralai/Mistral-7B-Instruct-v0.3
```

## CLI vs Messaging Quick Reference (Same as Original)

| Action | CLI | Messaging |
|--------|-----|-----------|
| Start chatting | `hermus` | Telegram/Discord message bot |
| Start fresh | `/new` or `/reset` | `/new` |
| Change model | `/model ollama/llama3.1:8b` | `/model` |
| Set personality | `/personality friendly coder` | `/personality` |
| Retry/undo | `/retry, /undo` | `/retry, /undo` |
| Compress context | `/compress, /usage, /insights` | same |
| Browse skills | `/skills` or `/<skill-name>` | `/<skill-name>` |
| Interrupt | Ctrl+C | `/stop` |
| Platform status | `/platforms` | `/status, /sethome` |
| Gateway | `hermus gateway setup` + `hermus gateway start` | - |
| Cron | `hermus cron add "daily 9am report"` | - |

## Project Structure

```
hermus-agent-free/
├── core/
│   ├── agent.py          # Main loop: memory search -> skill load -> tool calls -> response -> curate memory
│   ├── llm.py            # Free LLM abstraction: Ollama, Groq free, HF free, mock
│   ├── memory.py         # SQLite FTS5 sessions, curated memory, nudges, summarization, user modeling
│   ├── skill_manager.py  # Autonomous skill creation + self-improvement, agentskills.io compatible
│   └── config.py         # Config
├── tools/
│   ├── web_search.py     # DuckDuckGo free
│   ├── file_tools.py     # Read/write/edit/search
│   ├── shell.py          # Safe shell
│   ├── browser.py        # Playwright free
│   └── vision.py         # Ollama LLaVA free
├── gateway/
│   ├── gateway.py        # FastAPI single process gateway, cross-platform continuity
│   ├── telegram.py       # python-telegram-bot free
│   ├── discord.py        # discord.py free
│   └── cli.py            # CLI gateway
├── scheduler/
│   └── cron.py           # APScheduler + natural language parser
├── subagents/
│   └── subagent.py       # Spawn isolated subagents, RPC
├── tui/
│   └── tui.py            # prompt_toolkit TUI with autocomplete, multiline, streaming
├── skills/               # Auto-generated skills (SKILL.md + skill.py)
├── data/
│   ├── memory.db         # SQLite FTS5
│   ├── user_model.json   # Free Honcho alternative
│   ├── trajectories.jsonl
│   └── sessions/
└── tests/
```

## How Free Clone Handles Paywalled Parts

| Original Paywalled | Free Alternative Here |
|--------------------|----------------------|
| OpenRouter (paid API for 100s models) | Ollama local (llama3, mistral, phi3) - 100% free offline + Groq free tier + HF free inference |
| Honcho dialectic user modeling (paid) | `data/user_model.json` - LLM asks dialectic questions "What matters to you?" and builds model free |
| Hosted gateway / Modal/Daytona paid | Docker + SSH + local - free, plus free tier Modal/Daytona if you have account, but fallback to local |
| Premium voice transcription | faster-whisper (free, local Whisper) |
| Vector DB Pinecone paid | SQLite FTS5 (built-in, free, no API key) |

## Example: Skill Auto-Creation

After you ask: "Research Python async + write a file watcher that emails me on changes"

Agent does 5 tool calls: web_search, file_read, file_write, shell test, etc.

Then **autonomous skill creation triggers**:

`skills/file_watcher_emailer/`
- `SKILL.md` - Description, when to use, examples
- `skill.py` - Reusable function `watch_and_email(path, email)`

Next time you say "watch my logs folder", it reuses skill in 1 tool call instead of 5 = **zero-context-cost**.

Skills self-improve: Each use logs success/failure, LLM edits skill.py to improve.

## Gateway Setup (Free Telegram Example)

```bash
# 1. Talk to @BotFather on Telegram, /newbot -> get token (free)
export TELEGRAM_BOT_TOKEN=123456:ABC...

# 2. Setup gateway
python -m gateway.gateway setup --platform telegram

# 3. Start gateway (single process for all platforms)
python -m gateway.gateway start
# Now message your bot on Telegram - it has full memory + skills

# Add Discord free
export DISCORD_BOT_TOKEN=...
python -m gateway.gateway setup --platform discord
```

## Cron Example (Natural Language, Free)

```bash
# CLI
hermus cron add "every day at 9am send me a report of yesterday's commits"
hermus cron add "every Monday 8am weekly audit of my codebase"

# Or in chat
You: "Remind me every day at 9am to check logs"
Agent: Parses to cron "0 9 * * *" and adds via scheduler/cron.py, delivers to your current platform (Telegram/Discord/CLI)
```

## Subagents Example

```
You: "Research best Python async libraries and best Rust async libraries in parallel"

Agent: Spawns 2 subagents via subagents/subagent.py
- Subagent 1: web_search "Python async libraries 2024" -> file_write report_python.md
- Subagent 2: web_search "Rust async libraries 2024" -> file_write report_rust.md
Both run isolated, then main agent merges reports = parallel workstreams, zero-context-cost.
```

## Free vs Paid - 100% Free, MIT License

This clone is MIT, no tracking, no paywall, fully self-hosted. You own data in `data/memory.db` (SQLite file you can open).

## Roadmap

- [x] Core agent loop with free LLM
- [x] SQLite FTS5 memory + curated memory + nudges
- [x] Autonomous skill creation + self-improvement (agentskills.io compatible)
- [x] Free tools: web_search DuckDuckGo, file, shell, python_exec
- [x] Gateway: CLI + Telegram + Discord (free Bot APIs)
- [x] Cron scheduler natural language
- [x] Subagents parallel + RPC
- [x] TUI with slash commands autocomplete
- [ ] Browser automation Playwright free
- [ ] Vision LLaVA via Ollama free
- [ ] Voice memo transcription faster-whisper free
- [ ] More backends: Docker, SSH, Modal free tier, Daytona
- [ ] Trajectory batch generation + compression

## Contributing

PRs welcome - keep it free, no paid APIs required for core.

## Disclaimer

This is a community free clone, not affiliated with Nous Research. Original Hermes Agent is by Nous Research (hermes-agent.org). This free version respects original MIT license and aims for feature parity with 100% free stack.

---

**Hermus Agent Free - The agent that grows with you, for free, forever on your $5 VPS.** ☤
