"""Free Web Search - DuckDuckGo, no API key - Optimized with caching"""
from core.cache import web_search_cache

try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Free web search via DuckDuckGo - no API key - Optimized with caching"""
    # Check cache first - optimize
    cache_key = web_search_cache.make_key(query, max_results)
    cached = web_search_cache.get(cache_key)
    if cached is not None:
        return cached

    if not DDG_AVAILABLE:
        result = [{"title": f"Mock result for {query}", "href": "https://example.com", "body": f"This is mock search result for {query} - install duckduckgo-search for real free search: pip install duckduckgo-search"}]
        web_search_cache.set(cache_key, result)
        return result

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            normalized = []
            for r in results:
                normalized.append({
                    "title": r.get("title",""),
                    "href": r.get("href",""),
                    "body": r.get("body","")
                })
            web_search_cache.set(cache_key, normalized)
            return normalized
    except Exception as e:
        result = [{"title": "Search error", "href": "", "body": f"Search failed: {e}. Mock fallback for {query}"}]
        web_search_cache.set(cache_key, result)
        return result

# Tool definition for LLM
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Free web search via DuckDuckGo, no API key needed. Search for current information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results", "default": 5}
            },
            "required": ["query"]
        }
    }
}

def execute(query: str, max_results: int = 5) -> dict:
    results = web_search(query, max_results)
    return {"results": results, "count": len(results)}

TOOLS = [TOOL_DEFINITION]
TOOL_MAP = {"web_search": execute}
