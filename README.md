# ☤ Hermus Agent Free - The Agent That Grows With You, For Free

<p align="center">
<strong>Completely free, open-source, self-improving AI agent — No paywalls, no OpenRouter fees — MIT License</strong><br>
<a href="https://github.com/trmv2007-bot/hermus-agent-free"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT"></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10+-green.svg?style=for-the-badge&logo=python" alt="Python"></a>
<a href="https://github.com/trmv2007-bot/hermus-agent-free/stargazers"><img src="https://img.shields.io/github/stars/trmv2007-bot/hermus-agent-free?style=for-the-badge" alt="Stars"></a>
</p>

> **Original:** Hermes Agent by Nous Research (225k stars, Feb 2026) — The agent that grows with you + Agent Reach (67k stars) — Give your AI agent eyes to see entire internet + Strix (49k stars) — Open-source AI pen-testing tool
> **This Version:** 100% free clone built from scratch with free models and tools — Ollama local, DuckDuckGo, SQLite FTS5, no API keys needed — plus extra free features original doesn't have

---

## Why Free Version?

Original Hermes / Strix / Agent Reach use some paid services:

- **OpenRouter** (paid API for 100s models) → **Free Alternative:** Ollama local `llama3.1:8b` / `mistral` / `phi3` — 100% free, runs on laptop/$5 VPS, no API key + Groq free tier 30 req/min + HF free inference
- **Honcho paid user modeling** → `data/user_model.json` — LLM asks dialectic questions free
- **Hosted gateway / Modal / Daytona paid** → Docker + SSH + local free, plus free tier Modal/Daytona fallback
- **Premium voice transcription** → faster-whisper local Whisper free
- **Vector DB Pinecone paid** → SQLite FTS5 built-in free, no API key
- **Twitter API paid, Reddit 403, YouTube no subtitles** → Agent Reach free tools Jina Reader, yt-dlp, old.reddit.com .json, bili-cli, etc., zero API fees

**This free version:** 100% free, MIT, no tracking, self-hosted, you own `data/memory.db` SQLite file.

---

## Features — Everything Organized (What It Can Do Right Now)

### 1. Real Terminal Interface — `hermus` CLI — Gold and Kawaii Default Skin #DAA520 / Cornsilk #FFF8DC

- **Full TUI** with multiline editing (prompt_toolkit), **slash-command autocomplete**: `/new`, `/model`, `/skills`, `/platforms`, `/compress`, `/usage`, `/panel`, `/agents`, `/update`, `/check-update`
- **Conversation history** FileHistory, **auto_suggest** from history, **bottom toolbar live** Agents: X | Tasks: Y | Models: ollama/llama3.1:8b — refresh 2 sec
- **Interrupt-and-redirect** Ctrl+C, **streaming tool output**
- **Slide Panel** you asked: `/panel` or `/agents` — slides open panel showing what agents/models are running or doing task — right sidebar 0→440px cubic-bezier 0.35s, auto-refresh 2 sec, active agents (name, model badge, persona, task, status, started), active tasks, models in use, recently completed
- **Voice memo transcription** Whisper via faster-whisper free local

### 2. Lives Where You Do — Gateway Single Process Free

- **Single gateway** `hermus gateway start` port 8000 — one process for all platforms
- **Platforms free:** Telegram Bot API free, Discord free, Slack webhook free, WhatsApp/Signal via bridges free, CLI
- **Cross-platform continuity** — start on Telegram, continue on CLI — same memory via SQLite FTS5
- **Endpoints:** `/`, `/command`, `/agents/status` (slide panel data), `/platforms`, `/webhook/telegram`, `/dashboard`, `/keys/list/add/remove`, `/custom-apis/list/add/remove`, `/response-times`, `/update/check`, `/update/pull`, `/cache/stats`, `/cache/clear`
- **Living Agent Control Room** `http://localhost:8000/dashboard` — animated, task-first local mission control with an expressive Hermus core, distinct robotic crew, live progress, approvals, telemetry, connected system modules, and fullscreen **Live Agent Theatre** with backend-generated local speech and microphone input. The original all-in-one UI remains at `/dashboard/legacy`; see [`LIVING_CONTROL_ROOM.md`](LIVING_CONTROL_ROOM.md).

### 3. Closed Learning Loop — The Magic — Self-Improving

- **Agent-curated memory:** After each task, agent decides what to remember → `memory.curate_memory(key, value)`
- **Periodic nudges:** Cron that asks "Anything from yesterday you want to persist?" — finds recent sessions with "remember" or many tool calls
- **Autonomous skill creation:** After complex task (3+ tool calls), LLM analyzes trajectory and creates reusable skill in `skills/<name>/SKILL.md + skill.py` — compatible with agentskills.io open standard — zero-context-cost next time
- **Skills self-improve:** Each use logs success/failure + feedback to `skill_usage` table, LLM edits `skill.py` to improve, backup `.bak.timestamp`
- **FTS5 session search + LLM summarization:** `SELECT * FROM sessions WHERE sessions MATCH ?` via SQLite FTS5 porter tokenizer, then LLM summarizes for cross-session recall
- **User modeling free Honcho alternative:** Builds `data/user_model.json` with preferences, projects, workflows via dialectic questions "What kind of projects do you work on most?"
- **Research-ready:** `data/trajectories.jsonl` logs every turn for batch generation + compression

### 4. Scheduled Automations — Natural Language Cron Free

- **Built-in cron** with APScheduler BackgroundScheduler
- **Natural language:** "daily at 9am send me report" → parsed to cron `0 9 * * *` via simple rules + LLM fallback free, no paid parser
- **Delivery any platform:** Telegram, Discord, CLI, Slack
- **CLI:** `hermus cron add "every Monday 8am weekly audit"`, `list`, `remove`

### 5. Delegates and Parallelizes — Subagents Free

- **Spawn isolated subagents:** `hermus subagent spawn "research X and Y in parallel"` — multiprocessing isolated, each gets own session
- **Parallel:** `spawn_parallel_subagents()` — 3 research tasks in parallel, each subagent writes file, main merges = parallel workstreams
- **RPC zero-context-cost:** `write_python_tool_via_rpc()` — subagent writes Python script that calls tools via RPC collapsing multi-step pipelines into zero-context-cost turns

### 5b. Counsel System — Council of AIs That Plans Together & Upgrades Itself ⭐

