# Hermus Agent Free — JARVIS/ULTRON VISION

> **Status: long-term vision, not an active roadmap.** This document describes
> the aspirational end-state that shapes design direction. It predates and does
> not supersede [`IMPROVEMENT_PLAN.md`](IMPROVEMENT_PLAN.md) (near-term work),
> [`SPEC_PERSISTENT_FLEET.md`](SPEC_PERSISTENT_FLEET.md) (Fleet v2 spec) or
> [`RED_LINES.md`](RED_LINES.md) (non-negotiable boundaries — nothing here
> overrides them). Phase/week estimates below are illustrative, not committed.

**Vision**: An autonomous AI assistant with **deep system control** (OS, apps, files, processes) and **unrestricted internet agency** (browsing, APIs, auth, automation) — running locally, privacy-first, self-improving.

---

## 🎯 Target Capabilities (Jarvis/Ultron Tier)

### 🖥️ SYSTEM CONTROL (Local Omnipotence)

| Domain | Current State | Jarvis Target |
|--------|---------------|---------------|
| **File System** | Basic read/write via tools | Full VFS: search, watch, sync, diff, patch, archive, encrypt, version |
| **Process Management** | Subprocess sandbox | Spawn, monitor, attach, debug, profile, limit (cgroups), restart policies |
| **Window/Display** | Screen capture (placeholder) | Full X11/Wayland/Win32: focus, resize, move, screenshot, OCR, click, type, scroll |
| **Clipboard** | None | Read/write text, images, files; history; sync across devices |
| **Notifications** | None | System tray, toast, DBus, WinRT, push to phone |
| **Audio I/O** | TTS/STT basics | Always-listening wake word, real-time transcription, voice activity detection |
| **Hardware** | GPU/NPU detection | Fan control, thermal mgmt, power profiles, peripheral enumeration |
| **Package Mgmt** | None | Winget/Choco/Apt/Brew/Pip/Npm/Cargo — install, update, audit, rollback |

### 🌐 INTERNET AGENCY (Web Omnipotence)

| Domain | Current State | Jarvis Target |
|--------|---------------|---------------|
| **Browsing** | DDG search + basic scrape | Full CDP/Playwright: SPA navigation, Shadow DOM, WebGL, Canvas, WASM |
| **Authentication** | API keys only | OAuth2/OIDC/SAML flows, cookie jar persistence, 2FA/TOTP, passkeys, session replay |
| **API Integration** | REST via tools | GraphQL introspection, OpenAPI/Swagger ingestion, auto-generated clients, rate-limit aware |
| **Web Automation** | None | Form fill, file upload, drag-drop, infinite scroll, CAPTCHA solving (ethical), download mgmt |
| **Real-time** | SSE/WS client | WebSocket, Server-Sent Events, WebRTC, MQTT, gRPC-Web — subscribe, publish, proxy |
| **Search** | DDG only | Multi-engine (Google, Bing, Brave, Kagi, SearXNG), vertical (GitHub, Scholar, Patents), custom crawlers |
| **Content Processing** | Basic extract | Readability, PDF/Office parsing, video/audio transcription, table extraction, schema.org |

### 🧠 COGNITIVE ARCHITECTURE (Ultron Brain)

| Capability | Current | Target |
|------------|---------|--------|
| **Planning** | Mission DAG | HTN/GOAP planner with contingent branches, resource estimation, risk assessment |
| **Memory** | SQLite + FTS5 | Hybrid: episodic (vector), semantic (graph), procedural (code), working (context window) |
| **Reasoning** | ReAct loop | Chain-of-thought + Tree-of-thought + MCTS for complex decisions |
| **Self-Improvement** | Trajectory → skills | Genetic program synthesis, prompt optimization, tool distillation, model fine-tuning |
| **Multi-modal** | Text only | Vision (screen/camera), Audio (mic/system), Structured (JSON/CSV/Parquet) |
| **World Model** | None | Persistent environment state: file tree, running apps, network topology, user preferences |
---

