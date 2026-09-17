"""safety commands — the Hermus CLI's safety group.

Part of the grouped CLI: one module per capability group instead of one
module per command. Each command keeps its own ``configure``/``run`` pair,
so the command bodies are unchanged while the import shim, docstring and
``TYPE_CHECKING`` block that every one of the forty-three files carried are
gone.
"""

from __future__ import annotations

from ._common import CLIContext
from ._spec import Command, no_action


def _configure_perms(subparsers) -> None:
    perms_parser = subparsers.add_parser("perms", help="Permission manager - ALLOW/ASK/DENY")
    perms_sub = perms_parser.add_subparsers(dest="perms_action")
    perms_check = perms_sub.add_parser("check", help="Check a tool's permission decision")
    perms_check.add_argument("tool")
    perms_set = perms_sub.add_parser("set", help="Override a tool's policy")
    perms_set.add_argument("tool")
    perms_set.add_argument("decision", choices=["allow", "ask", "deny"])
    perms_set.add_argument("--agent", default=None)
    perms_approve = perms_sub.add_parser("approve", help="Create a scoped approval grant for yellow red-line actions")
    perms_approve.add_argument("title")
    perms_approve.add_argument("--tool", default="*")
    perms_approve.add_argument("--red-line", dest="red_lines", type=int, action="append", default=[])
    perms_approve.add_argument("--resource", dest="resources", action="append", default=[])
    perms_approve.add_argument("--purpose", default="")
    perms_approve.add_argument("--ttl-minutes", type=int, default=None)
    perms_approve.add_argument("--max-uses", type=int, default=None)
    perms_approve.add_argument("--notes", default="")
    perms_revoke = perms_sub.add_parser("revoke", help="Revoke an approval grant")
    perms_revoke.add_argument("id")
    perms_pending = perms_sub.add_parser("pending", help="List pending yellow-action approval prompts")
    perms_pending.add_argument("--all", action="store_true", help="Include resolved prompts")
    perms_resolve = perms_sub.add_parser("resolve", help="Resolve a pending approval prompt")
    perms_resolve.add_argument("id")
    perms_resolve.add_argument("decision", choices=["approve", "deny"])
    perms_resolve.add_argument("--resource", dest="resources", action="append", default=[])
    perms_resolve.add_argument("--purpose", default="")
    perms_resolve.add_argument("--ttl-minutes", type=int, default=None)
    perms_resolve.add_argument("--max-uses", type=int, default=None)
    perms_resolve.add_argument("--notes", default="")
    perms_resolve.add_argument(
        "--retry", action="store_true", help="Retry the approved action immediately through the ToolGateway"
    )
    perms_retry = perms_sub.add_parser("retry", help="Retry an approved pending action through the ToolGateway")
    perms_retry.add_argument("id")
    perms_bundles = perms_sub.add_parser("bundles", help="List pending approval bundles")
    perms_bundles.add_argument("--all", action="store_true", help="Include resolved bundles")
    perms_bundle_resolve = perms_sub.add_parser("resolve-bundle", help="Approve or deny an approval bundle")
    perms_bundle_resolve.add_argument("id")
    perms_bundle_resolve.add_argument("decision", choices=["approve", "deny"])
    perms_bundle_resolve.add_argument("--ttl-minutes", type=int, default=None)
    perms_bundle_resolve.add_argument("--max-uses", type=int, default=None)
    perms_bundle_resolve.add_argument("--notes", default="")
    perms_bundle_resolve.add_argument(
        "--resume", action="store_true", help="Resume the mission attached to the bundle after approval"
    )
    perms_sub.add_parser("approvals", help="List active approval grants")
    perms_sub.add_parser("list", help="Recent audit log")


