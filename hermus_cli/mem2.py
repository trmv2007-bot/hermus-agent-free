"""mem2 — Memory 2.0 - typed long-term memory with scoring."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    mem2_parser = subparsers.add_parser("mem2", help="Memory 2.0 - typed long-term memory with scoring")
    mem2_sub = mem2_parser.add_subparsers(dest="mem2_action")
    mem2_remember = mem2_sub.add_parser("remember", help="Persist a typed memory")
    mem2_remember.add_argument("kind", choices=["working", "episodic", "semantic", "procedural", "project"])
    mem2_remember.add_argument("content")
    mem2_remember.add_argument("--importance", type=float, default=5.0)
    mem2_remember.add_argument("--success", choices=["true", "false", "none"], default="none")
    mem2_recall = mem2_sub.add_parser("recall", help="Ranked recall")
    mem2_recall.add_argument("query")
    mem2_recall.add_argument("--limit", type=int, default=10)
    mem2_hybrid = mem2_sub.add_parser("hybrid", help="Hybrid recall (BM25 + vectors + RRF + decay)")
    mem2_hybrid.add_argument("query")
    mem2_hybrid.add_argument("--limit", type=int, default=10)
    mem2_hybrid.add_argument("--kind", dest="kinds", action="append", default=None)
    mem2_hybrid.add_argument("--project", default=None)
    mem2_hybrid.add_argument("--explain", action="store_true", help="Show rank contributions per hit")
    mem2_sub.add_parser("index", help="Index health (FTS5 / vector backend / coverage)")
    mem2_sweep = mem2_sub.add_parser("sweep", help="Apply decay lifecycle (archive/purge/consolidate)")
    mem2_sweep.add_argument("--apply", action="store_true", help="Actually mutate (default: dry run)")
    mem2_sweep.add_argument("--project", default=None)
    mem2_pin = mem2_sub.add_parser("pin", help="Pin/unpin a memory so decay never evicts it")
    mem2_pin.add_argument("id", type=int)
    mem2_pin.add_argument("--off", action="store_true", help="Unpin")
    mem2_compact = mem2_sub.add_parser("compact", help="Evict aged working-memory rows into episodic")
    mem2_compact.add_argument("--age-hours", type=float, default=24.0)
    mem2_context = mem2_sub.add_parser("context", help="Show the budget-packed prompt block + eviction report")
    mem2_context.add_argument("query", nargs="?", default="")
    mem2_sub.add_parser("reindex", help="Rebuild FTS + vector indexes")
    mem2_forget = mem2_sub.add_parser("forget", help="Tombstone a memory (recall stops returning it)")
    mem2_forget.add_argument("id", type=int, nargs="?", default=None)
    mem2_forget.add_argument("--query", default="", help="Find the row(s) to forget by meaning instead of id")
    mem2_forget.add_argument("--kind", default=None)
    mem2_forget.add_argument("--limit", type=int, default=5)


def run(args, ctx: CLIContext) -> None:
    from core.memory2 import memory2

    action = args.mem2_action
    if action == "remember":
        success = None if args.success == "none" else (args.success == "true")
        r = memory2.remember(args.kind, args.content, importance=args.importance, success=success)
        print(
            f"{'✅' if r.get('success') else '❌'} {args.kind} memory {'merged' if r.get('merged') else 'saved'} id={r.get('id')}"
        )
    elif action == "recall":
        res = memory2.recall(args.query, limit=args.limit)
        if not res:
            print("No memories found.")
        for m in res:
            band = (m.get("signals") or {}).get("band", "")
            print(
                f" [{m['kind']:10s}] score={m['score']:.3f} decay={m.get('decay', 1.0):.2f}"
                f"{(' ' + band) if band else ''} | {m['content'][:120]}"
            )
    elif action == "hybrid":
        if args.explain:
            out = memory2.explain(args.query, limit=args.limit, project=args.project, kinds=args.kinds)
            print(__import__("json").dumps(out, indent=2, default=str)[:4000])
        else:
            hits = memory2.hybrid_recall(args.query, limit=args.limit, project=args.project, kinds=args.kinds)
            idx = memory2.store.index_stats()
            print(
                f"hybrid index: fts5={idx.get('fts5')} vectors={idx.get('vectors_indexed')}/"
                f"{idx.get('corpus')} backend={idx.get('vector_backend')}"
            )
            if not hits:
                print("No memories found.")
            for h in hits:
                ret = h.get("retrieval") or {}
                con = ret.get("contributions") or {}
                print(
                    f" [{h['kind']:10s}] rrf={h.get('rrf_score', 0):.4f} "
                    f"bm25#{ret.get('bm25_rank') if ret.get('bm25_rank') is not None else '-'} "
                    f"vec#{ret.get('vector_rank') if ret.get('vector_rank') is not None else '-'} "
                    f"(c={con.get('bm25', 0):.3f}/{con.get('vector', 0):.3f}/{con.get('prior', 0):.3f}) "
                    f"decay={h.get('decay', 1.0):.2f} | {(h['content'] or '')[:100]}"
                )
    elif action == "index":
        print(__import__("json").dumps(memory2.store.index_stats(), indent=2, default=str))
    elif action == "sweep":
        r = memory2.sweep(project=args.project, dry_run=not args.apply)
        print(("DRY RUN — " if r.get("dry_run") else "") + __import__("json").dumps(r, indent=2, default=str))
    elif action == "pin":
        r = memory2.pin(args.id, not args.off)
        print(f"{'✅' if r.get('success') else '❌'} id={args.id} pinned={r.get('pinned')}")
    elif action == "compact":
        r = memory2.compact_working_memory(max_age_hours=args.age_hours)
        print(f"compacted {r.get('deleted_count', 0)} working rows -> {r.get('promoted_to') or 'no promotion'}")
    elif action == "context":
        out = memory2.recall_context(args.query or "")
        print(
            f"budget={out.get('budget_tokens')} used={out.get('tokens')} "
            f"({out.get('utilization', 0):.0%}) kept={len(out.get('kept') or [])} "
            f"evicted={len(out.get('evicted') or [])} mode={out.get('mode')}"
        )
        for e in (out.get("evicted") or [])[:10]:
            print(f"  - evicted [{e.get('kind')}] decay={e.get('decay', 0):.2f} | {(e.get('content') or '')[:80]}")
        print("--- prompt block ---")
        print(out.get("text") or "(empty)")
    elif action == "reindex":
        print(__import__("json").dumps(memory2.reindex(), indent=2, default=str))
    elif action == "forget":
        r = memory2.forget(args.id, query=args.query, kind=args.kind, limit=args.limit, reason="cli")
        print(
            f"{'✅' if r.get('success') else '❌'} forgotten={r.get('forgotten')}"
            + ("" if r.get("success") else f" — {r.get('error', '')}")
        )
    else:
        ctx.parser.parse_args(["mem2", "--help"])
