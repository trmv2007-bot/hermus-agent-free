# HERMUS Production Hardening Checklist

This checklist separates code-complete work from environment-dependent validation.

## Built in-repository

- [x] Durable idempotency receipts
- [x] Crash/resume checkpoints
- [x] Retry policy and circuit breakers
- [x] Incident ledger
- [x] Integrity-checked snapshots and restore
- [x] Verification-failure rollback hook
- [x] Resource/degraded-mode detection
- [x] Distributed assignment deduplication and fencing hooks
- [x] Reliability Control Room telemetry
- [x] Dependency/security CI audit
- [x] Failure-injection regression tests
- [x] Dependency-free metrics registry
- [x] Model provider fallback architecture (canonical ModelGateway)

## Must be validated on a real deployment

- [ ] Full pytest/ruff/mypy run on the target environment
- [ ] Two or more real worker nodes connected
- [ ] mTLS certificates provisioned and rotated
- [ ] OAuth credentials connected for selected providers
- [ ] Off-machine backup target configured
- [ ] Restore drill completed on a clean machine
- [ ] Real voice/STT/TTS devices tested
- [ ] API rate-limit and credential-expiry scenarios tested
- [ ] Power-loss / process-kill resume drill completed
- [ ] Network partition and node failover drill completed
- [ ] Resource exhaustion drill completed
- [ ] Production alerting destination configured

## Acceptance rule

HERMUS is not considered production-validated merely because the code paths exist.
A deployment should only be marked operational after the environment-dependent
checks above have been exercised and the resulting evidence is recorded.
