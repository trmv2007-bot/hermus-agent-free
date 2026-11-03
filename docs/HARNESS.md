# HERMUS as an AI Agent Harness

HERMUS ships with two layers under `core/harness/`:

| Layer | Modules | Purpose |
|---|---|---|
| **Runtime primitives** | `bus`, `files`, `sessions`, `compaction`, `memory_graph`, `swarm`, `runtime` | jcode-inspired machinery the ReAct loop uses every turn (session ≠ window, file-shift notices, swarm messages, compaction, cascade memory). |
| **Eval harness** | `tasks`, `verify`, `runner`, `report` | The outer layer that makes HERMUS usable *as a harness*: define task suites → run the agent against each task in an isolated workspace → verify → report. |

The eval harness is what turns "an agent" into "a measurable harness": the same
agent loop (`core/agent.py`) is executed per task under budgets, and the result
is scored by checks, not by vibes.

```
harness/suites/*.json          committed task suites (ships with: smoke)
core/harness/tasks.py          suite loading + Task model
core/harness/runner.py         isolated run per task, timeouts, artifacts, index
core/harness/verify.py         check engine (response / file / command / tools)
core/harness/report.py         markdown reports + A/B run comparison
data/harness/runs/<run_id>/    report.json, report.md, tasks/<id>/{task,result,trajectory}.json, workspace/
```

## Quick start

```bash
# list committed suites and their tasks
hermus harness suites
hermus harness tasks smoke

# run a suite against the real agent (offline with the mock model)
hermus harness run smoke --model mock/mock --tag demo

# with a real model
hermus harness run smoke --model groq/llama-3.3-70b-versatile

# subsets and budgets
hermus harness run smoke --only write_hello,nested_dir --max-steps 10 --timeout 120
hermus harness run path/to/my_suite.json --limit 5

# history / inspection / A-B
hermus harness runs
hermus harness show <run_id>            # partial id ok
hermus harness report <run_id>          # markdown
hermus harness compare <run_a> <run_b>  # regressions / fixes / winner
```

`run` prints live per-task progress, then a summary line and the report path.
Exit code is 0 even when tasks fail — the harness *measures*, it does not crash;
use `--json` for the full machine-readable report.

## Task suite format

A suite is a JSON file — either a bare list of tasks (compatible with
`tests/eval/benchmark_tasks.json`) or `{"name": ..., "description": ..., "tasks": [...]}`.
Builtin suites live in `harness/suites/` and are addressed by name (`smoke`);
any path to a JSON file also works.

```json
{
  "name": "my_suite",
  "tasks": [
    {
      "id": "write_hello",
      "category": "file",
      "prompt": "Create hello.txt containing exactly one line: hello harness",
      "files": {"README.md": "# Demo\n"},
      "setup": ["python3 -c \"open('seed.txt','w').write('1')\""],
      "checks": [
        {"type": "file_exists", "path": "hello.txt"},
        {"type": "file_contains", "path": "hello.txt", "value": "hello harness"}
      ],
      "max_steps": 6,
      "timeout_s": 120,
      "metadata": {"difficulty": "easy"}
    }
  ]
}
```

| field | meaning |
|---|---|
| `id` | unique within the suite (required) |
| `prompt` | what the agent is asked to do (required) |
| `category` | grouping label in reports (default `general`) |
| `files` | `{relpath: content}` seeded into the task workspace before the run |
| `setup` | shell commands run in the workspace before the agent starts; a non-zero exit fails the task with `setup_failed` |
| `checks` | list of check dicts (below) |
| `max_steps` / `timeout_s` | per-task step budget / wall-clock budget for the agent turn |
| `metadata` | free-form, copied into the report |

## Check types

Every check is `{"type": ..., ...}` plus optional `required` (default `true`).
**A task succeeds iff all required checks pass**; `score` = passed/total.
Optional (`required: false`) checks only affect the score.

