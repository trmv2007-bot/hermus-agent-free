"""21-pii-sanitization-agent - free clone of the original other agent.

Original: https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/21-pii-sanitization-agent
Framework: other - Industry: general
Free implementation (search + local LLM + honest fallback) lives in
``skills/_free_clone.py``; this module declares which clone it is.
"""

from typing import Any, Dict

from skills._free_clone import CLONES, run_free_clone

SPEC = CLONES["21_pii_sanitization_agent"]

# Declared rather than inferred: the shared runner searches the web (network)
# and reads the query (read). Pinned here so capability reporting stays exact.
CAPABILITIES = ["read", "network"]


def run(query: str = "default query", **kwargs: Any) -> Dict[str, Any]:
    """Run this free clone for ``query``."""
    return run_free_clone(SPEC, query, **kwargs)


if __name__ == "__main__":
    print(run("test query for 21-pii-sanitization-agent")["report"][:500])
