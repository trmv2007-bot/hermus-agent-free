# ☤ Hermus Agent Free

<p align="center">
<strong>A free, open-source, self-hosted AI agent for coding, research, automation and multi-agent work.</strong><br>
Build with local models or free-tier providers. Keep your data, tools and runtime under your control.
</p>

<p align="center">
<a href="https://github.com/trmv2007-bot/hermus-agent-free/stargazers"><img src="https://img.shields.io/github/stars/trmv2007-bot/hermus-agent-free?style=for-the-badge" alt="Stars"></a>
<a href="https://github.com/trmv2007-bot/hermus-agent-free/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT"></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-green.svg?style=for-the-badge&logo=python" alt="Python"></a>
</p>

> **Hermus Agent Free** is a self-hosted agent platform focused on practical autonomy: it can reason over tasks, use tools, delegate work, write and test software, maintain project memory, create reusable skills, run scheduled jobs, and expose a gateway/dashboard for interacting with the agent.

---

## ✨ What Hermus Is

Hermus is designed to be more than a chatbot. A typical task can move through a loop such as:

**Goal → Understand → Plan → Execute → Use tools → Verify → Repair → Produce evidence/artifacts → Learn**

The platform combines an agent runtime, tool system, memory, planning, delegation, verification, sandboxing, automation, gateway APIs and a live control-room dashboard.

It is **self-hosted and open source**. Local models such as Ollama can be used without an API bill; compatible hosted providers can also be configured when you want more capable models.

> **Important:** "free" refers to the software and supported free/local execution paths. Third-party providers may impose their own rate limits, quotas, terms or availability.

## 🚀 Highlights

- 🧠 **Mission Engine** — objective-driven planning, execution, verification, repair and proof.
- 👨‍💻 **SWE Mode** — software-engineering workflow for inspecting, editing, building, testing, debugging, reviewing and packaging projects.
- 🤖 **Multi-agent execution** — subagents, DAGs, delegation trees and parallel workstreams.
- 🏛️ **AI Counsel** — multiple agent roles can propose, critique, deliberate, vote and synthesize a plan.
- 🧩 **Self-improving skills** — successful trajectories can become reusable skills and later be re-evaluated.
- 🧠 **Project memory** — SQLite-backed memory, FTS5 retrieval, optional vector retrieval and memory decay/packing.
- 🔄 **Lessons loop** — corrections and failures can become reusable lessons injected into future reasoning.
- 🧪 **Verification & evaluation** — domain-specific verification plus an offline evaluation harness.
- 🛡️ **Sandboxing** — policy-controlled command execution with available isolation backends and audit trails.
- 🌐 **Gateway** — one process for CLI, webhooks and remote clients.
- 🎛️ **Control Room** — live task progress, agents, telemetry, reasoning and artifacts.
- ⏰ **Automations** — scheduled tasks and natural-language cron workflows.
- 📦 **Artifact tracking** — hashes, metadata and exportable deliverables.
- 🔌 **Provider flexibility** — local Ollama plus configurable compatible hosted/free-tier providers.

---

# 🧭 Core Architecture

## 0. Universal Mission Runtime (one execution core)

Every surface that runs work goes through **one runtime** — behavior no longer
depends on which door the request came in:

```
USER / DASHBOARD / SCHEDULE / CHANNEL / CLI / API
                     │
                     ▼
            core/runtime.execute()
             │                    │
   (goal classifier)      (single ReAct chat turn)
             │
             ▼
        MissionEngine   plan → DAG → execute → observe → verify → critic → repair
             │          (every DAG stage runs an evidence-gated agent loop)
             ▼
     unified result contract (``response`` + mission report / chat fields)
```

- **Entry points wired through it**: `agent.autonomous()`, `POST /command`
  (inline *and* queued), `POST /stream/command`, the job kinds
  `runtime.turn` / `agent.chat` / `agent.autonomous` / `mission.start` /
  `channel.reply` / `swe.develop`, background agents, the cron scheduler, the
  CLI (`hermus run`, `hermus mission start`, `hermus swe run`), Telegram /
  Discord / Slack channel messages, and sub-agent delegation.