## 🏗️ TECHNICAL ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────────┐
│                      HERMUS CORE (Rust/Go)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │  Kernel  │ │  Memory  │ │  Planner │ │  Skills  │           │
│  │  (Actor) │ │  (Vector │ │ (HTN/    │ │  (WASM   │           │
│  │          │ │   +Graph)│ │  GOAP)   │ │  Module) │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  SYSTEM BUS   │    │   NET BUS     │    │  MODEL BUS    │
│  (Local RPC)  │    │  (HTTP/WS/    │    │  (Multi-LLM   │
│               │    │   GraphQL/    │    │   Router)     │
│ - fs, proc,   │    │   gRPC,       │    │               │
│   win, clip   │    │   MQTT)       │    │ - Ollama      │
│ - audio, hw   │    │               │    │ - Local LLM   │
│ - notify, pkg │    │ - Browser CDP │    │ - API (Groq,  │
└───────────────┘    │ - Auth/OAuth  │    │   OpenRouter) │
                     │ - Crawlers    │    │ - Vision/VLM  │
                     └───────────────┘    └───────────────┘
```

### Core Technology Choices

| Layer | Technology | Rationale |
|-------|------------|-----------|
| **Core Runtime** | **Rust** (tokio, axum) | Memory safety, performance, WASM host, FFI for system calls |
| **Agent Host** | **Python** (current) → **Rust** | Gradual migration; Python for tool ecosystem, Rust for kernel |
| **Planner** | **HTN (Hierarchical Task Network)** + **GOAP** | Handles partial ordering, contingencies, resource constraints |
| **Memory** | **LanceDB** (vector) + **Kuzu/Neo4j** (graph) + **SQLite** (KV) | Columnar vectors, property graph, ACID metadata |
| **Browser** | **Playwright + CDP** | Full Chrome DevTools Protocol access |
| **System Interface** | **TAURI 2.0** / **WRT** (Web Runtime) | Secure sandboxed system APIs via capability tokens |
| **Model Serving** | **llama.cpp** / **vLLM** / **TGI** | Local + remote unified behind OpenAI-compatible gateway |
| **Skill Packaging** | **WASM Components** | Portable, sandboxed, language-agnostic tool modules |

---

## 🔐 SECURITY MODEL

### Capability Token System (OPA/Rego)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CAPABILITY TOKEN FLOW                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────┐     Request + Token      ┌──────────────┐                │
│  │  Agent   │ ──────────────────────▶ │  OPA Engine  │                │
│  │ (WASM)   │                          │  (Rego Policy)│                │
│  └──────────┘                          └──────┬───────┘                │
│                                               │                        │
│                        ┌──────────────────────┼──────────────────┐    │
│                        ▼                      ▼                  ▼    │
│               ┌──────────────┐        ┌──────────────┐    ┌────────────┐│
│               │   ALLOW      │        │   DENY       │    │  CONDITIONAL│
│               │  (execute)   │        │  (audit log) │    │  (MFA/step) │
│               └──────────────┘        └──────────────┘    └────────────┘│
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Token Structure (JWT-like Capability Token)

```json
{
  "capability": "fs:write",
  "resource": "/home/user/projects/**",
  "constraints": {
    "max_size_mb": 100,
    "allowed_extensions": [".rs", ".py", ".md", ".toml"],
    "deny_patterns": ["**/.git/**", "**/secrets/**", "**/*.key"]
  },
  "conditions": {
    "require_approval": false,
    "time_window": "09:00-18:00",
    "max_ops_per_min": 60
  },
  "metadata": {
    "issued_by": "user:owner",
    "issued_at": "2026-01-15T10:30:00Z",
    "expires_at": "2026-01-15T18:00:00Z",
    "session_id": "sess_abc123"
  },
  "signature": "ed25519:base64..."
}
```

### Policy Examples (Rego)

```rego
# fs_write.rego
package hermus.capabilities.fs.write

default allow = false

allow {
    input.capability == "fs:write"
    input.resource.matches("^/home/user/projects/.*")
    not input.resource.matches(".*\\.git/.*")
    not input.resource.matches(".*secrets/.*")
    input.constraints.max_size_mb <= 100
    count(input.operations) <= input.constraints.max_ops_per_min
}

