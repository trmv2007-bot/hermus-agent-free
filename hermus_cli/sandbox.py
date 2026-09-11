"""sandbox — Sandboxed execution - probe backends, run commands in a jail."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
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


def run(args, ctx: CLIContext) -> None:
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
        ctx.parser.parse_args(["sandbox", "--help"])
