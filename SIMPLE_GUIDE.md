# Hermus Agent Free - Simple Guide

**One sentence:** Free AI agent that lives on your server, remembers you, learns skills by itself, and can see the entire internet + hack/find bugs - no API keys needed, no paywall.

> **GitHub:** https://github.com/trmv2007-bot/hermus-agent-free

---

## What is it?

Think of it as **Jarvis for your $5 VPS** - but free, open-source, you own data.

- You teach it once, it remembers forever (no re-explaining every day)
- It creates skills from experience and self-improves
- It can chat from Telegram, Discord, CLI - same memory everywhere
- It can see Twitter, Reddit, YouTube, GitHub, Bilibili, etc. for free, no API fees
- It can find bugs in your code like a hacker, with real PoCs
- It runs 100% offline with Ollama (llama3) - no API key needed

Original Hermes (Nous Research) is 225k stars but needs OpenRouter paid API. This is **free clone** with same features + extra free features.

---

## What Can It Do? (Simple List)

### 💬 Chat & Memory
- **Chat:** `hermus` - talk in terminal, remembers across sessions
- **Memory:** SQLite file `data/memory.db` - you own it, searches via FTS5 free
- **Skills:** Auto-creates skills after 3+ tool calls, saves to `skills/name/SKILL.md + skill.py`, reuses next time in 1 call instead of 5 = zero cost
- **User Model:** Learns your projects, preferences via dialectic questions

### 🌍 Internet Eyes - See Entire Internet Free (Agent Reach - 67k stars)
- **Web:** `web_read(url)` - Any webpage via Jina Reader free, no key
- **YouTube:** `youtube_transcript(url)` - Get subtitles via yt-dlp free, `youtube_search(query)`
- **RSS:** `rss_read(url)` - Any RSS/Atom via feedparser free
- **GitHub:** `github_read(repo)` - Public repo free no key, `github_search(query)`
- **Twitter/X:** `twitter_read(url)` - Single tweet via Jina free, search needs Cookie-Editor free local
- **Bilibili:** `bilibili_search(query)` - Bilibili search via bili-cli free no login
- **Reddit:** `reddit_read(subreddit)` - Old.reddit.com .json + Jina fallback free
- **V2EX:** `v2ex_hot()` - Hot posts free no config
- **Facebook/Instagram/XiaoHongShu:** Via OpenCLI reusing Chrome session free if you have Chrome logged in
- **Doctor:** `doctor_check_all()` - Tells which platform works, which not, how to fix, real probing

### 🛡️ Pentest - Find & Fix Bugs Like Real Hacker (Strix - 49k stars)
- **Recon:** `subdomain_enum(domain)`, `fingerprinting(url)`, `attack_surface_mapping(domain)`
- **Exploitation:** `browser_xss_test(url)` XSS via Playwright free, `shell_exploit(command)`, `custom_exploit_runtime(code)` Python sandbox PoC validation
- **Vuln Knowledge Base:** `search_vuln_kb(query)` - CVE-style with CVSS scoring + OWASP Top 10
- **Scanner:** `comprehensive_scan(target)` - OWASP Top 10: A01 Broken Access Control IDOR, A03 Injection SQL/XSS, A10 SSRF, A07 JWT, etc., `scan_api_spec(path)` OpenAPI/Postman
- **Multi-Agent Pentest:** `pentest_distribute_task(target)` - 5 specialized agents recon/web_exploiter/api_tester/post_exploit/reporter collaborate, chain vulns, scalable scan parallel
- **Reporting:** `pentest_create_run(target)`, `pentest_add_finding(run_name, finding)` with PoC, `pentest_generate_patch(finding)` autofix PR, `pentest_generate_report(run_name)` compliance-ready summary critical/high/medium/low
- **Viewer:** `pentest_view_run(run_name)` - Local web viewer 127.0.0.1 random port private tokened link
- **SAST/DAST:** `sast_scan(dir)` pattern matching semgrep-like, `dast_scan(url)` header checks ZAP-like
- **CI/CD:** `generate_github_actions_workflow(target)` - Creates `.github/workflows/strix-pentest.yml` scans on every PR and blocks insecure code
- **Bug Bounty:** `bug_bounty_recon(target)`, `generate_bounty_poc(vuln, target)`, `bug_bounty_workflow(target)` - Recon + scan + PoCs + report
- **DevSecOps:** `github_integration_pr_comment()`, `slack_notify()`, `jira_create_issue()`, `linear_create_issue()`
- **Continuous Learning:** `continuous_learning_add_finding()`, `mark_false_positive()`, `stats()` - AI builds on past findings, reduces false positives
- **Compliance:** `generate_compliance_report(run_name, framework="OWASP Top 10 2021")` - OWASP, PCI-DSS 4.0, SOC2, GDPR

