# HERMUS capability build map

This roadmap is the implementation order for evolving HERMUS toward a persistent,
agentic assistant. Each stop has a dependency boundary; later stops should not
bypass earlier safety/runtime layers.

| Stop | Capability | Depends on | Status |
|---|---|---|---|
| 1 | Canonical execution runtime | MissionEngine | Existing |
| 2 | Executive Brain | Runtime | Built |
| 3 | Executive/runtime handoff | Executive + Runtime | Built |
| 4 | World Model + perception | Executive | Built / expanding |
| 5 | Unified executive lifecycle loop | 2–4 | Built / expanding |
| 6 | Persistent memory integration | World Model | Next |
| 7 | Specialist-agent delegation | Mission DAG | Next |
| 8 | Observation adapters | World Model | Next |
| 9 | Browser/computer control | Observation + safety | Planned |
| 10 | Voice and interruption | Runtime + channels | Planned |
| 11 | Live Control Room | Runtime events + World Model | Planned |
| 12 | Skill/lesson learning | Memory + verification | Planned |
| 13 | End-to-end hardening | All previous stops | Planned |

## Stop 5: executive lifecycle

The current implementation composes three existing boundaries:

```text
perceive
  -> ExecutiveBrain
  -> bounded handoff
  -> core.runtime.execute
  -> MissionEngine
  -> runtime events
  -> WorldModel
  -> executive reconciliation
```

`core/executive_loop.py` is the coordinator. It does not execute tools and does
not replace MissionEngine. This is intentional: the executive layer can plan
and remember, while the canonical runtime remains responsible for execution,
approvals, sandboxing, verification, cancellation, and repair.

## Completion rule

A stop is not considered complete because a module exists. It is complete when:

1. the capability has a stable interface;
2. at least one real runtime path uses it;
3. failures are observable and recoverable where appropriate;
4. tests cover persistence and safety boundaries; and
5. the Control Room/CLI can explain its state without inventing facts.

## Next implementation order

1. Connect the World Model to mission lifecycle events.
2. Add persistent episodic/semantic/procedural memory adapters.
3. Make agent delegation consume explicit capability contracts.
4. Add observation providers for filesystem, browser and service state.
5. Add computer-control actions only behind existing approval/sandbox gates.
6. Add voice as another interface to the same runtime rather than a separate brain.
7. Expose executive/world state through the existing gateway and Control Room.
8. Add learning from verified outcomes and failed/repair trajectories.
9. Run end-to-end tests across every user-facing entry point.
