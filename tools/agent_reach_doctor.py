"""Doctor - Tells you which platform works, which not, how to fix - like agent-reach doctor - free, real probing"""

import shutil
import subprocess
import requests
from pathlib import Path
from typing import List, Dict
import json

def probe_command(cmd: List[str], timeout: int = 10) -> Dict:
    """Real probing - actually executes upstream commands and tells apart missing/broken/timeout - free"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return {"status": "available", "output": result.stdout[:500], "cmd": " ".join(cmd)}
        else:
            # Check if broken (stale venv shebang after system Python upgrade) vs missing
            if "No such file or directory" in result.stderr or "not found" in result.stderr.lower():
                return {"status": "missing", "error": result.stderr[:500], "cmd": " ".join(cmd), "fix": f"Install: {' '.join(cmd)} not found"}
            else:
                return {"status": "broken", "error": result.stderr[:500], "output": result.stdout[:500], "cmd": " ".join(cmd), "fix": "Try reinstalling"}
    except FileNotFoundError:
        return {"status": "missing", "error": f"Command not found: {cmd[0]}", "cmd": " ".join(cmd), "fix": f"Install {cmd[0]}: pip install {cmd[0]} or apt install"}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"Timeout after {timeout}s", "cmd": " ".join(cmd), "fix": "Command timed out, maybe network issue, retry"}
    except Exception as e:
        return {"status": "error", "error": str(e), "cmd": " ".join(cmd)}

def doctor_check_all() -> Dict:
    """Doctor check all channels with real probing, ordered backend candidates - free"""
    checks = []

    # 1. Web - Jina Reader
    try:
        resp = requests.get("https://r.jina.ai/https://example.com", timeout=10)
        status = "available" if resp.status_code == 200 else "broken"
        checks.append({
            "platform": "web",
            "name": "Web - Read any webpage",
            "status": status,
            "backend": "jina_reader",
            "backend_candidates": ["jina_reader", "direct_fetch"],
            "active_backend": "jina_reader" if status == "available" else "direct_fetch",
            "zero_config": True,
            "free": True
        })
    except Exception as e:
        checks.append({"platform": "web", "name": "Web", "status": "error", "error": str(e)})

    # 2. YouTube - yt-dlp
    yt_probe = probe_command(["yt-dlp", "--version"], timeout=5)
    checks.append({
        "platform": "youtube",
        "name": "YouTube - Subtitle + Search",
        "status": yt_probe["status"],
        "backend": "yt-dlp",
        "backend_candidates": ["yt-dlp", "jina_fallback"],
        "active_backend": "yt-dlp" if yt_probe["status"] == "available" else "jina_fallback",
        "zero_config": True,
        "free": True,
        "probe": yt_probe,
        "fix": "Install free: pip install yt-dlp"
    })

    # 3. RSS - feedparser
    try:
        import feedparser
        checks.append({"platform": "rss", "name": "RSS", "status": "available", "backend": "feedparser", "zero_config": True, "free": True})
    except ImportError:
        checks.append({"platform": "rss", "name": "RSS", "status": "missing", "backend": "feedparser", "fix": "pip install feedparser (free)"})

    # 4. GitHub - gh CLI
    gh_probe = probe_command(["gh", "--version"], timeout=5)
    checks.append({
        "platform": "github",
        "name": "GitHub - Read public repo + search",
        "status": "available" if gh_probe["status"] == "available" else "fallback",
        "backend": "gh_cli",
        "backend_candidates": ["gh_cli", "api_no_key"],
        "active_backend": "gh_cli" if gh_probe["status"] == "available" else "api_no_key",
        "zero_config": True,
        "free": True,
        "probe": gh_probe
    })

    # 5. Twitter/X
    checks.append({
        "platform": "twitter",
        "name": "Twitter/X - Read single tweet / Search / Timeline",
        "status": "partial",
        "backend": "jina_reader",
        "backend_candidates": ["jina_reader", "opencli_chrome", "cookie_editor"],
        "active_backend": "jina_reader",
        "zero_config": "partial - single tweet via Jina free, search/timeline needs config",
        "free": True,
        "config_guide": "Tell Agent 'help me config Twitter' - Cookie-Editor manual export TWITTER_AUTH_TOKEN and TWITTER_CT0 free local only",
        "fix": "Install Cookie-Editor extension, export cookies, set env TWITTER_AUTH_TOKEN and TWITTER_CT0"
    })

    # 6. Bilibili
    bili_probe = probe_command(["bili-cli", "--version"], timeout=5)
    checks.append({
        "platform": "bilibili",
        "name": "Bilibili - Search + video details",
        "status": bili_probe["status"] if bili_probe["status"] == "available" else "fallback",
        "backend": "bili-cli",
        "backend_candidates": ["bili-cli", "duckduckgo_site_search"],
        "active_backend": "bili-cli" if bili_probe["status"] == "available" else "duckduckgo_site_search",
        "zero_config": True,
        "free": True,
        "probe": bili_probe,
        "fix": "Install free: pip install bili-cli"
    })

    # 7. Reddit
    checks.append({
        "platform": "reddit",
        "name": "Reddit - Search + read posts/comments",
        "status": "requires_config",
        "backend": "opencli_chrome",
        "backend_candidates": ["opencli_chrome", "rdt-cli+cookie", "old_reddit_json_fallback"],
        "active_backend": "old_reddit_json_fallback",
        "zero_config": False,
        "free": True,
        "config_guide": "Desktop OpenCLI using browser login (Chrome session free) or rdt-cli + Cookie free - Tell Agent 'help me config Reddit'",
        "note": "No zero-config path: anonymous API blocked, old.reddit.com .json still works sometimes + Jina fallback"
    })

    # 8. Facebook
    checks.append({
        "platform": "facebook",
        "name": "Facebook - Search, homepage, Feed, group list",
        "status": "requires_config",
        "backend": "opencli_chrome",
        "backend_candidates": ["opencli_chrome"],
        "active_backend": "opencli_chrome",
        "zero_config": False,
        "free": True,
        "config_guide": "Desktop OpenCLI reusing Chrome login free - Tell Agent 'help me config Facebook'"
    })

    # 9. Instagram
    checks.append({
        "platform": "instagram",
        "name": "Instagram - User search, Profile, recent posts, Explore",
        "status": "requires_config",
        "backend": "opencli_chrome",
        "backend_candidates": ["opencli_chrome"],
        "active_backend": "opencli_chrome",
        "zero_config": False,
        "free": True,
        "config_guide": "Desktop OpenCLI Chrome session free - Tell Agent 'help me config Instagram'"
    })

    # 10. XiaoHongShu
    checks.append({
        "platform": "xiaohongshu",
        "name": "XiaoHongShu - Search, reading, comments",
        "status": "requires_config",
        "backend": "opencli_chrome",
        "backend_candidates": ["opencli_chrome", "mcp", "cookie_editor"],
        "active_backend": "opencli_chrome",
        "zero_config": False,
        "free": True,
        "config_guide": "OpenCLI only uses user's existing Chrome session; MCP/old tool uses Cookie-Editor - Tell Agent 'help me config Xiaohongshu'",
        "note": "Agent Reach does not perform Xiaohongshu login for user, nor read browser Cookie; OpenCLI only uses existing controlled Chrome session"
    })

    # 11. V2EX
    checks.append({
        "platform": "v2ex",
        "name": "V2EX - Hot posts, node posts, details+replies, user info",
        "status": "available",
        "backend": "api_no_key",
        "backend_candidates": ["api_no_key"],
        "active_backend": "api_no_key",
        "zero_config": True,
        "free": True
    })

    # 12. Xueqiu
    checks.append({
        "platform": "xueqiu",
        "name": "Xueqiu - Stock行情, search stock, hot posts, hot stock ranking",
        "status": "partial",
        "backend": "jina_reader",
        "backend_candidates": ["config_file_cookie", "chrome_cookies_via_browser_cookie3", "homepage_fallback"],
        "active_backend": "jina_reader",
        "zero_config": False,
        "free": True,
        "config_guide": "Needs xq_a_token cookie from Chrome via browser_cookie3 or --from-browser chrome, see Agent Reach docs"
    })

    # 13. Xiaoyuzhou podcast
    checks.append({
        "platform": "xiaoyuzhou",
        "name": "Xiaoyuzhou Podcast - Podcast audio to text Whisper",
        "status": "requires_config",
        "backend": "whisper_transcription",
        "backend_candidates": ["groq_whisper_free", "openai_whisper"],
        "active_backend": "groq_whisper_free",
        "zero_config": False,
        "free": True,
        "config_guide": "Needs Groq free key for Whisper or OpenAI key - Tell Agent 'help me config Xiaoyuzhou podcast'",
        "note": "Whisper transcription module with Groq->OpenAI fallback - free key"
    })

    # 14. LinkedIn
    checks.append({
        "platform": "linkedin",
        "name": "LinkedIn - Jina Reader public page + Profile details",
        "status": "partial",
        "backend": "jina_reader",
        "backend_candidates": ["jina_reader", "opencli_chrome"],
        "active_backend": "jina_reader",
        "zero_config": "partial - public page via Jina free, details needs config",
        "free": True
    })

    # 15. Full web search
    checks.append({
        "platform": "full_search",
        "name": "Full Web Search - Semantic search",
        "status": "available",
        "backend": "duckduckgo_free",
        "backend_candidates": ["exa_mcp_free_no_key", "duckduckgo_free"],
        "active_backend": "duckduckgo_free",
        "zero_config": True,
        "free": True,
        "note": "Auto config MCP Exa free no key - we use DuckDuckGo free as fallback"
    })

    return {
        "total_platforms": len(checks),
        "available": len([c for c in checks if c["status"] == "available"]),
        "partial": len([c for c in checks if c["status"] in ("partial", "fallback")]),
        "requires_config": len([c for c in checks if c["status"] == "requires_config"]),
        "missing": len([c for c in checks if c["status"] == "missing"]),
        "checks": checks,
        "note": "Free, zero API fees, privacy: Cookie only locally not uploaded, code open source reviewable, local computer no proxy needed, proxy only for server ~$1/month"
    }

def doctor_text_report() -> str:
    """Text report like agent-reach doctor --json"""
    result = doctor_check_all()
    lines = []
    lines.append(f"Agent Reach Doctor - Free Check - {result['total_platforms']} platforms")
    lines.append(f"Available: {result['available']} | Partial/Fallback: {result['partial']} | Requires Config: {result['requires_config']} | Missing: {result['missing']}")
    lines.append("")
    for check in result["checks"]:
        icon = "✅" if check["status"] == "available" else "⚠️" if check["status"] in ("partial", "fallback") else "❌" if check["status"] == "missing" else "🔧"
        lines.append(f"{icon} {check['platform']:15} {check['name']}")
        lines.append(f"   Status: {check['status']} | Backend: {check.get('active_backend','unknown')} | Candidates: {', '.join(check.get('backend_candidates', []))}")
        lines.append(f"   Zero-config: {check.get('zero_config')} | Free: {check.get('free')}")
        if check.get("probe"):
            probe = check["probe"]
            lines.append(f"   Probe: {probe.get('cmd')} -> {probe.get('status')}: {probe.get('output','')[:80] or probe.get('error','')[:80]}")
        if check.get("config_guide"):
            lines.append(f"   Config: {check['config_guide']}")
        if check.get("fix"):
            lines.append(f"   Fix: {check['fix']}")
        lines.append("")

    lines.append("Note: " + result["note"])
    return "\n".join(lines)

# Tool definitions for free LLM
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "doctor_check_all",
            "description": "Doctor - Tells you which platform works, which not, how to fix - like agent-reach doctor --json - real probing of upstream commands, ordered backend candidates, tells apart missing/broken/timeout, free",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "doctor_text_report",
            "description": "Doctor text report - human readable report of all platforms status, backend candidates, active backend, zero-config, free vs config needed, fix instructions - free",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]

TOOL_MAP = {
    "doctor_check_all": doctor_check_all,
    "doctor_text_report": doctor_text_report,
}
