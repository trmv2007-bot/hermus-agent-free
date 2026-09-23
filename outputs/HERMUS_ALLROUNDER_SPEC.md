# Hermus: persistent multi-agent assistant specification

**Status:** Proposed architecture, not implemented.  
**Review date:** 19 September 2026.  
**Scope:** The existing `hermus-agent-free` Python project, its dashboard, model/key management, orchestration, and permissioned computer/internet capabilities.  
**Evidence:** Static source inspection only. The application, browser, model providers, and test suite were not run; no real API-key values were inspected. Repository line references below refer to the reviewed checkout, not stable release permalinks.

## 1. Executive decision

Keep Python. Consolidate the existing runtime instead of writing a third agent engine or adding another orchestration framework on top of it.

Build **one assistant with a durable team behind it**:

- One user conversation and one mission entry point.
- A persistent roster of named agents, each with a role, model binding, memory scope, mailbox, and permissions.
- One scheduler that assigns work and enforces resource, budget, and safety constraints.
- One model gateway and credential registry, used by both CLI and dashboard.
- Durable collaboration through tasks, messages, artifacts, and checkpoints.
- One permission-enforcing tool gateway for internet and system actions.
- A dashboard that projects actual backend state rather than maintaining another agent system.

**The crucial distinction:** a persistent agent is not a permanently open API request. Keep its identity, context, pending work, and configuration alive in your application. Start a model request when it has work; close that request when it finishes; return the agent to idle without deleting it. Idle agents consume no model tokens merely to remain registered. Transport connection reuse is an optimization, not agent persistence.

An external model is not a resident process on your computer. A local model can optionally stay loaded, subject to RAM/VRAM limits; its residency is also separate from agent identity. If the host is off, the roster survives but cannot execute until the host resumes. Always-on operation requires an always-on host and a service supervisor.

## 2. What the current project actually contains

There are useful foundations here. The immediate problem is inconsistent ownership and incomplete integration, not a lack of feature names.

| Source evidence | Finding | Consequence / required change |
|---|---|---|
| `core/agent_manager.py:201-313`; `gateway/routes_agents.py:23-33` | Canonical named-agent registry and queue coexist with a separate API-facing `core.agents` pool. | Make both clients use the canonical registry and queue. Do not build a third implementation. |
| `core/agents/agent.py:256-291,359-396` | Task execution and collaboration handlers contain simulated sleeps and generated completion strings instead of actual model execution. | These paths cannot serve as production orchestration. Remove their execution ownership or adapt them to the canonical runtime. |
| `core/multi_key.py:29-39,74-95`; `gateway/routes_agents.py:517-615` | Canonical JSON key store supports multiple keys, while agent API endpoints operate on the separate pool. Limits also disagree: 50/provider in the canonical manager versus 10/provider in this route. | One credential service, one limit policy, one redacted view for every interface. |
| `core/agents/pool.py:95-127,135-145,158-178` | Pool identities and keys are in process memory; shutdown destroys agents; idle cleanup can destroy them when the pool exceeds its configured limit. | Persist identities independently. Evict only in-memory caches, not registered agents. |
| `core/agent_manager.py:237-275` | `start()` registers handlers; `stop()` returns a status without persisting a stopped flag; `status()` reports `alive` from manager enablement. | Add persisted desired state and independently measured runtime health. A returned label is not lifecycle enforcement. |
| `core/agent_manager.py:82-85,137-151,217-234,303-307` | Named agents have a model/session and a per-agent queue lane, but no explicit credential binding in the shown identity/constructor path. | Extend the existing job payload and identity schema. Keep the generic handler; its shared job type is not itself a concurrency bug because agent-specific lanes already exist. |
| `gateway/static/control-room.js:113-123,235-242` | A second `getJSON()` declaration replaces the envelope-aware implementation. | Remove the duplicate and test real panel error paths, not just the helper. |
| `gateway/routes_canonical.py:84-101` | `/api/v1/commands` records a requested-command event and returns IDs; this handler does not dispatch execution. | Route actionable commands to the durable runtime and distinguish accepted from completed. Do not display this acknowledgment as work performed. |
| `gateway/queue.py:868-901` | Restart recovery restores recent job summaries and marks queued/running work interrupted; this code does not reconstruct complete executable payloads. | Retain the queue facade, but complete durable task/checkpoint recovery. History recovery is not sufficient for automatic resumption. |
| `core/mission.py:1401-1413,1968-2005`; `core/agent_manager.py:357-373` | Mission loading/resumption and terminal-state guards already exist. The named-agent watchdog reports delegation to the queue; it does not itself revive workers. | Preserve existing mission recovery semantics and bridge them to complete queue/task state. Do not mistake either summary replay or a watchdog method name for full-system recovery. |
| `ARCHITECTURE.md:246-258` compared with sources above | Some consolidation claims are stronger than the actual remaining code paths. | Treat behavioral integration tests, not documentation claims, as the completion gate. |

**Reuse:** `core.runtime`, `core.mission`, `core.models.ModelGateway`, `core.multi_key`, `core.provider_resolver`, `core.agent_manager`, `gateway.queue`, `core.delegation`, `core.model_fleet`, `core.memory`, `core.events`, `core.tools.ToolGateway`, approvals, emergency stop, and existing SSE infrastructure.

