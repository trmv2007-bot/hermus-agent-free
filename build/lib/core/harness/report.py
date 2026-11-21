"""Markdown rendering for harness run reports and run comparisons."""

from __future__ import annotations

from typing import Any


def _status_icon(r: dict[str, Any]) -> str:
    if r.get("success"):
        return "✅"
    status = r.get("status", "")
    return {"timeout": "⏱️", "setup_failed": "🧪", "error": "💥"}.get(status, "❌")


def render_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary", {})
    lines: list[str] = []
    lines.append(f"# Harness run `{report.get('run_id')}`")
    lines.append("")
    lines.append(f"- **Suite:** `{report.get('suite')}` (`{report.get('suite_source')}`)")
    lines.append(f"- **Model:** `{report.get('model')}`" + (f"  **Tag:** `{report.get('tag')}`" if report.get("tag") else ""))
    lines.append(f"- **Started:** {report.get('timestamp')}")
    lines.append(
        f"- **Result:** {s.get('passed', 0)}/{s.get('runs', 0)} passed — "
        f"**success rate {s.get('success_rate', 0.0):.1%}** "
        f"(avg {s.get('avg_steps', 0)} steps, avg {s.get('avg_duration_ms', 0)} ms/task, wall {s.get('wall_ms', 0)} ms, "
        f"~{s.get('total_tokens_est', 0)} tokens est)"
    )
    statuses = s.get("statuses") or {}
    if statuses:
        lines.append(f"- **Statuses:** {', '.join(f'{k}={v}' for k, v in sorted(statuses.items()))}")
    by_cat = s.get("by_category") or {}
    if by_cat:
        lines.append("")
        lines.append("## By category")
        lines.append("")
        lines.append("| category | passed | runs | rate |")
        lines.append("|---|---|---|---|")
        for cat, c in sorted(by_cat.items()):
            lines.append(f"| {cat} | {c.get('passed', 0)} | {c.get('runs', 0)} | {c.get('rate', 0.0):.0%} |")
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    lines.append("| task | category | result | checks | steps | ms | status |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in report.get("tasks", []):
        checks = f"{r.get('checks_passed', 0)}/{r.get('checks_total', 0)}"
        lines.append(
            f"| `{r.get('id')}` | {r.get('category', '')} | {_status_icon(r)} {'PASS' if r.get('success') else 'FAIL'} "
            f"| {checks} | {r.get('steps', 0)} | {r.get('duration_ms', 0)} | {r.get('status', '')} |"
        )
    failed = [r for r in report.get("tasks", []) if not r.get("success")]
    if failed:
        lines.append("")
        lines.append("## Failure details")
        lines.append("")
        for r in failed:
            lines.append(f"### `{r.get('id')}` ({r.get('status')})")
            if r.get("error"):
                lines.append(f"- error: `{str(r['error'])[:300]}`")
            for c in r.get("checks", []):
                if not c.get("passed"):
                    lines.append(f"- ❌ `{c.get('type')}`: {str(c.get('detail', ''))[:200]}")
            lines.append("")
    lines.append(f"Artifacts: `{report.get('report_json', '')}`")
    lines.append("")
    return "\n".join(lines)


def render_scoreboard(reports: list[dict[str, Any]]) -> str:
    """One-glance table across one or more runs (multi-suite / multi-model)."""
    lines = [
        "",
        "=== scoreboard ===",
        f"{'suite':<12} {'model':<26} {'passed':>7} {'rate':>6} {'tokens':>8} {'wall ms':>8}  run_id",
    ]
    for r in reports:
        s = r.get("summary", {})
        passed = f"{s.get('passed', 0)}/{s.get('runs', 0)}"
        rate = float(s.get("success_rate", 0.0) or 0.0)
        toks = int(s.get("total_tokens_est", 0) or 0)
        wall = int(s.get("wall_ms", 0) or 0)
        line = (
            f"{str(r.get('suite', '?'))[:12]:<12} {str(r.get('model', '?'))[:26]:<26} "
            f"{passed:>7} {rate:>6.0%} {toks:>8} {wall:>8}  {r.get('run_id', '?')}"
        )
        if s.get("regression"):
            line += "  REGRESSED"
        lines.append(line)
    return "\n".join(lines)


def render_compare(comp: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Harness comparison: `{comp.get('run_a')}` vs `{comp.get('run_b')}`")
    lines.append("")
    lines.append(
        f"- A: suite `{comp.get('suite_a')}`, model `{comp.get('model_a')}` — success rate **{comp.get('success_rate_a', 0.0):.1%}**"
    )
    lines.append(
        f"- B: suite `{comp.get('suite_b')}`, model `{comp.get('model_b')}` — success rate **{comp.get('success_rate_b', 0.0):.1%}**"
    )
    lines.append(
        f"- **Delta (B − A): {comp.get('delta', 0.0):+.1%}** · common tasks: {comp.get('common_tasks')} · both pass: {comp.get('both_pass')} · **winner: {comp.get('winner')}**"
    )
    regs = comp.get("regressions") or []
    fixed = comp.get("fixed") or []
    if regs:
        lines.append("")
        lines.append(f"## ⚠️ Regressions in B ({len(regs)})")
        for t in regs:
            lines.append(f"- `{t}`")
    if fixed:
        lines.append("")
        lines.append(f"## ✅ Fixed in B ({len(fixed)})")
        for t in fixed:
            lines.append(f"- `{t}`")
    only_a = comp.get("only_in_a") or []
    only_b = comp.get("only_in_b") or []
    if only_a or only_b:
        lines.append("")
        lines.append(f"Only in A: {', '.join(f'`{t}`' for t in only_a) or '-'}")
        lines.append(f"Only in B: {', '.join(f'`{t}`' for t in only_b) or '-'}")
    lines.append("")
    return "\n".join(lines)
