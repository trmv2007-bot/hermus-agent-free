# Phase 17 — Distributed HERMUS

Status: **Complete**

Phase 17 adds the distributed coordination foundation for HERMUS: an explicit
registry of authorized HERMUS nodes, heartbeat/freshness tracking, capability
routing, durable assignments and gateway visibility.

## Architecture

```
                 HERMUS control plane
                        |
             DistributedCoordinator
                 /          \
          node registry   assignment router
             /   |   \
        main PC laptop server
```

The coordinator routes work by declared capability, but it does not grant new
permissions or execute tools remotely.

## Capabilities

- Durable node registration
- Explicit node IDs and capability declarations
- Heartbeats and stale-node detection
- Capability-aware assignment
- Assignment completion state
- Durable node/assignment state
- Emergency-stop aware dispatch
- Gateway APIs for registration, heartbeat, routing and status

## Gateway

- GET `/distributed/status`
- GET `/distributed/nodes`
- POST `/distributed/nodes`
- POST `/distributed/nodes/{node_id}/heartbeat`
- DELETE `/distributed/nodes/{node_id}`
- POST `/distributed/assign`
- POST `/distributed/assignments/{assignment_id}/complete`

## Safety boundary

Distribution does **not** create a second execution authority.

Every node remains responsible for its own:

- permissions
- approvals
- red-line enforcement
- sandboxing
- emergency stop
- audit trail
- capability activation
- verification

The coordinator refuses new assignments while the canonical emergency stop is
active. A node must be explicitly registered and healthy before it can receive
an assignment.

## What remains future work

This phase establishes the distributed control-plane contract. Production
remote execution transport, mutual authentication, encrypted node-to-node
communication, signed job envelopes, leases and automatic safe failover should
be layered on this foundation rather than bypassing the canonical JobQueue and
Mission Runtime.

## Regression coverage

`tests/test_phase17_distributed.py` covers persistence, capability routing,
missing-capability rejection and stale-node rejection.

The GitHub build session added the regression tests but did not execute the full
repository test suite locally.