**Response checks** (against the agent's final text):
`substring`, `not_substring`, `regex`, `length_gt`, `length_lt` — each with `value`;
`tool_called` `{name}` (a called tool whose name contains `name`);
`json` `{key, value?}` — the response contains a JSON object (fenced or raw);
`key` is a dotted path (`top`, `items.0.id`) that must exist, and optional
`value` is a case-insensitive substring match on the extracted value.

**File checks** (paths relative to the task workspace):
`file_exists`, `file_not_exists`, `file_contains` (`path`, `value`),
`file_regex` (`path`, `pattern`).

**Command checks** (shell, run inside the task workspace):
`command` with `cmd`, `expect_exit` (default 0), `stdout_contains`,
`stdout_not_contains`, `stdout_regex`, `timeout` (default 30s).

**Combinators:** `all` / `any` with nested `checks`.

## What a run produces

```
data/harness/runs/<run_id>/
├── index.json                  # appended to the global run history
├── report.json                 # full machine-readable report
├── report.md                   # human-readable report (tables + failure details)
└── tasks/<task_id>/
    ├── task.json               # the task as run
    ├── result.json             # status, success, checks, response excerpt, steps, tool calls, timings
    ├── trajectory.json         # the agent's own trajectory for this task
    └── workspace/              # the isolated workspace the agent worked in (--no-keep deletes it)
```

Statuses: `ok` (ran to completion; success decided by checks), `setup_failed`,
`timeout` (wall-clock budget exceeded — recorded, run continues), `error`
(agent crashed — recorded with the exception).

## Python API

```python
from core.harness import HarnessRunner, RunOptions, resolve_suite, verify_task

runner = HarnessRunner()  # artifacts under data/harness/runs
report = runner.run_suite(
    "smoke",  # builtin name or path to a suite JSON
    RunOptions(model="mock/mock", limit=3, tag="ci", timeout_s=120),
)
print(report["summary"]["success_rate"])

runner.list_runs()  # history
runner.load_run(report["run_id"])  # full report
runner.compare(run_a="...", run_b="...")  # A/B dict; runner.compare_markdown(...) for md

# verify anything directly
verify_task(
    {"checks": [{"type": "file_contains", "path": "out.txt", "value": "ok"}]},
    workspace="/some/dir",
    response="...",
    tool_calls=["file_write"],
)

# plug in a different agent under test (the harness is agent-agnostic)
RunOptions(agent_factory=lambda task, ws, *, model, max_steps: MyAgent(ws))
```

The default agent factory builds a real `core.agent.HermusAgent`
(`mode="agent"`), so `hermus harness run` measures the actual agent — including
tools, memory and the ReAct loop — not a stub.

## Design notes

- **Isolation:** each task gets its own workspace directory; the agent runs with
  that directory as cwd, so file/shell tools operate inside it. Setup commands
  run there too, before the agent starts.
- **Honest failure:** timeouts and crashes are recorded as statuses, never
  silently retried into a pass. A stuck agent thread is abandoned (the run moves
  on) and the task is marked `timeout`.
- **Offline-safe:** `--model mock/mock` needs no keys and no network; the builtin
  `smoke` suite mixes response/tool/command checks (mock-passable) with
  file-writing checks (need a real model) so a mock run demonstrates both a
  pass and an honest fail.
- **Builtin suites:** `smoke` (harness mechanics), `fairy` — the agent plays
  ZZZ's FAIRY proactive attention layer: triage world signals (`TOP: <id>`),
  emit a JSON attention report, write `attention.md`, stay `IDLE` when all
  clear — and `sentinel` (`harness/suites/sentinel.json`) — `driver: "sentinel"`
  tasks feed synthetic events to the real always-alert core and verify its
  instant decisions + side effects (`alerts.jsonl`, `enqueued.jsonl`), and
  `ambient` (`harness/suites/ambient.json`) — `driver: "ambient"` tasks script
  `ticks` (attention snapshots) and verify `attention.raised` / `attention.cleared`
  change detection, and `watchdog` (`harness/suites/watchdog.json`) — the
  watchdog×sentinel loop: fix applied → enqueue (no alert), fix failed →
  alert+enqueue, repeated failures → rule proposal. Runs against any `--model`
  you have configured. See `docs/SENTINEL.md` and `docs/ALWAYS_ON.md`.
- **Pluggable:** `RunOptions.agent_factory` swaps the agent under test, which is
  how the test suite (`tests/test_harness_runner.py`) drives the runner with a
  scripted agent.
- **Isolated + parallel:** `--isolated` runs each `driver: "agent"` task in its
  own subprocess (own cwd, so no shared-`chdir` races — this also fixes the
  process-wide cwd caveat), `--workers N` runs up to N tasks concurrently, and
  every report carries `total_tokens_est` / per-task `tokens_est` (estimated
  with `core/token_counter.py`). `sentinel` / `ambient` driver tasks always
  stay inline.
- **Easy mode:** `hermus check` = env sanity + the always-on suites on
  `mock/mock` with a scoreboard and a CI exit code (offline, no keys).
  `hermus harness run` with **no suite argument runs every builtin suite** and
  ends with a scoreboard; `hermus doctor --harness` shows always-on health.
- **Product surface:** every run is indexed (`data/harness/runs/index.json`) with
  pass rate, tokens, cost estimate, budget/regression flags. CLI:
  `harness runs --suite S --model M`, `harness show <run_id> [--json]`,
  `harness report <run_id>`, `harness compare A B`, `harness suites --stats`
  (per-suite runs, avg success rate, **flaky tasks**), and
  `harness schedule --suite fairy --at "4:00 am"` to register the nightly
  product eval as a cron job (`--remove` to undo). API (gateway-auth gated):
  `GET /api/harness/suites?stats=1`, `/api/harness/runs?suite=&model=`,
  `/api/harness/latest?suite=`, `/api/harness/stats`, `/api/harness/runs/{id}`,
  `/api/harness/compare?a=&b=`.
- **Regression alerts:** after each run the harness compares against the
  previous run of the same suite+model and publishes `harness.run.completed` /
  `harness.run.failed` / `harness.regression` on the EventBus — the sentinel
  alerts on regressions (weight 0.85) and enqueues follow-up on failed runs, so
  a worsening suite pages you through the same always-on core.
- **Budgets + CI gate:** `--max-tokens N` flags the run `budget_exceeded` when
  estimated tokens pass the budget; `--strict` exits 1 on any failed task or an
  over-budget run. `make selfcheck` runs the always-on suites
  (`sentinel`, `watchdog`, `ambient`) at 100% on `mock/mock` with `--strict` —
  fully offline.
