# Phase 9 — Persistent Personal Context

Status: Complete

Phase 9 turns the existing memory subsystem into a structured personal-context layer without creating a competing memory writer.

## What changed

- Structured preferences.
- Explicit personal facts.
- Goals with status, priority, project and optional deadline.
- Project records with extensible details.
- Current focus tracking.
- Context snapshots combining:
  - user model
  - relevant typed memories
  - relevant recent sessions
- Bounded prompt context for each agent turn.
- Explicit-turn observation for recognizable statements such as:
  - "my name is ..."
  - "i prefer ..."
  - "i like ..."
  - "i use ..."
  - "my favorite ... is ..."
  - "remember that ..."
- Context is persisted through the canonical MemoryFacade.
- Agent turns now capture explicit context and inject relevant personal context into the system prompt.
- API operations for reading and updating preferences, goals, focus and projects.

## Privacy / grounding rule

The observer uses explicit, recognizable user statements. It does not infer personal facts merely from silence, behavioral telemetry or unstated assumptions.

The layer is a context/index over the existing memory system, not a second process-wide memory writer.

## API

The realtime gateway exposes:

- GET /personal-context
- POST /personal-context/preference
- POST /personal-context/goal
- POST /personal-context/focus
- POST /personal-context/project

## Result

HERMUS can now combine a current request with persistent personal context instead of treating every conversation as an isolated turn.
