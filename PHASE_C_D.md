# Phase C & D: Power & Polish

Phase A (foundation) and Phase B (reliability) are complete. This document
summarizes Phase C (make it powerful) and Phase D (polish) and how to use what
was added on top of the existing architecture.

---

## Phase C — make it powerful

### 9. Skill optimization ✅

`core/computer/skills.py` already stores evidence-backed skills (procedure,
success rate, durations, failures, repairs, visual states). Added a
**reliability profile** so the planner and dashboards can read a skill at a
glance:

```bash
hermus computer skills            # ranked list with reliability analytics
# API
GET /computer/skills/{skill_name}/profile
```

```json
{
  "name": "install-firefox",
  "task": "Install Firefox",
  "runs": 28,
  "successes": 26,
  "success_percent": 92.9,
  "average_duration": 39.8,
  "summary": "Install Firefox — 28 runs / 26 success / 92.9% / avg 39.8s"
}
```

### 10. Multi-agent computer delegation ✅

`core/computer/delegation.py` (`MultiAgentDelegator`) plans a dependency-aware
DAG of persistent agents (`researcher`, `coder`, `computer-operator`, ...).
GUI input stays serialized through a single `computer-operator`; non-GUI work
can run in parallel.

```bash
hermus computer delegate "Find how to install X then install it"
# API
POST /computer/delegate        # {"task": "..."} or a custom {"plan": {"units": [...]}}
GET  /computer/delegations     # persisted plans/results
```

`dry_run: true` validates the plan without launching agents.

### 11. Remote Android / web control ✅ (core)

`core/computer/remote.py` turns the gateway into a secure remote channel into
the local desktop:

- **`RemoteApprovalGate`** — per-action human approval. When enabled, every
  MEDIUM-or-higher action (click, type, launch, shell, …) is paused and queued
  as an approval prompt; a remote client Approves/Rejects it. Approving an
  action auto-allows the same action for a short grace window so multi-step
  tasks don't stall on every click.
- **`RemoteControlHub`** — aggregates approval prompts, the task-control
  lifecycle (pause/resume/cancel/emergency-stop) and the recent event feed for
  one remote snapshot.
- **Live screen** — `GET /computer/live-frame` streams the latest captured
  frame as JPEG.
- **Mobile page** — `GET /remote` is a mobile-first remote dashboard.

The approval gate is wired into `ComputerActionController` and is a **no-op
until enabled**, so existing autonomous behavior is unchanged. Enable it from
the gateway or CLI:

```
POST /remote/approval/enable   {"enabled": true, "required_risk": "medium"}
POST /remote/approve           {"prompt_id": "..."}
POST /remote/reject            {"prompt_id": "...", "reason": "..."}
POST /remote/control           {"action": "pause|resume|cancel|emergency-stop|release", "task_id": "..."}
GET  /remote/status            # consolidated snapshot
GET  /remote/approvals         # pending + history
```

**Security:** bind the gateway to localhost for local use; set
`HERMUS_GATEWAY_TOKEN` (see `hermus doctor`) before exposing `/remote` or
`/computer/dashboard` beyond localhost. The existing token auth
(`X-Hermus-Token` / `?token=`) protects every endpoint.

---

## Phase D — polish

### 12. Better dashboard visualization ✅

The live computer dashboard (`GET /computer/dashboard`) now includes a
**live screen pane**, a **resource monitor panel** and an **action-approval
panel** on top of the existing event feed, task controls and world state.
The mobile remote page (`GET /remote`) is purpose-built for a phone.

### 13. Performance / resource optimization ✅

`core/computer/resources.py` (`ResourceMonitor`) reports CPU, memory, threads,
disk and subsystem footprints (event bus buffer, cache) without heavy
dependencies (`psutil` used when available, POSIX fallback otherwise):

```
GET /computer/resources
```

### 14. Documentation + installation wizard ✅

New `hermus doctor` command runs a health/installation check (Python, required
and recommended packages, FFmpeg, desktop-control backends, data-dir
writability, gateway auth) and reports pass/fail per check:

```bash
hermus doctor          # human-readable report
hermus doctor --json   # machine-readable
```

### 15. Plugin / MCP ecosystem ✅

`core/plugins/` is a convention-driven plugin registry. Any module in a
`plugins/` directory (or `core/plugins/`) can declare `PLUGIN` metadata and a
`register(api)` entry point to register tools, subscribe to computer-event-bus
events and publish events — staying inside the existing event-driven
architecture. A broken plugin never takes down the gateway.

```
GET  /plugins            # discover + list plugins/tools/logs
POST /plugins/reload     # re-discover and reload all plugins
POST /plugins/invoke     # {"tool": "...", "args": {...}}
```

A sample plugin (`core/plugins/example_plugin.py`) shows the API. MCP client
support remains at `/mcp/servers` + `/mcp/connect`.

---

## What did NOT change

- The core computer loop (Record → Detect → Understand → Verify → Act) is
  untouched. Remote approval and resource monitoring are layered on top.
- The approval gate defaults to **disabled**, so Phase A/B autonomous behavior
  and all existing tests are unaffected.

## Run the tests

```bash
pip install pytest Pillow requests pydantic fastapi uvicorn httpx
python -m pytest tests/ -q
```
