# Phase 15 — Personal Operating System

Status: **Complete**

HERMUS now has a Personal Operating System control plane that unifies personal tasks with persistent context, goals, projects, schedules, proactive automations and the live World Model.

## Capabilities
- Durable personal task records with status, priority, area, project, due date and notes.
- Task completion/update/delete lifecycle.
- Priority-aware task listing.
- Personal OS snapshot combining current focus, goals, projects, tasks, schedules, proactive automations, World Model status and relevant memories.
- Generated personal briefing with priority/due tasks and active goals.
- Canonical execution path: Personal OS -> JobQueue -> runtime.turn.
- No direct tool execution from the Personal OS layer.

## Gateway
- GET /personal-os
- GET /personal-os/briefing
- GET /personal-os/tasks
- POST /personal-os/tasks
- PATCH /personal-os/tasks/{task_id}
- POST /personal-os/tasks/{task_id}/complete
- POST /personal-os/tasks/{task_id}/execute
- DELETE /personal-os/tasks/{task_id}

## Architecture

```text
Personal OS
  |
  +-- Personal Context / MemoryFacade
  +-- Tasks
  +-- Scheduler
  +-- Proactive Automation
  +-- World Model
  |
  +--> briefing / Control Room state
  |
  +--> JobQueue --> Mission/Runtime --> ToolGateway
```

The Personal OS is a coordination/control-plane layer, not a replacement execution engine.

## Safety
- Task execution reuses the canonical runtime.
- Existing approvals, red-lines, sandboxing, verification and emergency-stop controls remain authoritative.
- Stored task content is bounded.
- Persistent state uses atomic temporary-file replacement.
- The layer does not infer private facts or silently create automations.

## Regression coverage
tests/test_phase15_personal_os.py covers persistence, briefing, completion and canonical queue submission.

The GitHub build session added the regression tests but did not execute the full repository test suite locally.