# Phase 18 — HERMUS Reliability & Recovery

**Status: Complete**

Phase 18 adds a reliability control plane around the existing canonical JobQueue,
Mission Runtime, ToolGateway, ModelGateway, MemoryFacade, EventBus and distributed
coordination layer. It does not introduce a competing executor.

## Included

- bounded exponential retry policy with circuit breakers
- durable idempotency receipts for side-effect operations
- durable run checkpoints for crash/resume metadata
- incident ledger and supervisor health view
- integrity-checked recovery snapshots
- resource/degraded-mode signals
- emergency-stop-aware health state
- distributed leases/fencing and duplicate-job protection
- Control Room reliability visibility
- regression/failure-injection tests

## Safety

Reliability may retry recoverable infrastructure failures, but it must not retry
policy denials, approval gates or unsafe non-idempotent side effects without an
idempotency receipt. Emergency stop remains authoritative. Recovery can restart
or resume state but cannot grant capabilities or weaken red lines.

## Operational boundary

Provider-specific OAuth recovery, mTLS deployment, external backup storage and
cloud failover remain deployment concerns. HERMUS exposes the contracts and
local integrity mechanisms without inventing credentials.

## Verification

The repository contains targeted reliability tests and CI configuration. A full
local test-suite execution was not performed in this build environment.
