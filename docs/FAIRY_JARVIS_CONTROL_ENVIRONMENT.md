# FAIRY × JARVIS Control Environment

## Research-driven design target

HERMUS should feel like an operating environment, not a chatbot with a dashboard.

FAIRY in *Zenless Zone Zero* is useful as a reference for proactive information filtering, navigation, data processing and life-management behavior. JARVIS is useful as a reference for natural-language control, continuous situational awareness, infrastructure integration and autonomous assistance. Marvel's current Iron Man material also emphasizes a helmet HUD with environmental information and AI capable of piloting the suit to safety. These are fictional references, so HERMUS implements the underlying interaction patterns rather than claiming fictional capabilities.

## What the research changes

### 1. Environment over chat
The primary UI is the Nexus/Control Room:
- one command surface;
- persistent system state;
- live mission/activity signals;
- model/runtime control;
- FAIRY-style attention feed;
- contextual system surfaces;
- safety and approval controls kept close to execution.
The user should be able to glance at HERMUS and understand what it is doing, what it needs, and whether it is safe to continue.

### 2. Proactive attention
FAIRY's strongest design lesson is filtering rather than flooding. HERMUS should surface only actionable signals:
- blocked mission;
- approval required;
- reliability incident;
- stale distributed node;
- important personal task;
- model/provider degradation;
- world-state change;
- self-improvement proposal.
The attention feed is a projection, not a second execution engine.

### 3. Dynamic model environment
The dashboard must never contain a hardcoded selectable model list.
The model path is now: provider/runtime discovery -> secret-free model catalog -> capability evidence -> role preference -> ModelGateway.
The UI displays deployments returned by the runtime. A user can select Auto or a currently discoverable provider/model deployment. Provider credentials stay server-side.
No model should become executable merely because its name appears in source code. If discovery returns nothing, HERMUS reports that no model is available instead of silently choosing an arbitrary hardcoded fallback.

### 4. Model choice should be capability-aware
Selection is role-aware (default, reasoning, vision, coding, background, doctor, voice) and capability-aware. The model name is not treated as proof of vision, tool use or reasoning support.
Future model routing should optimize for:
- task success;
- latency;
- reliability;
- context capacity;
- tool/vision compatibility;
- cost where provider telemetry exists;
- human review rate.
The objective is useful work per outcome, not cheapest tokens.

### 5. Safety stays visible
Agentic systems need approval boundaries and understandable action context. HERMUS therefore keeps preflight, red lines, scoped approvals, emergency stop, verification, audit events and recovery controls as first-class control-plane state.

## What should be added next
1. Model telemetry ledger — record model/provider, latency, success, retries, capability fit and accepted outcome per run.
2. Independent reviewer — optional second-model review for high-risk or high-value missions; reviewer sees the contract/evidence, not hidden chain-of-thought.
3. Outcome ROI — track cost/latency per accepted result rather than raw token counts alone.
4. World-event prioritization — rank observations by user impact and freshness before surfacing them.
5. Attention policy — user-configurable quiet hours, priority classes and notification destinations.
6. Model lifecycle controls — discover, test, quarantine and retire deployments without changing application code.
7. Provider setup UX — show configured/reachable/capability state without exposing credentials.
8. Execution explainability — show goal, plan, permissions, checkpoints, verification and final evidence in one mission view.
9. Voice continuity — make interruption, barge-in, notification and active-run state visually consistent with the same mission runtime.
10. Recovery rehearsal — periodically test restore/failover paths in a safe sandbox.

## Architectural rule
New capability must plug into the existing canonical boundaries:
- Mission Runtime for execution lifecycle;
- ToolGateway for tool calls;
- ModelGateway for model selection/completion;
- MemoryFacade for memory writes;
- EventBus for event authority;
- JobQueue for proactive/scheduled work;
- safety/approval controls for protected actions.
Never create a second execution engine just to make the UI look autonomous.

## Evidence
Current agentic-system guidance emphasizes visibility into usage and outcome ROI, matching model capacity to task complexity, and governing tool/action access before scaling. OpenAI's agentic governance research also recommends constrained action spaces, meaningful approval context, and automated monitoring for systems operating at scale.
See the project roadmap for implementation status.