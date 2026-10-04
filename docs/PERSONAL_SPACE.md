# HERMUS Personal Space

Personal Space is HERMUS's bounded autonomous-curiosity layer.

When the owner is inactive, and HERMUS has no active high-priority attention, it may queue a small **read-only** curiosity turn through the canonical runtime. The turn can identify an idea worth exploring, but it cannot execute tools or change the system.

Useful ideas become durable proposals with a title, confidence, rationale, and next action. Proposals are **not executed automatically**. The owner must explicitly approve one through the Personal Space surface. Approval then queues the normal HERMUS runtime, so existing mission, permission, safety, verification, and learning controls remain authoritative.

Guardrails:
- idle threshold before background work
- daily curiosity-cycle cap
- one Personal Space runtime job at a time
- read-only curiosity turns
- no proposal below the confidence threshold
- explicit approval before real execution
- failures are recorded; they never become fake proposals
- disabling Personal Space stops the background loop without affecting normal HERMUS execution
