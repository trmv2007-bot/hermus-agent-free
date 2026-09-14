"""research — Web research - multi-source with citations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    research_parser = subparsers.add_parser("research", help="Web research - multi-source with citations")
    research_parser.add_argument("query")


def run(args, ctx: CLIContext) -> None:
    from core.research import research_pipeline

    out = research_pipeline.run(args.query)
    print(f"\n=== RESEARCH: {args.query} ===\n{out['answer']}\n")
    print(f"Confidence: {out['confidence']}")
    print(f"Sources ({len(out['sources'])}):")
    for s in out["sources"]:
        print(f" - [{s['rank']}] {s['title']} ({s['url']})")
    if out.get("contradictions"):
        print(f"\n⚠️ Contradictions ({len(out['contradictions'])}):")
        for c in out["contradictions"]:
            print(f"   A: {c['a'][:90]} [{c['source_a']}]")
            print(f"   B: {c['b'][:90]} [{c['source_b']}]")
    if out.get("uncertain"):
        print(f"\nUncertain claims: {out['uncertain'][:3]}")
