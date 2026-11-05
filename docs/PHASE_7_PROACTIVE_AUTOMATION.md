# Phase 7 — Proactive Automation

HERMUS now has an explicit event-driven automation boundary.

## What it adds

- Durable automation rules.
- Explicit enable/disable state; newly-created rules default to **disabled**.
- Event-type and exact-payload filtering.
- Cooldowns and optional fire-count limits.
- Safe action allowlist: proactive rules can only submit work to the existing
  runtime/mission queue.
- No direct tool execution from the automation layer.
- Automation state writes are atomic and best-effort.
- Queue failures do not break the canonical event path.

## Safety boundary

A proactive rule is an authorization to *submit a task*, not an authorization
to bypass normal execution policy. The resulting job still passes through the
existing queue, Mission Runtime, verification, approval gates, and red-line
controls.

## Intended next integration

Gateway/bootstrap wiring can subscribe this component to the canonical EventBus
and expose Control Room CRUD for rules. That integration should preserve the
same disabled-by-default behavior and require explicit user action to enable a
rule.