- **Evidence-gated success**: a stage whose job is to *perform* work (coder,
  implementation, verification, …) only succeeds when the agent actually did
  something — executed tools, changed files, produced artifacts. Describing
  the work fails the stage with `no_evidence_of_work` and feeds the repair
  loop. No backend configured → the mission is honestly `BLOCKED`, never
  fake-completed.
- **DAG parent-context handoff**: each stage's prompt carries the upstream
  stages' real outputs and artifacts ("Upstream results — build on these"),
  so Researcher → Architect → Coder is an actual chain, not a name.
- **Mid-run steering**: `POST /run/steer` queues an instruction on the run's
  inbox; the active agent loop drains it at the next step boundary and injects
  it into the conversation (`steer_applied` event on the stream). Steering
  reaches the model, not just the UI.
- **Queue-first dashboard**: the dashboard submits `async:true`, so a turn
  runs as a durable gateway job — close the tab and the work keeps going;
  the answer streams back over SSE when you return.
- **Flags**: `HERMUS_MISSION_RUNTIME=0` reverts to the legacy runner,
  `HERMUS_MISSION_AUTO_CLASSIFY=0` disables auto-promotion of goal-like
  messages (only explicit `autonomous`/`mission` requests run missions).

## 1. Autonomous Mission Engine

Start an objective rather than manually driving every individual tool call:

```bash
hermus mission start "Build and test a web application that does X"
```

The mission lifecycle supports:

1. Requirements extraction
2. Dependency-aware plan/DAG generation
3. Execution
4. Evidence collection
5. Domain verification
6. Repair/retry
7. Continuation after recoverable failures
8. Explicit `BLOCKED` states when progress genuinely cannot continue
9. Final proof/artifact reporting

This makes completion measurable instead of relying only on an LLM saying that a task is finished.

## 2. Software Engineer Mode

```bash
hermus swe run "Fix the failing tests and package the project"
```

The SWE loop is built around:

**Inspect → Understand → Plan → Edit → Build → Test → Debug/Repair → Review → Package**

Toolchain detection and repair iterations are used where supported.

## 3. Verification

Hermus includes domain-aware verification for areas such as:

- Python AST and test checks
- Android APK/AAB structure and manifest checks
- Web HTML/CSS/route checks
- Git branch/tree checks
- Linux permissions and port checks
- Research/citation validation

Run verification explicitly with:

```bash
hermus verify run --domain <domain>
```

## 4. Multi-Agent Delegation

Hermus can split work into independent tasks and execute them in parallel.

```bash
hermus subagent spawn "Research X and Y in parallel"
hermus delegate "goal" --task "workstream" --aggregate best
```

The delegation architecture supports:

- dependency-aware DAGs
- parallel workers
- fanout/race/map strategies
- nested delegation trees
- structured child results
- aggregation strategies such as `concat`, `vote`, `best` and `synthesize`
- cancellation of subtrees
- worker/depth budgets

Each child can return structured answers, confidence, evidence, artifacts, tool calls, usage and errors instead of only free-form text.

---

# 🏛️ AI Counsel & Deep Thinking

For harder tasks, Hermus can use a council of specialized agents:

**Chair → Researchers → Critic → Synthesizer → Judge**

The system can:

- generate multiple proposals
- deliberate over competing approaches
- require evidence/objections from critics
- score proposals
- synthesize a voted plan
- execute the selected plan with real tools
- preserve transcripts for later review

Available reasoning strategies include:

- reflexion
- verification passes
- self-consistency
- automatic strategy selection

Example:

```bash
hermus counsel run "Design and implement a reliable solution for X"
hermus counsel status
hermus counsel review
```

The evaluation harness can compare strategies using offline mock models, so reasoning upgrades can be measured instead of assumed to work.

---

# 🧠 Memory & Self-Improvement

Hermus is designed to improve from actual work rather than simply accumulating chat history.

### Memory

- SQLite-backed persistent memory
- SQLite FTS5 search
- optional dense-vector retrieval
- hybrid BM25 + vector retrieval when available
- project-scoped recall
- importance and recency signals
- memory decay and cleanup
- token-budget-aware context packing
- pinned memories

Useful commands include:

```bash
hermus mem2 hybrid "postgres" --explain
hermus mem2 context
hermus mem2 compact
hermus mem2 sweep --apply
```

### Lessons

Corrections, tool failures, reflections and skill failures can be distilled into a lessons store and selectively injected into future system prompts.

### Skill Forge

After a sufficiently complex **verified** trajectory, Hermus can distill reusable work into:

```text
skills/<name>/SKILL.md
skills/<name>/skill.py
```

Generated skills can be smoke-tested, quarantined if invalid, deduplicated and re-scored from later outcomes.

```bash
hermus forge list
hermus forge log
hermus forge run <skill>
```

---

# 🛠️ Tools & Execution

Hermus is intended to operate through tools rather than hallucinating actions.

Depending on configuration, capabilities include:

- shell/terminal execution
- filesystem/project editing
- web search and reading
- browser-oriented fallbacks
- Git operations
- Python execution
- research workflows
- artifact generation
- memory operations
- delegation
- scheduled automation
- platform messaging

Tool results are tracked as part of the task trajectory so later verification and learning can use what actually happened.

## 🔐 Command Sandbox

Local command execution can pass through a policy layer with controls such as:

- CPU/memory/PID/disk limits
- capability dropping
- `no_new_privs`
- read-only root filesystem where supported
- network isolation where supported
- environment allowlisting and secret scrubbing
- dangerous-command filtering
- output limits and ANSI stripping
- structured JSONL audit records

The runtime can select from available isolation backends such as Docker, Podman, bubblewrap or restricted local execution.

Check the effective configuration with:

```bash
hermus sandbox status
```

Sandbox availability depends on the host and installed dependencies. It is not safe to assume that every backend provides identical isolation.

---

# 🌐 Gateway & Platforms

Start the gateway:

```bash
hermus gateway start
```

The default gateway runs on port `8000` and provides a central runtime for supported interfaces.

The repository includes support for integrations such as:

- CLI
- Telegram
- Discord
- Slack webhooks
- WhatsApp/Signal bridges where configured
- web/dashboard clients

The gateway also exposes APIs for commands, agent status, platforms, dashboards, keys/custom APIs, response timing, updates and cache management.

### Live Streaming

The runtime supports resumable event streaming through SSE/WebSocket interfaces, including events such as:

```text
memory_recalled
step_started
llm_delta
tool_call
tool_result
verification
skill_created
mission_state / node_started / node_finished   (mission progress)
steer / steer_applied                          (mid-run steering)
agent_response                                 (full final answer on the stream)
runtime_issue                                  (structured non-fatal failures)
run_finished
```

Jobs can be queued and cancelled, and completed results can survive process restarts when durable job storage is enabled. The dashboard submits its turns **queue-first** (`async:true`), so closing the tab never cancels the work; the final answer arrives over SSE or via `GET /jobs/{id}/result`.

Structured diagnostics for every best-effort subsystem (memory, routing, telemetry, executor, …) are available at `GET /runtime/issues` — each entry carries component, operation, error, mission/run/step ids, retryability and the fallback taken, instead of silent `except: pass`.

### Attachments (drag & drop)

`POST /command` accepts `multipart/form-data` uploads. Text and code files are inlined; binary documents get real extraction — DOCX/ODT/EPUB, XLSX/ODS, PPTX, PDF (with `pypdf` when installed), and ZIP/JAR/APK entry listings — via `core/document_ingest`. Anything not extractable (images, media, unknown binaries) is saved to the workspace `uploads/` dir with its path included in the prompt so vision/OCR/transcription tools can open it.

---

# 🎛️ Control Room Dashboard

Hermus includes a task-first web control room designed for monitoring an autonomous run rather than simply chatting with a model.

The dashboard can expose:

- active missions
- task progress
- sub-goals and state
- active agents
- model usage
- telemetry
- approvals
- evidence
- artifacts
- reasoning/counsel state
- connected system modules
- live agent events

Start the gateway and open:

```text
http://localhost:8000/dashboard          # Main Control Center (Default)
http://localhost:8000/jarvis             # Jarvis 3D Spatial Holographic HUD
http://localhost:8000/computer/dashboard # Autonomous Computer Agent Flight Deck
http://localhost:8000/remote             # Mobile Pocket Remote Deck
```