**Do not infer:** static inspection does not establish live provider compatibility, actual dashboard appearance, latency, or test pass rates. This is a targeted architecture review, not a complete security audit or exhaustive repository audit.

## 3. Required behavior and definitions

| Requirement | Product contract |
|---|---|
| Register many keys | Support at least 10 separately named credentials per provider, with validation, disable/enable, rotation, usage tracking, and a single source of truth. |
| Select a model/key | Create or reuse a durable agent for that explicitly chosen configuration. A request to create another instance must not accidentally reuse the first. |
| Keep agents available | Completing or failing a task never deletes an agent. Its identity remains until explicitly archived. Pausing disables new work without deleting memory. |
| Activate another agent | Existing agents remain registered and can continue working or waiting independently. |
| Run all | Snapshot the selected roster/catalog and submit every eligible member in one operation. Execute concurrently where limits allow; show queued, excluded, and failed members separately. |
| Collaborate | Agents can propose subtasks, request help, share evidence, review outputs, and propose plan revisions. Durable scheduler decisions determine ownership and execution. |
| One assistant | A mission coordinator synthesizes one final answer and artifact set; raw agent transcripts are optional drill-down details. |
| Computer/internet control | Use capability-scoped tools for authorized tasks, with enforceable approvals and audit evidence. Broad usefulness must not mean unrestricted root/admin access. |

### Define “all” explicitly

Provide three selectors instead of one ambiguous button:

1. **All enabled agents:** use the current persistent roster.
2. **All available model deployments:** one participant per eligible endpoint/provider/model deployment, using an allowed credential.
3. **All selected credential-model bindings:** intentionally create/use separate participants for each selected key and model pair.

Example: three providers, ten keys each, and two selected models per provider yields thirty credentials, six provider/model combinations if each provider has one endpoint, and up to sixty credential-model bindings. It does **not** mean sixty distinct models. Entitlements may reduce that set. Registration does not automatically spawn the full key × model cross-product.

“Available” means enabled, compatible with this task, allowed by the mission's data policy, and accessible through an eligible credential. Temporarily rate-limited participants remain queued when within the deadline; unauthorized or disabled ones are excluded with a reason. Return the selection snapshot and counts before execution. An explicit all-model run can still exceed budgets; require confirmation of the quoted limit rather than silently choosing a subset.

## 4. Architecture and recommended stack

```text
Dashboard / CLI / other authorized inputs
                 |
          FastAPI command API
                 |
         core.runtime.execute
                 |
     MissionEngine + scheduler ----------------------+
        |                  |                        |
  Persistent agents   Task DAG + mailboxes     Durable state/events
        |                  |                        |
        +------ ModelGateway ------ credential broker ------ Provider APIs
        |
        +------ ToolGateway ------- permissions/approvals --- Tool executors
                                                             web/files/shell/desktop

Dashboard reads snapshots + replayable events from the same backend.
It does not create an independent pool, key store, or execution loop.
```

| Layer | Recommendation | Why |
|---|---|---|
| Backend | Existing Python + FastAPI + Pydantic + httpx | Already present; I/O-bound model requests work well with async concurrency. Do not change language to solve ownership problems. |
| Orchestration | Extend `MissionEngine`, canonical queue, and `AgentManager` | Preserve existing runtime boundaries and tests. Implement missing durability rather than introducing another competing engine. |
| Concurrency | `asyncio` for network I/O; supervised subprocesses for tools | Idle agents need neither a process nor a thread each. Blocking or dangerous tools must not block the API loop. |
| Operational storage | SQLite WAL on a local disk, transactional repository layer, versioned migrations | Lowest-operations single-user start. Recommend SQLAlchemy + Alembic if adopting an ORM; these would be new dependencies, not assumed installed. |
| Artifacts/memory | Files for large artifacts; metadata in DB; existing `MemoryFacade` | Keep one memory writer. Add search/indexing only when needed. Do not start with a separate vector database. |
| UI | Repair existing JS first; React + TypeScript + Vite for the redesigned dashboard | Type-checked state and reusable components suit a live orchestration UI. Build static assets and serve them from FastAPI; Node is build-time only. |
| Lowest-change UI alternative | Existing HTML/JS with modular, typed API contracts; optionally HTMX for forms | Less migration work, but streaming conversation/task views still need browser-side state. Changing UI libraries alone will not fix the runtime. |
| Events | Existing SSE, backed by durable ordered events | Sufficient for one-way progress updates; use normal HTTP for commands. Keep WebSockets only for genuine duplex needs. |
| Secrets | OS-protected secret storage; Windows Credential Manager/DPAPI-backed implementation | Store secret references in the application DB, not plaintext keys in ordinary JSON. Validate the actual secure backend; never silently use plaintext fallback. |

### Initial deployment topology

Start with **one Uvicorn worker and one runtime supervisor in that process**, one local SQLite database, and isolated tool subprocesses. An external service manager restarts the application. One OS process can host many concurrent model calls; it need not host one OS process per agent.

