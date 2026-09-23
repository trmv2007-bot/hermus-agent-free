# Hermus Fleet v2 — migration tooling

## What this directory is

This directory is the **user-facing migration entry point** for moving existing
Hermus data onto the Persistent Fleet v2 runtime and the Memory 2.0 subsystem. It
is intentionally **reporting-first** in this first version.

## What "Fleet v2" means in this repo

Fleet v2 is the event-sourced runtime surface under `core/fleet/`:

- `core/fleet.bus` — the Fleet Bus: durable JSONL event log at `data/fleet/`,
  snapshots, cursors, retention.
- `core/fleet.registry` — the Fleet Registry: the one durable roster of
  `LiveAgent`.
- `core/fleet.missions` — durable Mission objects on the bus.

The old runtime surfaces are still present during the migration window. Fleet v2
does **not** automatically absorb everything that used to live elsewhere.

## Design principles

- **Explicit reads, explicit writes.** Every phase says what it reads and what it
  would change.
- **Dry-run by default in inspection/dry-run phases.**
- **No silent drops.**
- **Machine-readable + human-readable logs.**
- **Config-driven paths.** Paths come from `core.config`, not hardcoded.

## What the first version does

1. **Inspect.** Read-only report of:
   - legacy v1 memory db (`data/memory.db`): curated memory, sessions, user model,
     token usage, lessons, skill usage, trajectories;
   - Memory 2.0 db (`data/memory2.db`): stats;
   - Fleet v2 state (`data/fleet/`): bus segments, snapshot, roster, any
     legacy-imported agents;
   - other known durable artifacts under `data/`.
2. **Dry-run.** Report what each known migration action *would* do, with counts.
3. **Apply (explicit, confirmed).** Run only actions safe to automate today.
4. **Verify.** Re-read and report whether the intended outcome holds.

## What this tooling does now

- Single entry point: `python -m migrations` (and `hermus migrate` once wired).
- Phases: `inspect`, `dry-run`, `apply`, `verify`.
- One structured JSON report per run under `logs/hermus_migration_<timestamp>.json`,
  plus a readable summary to stdout.
- Fleet v2 inspection via the existing FleetBus/Registry readers.
- Legacy memory inspection via the existing read-only `MigrationReader`.
- A **narrow** automated apply path: the existing
  `core.memory.migrate_legacy` path (v1 curated → semantic, sessions-as-episodic),
  re-exposed here with dry-run and confirmation.

## What this tooling refuses to do automatically

- **Bulk import of old agents into the Fleet Registry.** The existing
  `FleetRegistry.import_legacy_agent(...)` imports as `SLEEPING` by design; that is
  an operator choice per agent, not a silent bulk migration.
- **Republishing old harness/session/trajectory traffic onto the Fleet Bus.**
- **Deleting or emptying user data.**
- **Moving/renaming/repointing user content files.**
- **Writing secrets, keys, or env config.**

## What users should back up first

Before any `apply` phase, back up at least:

- `data/memory.db`
- `data/memory2.db`
- `data/fleet/`
- `data/presence.json`
- `data/user_model.json`
- `data/trajectories.jsonl`
- `data/embeddings.db`
- any user content dirs you care about (`skills/`, `recordings/`, `sandboxes/`,
  `outputs/`, `artifacts/`, `plans/`, `counsel/`, etc.)

A safe first run:

```
python -m migrations inspect
python -m migrations dry-run
```

then, only after reviewing the report:

```
python -m migrations apply
python -m migrations verify
```

## Reversibility

- The legacy memory migration writes a marker so re-runs are no-ops; removing the
  marker re-allows a re-run. It does not delete the legacy database.
- Fleet v2 writes go through the normal FleetBus/Registry path. To roll back,
  restore from the pre-`apply` backup.

## Scope limits and known gaps

- v1 is read-heavy by design. If a full Fleet v2 migration is not yet safe to
  automate for a given user's data, the inspection/reporting layer is the
  deliverable, and the actual steps are documented as manual/semi-manual.
- The tooling does not yet know about every possible user artifact. Treat the
  inspect report as a starting point and add missing paths to your own backup
  checklist.
