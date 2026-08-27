"""Semantic memory / embeddings tools for the agent."""

from core.embeddings import embedding_store


def embeddings_status() -> dict:
    return {"success": True, **embedding_store.backend_info()}


def embeddings_search(query: str, limit: int = 5) -> dict:
    return embedding_store.search(query, limit=limit)


def embeddings_hybrid_search(query: str, limit: int = 5) -> dict:
    return embedding_store.hybrid_search(query, limit=limit)


def embeddings_ingest(path: str, source: str = None) -> dict:
    return embedding_store.ingest_path(path, source=source)


def embeddings_add(text: str, source: str = "manual", key: str = None) -> dict:
    meta = {"key": key} if key else {}
    return embedding_store.add_text(text, metadata=meta, source=source)


def embeddings_clear(source: str = None) -> dict:
    return embedding_store.clear(source=source)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "embeddings_status",
            "description": "Semantic memory status - backend (ollama nomic-embed-text or hash fallback), dim, count - free",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embeddings_search",
            "description": "Semantic vector search over ingested docs/memory chunks - free local",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embeddings_hybrid_search",
            "description": "Hybrid FTS5 keyword + semantic vector search for best recall - free",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embeddings_ingest",
            "description": "Ingest a file or directory into semantic memory (md/txt/py/pdf/csv) - free local RAG",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embeddings_add",
            "description": "Add a free-form text chunk into semantic memory",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string", "default": "manual"},
                    "key": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "embeddings_clear",
            "description": "Clear semantic memory (optional source prefix filter)",
            "parameters": {
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "required": [],
            },
        },
    },
]

TOOL_MAP = {
    "embeddings_status": embeddings_status,
    "embeddings_search": embeddings_search,
    "embeddings_hybrid_search": embeddings_hybrid_search,
    "embeddings_ingest": embeddings_ingest,
    "embeddings_add": embeddings_add,
    "embeddings_clear": embeddings_clear,
}
