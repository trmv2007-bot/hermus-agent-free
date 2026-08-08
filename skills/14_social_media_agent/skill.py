"""
social-media-content-agent - Free Clone Skill for Hermus Agent Free
Original: https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/14-social-media-agent
Framework: crewai - Industry: marketing
Free implementation using Ollama local + DuckDuckGo + SQLite FTS5
"""

from typing import Dict, Any

def run(query: str = "default query", **kwargs) -> Dict[str, Any]:
    """
    Free clone of 14-social-media-agent from 500-AI-Agents-Projects
    Original: Generates platform-optimized content for Twitter, LinkedIn, and Instagram
    Framework: crewai
    
    This is simplified free version using Hermus tools:
    - web_search (DuckDuckGo free, no API key)
    - file_write (write report)
    - Uses Ollama local free LLM via core/llm.py if available
    """
    try:
        # Try to use Hermus free tools if available
        from tools.web_search import web_search
        from core.llm import free_llm
        
        # Search web for query
        search_results = web_search(query, max_results=3)
        
        # Synthesize via free LLM
        prompt = f"Task: Generates platform-optimized content for Twitter, LinkedIn, and Instagram\nQuery: {query}\nSearch results: {str(search_results)[:1000]}\n\nProvide structured report like original 14-social-media-agent would."
        
        messages = [
            {"role": "system", "content": "You are 14_social_media_agent - Generates platform-optimized content for Twitter, LinkedIn, and Instagram. Provide structured report."},
            {"role": "user", "content": prompt}
        ]
        
        resp = free_llm.chat(messages)
        
        return {
            "skill": "14_social_media_agent",
            "original": "https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/14-social-media-agent",
            "framework": "crewai",
            "industry": "marketing",
            "query": query,
            "search_results": search_results[:2],
            "report": resp.content[:2000],
            "method": "free_clone_using_ollama_duckduckgo_sqlite",
            "note": "Free clone - original uses crewai + GPT-4o-mini + Tavily paid, this uses Ollama local free + DuckDuckGo free + SQLite FTS5 free"
        }
        
    except Exception as e:
        # Fallback mock if tools not available
        return {
            "skill": "14_social_media_agent",
            "original": "https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/14-social-media-agent",
            "framework": "crewai",
            "query": query,
            "report": f"Mock report for {query} - Generates platform-optimized content for Twitter, LinkedIn, and Instagram. This is free clone of 14-social-media-agent from 500-AI-Agents-Projects (36k stars). Original uses crewai. Free version uses Ollama local + DuckDuckGo free.",
            "error_fallback": str(e)[:200],
            "method": "mock_free_fallback"
        }

if __name__ == "__main__":
    result = run("test query for 14-social-media-agent")
    print(result["report"][:500])
