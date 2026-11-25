# Developers

Everything `hermus --help` lists under **Developers** is here, with one line per
command. None of it is needed to use the product. The three commands a person
needs are on the first screen of `hermus --help`:

```
hermus              install it, or start it and open the room
hermus undo         undo the last thing it changed
hermus understand   say what it can and cannot do on this machine
```

`hermus tui` is the terminal chat that used to be the default, kept for working
without a browser.

---

## From a clone

```bash
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
./setup.sh            # Windows: .\setup.cmd
```

`setup.sh` / `setup.ps1` own host detection and packages; `bootstrap.py` owns the
virtualenv, dependencies, data layout and the live Doctor.

```bash
./setup.sh --verify-only     # change nothing, just run the Doctor
./setup.sh --repair          # repair a broken .venv and retry
./setup.sh --skip-optional   # required packages only
```

## Gates

```bash
make test        # pytest tests/ (skips tests/eval), about a minute
make lint        # ruff, whole tree, line length 130
make typecheck
make selfcheck   # offline self-test on mock/mock with a scoreboard
```

`test`, `lint` and `typecheck` must stay green. New behaviour gets a test; the
README's build-status table cites the test behind every row. Suites that need a
home must be given a temporary one:

```bash
HERMUS_HOME="$(mktemp -d)" HERMUS_DATA_DIR="$HERMUS_HOME/data" python -m pytest -q tests
```

`tests/conftest.py` refuses to start a session whose `HERMUS_HOME` is inside the
real user home, because a suite once deleted a person's setup record.

## Running it

| Command | What it does |
|---|---|
| `hermus` | Install if needed, start the gateway, open the room |
| `hermus gateway start [--port N]` | The gateway on its own, in this terminal |
| `hermus tui` | Terminal chat |
| `hermus doctor` | Full installation and runtime diagnosis |
| `hermus bootstrap` | Setup and health in one idempotent command |
| `hermus check` | Offline selfcheck on `mock/mock` with a scoreboard |
| `hermus update` | Pull a newer version from GitHub |
| `hermus jobs list`, `jobs status <id>`, `jobs events <id>` | Inspect the durable job log |

## What it knows and what it may change

| Command | What it does |
|---|---|
| `hermus understand [--json]` | The capability section the prompt carries, with the probe behind it |
| `hermus improve "<goal>" [--plan-only]` | Draft and test a change to itself in an isolated worktree; never applies it |
| `hermus presence status\|start\|stop\|install` | The desktop presence shell, its live state and its heartbeat |
| `hermus powers list\|registry` | The capability ledger: current, missing and discovered powers |
| `hermus profile create\|list\|use` | Agent personas with independent memory and system prompt |
| `hermus skill list`, `hermus forge …` | Skills, and harvesting, validating and quarantining them |
| `hermus mem2 …`, `hermus embed …` | Typed long-term memory and semantic indexing |
| `hermus context get\|preference\|goal\|focus\|project` | Persistent personal context |

## Safety and undo

| Command | What it does |
|---|---|
| `hermus undo [id]`, `hermus undo --list` | Undo the last journaled action, or list and undo one by id |
| `hermus perms …` | ALLOW / ASK / DENY per tool |
| `hermus safety report\|preflight\|scan-folder` | Autonomy safety reports and the preflight scan |
| `hermus emergency stop\|resume\|status` | The global red-line stop |
| `hermus rollback …` | Checkpoints and transactional rollback |
| `hermus sandbox status\|run\|python` | Sandbox backends, and running a command or code in one |
| `hermus verify …` | Domain verification subsystem |

## Work, agents and missions

| Command | What it does |
|---|---|
| `hermus run "<goal>"` | The autonomous plan / act / verify / repair loop |
| `hermus mission start\|status\|list` | Objective-driven missions with verification |
| `hermus plan …` | DeepThink plan persistence and resume |
| `hermus agent start\|list\|stop` | Persistent background agents |
| `hermus subagent …`, `hermus delegate …` | Parallel sub-agents and JSON-RPC workers |
| `hermus counsel run "<question>"` | A council of models that plans, critiques and synthesises |
| `hermus swe run "<task>"` | Software-engineering mode: inspect, edit, build, test, review |
| `hermus research "<question>"` | Multi-source web research with citations |
| `hermus computer …`, `hermus screen …` | The computer agent, and screen recording/verification |
| `hermus eval …` | Measure thinking strategies |

## Models, routing and harnesses

| Command | What it does |
|---|---|
| `hermus harness run\|models\|compare` | Task suites against any configured model, with reports |
| `hermus router choose` | Per-step model selection |
| `hermus fleet …` | Distribute tasks across several models and keys |
| `hermus multikey …` | Any OpenAI-compatible key: health, models, rate limits |
| `hermus multiai …` | Several AIs talking to each other |
| `hermus api …` | Register a custom HTTP API as a tool |
| `hermus engine status\|install\|start\|download` | Local engine detection and model downloads |
| `hermus tools`, `hermus mcp …` | The tool registry, and external MCP servers |

## Reacting to the machine

| Command | What it does |
|---|---|
| `hermus sentinel run\|status\|install\|alerts` | The always-alert core: reacts to events without polling |
| `hermus awareness [--pause\|--resume]` | Read-only PC awareness: focused window, power, idle, displays |
| `hermus cron add\|list\|status\|enable\|remove` | Natural-language scheduling |
| `hermus watchdog [error]` | Classify and repair failures |
| `hermus artifacts list\|export` | Artifact-centric workspace explorer |
| `hermus workspace …` | Per-project isolation |

## Surfaces

| Address | What it is |
|---|---|
| `/` | The welcome flow while nothing can answer, the companion once it can |
| `/welcome` | The three first-run choices and the model proof |
| `/companion` | The room: character, one input line, Systems |
| `/control` | The developer console: the probe panels, engine detail, the doctor |
| `/docs` | OpenAPI |
| `/readyz` | Real readiness, the same answer the boot line prints |

The console is for bug reports and for watching a running system. It is reached
from the room's Systems sheet, under Diagnostics, and the room never renders it.

## Environment

Every field is overridable with a `HERMUS_` prefix (`HERMUS_MODEL`,
`HERMUS_HOME`, `HERMUS_GATEWAY_PORT`, …). `HERMUS_MOCK_UI=1` enables the console's
mock fixture layer, which is a design tool and is disabled otherwise. Optional
packages degrade to an explicit `unavailable` state with a reason; they never
break import, startup or a test. `requirements-optional.txt` lists them.
