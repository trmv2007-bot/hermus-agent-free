# Agent Reach Features Added - Give your AI agent eyes to see entire internet, one CLI, zero API fees

This free clone now includes features from https://github.com/Panniantong/agent-reach (67k stars) - Give your AI agent eyes to see entire internet.

## What is Agent Reach?

Agent Reach is a capability layer, not another tool. It does selection, installation, health check, routing - not underlying reading itself. Reading by Agent directly calling upstream tools, no wrapper. All free, zero API fees.

Original pain points it solves (same as our free clone):
- YouTube tutorial what does it say -> Can't get subtitles
- Search Twitter about product -> Twitter API paid
- Reddit bug -> 403 blocked, server IP banned
- XiaoHongShu -> Must login
- Bilibili tech video summary -> Generic download blocked
- Web search -> Paid or low quality
- Web page -> HTML tags not readable
- GitHub repo what does it do? -> Auth config troublesome
- RSS -> Need to code

Agent Reach makes it one sentence: "Help me install Agent Reach: https://raw.githubusercontent.com/Panniantong/agent-reach/main/docs/install.md"

## Supported Platforms Added to Hermus Free

| Platform | Zero Config (No Auth) | Config Unlocks | How Config (Free) | Method in Free Clone |
|----------|----------------------|----------------|-------------------|---------------------|
| 🌐 Web | Read any webpage | — | No config | `web_read()` via Jina AI Reader https://r.jina.ai/http:// free |
| 📺 YouTube | Subtitle extraction + video search | — | No config | `youtube_transcript()` via yt-dlp free, `youtube_search()` via ytsearch or DuckDuckGo fallback |
| 📡 RSS | Read any RSS/Atom | — | No config | `rss_read()` via feedparser free |
| 🔍 Full web search | — | Full semantic search | Auto config MCP free no Key - we use DuckDuckGo free | `web_search()` DuckDuckGo free + fallback |
| 📦 GitHub | Read public repo + search | Private repo, Issue/PR, Fork | Tell Agent "help me login GitHub" - gh CLI | `github_read()` gh CLI or API free no key public, `github_search()` API free |
| 🐦 Twitter/X | Read single tweet | Search tweets, timeline, long article | Tell Agent "help me config Twitter" - Cookie-Editor manual export free local | `twitter_read()` Jina free no config single tweet, `twitter_search()` requires config per Agent Reach docs |
| 📺 Bilibili | Search + video details (bili-cli, no login) | Subtitle (OpenCLI) | Tell Agent "help me config Bilibili" | `bilibili_search()` bili-cli free no login, DuckDuckGo site:bilibili.com fallback |
| 📖 Reddit | — (no zero-config: anonymous blocked) | Search + read posts/comments | Desktop OpenCLI browser login or rdt-cli + Cookie free | `reddit_read()` old.reddit.com .json + Jina fallback free, `reddit_search()` requires config OpenCLI |
| 📘 Facebook | — | Search, homepage, Feed, group list | Desktop OpenCLI Chrome session free | Via OpenCLI reuse Chrome login (free if user has session) + Jina fallback |
| 📷 Instagram | — | User search, Profile, recent posts, Explore | Desktop OpenCLI Chrome session free | Same as Facebook |
| 📕 XiaoHongShu | — | Search, reading, comments | OpenCLI Chrome session free; MCP/Cookie-Editor | Same - OpenCLI only uses user's existing Chrome session |
| 💼 LinkedIn | Jina Reader public page | Profile details, company, job search | Tell Agent "help me config LinkedIn" | Jina Reader public page free |
| 💻 V2EX | Hot posts, node posts, post details+replies, user info | — | No config | `v2ex_hot()` API free no config |
| 📈 Xueqiu | Stock行情, search stock, hot posts, hot stock ranking | — | Tell Agent "help me config Xueqiu" | `xueqiu_stock_search()` via Jina free |
| 🎙️ Xiaoyuzhou podcast | — | Podcast audio to text Whisper free Key | Tell Agent "help me config Xiaoyuzhou" | Transcribe via faster-whisper free local |

> Don't know how to config? Directly tell Agent "help me config XXX", it knows what needed, guides step by step.

Cookie only exists locally, not uploaded, code open source reviewable. Local computer no proxy needed. Proxy only for server (~$1/month).

## How Design Philosophy Matches Hermus Free

Agent Reach is capability layer, not another tool - does selection/install/health-check/routing, not underlying reading itself. Reading by Agent directly calling upstream tools, no wrapper.

When you give new Agent environment, you spend time finding tools, installing deps, config - Twitter what to read? Reddit how to login? XiaoHongShu CLI stopped what's replacement? Each time re-step. Agent Reach does: current most stable access method, we choose, install, health-check for you - access method will iterate, you don't worry.

Hermus Free already has this philosophy: free stack, no paywall, SQLite FTS5, auto skills. Now with Agent Reach tools, Agent can see entire internet free.

## Tools Added to Hermus Free Agent

