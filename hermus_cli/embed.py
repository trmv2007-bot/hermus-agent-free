"""embed — Semantic memory - ingest docs + vector search (free local)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    emb_parser = subparsers.add_parser("embed", help="Semantic memory - ingest docs + vector search (free local)")
    emb_sub = emb_parser.add_subparsers(dest="embed_action")
    emb_sub.add_parser("status", help="Backend status")
    emb_ingest = emb_sub.add_parser("ingest", help="Ingest file or directory")
    emb_ingest.add_argument("path")
    emb_ingest.add_argument("--source", default=None)
    emb_search = emb_sub.add_parser("search", help="Semantic or hybrid search")
    emb_search.add_argument("query")
    emb_search.add_argument("--limit", type=int, default=5)
    emb_search.add_argument("--hybrid", action="store_true", default=True)
    emb_search.add_argument("--semantic-only", action="store_true")
    emb_clear = emb_sub.add_parser("clear", help="Clear embeddings")
    emb_clear.add_argument("--source", default=None)


def run(args, ctx: CLIContext) -> None:
    from core.embeddings import embedding_store

    if args.embed_action == "status":
        info = embedding_store.backend_info()
        print(f"Embeddings backend: {info.get('backend')}")
        print(f"  model: {info.get('model')} | dim: {info.get('dim')}")
        print(f"  stored embeddings: {info.get('count')}")
        print(f"  db: {info.get('db')}")
    elif args.embed_action == "ingest":
        result = embedding_store.ingest_path(args.path, source=args.source)
        if result.get("success") is False or result.get("error"):
            print(f"❌ {result.get('error', 'ingest failed')}")
        else:
            print(f"✅ Ingested {args.path}")
            print(f"  files: {result.get('files') or result.get('ingested_files')}")
            print(f"  chunks added: {result.get('chunks') or result.get('total_chunks')}")
            print(f"  total embeddings: {result.get('count') or result.get('total')}")
            for err in (result.get("errors") or [])[:5]:
                print(f"  ⚠️ {err}")
    elif args.embed_action == "search":
        if args.semantic_only:
            result = embedding_store.search(args.query, limit=args.limit)
        else:
            result = embedding_store.hybrid_search(args.query, limit=args.limit)
        print(f"Results for '{args.query}' (mode={result.get('mode', 'semantic')}):")
        for i, r in enumerate(result.get("results") or [], 1):
            score = r.get("score")
            score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
            print(f"\n #{i} score={score_txt} source={r.get('source', '?')}")
            print(f"    {(r.get('content') or '')[:400]}")
        if result.get("summary"):
            print(f"\nSummary: {result['summary'][:500]}")
        if result.get("error"):
            print(f"⚠️ {result['error']}")
    elif args.embed_action == "clear":
        print(embedding_store.clear(source=args.source))
    else:
        ctx.parser.parse_args(["embed", "--help"])
