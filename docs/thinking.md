# Thinking — routing, a visible plan, and verification that leaves a receipt

Three claims a companion makes when it thinks: it picks the right amount of
machine for the job, it shows the work before it starts, and it can prove what
it did. In this repo all three existed as modules and none of them reached a
turn. This document is what changed, and the numbers measured on Rishi's Windows
PC with `llama3.2:1b`.

Code: [`core/thinking/`](../core/thinking/) · endpoints:
[`gateway/routes_thinking.py`](../gateway/routes_thinking.py) · harness:
[`tests/eval/thinking_bench.py`](../tests/eval/thinking_bench.py)

---

## 1. Routing, with the reason written down

`core/thinking/routing.py` decides one thing per turn: **the local model, or a
free-tier hosted key**. The decision is a pure function of the request text, the
bound local model and the configured free keys — same input, same answer, no
network, no tokens.

| difficulty | local bound | free key | decision | reason recorded |
|---|---|---|---|---|
| 1–2 | yes | any | local | "the local model's ceiling covers this, so the free tier is not spent" |
| 3–5 | yes | yes | free hosted | "needs more reasoning than the local model reliably gives; escalating" |
| 3–5 | yes | no | local | "would escalate, but no free-tier key is configured; the local model may be too weak" |
| any | no | yes | free hosted | "no local model is bound, so the free hosted tier is the cheapest option" |
| any | no | no | unavailable | "nothing free to run on" |

Two guards matter more than the table:

- **Paid cannot enter.** Only `ollama`/`nollama`/`lmstudio`/`local` (local) and
  `groq`/`mistral`/`openrouter`/`cerebras`/`together`/`gemini` (free tier) are
  candidates. Anything else — `openai/...`, a typo — is dropped before the
  decision, so a stray env var cannot quietly route a turn to a paid API.
- **One selector.** When the thinking layer has decided, the older
  task-type router (`core/router2.py`) stands down for that turn instead of
  fighting it. The recorded reason and the receipt belong to the decision that
  actually ran.

### Over- and under-routing are counted, not guessed

After the turn, `routing_outcome()` labels the decision:

- **over-routed** — a hosted turn that finished within 2 steps. That capacity
  was spent on something the local model would have done.
- **under-routed** — a local turn with 2 or more failed tool calls. The task was
  beyond the local model, and the person paid for it in retries.

Both land in `data/thinking/routing.jsonl` (override with
`HERMUS_THINKING_DIR`) with steps, failed calls, verification and the reason.
`GET /api/v1/thinking/routing` returns the totals; `GET
/api/v1/thinking/routing/recent` returns the decisions with their reasons.

The under-routing label is also actionable: when a local turn is under-routed
and a free key is configured, the repair turn in §3 runs on the hosted tier
instead of retrying the model that already failed twice.

---

## 2. A plan the person sees before anything is touched

`core/thinking/plan.py` decides whether a request needs a plan and, when it
does, writes one **before the first model call** — deterministic, from the
request's own clauses, zero tokens:

- a request that changes something (`file_write`, shell, rename) always gets a
  plan: read the current state → make the change → verify it;
- a request with several clauses ("create X, then write Y, then list them") gets
  one step per clause, plus a final check;
- questions and explanations never get one, and neither does chat mode.

The plan is emitted as a `plan_ready` event — a four-line list, not a log — and
the turn is told to work through it. As the turn runs, `PlanProgress` watches
tool results and advances the list:

- a step turns `done` only when a tool of its own category succeeded
  (`file_write` → the change step; `shell_execute` → the run step);
- a failed tool marks its step `failed` and leaves it in view;
- a step nothing touched is `skipped` at the end, with "no tool result backed
  this step" as its evidence. It is never shown as done.

Each step carries the evidence that advanced it. `plan_step` events stream as it
happens, so a room can render progress instead of a spinner.

---

## 3. Verification: "done" carries a receipt, or says it could not

`core/thinking/receipt.py` answers one question: **what can be re-checked?** For
every mutating tool result it produces evidence — a file read back and hashed
(`sha256:…`), a command with its exit status and stdout, a URL with what came
back. The verdict is the repo's own `core/critic.py` outcome verifier, fed those
facts rather than prose.

