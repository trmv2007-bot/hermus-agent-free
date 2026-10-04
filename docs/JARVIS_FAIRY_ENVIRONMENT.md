# HERMUS: JARVIS × FAIRY Environment Direction

## Research-driven design

HERMUS should feel like one personal operating environment, not a collection of admin panels.

JARVIS is the reference for an integrated operating layer: system/device awareness, natural-language control, continuous situational information, and autonomous operation around a user. Marvel's own material describes Iron Man's technology as integrating environmental information, telecommunications, and AI capable of piloting the armor to safety. (See Marvel source: turn658310search2.)

FAIRY is the reference for proactive personal assistance: she is framed as a general-purpose AI for navigation, data processing, life management, automatic reporting, and assistance with repetitive work. The game has also expanded Fairy Auto Explore, including Quick Explore and broader commission coverage, reinforcing the idea that the assistant should remove routine work rather than merely answer questions. (See sources: turn482005search2, turn482005search6, turn482005search15.)

## Environment priorities

### 1. One command surface

The Nexus control room is the primary environment:

- conversational command entry
- live mission/activity state
- system capability status
- proactive attention
- model selection
- contextual system inspection
- safety visibility

The dashboard must not become a second execution engine. Commands still go to the canonical gateway/runtime.

### 2. Proactive attention

The assistant should continuously summarize things that deserve the user's attention:

- reliability incidents
- distributed node degradation
- emergency-state changes
- open personal tasks
- self-improvement state
- important world changes
- scheduled/proactive work

This is the FAIRY behavior to emulate: collect, filter, prioritize, and surface useful information instead of making the user search for it.

### 3. Dynamic model discovery

The model selector must never be a hardcoded list of model names.

HERMUS now uses this chain:

provider credentials/runtime -> live model discovery -> capability negotiation -> selectable catalog -> persisted role preference -> ModelGateway

OpenAI documents GET /v1/models as the API for listing currently available models; HERMUS follows the same discovery-first principle across its compatible providers.

The UI shows only:

- models actually discovered from configured runtimes
- models returned by provider catalogs
- capability evidence
- reachability/live status
- the explicit AUTO option

Provider preset defaults remain backend bootstrap fallbacks only; they are not injected into the dashboard catalog.

### 4. Capability-aware roles

Model selection is role-aware:

default, reasoning, vision, coding, background, doctor, voice.

A role preference is accepted only when the deployment is discoverable. Vision, tool use, context size and other capabilities are read through the existing capability negotiation layer.

### 5. Server-side secrets

Provider credentials are never returned by the model catalog and are never placed in browser code. OpenAI's API guidance likewise keeps API keys in environment/server-side configuration rather than client code.

### 6. Selection must affect runtime

Changing a model in the dashboard is useful only when future conversations actually use that choice. HERMUS therefore keeps ordinary web sessions unpinned and lets the canonical ModelGateway resolve the selected deployment.

### 7. Better operator tooling

The next high-value environment additions are:

- side-by-side model comparison using the same prompt
- latency/success/token telemetry per deployment
- per-role model policies
- provider connection tests
- capability mismatch explanations
- automatic fallback visibility
- conversation timeline and interruption controls
- richer multimodal workspace
- attention prioritization based on urgency and user goals
- device/desktop status as a first-class context signal

This mirrors patterns used by mature model gateways: model catalogs, connection testing, model pickers, routing groups, and side-by-side comparison.

## Current HERMUS implementation

The repository now contains:

- runtime-discovered model catalog
- persistent role-aware model selection
- capability-aware filtering
- dynamic vision model resolution
- Nexus model core UI
- FAIRY attention surface
- web-session integration that respects dynamic selection
- console manifest exposure for model discovery

## Non-goals

HERMUS should not:

- expose provider secrets to the browser
- invent models that the configured provider does not actually expose
- let the dashboard execute tools directly
- let model selection bypass safety controls
- assume one provider or one model family is permanently installed

> HERMUS chooses from what the environment can actually use, while the user remains in control of what it is allowed to do.