Do not enable multiple Uvicorn workers while authoritative registries, locks, or rate limits are still process-local. FastAPI documents that worker processes normally do not share memory [S1]. Browser disconnects do not cancel durable work.

For Windows desktop control, use an authenticated companion in the signed-in interactive user session. A background Windows service should not be assumed to control that desktop. If the companion or session is unavailable, expose that capability as unavailable; ordinary web/model tasks may continue.

Move to PostgreSQL plus a separate runtime worker service when multiple hosts, concurrent writers, or availability requirements demand it. A durable broker can be added then; Redis Pub/Sub alone is not durable work storage. Database claims and fencing remain authoritative even if a broker carries notifications.

### Framework decision

Do not add LangGraph, CrewAI, AutoGen, Celery, Redis, and a vector database together. LangGraph is an optional replacement for the *inside* of `MissionEngine` if maintaining graph/checkpoint logic becomes costly, not a second mission owner. Persistent checkpointers and long-term stores have different roles, and in-memory checkpointers do not survive restarts [S3]. Framework adoption would still require your credential broker, permissions, lifecycle, UI, and recovery tests.

## 5. Persistent agent model

### Identity is separate from execution

Persist each agent with an immutable ID, display name, role, instructions version, model deployment, credential policy, default memory scope, allowed capabilities, auto-resume setting, and desired lifecycle state. Editing its display name must not rename a session or change its ID.

Represent two separate dimensions:

- **Desired state:** `enabled`, `paused`, `archived`.
- **Observed activity:** `idle`, `queued`, `running`, `waiting_agent`, `waiting_approval`, `rate_limited`, `recovering`, `unavailable`.

Task outcomes such as `succeeded`, `failed`, and `cancelled` belong to tasks. A failed task does not mean the agent identity has failed forever. Compute an agent's visible activity from its persisted task state and verified runtime health.

Normal flow:

```text
register -> enabled / idle -> queued -> running -> enabled / idle
                                      |    |
                                      |    +-> waiting_approval -> queued
                                      +------> waiting_agent ----> queued
```

- Task completion, request closure, idle timeout, dashboard refresh, and browser closure must not delete agents.
- Idle cache eviction may unload the in-memory actor; the next message reconstructs it from storage.
- An idle agent need not ping a provider. Use runtime heartbeat/health checks and cached credential health separately.
- Pausing blocks new claims immediately. The default pause is graceful at the next safe step; cancel is a separate operation. Archiving requires settling or cancelling pending work.
- A key becoming invalid marks affected agents unavailable while preserving their history. Repairing the binding can restore readiness.
- Local-model residency and remote-provider reachability are separate badges, not synonyms for agent existence.

### Context continuity

Rebuild every model request from the pinned instruction version, agent profile, relevant conversation history, task state, approved memory, and necessary artifacts. API credentials do not carry conversational memory.

Namespace context by user/workspace, agent, conversation, and mission. Private agent scratch data and shared mission facts are separate. Summarize long histories with links back to source messages; keep summaries versioned. Do not silently share one global conversation between unrelated missions.

Default to one active reasoning turn per agent, using the existing agent-specific lane. Create additional agent instances when independent parallel conversations are needed. Agents waiting for approvals or peers must checkpoint and release scarce execution slots; no parent may occupy all worker slots while synchronously waiting for children.

## 6. Credential and model management

### Entity separation

- **Provider:** integration type and protocol adapter.
- **Endpoint/account:** actual endpoint, account/project scope, region and trust policy.
- **Credential:** secret reference, human label, masked fingerprint, enabled state, known entitlement information, cooldown and quota groups.
- **Model deployment:** model identifier at a particular endpoint, capabilities, limits, and known pricing metadata.
- **Credential entitlement:** which deployment a credential can access, verification result and freshness.
- **Agent binding:** the model deployment and credential-selection policy for a durable agent.

One credential can access multiple models; one model can be accessed through multiple credentials; many agents can share either. Endpoint-qualified model IDs avoid collisions between provider aliases.

### Binding policies

| Policy | Behavior |
|---|---|
| `pinned` | Use exactly the selected credential and deployment. If unavailable, wait or report it. Default when the user explicitly chooses a particular key. |
| `preferred` | Prefer that credential, then use explicitly allowed alternatives for the same deployment/account policy. |
| `pooled` | Select an eligible credential by remaining capacity, fairness, health, and cost constraints. |

Do not silently switch a pinned model to another model. Cross-model/provider fallback requires an allowed fallback policy, compatible capabilities, permitted data residency, and a visible event. The durable agent ID can remain the same, but each turn must record the actual deployment and credential ID used.

### Registry operations

Provide add, list-redacted, validate, refresh entitlements, disable, rotate, and revoke. Key import does not itself authorize test calls with user content. Prefer a non-generating validation endpoint where supported; label billable validation explicitly. Credential validation is scoped: access to one model does not prove access to every model.

Store full keys only in protected secret storage. Neither model prompts nor workers executing arbitrary code should receive the full key inventory. The broker resolves one approved credential just in time. Use per-request auth or credential-scoped clients; never mutate a shared client's default authorization header during concurrent calls.