def _run_perms(args, ctx: CLIContext) -> None:
    from core.permissions import permission_manager

    if args.perms_action == "check":
        r = permission_manager.check(args.tool)
        safety = r.get("safety") or {}
        print(
            f"{args.tool}: risk={r['risk']} decision={r['decision']} safety={safety.get('zone', 'green')} red_lines={safety.get('red_lines', [])}"
        )
        if r.get("approval"):
            print(f" approval={r['approval'].get('grant', {}).get('id')}")
    elif args.perms_action == "set":
        r = permission_manager.set_policy(args.tool, args.decision, agent=args.agent)
        print(
            f"{'✅' if r.get('success') else '❌'} {args.tool} -> {args.decision}"
            + (f" (agent={args.agent})" if args.agent else "")
        )
    elif args.perms_action == "approve":
        r = permission_manager.approval_grant(
            args.title,
            tool=args.tool,
            red_lines=args.red_lines,
            resources=args.resources,
            purpose=args.purpose,
            ttl_minutes=args.ttl_minutes,
            max_uses=args.max_uses,
            notes=args.notes,
        )
        grant = r.get("grant", {})
        print(f"{'✅' if r.get('success') else '❌'} approval {grant.get('id', '')} {grant.get('title', args.title)}")
    elif args.perms_action == "revoke":
        r = permission_manager.approval_revoke(args.id)
        print(f"{'✅' if r.get('success') else '❌'} revoked {args.id}")
    elif args.perms_action == "pending":
        for req in permission_manager.approval_pending(include_resolved=args.all):
            print(
                f" {req.get('id')} status={req.get('status')} tool={req.get('tool')} safety={req.get('safety', {}).get('zone')} red_lines={req.get('safety', {}).get('red_lines')} resources={req.get('suggested_resources')}"
            )
    elif args.perms_action == "resolve":
        r = permission_manager.approval_resolve(
            args.id,
            args.decision,
            resources=args.resources or None,
            purpose=args.purpose,
            ttl_minutes=args.ttl_minutes,
            max_uses=args.max_uses,
            notes=args.notes,
        )
        retry = None
        if args.retry and r.get("success") and args.decision == "approve":
            retry = permission_manager.approval_retry(args.id)
        print(
            f"{'✅' if r.get('success') else '❌'} {args.decision} {args.id}"
            + (f" grant={r.get('grant', {}).get('id')}" if r.get("grant") else "")
        )
        if retry is not None:
            print(f" retry={'✅' if retry.get('success') else '❌'}")
    elif args.perms_action == "retry":
        r = permission_manager.approval_retry(args.id)
        print(f"{'✅' if r.get('success') else '❌'} retry {args.id}")
        if r.get("error"):
            print(r.get("error"))
    elif args.perms_action == "bundles":
        for bundle in permission_manager.approval_bundles(include_resolved=args.all):
            print(
                f" {bundle.get('id')} status={bundle.get('status')} mission={bundle.get('mission_id')} requests={len(bundle.get('request_ids') or [])} title={bundle.get('title')}"
            )
    elif args.perms_action == "resolve-bundle":
        r = permission_manager.approval_bundle_resolve(
            args.id,
            args.decision,
            ttl_minutes=args.ttl_minutes,
            max_uses=args.max_uses,
            notes=args.notes,
        )
        print(f"{'✅' if r.get('success') else '❌'} {args.decision} bundle {args.id}")
        bundle = r.get("bundle") or {}
        if args.resume and args.decision == "approve" and bundle.get("mission_id"):
            try:
                from core.mission import mission_engine

                resumed = mission_engine.resume_mission(bundle["mission_id"], extra_steps=8)
                print(f" resume={resumed.state} mission={resumed.mission_id}")
            except Exception as exc:
                print(f" resume=❌ {exc}")
    elif args.perms_action == "approvals":
        for grant in permission_manager.approvals_list():
            print(
                f" {grant.get('id')} tool={grant.get('tool')} red_lines={grant.get('red_lines')} resources={grant.get('resources')} uses={grant.get('uses')}/{grant.get('max_uses')}"
            )
    elif args.perms_action == "list":
        for e in permission_manager.recent():
            print(
                f" {e.get('ts', '')[:19]} {e.get('tool')} -> {e.get('decision')} (risk={e.get('risk')}, agent={e.get('agent')})"
            )
    else:
        no_action(ctx, "perms")


