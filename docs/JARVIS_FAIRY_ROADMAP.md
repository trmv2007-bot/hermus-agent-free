# ⚡ HERMUS — Master Capability Build Map

This roadmap is the implementation order for evolving HERMUS from a reactive
agent into a persistent, proactive personal AI. Later phases build on earlier
execution, verification, memory, observability and safety boundaries; they do
not replace them.

| Phase | Capability | Depends on | Status |
|---|---|---|---|
| 1 | Verified computer-control execution loop | Computer + safety | ✅ Complete |
| 2 | Voice presence + executive integration | Runtime + voice | ✅ Complete |
| 3 | Control Room / system observability | Runtime events + state | ✅ Complete |
| 4 | Learning, memory and reusable skills | Memory + verification | ✅ Complete |
| 5 | End-to-end autonomy facade + safety hardening | Executive + runtime | ✅ Complete |
| 6 | Runtime reliability + observability | Autonomy facade + EventBus | ✅ Complete |
| 7 | Proactive event-driven automation | EventBus + JobQueue | ✅ Complete |
| 8 | Scheduling + time awareness | Automation + JobQueue | ✅ Complete |
| 9 | Persistent personal context | MemoryFacade + session history | ✅ Complete |
| 10 | World awareness / live World Model | Observation adapters + state | ✅ Complete |
| 11 | Advanced long-horizon planning | World Model + missions | ✅ Complete |
| 12 | Specialist agent ecosystem | Delegation + capability contracts | ✅ Complete |
| 13 | Multimodal intelligence | Vision + documents + browser | ✅ Complete |
| 14 | Natural conversation + interruption | Voice + runtime steering | ✅ Complete |
| 15 | Personal Operating System layer | World Model + agents + integrations | ✅ Complete |
| 16 | Self-improving agent | Verified outcomes + skills | ✅ Complete |
| 17 | Distributed HERMUS | Shared identity + memory + device workers | ✅ Complete |
| 18 | Reliability & recovery | Queue + distributed control plane + state | ✅ Complete |

## Architectural invariants

HERMUS keeps these rules across every phase:

1. Mission Runtime remains the canonical execution lifecycle.
2. Tool execution goes through the canonical ToolGateway.
3. Model selection/completion goes through ModelGateway.
4. Memory writes go through MemoryFacade.
5. EventBus is the canonical event authority.
6. Proactive and scheduled work enters the same queue/runtime path as normal work.
7. Verification is required before claiming successful completion.
8. Approval, red-line, sandbox and emergency-stop controls remain authoritative.
9. Self-improvement cannot silently weaken protected safety controls.
10. New subsystems should add capability, not create competing execution engines.

## Phase 8 — Scheduling & Time Awareness

Completed. HERMUS gained durable, timezone-aware one-shot and recurring
schedules, natural-language schedule parsing, restart restoration, enable /
disable lifecycle, run limits, optional quiet-hour deferral, next/last-run
tracking, and canonical JobQueue submission.

See docs/PHASE_8_SCHEDULING.md.

## Phase 9 — Persistent Personal Context

Completed. HERMUS gained a structured context layer for explicit preferences,
goals, project records and current focus. Each turn can combine that durable
profile with relevant typed memories and recent session history.

The context layer is built on MemoryFacade rather than creating a competing
memory writer, and explicit personal facts are captured only from recognizable
user statements.

See docs/PHASE_9_PERSONAL_CONTEXT.md.

## Phase 10 — World Awareness / Live World Model

Completed. HERMUS now reconciles enabled observation connectors plus local Git, runtime process, and existing-browser state into the canonical WorldModel, with freshness, provenance, confidence, change detection and gateway visibility.

See docs/PHASE_10_WORLD_AWARENESS.md.

## Phase 11 — Advanced Long-Horizon Planning

Completed. HERMUS now generates dependency-aware, checkpointed long-horizon plans with explicit success criteria and structured replanning triggers. MissionEngine remains the canonical executor.

See docs/PHASE_11_LONG_HORIZON_PLANNING.md.

## Phase 12 — Specialist Agent Ecosystem

Completed. HERMUS now exposes explicit specialist capability contracts covering capabilities, inputs, outputs, permissions, resource limits and verification requirements. Delegation plans carry those contracts into their DAG nodes.

See docs/PHASE_12_SPECIALIST_ECOSYSTEM.md.

## Phase 13 — Multimodal Intelligence

Completed. HERMUS now unifies image analysis, document extraction and browser visual state into structured multimodal evidence with provenance, confidence and WorldModel visibility.

See docs/PHASE_13_MULTIMODAL_INTELLIGENCE.md.

## Phase 14 — Natural Conversation + Interruption

Completed. HERMUS now has bounded conversation sessions, explicit session/run
correlation, mid-run steering and interruption through RunBus, background
notifications, WebSocket steering controls, and voice output interruption
generation state. Existing Mission Runtime, verification and safety controls
remain authoritative.

See docs/PHASE_14_NATURAL_CONVERSATION.md.
## Completion rule

A phase is complete only when:

- it has a stable interface;
- at least one real runtime path uses it;
- failures are observable and recoverable where appropriate;
- persistence/safety boundaries have regression coverage; and
- the Control Room / gateway can report its state from real backend evidence.


## Phase 15 — Personal Operating System

Completed. HERMUS now provides a durable Personal OS control plane for tasks,
priorities, areas, project context and personal briefings, while aggregating
schedules, proactive automations and World Model state. Task execution routes
through the canonical JobQueue/runtime path.

See docs/PHASE_15_PERSONAL_OS.md.


## Phase 16 — Self-Improving Agent

Completed. Reflection now feeds a governed improvement controller that creates
auditable proposals, evaluates them with the deterministic EvolutionPolicy,
and separates allowed development work from review-required or denied changes.
Existing SkillForge verification/repeatability, lessons and quarantine remain
part of the learning loop.

See docs/PHASE_16_SELF_IMPROVING_AGENT.md.


## Phase 17 — Distributed HERMUS

Completed. HERMUS now has a durable distributed coordination foundation with
explicit node registration, capability routing, heartbeat/stale detection,
assignments, emergency-stop-aware dispatch, and gateway visibility.

See docs/PHASE_17_DISTRIBUTED_HERMUS.md.


## Phase 18 — Reliability & Recovery

Completed. HERMUS now has a reliability control plane with bounded retry policy,
circuit breakers, durable idempotency receipts, crash/resume checkpoints,
incident tracking, integrity-checked state snapshots, resource/degraded-mode
signals, and distributed lease/fencing support. Recovery remains subordinate to
approval, red-line, sandbox, verification and emergency-stop controls.

See docs/PHASE_18_RELIABILITY_RECOVERY.md.
