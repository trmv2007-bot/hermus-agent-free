"""Free Web Search - DuckDuckGo, no API key"""
from typing import List, Dict
try:
    from duckduckgo_search import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

def web_search(query: str, max_results: int = 5) -> List[Dict]:
    """Free web search via DuckDuckGo - no API key"""
    if not DDG_AVAILABLE:
        # Fallback mock
        return [{"title": f"Mock result for {query}", "href": "https://example.com", "body": f"This is mock search result for {query} - install duckduckgo-search for real free search: pip install duckduckgo-search"}]

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            # Normalize to expected format
            normalized = []
            for r in results:
                normalized.append({
                    "title": r.get("title",""),
                    "href": r.get("href",""),
                    "body": r.get("body","")
                })
            return normalized
    except Exception as e:
        return [{"title": "Search error", "href": "", "body": f"Search failed: {e}. Mock fallback for {query}"}]

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

def execute(query: str, max_results: int = 5) -> Dict:
    results = web_search(query, max_results)
    return {"results": results, "count": len(results)}
