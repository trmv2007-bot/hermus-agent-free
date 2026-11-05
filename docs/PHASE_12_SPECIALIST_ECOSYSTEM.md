# Phase 12 — Specialist Agent Ecosystem

Status: Complete

HERMUS now represents specialist agents as explicit capability contracts rather than only role names.

## Capabilities

- Named specialist profiles and capability sets.
- Declared inputs and outputs.
- Maximum step budgets.
- Permission scopes.
- Verification requirements.
- Bounded active-specialist count.
- Selection validation before a delegation plan is built.
- Delegation DAG nodes now carry the specialist contract alongside the objective.

Existing delegation and MissionEngine execution remain authoritative; the registry does not execute agents itself.

## Gateway

- GET /specialists

## Safety

A specialist contract describes what a worker is allowed and expected to do. It does not grant capabilities by itself. Existing permissions, approvals, sandboxing, red-line policy, verification and queue/runtime controls still apply.