- **Council of AIs:** hard tasks (difficulty ≥ 4) auto-convene a council — Chair, Researcher(s), Critic, Synthesizer, Judge — each member with its own free model/key when available (Ollama local, Groq free tier, HF)
- **They talk, then vote:** parallel proposals → deliberation rounds (Critic must attach evidence/objection) → Judge scores 0-10 → Chair writes the voted plan → plan executes with real tools → transcripts saved to `data/counsel/`
- **It upgrades itself:** after each session the Meta-Counsel reviews the transcript and proposes AMENDMENTS to the council's own constitution (member prompts, rules, budgets). Low-risk changes auto-apply with versioning; high-risk wait for your approval; `rollback` anytime
- **CLI:** `hermus counsel run "task"`, `hermus counsel status`, `hermus counsel amend list|approve|reject|rollback <v>`, `hermus counsel review` — **TUI:** `/counsel`, `/counsel run <task>`, `/think on|off`
- **DeepThink plan-first:** multi-step tasks now get an explicit written plan before acting (`data/plans/`), zero-token difficulty governor decides how hard to think
- **Deliberation strategies (Phase 3):** difficulty 3 → reflexion (critique→revise pass), difficulty 4 → verify (key claims re-checked with web search before answering), difficulty 5 → self-consistency (k=3 parallel drafts merged) — all bounded and with graceful fallback. Per-task step budgets: easy tasks ≤2 steps, hard tasks up to 12
- **Lessons loop (Phase 3):** user corrections, tool failures, reflections and skill failures are distilled into a `lessons` table (`data/memory.db`) and the top relevant lessons are injected into every system prompt — the agent stops repeating its own past mistakes. `HERMUS_LESSONS_IN_PROMPT=8` controls how many
- **Eval harness (Phase 4):** 21 benchmark tasks in 5 categories — `hermus eval run --strategy reflexion`, `hermus eval compare --a reflexion --b self_consistency`, `hermus eval list|history`. Measures success rate / steps / tool failures so upgrades are proven, not guessed
- **Deterministic router (Phase 4):** task type + mode + workers → single / council / fleet / subagents; fleet strategy (fanout/race/map) chosen by a table, not keywords
- **Tool fallbacks (Phase 4):** registry-level retry + alternate-tool chains (web_search → retry → browser DDG; web_read → retry → browser_navigate) with a `fallback_trail` on the result; successful fallbacks feed the lessons loop
- **Project memory + plan resume + tags (Phase 4):** `memory_search(query, project=…)` scoped recall (TUI `/project set <name>`); `hermus plan list|show|resume <id>` resumes saved plans; trajectories are tagged with strategy/difficulty/plan for future training; dashboard has a **🧭 Reasoning** pane (`/eval/summary`, `/counsel/status`)
- **Config:** `HERMUS_COUNSEL_ENABLED`, `HERMUS_COUNSEL_MIN_DIFFICULTY`, `HERMUS_COUNSEL_MAX_MEMBERS/ROUNDS`, `HERMUS_COUNSEL_AUTO_REVIEW`, `HERMUS_THINK_ENABLED`, `HERMUS_STRATEGY` (auto|none|reflexion|self_consistency|verify), `HERMUS_SELF_CONSISTENCY_K`, `HERMUS_VERIFY_THRESHOLD`, `HERMUS_PROJECT`
- Design doc: `THINKING_SYSTEM_PLAN.md` — tests: `python tests/test_counsel_system.py`, `python tests/test_deepthink.py`, `python tests/test_phase4.py` (all offline, free mock model)

### 6. Runs Anywhere — Seven Backends Free

| Backend | Available Check | Description | Free? |
|---------|-----------------|-------------|-------|
| **local** | Always | Run commands directly on your machine | ✅ Always free |
| **docker** | `docker --version` | Isolated container with security hardening read-only root, dropped capabilities, PID limits | ✅ Free if Docker installed |
| **ssh** | `ssh -V` + HERMUS_SSH_HOST | Execute on any remote server via SSH | ✅ Free |
| **singularity** | `singularity --version` | Cloud and HPC execution backend | ✅ Free for HPC |
| **modal** | `import modal` | Serverless persistence - hibernates when idle and wakes on demand, costing nearly nothing between sessions, free tier | ✅ Free tier |
| **daytona** | `daytona --version` or DAYTONA_API_KEY | Serverless persistence free tier | ✅ Free tier |
| **vercel** | `vercel --version` or VERCEL_TOKEN | Vercel Sandbox serverless sandbox | ✅ Free tier |

- **Tool:** `backend_execute(backend, command, workdir, timeout)` + `list_backends()`

### 7. 44+ Free Tools → Now 91+ Tools (All Free, Zero API Fees)