| what happened | verdict |
|---|---|
| a file was written and reads back | `verified`, receipt cites the hash |
| a command ran, exit 0 | `verified`, receipt cites the command and stdout |
| a command failed, or a write did not land | `unverified` + the reason |
| the answer claims work; nothing changed | `unproven_claim` |
| the request asked for a change; nothing changed | `unmet` |
| the answer declines with a reason | `refused` (honest, not a failure) |
| a question answered, nothing changed | `not_applicable` |

A failed receipt earns **exactly one repair turn** (`HERMUS_THINKING_REPAIR_ENABLED`
turns it off): the model is told what could not be proven and must fix it with
tools or state plainly that it could not. There is never a second attempt, and
an unverified answer carries the line `Not verified: …` so a person reading the
transcript cannot mistake it for success. A verified one carries
`Verified: <file> sha256:… 12B`.

**What a receipt does not prove.** It proves a change happened and is readable —
not that the change is what was asked for. The benchmark in §4 checks the world
separately, and the two disagree often enough to be worth stating: on
`llama3.2:1b` a turn can write the wrong file and still hold an honest receipt.

---

## 4. The benchmark, and what a 1B model can actually do

`tests/eval/thinking_bench.py` runs 15 fixed tasks through
`core.runtime.execute` — the same path `/command` uses — with each task in its
own process, its own `HERMUS_HOME`, and its own sandbox. `~/.hermus` is never
touched. A task passes only when a deterministic check on the world says so.

```
python tests/eval/thinking_bench.py --model ollama/llama3.2:1b \
    --work .bench --out bench.json --markdown bench.md
```

Task set: 5 file tasks (create, edit, read, append, UTF-8), 2 shell tasks, 1 web
fetch against a local page, 2 multi-step tasks (one asserts the plan had 3+
steps and one verified step), 2 must-refuse tasks, 2 plain answers, 1 honesty
task (fetch a 404 and admit it).

### Results on this machine (Windows 11, i7-12700F, Ollama 0.40.16)

<!-- numbers: filled from the run below, do not edit by hand -->

**`ollama/llama3.2:1b`** — see the table produced by the run recorded in the pull
request. Summary of what it showed:

- **Single tool call, one action: it works.** `file_create`, `file_edit`,
  `file_read`, `file_append`, `file_unicode` and both shell tasks complete in
  2–4 steps each when the request names one tool and one path.
- **Multi-step work: it forgets the middle.** The model abandons the plan after
  the first successful write and either repeats the first action until the step
  budget stops it or narrates the remaining steps in prose.
- **Argument fidelity is the hard limit.** The model frequently emits
  `arguments` that echo the JSON schema instead of values
  (`{"type":"string","required":[…],"properties":{…}}`), so a call can be
  well-formed and still write the wrong thing. This is the reason a few file
  tasks fail at the check even though a tool ran.
- **Refusal and honesty are the surprise.** A 1B model refuses destructive
  requests cleanly and admits a failed fetch. Where it fails on those tasks it
  fails by *acting*, not by lying about it.
- **No free-tier key was configured on this machine**, so every turn routed
  `local` and the hosted escalation in §1 could not be measured — that number is
  unverified here and should be re-run with `GROQ_API_KEY` or
  `MISTRAL_API_KEY` set.

### What this says about "advanced thinking"

The thinking stack is real and now visible, but on a 1B local model the ceiling
is **one action per turn with correct arguments**. Routing is what makes that
ceiling livable: easy turns stay on the local model, and the moment a turn is
classified hard or is under-routed twice, the truth is written into the ledger
and — with a free key — the work moves to a model that can hold a plan. On a
machine with no free key, the honest outcome is a receipt that says the turn was
not verified. That is the intended behaviour, not a defect.

---

## 5. Configuration

| variable | default | effect |
|---|---|---|
| `HERMUS_THINKING_ENABLED` | `1` | the whole layer: routing, plan, receipt |
| `HERMUS_THINKING_ROUTING_ENABLED` | `1` | tier decision only |
| `HERMUS_ROUTE_TIER` | `auto` | `local` or `free_hosted` pins the tier |
| `HERMUS_THINKING_REPAIR_ENABLED` | `1` | the single repair turn |
| `HERMUS_THINKING_DIR` | `data/thinking` | where the routing ledger lives |

## 6. Tests

`tests/test_thinking_routing.py` (17), `tests/test_thinking_plan.py` (14),
`tests/test_thinking_receipt.py` (13), `tests/test_thinking_turn.py` (10),
`tests/test_thinking_bench.py` (15). All deterministic against a scripted agent
— none of them calls a model. The model itself is measured by the benchmark,
which is a report, not a test.