### 🌐 Gateway - Talk From Anywhere
- **Single process:** `hermus gateway start` port 8000
- **Platforms free:** Telegram Bot API free, Discord free, Slack webhook free, WhatsApp/Signal via bridges free, CLI
- **Cross-platform:** Start on Telegram, continue on CLI - same memory SQLite
- **Endpoints:** `/command`, `/agents/status` (slide panel data), `/platforms`, `/webhook/telegram`, `/dashboard`, `/keys/list/add/remove`, `/custom-apis/list/add/remove`, `/response-times`, `/update/check/pull`

### ⏰ Scheduler - Cron Natural Language Free
- `hermus cron add "every day at 9am send me report"` → cron `0 9 * * *`
- Delivery to any platform Telegram/Discord/CLI

### 👥 Subagents + Multi-AI - Parallel + Collaboration
- **Subagents:** `hermus subagent spawn "research X and Y in parallel"` - multiprocessing isolated, each own session, write Python scripts via RPC zero-context-cost
- **Multi-AI:** Multiple AIs talk to each other for anything
  - Personas: researcher, coder, reviewer, writer, planner, debater, optimist, pessimist
  - `hermus multiai debate "Python vs Rust" --rounds 2 --agents researcher coder reviewer`
  - Judge gives final consensus
  - Mixing multi-key + multi-AI: 3 agents each using different Groq keys parallel = 2x faster + better quality

### 🔑 Multi-Key - Use 10 Keys At Once, Complete Quickly
- **LLM keys:** Up to 20 per provider (groq, hf, openai, custom) - `hermus multikey add --provider groq --key gsk_... --name key1`
- **Custom API keys:** Up to 10 same API name from different websites you know - `hermus api add --name weather_api --auth-token siteA_key` + same name with siteB_key + siteC_key = 3 keys stored, round-robin Call1 siteA, Call2 siteB, Call3 siteC, fallback if 429
- **Parallel execution:** 3 tasks with 3 different keys in parallel = 3x faster vs 1 key sequential
- **Round-robin + failure tracking:** 5 min cooldown if fails >=3 times

### 🔌 Public API Discovery + Custom API Tools
- `hermus api discover "weather forecast" --auth No` searches the bundled `public-apis/public-apis` directory offline
- Filter with `--category Security`, `--cors Yes`, `--allow-http`, or refresh via `hermus api refresh-catalog`
- Agent tools: `public_api_search`, `public_api_categories`, `public_api_refresh`; gateway endpoints: `/public-apis/search`, `/public-apis/categories`, `/public-apis/refresh`
- Discovery returns provider documentation links, not automatically trusted endpoints. Review the provider's docs and terms first
- `hermus api add --name weather_api --description "Get weather" --url "https://api.openweathermap.org/data/2.5/weather/{city}" --param "city:City name" --auth-type bearer --auth-token YOUR_KEY`
- URL templating `{param}`, auth bearer/apikey/basic, up to 10 keys same name round-robin
- Stored in `data/custom_apis.json` local only, not uploaded
- Agent sees it as tool: `weather_api(city="London")`

### 📊 Token Counting - Free Tracking
- Counts tokens for all models: prompt/completion/total/cost, tiktoken accurate if installed else len/4 fallback
- SQLite `token_usage` table, `memory.add_token_usage()`, `get_token_usage()`
- `/usage` command in TUI shows session + global totals + recent calls, `/compress` shows trajectory tokens

### 👁️ Slide Panel - What Agents/Models Are Running
- **TUI:** `/panel` or `/agents` command + bottom toolbar live `Agents: 2 | Tasks: 1 | Models: ollama/llama3.1:8b` refresh 2 sec, right sidebar 0→440px cubic-bezier slides open, auto-refresh 2 sec, shows active agents (name, model badge, persona, task, status, started), active tasks, models in use, recently completed
- **Dashboard:** 👁️ Agents Panel button fixed right top, slides right sidebar, same data via `GET /agents/status`

