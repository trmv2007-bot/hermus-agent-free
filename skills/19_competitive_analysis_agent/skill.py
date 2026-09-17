"""19-competitive-analysis-agent - free clone of the original langgraph agent.

Original: https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/19-competitive-analysis-agent
Framework: langgraph - Industry: business
Free implementation (search + local LLM + honest fallback) lives in
``skills/_free_clone.py``; this module declares which clone it is.
"""

from typing import Any, Dict

from skills._free_clone import CLONES, run_free_clone

SPEC = CLONES["19_competitive_analysis_agent"]

# Declared rather than inferred: the shared runner searches the web (network)
# and reads the query (read). Pinned here so capability reporting stays exact.
CAPABILITIES = ["read", "network"]


def run(query: str = "default query", **kwargs: Any) -> Dict[str, Any]:
    """Run this free clone for ``query``."""
    return run_free_clone(SPEC, query, **kwargs)


if __name__ == "__main__":
    print(run("test query for 19-competitive-analysis-agent")["report"][:500])