Mask secrets in events, traces, exception bodies, screenshots, configuration exports, and dashboard HTML. Apply a credential fingerprint for duplicate detection without displaying the secret. Editing or deleting an application credential reference does not automatically revoke the provider-side credential; show the distinction.

### Rate limits, accounting, and retries

Maintain admission limits at global, mission, agent, provider, account/project, model-family quota group, and credential scopes as applicable. Ten keys from one account are not necessarily ten independent quota allocations: OpenAI, for example, documents organization/project limits and shared model-family limits [S2]. Keys are for authorized capacity management, not bypassing limits.

Before a call, atomically reserve estimated token/cost capacity, including an output cap. Reconcile with provider usage afterward. Persist reservation and cooldown state sufficiently to prevent a restart from bursting through a quota. Use configured price metadata with timestamps; unknown pricing or usage is marked unknown, never silently recorded as zero.

- Temporary `429`: honor the applicable retry/reset hint, defer with jitter, and release the worker slot.
- Authentication failure: disable/quarantine the credential pending repair; do not retry indefinitely.
- Access failure: distinguish model entitlement from invalid credentials.
- Billing/quota exhaustion: show blocked status; waiting a few seconds is not a fix.
- Provider overload/network failure: bounded retries, then an authorized fallback or explicit failure.
- Partial streams: preserve the attempt and visible partial output. Do not blindly replay a tool-bearing turn.

Give retry ownership to one layer; disable or tightly cap overlapping SDK/gateway retries. Track every attempt, including failed attempts, and explain that provider billing may not match a local estimate until reconciled.

## 7. Durable storage and recovery contract

Use transactional current-state tables plus an ordered event/outbox log; do not require a full event-sourcing framework.

| Entity | Minimum information |
|---|---|
| `agents` | Identity, role, deployment/binding references, desired state, instruction version, permissions, context namespace |
| `credentials` / `deployments` / `entitlements` | Secret references and metadata, endpoint-qualified model IDs, verified access, quota-group links |
| `missions` / `participants` | User goal, status, plan version, roster snapshot, coordinator, budgets, deadline, final artifact references |
| `tasks` / `dependencies` | Complete executable input, owner, state, prerequisites, output schema, verification criteria, attempts, next eligible time |
| `leases` | Task/turn, worker ID, expiry, fencing token, heartbeat |
| `messages` / `deliveries` | Sender, intended recipients, mission/task correlation, typed payload, dedupe key, delivery state, response linkage |
| `checkpoints` | Agent/conversation/task state, schema version, instruction version, last committed step |
| `events` / `outbox` | Monotonic sequence, event ID/type/version, entity IDs, redacted payload, delivery marker |
| `tool_invocations` / `approvals` | Exact normalized action, approval scope, idempotency key, policy version, attempt, outcome/evidence |
| `usage_reservations` / `usage` | Quota-group and credential references, reserved/actual usage, estimate versus confirmed classification |
| `artifacts` | Workspace-relative path, owner, content hash, type, provenance, revision and access scope |

Continue to access long-term memory through `MemoryFacade`; it may retain its existing backend. Do not introduce a second writable memory truth just to put every table into one file.

### Atomic boundaries

Store the complete resumable checkpoint payload in the operational database. Commit it together with task/state changes and corresponding events/outbox entries in one transaction. The checkpoint includes the exact bounded conversation/task context or immutable versions needed to replay a turn; long-term recall remains owned by `MemoryFacade`. New long-term memories are written through that facade by idempotent outbox consumers and reconciled after a crash, not assumed to be atomically committed in a different backend. No resume step may depend on an uncommitted memory write.

Publish events after commit. A notification or SSE event must not be the only copy of a task. For large artifacts, write a temporary file, finalize its hash/path, then commit its metadata; verify referenced artifacts during recovery and handle orphaned temporary artifacts without fabricating success.

Claim work atomically with versioned leases. Stale workers must not commit results after a lease has been reassigned. Tool authorization checks the current lease and cancellation state immediately before acting; fencing cannot undo an external action already in progress.

Use at-least-once delivery with deduplication. Allocate a durable `effect_id` when admitting each normalized tool action; retries of that logical action retain the same ID. Namespace deduplication by workspace, mission, and effect ID, and validate the stored argument hash. A changed action or intentional repeat gets a new effect ID and, where required, a new approval. All entry points use this same invocation ledger. Do not derive the dedupe key from a retry attempt number or rely on matching free-form tool text.

Do not promise exactly-once effects across arbitrary external services.

### Recovery sequence

1. Acquire the single-supervisor lock, migrate schema once, and load desired-state agents.
2. Mark incomplete attempts recovering; preserve already completed steps and cancelled missions.
3. Reconcile expired leases and unfinished tool invocations.
4. Requeue safe retryable steps from complete payloads/checkpoints; never recover only the latest fifty display summaries.
5. For an ambiguous external effect, verify its receipt/state or pause for review instead of repeating it automatically.
6. Resume mailboxes, quota cooldowns and enabled work; emit recovery events.
7. Reconstruct the dashboard from a consistent snapshot and subsequent events.