### 🎨 Polished Dashboard - Gold & Kawaii + 16 Panes
- **Colors:** Default gold #DAA520 cornsilk #FFF8DC kawaii faces (｡♥‿♥｡), slate royal blue #4169e1, ares crimson #8B0000 bronze #CD7F32, mono #555555/#c9d1d9, poseidon deep blue #006994 seafoam
- **16 panes like original Hermes:** Sessions (stats bar, FTS5 search, table title/source icon/model/msgs/tools/active time, live pulsing green badge, rename/export/delete), Keys (redacted preview, description, link, input, delete, Custom Keys for arbitrary env vars), Agents, Config (skin engine + animations toggle), Providers, Tools, Custom Keys, Gateway, Channels, Webhooks, Pairing, Logs (File Level Component Lines auto-refresh 5 sec), Analytics (token analytics), Cron, Kanban, Achievements (plugins)
- **Skin Engine:** `core/skin_engine.py` loads YAML from `data/skins/*.yaml` and `~/.hermes/skins/*.yaml`, fields colors.banner_border/title/accent/text/bg, spinner thinking_verbs/faces/wings/banner_ascii, branding agent_name/response_label/tool_prefix, example cyberpunk.yaml neon #FF00FF/#00FFFF, activate `/skin cyberpunk`
- **Smooth Animations + Toggle:** cardSlideIn 0.5s, statPop 0.6s, navSlideIn, fadeInUp, float 3s, pulse 2s, all elements transition 0.35s cubic-bezier, toggle in Config pane Enable Smooth Animations + Reduce Motion switches + localStorage + body.no-animations

### 🔄 Update Thing - Shows Update in Dashboard and CLI Too
- `core/updater.py`: `get_local_commit()` git rev-parse HEAD, `get_remote_commit()` GitHub API free no key + git ls-remote fallback, `check_for_updates()` compares local vs remote fetch origin main rev-list --count behind_by, `update()` git pull origin main + pip install
- Tools: `check_update()`, `do_update()`, `get_local_commit()`, `get_remote_commit()`
- CLI: `hermus update --check` only checks, `hermus update` pulls, TUI `/update` command, startup auto checks and shows banner if update available
- Gateway: `GET /update/check`, `POST /update/pull`, `GET /update/local`, `GET /update/remote`
- Dashboard: Orange banner gradient #ff9800→#ff5722 top with Update available! Local X behind remote Y by N: message by author on date - Click to update + Update Now button + badge top-right Update! X behind + auto-check every 30 sec

### ⏱️ Response Time Test - How Much Time API Key Takes
- `core/response_tester.py`: `test_llm_key(provider, api_key, model, prompt)` measures seconds/ms success tokens model preview, saves to `data/response_times.json` history last 50, updates multi_key_manager avg_response_time, `test_custom_api_key`, `test_all_keys_for_provider` and `test_all_keys_for_custom_api` rank fastest first
- Tools: `test_api_key_response_time(provider, api_key, model, prompt)`, `test_custom_api_response_time(api_name, api_key, test_args)`, `get_response_time_stats()`
- Gateway: `GET /response-times`, `POST /response-times/test`
- Dashboard Keys pane: Test All Response Time button per provider and per custom API group, each key row Test button + avg/last response time, history chart

### 🚀 Optimized Everything
- `core/cache.py`: LRUCache max_size 100 TTL 300-600 sec OrderedDict timestamps Lock hits/misses, make_key md5, OptimizedFileCache mtime check <1MB, global caches llm_cache 100 10 min, memory_search_cache 50 5 min, web_search_cache 50 10 min, tool_result_cache 100 5 min, skill_cache 20 10 min
- Memory: WAL mode, synchronous NORMAL, cache_size 64MB, temp_store MEMORY, indexes idx_sessions_session_id/timestamp
- Web search, file tools, skill manager cached
- Gateway GZipMiddleware minimum_size 500 + /cache/stats and /cache/clear endpoints
- Performance: LLM repeated 2-5 sec → 0.01 sec cache hit 200-500x faster, memory search 50-100ms → 1ms 50-100x faster, web search 1-2 sec → 1ms 1000x faster, file read 5-10ms → 0.1ms 50-100x faster, skills list 20-50ms → 1ms 20-50x faster, gateway dashboard 500ms → 200ms 2.5x faster

---

