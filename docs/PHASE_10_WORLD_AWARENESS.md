# Phase 10 — World Awareness / Live World Model

Status: Complete

HERMUS now has a read-only world-awareness reconciliation layer above the existing WorldModel and connector system.

## What changed

- Reconciles enabled observation connectors into the canonical WorldModel.
- Adds host/runtime process observations when psutil is available.
- Adds Git workspace commit, branch and dirty-status observations.
- Observes the existing HERMUS browser session without launching a browser or opening a new network session.
- Tracks observation freshness and age.
- Computes a bounded observation digest for change detection.
- Emits reconciliation and observation-error events.
- Preserves source, confidence, permission scope and observation timestamps.
- Keeps connector state visible to the Control Room/gateway.
- Executive perception now performs world reconciliation as part of the normal request lifecycle.

## Gateway

- GET /world
- POST /world/refresh

The refresh endpoint is read-only. It does not grant computer, browser, filesystem-write or external-service permissions.

## Architecture

Request → Perception → World Awareness → Observation Connectors → WorldModel → Freshness / provenance / change detection → Executive planning

Execution remains separate:

Plan → Mission Runtime → ToolGateway → Safety/Approval → Execute → Verify

## Safety and privacy

World awareness only records observations that the configured connector is permitted to read. Obvious credential-shaped fields are redacted by the WorldModel before persistence. Browser observation only inspects an already-running HERMUS browser page; it never launches a browser as a side effect of awareness.

## Regression coverage

Phase 10 tests cover Git/workspace observation, freshness reporting, observation change tracking, provenance and confidence metadata, and browser-state observation.

A full repository test-suite run was not performed as part of the GitHub-only build session.