Persist an invocation intent before an external action, then its receipt/result afterward. Use provider idempotency support where available. If a crash occurs after sending an email but before recording the receipt, do not claim that replay can guarantee no duplicate; reconciliation or user review is required.

Use local-disk SQLite, short transactions, bounded write contention, integrity checks, and consistent backups including artifacts and a separate secret-recovery strategy. Restoring secrets may require reauthentication. Define configurable retention for events, messages, and artifacts; agent identities are not expired by an idle timer.

## 8. Self-orchestration and communication

### Use a coordinated team, not unrestricted agent chatter

Each mission has one logical coordinator whose role can be reassigned under a lease. The coordinator is a role, not a fixed provider. Other agents can propose work, ask each other questions, and challenge results. The scheduler validates those proposals and commits assignments. Models do not directly overwrite task ownership or grant permissions.

Mission flow:

1. Interpret the goal, success criteria, available capabilities, data boundaries, budget, and deadline.
2. For simple requests, use one suitable agent. Do not activate a fleet unnecessarily.
3. For complex requests, propose a validated task DAG with explicit dependencies and verifiable outputs.
4. Assign tasks by capabilities, availability, context access, cost, and role; snapshot participant bindings.
5. Execute independent nodes concurrently. Share evidence and artifacts by reference.
6. Review outputs against the requested criteria using tests, citations, file checks, or a separate reviewer.
7. Replan only failed or blocked parts within bounded retries and budget.
8. Synthesize one final response with outputs, evidence, and unresolved limitations; return available agents to idle.

“Use all models” should offer **collaborative** mode (different subtasks) and **compare answers** mode (same prompt, different answers). Majority voting can compare opinions; it is not proof of correctness, especially when several keys reach the same underlying model.

### Message contract

Use typed envelopes validated against schemas:

```json
{
  "schema_version": 1,
  "message_id": "msg-example-001",
  "mission_id": "mission-example-001",
  "task_id": "task-example-002",
  "sender_agent_id": "agent-research",
  "recipient_agent_ids": ["agent-builder"],
  "type": "artifact_available",
  "payload": {
    "artifact_id": "artifact-example-001",
    "summary": "Source evidence for the assigned task"
  },
  "reply_to": null,
  "dedupe_key": "task-example-002:artifact-example-001"
}
```

Suggested types: `task_proposal`, `assignment`, `help_request`, `artifact_available`, `review_request`, `review_result`, `blocked`, `plan_change`, and `cancel`. Authenticate the sender from runtime context, not from a model-supplied sender field.

Durably record a message and its recipient deliveries before acknowledging it. Order delivery per recipient/conversation where required; do not assume one total order for all agent activity. Deduplicate replay, track failures, and move undeliverable messages to a reviewable dead-letter state. Delivery means “recorded for processing,” not “reasoned about successfully.”

Only relevant peers receive messages. A shared mission board holds accepted facts, decisions, artifacts and provenance. Large documents are references, not copied into every prompt. Suggestions become accepted facts only after validation. Do not expose hidden reasoning; show concise decisions, evidence, and actions.

### Bounded autonomy

Suggested initial defaults, configurable and to be validated under load:

- Eight concurrent model calls globally, initially two per provider/account group and one per credential, subject to real quotas.
- Automatic team selection up to eight participants; explicit all-model runs may exceed roster size limits but still obey active-call limits.
- Twenty reasoning steps per task, two retry attempts after the original attempt, and three review/replan rounds per mission.
- Fifteen-minute default mission deadline unless explicitly extended; a configured hard mission spend cap and output-token cap.
- No idle polling with an LLM, no unlimited self-spawning, no unbounded peer broadcasts, and no unsolicited endless debates.

A supervisor detects stalled tasks, cyclic task dependencies, unresolved requests, repeated equivalent plans, and exhausted budgets. Every waiting state needs a wake-up condition and deadline. Waiting for a peer must not monopolize a worker or deadlock on that peer's agent lane.

### Concurrent write control

Parallel reasoning is allowed; conflicting actions are serialized. Use exclusive resources for a desktop session, browser profile, file, database migration, or deployment target. For coding, use per-task branches/worktrees or isolated workspaces and let one integrator apply reviewed changes. Two agents must not type into the same desktop or overwrite the same file simultaneously.

## 9. Internet and system control

Treat the desired power as **broad authorized capability with explicit boundaries**, not a way to remove safeguards.

| Capability | Execution design |
|---|---|
| Public information | Search/fetch/extract through the existing web gateway; preserve source URLs, timestamps and provenance. |
| Browser work | Separate browser contexts, domain constraints, explicit credential/session selection, downloads quarantined until inspected. |
| Project files | Workspace-scoped tools, canonical-path checks, symlink/junction escape checks, diffs and snapshots before changes. |
| Shell/code | Structured commands or reviewed scripts in constrained subprocesses with time, output, network and resource limits. |
| Desktop | Interactive-session companion with consent and an exclusive session lock; prefer semantic controls over blind coordinate clicks. |
| Third-party services | Narrow authenticated adapters with per-operation capabilities; do not treat access to one account as access to every account. |

