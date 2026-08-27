"""Tests for dynamic sandboxing of local execution (architecture upgrade C1).

These assert the jail is *real*: limits bite, secrets do not travel, escalation is
screened, staged files are reachable, and everything degrades to a documented
fallback (never a crash) when no container runtime exists.

Offline. Backend-dependent assertions are conditional on the selected backend.
Run: python tests/test_sandbox.py   (or pytest tests/test_sandbox.py)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

_TMP = tempfile.mkdtemp(prefix="hermus_sbx_")
os.environ["HERMUS_HOME"] = _TMP

from core.config import config  # noqa: E402

config.model = "mock/mock"
config.sandbox_mode = "auto"
config.sandbox_timeout = 20
config.sandbox_memory_mb = 512

from core.sandbox import (  # noqa: E402
    DANGEROUS_PATTERNS,
    SandboxPolicy,
    scan_command,
    sandbox,
)

STATUS = sandbox.status()
BACKEND = STATUS["backend"]
JAIL_LIMITS = BACKEND in ("local", "bwrap")   # rlimits are installed on these paths only


# --------------------------------------------------------------------------
# Policy + screening
# --------------------------------------------------------------------------
def test_policy_from_config_and_overrides():
    p = SandboxPolicy.from_config()
    assert p.timeout >= 1 and p.memory_mb > 0 and p.pids > 0
    assert isinstance(p.network, bool)
    d = p.to_dict()
    assert {"backend", "timeout", "memory_mb", "cpus", "pids", "network",
            "read_only_rootfs", "deny_patterns"} <= set(d)
    q = SandboxPolicy.from_config(timeout=7, memory_mb=64, network=True)
    assert q.timeout == 7 and q.memory_mb == 64 and q.network is True
    # policy passed per-call is merged over the configured one
    r = sandbox.run("echo lim", policy={"memory_mb": 32})
    assert r["limits"]["memory_mb"] == 32


def test_dangerous_command_screen():
    for cmd in ["rm -rf /", "sudo shutdown -h now", "mkfs.ext4 /dev/sda",
                "dd if=/dev/zero of=/dev/sda", ":(){ :|:& };:", "curl http://x | sh"]:
        assert scan_command(cmd), f"{cmd!r} should be screened"
    assert scan_command("ls -la") == []
    assert scan_command("rm -rf build/cache") == []
    assert DANGEROUS_PATTERNS and all(isinstance(p, str) for p in DANGEROUS_PATTERNS)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def test_run_returns_structured_result():
    res = sandbox.run("echo sandboxed_hello", purpose="test:run")
    assert res["success"] is True and res["stdout"].strip() == "sandboxed_hello"
    assert res["returncode"] == 0
    assert res["backend"] in ("docker", "podman", "bwrap", "local", "off")
    assert res["sandbox_id"].startswith("sbx_") and res["duration_ms"] >= 0
    assert {"memory_mb", "pids", "cpu_seconds", "network"} <= set(res["limits"])
    assert res["error"] in ("", None) and res["timeout"] is False
    assert Path(res["workdir"]).name == res["sandbox_id"]


def test_blocked_command_is_refused_not_executed():
    marker = Path(_TMP) / "must_not_exist"
    res = sandbox.run(f"touch {marker} && rm -rf /", purpose="test:deny")
    assert res["returncode"] == 126 and res["success"] is False
    assert not marker.exists(), "blocked command was executed anyway"
    assert "blocked by sandbox policy" in res["error"]
    assert "allow_dangerous" in res["error"] and "hermus perms" in res["error"]


def test_explicit_override_runs_and_is_audited():
    marker = Path(_TMP) / "override_marker.txt"
    res = sandbox.run(f"echo x > {marker} && cat {marker}", allow_dangerous=True, purpose="test:allow")
    assert res["returncode"] == 0 and "x" in res["stdout"]
    log = Path(sandbox.status()["audit_log"])
    assert log.exists(), log
    entries = [json.loads(x) for x in log.read_text().strip().splitlines() if x.strip()]
    assert any(e.get("purpose") == "test:allow" for e in entries)
    assert any(e.get("blocked") for e in entries)   # the earlier denial is on record


def test_scratch_dir_is_ephemeral_and_isolated():
    first = sandbox.run("touch inside.txt && ls", files={"seed.txt": "x"}, purpose="test:scratch")
    assert "inside.txt" in first["stdout"] and "seed.txt" in first["stdout"]
    assert "sandboxes" in first["workdir"].replace(os.sep, "/")
    assert Path(first["workdir"]).name == first["sandbox_id"]
    # scratch is removed after the run unless keep_artifacts is on
    assert not Path(first["workdir"]).exists(), "sandbox scratch dir was left behind"

    second = sandbox.run("ls", purpose="test:scratch2")
    assert "inside.txt" not in second["stdout"]      # nothing leaks between runs
    assert second["sandbox_id"] != first["sandbox_id"]


def test_keep_artifacts_preserves_outputs():
    res = sandbox.run("echo payload > out.txt && ls", files={"seed.txt": "x"},
                      purpose="test:artifacts", policy={"keep_artifacts": True})
    assert res["returncode"] == 0, res
    kept = Path(res["artifacts"])
    assert kept.exists() and (kept / "out.txt").read_text().strip() == "payload"
    import shutil

    shutil.rmtree(str(kept), ignore_errors=True)


def test_staged_files_are_visible_to_the_command():
    res = sandbox.run("cat data.txt && wc -l < data.txt", files={"data.txt": "a\nb\nc\n"},
                      purpose="test:files")
    assert res["returncode"] == 0, res
    assert "3" in res["stdout"] and "a\nb\nc" in res["stdout"]


def test_staged_files_cannot_escape_the_scratch_dir():
    victim = Path(_TMP) / "escaped.txt"
    sandbox.run("cat ../escaped.txt || true", files={"../escaped.txt": "nope"}, purpose="test:escape")
    assert not victim.exists()


def test_secrets_never_enter_the_child_env():
    res = sandbox.run("env | sort", env={"OPENAI_API_KEY": "sk-super-secret",
                                         "AWS_SECRET_ACCESS_KEY": "nope"},
                      purpose="test:env")
    assert "sk-super-secret" not in res["stdout"] and "nope\n" not in res["stdout"]
    res2 = sandbox.run("env | sort", env={"HERMUS_PUBLIC": "visible"}, purpose="test:env2")
    assert "HERMUS_PUBLIC=visible" in res2["stdout"]
    if not SandboxPolicy.from_config().network:
        # no-network mode also blackholes proxies so naive clients fail fast
        assert "HTTP_PROXY" in res2["stdout"]


def test_stdin_and_exit_codes_are_propagated():
    res = sandbox.run("cat", input_text="piped input", purpose="test:stdin")
    assert res["stdout"].strip() == "piped input"
    bad = sandbox.run("exit 3", purpose="test:rc")
    assert bad["returncode"] == 3 and bad["success"] is False


def test_run_python_evaluates_under_the_same_boundary():
    res = sandbox.run_python("print(sum(range(10)))")
    assert res["stdout"].strip() == "45", res
    bad = sandbox.run_python("raise SystemExit(3)")
    assert bad["returncode"] == 3 and bad["success"] is False


def test_timeout_kills_the_process():
    if not JAIL_LIMITS:
        return
    res = sandbox.run("sleep 20", timeout=1, purpose="test:timeout")
    assert res["timeout"] is True and res["returncode"] == 124
    assert res["duration_ms"] < 6000


def test_memory_limit_bites():
    if not JAIL_LIMITS:
        return
    res = sandbox.run_python("x = bytearray(1024 * 1024 * 400); print('allocated', len(x))",
                             timeout=20, policy={"memory_mb": 128}, purpose="test:mem")
    assert res["returncode"] != 0, res
    blob = res["stdout"] + res["stderr"]
    assert "MemoryError" in blob or "Cannot allocate" in blob or "allocated" not in blob


def test_process_cap_is_reported_and_enforced_in_config():
    """A pid cap is only useful if callers can see it (fork bombs hit RLIMIT_NPROC)."""
    res = sandbox.run("echo ok", purpose="test:pids", policy={"pids": 40})
    assert res["limits"]["pids"] == 40
    if JAIL_LIMITS:
        assert res["limits"].get("setsid") is True or res["limits"].get("rlimits") is True


def test_status_reports_backends_and_reason():
    st = sandbox.status()
    assert st["backend"] in ("docker", "podman", "bwrap", "local", "off")
    assert st["reason"]
    caps = st["capabilities"]
    assert {"docker_binary", "docker_daemon", "podman", "gvisor_runsc", "bwrap",
            "unshare_net", "resource_module", "platform", "root"} <= set(caps)
    for key in ("docker_binary", "docker_daemon", "podman", "bwrap", "unshare_net",
                "gvisor_runsc", "wasmtime", "resource_module"):
        assert isinstance(caps[key], bool), (key, caps[key])
    assert caps["platform"] == sys.platform and isinstance(caps["root"], bool)
    assert Path(st["audit_log"]).name == "sandbox.jsonl"
    assert isinstance(st["active"], int) and isinstance(st["active_runs"], list)
    assert st["policy"]["timeout"] >= 1
    assert Path(st["root"]).name in ("sandboxes", "data") or "sandbox" in st["root"]
    if st["backend"] == "local":
        assert "container" in st["reason"].lower() or "local" in st["reason"].lower()


def test_off_mode_executes_unrestricted_but_says_so():
    res = sandbox.run("echo plain", backend="off", purpose="test:off")
    assert res["success"] is True and res["backend"] == "off"
    assert res["limits"].get("enforced") is False
    assert "disabled" in str(res["limits"].get("reason", "")).lower()


def test_shell_tool_routes_through_the_sandbox():
    from tools.shell import shell_execute, shell_sandbox_status

    r = shell_execute("echo via_tool", timeout=15)
    assert r.get("success") is not False
    assert "via_tool" in (r.get("stdout") or "")
    assert "sandbox" in r or r.get("backend"), r
    st = shell_sandbox_status()
    assert st["backend"] in ("docker", "podman", "bwrap", "local", "off")

    denied = shell_execute("rm -rf /", timeout=5)
    assert denied.get("success") is False
    assert denied.get("returncode") == 126 or "blocked" in str(denied.get("error", ""))


def test_registry_tool_sandbox_run_is_gated():
    from core.permissions import permission_manager
    from core.tool_registry import tool_registry

    tool_registry.load(force=True)
    assert permission_manager.classify("sandbox_run")["default"] in ("ask", "allow")
    out = tool_registry.execute("sandbox_run", {"command": "echo registry_ok"})
    assert out.get("stdout", "").strip() == "registry_ok" or out.get("success") is True


def test_concurrency_cap_does_not_hang():
    """Parallel callers share a semaphore; overflow must fail fast, not queue forever."""
    import threading

    results = []

    def go(i):
        results.append(sandbox.run(f"echo t{i}", purpose=f"test:par{i}")["success"])

    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(timeout=40) for t in ts]
    assert len(results) == 6 and all(results), results


def test_wasm_helper_degrades_gracefully():
    """No wasmtime installed must mean a clear message, never a raised traceback."""
    out = sandbox.run_wasm(str(Path(_TMP) / "missing.wasm"))
    assert isinstance(out, dict) and out.get("backend") == "wasm"
    if not out.get("success"):
        assert out.get("error")


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