See [`LIVING_CONTROL_ROOM.md`](LIVING_CONTROL_ROOM.md) for the dashboard architecture.

---

# 📦 Artifacts

Hermus treats important deliverables as first-class artifacts.

Supported workflows can track:

- APK/AAB files
- ZIP archives
- Python wheels
- reports
- diffs
- generated project files
- other task outputs

Artifacts can include SHA-256 hashes and metadata and can be bundled for export.

```bash
hermus artifacts list
hermus artifacts export
```

---

# ⏰ Automation

Hermus includes scheduled automation through APScheduler.

Examples:

```bash
hermus cron add "every Monday 8am weekly audit"
hermus cron list
hermus cron remove <id>
```

Natural-language scheduling can be interpreted into cron rules, with an LLM fallback where configured.

---

# 🤖 Model Providers

Hermus is provider-flexible rather than locked to one paid API.

### Local

[Ollama](https://ollama.com/) is the primary local-model path. A local model can run without an external API bill, subject to your hardware.

Example model families include:

```text
llama
mistral
phi
```

The exact model depends on what you install and what your hardware can handle.

### Hosted / Free Tier

Compatible providers can be configured when desired, including providers offering free tiers. Free-tier limits, model availability and rate limits are controlled by those providers and can change.

**Hermus does not require OpenRouter specifically.**

---

# 💻 Installation

Hermus is a Python project.

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then inspect the available commands:

```bash
hermus --help
```

For the project-specific setup flow, see [`QUICKSTART.md`](QUICKSTART.md) and [`SIMPLE_GUIDE.md`](SIMPLE_GUIDE.md).

> Some capabilities require optional system packages, model runtimes, browser tooling, containers or provider credentials. The base project remains usable without every optional dependency.

---

# ⚡ Quick Start & Installation

### 1. 1-Step Master Installation (Linux / WSL / macOS)

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
bash setup.sh
```

### 2. Launch Gateway & Dashboards

```bash
./hermus-gateway
```
*(Or `./bin/hermus-gateway` or `source activate.sh && hermus-gateway`)*

Open in your browser:
* **🎛️ Control Center & Setup Wizard (Default):** [`http://localhost:8000/dashboard`](http://localhost:8000/dashboard)
* **🌌 Jarvis 3D Holographic Spatial HUD:** [`http://localhost:8000/jarvis`](http://localhost:8000/jarvis)
* **🖥️ Computer Agent Flight Deck:** [`http://localhost:8000/computer/dashboard`](http://localhost:8000/computer/dashboard)
* **📱 Mobile Pocket Remote Deck:** [`http://localhost:8000/remote`](http://localhost:8000/remote)

### 3. Interactive Terminal Agent

```bash
./hermus
```

### Mission

```bash
hermus mission start "Create a small Python application and test it"
```

### SWE task

```bash
hermus swe run "Inspect this project, fix the failing tests and package it"
```

### Counsel

```bash
hermus counsel run "Compare three architectures and recommend the best one"
```

### Jobs

```bash
hermus jobs list
```

---

# ⌨️ Useful CLI Commands

| Command | Purpose |
|---|---|
| `hermus` | Start the interactive TUI |
| `hermus mission start <goal>` | Start an autonomous mission |
| `hermus swe run <task>` | Run the software-engineer workflow |
| `hermus verify run --domain <domain>` | Run domain verification |
| `hermus counsel run <task>` | Use the AI council |
| `hermus counsel status` | View counsel state |
| `hermus subagent spawn <task>` | Spawn a subagent |
| `hermus delegate ...` | Delegate work to an agent tree |
| `hermus forge list` | List forged skills |
| `hermus artifacts list` | List tracked artifacts |
| `hermus jobs list` | List durable jobs |
| `hermus mem2 context` | Inspect packed memory context |
| `hermus sandbox status` | Inspect command-execution sandbox |
| `hermus cron list` | List scheduled jobs |
| `hermus gateway start` | Start the unified gateway |

The exact command surface can evolve; use `hermus --help` for the version currently installed.

---

# ⚙️ Configuration

Hermus supports environment-based configuration for major runtime systems.

Examples include:

```text
HERMUS_COUNSEL_ENABLED
HERMUS_COUNSEL_MIN_DIFFICULTY
HERMUS_COUNSEL_MAX_MEMBERS
HERMUS_COUNSEL_MAX_ROUNDS
HERMUS_COUNSEL_AUTO_REVIEW
HERMUS_THINK_ENABLED
HERMUS_STRATEGY
HERMUS_SELF_CONSISTENCY_K
HERMUS_VERIFY_THRESHOLD
HERMUS_PROJECT
HERMUS_LESSONS_IN_PROMPT
HERMUS_MEMORY_SWEEP_MINUTES
HERMUS_QUEUE_ENABLED
HERMUS_QUEUE_BACKEND
HERMUS_SANDBOX
```

See `.env.example` and the architecture/design documents for the complete configuration surface.

---

# 🧪 Testing & Evaluation

Hermus contains offline tests and evaluation tooling intended to validate core behavior without requiring paid APIs.

Examples:

```bash
python tests/test_counsel_system.py
python tests/test_deepthink.py
python tests/test_phase4.py
python -m core.delegation --self-test
```

Evaluation commands include:

```bash
hermus eval list
hermus eval run --strategy reflexion
hermus eval compare --a reflexion --b self_consistency
hermus eval history
```

The goal is to measure success rate, steps and tool failures instead of treating an LLM's self-report as proof of correctness.

---

# 📚 Architecture & Design Docs

| Document | Purpose |
|---|---|
| [`ARCHITECTURE_UPGRADES.md`](ARCHITECTURE_UPGRADES.md) | Architecture and reliability upgrades |
| [`THINKING_SYSTEM_PLAN.md`](THINKING_SYSTEM_PLAN.md) | Counsel/deep-thinking design |
| [`LIVING_CONTROL_ROOM.md`](LIVING_CONTROL_ROOM.md) | Dashboard/control-room design |
| [`AUTONOMY_BOUNDARIES.md`](AUTONOMY_BOUNDARIES.md) | Autonomy and capability boundaries |
| [`PHASE_A_ROADMAP.md`](PHASE_A_ROADMAP.md) | Development roadmap |
| [`PHASE_C_D.md`](PHASE_C_D.md) | Later architecture phases |
| [`QUICKSTART.md`](QUICKSTART.md) | Quick setup |
| [`SIMPLE_GUIDE.md`](SIMPLE_GUIDE.md) | Beginner-oriented guide |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | Third-party notices |

---

# 🔒 Security & Trust

Hermus can interact with your filesystem, execute commands and use external services. Treat it like any other powerful local automation tool.

Recommended practices:

- use a dedicated environment/workspace for autonomous tasks
- keep API keys in environment/configuration rather than source code
- review permissions before enabling powerful capabilities
- use the strongest available sandbox backend for untrusted work
- inspect audit logs when debugging unexpected actions
- avoid exposing the gateway directly to the public internet without authentication and network controls
- verify generated code and artifacts before deploying them

Hermus provides capability and audit mechanisms, but **no software can guarantee perfect isolation or perfect autonomous behavior**.

---

# 🗺️ Roadmap

Hermus is actively evolving toward a more capable general-purpose agent platform.

Areas of continued development include:

- stronger autonomous coding and app/game building workflows
- broader verification coverage
- better browser/computer-use capabilities
- more robust model routing
- richer mobile/remote control
- improved agent collaboration
- stronger evaluation and regression coverage
- more resilient long-running missions
- better artifact and project lifecycle management
- continued dashboard/control-room improvements

Features should be considered **implemented only when they are present and working in the current repository**; design documents and roadmap items are not guarantees of future functionality.

---

# 🤝 Contributing

Issues, bug reports, tests, documentation improvements and code contributions are welcome.

Before submitting a major change:

1. inspect the existing architecture and design docs
2. keep changes focused
3. add or update tests where practical
4. document new configuration or commands
5. avoid committing secrets, credentials or private data

---

# 📄 License

Hermus Agent Free is released under the **MIT License**. See [`LICENSE`](LICENSE).

---

<p align="center">
<strong>Hermus Agent Free — build, research, automate, remember, verify and improve.</strong>
</p>
