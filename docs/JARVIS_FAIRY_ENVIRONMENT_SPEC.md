# HERMUS JARVIS × FAIRY Environment Specification

## Why this pass exists

FAIRY and J.A.R.V.I.S. are useful as interaction and systems-design references, not as literal implementation targets.

- FAIRY is portrayed as a type III general-purpose AI with total sequential integration, producing observation reports from activity records and helping filter daily work. The important design pattern is continuous context + filtering + proactive assistance, not a single chat response.
- J.A.R.V.I.S. is portrayed as a natural-language operating layer over Tony Stark's technology. The important pattern is one executive interface over many tools, sensors, devices, and automated systems.

Current external engineering guidance points to the same architecture for real agents: model choice should be runtime-configurable; routing should match task capability/complexity and have a fallback path; model changes should be evaluated against workload latency/quality data; dashboards should expose model, latency, failures, tool activity, and trace context.

Sources:
- https://zenless.gg/fairys-observation-report-vol-1-sixth-street/
- https://zenless.gg/fairys-observation-report-vol-2-business/
- https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf02-bp02.html
- https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/choose-ai-model
- https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router-how-it-works
- https://www.datadoghq.com/blog/ai-gateways-best-practices/
- https://blog.sentry.io/ai-agent-observability-developers-guide-to-agent-monitoring/

## What HERMUS should feel like

The target is not a chatbot with a cool orb. It is:

**HERMUS = personal AI operating environment + executive agent + persistent world model + provider-agnostic model gateway + proactive personal assistant.**

The dashboard should behave like a control room:
1. Presence — one persistent HERMUS identity.
2. Command — natural language is the primary control surface.
3. World — what HERMUS currently knows about the user, systems, missions, devices and integrations.
4. Attention — what HERMUS thinks needs attention, with evidence.
5. Model Core — runtime-discovered model deployments, capabilities, reachability and user-selected roles.
6. Execution — missions, tools, approvals, computer control and verification.
7. Recovery — incidents, checkpoints, distributed leases/fencing and emergency stop.
8. Memory — personal context, goals, preferences and reusable skills.

## Implemented in this pass

### 1. Dynamic model discovery
core/models/model_catalog.py discovers models from configured runtime/provider bundles and can live-probe provider model catalogs. The dashboard only receives secret-free model metadata.

### 2. Persistent role-aware selection
core/model_preferences.py stores selections for default, reasoning, vision, coding, background, doctor and voice. Each role supports Auto. Explicit choices are validated against the currently discoverable model catalog.

### 3. Runtime selection
ModelGateway resolves omitted model choices from: explicit dashboard preference; runtime model catalog; then the legacy configured model only as a bootstrap fallback when discovery is empty.

### 4. Explicit selection wins
The main agent will not let the older keyword router silently replace an explicit dashboard-selected default model. Planning/reasoning and Meta-Counsel paths also resolve through ModelGateway role selection.

### 5. Vision is dynamic
Vision no longer defaults to a hardcoded vision model inside ModelGateway. It requests a discovered deployment with the required vision capability.

### 6. Runtime model telemetry
ModelGateway records per-model call count, successes/failures, average latency, last-used time and success rate. The Control Room can read this through /models/health.

### 7. Nexus model environment
The Nexus dashboard provides runtime discovery, role selection, Auto mode, explicit USE, SYNC/live probe, provider count, reachability count, average latency when telemetry exists, capability chips, and a dedicated Models surface.

## What should be added next

### A. Workload-aware routing
Classify requests into fast conversation, normal assistant, tool execution, deep reasoning, coding, vision, long-context synthesis and background automation. Route each class to the smallest healthy model meeting its capability and quality bar. Keep explicit user selections authoritative.

### B. Evaluation flywheel
Record task class, selected model, latency, token usage, tool success, verification result, fallback/escalation and explicit user-correction signals. Compare models on HERMUS's real workload rather than only public leaderboards.

### C. Quality-gated fallback
A retry repeats a call. A fallback changes the model. Important tasks should escalate once on structured quality failure such as schema validation, verification failure, capability mismatch, timeout or explicit low confidence.

### D. Provider governance
Expose configured provider, live reachability, authentication health, rate-limit state, circuit state, allowed model subset and capability evidence. New models should appear through discovery and explicit approval, not source-code changes.

### E. Cost/budget controls
Track provider pricing/token data where available, then support per-role budgets and alerts. Local/free models still need latency, VRAM, concurrency and resource-pressure controls.

### F. Full execution traces
Let the Control Room open a mission and see request → routing → model calls → tools → observations → approvals → recovery/fallback → verification → result.

### G. Physical-device bridge
The fictional assistants feel powerful because they inhabit an environment. Real HERMUS should add explicit connectors for computer, phone, browser, calendar, email, messaging, smart-home and local devices, each behind capability scopes and approval policies.

## Non-negotiable safety rule

The Fairy/Jarvis feeling should come from better orchestration and context, not from removing controls. Model selection must never grant tool permissions. Recovery must never bypass approval or emergency stop. Dynamic routing must remain observable and reversible.