def _configure_powers(subparsers) -> None:
    powers_parser = subparsers.add_parser("powers", help="Capability ledger - current, missing and discovered powers")
    powers_sub = powers_parser.add_subparsers(dest="powers_action")
    powers_sub.add_parser("list", help="List discovered possible powers")
    powers_add = powers_sub.add_parser("add", help="Add a discovered possible power to CAPABILITY_LEDGER.md")
    powers_add.add_argument("power")
    powers_add.add_argument("--use", default="")
    powers_add.add_argument("--risk", default="")
    powers_add.add_argument("--approval", default="")
    powers_add.add_argument("--status", default="not_granted")
    powers_propose = powers_sub.add_parser("propose", help="Generate a safe setup proposal for a missing/not-granted power")
    powers_propose.add_argument("power")
    powers_propose.add_argument("--write", action="store_true", help="Write docs/capability_proposals/<power>.md")
    powers_sub.add_parser("registry", help="List capability readiness/activation registry")
    powers_register = powers_sub.add_parser("register", help="Register/update capability readiness")
    powers_register.add_argument("power")
    powers_register.add_argument("--category", default="generic")
    powers_register.add_argument("--status", default="missing")
    powers_register.add_argument("--notes", default="")
    powers_setup = powers_sub.add_parser("setup", help="Generate setup proposal and planning command for a capability")
    powers_setup.add_argument("power")
    powers_setup.add_argument("--no-write", action="store_true")
    powers_activate_req = powers_sub.add_parser("request-activation", help="Create pending activation approval request")
    powers_activate_req.add_argument("power")
    powers_activate_req.add_argument("--reason", default="")
    powers_activate = powers_sub.add_parser("activate", help="Activate only after approved activation request")
    powers_activate.add_argument("power")
    powers_activate.add_argument("--approval-id", required=True)


def _run_powers(args, ctx: CLIContext) -> None:
    from core.capability_ledger import CapabilityEntry, get_capability_ledger

    ledger = get_capability_ledger()
    if args.powers_action == "list":
        rows = ledger.list_discovered()
        if not rows:
            print("No discovered possible powers recorded yet.")
        for row in rows:
            print(f" - {row.get('power')} | status={row.get('status')} | approval={row.get('needed_approval_setup')}")
    elif args.powers_action == "add":
        r = ledger.add_discovered(
            CapabilityEntry.create(
                power=args.power,
                use=args.use,
                risk=args.risk,
                needed_approval_setup=args.approval,
                status=args.status,
                source="cli",
            )
        )
        print(f"{'✅' if r.get('success') else '❌'} capability {args.power}" + (" (already listed)" if r.get("deduped") else ""))
    elif args.powers_action == "propose":
        r = ledger.propose(args.power, write=args.write)
        if args.write and r.get("path"):
            print(f"✅ proposal written: {r['path']}")
        else:
            print(r.get("markdown", ""))
    elif args.powers_action in {"registry", "register", "setup", "request-activation", "activate"}:
        from core.capability_registry import get_capability_registry

        registry = get_capability_registry()
        if args.powers_action == "registry":
            for rec in registry.list():
                print(
                    f" - {rec.get('name')} | status={rec.get('status')} | category={rec.get('category')} | activation={rec.get('activation_request_id')}"
                )
        elif args.powers_action == "register":
            r = registry.register(args.power, category=args.category, status=args.status, source="cli", notes=args.notes)
            print(f"{'✅' if r.get('success') else '❌'} {args.power} -> {r.get('record', {}).get('status')}")
        elif args.powers_action == "setup":
            r = registry.setup_plan(args.power, write_proposal=not args.no_write)
            rec = r.get("record", {})
            print(
                f"{'✅' if r.get('success') else '❌'} setup {args.power}: status={rec.get('status')} proposal={rec.get('proposal_path')}"
            )
            if rec.get("planning_command"):
                print(f" planning: {rec.get('planning_command')}")
        elif args.powers_action == "request-activation":
            r = registry.request_activation(args.power, reason=args.reason)
            print(f"{'✅' if r.get('success') else '❌'} activation request {r.get('request', {}).get('id', '')}")
        elif args.powers_action == "activate":
            r = registry.activate(args.power, approval_id=args.approval_id)
            print(
                f"{'✅' if r.get('success') else '❌'} activate {args.power}: {r.get('record', {}).get('status') or r.get('error')}"
            )
    else:
        no_action(ctx, "powers")


