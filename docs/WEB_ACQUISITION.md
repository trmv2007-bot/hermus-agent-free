# Web Acquisition — Scrapling integration in Hermus

Hermus acquires web information through **one canonical subsystem**: `core/web`
(the *WebGateway*), backed by [Scrapling](https://github.com/D4Vinci/Scrapling)
as the fetch/parse engine. This document is the source of truth for the
architecture, capabilities, configuration, security model and limitations.

---

## 1. What Scrapling adds

Scrapling (BSD-3-Clause, optional dependency) provides:

| Capability | What Hermus uses it for |
| --- | --- |
| `Fetcher` (curl_cffi, browser TLS fingerprint) | **static** strategy — fast HTTP that looks like a real browser |
| `DynamicFetcher` (Playwright Chromium) | **dynamic** strategy — JS-rendered pages, `wait_selector`, `network_idle` |
| `StealthyFetcher` (hardened Chromium) | **stealth** strategy — anti-bot interstitials, opt-in only |
| `FetcherSession` / `DynamicSession` / `StealthySession` | persistent, domain-pinned sessions (cookies, reuse) |
| Adaptive extraction (`css(..., adaptive=True, auto_save=True)`) | selectors that survive site redesigns |
| `capture_xhr` | capturing the underlying JSON APIs a page calls |
| `Response.markdown()` (`scrapling[ai]`) | clean Markdown for RAG/model input |
| Parser (`css` / `xpath` / `find_all`) | all extraction, also on HTML fetched earlier |
| Redirect safety (`follow_redirects="safe"`) | refuses redirects into private IPs (belt; Hermus adds suspenders) |

**Scrapling's MCP server is NOT the integration path.** The canonical flow is
Agent → ToolGateway → WebGateway → Scrapling. Scrapling's MCP server can still
be attached as *an external adapter* through Hermus's generic MCP manager
(`data/mcp_servers.json`) for experimentation, but production web actions never
detour through it — that would bypass permission gating, security checks and
telemetry.

---

## 2. Architecture

```
Agent (LLM decision)
  ↓
ToolGateway                core/tools/gateway.py — permission gate + event envelope
  ↓
web_* tools                tools/web_acquisition.py — schema validation, trust labels
  ↓
WebGateway                 core/web/gateway.py — THE one web facade (singleton)
  ├─ Security              core/web/security.py — SSRF/scheme/domain policy, BEFORE any request
  ├─ Page cache            core/cache.py LRUCache (bounded, TTL; successes only)
  ├─ Sessions              core/web/sessions.py — domain-pinned, in-memory cookie jars
  ↓
Web Strategy Router        core/web/router.py — cheapest-sufficient-first + escalation
  ↓
ScraplingBackend           core/web/scrapling_backend.py — the ONLY scrapling importer
  ├─ Fetcher / DynamicFetcher / StealthyFetcher / Sessions
  ↓
RawFetch (internal)        scrapling Response never crosses this line
  ↓
Normalizer                 core/web/normalizer.py — WebResult, secrets-free, size-bounded
  ├─ Sanitizer             core/web/sanitize.py — prompt-injection hygiene + labels
  ↓
WebResult                  models.py — the only object the rest of Hermus sees
  ↓
Agent / ModelGateway / MemoryFacade
  + EventBus telemetry     redacted URLs, strategy, escalation trail, timings

Background crawls:
Agent → WebGateway.crawl_async → JobQueue ("web.crawl" kind, gateway/handlers.py)
      → CrawlWorker (core/web/crawl.py) → Scrapling sessions → progress via
        ctx.emit (run bus) + canonical EventBus
```

Enforced structurally by `tests/test_architecture_gates.py`:

* `scrapling` may only be imported inside `core/web/*` — nowhere else;
* web tools must call `get_web_gateway()`, never scrapling/raw HTTP;
* the gateway must own security + routing + canonical EventBus + canonical cache.

---

## 3. Strategy router (autonomous escalation)

| Level | Strategy | Used when |
| --- | --- | --- |
| 1 | `static` | Default. Page content is in the initial HTML. |
| 2 | `dynamic` | Static output is a JS shell / empty / challenge, or the caller requires JS. Needs browser binaries. |
| 3 | `stealth` | Only when enabled in config **and** lower levels genuinely failed. Never attempted blindly. Policy-controlled — **not** model-selectable (the `web_fetch`/`web_crawl` tool schemas expose only `auto`/`static`/`dynamic`). |
| 4 | crawl | Many URLs / recursive traversal → background job on the canonical JobQueue. Uses the SAME router per page (see §3a). |

### 3a. Crawl strategy escalation

A crawl runs each page through the **same** strategy router as single-page
acquisition instead of forcing static:

* `strategy="auto"` (default, from `HERMUS_WEB_STRATEGY`) — static first,
  escalate a discovered JS-heavy page to `dynamic`/`stealth` **where policy and
  capabilities permit**. Lightweight pages stay lightweight;
* `strategy="static"` — every page stays on fast HTTP (no browser);
* `strategy="dynamic"` — request a real browser per page where permitted;
* `max_dynamic_pages` (crawl-level, default 0 = unlimited within `max_pages`)
  caps how many pages may escalate to a browser strategy, so a JS-heavy site
  cannot spin up unbounded Chromium instances. The budget is **atomic**: a
  slot is reserved under a lock *before* a browser-capable fetch starts, so
  concurrent crawl workers can never exceed the cap (an `auto` page that ends
  up static returns its unused slot);
* `allow_fallback=False` strictly disables strategy escalation everywhere —
  including `strategy="auto"`, which then runs only its first (cheapest
  permitted) strategy;
* the crawl wall clock is a hard scheduling deadline: no new page fetch starts
  after it, politeness waits are bounded by it, in-flight fetches are waited
  on only briefly past it (then abandoned as daemon threads — never killed),
  and the summary reports `status="timeout"` / `timed_out=true` honestly;
* the configured per-domain politeness delay
  (`HERMUS_WEB_CRAWL_DELAY_MS` → `web_crawl_per_domain_delay_ms`) is threaded
  Config → WebGateway → `plan_crawl` → `CrawlPlan` → `CrawlWorker` and is
  actually honored between same-host hits (bounded global concurrency preserved).

Escalation rules:

* attempts are recorded on the result (`WebResult.attempts`: strategy, status,
  latency, failure class, escalation reason) — full observability of the path;
* a failing strategy is **never** blindly retried (transport-level retries are
  Scrapling's own bounded per-request retries);
* security refusals abort the whole plan (never escalated around);
* an oversized response (`SIZE_LIMIT`) also aborts the plan — a heavier strategy
  never shrinks a body, so escalation would be pointless;
* content-sufficiency heuristic — **large HTML ≠ JavaScript-required.** A page is
  only treated as a JS shell when there is *little meaningful extracted text*
  AND a corroborating signal: an explicit empty mount node
  (`<div id="root"></div>`), a "enable JavaScript" `<noscript>`, a hydration
  marker (`__NEXT_DATA__`, `window.__NUXT__`, `data-reactroot`, …), or
  script-dominated HTML (scripts > 60 % of bytes with no semantic body). A large
  text-rich article — even 100 KB with analytics scripts — stays **static**;
* challenge markers (Cloudflare/CAPTCHA) classify the failure as `bot_challenge`;
* on Android/Termux, browser strategies are restricted by default
  (`HERMUS_WEB_TERMUX_RESTRICT=1`) — untested capability is never claimed.

---

## 4. Capabilities matrix

| Capability | Status | How to enable | Verified |
| --- | --- | --- | --- |
| Fast HTTP fetch (browser TLS) | `core.web` static strategy | `pip install "scrapling[fetchers]"` | ✅ real live fetch (pypi.org) + loopback tests |
| Targeted CSS/XPath extraction | `web_extract` tool | same | ✅ real extraction tests |
| Adaptive extraction | `web_extract` with `adaptive=true` | same | ✅ unit tests (mocked DOM relocation); **live site drift not verified** |
| Markdown output | `Response.markdown()` | `pip install "scrapling[ai]"` | ✅ degrade-honest tests; live markdown needs the extra |
| Dynamic (JS) fetching | `web_fetch` escalation / `strategy=dynamic` | `scrapling install` (Chromium) | ⚠️ capability detection tested; **browser fetch itself not verified in CI** (no browser binaries) |
| Stealth fetching | `strategy=stealth` | `HERMUS_WEB_STEALTH=1` + `scrapling install` | ⚠️ same — **unverified against live anti-bot sites** |
| XHR/API capture | `web_fetch` dynamic + `capture_xhr` | dynamic stack | ⚠️ bundling logic unit-tested with fakes; **not verified live** |
| Sessions (cookies) — **static** | `web_session` tool | static stack | ✅ **persistent**: a live `FetcherSession` client + cookie jar is reused across fetches (`static_session_persistence=available`); isolation/pinning/TTL tested; cookie values never exposed |
| Sessions (cookies) — **dynamic/stealth** | `web_session` tool | browser stack | ⚠️ **not persistent**: browser strategies use one-off fetchers per call, so cross-fetch browser contexts are **not** reused (`dynamic_session_persistence=unavailable`). No fake persistence is claimed. |
| Background crawling | `web_crawl` tool → JobQueue | any install | ✅ real loopback crawl + real JobQueue job test |
| Search + acquire | `web_search_and_extract` | ddgs + static | ✅ pipeline tests (search faked, fetch real) |
| Android/Termux | HTTP-only by default | — | ✅ detection tested; **no Android browser claim** |

Legend: ✅ verified by tests in this repo · ⚠️ implemented, honestly reported as
`not_verified`/`unavailable` by Doctor until a real run proves it.

---

## 5. Security model

**Every** outbound web action passes through `core/web/security.py` first:

1. scheme allowlist (`http`/`https` only — `file:`, `data:`, `ftp:` refused);
2. embedded URL credentials refused;
3. DNS resolution of the target checked against loopback / private / link-local
   / reserved / multicast ranges — SSRF refused before connecting;
4. `localhost`, `.local`, `.internal` hosts refused (relaxable only via
   `HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES=1` — tests/intranets only);
5. classic SSRF pivot ports refused (ssh, redis, postgres, …);
6. domain block list (wildcards) + optional allow list; block wins;
7. redirect safety (belt **and** suspenders): Scrapling's
   `follow_redirects="safe"` refuses redirects to internal IPs at the transport
   layer, **and** Hermus independently re-validates the final URL *and every
   redirect hop* through the **same** security battery as the requested URL
   (`WebSecurityPolicy.check_final_url` → `check`). This catches what the
   transport check does not: a redirect to a **blocked domain**, a **disallowed
   scheme**, embedded **credentials**, or an SSRF **pivot port**. A refused
   redirect returns a typed `WEB_SECURITY_BLOCKED` result — never a leaked
   exception. Tested: public→loopback (v4/v6), public→private, public→link-local
   metadata, public→blocked-domain, public→disallowed-scheme, and multi-hop;
8. response-size cap: **honest limitation** — Scrapling's HTTP fetcher
   (curl_cffi) buffers the full body before Hermus sees it, so there is no true
   streamed *pre-download* abort. The cap (`web_max_response_bytes`) is therefore
   enforced at the **earliest safe point** — immediately in the backend after
   acquisition, *before* the body is retained, parsed, normalized, cached or
   sent to memory (raising `ResponseTooLargeError`/`SIZE_LIMIT`). It is honestly
   a *reject-after-acquisition* cutoff, **not** *download-prevented*. Crawls also
   enforce an aggregate byte cap plus page/depth/concurrency/wall-clock ceilings,
   per-domain rate limiting and bounded concurrency;
9. secrets never cross the boundary: cookies, request headers, proxy settings
   are dropped in the normalizer; telemetry redacts URLs (no query strings).

Permissions (canonical `core/permissions.py`): single-page tools are
`NETWORK/ALLOW`; `web_crawl` and `web_session` are `NETWORK/ASK` (heavier or
stateful → human-aligned). Proxy configuration is a deployment concern
(config/env), never an agent-callable argument.

## 6. Prompt-injection defense

Webpage content is **untrusted data, never instructions** (spec §9, enforced by
`core/web/sanitize.py`):

* text is sanitized (control/zero-width chars, fake code fences, fake
  `<system>`/`[INST]` markers neutralized);
* injection attempts are *detected* and surfaced as warnings;
* anything model-facing carries `untrusted: true` plus an explicit rule block
  (`wrap_untrusted`) with the source URL;
* no scraped text can trigger tools, redefine policy, or reach the model as a
  directive — the framing is part of every result.

## 7. Configuration

All settings live in `core/config.py` (env `HERMUS_WEB_*`), documented with
defaults in `.env.example` and raised ceilings in `.env.limits.example`.
Highlights: `HERMUS_WEB_ENABLED`, `HERMUS_WEB_STRATEGY`,
`HERMUS_WEB_DYNAMIC`, `HERMUS_WEB_STEALTH`, `HERMUS_WEB_STEALTH_CF`,
`HERMUS_WEB_TERMUX_RESTRICT`, `HERMUS_WEB_TIMEOUT`,
`HERMUS_WEB_BROWSER_TIMEOUT`, `HERMUS_WEB_MAX_RESPONSE_BYTES`,
`HERMUS_WEB_MAX_CONTENT_CHARS`, `HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES`,
`HERMUS_WEB_ALLOWED_DOMAINS`, `HERMUS_WEB_BLOCKED_DOMAINS`,
`HERMUS_WEB_CRAWL_*` (including `HERMUS_WEB_CRAWL_DELAY_MS`, the per-domain
politeness delay now propagated all the way to the crawl worker),
`HERMUS_WEB_SESSION_*`, `HERMUS_WEB_CACHE*`.

## 8. Installation

```bash
# Lightweight core (no Scrapling): everything imports; web tools return
# typed "not installed" results. Nothing breaks.
pip install -r requirements.txt           # scrapling is an optional group

# Web acquisition:
pip install "scrapling[fetchers]"         # HTTP fetchers + parser extras
scrapling install                          # optional: Chromium for dynamic/stealth
```

`setup.sh` performs both steps non-fatally (step 6b). Python ≥ 3.10 required by
Scrapling (Hermus targets 3.10+ anyway). Scrapling is pinned
`>=0.3.10,<0.5` (verified against 0.4.15, BSD-3-Clause — see
`THIRD_PARTY_NOTICES.md`).

## 9. Doctor / diagnostics

`hermus doctor` (via `core/diagnostics.py`) reports, at `recommended` level:
`web_parser`, `web_static`, `web_dynamic`, `web_stealth`, `web_markdown`,
`web_config` (+ `web_termux` on Android). Statuses distinguish
**available / unavailable / not_installed / not_verified** — "importable" is
never conflated with "working": browser binaries are checked on disk, and a
strategy only reads *available* after one real fetch succeeded in-process
(`capabilities.mark_verified`). The capability probe additionally reports
`static_session_persistence` (available with the static stack) and
`dynamic_session_persistence` (always `unavailable` — see §4). The doctor also
raises findings for security misconfigurations (e.g.
`HERMUS_WEB_ALLOW_PRIVATE_ADDRESSES=1` is a HIGH finding).

## 10. Android / Termux

* `capabilities.is_termux()` detects the environment.
* `HERMUS_WEB_TERMUX_RESTRICT=1` (default) keeps dynamic/stealth out of plans;
  the fast HTTP path works.
* Doctor says so explicitly. Hermus makes **no claim** of Android browser
  support until you verify it and set `HERMUS_WEB_TERMUX_RESTRICT=0`.

## 11. Testing & honest verification labels

* `tests/test_web_gateway.py` — security gates, normalization, caching, telemetry (real loopback fetches).
* `tests/test_web_router.py` — planning + escalation (faked backend; logic only).
* `tests/test_web_crawl.py` — bounded crawl, cancellation, limits (real loopback + **real JobQueue job**).
* `tests/test_web_sessions.py` — session isolation, TTL, secrecy.
* `tests/test_web_injection.py` — injection fixtures through the real path.
* `tests/test_web_capability.py` — the four honest states, Termux, verification,
  static/dynamic session-persistence reporting.
* `tests/test_web_tools.py` — registry/permission/tool wiring.
* `tests/test_web_redirect_security.py` — post-redirect final-URL re-validation
  (loopback v4/v6, private, link-local, blocked domain, disallowed scheme,
  multi-hop) and response-size enforcement (backend cutoff + router abort).
* `tests/test_web_heuristic.py` — JS-shell heuristic cases A–D (large static
  stays static; SPA shell escalates; static-with-scripts stays static; dynamic
  content escalates).
* `tests/test_web_crawl_strategy.py` — crawl uses the intelligent router for
  `auto`, stays static for `static`, honors `max_dynamic_pages` (including
  atomically under real thread concurrency), and actually propagates/honors
  the configured per-domain delay.
* `tests/test_web_crawl_timeout.py` — hard wall-clock deadline: no new work
  after the deadline, bounded politeness waits, bounded joins with honest
  abandonment reporting, and accurate `timeout` status.
* `tests/test_web_live.py` — **REAL LIVE** internet fetch (pypi.org; skipped
  without egress) and **REAL LOCAL** full-stack fetch/extract/crawl.
* Mocked tests prove routing/wiring logic only — they are never cited as proof
  that browser or anti-bot fetching works.

## 12. Troubleshooting

| Symptom | Meaning / fix |
| --- | --- |
| `WEB_NO_STRATEGY` / `not_installed` | Scrapling missing: `pip install "scrapling[fetchers]"` |
| `WEB_STRATEGY_UNAVAILABLE` on dynamic/stealth | browser binaries missing: `scrapling install` (or `playwright install chromium`) |
| `markdown extraction unavailable` warning | optional extra missing: `pip install "scrapling[ai]"` (text still works) |
| `WEB_SECURITY_BLOCKED` | target hit the SSRF/domain policy — check `HERMUS_WEB_BLOCKED_DOMAINS`, private-address setting |
| dynamic fetch slow | first run launches Chromium; consider `HERMUS_WEB_BROWSER_TIMEOUT` |
| crawl job stuck | `hermus jobs list` / dashboard; jobs are cancellable; wall-clock cap applies |

## 13. Licensing

Scrapling is BSD-3-Clause; its runtime dependencies under `scrapling[fetchers]`
(curl_cffi, lxml, orjson, w3lib, cssselect, tld, playwright, markdownify) are
Apache/BSD/MIT-family permissive licenses. No Scrapling source is vendored —
it is used purely as a pip dependency. See `THIRD_PARTY_NOTICES.md`.
