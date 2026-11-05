# Executive Agent Delegation

HERMUS now has a bounded delegation planner in `core.agent_delegation`.

The planner selects specialist roles from mission intent and builds an `AgentDAG` for the existing MissionEngine to execute. It does not call providers, create agents, or execute tools itself.

## Boundary

```text
Executive Brain
      |
      v
AgentDelegator
      |
      v
bounded AgentDAG
      |
      v
MissionEngine / AgentPool
      |
      v
execution + verification
```

This keeps planning and execution separate and preserves the existing safety/runtime authority.

The default specialist vocabulary includes research, architecture, coding, review, security audit, integration, and verification. The planner is deterministic and bounded so a model cannot silently create an unbounded agent swarm.