# Conditional approval for sensitive paths
require_approval {
    input.resource.matches(".*/production/.*")
    input.resource.matches(".*\\.env.*")
}

# Rate limiting
deny {
    input.operations_per_minute > input.constraints.max_ops_per_min
    msg := sprintf("Rate limit exceeded: %d > %d", [input.operations_per_minute, input.constraints.max_ops_per_min])
}
```

### Capability Registry

| Capability | Resource Pattern | Default Constraints | Approval Required |
|------------|------------------|---------------------|-------------------|
| `fs:read` | `/home/user/**` | max 1GB/day | No |
| `fs:write` | `/home/user/projects/**` | 100MB/file, 60 ops/min | No |
| `fs:write` | `/etc/**`, `/Windows/**` | - | **Yes (MFA)** |
| `proc:spawn` | `cargo`, `npm`, `pip`, `python` | 5 concurrent, 30min timeout | No |
| `proc:spawn` | `sudo`, `powershell -ExecutionPolicy Bypass` | - | **Yes (MFA)** |
| `net:http` | `api.github.com`, `*.openai.com` | 100 req/min, no auth leakage | No |
| `net:http` | `*`, with `Authorization` header | - | **Yes (step-up)** |
| `browser:cdp` | `localhost:*`, `*.local` | Headless only | No |
| `browser:cdp` | `bank.*`, `*.gov`, `accounts.*` | - | **Yes (MFA + screen record)** |
| `audio:record` | `default` | 30s chunks, auto-delete | **Yes (visual indicator)** |
| `skill:install` | `registry.hermus.dev/**` | Signed, sandboxed | **Yes (review)** |

### Audit Log Schema (Append-Only, Tamper-Evident)

```sql
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id      UUID NOT NULL,
    agent_id        TEXT NOT NULL,
    capability      TEXT NOT NULL,
    resource        TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('allow', 'deny', 'conditional')),
    policy_version  TEXT NOT NULL,
    input_hash      BYTEA NOT NULL,
    output_hash     BYTEA NOT NULL,
    prev_hash       BYTEA,
    metadata        JSONB
);
```

---

## 🗺️ IMPLEMENTATION ROADMAP

### Phase 0: Foundation (Weeks 1-4) — "Iron Man Mark I"

| Week | Deliverable | Success Criteria |
|------|-------------|------------------|
| 1 | **Rust Core Skeleton** | `cargo run` → actor system, message bus, config loader |
| 2 | **Capability Token System** | OPA/Rego integration, token minting, audit log (SQLite) |
| 3 | **System Bus v1** | File ops (read/write/list/watch), process spawn, clipboard |
| 4 | **Model Bus v1** | llama.cpp integration, OpenAI-compatible router, tool calling |

**Exit Criteria**: Agent can read/write files, spawn processes, call local LLM, all gated by capability tokens with audit trail.

---

### Phase 1: Local Omnipotence (Weeks 5-10) — "Jarvis Mk II"

| Week | Deliverable | Success Criteria |
|------|-------------|------------------|
| 5 | **Window/Display Control** | Win32/X11/Wayland: focus, move, resize, screenshot, OCR |
| 6 | **Audio Pipeline** | Wake word (Porcupine), STT (Whisper.cpp), TTS (Piper), VAD |
| 7 | **Package Manager Abstraction** | Winget/Choco/Apt/Brew unified interface, install/update/audit |
| 8 | **Memory System v1** | LanceDB (vector) + Kuzu (graph) + SQLite (KV), hybrid retrieval |
| 9 | **HTN/GOAP Planner** | Task decomposition, contingent branches, resource estimation |
| 10 | **World Model v1** | File tree, running processes, network ports, user prefs persistence |

**Exit Criteria**: Full local control via voice/text; planner executes multi-step tasks (e.g., "refactor this crate, run tests, commit").

---

### Phase 2: Internet Agency (Weeks 11-18) — "Ultron Awakens"

| Week | Deliverable | Success Criteria |
|------|-------------|------------------|
| 11 | **Browser Engine** | Playwright + CDP: SPA nav, Shadow DOM, frames, downloads |
| 12 | **Auth Framework** | OAuth2/OIDC/SAML, cookie jar, 2FA/TOTP, passkeys, session replay |
| 13 | **API Integration Layer** | OpenAPI/GraphQL ingestion, auto-clients, rate-limit middleware |
| 14 | **Web Automation** | Form fill, file upload, drag-drop, infinite scroll, ethical CAPTCHA |
| 15 | **Real-time Protocols** | WS, SSE, WebRTC, MQTT, gRPC-Web — subscribe/publish/proxy |
| 16 | **Multi-Engine Search** | Google/Bing/Brave/Kagi/SearXNG + vertical (GitHub, Scholar, Patents) |
| 17 | **Content Processing** | Readability, PDF/Office, video/audio transcription, table extraction |
| 18 | **Crawler Framework** | Custom crawlers, robots.txt respect, politeness, incremental sync |

**Exit Criteria**: Agent can "book a flight," "compare GPU prices across 5 sites," "monitor GitHub issues and auto-triage."

---

### Phase 3: Cognitive Leap (Weeks 19-28) — "Ultron Prime"

| Week | Deliverable | Success Criteria |
|------|-------------|------------------|
| 19 | **Tree-of-Thought + MCTS** | Complex decision making with backtracking, value estimation |
| 20 | **Procedural Memory (Skills)** | WASM skill modules, genetic program synthesis, tool distillation |
| 21 | **Self-Improvement Loop** | Trajectory → skill extraction → validation → registry publish |
| 22 | **Multi-modal Fusion** | Vision (screen/camera), Audio, Structured — unified embedding space |
| 23 | **Persistent World Model** | Cross-session environment state, predictive simulation |
| 24 | **Multi-Agent Orchestration** | Sub-agents for parallel tasks, consensus, conflict resolution |
| 25 | **Code Intelligence** | AST-aware editing, refactoring, test generation, dependency analysis |
| 26 | **Long-Horizon Planning** | Multi-day projects, milestone tracking, resource budgeting |
| 27 | **Distributed Execution** | Remote workers, edge offload, peer-to-peer skill sharing |
| 28 | **Production Hardening** | Observability, chaos testing, disaster recovery, security audit |

**Exit Criteria**: Agent autonomously maintains a codebase, learns new APIs from docs, optimizes own prompts, proposes architecture improvements.

---

## ⚡ IMMEDIATE NEXT STEPS (THIS WEEK)

| Day | Task | Owner | Artifact |
|-----|------|-------|----------|
| Mon | Initialize Rust workspace: `hermus-core`, `hermus-sysbus`, `hermus-netbus`, `hermus-modelbus`, `hermus-planner`, `hermus-memory`, `hermus-skills` | You | `Cargo.toml` workspace |
| Tue | Actor kernel: `tokio` + `kameo`/`actix`, message envelopes, supervision tree | You | `src/kernel/` |
| Wed | Capability token: `jsonwebtoken` + `opa-wasm`, Regor compiler, token mint/verify CLI | You | `hermus-capability` crate |
| Thu | OPA policy bundle: base policies for fs, proc, net; unit tests with `regal` | You | `policies/*.rego` |
| Fri | System Bus v1: file ops (read/write/list/watch via `tokio::fs` + `notify`), process spawn (`tokio::process`) | You | `hermus-sysbus` crate |
| Sat | Model Bus v1: `llama.cpp` bindings (`llama-cpp-2`), OpenAI-compatible `/v1/chat/completions` | You | `hermus-modelbus` crate |
| Sun | Integration test: Agent reads file → summarizes via LLM → writes summary → audit log verified | You | `tests/integration/` |

**Definition of Done (Week 1)**: `cargo test --workspace` passes; `cargo run --example hello_jarvis` demonstrates token-gated file read + LLM call + audit entry.

---

## 💰 RESOURCE ESTIMATES

### Compute (Local-First, GPU Recommended)

| Component | Minimum | Recommended | Optimal |
|-----------|---------|-------------|---------|
| **CPU** | 8C/16T (Ryzen 7 / i7-13xxx) | 16C/32T (Ryzen 9 / i9-13xxx) | 24C/48T (Threadripper / Xeon-W) |
| **RAM** | 32 GB DDR5 | 64 GB DDR5 | 128 GB DDR5 ECC |
| **GPU** | 12 GB VRAM (RTX 3060/4060) | 24 GB VRAM (RTX 3090/4090) | 48 GB VRAM (A6000 / 2×4090) |
| **Storage** | 2 TB NVMe | 4 TB NVMe + 2 TB SATA | 8 TB NVMe RAID + 16 TB HDD cold |
| **NPU** | — | Intel Core Ultra / AMD Ryzen AI | Qualcomm Snapdragon X Elite |

### Model Serving (Local)

| Model | Quant | VRAM | Use Case |
|-------|-------|------|----------|
| Llama-3.1-8B | Q4_K_M | ~6 GB | Fast planning, tool calling |
| Llama-3.1-70B | Q4_K_M | ~40 GB | Complex reasoning, code gen |
| Qwen2.5-Coder-32B | Q4_K_M | ~18 GB | Code intelligence |
| Nemotron-3-Ultra | Q4_K_M | ~28 GB | General reasoning |
| Phi-3.5-mini | Q4_K_M | ~2.5 GB | Edge/fallback, classification |
| **Total (concurrent)** | | **~95 GB** | Multi-model router |

> **Note**: With 24 GB VRAM, run 8B + 32B concurrently; offload 70B to CPU (slow) or use remote (Groq/OpenRouter) for heavy lifts.

### Cloud Burst Budget (Monthly)

| Service | Est. Cost | Purpose |
|---------|-----------|---------|
| Groq (Llama-3.1-70B) | $50-200 | Ultra-fast inference for planning |
| OpenRouter (fallback) | $30-100 | Model diversity, vision models |
| Browserbase / ScrapingBee | $50-150 | Cloud browser for heavy scraping |
| GitHub Codespaces / Fly.io | $20-50 | Remote skill execution, CI |
| **Total** | **$150-500/mo** | Burst capacity, not baseline |

### Development Time (Solo)

| Phase | Weeks | Cumulative |
|-------|-------|------------|
| Phase 0: Foundation | 4 | 4 |
| Phase 1: Local Omnipotence | 6 | 10 |
| Phase 2: Internet Agency | 8 | 18 |
| Phase 3: Cognitive Leap | 10 | 28 |
| **Total** | **28 weeks** | **~7 months** |

> With 2 engineers: ~4 months. With 4: ~2.5 months (Brooks' law applies — planner & memory are sequential bottlenecks).

---

## 👥 HUMAN-IN-THE-LOOP DESIGN

### Approval Tiers

| Tier | Trigger | UX | Timeout | Fallback |
|------|---------|-----|---------|----------|
| **0 - Auto** | Low-risk, whitelisted | None (silent) | N/A | N/A |
| **1 - Notify** | New domain, elevated caps | Toast + log entry | 30s auto-approve | Deny |
| **2 - Confirm** | Write to protected, financial | Modal dialog (details + diff) | 5 min | Deny |
| **3 - MFA** | Sudo, auth tokens, browser banking | Push to phone (TOTP/WebAuthn) | 2 min | Deny |
| **4 - Review** | Skill install, policy change | Full UI: diff, risk score, rollback plan | 24h | Deny |

### Intervention Points

```
┌────────────────────────────────────────────────────────────────┐
│                    AGENT EXECUTION LOOP                        │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  PLAN → [Tier 0/1] → EXECUTE → [Tier 1/2] → OBSERVE           │
│    │                            │                              │
│    ▼                            ▼                              │
│  [Tier 2/3]                   [Tier 2/3]                       │
│  (plan approval)              (action approval)                │
│                                                                │
│  On DENY:  →  Replan with constraints  →  Present alternatives │
│  On TIMEOUT:  →  Escalate tier  →  Safe default (deny)         │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### Transparency Dashboard (Local Web UI)

- **Live Capability Graph**: What tokens active, what resources accessed
- **Decision Trail**: Every allow/deny with policy rule reference
- **Skill Registry**: Installed skills, source, permissions, last audit
- **World Model View**: Current file tree, processes, network conns
- **Plan Visualization**: HTN decomposition, contingencies, resource estimates
- **Rollback Button**: One-click revert last N operations (files, config, packages)

---

## ❓ DECISIONS NEEDED

| # | Decision | Options | Recommendation | Blocker? |
|---|----------|---------|----------------|----------|
| 1 | **Core Language** | Rust (full) vs Rust+Python (gradual) | **Rust full** — avoids FFI boundary, single binary deploy | No |
| 2 | **Actor Framework** | `kameo` vs `actix` vs `bastion` vs custom | **kameo** — lightweight, WASM-friendly, good supervision | No |
| 3 | **Vector DB** | LanceDB vs Qdrant vs Chroma | **LanceDB** — embedded, columnar, ACID, no separate server | No |
| 4 | **Graph DB** | Kuzu (embedded) vs Neo4j (server) | **Kuzu** — embedded, Cypher, WASM-compilable | No |
| 5 | **Browser** | Playwright vs ChromeDP vs Rod | **Playwright** — best CDP, cross-browser, maintained | No |
| 6 | **System UI** | Tauri 2.0 vs WRT vs egui + custom | **Tauri 2.0** — capability model matches, WebView2/WebKitGTK | No |
| 7 | **Planner** | Custom HTN/GOAP vs `planning-rs` vs ASP (clingo) | **Custom HTN/GOAP** — need contingent branches + resource model | No |
| 8 | **Policy Engine** | OPA (Rego) vs Cedar vs Casbin | **OPA** — mature, WASM, GitHub Actions for policy CI | No |
| 9 | **Model Gateway** | llama.cpp server vs vLLM vs TGI vs Ollama | **llama.cpp + custom router** — lowest overhead, unified API | No |
| 10 | **Skill Runtime** | WASM Components (wasmtime) vs wasmer vs extism | **wasmtime (component model)** — standard, async, WIT definitions | No |
| 11 | **Auth Storage** | OS keyring vs encrypted SQLite vs HashiCorp Vault (local) | **OS keyring + encrypted SQLite** — native, no deps | No |
| 12 | **Telemetry** | OpenTelemetry + Jaeger vs custom event log | **OpenTelemetry** — standards, visualization ecosystem | No |

---

## ✅ APPROVAL GATE

### Gate 0: Vision Sign-Off (This Document)

- [ ] **Technical Lead**: Architecture viable, no showstoppers
- [ ] **Security Review**: Capability model covers threat model
- [ ] **Product**: Capabilities align with user value
- [ ] **Resource Owner**: Compute/budget approved

### Gate 1: Phase 0 Complete (Week 4)

- [ ] Rust workspace builds, tests pass
- [ ] Capability tokens mint/verify/audit end-to-end
- [ ] System Bus: file, process, clipboard ops gated
- [ ] Model Bus: local LLM tool calling works
- [ ] Integration test: "read file → summarize → write" with audit trail

### Gate 2: Phase 1 Complete (Week 10)

- [ ] Window/audio/pkg/memory/planner/world-model all integrated
- [ ] Voice command: "refactor X, test, commit" executes autonomously
- [ ] Zero Tier 3+ approvals for standard dev workflows
- [ ] Benchmark: <200ms p99 for local tool calls

### Gate 3: Phase 2 Complete (Week 18)

- [ ] Browser automation: login, navigate, extract, act on 10 diverse sites
- [ ] Auth: OAuth flow completion, session persistence, 2FA handling
- [ ] Search: multi-engine results fused, ranked, cited
- [ ] Red-team: no credential leakage, no SSRF, no unauthorized nav

### Gate 4: Phase 3 Complete (Week 28)

- [ ] Self-improvement: agent extracts skill from trajectory, publishes, uses
- [ ] Multi-agent: 3+ sub-agents coordinate on complex task
- [ ] Long-horizon: 5-day project planned, executed, adapted
- [ ] Security audit: third-party review, zero critical findings

---

**Document Version**: 1.1  
**Last Updated**: 2026-09-21 (status banner + review refresh)  
**Next Review**: Gate 0 sign-off + 1 week