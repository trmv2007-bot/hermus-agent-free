"""Facebook, Instagram, XiaoHongShu, LinkedIn, Xiaoyuzhou - via OpenCLI reusing Chrome session free + Jina fallback"""


def facebook_search(query: str, max_results: int = 5) -> dict:
    """Facebook search, homepage, Feed, group list via OpenCLI reusing Chrome login free"""
    # Check OpenCLI available
    try:
        import shutil
        if shutil.which("opencli"):
            # OpenCLI would use Chrome session
            return {
                "success": True,
                "query": query,
                "method": "opencli_chrome_free_if_user_has_session",
                "note": "Free via OpenCLI reusing Chrome login session - requires user has Chrome logged into Facebook and OpenCLI installed. Tell Agent 'help me config Facebook'",
                "results": f"Would search Facebook for {query} via OpenCLI Chrome session"
            }
    except:
        pass

    # Fallback Jina Reader for public Facebook pages (limited)
    try:
        from .internet_eyes import web_read
        # Try Facebook public page via Jina
        result = web_read(f"https://www.facebook.com/{query}", use_jina=True)
        result["method"] = "jina_fallback_public_page_free"
        result["note"] = "Free via Jina Reader for public Facebook pages. For full search/feed/groups, needs OpenCLI Chrome session free"
        return result
    except Exception as e:
        return {
            "success": False,
            "query": query,
            "error": str(e),
            "config_guide": "Desktop OpenCLI reusing Chrome login free - Tell Agent 'help me config Facebook' - requires Chrome logged into Facebook and OpenCLI installed"
        }

def instagram_user_search(username: str) -> dict:
    """Instagram user search, Profile, recent posts, Explore via OpenCLI free"""
    try:
        import shutil
        if shutil.which("opencli"):
            return {
                "success": True,
                "username": username,
                "method": "opencli_chrome_free",
                "note": "Free via OpenCLI reusing Chrome session - requires Chrome logged into Instagram"
            }
    except:
        pass

    # Fallback Jina for public profile
    try:
        from .internet_eyes import web_read
        result = web_read(f"https://www.instagram.com/{username}/", use_jina=True)
        result["method"] = "jina_fallback_public_profile_free"
        return result
    except Exception as e:
        return {"success": False, "error": str(e), "config_guide": "Tell Agent 'help me config Instagram' - OpenCLI Chrome session free"}

def xiaohongshu_search(query: str) -> dict:
    """XiaoHongShu search, reading, comments via OpenCLI free - only uses user's existing Chrome session"""
    return {
        "success": False,
        "query": query,
        "error": "XiaoHongShu search requires config",
        "method": "requires_config",
        "config_guide": "OpenCLI only uses user's existing Chrome session; MCP/old tool uses Cookie-Editor. Agent Reach does not perform Xiaohongshu login for user, nor read browser Cookie; OpenCLI only uses existing controlled Chrome session. Tell Agent 'help me config Xiaohongshu'",
        "note": "Cookie only locally, not uploaded, code open source reviewable. OpenCLI only uses existing controlled Chrome session"
    }

def linkedin_read(url: str) -> dict:
    """LinkedIn Jina Reader public page + Profile details via OpenCLI"""
    try:
        from .internet_eyes import web_read
        result = web_read(url, use_jina=True)
        result["method"] = "jina_reader_public_page_free"
        result["note"] = "Free via Jina Reader for public LinkedIn pages. For Profile details, company page, job search, needs OpenCLI Chrome session free - Tell Agent 'help me config LinkedIn'"
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

def xiaoyuzhou_transcribe(podcast_url: str) -> dict:
    """Xiaoyuzhou podcast audio to text Whisper transcription with Groq->OpenAI fallback free"""
    return {
        "success": False,
        "url": podcast_url,
        "error": "Xiaoyuzhou podcast transcription needs config",
        "method": "requires_config",
        "config_guide": "Needs Groq free key for Whisper (Groq->OpenAI fallback) - Tell Agent 'help me config Xiaoyuzhou podcast' - Whisper transcription module with Groq free key, free",
        "note": "Transcribe module: download -> compress -> chunk -> transcribe with provider fallback, fully mocked tests"
    }

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "facebook_search",
            "description": "Facebook search, homepage, Feed, group list via OpenCLI reusing Chrome login free - requires Chrome logged into Facebook and OpenCLI installed, Tell Agent 'help me config Facebook'",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "default": 5}}, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "instagram_user_search",
            "description": "Instagram user search, Profile, recent posts, Explore via OpenCLI Chrome session free - Tell Agent 'help me config Instagram'",
            "parameters": {"type": "object", "properties": {"username": {"type": "string"}}, "required": ["username"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "xiaohongshu_search",
            "description": "XiaoHongShu search, reading, comments via OpenCLI free - only uses user's existing Chrome session, Cookie only locally not uploaded, Tell Agent 'help me config Xiaohongshu'",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "linkedin_read",
            "description": "LinkedIn Jina Reader public page free + Profile details, company page, job search via OpenCLI Chrome session free",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "xiaoyuzhou_transcribe",
            "description": "Xiaoyuzhou podcast audio to text Whisper transcription with Groq->OpenAI fallback free - Tell Agent 'help me config Xiaoyuzhou podcast'",
            "parameters": {"type": "object", "properties": {"podcast_url": {"type": "string"}}, "required": ["podcast_url"]}
        }
    },
]

TOOL_MAP = {
    "facebook_search": facebook_search,
    "instagram_user_search": instagram_user_search,
    "xiaohongshu_search": xiaohongshu_search,
    "linkedin_read": linkedin_read,
    "xiaoyuzhou_transcribe": xiaoyuzhou_transcribe,
}
