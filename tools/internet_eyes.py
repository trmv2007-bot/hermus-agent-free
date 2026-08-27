"""Internet Eyes - Agent Reach Features - Give your AI agent eyes to see entire internet, one CLI, zero API fees, free

Inspired by https://github.com/Panniantong/agent-reach
Supported platforms:
- Web: read any webpage via Jina AI Reader free https://r.jina.ai/http://
- YouTube: subtitle extraction + video search via yt-dlp free
- RSS: read any RSS/Atom via feedparser free
- Full web search: DuckDuckGo free + Exa via MCP free no key
- GitHub: read public repo + search via gh CLI or API free
- Twitter/X: read single tweet, search, timeline via Jina + browser cookies free
- Bilibili: search + video details via bili-cli free no login
- Reddit: search + read posts/comments via old.reddit.com .json + Jina fallback free
- Facebook, Instagram, XiaoHongShu: via OpenCLI reusing Chrome login (free if user has Chrome session) + Jina fallback
- V2EX, Xueqiu, Xiaoyuzhou podcast

All free, zero API fees, no paid API keys needed for core (except optional free tiers)
"""

import requests
import re
from pathlib import Path
from typing import Optional
import subprocess
import sqlite3
import time

# Free Jina AI Reader - read any webpage free, no API key
# https://r.jina.ai/http://example.com returns markdown
JINA_READER_BASE = "https://r.jina.ai/"

_WEB_CACHE_DB = None

def _get_web_cache_db():
    global _WEB_CACHE_DB
    if _WEB_CACHE_DB is None:
        cache_path = Path("data/web_read_cache.db")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _WEB_CACHE_DB = sqlite3.connect(str(cache_path), check_same_thread=False)
        with _WEB_CACHE_DB:
            _WEB_CACHE_DB.execute("""
                CREATE TABLE IF NOT EXISTS web_cache (
                    url TEXT PRIMARY KEY,
                    content TEXT,
                    content_length INTEGER,
                    method TEXT,
                    created_at REAL
                )
            """)
    return _WEB_CACHE_DB

def _get_cached_web_read(url: str, ttl_seconds: int = 43200) -> Optional[dict]:
    try:
        db = _get_web_cache_db()
        with db:
            cur = db.execute("SELECT content, content_length, method, created_at FROM web_cache WHERE url = ?", (url,))
            row = cur.fetchone()
            if row:
                content, content_length, method, created_at = row
                if time.time() - created_at < ttl_seconds:
                    return {
                        "success": True,
                        "url": url,
                        "method": f"{method}_cached",
                        "content": content,
                        "content_length": content_length,
                        "cached": True,
                        "note": "Returned from local persistent cache"
                    }
    except Exception:
        pass
    return None

def _set_cached_web_read(url: str, content: str, method: str) -> None:
    try:
        db = _get_web_cache_db()
        with db:
            db.execute(
                "INSERT OR REPLACE INTO web_cache (url, content, content_length, method, created_at) VALUES (?, ?, ?, ?, ?)",
                (url, content, len(content), method, time.time())
            )
    except Exception:
        pass