All routes, agents, schedulers and integration adapters must enter through `ToolGateway`; no direct tool execution bypass. Keep policy deterministic and separate from model judgment.

### Permission levels

- **Automatic within scope:** permitted public reads, project inspection, analysis, and preauthorized reversible operations.
- **Approval required:** modifying personal files, installing software, sending messages, posting content, making purchases, deleting data, changing security settings, and accessing sensitive destinations.
- **Denied:** actions outside authorized scope, credential theft, unauthorized access, destructive policy bypass, and disabling audit/security controls.

Approvals bind the normalized action and arguments, target, mission, capability scope, expiry, maximum uses, and current policy version. Any materially changed action needs reapproval. Agents cannot approve their own requests or expand their own permissions. A granted read does not imply permission to transmit the data to another provider.

Validate destination addresses, redirects and DNS resolution for web requests; block unexpected loopback/private-network/cloud-metadata access unless a specific local-service capability permits it. Recheck network targets rather than trusting the first URL. Treat web pages, downloaded documents, and tool outputs as untrusted data, not instructions that can override the task or reveal secrets.

Use OS-level isolation for untrusted code; a subprocess, restricted prompt, or Python virtual environment is not by itself a security sandbox. An OS vault protects secrets at rest but does not necessarily isolate them from malicious code running as the same user. Before enabling arbitrary generated code, run executors under an isolated identity/container with no host-profile or secret-store access, and keep credential resolution in a broker protected by a separate OS security boundary. A separate process under the same unrestricted user is insufficient. Fail closed if that isolation is unavailable; do not advertise the development configuration as hardened.

Give the desktop companion only narrowly scoped execution capabilities, never the provider-key inventory. Desktop automation is inherently privileged within its signed-in session and may see sensitive applications; a capability token cannot eliminate that exposure. Keep secret-management applications out of its allowed targets and require explicit user interaction for sensitive credential screens. Do not expose host API keys to general shell workers. Require explicit data-sharing policy before forwarding sensitive artifacts between providers.

The emergency stop must prevent new tool admissions immediately, cancel queued work, request cooperative cancellation, and terminate supported tool processes where safe. It cannot undo completed external actions or guarantee cancellation of already accepted provider requests. Report those limitations and preserve audit records.

## 10. Dashboard product specification

### Main layout

1. **Assistant:** one conversation, mission goal, consolidated answer, artifacts, and a visible team selector.
2. **Agents:** persistent cards/list with model, key alias, desired state, real activity, current task, queued work, last result, usage, and pause/resume controls.
3. **Missions:** task DAG/list, assignments, dependencies, evidence, reviewer findings, blocked reasons, cancel/retry controls, and final status.
4. **Models and keys:** provider/account groups, redacted credentials, entitlement checks, limits, cooldowns, usage, actual versus estimated cost, and binding policies.
5. **Approvals and activity:** approval queue, scoped action preview, event timeline, filters, and emergency stop.

The primary workspace should be conversation + current mission + compact agent roster. Put subsystem diagnostics in an advanced section rather than making the user navigate a wall of unrelated panels.

### Truthful state

Distinguish:

- Agent registered from agent currently executing.
- Agent idle from provider unreachable.
- Runtime available from browser stream connected.
- Task queued from provider request in progress.
- Command accepted from task completed.
- Measured usage from estimated usage.
- Capability implemented from capability verified on this machine.

An idle agent card must stay visible after its response, browser refresh, and runtime restart. The UI must not manufacture running status to satisfy the “live agent” concept.

### Snapshot + replay

Fetch a consistent authorized snapshot with a last-event sequence. Subscribe to events after that sequence. Include event ID, sequence, type/version, relevant entity IDs and redacted payload. Deduplicate replay, detect gaps, and refresh the snapshot when a requested cursor has expired. Support reconnection/backoff and heartbeat-based stale-state detection.

Use one API client and one error-envelope renderer. Show request IDs and retry buttons only when retry is valid. Provide loading, empty, blocked, unavailable, reconnecting, and permission-denied states. Do not mark a mutation complete optimistically before backend acknowledgment.

Protect gateway endpoints, event streams and WebSockets with authentication and object-level access checks. For a same-origin browser UI, prefer secure HttpOnly session cookies plus CSRF protection; use scoped bearer tokens for CLI/service clients. Avoid permanent gateway secrets in URLs. Limit CORS and validate WebSocket origins where applicable. Bind local development to loopback.

## 11. Proposed API contract

These are **target interfaces**, not a claim that the current endpoints already implement them. Version incompatible changes explicitly or retain temporary compatibility adapters.

