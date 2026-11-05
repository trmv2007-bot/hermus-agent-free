# Phase 6 — Runtime Reliability & Observability

Phase 6 hardens the existing universal Mission Runtime without creating a second
execution engine.

## Goals

- Give every end-to-end autonomy request a stable run_id.
- Correlate emitted lifecycle events to that run.
- Expose elapsed time and event counts as a small machine-readable health record.
- Classify structured mission failures into transient, policy, configuration,
  recoverable, terminal, or unknown classes.
- Keep observability non-blocking: telemetry failures must never become mission
  failures.
- Keep ExecutiveLoop and MissionEngine as the lifecycle authorities.

## Contract

core.runtime_health.RunTracker owns correlation and timing only. The
AutonomyFacade attaches its health snapshot to the final result and decorates
events with run_id. Existing callers that only consume ok/state/mission_id
remain compatible.

Failure classification is based on the structured failure contract, not on
user-facing response prose. This makes retry and dashboard decisions safer.

## Boundary

This layer does not:

- bypass approvals or red-line policy;
- retry destructive actions;
- replace MissionEngine;
- infer success from an exception-free return;
- make autonomous decisions on behalf of the user.

It is a reliability/observability boundary around the existing execution core.
