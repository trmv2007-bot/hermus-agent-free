# Documentation archive

Historical documents kept for provenance. **Nothing here is the current
reference** — each file carries a banner pointing at the document that
supersedes it, and the content below those banners is preserved verbatim.

| File | Why it is here | Current reference |
|---|---|---|
| `ARCHITECTURE_UPGRADES.md` | foundation narrative, explicitly superseded | [`ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| `FINAL_REPORT.md` | branch-reconciliation record for a merge that has since landed | [`ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| `PRODUCTION_READINESS_REPORT.md` | audit report pinned to one commit | [`ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| `PHASE_A_ROADMAP.md` | completed work plan | [`ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| `PHASE_C_D.md` | completed work plan | [`ARCHITECTURE.md`](../../ARCHITECTURE.md) |
| `THINKING_SYSTEM_PLAN.md` | design record for the counsel subsystem, now built | [`README.md`](../../README.md) |
| `SIMPLE_GUIDE.md` | second getting-started guide; unique sections merged out | [`QUICKSTART.md`](../../QUICKSTART.md) |

Three repository-root documents are **load-bearing** and must stay where they
are — code and tests read them by name:

- `RED_LINES.md` — the autonomy constitution (`core/evolution.py` protects it;
  `tests/test_red_lines_policy.py` asserts its contents).
- `AUTONOMY_BOUNDARIES.md` — the derived boundary policy.
- `CAPABILITY_LEDGER.md` — written at runtime by `core/capability_ledger.py`.