| Operation | Target interface |
|---|---|
| Add/list credentials | `POST /api/v1/credentials`, `GET /api/v1/credentials` |
| Validate/disable/rotate credential | `POST /api/v1/credentials/{id}/validate`, `PATCH /api/v1/credentials/{id}`, `POST /api/v1/credentials/{id}/rotate` |
| List entitled deployments | `GET /api/v1/models` with endpoint/capability/health filters |
| Create/list agent | `POST /api/v1/agents`, `GET /api/v1/agents` |
| Activate or explicitly clone binding | `POST /api/v1/agents/activate` with a stable roster-slot/idempotency key and explicit reuse policy |
| Pause/resume/archive | `PATCH /api/v1/agents/{id}`; persisted desired state |
| Direct agent task | `POST /api/v1/agents/{id}/tasks` |
| Create team mission | `POST /api/v1/missions` with goal, selection mode, constraints, budget and deadline |
| Preflight all selection | `POST /api/v1/missions/preview` returns eligible/queued/excluded participants and estimates |
| Cancel/retry mission | `POST /api/v1/missions/{id}/cancel` or `/retry`, subject to policy |
| Mission details | `GET /api/v1/missions/{id}` and `/tasks` |
| Approve/deny | `POST /api/v1/approvals/{id}/decision`, user-authorized only |
| Snapshot/events | `GET /api/v1/state`, `GET /api/v1/events?after={sequence}` |

Long-running commands return `202 Accepted` **after durable admission**, with mission/task IDs and an event cursor. Reusing an idempotency key with the same payload returns the same admission; conflicting payloads return a conflict. Queue unavailable means an explicit unavailable response, not silent inline execution with a different response shape.

Use existing canonical error-envelope conventions, correlation IDs, and schema validation. All client interfaces must operate on the same records. A process-local CLI singleton must not enqueue into its own unused queue: CLI commands go through the running runtime API, or enter an explicitly exclusive standalone runtime mode.

## 12. Migration plan mapped to the repository

### Phase 0 — Establish evidence and stop misleading behavior

- Capture a fresh test baseline before any rewrite; label provider/device/browser coverage separately.
- Fix the duplicated `getJSON` and add a panel-level regression test.
- Label simulated agent paths unavailable for real execution until redirected.
- Make command acknowledgments visibly distinct from completed work.
- Add architecture gates forbidding routes from using the legacy pool/key store.

**Gate:** no UI action reports completed work without an actual runtime result.

### Phase 1 — Unify agents, keys, and runtime entry

- Extend `core.agent_manager` with durable desired state, stable IDs, deployment and credential policies.
- Rewire `gateway/routes_agents.py` and CLI model/agent commands to the canonical services.
- Reuse generic job handlers and per-agent lanes; pass explicit agent/binding IDs instead of registering one handler implementation per named agent.
- Ensure the CLI reaches the running runtime rather than an unrelated in-process queue.
- Import existing persistent identities and canonical keys through an explicit migration. Reconcile key duplicates by protected fingerprints and user-visible aliases.
- The pool's runtime-only keys/agents require a one-time authenticated export/import while the old process is alive; if already lost, report that rather than inventing recovery.

**Gate:** adding a key or agent through either interface is visible through the other, and disabled agents cannot claim new tasks.

### Phase 2 — Complete durability and lifecycle

- Introduce schema migrations and the repository layer beneath canonical facades.
- Persist complete task payloads, mailboxes, checkpoint references, invocation intents, usage reservations, leases and events.
- Preserve `gateway.queue` as the execution facade while replacing summary-only recovery with full resumable storage.
- Add startup reconciliation, graceful drain, and restart-safe pause/cancel behavior.

**Gate:** an enabled roster survives a crash, interrupted safe work resumes once logically, and ambiguous side effects pause instead of duplicating.

### Phase 3 — Implement genuine team collaboration

- Extend `core.mission` with validated DAG plans, participant snapshots, bounded delegation and coordinator leases.
- Reuse `core.delegation` and `core.model_fleet` as workers/strategies, not competing lifecycle owners.
- Add durable message delivery, review/evidence requirements, resource locks, and all-model selection modes.

**Gate:** a real multi-agent workflow exchanges artifacts, survives one worker failure, and produces one verified final result within configured limits.

### Phase 4 — Redesign the control room

- Publish typed API/event contracts first.
- Implement the assistant/agents/missions/keys/approvals layout against real snapshots and replay.
- Keep `/control` as one production surface; replace the old shell incrementally rather than deploying parallel dashboards permanently.
- Remove compatibility execution paths only after parity and migration gates pass.

**Gate:** UI reflects the same persisted state as CLI/API through refreshes, disconnects, failures and restarts.

### Phase 5 — Harden controls and expand deployment only when needed

- Verify every tool path crosses permissions, approvals, emergency stop and audit.
- Test desktop companion availability and resource locking on the actual target OS.
- Measure throughput before adopting PostgreSQL/separate workers/brokers.

**Gate:** policy bypass attempts fail closed, ordinary authorized tasks still work, and reported capabilities match actual device/provider tests.

At each storage cutover, back up state and verify counts/checksums before switching ownership. Prefer a paused, transactional import plus read-only legacy compatibility over permanent dual writes. Keep a rollback plan; no application code or existing user data was changed as part of this specification.

## 13. Acceptance tests

These tests define completion; they were **not executed** for this review.

