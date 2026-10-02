# HERMUS Executive Brain

The Executive Brain is the control-plane foundation for HERMUS's Jarvis/FAIRY-style behavior.

## Responsibility boundary

The Executive Brain owns:

- persistent executive goals
- goal priority
- recent observations/events
- bounded goal decomposition
- success criteria
- hand-off metadata for the mission runtime

It does **not** own:

- shell execution
- browser/computer control
- model tool calls
- permissions or approvals
- sandboxing
- verification itself

Those remain behind the existing runtime and safety boundaries.

## Execution flow

```text
User / Channel / Scheduler
          |
          v
   Executive Brain
          |
   perceive + prioritize
          |
       plan_goal
          |
       handoff()
          |
          v
   Mission Runtime
          |
   MissionEngine
          |
 plan -> DAG -> execute -> observe -> verify -> repair
          |
          v
   Executive observations
```

## Storage

By default the brain uses:

`~/.hermus/executive.sqlite3`

Tests and deployments can override this with `HERMUS_EXECUTIVE_DB`.

## Design rule

The Executive Brain must remain a **planner/control plane**, not a second autonomy engine. All real-world actions continue through the existing Mission Runtime so red-line policy, approvals, sandboxing, evidence gates, and verification remain authoritative.

## Next integration stages

1. Wire runtime entry points to record executive observations.
2. Use executive priorities to select active missions.
3. Feed bounded executive plans into MissionEngine requirements/subgoals.
4. Add world-state adapters for filesystem, browser, computer, services and channels.
5. Add model-assisted planning behind the same `ExecutivePlan` contract.
6. Add Control Room views for goals, world state, plans and live delegation.
