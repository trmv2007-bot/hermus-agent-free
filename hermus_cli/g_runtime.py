"""runtime commands — the Hermus CLI's runtime group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

import json

from core.config import config


def _configure_context(subparsers) -> None:
    parser = subparsers.add_parser("context", help="Persistent personal context")
    sub = parser.add_subparsers(dest="context_action")
    get_parser = sub.add_parser("get", help="Show personal context")
    get_parser.add_argument("--query", default="")
    get_parser.add_argument("--project", default=None)
    pref = sub.add_parser("preference", help="Remember an explicit preference")
    pref.add_argument("key")
    pref.add_argument("value")
    goal = sub.add_parser("goal", help="Add or update a personal goal")
    goal.add_argument("title")
    goal.add_argument("--priority", default="normal")
    goal.add_argument("--status", default="active")
    goal.add_argument("--project", default=None)
    goal.add_argument("--deadline", default=None)
    focus = sub.add_parser("focus", help="Set current focus")
    focus.add_argument("focus")
    focus.add_argument("--project", default=None)
    project = sub.add_parser("project", help="Add or update a project record")
    project.add_argument("name")
    project.add_argument("--language", default=None)
    project.add_argument("--description", default=None)


def _run_context(args, ctx: CLIContext) -> None:
    import json as _json

    from core.personal_context import personal_context

    if args.context_action == "get":
        print(_json.dumps(personal_context.snapshot(query=args.query, project=args.project).as_dict(), indent=2, default=str))
    elif args.context_action == "preference":
        print(_json.dumps(personal_context.remember_preference(args.key, args.value), indent=2, default=str))
    elif args.context_action == "goal":
        print(
            _json.dumps(
                personal_context.add_goal(
                    args.title, priority=args.priority, status=args.status, project=args.project, deadline=args.deadline
                ),
                indent=2,
                default=str,
            )
        )
    elif args.context_action == "focus":
        print(_json.dumps(personal_context.set_focus(args.focus, project=args.project), indent=2, default=str))
    elif args.context_action == "project":
        print(
            _json.dumps(
                personal_context.upsert_project(args.name, language=args.language, description=args.description),
                indent=2,
                default=str,
            )
        )
    else:
        no_action(ctx, "context")


from ._common import CLIContext
from ._spec import Command, no_action


def _configure_gateway(subparsers) -> None:
    gateway_parser = subparsers.add_parser("gateway", help="Gateway - single process for Telegram/Discord/CLI")
    gateway_sub = gateway_parser.add_subparsers(dest="gateway_action")
    gateway_setup = gateway_sub.add_parser("setup", help="Setup gateway for platform")
    gateway_setup.add_argument("--platform", default="telegram", help="telegram, discord, slack, etc.")
    gateway_start = gateway_sub.add_parser("start", help="Start gateway")
    gateway_start.add_argument("--port", type=int, default=config.gateway_port)


def _run_gateway(args, ctx: CLIContext) -> None:
    if args.gateway_action == "setup":
        from gateway.gateway import setup

        setup(args.platform)
    elif args.gateway_action == "start":
        from gateway.gateway import start

        start(args.port)
    else:
        no_action(ctx, "gateway")


def _configure_bootstrap(subparsers) -> None:
    bootstrap_parser = subparsers.add_parser(
        "bootstrap", help="One-command idempotent setup: venv, deps, layout, migration, health"
    )
    bootstrap_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    bootstrap_parser.add_argument(
        "--verify-only", action="store_true", help="Verify the existing installation without installing"
    )
    bootstrap_parser.add_argument("--repair", action="store_true", help="Repair broken runtime pieces without deleting user data")
    bootstrap_parser.add_argument("--skip-browser", action="store_true", help="Do not download Chromium")
    bootstrap_parser.add_argument("--skip-optional", action="store_true", help="Do not install optional integrations")


def _run_bootstrap(args, ctx: CLIContext) -> None:
    import json as _json

    from bootstrap import doctor as bootstrap_doctor
    from bootstrap import run as bootstrap_run

    has_extended_flags = any(getattr(args, flag, False) for flag in ("verify_only", "repair", "skip_browser", "skip_optional"))
    if getattr(args, "json", False) and not has_extended_flags:
        report = bootstrap_doctor()
        print(_json.dumps(report, indent=2, default=str))
        raise SystemExit(report.get("exit", 1))
    raise SystemExit(
        bootstrap_run(
            verify_only=bool(getattr(args, "verify_only", False)),
            repair=bool(getattr(args, "repair", False)),
            skip_browser=bool(getattr(args, "skip_browser", False)),
            skip_optional=bool(getattr(args, "skip_optional", False)),
            json_output=bool(getattr(args, "json", False)),
        )
    )


def _configure_doctor(subparsers) -> None:
    doctor_parser = subparsers.add_parser("doctor", help="Health/installation check for Hermus")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    doctor_parser.add_argument(
        "--self-repair",
        action="store_true",
        help="Run the Hermus doctor: diagnose Hermus itself, report what went wrong and how to manage it",
    )
    doctor_parser.add_argument("--no-internet", action="store_true", help="Do not look unknown failures up online")
    doctor_parser.add_argument("--no-llm", action="store_true", help="Deterministic triage only (no model call)")
    doctor_parser.add_argument("--reap", action="store_true", help="Close out runs/jobs stuck in a non-terminal state")
    doctor_parser.add_argument(
        "--harness", action="store_true", help="Also report always-on/harness health (suites, sentinel, last runs)"
    )


def _run_doctor(args, ctx: CLIContext) -> None:
    if getattr(args, "self_repair", False):
        # The Hermus doctor's patient is Hermus itself: runtime errors,
        # stuck runs/jobs, engine health — explained with a management plan.
        from core.doctor import doctor as hermus_doctor
        from core.doctor import to_markdown

        report = hermus_doctor.run(
            ask_internet=not getattr(args, "no_internet", False),
            use_llm=not getattr(args, "no_llm", False),
            reap=bool(getattr(args, "reap", False)),
        )
        if getattr(args, "json", False):
            print(__import__("json").dumps(report, indent=2, default=str))
        else:
            print(to_markdown(report))
        raise SystemExit(0 if report.get("status") == "ok" else 1)
    from core.diagnostics import print_diagnostics, run_diagnostics

    report = run_diagnostics()
    if getattr(args, "json", False):
        print(__import__("json").dumps(report, indent=2, default=str))
    else:
        print_diagnostics(report)
    if getattr(args, "harness", False):
        _print_harness_health()
    raise SystemExit(0 if report["overall"]["ok"] else 1)


def _configure_engine(subparsers) -> None:
    engine_parser = subparsers.add_parser(
        "engine", help="Local AI engine: NPU/GPU detection, NoLlama install/serve, model downloads"
    )
    engine_sub = engine_parser.add_subparsers(dest="engine_action")
    engine_sub.add_parser("status", help="Detected hardware, routing plan and engine health")
    engine_sub.add_parser("install", help="Install the NoLlama server (no model weights)")
    engine_start = engine_sub.add_parser("start", help="Start the local engine")
    engine_start.add_argument("--device", default="", help="NPU | GPU | CPU (default: auto-detect)")
    engine_start.add_argument("--model-dir", default="", help="Model directory to serve")
    engine_sub.add_parser("stop", help="Stop the local engine")
    engine_sub.add_parser("models", help="List catalog + models already on disk")
    engine_dl = engine_sub.add_parser("download", help="Download a model (default: minicpm)")
    engine_dl.add_argument("model", nargs="?", default="minicpm", help="Catalog id (see 'engine models')")
    engine_dl.add_argument("--wait", action="store_true", help="Wait for the download to finish")


def _run_engine(args, ctx: CLIContext) -> None:
    import json as _json

    from core.accelerators import state as engine_state
    from core.nollama import TERMINAL_STATES, nollama_manager

    action = getattr(args, "engine_action", None)
    if action == "status":
        info = engine_state()
        plan = info["plan"]
        hw = plan.get("hardware") or {}
        print(
            f"mode      : {plan.get('mode')}   status: {info.get('status')}"
            + (f"   action: {info['action']}" if info.get("action") else "")
        )
        print(f"NPU       : {', '.join(d['name'] for d in hw.get('npu', [])) or 'none detected'}")
        print(f"GPU       : {', '.join(d['name'] for d in hw.get('gpus', [])) or 'none detected'}")
        for role, assignment in (plan.get("roles") or {}).items():
            print(f"  {role:<11s} {assignment['engine']:<8s} {assignment['device']:<4s} {assignment['model']}")
        for note in plan.get("notes") or []:
            print(f"note      : {note}")
        if info.get("recommended_model"):
            rec = info["recommended_model"]
            print(f"missing   : {rec['name']} (~{rec['est_size_gb']} GB) — hermus engine download {rec['id']}")
        raise SystemExit(0)
    if action == "install":
        result = nollama_manager.install()
        print(_json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("success") else 1)
    if action == "start":
        result = nollama_manager.start(
            device=getattr(args, "device", "") or "",
            model_dir=getattr(args, "model_dir", "") or None,
        )
        print(_json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("success") else 1)
    if action == "stop":
        print(_json.dumps(nollama_manager.stop(), indent=2, default=str))
        raise SystemExit(0)
    if action == "models":
        for row in nollama_manager.list_catalog():
            flag = "installed" if row["installed"] else f"~{row['est_size_gb']} GB"
            print(f"  {row['id']:<16s} {flag:<12s} {row['name']}  [{','.join(row['devices'])}]")
        raise SystemExit(0)
    if action == "download":
        started = nollama_manager.download_model(getattr(args, "model", "minicpm") or "minicpm")
        if not started.get("success"):
            print(_json.dumps(started, indent=2, default=str))
            raise SystemExit(1)
        job = started["job"]
        if not getattr(args, "wait", False):
            print(_json.dumps(job, indent=2, default=str))
            raise SystemExit(0)
        import time as _time

        while True:
            job = nollama_manager.download_status(job["id"]) or job
            print(f"\r  {job['model_id']:<16s} {job['state']:<12s} {job['percent']:>5.1f}%", end="", flush=True)
            if job["state"] in TERMINAL_STATES:
                break
            _time.sleep(2)
        print()
        if job.get("error"):
            print(f"error: {job['error']}")
        raise SystemExit(0 if job["state"] == "ready" else 1)
    no_action(ctx, "engine")
    raise SystemExit(2)


def _configure_cron(subparsers) -> None:
    cron_parser = subparsers.add_parser("cron", help="Scheduler - natural language + one-shot/recurring")
    cron_sub = cron_parser.add_subparsers(dest="cron_action")
    cron_add = cron_sub.add_parser("add", help="Add a scheduled task")
    cron_add.add_argument("text", help="Schedule, e.g. 'tomorrow at 9am' or 'every weekday at 9am'")
    cron_add.add_argument("--task", help="Task to execute (defaults to text)")
    cron_add.add_argument("--platform", default="cli")
    cron_add.add_argument("--user-id", default="default")
    cron_add.add_argument("--timezone", default=None, help="IANA timezone, e.g. Asia/Kolkata")
    cron_add.add_argument("--max-runs", type=int, default=None)
    cron_add.add_argument("--quiet-start", type=int, default=None, help="Quiet-hour start, 0-23")
    cron_add.add_argument("--quiet-end", type=int, default=None, help="Quiet-hour end, 0-23")
    cron_sub.add_parser("list", help="List scheduled jobs")
    cron_sub.add_parser("status", help="Show scheduler status")
    cron_enable = cron_sub.add_parser("enable", help="Enable/disable a schedule")
    cron_enable.add_argument("job_id")
    cron_enable.add_argument("--off", action="store_true")
    cron_remove = cron_sub.add_parser("remove", help="Remove a schedule")
    cron_remove.add_argument("job_id")


def _run_cron(args, ctx: CLIContext) -> None:
    from scheduler.cron import cron_manager

    if args.cron_action == "add":
        quiet = None
        if args.quiet_start is not None or args.quiet_end is not None:
            if args.quiet_start is None or args.quiet_end is None:
                print("quiet-start and quiet-end must be provided together")
                raise SystemExit(2)
            quiet = (args.quiet_start, args.quiet_end)
        job = cron_manager.add_job(
            args.text,
            task=args.task,
            platform=args.platform,
            user_id=args.user_id,
            timezone=args.timezone,
            max_runs=args.max_runs,
            respect_quiet_hours=quiet is not None,
            quiet_hours=quiet,
        )
        next_run = job.get("next_run_at") or "-"
        print(f"Scheduled: {job['id']} | type={job['schedule_type']} | next={next_run} | task={job['task']}")
    elif args.cron_action == "list":
        jobs = cron_manager.list_jobs()
        print(f"Schedules ({len(jobs)}):")
        for j in jobs:
            print(f" - {j['id']}: {j.get('schedule_type')} | {j.get('cron') or j.get('run_at')} | enabled={j.get('enabled')}")
    elif args.cron_action == "status":
        print(json.dumps(cron_manager.status(), indent=2, default=str))
    elif args.cron_action == "enable":
        ok = cron_manager.set_enabled(args.job_id, not args.off)
        print(f"Schedule {args.job_id}: {ok}")
    elif args.cron_action == "remove":
        ok = cron_manager.remove_job(args.job_id)
        print(f"Removed {args.job_id}: {ok}")
    else:
        no_action(ctx, "cron")


def _configure_update(subparsers) -> None:
    update_parser = subparsers.add_parser(
        "update", help="Update from GitHub - shows update in dashboard and CLI too - like hermes update"
    )
    update_parser.add_argument("--check", action="store_true", help="Only check for updates, don't pull")


def _run_update(args, ctx: CLIContext) -> None:
    from core.updater import get_updater_for_current_repo

    updater = get_updater_for_current_repo()
    if args.check:
        print("🔍 Checking for updates from GitHub...")
        result = updater.check_for_updates()
        if result.get("update_available"):
            print(f"\n🚀 Update available! {result.get('message')}")
            print(f"Local: {result.get('local', {}).get('short')} - {result.get('local', {}).get('message', '')[:80]}")
            print(
                f"Remote: {result.get('remote', {}).get('short')} - {result.get('remote', {}).get('message', '')[:80]} by {result.get('remote', {}).get('author', '')} on {result.get('remote', {}).get('date', '')[:10]}"
            )
            print(f"Behind by: {result.get('behind_by', 1)} commit(s)")
            print(f"Remote URL: {result.get('remote_url', '')}")
            print("\nTo update: Run 'hermus update' without --check or 'git pull' - shows in dashboard and CLI")
            print("Dashboard will show banner if update available at http://localhost:8000/control")
        elif result.get("up_to_date"):
            print(f"\n✅ Up to date! {result.get('message')}")
        else:
            print(f"\nUpdate check result: {result}")
    else:
        print("🔄 Updating from GitHub via git pull origin main + pip install -r requirements.txt...")
        result = updater.update()
        if result.get("success"):
            print(f"\n✅ Update success! {result.get('message')}")
            print(
                f"New commit: {result.get('new_commit', {}).get('short')} - {result.get('new_commit', {}).get('message', '')[:80]}"
            )
            print("Pull output:", result.get("pull_stdout", "")[:500])
            print("\nDashboard and CLI will now show up to date - refresh dashboard to see new version")
        else:
            print(f"\n❌ Update failed: {result.get('error', result.get('pull_stderr', ''))[:500]}")


def _configure_jobs(subparsers) -> None:
    jobs_parser = subparsers.add_parser("jobs", help="Inspect the gateway job queue (data/jobs/)")
    jobs_sub = jobs_parser.add_subparsers(dest="jobs_action")
    jobs_list = jobs_sub.add_parser("list", help="Recent jobs from the durable log")
    jobs_list.add_argument("--limit", type=int, default=20)
    jobs_list.add_argument("--status", default=None, help="queued|running|succeeded|failed|cancelled|interrupted")
    jobs_list.add_argument("--log", default=None, help="read another jobs.jsonl (e.g. another instance)")
    jobs_status = jobs_sub.add_parser("status", help="One job's status + result")
    jobs_status.add_argument("job_id")
    jobs_show = jobs_sub.add_parser("events", help="Replay a job's run events")
    jobs_show.add_argument("job_id")


def _run_jobs(args, ctx: CLIContext) -> None:
    from gateway.queue import job_queue

    if args.jobs_action == "list":
        log = getattr(args, "log", None)
        if log:
            rows = job_queue.read_log(log, 500)
        else:
            rows = job_queue.recent_jobs(max(args.limit * 4, 40))
        state = getattr(args, "status", None)
        if state:
            rows = [r for r in rows if r.get("status") == state]
        rows = rows[: args.limit]
        if not rows:
            print(f"no jobs in {log or job_queue.persist_path} yet (is the gateway running?)")
        for row in rows:
            stamp = row.get("created") or row.get("ts") or row.get("finished") or ""
            print(
                f" {str(stamp)[:19]:19s} {str(row.get('job_id') or row.get('id', ''))[:16]:18s} "
                f"{str(row.get('kind', ''))[:18]:18s} {str(row.get('status', ''))[:10]:10s} "
                f"{str(row.get('duration_ms') or 0):>6}ms "
                f"{str(row.get('error') or row.get('result_brief') or '')[:56]}"
            )
    elif args.jobs_action == "status":
        print(__import__("json").dumps(job_queue.status(args.job_id), indent=2, default=str))
        res = job_queue.result(args.job_id)
        if res is not None:
            print("--- result ---")
            print(__import__("json").dumps(res, indent=2, default=str)[:3000])
    elif args.jobs_action == "events":
        for e in job_queue.events(args.job_id):
            print(
                f" #{e.get('id')} {e.get('ts', '')[:19]} {e.get('type'):22s} "
                f"{__import__('json').dumps(e.get('data'), default=str)[:110]}"
            )
    else:
        no_action(ctx, "jobs")


def _configure_watchdog(subparsers) -> None:
    watchdog_parser = subparsers.add_parser("watchdog", help="Self-healing watchdog - classify/repair errors")
    watchdog_parser.add_argument("error", nargs="?", default="", help="Error text to classify/repair")


def _run_watchdog(args, ctx: CLIContext) -> None:
    from core.watchdog import watchdog as wd

    err = args.error or "JSONDecodeError: expecting value"
    r = wd.handle(err)
    print(f"Known={r['known']} action={r['action']} ok={r.get('ok')} fix={r.get('fix', '')}")


def _configure_artifacts(subparsers) -> None:
    art_parser = subparsers.add_parser("artifacts", help="Artifact-Centric Workspace Explorer")
    art_sub = art_parser.add_subparsers(dest="artifact_action")
    art_list = art_sub.add_parser("list", help="List registered artifacts")
    art_list.add_argument("--mission", default=None, help="Filter by mission ID")
    art_export = art_sub.add_parser("export", help="Export artifacts to ZIP bundle")
    art_export.add_argument("output_zip", help="Destination ZIP file")
    art_export.add_argument("--mission", default=None)


def _run_artifacts(args, ctx: CLIContext) -> None:
    from core.artifact_manager import artifact_manager

    if args.artifact_action == "list":
        arts = artifact_manager.list_artifacts(mission_id=args.mission)
        print(f"Artifacts ({len(arts)}):")
        for a in arts:
            print(f" - [{a.artifact_type}] {a.name} ({a.size_bytes} B) -> {a.path}")
    elif args.artifact_action == "export":
        p = artifact_manager.export_bundle(args.output_zip, mission_id=args.mission)
        print(f"Exported bundle to: {p}")
    else:
        no_action(ctx, "artifacts")


def _configure_sentinel(subparsers) -> None:
    sp = subparsers.add_parser("sentinel", help="Always-alert core - reacts to events instantly (FAIRY side), never idles")
    sub = sp.add_subparsers(dest="sentinel_action")
    run_p = sub.add_parser("run", help="Run the sentinel daemon, inline on the canonical event bus")
    run_p.add_argument("--model", default="auto", help="Model for LLM triage: auto | role | provider/model | mock/mock")
    run_p.add_argument("--once", action="store_true", help="Process recent events once and exit")
    run_p.add_argument("--dry-run", action="store_true", help="Decide and record, but do not enqueue/notify")
    run_p.add_argument("--consolidate-seconds", type=float, default=None, help="Event->memory digest interval")
    test_p = sub.add_parser("test", help="Fire one synthetic event and show the decision")
    test_p.add_argument(
        "event", help='Event JSON, e.g. \'{"type": "incident", "command": "x", "args": {"severity": "critical"}}\''
    )
    test_p.add_argument("--model", default=None, help="Optional LLM triage model spec")
    sub.add_parser("status", help="Thresholds, bus cursor, recent decisions")
    sub.add_parser("install", help="Write an autostart entry (systemd user unit / Termux boot) so the sentinel survives reboots")
    sub.add_parser("alerts", help="Recent local alerts (data/sentinel/alerts.jsonl)")
    cons_p = sub.add_parser("consolidate", help="One-shot event->memory digest (learning pass)")
    cons_p.add_argument("--deep", action="store_true", help="Also write recurring failures as lessons (the night shift)")
    ns_p = sub.add_parser("nightshift", help="Register the nightly deep-consolidation cron job")
    ns_p.add_argument("--at", default="3:00 am", help="Schedule, natural language, e.g. '3:00 am'")
    ns_p.add_argument("--remove", action="store_true", help="Remove the night-shift cron job instead")
    sub.add_parser("rules", help="List sentinel rule proposals + existing proactive rules")
    appr = sub.add_parser("approve", help="Approve a sentinel rule proposal (enables a durable proactive rule)")
    appr.add_argument("proposal_id")


def _run_sentinel(args, ctx: CLIContext) -> None:
    import json as _json

    from core.config import config
    from core.sentinel import Sentinel, build_llm, default_recorder, envelope_from_dict, thresholds

    action = args.sentinel_action
    if action == "run":
        from core.events import get_bus

        bus = get_bus()
        llm, res = build_llm(args.model)
        print(f"triage model: {res.get('ref') or 'none'} — {str(res.get('note', ''))[:90]}")
        dry = {
            "enqueue": lambda k, p: {"ok": True, "dry_run": True, "kind": k},
            "notify": lambda t, s: {"ok": True, "dry_run": True},
        }
        s = Sentinel(
            bus=bus,
            enqueue=None if not args.dry_run else dry["enqueue"],
            notify=None if not args.dry_run else dry["notify"],
            llm=llm,
            model=res.get("ref"),
            use_llm_triage=bool(getattr(config, "sentinel_llm_triage_enabled", True)),
            recorder=default_recorder(config.resolve_path("data/sentinel/decisions.jsonl")),
        )
        if args.once:
            s.subscribe()
            recent = bus.recent(limit=100)
            for env in recent:
                s.handle_event(env)
            acts = {}
            for d in s.decisions:
                acts[d["action"]] = acts.get(d["action"], 0) + 1
            print(f"processed {len(s.decisions)}/{len(recent)} recent events -> {acts}")
            return
        print("sentinel running — reacting inline to every event (Ctrl+C to stop)")
        try:
            s.serve(consolidate_every_s=args.consolidate_seconds)
        except KeyboardInterrupt:
            print("\nsentinel stopped")
        return
    if action == "test":
        ev = _json.loads(args.event)
        llm, res = build_llm(args.model) if getattr(args, "model", None) else (None, {})
        s = Sentinel(
            bus=None,
            enqueue=lambda k, p: {"ok": True, "dry_run": True, "kind": k, "payload": p},
            notify=lambda t, sev: {"ok": True, "dry_run": True, "text": t, "severity": sev},
            llm=llm,
            model=res.get("ref") if res else None,
        )
        d = s.handle_event(envelope_from_dict(ev))
        print(_json.dumps(d, indent=2, default=str))
        return
    if action == "status":
        th = thresholds()
        print("Sentinel thresholds: " + ", ".join(f"{k}={v}" for k, v in th.items()))
        try:
            from core.events import get_bus

            print(f"event bus cursor: {get_bus().cursor}")
        except Exception as e:
            print(f"event bus: unavailable ({e})")
        p = config.resolve_path("data/sentinel/decisions.jsonl")
        if p.is_file():
            rows = [r for r in p.read_text().splitlines() if r.strip()][-10:]
            print(f"last {len(rows)} decisions ({p}):")
            for r in rows:
                try:
                    d = _json.loads(r)
                    print(f" - {d.get('ts')} {d.get('action')} salience={d.get('salience')} type={d.get('type')}")
                except Exception:
                    print(f" - {r[:120]}")
        else:
            print("no decisions recorded yet (run: hermus sentinel run)")
        return
    if action == "install":
        import os
        import sys

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        entry = f'"{sys.executable}" "{root}/hermus.py" sentinel run --model auto'
        if os.name == "nt":
            ps1 = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "hermus-sentinel-autostart.ps1")
            with open(ps1, "w", encoding="utf-8") as f:
                f.write(
                    f'Start-Process -WindowStyle Hidden -FilePath {sys.executable!r} -ArgumentList @("{root}/hermus.py", "sentinel", "run", "--model", "auto")\n'
                )
            print(f"wrote {ps1}")
            print("register with Task Scheduler (run once, elevated):")
            print(
                f'  schtasks /create /tn "HERMUS Sentinel" /sc onlogon /tr "powershell -WindowStyle Hidden -File {ps1}" /rl highest'
            )
        elif os.path.exists("/run/systemd/system"):
            unit_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(unit_dir, exist_ok=True)
            unit = os.path.join(unit_dir, "hermus-sentinel.service")
            with open(unit, "w", encoding="utf-8") as f:
                f.write(
                    "[Unit]\nDescription=HERMUS sentinel (always-alert core)\nAfter=network-online.target\n\n"
                    "[Service]\nType=simple\nExecStart=" + entry + "\nRestart=always\nRestartSec=5\n\n"
                    "[Install]\nWantedBy=default.target\n"
                )
            print(f"wrote {unit}")
            print("enable with:  systemctl --user enable --now hermus-sentinel")
        elif "com.termux" in os.environ.get("PREFIX", ""):
            boot = os.path.expanduser("~/.termux/boot")
            os.makedirs(boot, exist_ok=True)
            script = os.path.join(boot, "hermus-sentinel")
            with open(script, "w", encoding="utf-8") as f:
                f.write("#!/data/data/com.termux/files/usr/bin/sh\n" + entry + " &\n")
            os.chmod(script, 0o755)
            print(f"wrote {script} (runs on Termux boot)")
        else:
            print("no systemd/Termux detected — add this to your crontab for autostart:")
            print(f"@reboot {entry}")
        return
    if action == "alerts":
        p = config.resolve_path("data/sentinel/alerts.jsonl")
        rows = [r for r in p.read_text().splitlines() if r.strip()] if p.is_file() else []
        print(f"Alerts ({len(rows)}): {p}")
        for r in rows[-20:]:
            print(" - " + r[:200])
        return
    if action == "consolidate":
        from core.events import get_bus
        from core.sentinel import Sentinel, build_llm

        s = Sentinel(bus=get_bus(), llm=build_llm("auto")[0])
        d = s.consolidate(deep=bool(getattr(args, "deep", False)))
        print(_json.dumps(d, indent=2, default=str))
        return
    if action == "nightshift":
        from scheduler.cron import cron_manager

        existing = [j for j in cron_manager.list_jobs() if "sentinel consolidate" in str(j.get("task", ""))]
        if getattr(args, "remove", False):
            removed = sum(1 for j in existing if cron_manager.remove_job(j["id"]))
            print(f"removed {removed} night-shift job(s)")
            return
        if existing:
            print(f"night-shift job already present: {existing[0]['id']} ({existing[0].get('natural')})")
            return
        job = cron_manager.add_job(
            f"every day at {args.at}",
            task="hermus sentinel consolidate --deep",
            platform="automation",
            user_id="sentinel",
            respect_quiet_hours=True,
        )
        print(f"registered night-shift job {job['id']}: every day at {args.at} -> hermus sentinel consolidate --deep")
        return
    if action == "rules":
        from core.proactive import ProactiveAutomation
        from core.sentinel import list_proposals

        props = list_proposals()
        print(f"Sentinel rule proposals ({len(props)}):")
        for pr in props:
            print(
                f" - {pr['id']} | pattern={pr['pattern']} seen {pr.get('count')}x | approved={pr.get('approved')}"
                f" | enable: hermus sentinel approve {pr['id']}"
            )
        rules = ProactiveAutomation().list_rules()
        print(f"Proactive rules ({len(rules)}):")
        for r in rules:
            print(f" - {r['id']} | {r['event_type']} -> {r['action_type']} | enabled={r['enabled']} | {r['name']}")
        return
    if action == "approve":
        from core.sentinel import approve_proposal

        out = approve_proposal(args.proposal_id)
        print(_json.dumps(out, indent=2, default=str))
        if not out.get("ok"):
            raise SystemExit(2)
        return
    no_action(ctx, "sentinel")


def _configure_rollback(subparsers) -> None:
    rb_parser = subparsers.add_parser("rollback", help="Transactional Rollback & Checkpoint Manager")
    rb_sub = rb_parser.add_subparsers(dest="rollback_action")
    rb_chk = rb_sub.add_parser("checkpoint", help="Create a workspace snapshot checkpoint")
    rb_chk.add_argument("label", help="Checkpoint description/label")
    rb_res = rb_sub.add_parser("restore", help="Restore workspace to checkpoint state")
    rb_res.add_argument("checkpoint_id", help="Checkpoint ID")
    rb_diff = rb_sub.add_parser("diff", help="Compare workspace state against checkpoint")
    rb_diff.add_argument("checkpoint_id")
    rb_sub.add_parser("list", help="List saved checkpoints")


def _run_rollback(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.rollback import rollback_manager

    if args.rollback_action == "checkpoint":
        cp = rollback_manager.checkpoint(label=args.label)
        print(f"Checkpoint created: {cp.id} ('{cp.label}')")
    elif args.rollback_action == "restore":
        res = rollback_manager.restore(args.checkpoint_id)
        print(json_lib.dumps(res, indent=2))
    elif args.rollback_action == "diff":
        res = rollback_manager.diff(args.checkpoint_id)
        print(json_lib.dumps(res, indent=2))
    elif args.rollback_action == "list":
        cps = rollback_manager.list_checkpoints()
        print(f"Checkpoints ({len(cps)}):")
        for c in cps:
            print(f" - {c.id} [{c.timestamp}] {c.label} ({len(c.files)} files)")
    else:
        no_action(ctx, "rollback")


def _configure_harness(subparsers) -> None:
    harness_parser = subparsers.add_parser("harness", help="Agent harness - sessions, swarm bus, file-shift, compaction")
    harness_sub = harness_parser.add_subparsers(dest="harness_action")
    harness_sub.add_parser("sessions", help="List server-owned sessions")
    h_attach = harness_sub.add_parser("attach", help="Attach a surface to a session")
    h_attach.add_argument("session_id")
    h_detach = harness_sub.add_parser("detach", help="Detach a surface from a session")
    h_detach.add_argument("session_id")
    h_msg = harness_sub.add_parser("send", help="Send a swarm message")
    h_msg.add_argument("body")
    h_msg.add_argument("--from", dest="sender", default="cli")
    h_msg.add_argument("--to", default="")
    h_msg.add_argument("--channel", default="")
    h_msg.add_argument("--kind", default="broadcast", choices=["dm", "broadcast", "channel"])
    h_inbox = harness_sub.add_parser("inbox", help="Read swarm inbox")
    h_inbox.add_argument("session_id")
    h_spawn = harness_sub.add_parser("spawn", help="Register swarm workers (no LLM)")
    h_spawn.add_argument("task")
    h_spawn.add_argument("--parent", default="cli")
    h_spawn.add_argument("--count", type=int, default=2)
    h_recall = harness_sub.add_parser("recall", help="Cascade memory recall")
    h_recall.add_argument("query")
    # --- eval harness: run suites against the agent, verify, report ---
    h_run = harness_sub.add_parser("run", help="Run a task suite against the agent and verify results")
    h_run.add_argument(
        "suite",
        nargs="?",
        default=None,
        help="Builtin suite name (e.g. smoke, fairy) or path to a suite JSON file — omit to run ALL builtin suites",
    )
    h_run.add_argument(
        "--model",
        default=None,
        help="Any configured model: 'auto' (default), a role (coding, reasoning, ...), 'provider/model', a bare provider (groq, ollama), or mock/mock (offline)",
    )
    h_run.add_argument(
        "--models", default="", help="Comma-separated model specs — run the whole suite once per model and compare"
    )
    h_run.add_argument("--limit", type=int, default=None, help="Run only the first N tasks")
    h_run.add_argument("--only", default="", help="Comma-separated task ids to run")
    h_run.add_argument("--max-steps", type=int, default=None, help="Agent step budget per task")
    h_run.add_argument("--timeout", type=float, default=300.0, help="Wall-clock seconds per task")
    h_run.add_argument("--tag", default="", help="Label for this run (report/history)")
    h_run.add_argument("--out", default=None, help="Directory for run artifacts (default data/harness/runs)")
    h_run.add_argument("--no-keep", action="store_true", help="Delete task workspaces after the run")
    h_run.add_argument("--no-ping", action="store_true", help="Skip the pre-run model ping")
    h_run.add_argument(
        "--isolated", action="store_true", help="Run each agent task in its own subprocess (own cwd — safe to parallelize)"
    )
    h_run.add_argument("--workers", type=int, default=1, help="Run up to N tasks in parallel (agent tasks need --isolated)")
    h_run.add_argument(
        "--max-tokens", type=int, default=None, help="Token budget (estimated); run is flagged over-budget if exceeded"
    )
    h_run.add_argument("--strict", action="store_true", help="Exit 1 if any task failed or the token budget was exceeded")
    h_run.add_argument("--json", action="store_true", help="Print the full JSON report")
    h_models = harness_sub.add_parser(
        "models", help="List every model the runtime can see (API-key/auth providers + local runtimes + mock)"
    )
    h_models.add_argument("--probe", action="store_true", help="Live-probe providers (network; lists real remote model ids)")
    h_models.add_argument("--json", action="store_true")
    h_ping = harness_sub.add_parser("ping", help="One-shot live check that a model answers")
    h_ping.add_argument("model", nargs="?", default="auto", help="Model spec (default: auto)")
    h_ping.add_argument("--timeout", type=float, default=30.0)
    h_ping.add_argument("--json", action="store_true")
    h_suites = harness_sub.add_parser("suites", help="List builtin task suites (--stats adds run history)")
    h_suites.add_argument("--stats", action="store_true", help="Per-suite runs, avg success rate, flaky tasks")
    h_tasks = harness_sub.add_parser("tasks", help="List tasks in a suite")
    h_tasks.add_argument("suite")
    h_tasks.add_argument("--json", action="store_true")
    h_runs = harness_sub.add_parser("runs", help="List past harness runs")
    h_runs.add_argument("--limit", type=int, default=20)
    h_runs.add_argument("--out", default=None, help="Runs directory (if not the default data/harness/runs)")
    h_runs.add_argument("--suite", default="", help="Only runs of this suite")
    h_runs.add_argument("--model", default="", help="Only runs of this model ref")
    h_show = harness_sub.add_parser("show", help="Show one run's summary + per-task results")
    h_show.add_argument("run_id")
    h_show.add_argument("--out", default=None)
    h_show.add_argument("--json", action="store_true")
    h_report = harness_sub.add_parser("report", help="Print a run's markdown report")
    h_report.add_argument("run_id")
    h_report.add_argument("--out", default=None)
    h_cmp = harness_sub.add_parser("compare", help="Compare two runs (A/B)")
    h_sched = harness_sub.add_parser("schedule", help="Register/remove the nightly product-eval cron")
    h_sched.add_argument("--suite", default="fairy", help="Suite to eval nightly (default fairy)")
    h_sched.add_argument("--model", default="auto", help="Model spec for the nightly run (default auto)")
    h_sched.add_argument("--at", default="4:00 am", help="Schedule, natural language")
    h_sched.add_argument("--remove", action="store_true", help="Remove the nightly eval job instead")
    h_cmp.add_argument("run_a")
    h_cmp.add_argument("run_b")
    h_cmp.add_argument("--out", default=None)
    h_cmp.add_argument("--json", action="store_true")


def _safe_model_tag(spec: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(spec))


def _harness_progress(i: int, n: int, r: dict) -> None:
    icon = "PASS" if r.get("success") else f"FAIL({r.get('status')})"
    print(
        f"  [{i}/{n}] {r.get('id')}: {icon} "
        f"checks={r.get('checks_passed', 0)}/{r.get('checks_total', 0)} "
        f"steps={r.get('steps', 0)} {r.get('duration_ms', 0)}ms"
    )


def _run_harness(args, ctx: CLIContext) -> None:
    import json as _json

    from core.harness import bus, sessions
    from core.harness.memory_graph import cascade_recall
    from core.harness.swarm import spawn as swarm_spawn

    if args.harness_action == "run":
        from core.harness.runner import HarnessRunner, RunOptions
        from core.harness.tasks import SuiteError

        runner = HarnessRunner(runs_dir=args.out) if args.out else HarnessRunner()
        specs = [s.strip() for s in (args.models or "").split(",") if s.strip()] or [args.model or "auto"]
        reports = []
        from core.harness.tasks import list_builtin_suites

        suite_list = [args.suite] if getattr(args, "suite", None) else list_builtin_suites()
        for suite_name in suite_list:
            if len(suite_list) > 1:
                print(f"\n=== suite '{suite_name}' ===")
            for spec in specs:
                if len(specs) > 1:
                    print(f"\n=== model spec '{spec}' ===")
                options = RunOptions(
                    model=spec,
                    limit=args.limit,
                    only=[s for s in (args.only or "").split(",") if s.strip()] or None,
                    max_steps=args.max_steps,
                    timeout_s=args.timeout,
                    tag=(args.tag + "+" if args.tag and len(specs) > 1 else args.tag) + _safe_model_tag(spec)
                    if len(specs) > 1
                    else args.tag,
                    out_dir=args.out,
                    keep_workspace=not args.no_keep,
                    preflight=not args.no_ping,
                    isolated=bool(getattr(args, "isolated", False)),
                    workers=max(1, int(getattr(args, "workers", 1) or 1)),
                    max_tokens_est=getattr(args, "max_tokens", None),
                    on_task_done=_harness_progress,
                )
                try:
                    report = runner.run_suite(suite_name, options)
                except (SuiteError, ValueError) as e:
                    print(f"error: {e}")
                    if len(specs) > 1:
                        continue
                    raise SystemExit(2)
                reports.append(report)
                res = report.get("model_resolution", {})
                pf = report.get("preflight") or {}
                s = report["summary"]
                print(
                    f"\nRun {report['run_id']} — suite '{report['suite']}' model {report['model']} "
                    f"(spec '{report.get('model_spec')}'{'' if res.get('validated', True) else ', unvalidated'}): "
                    f"{s['passed']}/{s['runs']} passed (success rate {s['success_rate']:.1%}) in {s['wall_ms']}ms"
                )
                if pf:
                    print(
                        f"  preflight ping: {'OK' if pf.get('ok') else 'FAILED'} ({pf.get('latency_ms')}ms) {pf.get('error', '')[:120]}"
                    )
                print(f"  report: {report['report_md']}")
                if args.json:
                    print(_json.dumps(report, indent=2, default=str))
                s2 = report["summary"]
                if s2.get("budget_exceeded"):
                    print(
                        f"  token budget exceeded: ~{s2.get('total_tokens_est', 0)} > {s2.get('budget_tokens_est')} (estimated)"
                    )
        if getattr(args, "strict", False):
            bad = [r for r in reports if r["summary"].get("failed", 0) > 0 or r["summary"].get("budget_exceeded")]
            if bad:
                print(f"strict: {len(bad)} run(s) failed or over budget")
                raise SystemExit(1)
        if len(reports) > 1:
            from core.harness.report import render_scoreboard

            print(render_scoreboard(reports))
            print("\n=== model comparison ===")
            print(f"{'model':<40} {'success':>8} {'passed':>7} {'avg steps':>10} {'wall ms':>9}  run_id")
            for r in reports:
                s = r["summary"]
                print(
                    f"{r['model']:<40} {s['success_rate']:>8.1%} {s['passed']:>3}/{s['runs']:<3} "
                    f"{s['avg_steps']:>10} {s['wall_ms']:>9}  {r['run_id']}"
                )
        return
    if args.harness_action == "models":
        from core.harness.models import list_models

        info = list_models(probe=getattr(args, "probe", False))
        if getattr(args, "json", False):
            print(_json.dumps(info, indent=2, default=str))
            return
        print(f"Discovered models ({info['count']}){' [live-probed]' if info['probe'] else ' [stored catalog, offline]'}:")
        for m in info["models"]:
            src = m["source"] + (" (local)" if m.get("local") else "")
            caps = ",".join(sorted(m["capabilities"])[:6]) or "-"
            reach = "" if m.get("reachable") is None else (" reachable" if m["reachable"] else " UNREACHABLE")
            print(
                f" - {m['ref']:<44} provider={m['provider']:<12} source={src:<18} ctx={m.get('context_tokens') or '-'}{reach} caps={caps}"
            )
        print("\nRun one with: hermus harness run smoke --model <ref>   (or: auto | role | bare provider | mock/mock)")
        return
    if args.harness_action == "ping":
        from core.harness.models import ping_model, resolve_model_spec

        res = resolve_model_spec(args.model)
        if not res.get("ok"):
            print(f"error: {res.get('note')}")
            raise SystemExit(2)
        print(f"pinging {res['ref']} (spec '{args.model}') ...")
        out = ping_model(res["ref"], timeout_s=args.timeout)
        if getattr(args, "json", False):
            out = {**out, "resolution": res}
            print(_json.dumps(out, indent=2, default=str))
        else:
            status = "OK" if out["ok"] else "FAILED"
            print(f"  {status} in {out['latency_ms']}ms — reply: {out.get('content', '')[:120]!r}")
            if out.get("error"):
                print(f"  error: {out['error'][:300]}")
        if not out["ok"]:
            raise SystemExit(1)
        return
    if args.harness_action == "suites":
        from core.harness.tasks import list_builtin_suites, suite_info

        names = list_builtin_suites()
        print(f"Builtin suites ({len(names)}):")
        stats = {}
        if getattr(args, "stats", False):
            from core.harness.runner import HarnessRunner

            stats = {s["suite"]: s for s in HarnessRunner().suite_stats()}
        for n in names:
            info = suite_info(n)
            line = f" - {n}: {info['task_count']} tasks — {info['description'][:90]}"
            if n in stats:
                st = stats[n]
                line += f" | runs={st['runs']} avg={st['avg_success_rate']:.0%} last={st['last'].get('success_rate'):.0%}" + (
                    f" flaky={','.join(st['flaky_tasks'])}" if st["flaky_tasks"] else ""
                )
            print(line)
        return
    if args.harness_action == "tasks":
        from core.harness.tasks import SuiteError, suite_info

        try:
            info = suite_info(args.suite)
        except SuiteError as e:
            print(f"error: {e}")
            raise SystemExit(2)
        if getattr(args, "json", False):
            print(_json.dumps(info, indent=2, default=str))
        else:
            print(f"Suite '{info['name']}' ({info['task_count']} tasks) — {info['description']}")
            for t in info["tasks"]:
                print(
                    f" - {t['id']} [{t['category']}] checks={t['checks']} files={t['files']} setup={t['setup']} :: {t['prompt'][:80]}"
                )
        return
    if args.harness_action == "runs":
        from core.harness.runner import HarnessRunner

        runner = HarnessRunner()
        runs = runner.list_runs(limit=100000)
        if getattr(args, "suite", ""):
            runs = [r for r in runs if r.get("suite") == args.suite]
        if getattr(args, "model", ""):
            runs = [r for r in runs if r.get("model") == args.model]
        runs = runs[-args.limit :]
        print(f"Harness runs ({len(runs)}):")
        for r in reversed(runs):
            print(
                f" - {r.get('run_id')} | suite={r.get('suite')} model={r.get('model')} "
                f"| {r.get('passed')}/{r.get('runs')} ({r.get('success_rate')}) | tokens~{r.get('total_tokens_est', 0)} "
                f"{'REGRESSED ' if r.get('regression') else ''}| tag={r.get('tag') or '-'} | {r.get('timestamp')}"
            )
        return
    if args.harness_action == "show":
        from core.harness.runner import HarnessRunner

        runner = HarnessRunner()
        report = runner.load_run(args.run_id)
        if report is None:
            print(f"error: run '{args.run_id}' not found")
            raise SystemExit(2)
        if getattr(args, "json", False):
            print(_json.dumps(report, indent=2, default=str))
            return
        s = report.get("summary", {})
        print(
            f"Run {report.get('run_id')} — suite '{report.get('suite')}' model {report.get('model')} tag={report.get('tag') or '-'}"
        )
        print(
            f"  {s.get('passed')}/{s.get('runs')} passed (rate {s.get('success_rate')}), avg steps {s.get('avg_steps')}, wall {s.get('wall_ms')}ms"
        )
        for t in report.get("tasks", []):
            icon = "PASS" if t.get("success") else "FAIL"
            print(
                f"  - {t.get('id')} [{t.get('category')}] {icon} ({t.get('status')}) checks={t.get('checks_passed')}/{t.get('checks_total')} steps={t.get('steps')}"
            )
            if t.get("error"):
                print(f"      error: {str(t['error'])[:200]}")
        return
    if args.harness_action == "report":
        from core.harness.report import render_markdown
        from core.harness.runner import HarnessRunner

        runner = HarnessRunner()
        report = runner.load_run(args.run_id)
        if report is None:
            print(f"error: run '{args.run_id}' not found")
            raise SystemExit(2)
        print(render_markdown(report))
        return
    if args.harness_action == "compare":
        from core.harness.runner import HarnessRunner

        runner = HarnessRunner(runs_dir=args.out) if args.out else HarnessRunner()
        try:
            comp = runner.compare(args.run_a, args.run_b)
        except ValueError as e:
            print(f"error: {e}")
            raise SystemExit(2)
        if getattr(args, "json", False):
            print(_json.dumps(comp, indent=2, default=str))
        else:
            print(runner.compare_markdown(args.run_a, args.run_b))
        return
    if args.harness_action == "schedule":
        from scheduler.cron import cron_manager

        cmd = f"hermus harness run {args.suite} --model {args.model} --strict --tag nightly"
        existing = [
            j
            for j in cron_manager.list_jobs()
            if "harness run" in str(j.get("task", "")) and args.suite in str(j.get("task", ""))
        ]
        if getattr(args, "remove", False):
            removed = sum(1 for j in existing if cron_manager.remove_job(j["id"]))
            print(f"removed {removed} nightly-eval job(s) for suite '{args.suite}'")
            return
        if existing:
            print(f"nightly eval already present for '{args.suite}': {existing[0]['id']} ({existing[0].get('natural')})")
            return
        job = cron_manager.add_job(
            f"every day at {args.at}",
            task=cmd,
            platform="automation",
            user_id="harness",
            respect_quiet_hours=True,
        )
        print(f"registered nightly eval {job['id']}: every day at {args.at} -> {cmd}")
        return
    if args.harness_action == "sessions":
        items = sessions.list_sessions()
        print(f"Sessions ({len(items)}):")
        for s in items:
            print(f" - {s.get('id')} | {s.get('status')} | role={s.get('role')} | {str(s.get('task') or '')[:70]}")
    elif args.harness_action == "attach":
        print(_json.dumps(sessions.attach(args.session_id), indent=2))
    elif args.harness_action == "detach":
        print(_json.dumps(sessions.detach(args.session_id), indent=2))
    elif args.harness_action == "send":
        kind = args.kind if not args.channel else "channel"
        print(
            _json.dumps(bus.send(args.body, args.sender, to=args.to or None, channel=args.channel or None, kind=kind), indent=2)
        )
    elif args.harness_action == "inbox":
        print(_json.dumps(bus.inbox(args.session_id), indent=2, default=str))
    elif args.harness_action == "spawn":
        print(_json.dumps(swarm_spawn(args.task, args.parent, count=args.count), indent=2, default=str))
    elif args.harness_action == "recall":
        print(_json.dumps(cascade_recall(args.query), indent=2, default=str))
    else:
        no_action(ctx, "harness")


CHECK_SUITES = ("sentinel", "watchdog", "ambient")


def _print_harness_health() -> None:
    """Always-on / harness section of `hermus doctor --harness`."""
    from core.harness.runner import HarnessRunner
    from core.harness.tasks import list_builtin_suites
    from core.sentinel import list_proposals, thresholds

    print("\n== always-on / harness ==")
    print(f"  suites: {', '.join(list_builtin_suites())}")
    print(f"  sentinel thresholds: {thresholds()} | proposals pending approval: {len(list_proposals())}")
    runner = HarnessRunner()
    for name in CHECK_SUITES:
        last = runner.latest_run(name)
        if last:
            print(
                f"  last {name} run: {last.get('passed')}/{last.get('runs')} ({float(last.get('success_rate', 0)):.0%}) {last.get('run_id')}"
            )
        else:
            print(f"  last {name} run: none yet — run `hermus check`")


def _configure_check(subparsers) -> None:
    p = subparsers.add_parser(
        "check",
        help="One-command offline selfcheck: env sanity + always-on suites on mock/mock, scoreboard, exit 1 on any failure",
    )
    p.add_argument("--suite", default=",".join(CHECK_SUITES), help="Comma-separated suites, or 'all' for every builtin suite")
    p.add_argument("--model", default="mock/mock", help="Model spec (default mock/mock — fully offline, no keys)")
    p.add_argument("--timeout", type=float, default=120.0, help="Seconds per task")
    p.add_argument("--out", default=None, help="Runs directory override (default data/harness/runs)")
    p.add_argument("--no-env", action="store_true", help="Skip the environment checklist")
    p.add_argument("--no-ping", action="store_true", help="Skip the model preflight ping")


def _run_check(args, ctx: CLIContext) -> None:
    import shutil
    import sys

    from core.config import config

    env_ok = True
    if not getattr(args, "no_env", False):
        print("== environment ==")
        fatal = []
        fatal.append(("python >= 3.11", sys.version_info >= (3, 11)))
        try:
            from core.harness.models import list_models

            n = len(list_models(probe=False))
            fatal.append((f"model catalog ({n} entries incl. mock/mock)", n > 0))
        except Exception as e:
            fatal.append((f"model catalog ({type(e).__name__}: {e})", False))
        try:
            from core.sentinel import thresholds

            fatal.append((f"sentinel thresholds {thresholds()}", True))
        except Exception as e:
            fatal.append((f"sentinel ({type(e).__name__}: {e})", False))
        try:
            from core.events import get_bus

            fatal.append((f"event bus cursor={get_bus().cursor}", True))
        except Exception as e:
            fatal.append((f"event bus ({type(e).__name__}: {e})", False))
        cron_dir = config.resolve_path("data/cron_jobs.json").parent
        try:
            cron_dir.mkdir(parents=True, exist_ok=True)
            fatal.append((f"cron store writable ({cron_dir})", True))
        except Exception as e:
            fatal.append((f"cron store ({e})", False))
        for label, good in fatal:
            print(f"  {'✓' if good else '✗'} {label}")
            env_ok = env_ok and good
        print(f"  · notify-send available: {bool(shutil.which('notify-send'))} (optional desktop alerts)")

    from core.harness.report import render_scoreboard
    from core.harness.runner import HarnessRunner, RunOptions
    from core.harness.tasks import list_builtin_suites

    names = list_builtin_suites() if args.suite == "all" else [s.strip() for s in args.suite.split(",") if s.strip()]
    runner = HarnessRunner(runs_dir=args.out) if args.out else HarnessRunner()
    print(f"\n== suites ({len(names)}): {', '.join(names)} | model {args.model} ==")
    reports = []
    for name in names:
        rep = runner.run_suite(
            name, RunOptions(model=args.model, timeout_s=args.timeout, tag="check", out_dir=args.out, preflight=not args.no_ping)
        )
        reports.append(rep)
        s = rep["summary"]
        line = f"  {name}: {s['passed']}/{s['runs']} ({s['success_rate']:.0%})"
        if s.get("regression"):
            line += "  REGRESSED"
        print(line)
    print(render_scoreboard(reports))
    bad = [r for r in reports if r["summary"].get("failed", 0) > 0 or r["summary"].get("regression")]
    raise SystemExit(1 if (not env_ok) or bad else 0)


COMMANDS: tuple[Command, ...] = (
    Command(
        name="gateway", help="Gateway - single process for Telegram/Discord/CLI", configure=_configure_gateway, run=_run_gateway
    ),
    Command(
        name="bootstrap",
        help="One-command idempotent setup: venv, deps, layout, migration, health",
        configure=_configure_bootstrap,
        run=_run_bootstrap,
    ),
    Command(name="doctor", help="Health/installation check for Hermus", configure=_configure_doctor, run=_run_doctor),
    Command(
        name="check",
        help="One-command offline selfcheck (env + always-on suites on mock, scoreboard, CI exit code)",
        configure=_configure_check,
        run=_run_check,
    ),
    Command(
        name="engine",
        help="Local AI engine: NPU/GPU detection, NoLlama install/serve, model downloads",
        configure=_configure_engine,
        run=_run_engine,
    ),
    Command(name="cron", help="Scheduler - natural language + one-shot/recurring", configure=_configure_cron, run=_run_cron),
    Command(name="context", help="Persistent personal context", configure=_configure_context, run=_run_context),
    Command(
        name="update",
        help="Update from GitHub - shows update in dashboard and CLI too - like hermes update",
        configure=_configure_update,
        run=_run_update,
    ),
    Command(name="jobs", help="Inspect the gateway job queue (data/jobs/)", configure=_configure_jobs, run=_run_jobs),
    Command(
        name="watchdog", help="Self-healing watchdog - classify/repair errors", configure=_configure_watchdog, run=_run_watchdog
    ),
    Command(name="artifacts", help="Artifact-Centric Workspace Explorer", configure=_configure_artifacts, run=_run_artifacts),
    Command(
        name="rollback", help="Transactional Rollback & Checkpoint Manager", configure=_configure_rollback, run=_run_rollback
    ),
    Command(
        name="harness",
        help="Agent harness - sessions, swarm bus, file-shift, compaction + eval runs/reports",
        configure=_configure_harness,
        run=_run_harness,
    ),
    Command(
        name="sentinel",
        help="Always-alert core - instant event reaction (FAIRY side), LLM triage, autostart",
        configure=_configure_sentinel,
        run=_run_sentinel,
    ),
)

__all__ = ["COMMANDS"]