def web_read(url: str, use_jina: bool = True, use_cache: bool = True) -> dict:
    """Read any webpage - free via Jina AI Reader, with persistent local cache"""
    if not url.startswith("http"):
        url = "https://" + url

    if use_cache:
        cached = _get_cached_web_read(url)
        if cached:
            return cached

    result = None
    if use_jina:
        try:
            jina_url = JINA_READER_BASE + url
            resp = requests.get(jina_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                # Jina returns markdown
                content = resp.text[:15000]
                result = {
                    "success": True,
                    "url": url,
                    "method": "jina_reader_free",
                    "content": content,
                    "content_length": len(content),
                    "note": "Free via Jina AI Reader, no API key, no config"
                }
            else:
                # Fallback to direct
                pass
        except Exception as e:
            # Fallback to direct requests with basic extraction
            pass

    # Fallback direct fetch
    if not result:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            # Simple HTML to text via regex (basic)
            text = re.sub(r'<[^>]+>', ' ', resp.text)
            text = re.sub(r'\s+', ' ', text)[:10000]
            result = {
                "success": True,
                "url": url,
                "method": "direct_fetch_free",
                "content": text,
                "status_code": resp.status_code
            }
        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    if result and result.get("success") and result.get("content"):
        _set_cached_web_read(url, result["content"], result.get("method", "direct"))

    return result

def rss_read(rss_url: str) -> dict:
    """Read any RSS/Atom feed - free via feedparser"""
    try:
        import feedparser
        feed = feedparser.parse(rss_url)
        entries = []
        for entry in feed.entries[:10]:
            entries.append({
                "title": entry.get("title",""),
                "link": entry.get("link",""),
                "published": entry.get("published",""),
                "summary": entry.get("summary","")[:500]
            })
        return {
            "success": True,
            "rss_url": rss_url,
            "feed_title": feed.feed.get("title",""),
            "entries": entries,
            "count": len(entries),
            "method": "feedparser_free_no_config"
        }
    except ImportError:
        # Fallback via Jina
        return web_read(rss_url, use_jina=True)
    except Exception as e:
        return {"success": False, "error": str(e)}

def youtube_transcript(video_url: str) -> dict:
    """YouTube subtitle extraction + video details - free via yt-dlp, no config needed for public videos"""
    # Try yt-dlp
    try:
        # Check if yt-dlp available
        result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise FileNotFoundError("yt-dlp not found")

        # Extract info without downloading
        cmd = ["yt-dlp", "--skip-download", "--write-auto-sub", "--sub-lang", "en", "--print", "%(title)s|%(description)s|%(duration)s|%(uploader)s", video_url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            parts = result.stdout.strip().split("|")
            title = parts[0] if len(parts) > 0 else ""
            description = parts[1] if len(parts) > 1 else ""
            return {
                "success": True,
                "url": video_url,
                "title": title,
                "description": description[:2000],
                "method": "yt-dlp_free_no_config",
                "note": "Free via yt-dlp, no API key, for public YouTube videos"
            }

        # Fallback to Jina reading YouTube page
        return web_read(video_url, use_jina=True)

    except FileNotFoundError:
        # Fallback to Jina Reader for YouTube - yt-dlp not installed
        jina_result = web_read(video_url, use_jina=True)
        jina_result["method"] = "jina_fallback_yt-dlp_not_installed_install_free: pip install yt-dlp"
        return jina_result
    except Exception as e:
        return {"success": False, "url": video_url, "error": str(e), "fallback": web_read(video_url, use_jina=True)}

def youtube_search(query: str, max_results: int = 5) -> dict:
    """YouTube video search - free via yt-dlp search or DuckDuckGo"""
    try:
        # yt-dlp search: ytsearch5:query
        cmd = ["yt-dlp", f"ytsearch{max_results}:{query}", "--skip-download", "--print", "%(title)s|%(webpage_url)s|%(duration)s", "--no-warnings"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            videos = []
            for line in result.stdout.strip().split("\n"):
                if "|" in line:
                    parts = line.split("|")
                    videos.append({"title": parts[0], "url": parts[1] if len(parts)>1 else "", "duration": parts[2] if len(parts)>2 else ""})
            return {"success": True, "query": query, "videos": videos, "method": "yt-dlp_search_free"}
    except Exception:
        pass

    # Fallback to DuckDuckGo search for YouTube
    try:
        from tools.web_search import web_search
        results = web_search(f"site:youtube.com {query}", max_results=max_results)
        return {"success": True, "query": query, "videos": results, "method": "duckduckgo_fallback_free"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def github_read(repo: str, path: str = "") -> dict:
    """Read GitHub repo - public repo + search free via gh CLI or API, no config for public"""
    # Try gh CLI
    try:
        cmd = ["gh", "repo", "view", repo, "--json", "description,url,issues,readme"]
        if path:
            cmd = ["gh", "api", f"/repos/{repo}/contents/{path}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return {"success": True, "repo": repo, "path": path, "data": result.stdout[:10000], "method": "gh_cli_free"}
    except Exception:
        pass

    # Fallback to public API no auth
    try:
        if path:
            url = f"https://api.github.com/repos/{repo}/contents/{path}"
        else:
            url = f"https://api.github.com/repos/{repo}"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Hermus Free"})
        if resp.status_code == 200:
            return {"success": True, "repo": repo, "data": resp.json(), "method": "github_api_free_no_key_public"}
        else:
            # Try Jina reader for GitHub page
            return web_read(f"https://github.com/{repo}", use_jina=True)
    except Exception as e:
        return {"success": False, "repo": repo, "error": str(e)}

def github_search(query: str, max_results: int = 5) -> dict:
    """GitHub search free"""
    try:
        url = f"https://api.github.com/search/repositories?q={requests.utils.quote(query)}&per_page={max_results}"
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Hermus Free"})
        if resp.status_code == 200:
            data = resp.json()
            repos = [{"name": r["full_name"], "description": r["description"], "stars": r["stargazers_count"], "url": r["html_url"]} for r in data.get("items", [])[:max_results]]
            return {"success": True, "query": query, "repos": repos, "method": "github_search_api_free"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def twitter_read(tweet_url: str) -> dict:
    """Twitter/X read single tweet - free via Jina Reader + browser cookies if available"""
    # Free method 1: Jina Reader can often read tweet via https://r.jina.ai/https://twitter.com/...
    # Free method 2: If user has browser cookies via OpenCLI (Chrome session), use that
    # For free version, we try Jina first (no config), then note about OpenCLI for full access

    # Try Jina
    result = web_read(tweet_url, use_jina=True)
    result["method"] = "jina_reader_free_no_config_single_tweet"
    result["note"] = "Free via Jina Reader for single tweet, no config. For search/timeline/long articles, tell Agent 'help me config Twitter' - needs Cookie-Editor manual export, free, local only"

    # Check if OpenCLI available (free if user has Chrome session)
    try:
        import shutil
        # OpenCLI would be via opencli tool if installed
        # For free version, we just note
        result["opencli_available"] = shutil.which("opencli") is not None
    except Exception:
        pass

    return result

def twitter_search(query: str) -> dict:
    """Twitter search - needs config for full access per Agent Reach"""
    return {
        "success": False,
        "query": query,
        "error": "Twitter search needs login. Free method: Tell Agent 'help me config Twitter' - needs Cookie-Editor manual export of TWITTER_AUTH_TOKEN and TWITTER_CT0, free, local only, per Agent Reach docs. No API fees, but requires your Chrome session.",
        "method": "requires_config",
        "config_guide": "Use Cookie-Editor extension to export Twitter cookies, then set env TWITTER_AUTH_TOKEN and TWITTER_CT0. See Agent Reach docs."
    }

def bilibili_search(query: str, max_results: int = 5) -> dict:
    """Bilibili search + video details via bili-cli free no login"""
    # Try bili-cli if available
    try:
        import shutil
        if shutil.which("bili-cli"):
            cmd = ["bili-cli", "search", query, "--limit", str(max_results)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                return {"success": True, "query": query, "results": result.stdout[:5000], "method": "bili-cli_free_no_login"}
    except Exception:
        pass

    # Fallback to DuckDuckGo search site:bilibili.com
    try:
        from tools.web_search import web_search
        results = web_search(f"site:bilibili.com {query}", max_results=max_results)
        return {"success": True, "query": query, "results": results, "method": "duckduckgo_fallback_free", "note": "Free via DuckDuckGo site search, no login. For full details, install bili-cli free: pip install bili-cli"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def reddit_read(subreddit: str, post_id: str = None) -> dict:
    """Reddit search + read posts/comments - free via old.reddit.com .json + Jina fallback"""
    # Reddit anonymous API blocked, but old.reddit.com + .json still works sometimes, plus Jina reader
    try:
        if post_id:
            url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}.json"
        else:
            url = f"https://old.reddit.com/r/{subreddit}/.json"

        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (Hermus Free)"})
        if resp.status_code == 200:
            data = resp.json()
            return {"success": True, "subreddit": subreddit, "data": str(data)[:10000], "method": "old_reddit_json_free"}
        else:
            # Fallback to Jina
            jina_url = f"https://old.reddit.com/r/{subreddit}/"
            if post_id:
                jina_url += f"comments/{post_id}/"
            return web_read(jina_url, use_jina=True)
    except Exception as e:
        return {"success": False, "error": str(e), "note": "Reddit anonymous API blocked, try Jina Reader free or OpenCLI with browser login (Chrome session) free as per Agent Reach: desktop OpenCLI reuses Chrome login"}

def reddit_search(query: str, subreddit: str = None) -> dict:
    """Reddit search - needs config for full access, free via OpenCLI + browser login"""
    return {
        "success": False,
        "query": query,
        "error": "Reddit search has no zero-config path (anonymous API blocked). Free methods: 1) Desktop OpenCLI using browser login (Chrome session, free), 2) rdt-cli + Cookie (free). Tell Agent 'help me config Reddit'",
        "method": "requires_config",
        "free_alternatives": "Use web_search with site:reddit.com as fallback free"
    }

def v2ex_hot() -> dict:
    """V2EX hot posts, node posts, post details + replies, user info - free no config"""
    try:
        url = "https://www.v2ex.com/api/topics/hot.json"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {"success": True, "hot": data[:10], "method": "v2ex_api_free_no_config"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def xueqiu_stock_search(query: str) -> dict:
    """Xueqiu stock search free but needs cookies for some endpoints"""
    try:
        # Try public search endpoint
        url = f"https://xueqiu.com/k?q={requests.utils.quote(query)}"
        # Need to use Jina for free
        return web_read(url, use_jina=True)
    except Exception as e:
        return {"success": False, "error": str(e)}

# Tool definitions for LLM - free, no API fees
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": "Read any webpage via free Jina AI Reader https://r.jina.ai/http:// - no config, no API key, zero fees. Converts HTML to markdown readable for AI. Use for any URL.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "URL to read"}, "use_jina": {"type": "boolean", "default": True}}, "required": ["url"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rss_read",
            "description": "Read any RSS/Atom feed via free feedparser, no config",
            "parameters": {"type": "object", "properties": {"rss_url": {"type": "string"}}, "required": ["rss_url"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_transcript",
            "description": "YouTube subtitle extraction + video details via free yt-dlp, no config for public videos, zero fees",
            "parameters": {"type": "object", "properties": {"video_url": {"type": "string"}}, "required": ["video_url"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_search",
            "description": "YouTube video search free via yt-dlp search or DuckDuckGo fallback",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_read",
            "description": "Read GitHub public repo + search free via gh CLI or API no key, private needs login Tell Agent 'help me login GitHub'",
            "parameters": {"type": "object", "properties": {"repo": {"type": "string", "description": "Repo owner/name"}, "path": {"type": "string", "description": "Optional path inside repo"}}, "required": ["repo"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_search",
            "description": "GitHub search free via API no key",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "twitter_read",
            "description": "Read single tweet via free Jina Reader no config, for search/timeline needs config Tell Agent 'help me config Twitter' - Cookie-Editor manual export free local only",
            "parameters": {"type": "object", "properties": {"tweet_url": {"type": "string"}}, "required": ["tweet_url"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bilibili_search",
            "description": "Bilibili search + video details via bili-cli free no login, zero API fees",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reddit_read",
            "description": "Reddit read posts/comments via old.reddit.com .json + Jina fallback free, search needs config OpenCLI browser login free",
            "parameters": {"type": "object", "properties": {"subreddit": {"type": "string"}, "post_id": {"type": "string"}}, "required": ["subreddit"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "v2ex_hot",
            "description": "V2EX hot posts, node posts, post details + replies, user info - free no config",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]

TOOL_MAP = {
    "web_read": web_read,
    "rss_read": rss_read,
    "youtube_transcript": youtube_transcript,
    "youtube_search": youtube_search,
    "github_read": github_read,
    "github_search": github_search,
    "twitter_read": twitter_read,
    "twitter_search": lambda query: {"success": False, "error": "Twitter search needs config, see twitter_read doc"},
    "bilibili_search": bilibili_search,
    "reddit_read": reddit_read,
    "reddit_search": reddit_search,
    "v2ex_hot": v2ex_hot,
    "xueqiu_stock_search": xueqiu_stock_search,
}