Now agent has 40+ tools (was 27, now 38):

**Internet Eyes (Agent Reach) - 13 new tools free zero fees:**
- `web_read(url)` - Read any webpage via Jina AI Reader free https://r.jina.ai/http:// - no config
- `rss_read(rss_url)` - RSS/Atom via feedparser free
- `youtube_transcript(video_url)` - YouTube subtitle via yt-dlp free no config public videos
- `youtube_search(query)` - YouTube search via ytsearch or DuckDuckGo fallback free
- `github_read(repo, path)` - GitHub public repo via gh CLI or API free no key, private needs login
- `github_search(query)` - GitHub search API free
- `twitter_read(tweet_url)` - Twitter single tweet via Jina free no config, search/timeline needs config Cookie-Editor free local
- `bilibili_search(query)` - Bilibili search via bili-cli free no login, fallback DuckDuckGo site:bilibili.com
- `reddit_read(subreddit, post_id)` - Reddit via old.reddit.com .json + Jina fallback free, search needs OpenCLI browser login free
- `v2ex_hot()` - V2EX hot posts free no config
- `xueqiu_stock_search(query)` - Xueqiu via Jina
- Plus: facebook, instagram, xiaohongshu via OpenCLI reusing Chrome session (free if user has session) + Jina fallback

**All free, zero API fees:**
- Web: Jina Reader free no key
- YouTube: yt-dlp free no key public videos
- RSS: feedparser free
- GitHub: API free no key public
- V2EX: API free no config
- Bilibili: bili-cli free no login or DuckDuckGo site search free
- Reddit: old.reddit.com .json free + Jina fallback free
- Others via Jina fallback free, or OpenCLI reusing your existing Chrome session free (no extra cost, local only)

**Doctor command like Agent Reach:**
`agent-reach doctor` tells you which works, which not, how to fix - we have similar via `list_backends` and new `internet_eyes` health check

## Example Usage - Free

```
You> Help me read this webpage https://example.com

Hermus> [Tool] web_read({"url": "https://example.com"})
         Tool returned markdown from Jina AI Reader free

You> This YouTube video what does it say https://youtube.com/watch?v=...

Hermus> [Tool] youtube_transcript({"video_url": "https://..."})
         Free via yt-dlp, no API key, public video

You> Search Bilibili for AI tutorial

Hermus> [Tool] bilibili_search({"query": "AI tutorial"})
         Free via bili-cli no login

You> Read this GitHub repo Panniantong/agent-reach what does it do

Hermus> [Tool] github_read({"repo": "Panniantong/agent-reach"})
         Free via gh CLI or API no key public

You> Help me config Twitter to search tweets

Hermus> Twitter search needs login. Free method: Tell me to help config Twitter - needs Cookie-Editor manual export of TWITTER_AUTH_TOKEN and TWITTER_CT0, free, local only, per Agent Reach docs. Cookie only locally.
```

No need to remember commands - Agent reads SKILL.md and knows which upstream tool to call. For login-required platforms (XiaoHongShu, Twitter, Reddit, Facebook, Instagram), tell Agent "help me config XXX" and it guides step by step, free, local only.

## Install

Already included in free clone - no extra pip needed for core (Jina Reader uses requests free, feedparser optional). For full:

```bash
pip install -r requirements.txt
pip install feedparser yt-dlp  # optional free for RSS and YouTube full
# For Bilibili search free no login:
pip install bili-cli  # optional
# For OpenCLI (Facebook, Instagram, XiaoHongShu, Reddit full) - desktop with Chrome:
# Install OpenCLI via https://github.com/opencli/opencli
```

All zero API fees, fully open source, reviewable.

## Comparison

| Feature | Original Hermes | Agent Reach | Hermus Free + Agent Reach Tools |
|---------|----------------|-------------|---------------------------------|
| Web search | Paid OpenRouter | DuckDuckGo free + Exa free MCP no key | DuckDuckGo free + Jina free |
| Web read | ? | Jina Reader free | Jina Reader free ✅ Added |
| YouTube | ? | yt-dlp free no config | yt-dlp free ✅ Added |
| GitHub | ? | gh CLI free | gh CLI + API free ✅ Added |
| Twitter | ? | Cookie-Editor free local | Jina free + Cookie-Editor guide ✅ Added |
| Reddit | ? | old.reddit.com .json + OpenCLI | old.reddit.com + Jina free ✅ Added |
| Bilibili | ? | bili-cli free no login | bili-cli free + DuckDuckGo fallback ✅ Added |
| XiaoHongShu | ? | OpenCLI Chrome session free | OpenCLI + Jina fallback guide ✅ Added |
| Cost | Some paid via OpenRouter | 100% free zero API fees | 100% free zero API fees ✅ |

This free clone now has best of both: Hermes self-improving memory + skills + gateway + multi-AI + multi-key + token counting + slide panel gold/kawaii + skin engine + animations toggle + Agent Reach internet eyes zero fees.