def _configure_safety(subparsers) -> None:
    safety_parser = subparsers.add_parser("safety", help="Autonomy safety reports and audit exports")
    safety_sub = safety_parser.add_subparsers(dest="safety_action")
    safety_report_p = safety_sub.add_parser("report", help="Generate an Autonomy Safety Report")
    safety_report_p.add_argument("--write", action="store_true", help="Write docs/safety_reports/autonomy-safety-report-*.md")
    safety_report_p.add_argument("--output", help="Write report to this markdown path")
    safety_report_p.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    safety_preflight_p = safety_sub.add_parser("preflight", help="Check a mission/action before starting it")
    safety_preflight_p.add_argument("goal", nargs="+", help="Goal/action to pre-flight")
    safety_preflight_p.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    safety_preflight_p.add_argument(
        "--create-approval-prompts", action="store_true", help="Create draft pending approval prompts suggested by pre-flight"
    )
    safety_scan_p = safety_sub.add_parser("scan-folder", help="Run read-only local defensive folder scanner")
    safety_scan_p.add_argument("path")
    safety_scan_p.add_argument("--max-files", type=int, default=500)
    safety_scan_p.add_argument("--save-report", action="store_true", help="Write a Markdown scan report artifact")
    safety_scan_p.add_argument("--mission-id", default="", help="Attach saved scan report to a mission")
    safety_scan_p.add_argument("--json", action="store_true")
    safety_scan_mission_p = safety_sub.add_parser("scan-mission", help="Create a gated local folder scan mission")
    safety_scan_mission_p.add_argument("path")
    safety_scan_mission_p.add_argument("--purpose", default="malware")
    safety_scan_mission_p.add_argument("--max-files", type=int, default=500)
    safety_scan_mission_run_p = safety_sub.add_parser("scan-mission-run", help="Run an approved local folder scan mission")
    safety_scan_mission_run_p.add_argument("mission_id")


