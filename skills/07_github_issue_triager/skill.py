"""07-github-issue-triager - free clone of the original langchain agent.

Original: https://github.com/ashishpatel26/500-AI-Agents-Projects/tree/main/07-github-issue-triager
Framework: langchain - Industry: software-development
Free implementation (search + local LLM + honest fallback) lives in
``skills/_free_clone.py``; this module declares which clone it is.
"""

from typing import Any, Dict

from skills._free_clone import CLONES, run_free_clone

SPEC = CLONES["07_github_issue_triager"]

# Declared rather than inferred: the shared runner searches the web (network)
# and reads the query (read). Pinned here so capability reporting stays exact.
CAPABILITIES = ["read", "network"]


def run(query: str = "default query", **kwargs: Any) -> Dict[str, Any]:
    """Run this free clone for ``query``."""
    return run_free_clone(SPEC, query, **kwargs)


if __name__ == "__main__":
    print(run("test query for 07-github-issue-triager")["report"][:500])