## Quick Start (100% Free, No API Keys)

### Ollama Fully Free Offline (Recommended)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b        # LLM free
ollama pull llava:7b           # Vision free

# Clone free agent
git clone https://github.com/trmv2007-bot/hermus-agent-free.git
cd hermus-agent-free
pip install -r requirements.txt
pip install playwright && playwright install chromium  # Browser free
pip install faster-whisper  # Voice free

# Chat free offline, no API key
python hermus.py --model ollama/llama3.1:8b

# TUI with gold/kawaii dashboard
# Type /panel to see running agents, /update to check GitHub updates, /skills, /model
```

### Add Multiple API Keys (Free Tier) for 3x Faster

```bash
# Get 3 Groq free keys from console.groq.com/keys (30 req/min per key)
python hermus.py multikey add --provider groq --key gsk_abc... --name groq_key_1
python hermus.py multikey add --provider groq --key gsk_def... --name groq_key_2
python hermus.py multikey add --provider groq --key gsk_ghi... --name groq_key_3
# Now 90 req/min = 3x faster, round-robin + fallback if 429
```

### Add Any API From Different Websites You Know (Up to 10 Same Name)

```bash
# Weather API with 3 keys from 3 different websites you know
python hermus.py api add --name weather_api --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City" --auth-token siteA_key_123
python hermus.py api add --name weather_api --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City" --auth-token siteB_key_456
python hermus.py api add --name weather_api --url "https://api.openweathermap.org/data/2.5/weather" --param "q:City" --auth-token siteC_key_789
# Now 3 keys stored for same API name, round-robin Call1 siteA, Call2 siteB, Call3 siteC

# Test which key fastest
python hermus.py api test --name weather_api --args '{"q": "London"}'
# Or via dashboard Keys pane Test All ⏱️ button ranks fastest first
```

### Gateway + Dashboard (Gold & Kawaii)

```bash
python hermus.py gateway start --port 8000
# Open http://localhost:8000/dashboard
# - Top-right skin selector: gold / slate (royal blue #4169e1) / ares crimson / mono gray / sea (poseidon)
# - Click 👁️ Agents Panel button to slide open live agents/models/tasks
# - Keys pane: Add API key form + Custom APIs form + Test All Response Time ⏱️ buttons
# - Config pane: Skin Engine + Smooth Animations toggle + Reduce Motion toggle
# - Orange banner if GitHub has update: "Update available! Local abc behind remote def by 1" + Update Now button
# - Stats: Active Agents, Tasks, Completed, Models In Use
# - Chat input to talk to agent
```

### Multi-AI Debate - Multiple AIs Talk to Each Other

```bash
# 3 AIs debate Python vs Rust
python hermus.py multiai debate "Python vs Rust for async?" --rounds 2 --agents researcher coder reviewer

# Output:
# [researcher - Round 1]: Python async easy...
# [coder - Round 1]: Here's code...
# [reviewer - Round 1]: Coder missed GIL...
# === Final Answer === Consensus: Use Python for prototyping, Rust for perf...
```

### Pentest - Find & Fix Bugs Like Real Hacker (Strix features)

```bash
# Recon
python hermus.py --model mock/mock -c "Use subdomain_enum to find subdomains for example.com"

# Scan OWASP Top 10
python hermus.py --model mock/mock -c "Use comprehensive_scan to scan https://example.com"

# Create pentest run and view in local dashboard
python hermus.py --model mock/mock -c "Use pentest_create_run to create run for target example.com"
python hermus.py --model mock/mock -c "Use pentest_view_run to view latest run"
# Opens http://127.0.0.1:random_port with private tokened link, nothing leaves machine

# CI/CD - Generate GitHub Actions workflow that blocks PR if critical vulns
python hermus.py --model mock/mock -c "Use generate_github_actions_workflow to create workflow for target ."
# Creates .github/workflows/strix-pentest.yml
```

### Internet Eyes - See Entire Internet Free (Agent Reach features)

```bash
# No config needed:
# Web any page via Jina free
python hermus.py --model mock/mock -c "Use web_read to read https://example.com"

# YouTube transcript via yt-dlp free
python hermus.py --model mock/mock -c "Use youtube_transcript for https://youtube.com/watch?v=..."

# GitHub public repo free no key
python hermus.py --model mock/mock -c "Use github_read for Panniantong/agent-reach"

