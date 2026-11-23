> **Archived — historical document.**
> This is a design/phase note from the JARVIS/FAIRY era of this project, superseded by the
> harness-first product described in [`README.md`](../../README.md). It is **not** the current
> reference; the code it describes still exists, the centre of gravity it assumes does not.
> Content below is preserved verbatim.
>
# Phase 11 — Advanced Long-Horizon Planning

Status: Complete

HERMUS now has an explicit planning layer for objectives that span multiple dependent stages.

## Capabilities

- Dependency-aware plan steps and DAG conversion.
- Explicit success criteria on plan stages.
- Checkpoints for resumable progress.
- Structured replanning triggers.
- Recovery-step insertion when a stage fails.
- Planning remains separate from execution: MissionEngine is still the canonical executor.
- Mission requests now carry the generated long-horizon plan in the runtime result.

## Gateway

- POST /plans/long-horizon

## Safety

The planner cannot execute tools, grant permissions or bypass verification. A plan is an execution proposal; MissionEngine, ToolGateway and existing approval/red-line controls remain authoritative.
