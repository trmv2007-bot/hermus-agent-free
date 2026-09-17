"""runtime commands — the Hermus CLI's runtime group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

from core.config import config

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
    cron_parser = subparsers.add_parser("cron", help="Cron scheduler - natural language")
    cron_sub = cron_parser.add_subparsers(dest="cron_action")
    cron_add = cron_sub.add_parser("add", help="Add cron job from natural language")
    cron_add.add_argument("text", help="Natural language schedule: 'daily at 9am send report'")
    cron_add.add_argument("--task", help="Task to execute (defaults to text)")
    cron_add.add_argument("--platform", default="cli")
    cron_add.add_argument("--user-id", default="default")
    cron_sub.add_parser("list", help="List cron jobs")
    cron_remove = cron_sub.add_parser("remove", help="Remove cron job")
    cron_remove.add_argument("job_id")


def _run_cron(args, ctx: CLIContext) -> None:
    from scheduler.cron import cron_manager

    if args.cron_action == "add":
        job = cron_manager.add_job(args.text, task=args.task, platform=args.platform, user_id=args.user_id)
        print(f"Cron job added: {job['id']} - {job['cron']} - {job['task']}")
    elif args.cron_action == "list":
        jobs = cron_manager.list_jobs()
        print(f"Cron jobs ({len(jobs)}):")
        for j in jobs:
            print(f" - {j['id']}: {j['cron']} - {j['natural']} -> {j['platform']}:{j['user_id']}")
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


def _run_harness(args, ctx: CLIContext) -> None:
    import json as _json

    from core.harness import bus, sessions
    from core.harness.memory_graph import cascade_recall
    from core.harness.swarm import spawn as swarm_spawn

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
        name="engine",
        help="Local AI engine: NPU/GPU detection, NoLlama install/serve, model downloads",
        configure=_configure_engine,
        run=_run_engine,
    ),
    Command(name="cron", help="Cron scheduler - natural language", configure=_configure_cron, run=_run_cron),
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
        help="Agent harness - sessions, swarm bus, file-shift, compaction",
        configure=_configure_harness,
        run=_run_harness,
    ),
)

__all__ = ["COMMANDS"]