| ID | Scenario | Required result |
|---|---|---|
| A01 | Register ten keys each for three providers | Thirty distinct redacted credentials persist; CLI and dashboard agree. |
| A02 | Run model A through a pinned key, then run B | Both agents remain visible; A is idle after completion; each turn records its actual binding. |
| A03 | Leave 100 agents idle with a fake provider adapter | Zero model calls/tokens caused solely by idleness; identities do not expire. |
| A04 | Restart the runtime and refresh the browser | Stable agent IDs, desired states, messages and context reload; paused/archived agents do not auto-run. |
| A05 | Run all six eligible deployments in the example | Six participant records, bounded overlapping call intervals where quotas permit, individual outputs and one summary. |
| A06 | Select sixty credential-model bindings | All eligible bindings are admitted or explicitly excluded/blocked; active versus queued counts remain truthful. |
| A07 | Exhaust a shared account quota using one key | Other keys in that quota group do not bypass its cooldown; unrelated permitted groups may continue. |
| A08 | Disable a pinned key mid-mission | Agent identity remains; no silent model/key change; visible block or explicitly authorized fallback. |
| A09 | Deliver a help message twice and reconnect stream | Recipient processes the logical message once; duplicate events do not duplicate UI state or tool actions. |
| A10 | Parent waits for child with all execution slots busy | Parent releases its slot; child can progress; no permanent wait cycle. |
| A11 | Crash during a provider call | Persisted attempt/context survives; safe retry policy applies; uncertain usage remains labeled. |
| A12 | Crash after a non-idempotent external action | Reconcile receipt or request review; no blind replay and no false exactly-once claim. |
| A13 | Stale worker returns after lease reassignment | Its fenced state commit is rejected; current owner remains authoritative. |
| A14 | Two agents edit one file or use one desktop | Resource policy serializes writes/control or creates isolated workspaces; no silent overwrite. |
| A15 | Approval expires or action arguments change | Execution is rejected until a matching valid approval exists. |
| A16 | Web content instructs secret disclosure or private-network access | Data is treated as untrusted; tool/data policy blocks unauthorized disclosure/access. |
| A17 | Emergency stop during a team mission | New effects stop; queued work cancels; unavoidable in-flight effects are reported honestly. |
| A18 | Queue unavailable or provider fails | No simulated success or silent inline downgrade; recoverable failures include useful error details. |
| A19 | Event cursor expires or stream drops | Client reconnects/replays or resnapshots without ghost agents, duplicate actions, or lost final state. |
| A20 | Inspect logs, exports, browser state and tool environments | No raw provider key inventory; only permitted, redacted credential metadata. |
| A21 | Agent claims success but artifact/test is absent | Mission cannot pass its completion criteria; reviewer requests repair or reports partial failure. |
| A22 | Start a second local runtime owner | Startup refuses or safely joins an explicit shared-worker topology; no independent duplicate scheduler. |

Run deterministic fake-provider contract tests and crash/recovery tests first. Then conduct explicitly authorized, budget-capped live-provider smoke tests, actual browser tests, and actual desktop tests. Report mock versus live evidence separately. Do not reuse historical pass counts from documentation as a current validation result.

## 14. Success criteria and operating signals

The release is successful when the user can register a team, run it, leave it idle, return later, and continue without recreating agents or losing context—and can see why any task is waiting or failing.

Measure persisted-versus-visible roster agreement, task admission latency, queue wait, time to first output, attempts per task, token/spend reconciliation, recovery time, duplicate-effect prevention, approval outcomes, mailbox backlog, stream lag, and verified task completion. Useful work completed is the objective, not the number of agents marked “alive.”

**First implementation slice:** one real model call through the canonical gateway, one durable idle agent afterward, a second independent agent, one shared key-management view, and one dashboard refresh/restart test. Prove that end to end before building elaborate autonomous debates.

## 15. Source notes and remaining decisions

### External documentation consulted

- **[S1] FastAPI deployment concepts:** https://fastapi.tiangolo.com/deployment/concepts/ — process memory isolation, startup, external restarts, and one-time preparation steps.
- **[S2] OpenAI rate limits:** https://platform.openai.com/docs/guides/rate-limits — organization/project scopes, shared model-family quotas, retry guidance, and rate-limit headers. Other providers require their own adapters and validated quota behavior.
- **[S3] LangGraph persistence:** https://docs.langchain.com/oss/python/langgraph/persistence — checkpointers versus stores, thread scope, and why in-memory checkpointing is not restart persistence. The durable-execution documentation URL resolved to this persistence page during review.

Sources were consulted on the review date. Stack choices and proposed defaults are engineering recommendations, not guarantees supplied by these vendors.

### Decisions to confirm before implementation

1. Which providers, endpoint URLs, and permitted account/project scopes will be supported first? Do not send secrets in a design document.
2. Should direct key selection remain strictly pinned, as recommended, or allow an explicit fallback list?
3. What spend caps, retention periods, and data-sharing rules should apply?
4. Is the first deployment only this Windows machine, or an always-on remote runtime with a local desktop companion?
5. Is the larger dashboard rebuild worth adding a frontend build step, or should the first release retain the current HTML/JS shell?

These decisions do not block the architecture above. Recommended defaults are local single-user deployment, strict pinning for explicit key choices, bounded automatic teams, explicit approval for sensitive effects, and consolidation before frontend replacement.