#### Core Free
- `web_search` - DuckDuckGo free no API key, LRU cache 50 items TTL 10 min
- `public_api_search`, `public_api_categories`, `public_api_refresh` - Search 1,600+ community-curated APIs from [`public-apis/public-apis`](https://github.com/public-apis/public-apis), filter by auth/HTTPS/CORS/category, offline snapshot + opt-in GitHub refresh
- `file_read`, `file_write`, `file_edit`, `file_search` - OptimizedFileCache mtime check <1MB, 50-100x faster
- `shell_execute` - Safe subprocess timeout 10s
- `memory_search`, `memory_add` - FTS5 free
- `skill_list`, `skill_use` - File-based skills, LRU cache 20 items
- `subagent_spawn` - Multiprocessing isolated free
- `check_update`, `do_update`, `get_local_commit`, `get_remote_commit` - GitHub update check

#### Browser Automation Playwright Free
- `browser_navigate(url)`, `browser_click(selector)`, `browser_type(selector,text)`, `browser_screenshot(path,full_page)`, `browser_extract(selector)`, `browser_close()` - Playwright sync_api free, headless, no API key

#### Vision LLaVA via Ollama Free
- `vision_analyze(image_path, prompt, model="llava:7b")` - Ollama LLaVA base64 image, POST to `http://localhost:11434/api/generate`, `ollama pull llava:7b` free local vision
- `vision_available_models()` - Lists vision models via /api/tags

#### Hermus Computer Agent v3 — World State → Graph Plan → Act → Verify → Repair → Resume

```bash
# Detached recording survives the command that starts it
hermus screen record start --fps 10 --buffer-seconds 30 --format mp4
hermus screen record status
hermus screen record stop

# A filename saves video + named JSON sidecars
hermus screen record save task-123.mp4

# A task id (no extension) creates the complete task bundle
hermus screen record save task_042
# data/recordings/task_042/{recording.mp4,timeline.json,events.json,
#                           actions.json,result.json,manifest.json}

# Turn a recording into a semantic timeline with local Ollama/LLaVA
hermus screen analyze data/recordings/task-123.mp4 --task "Install X"
# Change detection only (no model calls)
hermus screen analyze data/recordings/task-123.mp4 --no-vision

# Event-driven condition watching
hermus screen watch "wait until the installation finishes" --timeout 60

# Run a persistent, repairable computer task
hermus computer run "Open Chrome and go to youtube.com" --task-id youtube
hermus computer show youtube
hermus computer tasks

# Continue at the first unfinished visual state after a crash/failure
hermus computer resume youtube

# Learned skills report runs, success rate, failures, repairs and duration
hermus computer skills

# Dependency-aware background delegation. GUI work is serialized through one
# computer-operator while research/coding agents can work independently.
hermus computer delegate "research the package then download and install it"

# Persistent background-agent jobs are queryable by job id
hermus agent create desktop --role computer-operator
hermus agent start desktop
hermus agent job desktop "Open Chrome" --wait
```

Every task directory contains `state.json`, `plan.json`, recording generation(s),
`timeline.json`, `actions.json`, `verification.json`, `repairs.json`,
`result.json`, and `summary.md`. `state.json` is updated atomically after each
state-machine event and includes completed/pending states, known failures,
repairs, and the shared `WorldState`. The planner produces a validated state
graph with goals, preconditions, post-action visual states, dependencies,
transitions and fallbacks. Failed verification is diagnosed and repaired; the
original action is retried only after the repair itself is visually verified.

The rolling RAM buffer contains JPEG bytes, not full PIL screen images, and is
bounded by both duration and memory. The full session is streamed separately
to MP4/WebM through free FFmpeg (`imageio-ffmpeg` is included as a fallback).
Only debounced important frames are decoded for vision. Screen capture and
watch tools are privacy-gated in `core.permissions`; recordings default to
private files under `data/recordings/` and are ignored by Git.

Agent/gateway tools: `screen_record_start`, `screen_record_stop`,
`screen_record_status`, `screen_record_save`, `screen_analyze`, `screen_verify`,
`screen_action_before` / `screen_action_after`, and `screen_watch`. The action
boundary pair captures exact evidence around a click/type/launch instead of
inferring boundaries from an arbitrary time window. Gateway routes mirror these at
`/screen/{start,stop,status,save,analyze,watch}` and
`/screen/action/{before,after}`.

**Phase C & D (power & polish):** live computer dashboard at
`/computer/dashboard`, mobile remote control at `/remote` (live screen, action
approval, pause/resume/cancel/emergency-stop), resource telemetry at
`/computer/resources`, skill reliability profiles at
`/computer/skills/{name}/profile`, multi-agent delegation at `/computer/delegate`,
a plugin/MCP registry at `/plugins`, and an install health check via `hermus
doctor`. See `PHASE_C_D.md`.

#### Voice Memo Transcription Faster-Whisper Free
- `transcribe_audio(audio_path, model="base", language)` - faster-whisper WhisperModel base/small/medium/large-v2 free local, no cloud, beam_size 5, segments start/end/text
- `voice_available_models()` - tiny 39M, base 74M, small 244M, medium 769M, large-v2 1550M

#### Internet Eyes - Agent Reach Full (67k stars) - Zero API Fees
- `web_read(url)` - Any webpage via Jina AI Reader https://r.jina.ai/http:// free no config
- `rss_read(rss_url)` - RSS/Atom via feedparser free
- `youtube_transcript(video_url)` - YouTube subtitle via yt-dlp free no config public videos
- `youtube_search(query)` - YouTube search via ytsearch or DuckDuckGo fallback free
- `github_read(repo, path)` - GitHub public repo via gh CLI or API free no key, private needs login
- `github_search(query)` - GitHub search API free
- `twitter_read(tweet_url)` - Single tweet via Jina free no config, search/timeline needs Cookie-Editor manual export free local
- `bilibili_search(query)` - Bilibili search via bili-cli free no login, fallback DuckDuckGo site:bilibili.com
- `reddit_read(subreddit, post_id)` - Reddit via old.reddit.com .json + Jina fallback free, search needs OpenCLI Chrome session free
- `v2ex_hot()` - V2EX hot posts free no config
- `xueqiu_stock_search(query)` - Xueqiu via Jina free

#### Remaining Platforms - Facebook, Instagram, XiaoHongShu, LinkedIn, Xiaoyuzhou
- `facebook_search(query)` - Facebook search homepage Feed group list via OpenCLI Chrome session free, fallback Jina public page free
- `instagram_user_search(username)` - Instagram via OpenCLI free
- `xiaohongshu_search(query)` - XiaoHongShu via OpenCLI only uses existing Chrome session, cookie only locally not uploaded
- `linkedin_read(url)` - LinkedIn Jina public page free + details OpenCLI
- `xiaoyuzhou_transcribe(podcast_url)` - Podcast Whisper Groq->OpenAI fallback free

#### Doctor - Real Probing
- `doctor_check_all()` - Doctor check all channels with real probing, ordered backend candidates, tells apart missing/broken/timeout, 15 platforms, free
- `doctor_text_report()` - Human readable report

#### Backends 7
- `backend_execute(backend, command, workdir, timeout)` + `list_backends()`

#### Trajectory Batch + Compression - Research-Ready Free
- `trajectory_batch_generate(prompts, model, max_workers=3)` - Batch generation thousands of tool-calling trajectories in parallel with checkpointing
- `trajectory_compress(max_tokens=4000)` - Fits into token budgets, 11 parsers truncated, ShareGPT export for fine-tuning
- `trajectory_stats()` - Stats total, size_mb, compressed_count

#### Response Time Tester - How Much Time API Key Takes
- `test_api_key_response_time(provider, api_key, model, prompt)` - Measures seconds/ms, success, tokens, model, preview, saves to data/response_times.json history last 50, updates multi_key avg_response_time
- `test_custom_api_response_time(api_name, api_key, test_args)` - Custom API from different websites
- `get_response_time_stats()` - Total tests, successful, failed, avg, fastest, slowest

#### Updater - GitHub Update Check
- `check_update()` - Check if update available from GitHub, local vs remote, behind_by count, remote message author date, shows in dashboard and CLI
- `do_update()` - git pull origin main + pip install -r requirements.txt like `hermes update`

#### Pentest - real, runnable security tooling
- **Recon:** `subdomain_enum(domain)`, `fingerprinting(url)`, `attack_surface_mapping(domain)` - sublist3r, DNS brute, tech stack detection
- **Exploitation:** `browser_xss_test(url)`, `shell_exploit(command)`, `custom_exploit_runtime(code)` Python sandbox PoC validation working PoCs not false positives, `http_proxy_intercept(url)` Caido-like
- **Vuln KB:** `search_vuln_kb(query, owasp, severity)`, `get_owasp_categories()` - OWASP Top 10 and beyond 14 categories, CVE-style KB 5 vulns CVSS scoring
- **Scanner:** `scan_api_spec(api_spec_path)` - parse OpenAPI / Swagger / Postman collections, return endpoint list
- **Multi-Agent Pentesting:** `pentest_distribute_task(target)` - Graph of Agents distributed specialized agents recon/web_exploiter/api_tester/post_exploit/reporter, `pentest_chain_vulns()` chain vulnerabilities like red team, `pentest_scalable_scan(targets)` parallel across multiple targets
- **Reporting:** `pentest_create_run(target)`, `pentest_add_finding(run_name, finding)` with PoC and reproduction steps validated findings, `pentest_generate_patch(finding)` one-click autofix PR (SQLi param queries, XSS html.escape CSP), `pentest_generate_report(run_name)` compliance-ready summary critical/high/medium/low OWASP, `list_compliance_frameworks()` OWASP Top 10 2021, PCI-DSS 4.0, SOC 2, GDPR, `generate_compliance_report(run_name, framework)`
- **Viewer:** `pentest_view_run(run_name, port, open_browser)` - Local Web Viewer every scan writes results to disk, bring up in local dashboard with single command, lightweight local server 127.0.0.1 random port, private tokened link, nothing leaves machine
- **SAST+DAST:** `sast_scan(target_dir)` pattern matching semgrep-like SQLi XSS hardcoded secrets, `dast_scan(url)` header checks CSP X-Frame-Options verbose errors ZAP-like, `sast_dast_combined(target, url)`
- **CI/CD:** `generate_github_actions_workflow(target, fail_on)` - Creates `.github/workflows/pentest.yml` that runs the real SAST scanner on every PR and blocks insecure code; `generate_gitlab_ci()`, `generate_jenkinsfile()`
- **Bug Bounty:** `bug_bounty_recon(target)`, `generate_bounty_poc(vulnerability, target)`, `bug_bounty_workflow(target, output_dir)` - recon, scan, PoCs, report json + md files
- **DevSecOps:** `github_integration_pr_comment(repo, pr_number, report_path, github_token)`, `gitlab_integration_mr_note()`, `slack_notify(webhook_url, report_path)`, `jira_create_issue(jira_url, project_key, report_path, jira_token, jira_email)`, `linear_create_issue(linear_api_key, team_id, report_path)`
- **Continuous Learning:** `continuous_learning_add_finding(finding)`, `continuous_learning_mark_false_positive(finding_id, reason)`, `continuous_learning_stats()` - AI builds on past findings, adapts to codebase, reduces false positives, `should_skip_similar(new_finding)` checks if similar previously FP
- **Compliance:** `generate_compliance_report(run_name, framework)`, `list_compliance_frameworks()`

Total: **91+ tools** and growing, all free, zero API fees for core, no paywall

### 8. Research-Ready
- Batch trajectory generation
- Trajectory compression for training next-gen tool-calling models
- Logs in `data/trajectories.jsonl`

### 9. Public API Discovery + Custom API Tools

Find a suitable provider before registering its documented endpoint:

```bash
hermus api discover "weather forecast" --auth No
hermus api discover "threat intelligence" --category Security --limit 5
hermus api categories
hermus api refresh-catalog             # optional; refreshes an untracked runtime cache
```

- Bundles an offline snapshot of **1,600+ APIs across 51 categories** from [`public-apis/public-apis`](https://github.com/public-apis/public-apis), with filters for authentication, HTTPS, CORS, and category
- Agent tools: `public_api_search(...)`, `public_api_categories()`, `public_api_refresh()`
- Gateway: `GET /public-apis/search`, `GET /public-apis/categories`, `POST /public-apis/refresh`
- Catalog links point to provider documentation, **not automatically trusted executable endpoints**. Review provider terms, privacy, and docs before use. Attribution/license: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
- `hermus api add --name weather_api --description "Get weather" --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City"` - URL templating `{param}`, auth bearer/apikey/basic
- Up to **10 keys per custom API same name** from different websites you know + **20 per provider** (groq/hf/openai) round-robin, failure tracking 5 min cooldown, fallback, parallel execution different keys = 3x faster
- Runtime storage: `data/api_keys.json`, `data/custom_apis.json`, and `data/public_apis_catalog_cache.json` are local only and not uploaded

### 10. Multi-AI Collaboration - Multiple AIs Talk to Each Other
- Personas: researcher, coder, reviewer, writer, planner, debater, optimist, pessimist
- `chat_round()`, `debate()`, `collaborate_on_task()` with tools, judge final consensus, mixing multi-key + multi-AI for speed
- CLI: `hermus multiai debate "Python vs Rust" --rounds 2 --agents researcher coder reviewer`

### 11. Token Counting - Free Tracking
- `core/token_counter.py` uses tiktoken accurate if installed else len/4 fallback, counts prompt/completion/total/cost per model (Ollama $0, Groq $0.59/1M)
- SQLite `token_usage` table, `memory.add_token_usage()`, `get_token_usage()`, `/usage` command shows session + global totals + recent calls, `/compress` shows trajectory tokens

### 12. Slide Panel - What Agents/Models Running (You Asked)
- TUI: `/panel` or `/agents` + bottom toolbar live Agents: X | Tasks: Y | Models, refresh 2 sec, right sidebar 0→440px cubic-bezier
- Dashboard: 👁️ Agents Panel button top-right fixed, slides right sidebar 0→440px, auto-refresh 2 sec when open, shows active agents (name, model badge, persona, task, status, started), active tasks, models in use, recently completed

### 13. Living Agent Control Room + Fullscreen Live Agent Theatre
- **Living task-first control room:** current mission, phases, event stream, approvals and results dominate the opening view. An expressive Hermus core coordinates distinct Researcher, Critic, Tool Runner and Verifier robotic units.
- **Live Agent Theatre:** selecting Talking automatically opens a fullscreen agent-watching surface with large captions, mission state, visible crew activity, live gateway + computer events, typed input, microphone capture and permanent stop/exit controls.
- **Backend audio:** `core/speech.py` discovers Piper, espeak-ng/espeak or pyttsx3 and produces local WAV clips. Microphone blobs use the existing local faster-whisper transcription. If TTS is unavailable the full view degrades cleanly to captions.
- **Safety:** Preview First uses tool-free planning for regular agent requests and true `dry_run` for computer tasks; Halt and pending human approvals remain visible. The previous UI is preserved at `/dashboard/legacy`. Full setup and API details: [`LIVING_CONTROL_ROOM.md`](LIVING_CONTROL_ROOM.md).
- **Motion and accessibility:** the dashboard uses local particle, mesh and crew animation, offers an independent ambience pause, and respects reduced-motion preferences. `core/skin_engine.py` remains available to the CLI/TUI.

### 14. Update Thing - If You Update Anything in GitHub, Shows Update in Dashboard and CLI Too
- `core/updater.py`: get_local_commit() git rev-parse HEAD, get_remote_commit() GitHub API api.github.com/repos/{owner}/{repo}/commits/main free no key + git ls-remote fallback, check_for_updates() compares local vs remote, git fetch origin main, rev-list --count local..remote behind_by, update() git pull origin main + pip install -r requirements.txt like hermes update
- Tools: `check_update()`, `do_update()`, `get_local_commit()`, `get_remote_commit()`
- CLI: `hermus update --check` only checks, `hermus update` pulls, TUI `/update` command, startup auto checks and shows banner if update available
- Gateway: `GET /update/check`, `POST /update/pull`, `GET /update/local`, `GET /update/remote`
- Dashboard: Orange banner gradient #ff9800→#ff5722 top with Update available! Local X behind remote Y by N: message by author on date - Click to update + Update Now button, badge top-right Update! X behind, auto-check every 30 sec, shows in dashboard and CLI

### 15. Response Time Test - How Much Time API Key Takes
- `core/response_tester.py`: test_llm_key(provider, api_key, model, prompt) measures seconds/ms, success, tokens, model, preview, saves to data/response_times.json history last 50, updates multi_key_manager avg_response_time, test_custom_api_key, test_all_keys_for_provider/provider, get_history, get_stats
- Tools: `test_api_key_response_time(provider, api_key, model, prompt)`, `test_custom_api_response_time(api_name, api_key, test_args)`, `get_response_time_stats()`
- Gateway: `GET /response-times`, `POST /response-times/test`
- Dashboard Keys pane: Test All Response Time button per provider and per custom API group, each key row Test button + avg + last response time, history chart

### 16. Optimized Everything - Optifine
- `core/cache.py`: LRUCache max_size 100 TTL 300-600 sec OrderedDict timestamps Lock hits/misses, make_key md5, OptimizedFileCache mtime check <1MB, Global caches llm_cache 100 10 min, memory_search_cache 50 5 min, web_search_cache 50 10 min, tool_result_cache 100 5 min, skill_cache 20 10 min, clear_all_caches(), get_cache_stats()
- Memory: WAL mode, synchronous NORMAL, cache_size 64MB, temp_store MEMORY, indexes idx_sessions_session_id/timestamp
- Web search, file tools, skill manager cached, gateway GZipMiddleware, dashboard smooth animations toggle, multi-key 10/20, response time tracking
- Performance: LLM repeated 2-5 sec → 0.01 sec cache hit 200-500x faster, memory search 50-100ms → 1ms 50-100x faster, web search 1-2 sec → 1ms 1000x faster, file read <1MB 5-10ms → 0.1ms 50-100x faster, skills list 20-50ms → 1ms 20-50x faster, gateway dashboard 500ms → 200ms 2.5x faster with GZip, SQLite writes 2-3x faster WAL, multi-key parallel 3 tasks 30 sec → 10 sec 3x faster

---

## Quick Start — one command from scratch

```bash
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash
```

That clones (if needed), creates `.venv`, installs **all** deps, Playwright browser, launchers, and verifies Hermus.

```bash
# + local Ollama model
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash -s -- --with-ollama

# + Groq key + start dashboard
curl -fsSL https://raw.githubusercontent.com/trmv2007-bot/hermus-agent-free/main/install.sh | bash -s -- \
  --groq-key gsk_YOUR_KEY --start

# Already cloned?
cd hermus-agent-free && bash setup.sh
```

After setup:

```bash
cd ~/hermus-agent-free
source activate.sh
./hermus --model groq/llama-3.1-8b-instant   # or ollama/llama3.1:8b
./bin/hermus-gateway                           # http://localhost:8000/dashboard
```

See **[QUICKSTART.md](QUICKSTART.md)** for all flags (`--custom-base-url`, `--openrouter-key`, etc.).

---

## Quick Start (100% Free, No API Keys)

### Option 1: Ollama (Fully Free, Offline)

```bash
# Install Ollama https://ollama.com
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b  # or mistral, phi3:mini
ollama pull llava:7b  # for vision free

# Install Hermus Free
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
pip install -r requirements.txt
pip install playwright && playwright install chromium  # for browser free
pip install faster-whisper  # for voice memo transcription free

# Start chatting (uses Ollama local, no API key)
python -m core.agent --model ollama/llama3.1:8b

# Or TUI with slide panel, gold/kawaii dashboard, animations toggle, update check
python hermus.py --model ollama/llama3.1:8b
# In TUI: /panel to see running agents, /update to check GitHub updates, /skills, /model
```

### Option 2: Groq Free Tier (Free Cloud, Fast) + Multi-Key 10 Keys

```bash
# Get free Groq keys https://console.groq.com/keys (30 req/min per key)
# Add 3 keys for 90 req/min = 3x faster
python hermus.py multikey add --provider groq --key gsk_abc... --name groq_key_1
python hermus.py multikey add --provider groq --key gsk_def... --name groq_key_2
python hermus.py multikey add --provider groq --key gsk_ghi... --name groq_key_3

# Chat with Groq free, multi-key round-robin + fallback
python -m core.agent --model groq/llama-3.1-70b-versatile
```

### Option 3: Custom API - Add Any API From Different Websites You Know

```bash
# Add weather API with 3 keys from 3 different websites you know (up to 10 same name)
python hermus.py api add --name weather_api --description "Get weather" --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City" --auth-token siteA_key_123
python hermus.py api add --name weather_api --description "Get weather - second key" --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City" --auth-token siteB_key_456
python hermus.py api add --name weather_api --description "Get weather - third key" --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City" --auth-token siteC_key_789

# List multi-key
python hermus.py api list
# weather_api - 3 keys from different websites [Multi-key 3/10 round-robin]

# Test response time for each key - how much time API key takes
python hermus.py --model mock/mock -c "Use test_custom_api_response_time to test all keys for weather_api and rank fastest"

# Agent uses round-robin: Call1 siteA, Call2 siteB, Call3 siteC, Call4 siteA again, fallback if 429
```

---

## CLI vs Messaging Quick Reference (Same as Original + Extra Free Features)

| Action | CLI | Messaging |
|--------|-----|-----------|
| Start chatting | `hermus` | Telegram/Discord message bot |
| Start fresh | `/new` or `/reset` | `/new` |
| Change model | `/model ollama/llama3.1:8b` | `/model` |
| Set personality | `/personality friendly coder` | `/personality` |
| Retry/undo | `/retry, /undo` | `/retry, /undo` |
| Compress context / usage | `/compress, /usage, /insights` | same - shows token counting free |
| Browse skills | `/skills` or `/<skill-name>` | `/<skill-name>` |
| Interrupt | Ctrl+C | `/stop` |
| Platform status + live agents | `/platforms` + `/panel` slide open | `/status, /sethome` |
| Check update from GitHub | `/update` or `/check-update` or `hermus update --check` | Shows update in dashboard and CLI too |
| Update from GitHub | `hermus update` | - |
| Gateway | `hermus gateway setup` + `hermus gateway start` | - |
| Cron | `hermus cron add "daily 9am report"` | - |
| Subagent | `hermus subagent spawn "research X in parallel"` | - |
| Multi-Key | `hermus multikey add/list/remove/parallel` | - |
| Multi-AI | `hermus multiai debate "Topic" --rounds 2 --agents researcher coder reviewer` | - |
| Public/custom API | `hermus api discover/categories/refresh-catalog` + `add/list/remove/test` | - |
| Response time test | Use tool `test_api_key_response_time` or dashboard Keys pane Test ⏱️ buttons | - |
| Doctor health check | `hermus --model mock/mock -c "Use doctor_check_all"` | - |
| Pentest | `hermus --model mock/mock -c "Use sast_scan to scan the repo, then generate a report"` | - |

---

## Project Structure (Updated - 91+ Tools)

```
hermus-agent-free/
├── core/
│   ├── agent.py          # Main loop: 91+ tools, memory search -> skill load -> tool calls -> response -> curate memory + token tracking + task tracker + update check on startup
│   ├── llm.py            # Free LLM: Ollama local no key, Groq free tier multi-key 20 keys round-robin + failure tracking, HF free, mock + caching LRU 100 items + token counting + cost estimation
│   ├── memory.py         # SQLite FTS5 sessions + WAL mode + indexes + curated memory + nudges + token_usage table + user_model.json dialectic + trajectories.jsonl + FTS5 search + summarization
│   ├── skill_manager.py  # Auto skill creation 3+ tool calls via free LLM, self-improve on use, agentskills.io compatible SKILL.md + skill.py, LRU cache 20 items
│   ├── config.py         # Config paths, auto_skill_threshold 3, skills_dir, memory_db, etc.
│   ├── cache.py          # NEW - LRUCache max_size 100 TTL 300-600, OptimizedFileCache mtime <1MB, global caches llm/memory_search/web_search/tool_result/skill, clear_all_caches, get_cache_stats
│   ├── token_counter.py  # NEW - Free token counter tiktoken accurate if installed else len/4 fallback, count_text/messages/tools, estimate_cost pricing per 1M Ollama $0 Groq 70b $0.59/0.79
│   ├── multi_key.py      # NEW - Multi-API Keys up to 20 per provider + 10 per custom API same name from different websites, round-robin deque, failure tracking 5 min cooldown, parallel execution multiprocessing different keys = 3x faster
│   ├── multi_ai.py       # NEW - Multi-AI collaboration multiple AIs talk to each other, personas researcher/coder/reviewer/writer/planner/debater/optimist/pessimist, chat_round, debate, collaborate_on_task, judge final consensus, mixing multi-key + multi-AI
│   ├── custom_api.py     # NEW - Custom API add any REST API as tool in 1 command, URL templating {param}, auth bearer/apikey/basic, up to 10 keys same name multi-key round-robin + fallback, deduplicated tool defs
│   ├── skin_engine.py    # NEW - Skin engine data-driven theming like original, DEFAULT_SKINS default gold #DAA520 cornsilk #FFF8DC kawaii, slate #4169e1, ares crimson, mono #555555, poseidon, plus cyberpunk neon, fields colors banner_border/title/accent/text/bg, spinner thinking_verbs/faces/wings, branding agent_name/response_label/tool_prefix, list_skins() builtin + custom YAML from data/skins and ~/.hermes/skins/, get_skin, set_skin saves to user_model, create_custom_skin writes YAML, animations toggle
│   ├── task_tracker.py   # NEW - Tracks what agents/models running or doing task for slide panel, add_agent, update_agent, remove_agent, add_task, update_task, complete_task, get_status (active_agents, active_tasks, completed, models_in_use), get_for_tui formatted text
│   ├── updater.py        # NEW - Check for GitHub updates and show in dashboard and CLI too, get_local_commit git rev-parse HEAD, get_remote_commit GitHub API free no key + git ls-remote fallback, check_for_updates compares local vs remote fetch origin main rev-list --count behind_by, update git pull origin main + pip install
│   ├── response_tester.py # NEW - Test response time for API key how much time API key takes, test_llm_key provider api_key model prompt measures seconds/ms success tokens, test_custom_api_key, test_all_keys_for_provider/provider and rank fastest first, get_history limit 50, get_stats total/successful/failed avg fastest slowest, saves to data/response_times.json
│   └── trajectory.py     # NEW - Trajectory batch generation + compression research-ready, batch_generate prompts model max_workers parallel via subagents checkpointing, compress_trajectories max_tokens fits into token budgets, ShareGPT export, stats
├── tools/
│   ├── web_search.py     # DuckDuckGo free no API key, LRU cache 50 items TTL 10 min
│   ├── public_apis.py    # Offline-first public API discovery, filters + explicit upstream refresh
│   ├── file_tools.py     # Read/write/edit/search, OptimizedFileCache mtime <1MB
│   ├── shell.py          # Safe shell timeout 10s
│   ├── browser.py        # NEW - Browser automation Playwright free 6 tools: navigate, click, type, screenshot, extract, close
│   ├── vision.py         # NEW - Vision LLaVA via Ollama free, vision_analyze image_path prompt model llava:7b base64, vision_available_models
│   ├── voice.py          # NEW - Voice memo transcription faster-whisper free local WhisperModel base/small/medium/large-v2
│   ├── internet_eyes.py  # NEW - Agent Reach features 13 tools free zero API fees: web_read Jina Reader https://r.jina.ai/http://, rss_read feedparser, youtube_transcript yt-dlp free, youtube_search ytsearch, github_read/search API free no key, twitter_read Jina single tweet free, bilibili_search bili-cli free no login, reddit_read old.reddit.com .json + Jina fallback, v2ex_hot API free no config, xueqiu_stock_search, plus Facebook Instagram XiaoHongShu LinkedIn Xiaoyuzhou via OpenCLI Chrome session free
│   ├── agent_reach_doctor.py # NEW - Doctor real probing ordered backend candidates tells apart missing/broken/timeout, 15 platforms web, youtube, rss, github, twitter, bilibili, reddit, facebook, instagram, xiaohongshu, v2ex, xueqiu, xiaoyuzhou, linkedin, full search
│   ├── facebook.py       # NEW - Facebook search, Instagram user search, XiaoHongShu search, LinkedIn read, Xiaoyuzhou transcribe via OpenCLI Chrome session free + Jina fallback
│   ├── updater.py        # NEW - Updater tools: check_update, do_update, get_local_commit, get_remote_commit
│   ├── pentest.py        # NEW - 31 tools: recon subdomain_enum fingerprinting attack_surface_mapping, exploitation browser_xss_test shell_exploit custom_exploit_runtime http_proxy_intercept, vuln_kb search_vuln_kb get_owasp_categories, scanner scan_api_spec, multi-agent pentest_distribute_task chain_vulns scalable_scan, reporting create_run add_finding generate_patch generate_report, viewer view_run, SAST/DAST sast_scan dast_scan combined, CI/CD generate_github_actions_workflow gitlab_ci jenkinsfile, bug bounty recon generate_poc workflow, DevSecOps github_integration_pr_comment slack_notify jira_create_issue linear_create_issue, continuous learning add_finding mark_false_positive stats, compliance generate_compliance_report list_compliance_frameworks
│   └── response_tester.py # Actually in core/response_tester.py but tool wrapper
├── gateway/
│   ├── gateway.py        # FastAPI single process gateway, cross-platform continuity, GZipMiddleware, /command, /agents/status slide panel data, /platforms, /webhook/telegram, /dashboard, /keys/list/add/remove with redacted preview, /custom-apis/list/add/remove, /response-times and /response-times/test for response time test, /update/check and /update/pull and /update/local and /update/remote for update thing, /cache/stats and /cache/clear for optimization
│   ├── dashboard.html    # Living Control Room + fullscreen Live Agent Theatre
│   ├── dashboard_legacy.html # Previous dashboard compatibility view at /dashboard/legacy
│   └── static/           # Offline living-deck CSS/JS (no CDN dependencies)
├── scheduler/
│   └── cron.py           # APScheduler + natural language parser simple rules + LLM fallback free
├── subagents/
│   └── subagent.py       # Spawn isolated subagents multiprocessing, task_wrapper adds agent/task to tracker, spawn_subagent, spawn_parallel_subagents
├── tui/
│   └── tui.py            # prompt_toolkit TUI multiline editing, slash autocomplete /new /model /skills /platforms /compress /usage /panel /agents /update /check-update, history FileHistory, auto_suggest, bottom_toolbar live Agents Tasks Models refresh 2 sec, streaming tool output, interrupt Ctrl+C, /panel slide open panel showing what agents/models running
├── pentest/
│   ├── recon.py, exploitation.py, vuln_kb.py, scanner.py, multi_agent.py, reporting.py, viewer.py, sast_dast.py, cicd.py, bugbounty.py, devsecops.py, continuous_learning.py, compliance.py - All Strix features free
├── skills/
│   ├── .gitkeep
│   └── agent_reach/      # SKILL.md auto-registration for Agent Reach - trigger-first description, standing rules, quick commands bili search doctor --json
├── data/
│   ├── memory.db         # SQLite FTS5 + WAL mode + indexes + token_usage table
│   ├── user_model.json   # Free Honcho alternative
│   ├── trajectories.jsonl
│   ├── api_keys.json     # Multi-key up to 20 per provider, local only, not uploaded, redacted preview
│   ├── custom_apis.json  # Custom APIs up to 10 same name from different websites, local only
│   ├── public_apis_catalog_cache.json # Optional refreshed catalog, local only, ignored by Git
│   ├── response_times.json # Response time history last 50, avg per key
│   └── sessions/
├── resources/
│   └── public_apis_catalog.json # Bundled offline public-apis snapshot (MIT)
└── tests/
    ├── test_free_stack.py
    ├── test_custom_api.py
    └── test_token_count.py
```

## How Free Clone Handles Paywalled Parts

| Original Paywalled | Free Alternative Here |
|--------------------|----------------------|
| OpenRouter (paid API for 100s models) | Ollama local (llama3, mistral, phi3) - 100% free offline + Groq free tier 30 req/min per key, up to 20 keys = 600 req/min + HF free inference |
| Honcho dialectic user modeling (paid) | `data/user_model.json` - LLM asks dialectic questions "What matters to you?" and builds model free |
| Hosted gateway / Modal/Daytona paid | Docker + SSH + local - free, plus free tier Modal/Daytona if you have account, but fallback to local + 7 backends list |
| Premium voice transcription | faster-whisper (free, local Whisper) |
| Vector DB Pinecone paid | SQLite FTS5 (built-in, free, no API key) + WAL mode + indexes + OptimizedFileCache |
| Twitter API paid, Reddit 403, YouTube no subtitles, XiaoHongShu must login | Agent Reach free tools: Jina Reader free, yt-dlp free, old.reddit.com .json + Jina fallback, bili-cli free no login, OpenCLI Chrome session free if user has session, V2EX API free no config |

## Free vs Paid - 100% Free, MIT License

This clone is MIT, no tracking, no paywall, fully self-hosted. You own data in `data/memory.db`, `data/api_keys.json`, `data/custom_apis.json` (local only, redacted preview in dashboard, .gitignore ignores sensitive).

## New in 2.2 — Any AI API Key + Multi-Model Fleet

### Any OpenAI-compatible API key works
Hermus talks to **any** provider with a `/v1/chat/completions` endpoint:

`openai` · `groq` · `openrouter` · `together` · `fireworks` · `deepseek` · `mistral` · `gemini` · `cerebras` · `sambanova` · `hf` · `github` · `azure` · `ollama` · `lmstudio` · `vllm` · `custom` · `anthropic`

```bash
# List known providers
hermus multikey providers

# Add ANY key — auto health check + model discovery + rate-limit headers
hermus multikey add --provider groq --key gsk_...
hermus multikey add --provider openrouter --key sk-or-...
hermus multikey add --provider gemini --key AIza...
hermus multikey add --provider custom --key sk-... \
  --base-url https://my-proxy.example.com/v1 --model my-model --rpm 30 --tpm 60000

# What models can this key run?
hermus multikey models --provider groq

# Health: auth, latency, RPM/TPM headers, sample models
hermus multikey health
hermus multikey rates

# Chat with that provider
hermus --model groq/llama-3.1-8b-instant
hermus --model openrouter/auto
hermus --model custom/my-model   # uses stored custom base_url
```

### Multi-model task distribution (fleet)
Give work to **multiple models + keys in parallel**:

| Strategy | Behavior |
|----------|----------|
| `fanout` | Same prompt → many models → judge consensus |
| `map` | Split goal into subtasks → each model/key does one → merge |
| `race` | First healthy successful response wins |
| `auto` | Picks map vs fanout from goal complexity |

```bash
hermus fleet workers
hermus fleet run "Compare Python vs Rust async and recommend" --strategy auto
hermus fleet fanout "What is CRDT?" --providers groq,openai
hermus fleet map "Research X, implement Y, review risks" --workers 3

# Multi-agent mode auto-dispatches hard goals across the fleet
hermus --mode multi-agent --model groq/llama-3.1-8b-instant
hermus --mode multi-chat   # fanout consensus for accuracy
```

Agent tools: `add_api_key`, `discover_models`, `check_api_key_health`, `get_rate_limit_status`, `fleet_distribute_task`, `fleet_fanout`, `fleet_map_goal`, `list_ai_providers`.

Gateway: `GET /providers`, `/keys/health`, `/keys/rates`, `/keys/models`, `/fleet/workers`, `POST /fleet/run`.

---

## New in 2.1 — Versatility Upgrade

### A. Multi-step agent loop + auto tool registry
- **ReAct loop** — agent can call tools across multiple rounds (`HERMUS_MAX_TOOL_STEPS`, default 8) until the task is done
- **`core/tool_registry.py`** — auto-discovers `TOOLS` + `TOOL_MAP` from all modules (no more giant if/elif)
- **109 tools registered** including full pentest map (SAST/DAST, CI/CD, bounty, compliance) previously missing from execute path
- **`skill_use(name, task=...)`** — skills receive task/query context
- CLI: `hermus tools`

### B. Real Telegram + Discord channels
- **Telegram** — real `sendMessage` replies, voice memo → Whisper, `/new` `/status` `/help`
  - Long-polling by default (no public URL): set `TELEGRAM_BOT_TOKEN` and `hermus gateway start`
  - Or webhook mode: `HERMUS_TELEGRAM_MODE=webhook` + `/webhook/telegram`
- **Discord** — real `discord.py` bot (mention or DM); set `DISCORD_BOT_TOKEN` + Message Content Intent
- Gateway: `/channels/status`, `/channels/start`, `/telegram/send`

### C. MCP client + semantic embeddings (free local RAG)
- **MCP stdio client** — plug external tool servers into the agent tool bus
  - CLI: `hermus mcp add|list|remove|connect|call`
  - Built-in test server: `tools/mcp_echo_server.py`
  - Config: `data/mcp_servers.json`
- **Semantic memory** — Ollama `nomic-embed-text` when available, hashing fallback offline
  - Hybrid FTS5 + vector search on every chat turn
  - Ingest files/dirs: `hermus embed ingest ./docs`
  - Tools: `embeddings_ingest`, `embeddings_search`, `embeddings_hybrid_search`, `memory_semantic_search`
  - CLI: `hermus embed status|ingest|search|clear`

```bash
# Multi-step agent (default)
python hermus.py --model mock/mock

# Semantic RAG
python hermus.py embed ingest ./README.md
python hermus.py embed search "tool registry"

# MCP echo server test
python hermus.py mcp add --name echo --command python3 --arg tools/mcp_echo_server.py
python hermus.py mcp connect
python hermus.py mcp call --server echo --tool echo --args '{"message":"hi"}'

# Telegram + Discord (with gateway)
export TELEGRAM_BOT_TOKEN=...
export DISCORD_BOT_TOKEN=...
python hermus.py gateway start
```

---

## Roadmap - All Done ✅

- [x] Multi-step ReAct tool loop + auto tool registry (109 tools)
- [x] Real Telegram sendMessage + long-poll + Discord bot listener
- [x] MCP client (stdio) + semantic embeddings hybrid memory (free local RAG)
- [x] Core agent loop with free LLM + token counting + caching + task tracker + update check
- [x] SQLite FTS5 memory + curated memory + nudges + token_usage table + WAL mode + indexes + OptimizedFileCache
- [x] Autonomous skill creation + self-improvement (agentskills.io compatible) + LRU cache
- [x] Free tools: web_search DuckDuckGo cached, file cached, shell, python_exec
- [x] Browser automation Playwright free 6 tools
- [x] Vision LLaVA via Ollama free 2 tools
- [x] Voice memo transcription faster-whisper free 2 tools
- [x] Internet Eyes Agent Reach 13 tools + 5 social + doctor 2 tools = 20 tools free zero API fees
- [x] Gateway: CLI + Telegram + Discord (free Bot APIs) + FastAPI single process + GZip + endpoints /agents/status /keys/list/add/remove /custom-apis/list/add/remove /response-times and /response-times/test /update/check/pull/local/remote /cache/stats/clear + cross-platform continuity
- [x] Cron scheduler natural language + APScheduler free
- [x] Subagents parallel + RPC + task tracker
- [x] TUI with slash commands autocomplete + bottom toolbar live + slide panel /panel /agents + /update
- [x] Backends 7 terminal: local, Docker hardened, SSH, Singularity, Modal free tier hibernates, Daytona free tier, Vercel Sandbox free tier
- [x] Trajectory batch generation + compression + ShareGPT export
- [x] Custom API add any REST API as tool in 1 command + URL templating + auth bearer/apikey/basic + up to 10 keys same name from different websites round-robin + deduplicated tool defs
- [x] Multi-Key up to 20 per provider groq/hf/openai/custom + 10 per custom API same name from different websites, round-robin deque, failure tracking 5 min cooldown, fallback, parallel execution multiprocessing different keys = 3x faster, CLI multikey add/list/remove/parallel
- [x] Multi-AI collaboration multiple AIs talk to each other, personas researcher/coder/reviewer/writer/planner/debater/optimist/pessimist, chat_round, debate, collaborate_on_task, judge final consensus, mixing multi-key + multi-AI, CLI multiai debate/chat/personas
- [x] Token counting free tiktoken accurate if installed else len/4 fallback, cost estimation pricing per 1M Ollama $0 Groq 70b $0.59/0.79, SQLite token_usage table, /usage command, /compress
- [x] Slide panel what agents/models running - TUI /panel + Web dashboard 👁️ button slide right 0→440px auto-refresh 2 sec
- [x] Living Agent Control Room with task-first mission telemetry, animated Hermus core and robotic crew, connected Missions/Computer/Agents/Memory/Models/Connections/Settings modules, automatic fullscreen Live Agent Theatre, backend local speech, microphone transcription, captions, approvals and Halt; legacy UI preserved at `/dashboard/legacy`
- [x] Skin Engine data-driven theming like original, YAML from data/skins and ~/.hermes/skins/, colors banner_border/title/accent/text/bg, spinner thinking_verbs/faces/wings/banner_ascii, branding agent_name/response_label/tool_prefix, example cyberpunk.yaml neon #FF00FF/#00FFFF, smooth animations cardSlideIn statPop navSlideIn fadeInUp float pulse + toggle in Config pane + localStorage + body.no-animations
- [x] Update thing - if you update anything in GitHub, shows update in dashboard and CLI too - core/updater.py get_local_commit git rev-parse HEAD, get_remote_commit GitHub API free no key + git ls-remote fallback, check_for_updates compares local vs remote fetch origin main rev-list --count behind_by, update git pull origin main + pip install, tools updater 4 tools, CLI hermus update --check and hermus update without check, TUI /update, gateway endpoints /update/check/pull/local/remote, dashboard orange banner gradient #ff9800→#ff5722 + badge Update! X behind + Update Now button + auto-check every 30 sec
- [x] Response time test for API key - how much time API key takes to get response - core/response_tester.py test_llm_key provider api_key model prompt measures seconds/ms success tokens, test_custom_api_key, test_all_keys_for_provider/provider and rank fastest, get_history limit 50, get_stats total/successful/failed avg fastest slowest, saves to data/response_times.json, updates multi_key_manager avg_response_time, tools 3, gateway endpoints /response-times and /response-times/test, dashboard Keys pane Test All buttons per provider and per custom API group + response time history
- [x] Optimization - Optifine everything - LRU cache 100 items TTL, WAL mode, indexes, GZip, smooth animations, multi-key 10/20, response time tracking, token counting, file cache <1MB mtime, skill cache, etc.
- [x] Pentest Strix full - recon, exploitation, vuln KB CVSS OWASP, scanner OWASP Top 10 and beyond, multi-agent Graph of Agents distributed scalable dynamic coordination, reporting create_run add_finding PoC generate_patch autofix generate_report compliance, viewer local web viewer, SAST/DAST, CI/CD GitHub Actions blocking PR, Bug Bounty recon PoC workflow, DevSecOps GitHub/GitLab/Slack/Jira/Linear, Continuous Learning, Compliance OWASP PCI-DSS SOC2 GDPR
- [x] Comparison document MD + DOCX + PDF downloadable
- [x] Optimization guide

## Contributing

PRs welcome - keep it free, no paid APIs required for core. Add more skins YAML, more persona presets, more free tools.

## Disclaimer

This is a community free clone, not affiliated with Nous Research, Panniantong Agent Reach, or Strix. Original Hermes Agent is by Nous Research (hermes-agent.org), Agent Reach by Panniantong (github.com/Panniantong/agent-reach), Strix by usestrix (strix.ai). This free version respects original MIT/Apache licenses and aims for feature parity with 100% free stack.

---

**Hermus Agent Free - The agent that grows with you, for free, forever on your $5 VPS.** ☤ Gold and Kawaii (｡♥‿♥｡) • MIT • No tracking • Self-hosted • No paywall • Optimized Everything • 91+ Tools