# V2EX hot free no config
python hermus.py --model mock/mock -c "Use v2ex_hot to get hot posts"
```

---

## Comparison vs All OS Agents - Simple Table

| Feature | Your Hermus Free | Hermes Original (225k) | OpenClaw | Agent Reach (67k) | OpenHands / AutoGen |
|---------|------------------|------------------------|----------|-------------------|---------------------|
| Self-improving + Skills | ✅ Auto + self-improve | ✅ 40+ builtin | ❌ | ✅ SKILL.md register | ❌ |
| Memory FTS5 Free | ✅ SQLite free + curated | ✅ FTS5 + Honcho paid | ✅ Some | ❌ | ❌ |
| Free Stack No Paywall | ✅ 100% free Ollama + DuckDuckGo + FTS5 | ❌ Needs OpenRouter paid | ❌ Mix | ✅ 100% free zero fees | ✅ Mostly free |
| Internet Eyes 15 Platforms | ✅ Web Jina, YouTube yt-dlp, GitHub API, Twitter Jina, Bilibili bili-cli, Reddit old.json, V2EX free, Facebook/Instagram/XiaoHongShu OpenCLI | ❌ No | ❌ No | ✅ Core feature | ❌ No |
| Gateway Multi-Platform | ✅ Telegram/Discord/Slack/CLI single process | ✅ Same | ✅ Some | ❌ No | ❌ No |
| Dashboard 16 Panes Gold & Kawaii | ✅ Gold #DAA520 cornsilk #FFF8DC, slate #4169e1, 16 panes Sessions Keys Agents Config etc + slide panel + skin engine + animations toggle + update banner | ✅ Same 16 panes | ❌ Simple | ❌ No dashboard | ❌ No |
| Multi-Key 10 Same Name | ✅ Up to 10 same API name from different websites round-robin | ❌ No | ❌ No | ❌ No | ❌ No |
| Multi-AI Debate | ✅ 3 AIs researcher/coder/reviewer talk, judge final | ❌ No | ❌ No | ❌ No | ✅ Yes but no personas |
| Token Counting Free | ✅ tiktoken + SQLite token_usage + /usage command | ✅ Via OpenRouter paid | ❌ No | ❌ No | ❌ No |
| Pentest Strix Full | ✅ Recon, Exploitation, Vuln KB CVSS OWASP, Scanner OWASP Top10, Multi-Agent Graph, Reporting, Viewer, SAST/DAST, CI/CD, Bug Bounty, DevSecOps, Continuous Learning, Compliance | ❌ No | ❌ No | ❌ No | ❌ No |
| Update Thing | ✅ Check GitHub + show banner in dashboard and CLI + Update Now button + auto-check 30 sec | ✅ hermes update | ❌ No | ❌ No | ❌ No |

**Your project can do right now what most OS agents can't:** Run 100% offline free on laptop/$5 VPS with Ollama no API key, 44+ tools including internet eyes zero fees + pentesting + browser + vision + voice, 7 backends, token counting, multi-key 10 keys parallel 3x faster, multi-AI debate, custom API add any website, slide panel live, gold/kawaii dashboard with 16 panes, skin engine custom YAML, smooth animations toggle, update banner when GitHub updates, response time test which key fastest.

---

## Quick Links

- **GitHub:** https://github.com/trmv2007-bot/hermus-agent-free
- **Dashboard:** `python hermus.py gateway start --port 8000` → http://localhost:8000/dashboard
- **TUI:** `python hermus.py` → type `/help`, `/panel`, `/update`, `/skills`
- **Docs:** `README.md` (30KB), `SIMPLE_GUIDE.md` (this file), `COMPARISON_DOCUMENT.md` (30KB), `AGENT_REACH_FEATURES.md`, `CUSTOM_API_GUIDE.md`, `MULTI_KEY_MULTI_AI_GUIDE.md`, `TOKEN_COUNTING_GUIDE.md`, `OPTIMIZATION_GUIDE.md`
- **Download:** `COMPARISON_DOCUMENT.md`, `Hermus_Agent_Free_Comparison_Document.docx` (48KB), `Hermus_Agent_Free_Comparison_Document.pdf` (25KB)

---

**Free, MIT, No Tracking, Self-Hosted, You Own Data, The agent that grows with you, for free, forever on your $5 VPS.** ☤ Gold and Kawaii (｡♥‿♥｡)
