"""memory commands — the Hermus CLI's memory group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

from pathlib import Path

from ._common import CLIContext
from ._spec import Command, no_action


def _configure_mem2(subparsers) -> None:
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


def _run_mem2(args, ctx: CLIContext) -> None:
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
        no_action(ctx, "mem2")


def _configure_embed(subparsers) -> None:
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


def _run_embed(args, ctx: CLIContext) -> None:
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
        no_action(ctx, "embed")


def _configure_skill(subparsers) -> None:
    skill_parser = subparsers.add_parser("skill", help="Skills")
    skill_parser.add_argument("action", choices=["list", "improve"], help="list or improve")
    skill_parser.add_argument("--name", help="Skill name for improve")


def _run_skill(args, ctx: CLIContext) -> None:
    from core.skill_manager import skill_manager

    if args.action == "list":
        skills = skill_manager.list_skills()
        print(f"Skills ({len(skills)}):")
        for s in skills:
            print(f" - {s['name']}: {s['description'][:100]}")
    elif args.action == "improve":
        if not args.name:
            print("Need --name for improve")
        else:
            result = skill_manager.improve_skill(args.name)
            print(f"Improve result: {result}")


def _configure_forge(subparsers) -> None:
    forge_parser = subparsers.add_parser("forge", help="Skill forge - harvest skills, validate, quarantine")
    forge_sub = forge_parser.add_subparsers(dest="forge_action")
    forge_sub.add_parser("list", help="Installed skills + health")
    forge_sub.add_parser("stats", help="Harvest stats (created/quarantined/outcome rate)")
    forge_validate = forge_sub.add_parser("validate", help="Validate one skill (import + replay + smoke test)")
    forge_validate.add_argument("name")
    forge_run = forge_sub.add_parser("run", help="Run a harvested skill")
    forge_run.add_argument("name")
    forge_run.add_argument("--task", default="")
    forge_run.add_argument("--execute", action="store_true", help="Actually execute the replay plan")
    forge_sub.add_parser("quarantine", help="List quarantined skills")
    forge_log = forge_sub.add_parser("log", help="Recent forge decisions")
    forge_log.add_argument("--limit", type=int, default=15)


def _run_forge(args, ctx: CLIContext) -> None:
    from core.skill_forge import skill_forge

    action = args.forge_action
    if action == "list":
        reg = skill_forge.index()
        st = skill_forge.stats()
        print(f"skills: {reg['count']} (harvested={st['harvested']}, quarantined={st['quarantined']})")
        print(f"registry: {reg['path']}")
        for name, entry in reg["skills"].items():
            print(f" - {name:30s} v{entry.get('version', 1)} :: {str(entry.get('title', ''))[:52]}")
            print(f"     tools={','.join(entry.get('tools') or [])[:70]} status={entry.get('status')}")
    elif action == "stats":
        print(__import__("json").dumps(skill_forge.stats(), indent=2, default=str))
    elif action == "validate":
        print(__import__("json").dumps(skill_forge.validate(Path(skill_forge.skills_dir) / args.name), indent=2, default=str))
    elif action == "run":
        print(__import__("json").dumps(skill_forge.run(args.name, task=args.task, execute=args.execute), indent=2, default=str))
    elif action == "quarantine":
        q = Path(skill_forge.skills_dir) / ".quarantine"
        names = sorted(p.name for p in q.iterdir()) if q.exists() else []
        print(f"quarantined ({len(names)}): " + (", ".join(names) or "none"))
        for n in names:
            rep = q / n / "report.json"
            if rep.exists():
                try:
                    print(f"  - {n}: {__import__('json').loads(rep.read_text()).get('error', '')[:120]}")
                except Exception:
                    pass
    elif action == "log":
        log = Path(skill_forge.skills_dir) / "forge_log.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        if not lines:
            print(f"no forge log yet ({log})")
        for line in lines[-args.limit :]:
            try:
                e = __import__("json").loads(line)
            except Exception:
                continue
            print(
                f" {e.get('ts', '')} {e.get('action'):12s} {e.get('stage', '')} "
                f"{e.get('name', '')} score={e.get('evaluation', {}).get('score')} "
                f"{str(e.get('reasons') or e.get('report') or '')[:90]}"
            )
    else:
        no_action(ctx, "forge")


def _configure_eval(subparsers) -> None:
    eval_parser = subparsers.add_parser("eval", help="Eval harness - measure thinking strategies")
    eval_sub = eval_parser.add_subparsers(dest="eval_action")
    eval_run = eval_sub.add_parser("run", help="Run benchmark tasks under a strategy")
    eval_run.add_argument("--strategy", default="auto", help="auto|none|reflexion|self_consistency|verify")
    eval_run.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    eval_run.add_argument("--category", default=None, help="Only one category: fact|research|code|extraction|math")
    eval_run.add_argument("--model", default=None, help="Model for the solver")
    eval_sub.add_parser("list", help="List benchmark tasks")
    eval_compare = eval_sub.add_parser("compare", help="A/B two strategies on the same tasks")
    eval_compare.add_argument("--a", required=True, help="Strategy A: auto|none|reflexion|self_consistency|verify")
    eval_compare.add_argument("--b", required=True, help="Strategy B")
    eval_compare.add_argument("--limit", type=int, default=None)
    eval_compare.add_argument("--model", default=None)
    eval_history = eval_sub.add_parser("history", help="Show eval run history")
    eval_history.add_argument("--limit", type=int, default=10)


def _run_eval(args, ctx: CLIContext) -> None:
    from core.reasoning.eval import eval_harness

    if args.eval_action == "run":
        tasks = eval_harness.load_tasks()
        if args.category:
            tasks = [t for t in tasks if t.get("category") == args.category]
            print(f"Eval tasks filtered to category '{args.category}': {len(tasks)}")
        res = eval_harness.run(strategy=args.strategy, tasks=tasks, limit=args.limit, model=args.model)
        print(f"\n=== Eval run: strategy={res.get('strategy')} ===")
        print(
            f"  {res.get('success')}/{res.get('runs')} passed | success_rate={res.get('success_rate')} | "
            f"avg_steps={res.get('avg_steps')} | avg_tool_failures={res.get('avg_tool_failures')}"
        )
        for cat, c in (res.get("by_category") or {}).items():
            print(f"  {cat:12s} {c['success']}/{c['runs']}")
        for r in res.get("results", []):
            print(
                f"  [{'✅' if r['success'] else '❌'}] {r['id']:14s} ({r['strategy']:16s}) steps={r['steps']} fails={r['tool_failures']}"
            )
    elif args.eval_action == "list":
        for t in eval_harness.load_tasks():
            print(f"  {t['id']:16s} {t['category']:10s} {t['prompt'][:70]}")
    elif args.eval_action == "compare":
        res = eval_harness.compare(args.a, args.b, limit=args.limit, model=args.model)
        print(f"Compare {args.a} vs {args.b}: WINNER = {res['winner']}")
        print(f"  {args.a}: {res['a']}")
        print(f"  {args.b}: {res['b']}")
        print("Tip: rerun with different strategies or --limit to get stable results on real models.")
    elif args.eval_action == "history":
        h = eval_harness.history(limit=args.limit)
        if not h:
            print("No eval runs yet — `hermus eval run`")
        for run in h:
            print(
                f"  {run.get('timestamp', '')[:19]} | {str(run.get('strategy'))[:16]:16s} | "
                f"rate={run.get('success_rate')} | runs={run.get('runs')} | tag={run.get('tag', '')}"
            )
    else:
        no_action(ctx, "eval")


COMMANDS: tuple[Command, ...] = (
    Command(name="mem2", help="Memory 2.0 - typed long-term memory with scoring", configure=_configure_mem2, run=_run_mem2),
    Command(
        name="embed",
        help="Semantic memory - ingest docs + vector search (free local)",
        configure=_configure_embed,
        run=_run_embed,
    ),
    Command(name="skill", help="Skills", configure=_configure_skill, run=_run_skill),
    Command(name="forge", help="Skill forge - harvest skills, validate, quarantine", configure=_configure_forge, run=_run_forge),
    Command(name="eval", help="Eval harness - measure thinking strategies", configure=_configure_eval, run=_run_eval),
)

__all__ = ["COMMANDS"]
