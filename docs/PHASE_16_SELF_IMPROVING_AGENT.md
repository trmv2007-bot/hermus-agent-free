# Phase 16 — Self-Improving Agent

Status: **Complete**

Phase 16 upgrades HERMUS from reflection-only learning into a governed self-improvement loop.

## Capabilities
- Reflection continues to detect tool failures, user corrections and weak trajectories.
- Improvements are converted into explicit, auditable change proposals.
- Every proposal is evaluated by the deterministic EvolutionPolicy.
- Ordinary tested changes can be automatically classified as allowed for development.
- Protected control-plane changes require independent review.
- Bypass attempts and dangerous host-access patterns are denied.
- Proposal history is persisted in an append-only evolution ledger.
- Control Room/API can inspect self-improvement status and proposal history.
- Existing SkillForge repeatability, verification, quarantine and lessons systems remain part of the learning path.

## Runtime path

```text
Completed work
   -> reflection
   -> lessons / skills
   -> improvement proposal
   -> EvolutionPolicy
       |
       +--> allow: safe development/evaluation path
       +--> review: independent approval required
       +--> deny: blocked
```

Self-improvement does not silently rewrite protected controls. The policy is deterministic and independent of the LLM decision path.

## Gateway
- GET /self-improvement/status
- GET /self-improvement/proposals
- POST /self-improvement/reflect

## Safety invariants
- No self-disablement of emergency stop, permissions, sandboxing or approval gates.
- No deletion or falsification of audit history.
- No silent capability escalation.
- No automatic approval of protected control-plane changes.
- Evidence and test requirements remain explicit.

## Regression coverage
`tests/test_phase16_self_improvement.py` covers protected-file review, bypass denial, normal-change allowance and reflection-to-proposal flow.

The GitHub build session added the regression tests but did not execute the full repository test suite locally.