def _run_safety(args, ctx: CLIContext) -> None:
    import json as _json
    from pathlib import Path as _Path

    from core.safety_report import generate_safety_report, write_safety_report

    if args.safety_action == "report":
        report = generate_safety_report()
        if args.write or args.output:
            result = write_safety_report(report, output=_Path(args.output) if args.output else None)
            print(f"✅ safety report written: {result['path']}")
        elif args.json:
            print(_json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(report.to_markdown())
    elif args.safety_action == "preflight":
        from core.autonomy_preflight import create_preflight_approval_requests, preflight_goal

        goal = " ".join(args.goal)
        report = preflight_goal(goal)
        if args.json:
            print(_json.dumps(report.to_dict(), indent=2, default=str))
        else:
            print(report.to_markdown())
        if args.create_approval_prompts:
            prompts = create_preflight_approval_requests(goal)
            print("\n--- draft approval prompts ---")
            print(
                _json.dumps(
                    {"success": prompts.get("success"), "created": prompts.get("created"), "errors": prompts.get("errors")},
                    indent=2,
                    default=str,
                )
            )
    elif args.safety_action == "scan-folder":
        try:
            from core.permissions import Decision, permission_manager

            check = permission_manager.check(
                "local_folder_defensive_scan",
                args={
                    "path": args.path,
                    "max_files": args.max_files,
                    "purpose": "defensive_scan",
                    "save_report": args.save_report,
                    "mission_id": args.mission_id,
                },
            )
            if check.get("decision") != Decision.ALLOW.value:
                print(_json.dumps({"success": False, "error": "approval required", "permission": check}, indent=2, default=str))
                return
        except Exception as exc:
            print(_json.dumps({"success": False, "error": f"permission check failed closed: {exc}"}, indent=2, default=str))
            return
        from core.local_defense_scanner import scan_folder

        result = scan_folder(args.path, max_files=args.max_files, save_report=args.save_report, mission_id=args.mission_id)
        if args.json:
            print(_json.dumps(result, indent=2, default=str))
        else:
            print(result.get("markdown") or _json.dumps(result, indent=2, default=str))
    elif args.safety_action == "scan-mission":
        from core.local_defense_workflow import start_local_scan_mission

        report = start_local_scan_mission(args.path, purpose=args.purpose, max_files=args.max_files)
        print(_json.dumps(report.to_dict(), indent=2, default=str))
    elif args.safety_action == "scan-mission-run":
        from core.local_defense_workflow import run_local_scan_mission

        try:
            print(_json.dumps(run_local_scan_mission(args.mission_id).to_dict(), indent=2, default=str))
        except ValueError as exc:
            print(f"Error: {exc}")
    else:
        no_action(ctx, "safety")


def _configure_emergency(subparsers) -> None:
    emergency_parser = subparsers.add_parser("emergency", help="Global Red Line 1 emergency stop")
    emergency_sub = emergency_parser.add_subparsers(dest="emergency_action")
    emergency_stop_p = emergency_sub.add_parser("stop", help="Activate global emergency stop")
    emergency_stop_p.add_argument("reason", nargs="*", default=[])
    emergency_resume_p = emergency_sub.add_parser("resume", help="Clear global emergency stop")
    emergency_resume_p.add_argument("reason", nargs="*", default=[])
    emergency_sub.add_parser("status", help="Show global emergency stop state")


def _run_emergency(args, ctx: CLIContext) -> None:
    from core.emergency_stop import get_emergency_stop

    brake = get_emergency_stop()
    if args.emergency_action == "stop":
        r = brake.activate(" ".join(args.reason) or "manual emergency stop", set_by="cli")
        print(f"✅ emergency stop active: {r['state'].get('reason')}")
    elif args.emergency_action == "resume":
        r = brake.clear(" ".join(args.reason) or "manual resume", set_by="cli")
        print(f"✅ emergency stop cleared: {r['state'].get('reason')}")
    elif args.emergency_action == "status":
        s = brake.state().to_dict()
        print(f"emergency_stop={s.get('active')} reason={s.get('reason')} updated={s.get('updated_at')}")
    else:
        no_action(ctx, "emergency")


def _configure_sandbox(subparsers) -> None:
    sandbox_parser = subparsers.add_parser("sandbox", help="Sandboxed execution - probe backends, run commands in a jail")
    sandbox_sub = sandbox_parser.add_subparsers(dest="sandbox_action")
    sandbox_sub.add_parser("status", help="Backend selection + capability probe")
    sandbox_run = sandbox_sub.add_parser("run", help="Run a command inside the sandbox")
    sandbox_run.add_argument("command", nargs="+")
    sandbox_run.add_argument("--timeout", type=int, default=30)
    sandbox_run.add_argument("--network", action="store_true")
    sandbox_py = sandbox_sub.add_parser("python", help="Run Python source in the sandbox")
    sandbox_py.add_argument("code")
    sandbox_py.add_argument("--timeout", type=int, default=30)


def _run_sandbox(args, ctx: CLIContext) -> None:
    from core.sandbox import sandbox

    if args.sandbox_action == "status":
        st = sandbox.status()
        print(f"backend : {st['backend']}  ({st['reason']})")
        pol = st["policy"]
        print(
            f"policy  : timeout={pol.get('timeout')}s mem={pol.get('memory_mb')}MB "
            f"cpu={pol.get('cpus')} pids={pol.get('pids')} disk={pol.get('disk_mb')}MB "
            f"net={pol.get('network')} ro_rootfs={pol.get('read_only_rootfs')}"
        )
        if st.get("active"):
            print(f"running : {st['active']} sandbox(es) in flight")
        caps = st.get("capabilities", {})

        def _cap(v):
            if isinstance(v, dict):
                return bool(v.get("ok")), str(v.get("note", ""))[:60]
            return bool(v), ""

        for k, label in (
            ("docker_binary", "docker cli"),
            ("docker_daemon", "docker daemon"),
            ("podman", "podman"),
            ("bwrap_usable", "bubblewrap"),
            ("unshare_net", "unshare -n"),
            ("gvisor_runsc", "gVisor runsc"),
            ("wasmtime", "wasmtime"),
            ("resource_module", "posix rlimits"),
        ):
            ok, note = _cap(caps.get(k))
            print(f"  {'✅' if ok else '—'} {label:16s} {note}")
        print("workdir : " + str(st.get("root")))
    elif args.sandbox_action in ("run", "python"):
        cmd = " ".join(args.command) if args.sandbox_action == "run" else args.code
        if args.sandbox_action == "python":
            res = sandbox.run_python(cmd, timeout=args.timeout)
        else:
            res = sandbox.run(cmd, timeout=args.timeout, network=args.network)
        lim = res.get("limits") or {}
        print(
            f"[{res['backend']}] rc={res['returncode']} {res['duration_ms']}ms "
            f"limits={lim.get('memory_mb')}MB/{lim.get('pids')}pids "
            f"net={lim.get('network')}"
        )
        if res.get("stdout"):
            print(res["stdout"].rstrip()[:4000])
        if res.get("stderr"):
            print("stderr:", res["stderr"].rstrip()[:1500])
        if res.get("error"):
            print("error:", res["error"][:400])
    else:
        no_action(ctx, "sandbox")


def _configure_verify(subparsers) -> None:
    verify_parser = subparsers.add_parser("verify", help="Domain Verification Subsystem")
    verify_sub = verify_parser.add_subparsers(dest="verify_action")
    v_run = verify_sub.add_parser("run", help="Run domain verification")
    v_run.add_argument("--domain", default="auto", help="Domain name")
    v_run.add_argument("--path", default=None, help="Target path or file")
    v_run.add_argument("--task", default="", help="Original task description")
    v_run.add_argument("--output", default="", help="Execution output")
    verify_sub.add_parser("domains", help="List available domain verifiers")


def _run_verify(args, ctx: CLIContext) -> None:
    import json as json_lib

    from core.verifier_registry import verifier_registry

    if args.verify_action == "run":
        res = verifier_registry.verify(
            domain_or_auto=args.domain,
            context={"task": args.task, "target_path": args.path, "output": args.output},
        )
        print(json_lib.dumps(res.to_dict(), indent=2))
    elif args.verify_action == "domains":
        print("Registered Domain Verifiers:", verifier_registry.list_domains())
    else:
        no_action(ctx, "verify")


COMMANDS: tuple[Command, ...] = (
    Command(name="perms", help="Permission manager - ALLOW/ASK/DENY", configure=_configure_perms, run=_run_perms),
    Command(
        name="powers",
        help="Capability ledger - current, missing and discovered powers",
        configure=_configure_powers,
        run=_run_powers,
    ),
    Command(name="safety", help="Autonomy safety reports and audit exports", configure=_configure_safety, run=_run_safety),
    Command(name="emergency", help="Global Red Line 1 emergency stop", configure=_configure_emergency, run=_run_emergency),
    Command(
        name="sandbox",
        help="Sandboxed execution - probe backends, run commands in a jail",
        configure=_configure_sandbox,
        run=_run_sandbox,
    ),
    Command(name="verify", help="Domain Verification Subsystem", configure=_configure_verify, run=_run_verify),
)

__all__ = ["COMMANDS"]
