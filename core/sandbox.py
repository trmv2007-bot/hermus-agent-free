"""Dynamic sandboxing — run agent tool commands in an ephemeral, unprivileged jail.

`tools/shell.py` used to call ``subprocess.run(command, shell=True)`` with a
timeout and nothing else: full user privileges, full filesystem, full network,
unbounded memory, no audit. This module is the enforcement layer every exec-ish
tool should go through.

Backends (auto-detected, first available wins, always degrades instead of failing):

  docker   — ``docker run --rm`` with a dropped-capability, read-only-rootfs,
             single-CPU-capped container. If ``gvisor``'s ``runsc`` (or
             ``kata-runtime``) is installed, ``--runtime=`` is added so the
             kernel itself is virtualised.
  podman   — same shape, rootless-friendly (``--userns=keep-id``).
  local    — stdlib hardening for machines with no container runtime:
             ``resource`` rlimits (AS/CPU/FSIZE/NPROC/CORE), new session +
             process-group kill on timeout, ``PR_SET_NO_NEW_PRIVS``, env
             scrubbing (API keys/tokens never enter the child), and
             ``unshare -n`` when available so ``network=false`` is real.
  off      — plain subprocess (escape hatch for trusted local use).

Every run is audited to ``<HERMUS_HOME>/logs/sandbox.jsonl`` with the policy,
resolved backend, exit code and duration, and writes its scratch dir under
``data/sandboxes/<id>/`` (removed unless ``keep_artifacts``).

Not a silver bullet: ``local`` cannot stop a determined exploit the way a VM or
gVisor can — it is defence in depth, and the docstrings say so rather than
pretending otherwise.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable, Sequence

from .config import config

try:  # POSIX only
    import resource
except Exception:  # pragma: no cover - windows
    resource = None  # type: ignore


DANGEROUS_PATTERNS = (
    r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+/(?:\s|$)",       # rm -rf /
    r"\brm\s+-[a-z]*[rf][a-z]*\s+\*",                   # rm -rf *
    r"\bmkfs(\.|\s)",
    r"\bdd\b.*\bof=/dev/",
    r">\s*/dev/(sd|nvme|md|disk)",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;:",                    # fork bomb
    r"\bchmod\s+-R\s+777\s+/",
    r"\bcurl\b.*\|\s*(ba)?sh",
    r"\bwget\b.*\|\s*(ba)?sh",
    r"\bshutdown\b|\breboot\b|\bhalt\b",
    r"\bsystemctl\s+(stop|disable|mask)\b",
    r"\bgit\s+push\s+--force",
    r"\bDROP\s+(TABLE|DATABASE)\b",
)
SECRET_ENV_RE = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|ACCESS_KEY|PRIVATE_KEY|AUTH)", re.I)
ENV_ALLOW_DEFAULT = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "PYTHONIOENCODING", "PYTHONUNBUFFERED")

VALID_BACKENDS = ("auto", "docker", "podman", "local", "off")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class SandboxPolicy:
    """Everything an unprivileged execution boundary needs, in one place."""

    backend: str = ""                      # "" → auto-detect
    image: str = ""
    runtime: str = ""                      # runsc / kata-runtime / ""
    cpus: float = 1.0
    memory_mb: int = 1024
    pids: int = 128
    disk_mb: int = 256
    timeout: int = 60
    network: bool = False
    read_only_rootfs: bool = True
    tmpfs_size_mb: int = 128
    workspace_mode: str = "rw"             # rw | ro | none
    drop_capabilities: tuple[str, ...] = ("ALL",)
    add_capabilities: tuple[str, ...] = ()
    env_allowlist: tuple[str, ...] = ENV_ALLOW_DEFAULT
    deny_patterns: tuple[str, ...] = DANGEROUS_PATTERNS
    max_output_chars: int = 6000
    keep_artifacts: bool = False
    confine_to_workspace: bool = True
    run_as_nobody: bool = True
    soft_no_new_privs: bool = True

    @classmethod
    def from_config(cls, **overrides: Any) -> "SandboxPolicy":
        p = cls(
            backend=str(getattr(config, "sandbox_mode", "auto") or "auto").lower(),
            image=str(getattr(config, "sandbox_image", "python:3.11-alpine")),
            runtime=str(getattr(config, "sandbox_runtime", "") or ""),
            cpus=float(getattr(config, "sandbox_cpus", 1.0)),
            memory_mb=int(getattr(config, "sandbox_memory_mb", 1024)),
            pids=int(getattr(config, "sandbox_pids", 128)),
            disk_mb=int(getattr(config, "sandbox_disk_mb", 256)),
            timeout=int(getattr(config, "sandbox_timeout", 60)),
            network=bool(getattr(config, "sandbox_network", False)),
            read_only_rootfs=bool(getattr(config, "sandbox_read_only", True)),
            workspace_mode="rw" if getattr(config, "sandbox_workspace_rw", True) else "ro",
        )
        for k, v in (overrides or {}).items():
            if v is not None and hasattr(p, k):
                setattr(p, k, v)
        return p

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["deny_patterns"] = list(self.deny_patterns)
        return d


@dataclass
class SandboxResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 1
    backend: str = "local"
    sandbox_id: str = ""
    duration_ms: int = 0
    timeout: bool = False
    error: str = ""
    workdir: str = ""
    limits: dict[str, Any] = field(default_factory=dict)
    container: str = ""
    artifacts: str = ""
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------- detection
class CapabilityProbe:
    """Cached runtime-capability detection (binaries, rlimits, unshare)."""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def has(self, name: str) -> bool:
        return bool(self.binary(name))

    def binary(self, name: str) -> Optional[str]:
        with self._lock:
            if name in self._cache:
                return self._cache[name]
        path = self._which(name)
        self._cache[name] = path
        return path

    def docker_daemon(self) -> bool:
        if self._cache.get("docker_daemon") is not None:
            return bool(self._cache["docker_daemon"])
        ok = False
        if self.binary("docker"):
            try:
                r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                   capture_output=True, text=True, timeout=6)
                ok = r.returncode == 0 and bool(r.stdout.strip())
            except Exception:
                ok = False
        with self._lock:
            self._cache["docker_daemon"] = ok
        return ok

    def gvisor(self) -> bool:
        """gVisor is usable when runsc exists or docker reports the runtime."""
        if self.binary("runsc"):
            return True
        if self.docker_daemon():
            try:
                r = subprocess.run(["docker", "info", "--format", "{{json .Runtimes}}"],
                                   capture_output=True, text=True, timeout=8)
                return "runsc" in (r.stdout or "")
            except Exception:
                return False
        return False

    def bwrap(self) -> bool:
        """bubblewrap — unprivileged user-namespace sandbox with a read-only root."""
        if self._cache.get("bwrap") is not None:
            return bool(self._cache["bwrap"])
        ok = False
        binpath = self.binary("bwrap")
        if binpath:
            try:
                r = subprocess.run(
                    [binpath, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                     "--unshare-net", "--", "/bin/true"],
                    capture_output=True, timeout=10,
                )
                ok = r.returncode == 0
            except Exception:
                ok = False
        with self._lock:
            self._cache["bwrap"] = ok
        return ok

    def unshare_net(self) -> bool:
        """Can we genuinely drop network namespace? (needs CAP_SYS_ADMIN or userns)."""
        if self._cache.get("unshare_net") is not None:
            return bool(self._cache["unshare_net"])
        ok = False
        binpath = self.binary("unshare")
        if binpath:
            try:
                r = subprocess.run([binpath, "-n", "true"], capture_output=True, timeout=8)
                ok = r.returncode == 0
            except Exception:
                ok = False
        with self._lock:
            self._cache["unshare_net"] = ok
        return ok

    def rlimits(self) -> bool:
        return resource is not None

    def snapshot(self) -> dict[str, Any]:
        return {
            "docker_binary": bool(self.binary("docker")),
            "docker_daemon": self.docker_daemon(),
            "podman": bool(self.binary("podman")),
            "gvisor_runsc": self.gvisor(),
            "wasmtime": bool(self.binary("wasmtime")),
            "unshare": bool(self.binary("unshare")),
            "bwrap": bool(self.binary("bwrap")),
            "bwrap_usable": self.bwrap(),
            "unshare_net": self.unshare_net(),
            "resource_module": self.rlimits(),
            "platform": sys.platform,
            "root": bool(os.geteuid() == 0) if hasattr(os, "geteuid") else False,
        }


probe = CapabilityProbe()


def scan_command(command: str, patterns: Sequence[str] = DANGEROUS_PATTERNS) -> list[str]:
    """Return the dangerous patterns a command matches (empty = clean)."""
    hits: list[str] = []
    text = command or ""
    for pat in patterns:
        try:
            if re.search(pat, text, re.I):
                hits.append(pat)
        except re.error:
            continue
    return hits


# ---------------------------------------------------------------------- sandbox
class Sandbox:
    """Ephemeral execution boundary shared by shell/file/skill tooling."""

    def __init__(self, policy: Optional[SandboxPolicy] = None, probe_: Optional[CapabilityProbe] = None):
        self.policy = policy or SandboxPolicy.from_config()
        self.probe = probe_ or probe
        self._sem = threading.Semaphore(max(1, int(os.getenv("HERMUS_SANDBOX_MAX_CONCURRENT", "4"))))
        self._active: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    @property
    def root(self) -> Path:
        return Path(config.resolve_path("data/sandboxes"))

    def _resolve_backend(self, policy: SandboxPolicy) -> tuple[str, str]:
        """Pick a backend and a human-readable reason."""
        want = (policy.backend or "auto").lower()
        if want not in VALID_BACKENDS:
            want = "auto"
        if want == "off":
            return "off", "sandboxing disabled by policy"
        if want == "auto":
            if self.probe.docker_daemon():
                extra = " + gVisor runtime" if (policy.runtime == "runsc" or self.probe.gvisor()) else ""
                return "docker", f"docker daemon available{extra}"
            if self.probe.has("podman"):
                return "podman", "podman available (rootless)"
            if self.probe.bwrap():
                return "bwrap", "bubblewrap available — read-only root + netns (no container daemon)"
            return "local", "no container runtime; hardened local execution"
        if want in ("docker", "podman", "bwrap"):
            if want == "docker":
                ok = self.probe.docker_daemon()
            elif want == "podman":
                ok = self.probe.has("podman")
            else:
                ok = self.probe.bwrap()
            if ok:
                return want, f"{want} requested and available"
            return "local", f"{want} requested but unavailable → hardened local execution"
        return "local", "hardened local execution"

    def _env(self, policy: SandboxPolicy, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        """Scrubbed environment: allowlist only, secrets never forwarded."""
        env: dict[str, str] = {}
        for key in policy.env_allowlist:
            if key in os.environ:
                env[key] = os.environ[key]
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        if not policy.network:
            # Blackhole proxies so naive HTTP clients fail fast instead of dialling out.
            env.update({
                "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
                "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                "NO_PROXY": "",
            })
        for k, v in (extra or {}).items():
            if SECRET_ENV_RE.search(str(k)) and not policy.network:
                continue  # never hand a secret to a sandboxed process
            env[str(k)] = str(v)
        return env

    def _preexec(self, policy: SandboxPolicy, timeout: int) -> Optional[Callable[[], None]]:
        """Build the child-process jail (POSIX rlimits + session + no-new-privs)."""
        if os.name != "posix":
            return None

        def _prctl_no_new_privs() -> None:
            try:
                import ctypes

                libc = ctypes.CDLL(None, use_errno=True)
                libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
            except Exception:
                pass

        def _setup() -> None:  # runs in the forked child
            try:
                os.setsid()
            except Exception:
                pass
            if policy.soft_no_new_privs:
                _prctl_no_new_privs()
            if resource is not None:
                soft_cpu = max(1, int(timeout))
                for name, value in (
                    ("RLIMIT_CPU", soft_cpu),
                ):
                    try:
                        resource.setrlimit(getattr(resource, name), (value, value + 2))
                    except Exception:
                        pass
                if policy.memory_mb:
                    limit = int(policy.memory_mb) * 1024 * 1024
                    try:
                        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
                    except Exception:
                        pass
                if policy.disk_mb:
                    limit = int(policy.disk_mb) * 1024 * 1024
                    try:
                        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
                    except Exception:
                        pass
                if policy.pids:
                    for attr in ("RLIMIT_NPROC", "RLIMIT_RTTIME"):
                        try:
                            resource.setrlimit(getattr(resource, attr), (int(policy.pids), int(policy.pids)))
                        except Exception:
                            pass
                try:
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 512))
                except Exception:
                    pass
            if policy.run_as_nobody and hasattr(os, "geteuid") and os.geteuid() == 0:
                try:
                    import pwd

                    pw = pwd.getpwnam("nobody")
                    os.setgid(int(pw.pw_gid))
                    os.setuid(int(pw.pw_uid))
                except Exception:
                    pass

        return _setup

    def _spawn_dir(self, sandbox_id: str) -> Path:
        d = self.root / sandbox_id
        (d / "out").mkdir(parents=True, exist_ok=True)
        return d

    @property
    def audit_log(self) -> Path:
        try:
            from .workspace import workspace

            return Path(workspace.dirs["logs"]) / "sandbox.jsonl"
        except Exception:
            return Path(config.resolve_path("data/logs/sandbox.jsonl"))

    def _audit(self, record: dict[str, Any]) -> None:
        record = {"ts": _now(), **record}
        try:
            from .workspace import workspace

            workspace.log("sandbox", record)
        except Exception:
            try:
                path = Path(config.resolve_path("data/logs/sandbox.jsonl"))
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str) + "\n")
            except Exception:
                pass

    # ----------------------------------------------------------------- public API
    def status(self) -> dict[str, Any]:
        backend, reason = self._resolve_backend(self.policy)
        caps = self.probe.snapshot()
        return {
            "configured": self.policy.backend,
            "backend": backend,
            "reason": reason,
            "capabilities": caps,
            "policy": self.policy.to_dict(),
            "active": len(self._active),
            "root": str(self.root),
            "audit_log": str(self.audit_log),
            "active_runs": [
                {"sandbox_id": k, **{a: b for a, b in v.items()}}
                for k, v in list(self._active.items())[:20]
            ],
            "note": (
                "local backend applies rlimits/session/no-new-privs and (when possible) "
                "drops network via unshare; it is defence in depth, not a VM. "
                "Install Docker (+ gVisor runsc) for kernel-level isolation."
                if backend == "local" else "container backend active"
            ),
        }

    def run(
        self,
        command: str,
        *,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        files: Optional[dict[str, str]] = None,
        input_text: Optional[str] = None,
        backend: Optional[str] = None,
        network: Optional[bool] = None,
        policy: Optional[dict[str, Any]] = None,
        allow_dangerous: bool = False,
        purpose: str = "shell",
    ) -> dict[str, Any]:
        """Execute ``command`` inside an ephemeral sandbox. Never raises."""
        t0 = time.time()
        pol = replace(self.policy, **(policy or {}))
        if backend:
            pol.backend = str(backend).lower()
        if network is not None:
            pol.network = bool(network)
        pol.timeout = int(timeout or pol.timeout)
        sandbox_id = f"sbx_{uuid.uuid4().hex[:10]}"
        chosen, reason = self._resolve_backend(pol)

        denied = scan_command(command, pol.deny_patterns)
        if denied and not allow_dangerous:
            self._audit({"sandbox_id": sandbox_id, "backend": chosen, "purpose": purpose,
                         "command": command[:400], "blocked": True, "matched": denied})
            return SandboxResult(
                success=False, returncode=126, backend=chosen, sandbox_id=sandbox_id,
                error=("blocked by sandbox policy — command matches dangerous pattern(s): "
                       f"{denied}. Re-run with allow_dangerous=true (audited) or "
                       "raise the permission decision via `hermus perms set <tool> allow`."),
                duration_ms=int((time.time() - t0) * 1000),
            ).to_dict()

        workdir, mounted_cwd = self._workdirs(pol, cwd, sandbox_id)
        if files:
            staged = 0
            for name, content in (files or {}).items():
                try:
                    safe = (workdir / str(name).lstrip("/")).resolve()
                    if pol.confine_to_workspace and workdir.exists() and \
                            str(safe) != str(workdir.resolve()) and \
                            not str(safe).startswith(str(workdir.resolve()) + os.sep):
                        continue          # refuse ../ escapes out of the scratch dir
                    safe.parent.mkdir(parents=True, exist_ok=True)
                    safe.write_text(str(content))
                    staged += 1
                except Exception:
                    continue
            if staged:
                # Staged input means "work on these files": run with the scratch dir
                # as cwd (the container path when a container backend is in play),
                # otherwise the jail's default cwd would not see them at all.
                base = mounted_cwd if chosen in ("docker", "podman") else str(workdir)
                command = f"cd {shlex.quote(str(base))} && {command}"

        if not self._sem.acquire(timeout=max(1.0, pol.timeout)):
            return SandboxResult(success=False, error="sandbox busy (concurrency cap reached)",
                                 backend=chosen, sandbox_id=sandbox_id).to_dict()
        try:
            with self._lock:
                self._active[sandbox_id] = {"started": _now(), "backend": chosen, "purpose": purpose}
            if chosen in ("docker", "podman"):
                res = self._run_container(chosen, command, pol, workdir, mounted_cwd, sandbox_id,
                                          env, input_text, reason)
            elif chosen == "bwrap":
                res = self._run_bwrap(command, pol, workdir, sandbox_id, env, input_text, reason)
            elif chosen == "off":
                res = self._run_raw(command, pol, workdir, sandbox_id, env, input_text, reason)
            else:
                res = self._run_local(command, pol, workdir, sandbox_id, env, input_text, reason)
        except Exception as e:
            res = SandboxResult(success=False, error=f"sandbox failure: {e}", backend=chosen,
                                sandbox_id=sandbox_id)
        finally:
            self._sem.release()
            with self._lock:
                self._active.pop(sandbox_id, None)
            if not pol.keep_artifacts:
                shutil.rmtree(str(workdir), ignore_errors=True)

        out = res.to_dict() if isinstance(res, SandboxResult) else dict(res)
        out["duration_ms"] = int((time.time() - t0) * 1000)
        out.setdefault("limits", {})[ "reason"] = reason
        self._audit({
            "sandbox_id": sandbox_id, "backend": out.get("backend"), "purpose": purpose,
            "command": command[:400], "returncode": out.get("returncode"),
            "timeout": out.get("timeout"), "blocked": False,
            "limits": out.get("limits"), "duration_ms": out.get("duration_ms"),
        })
        return out

    def run_python(self, code: str, **kw) -> dict[str, Any]:
        """Run a Python snippet under the same boundary (used by skill validation)."""
        kw.setdefault("purpose", "python")
        quoted = shlex.quote(code)
        return self.run(f"{shlex.quote(sys.executable or 'python3')} -c {quoted}", **kw)

    def run_wasm(self, module_path: str, *, args: Sequence[str] = (), timeout: int = 20) -> dict[str, Any]:
        """Optional WASI path: run a .wasm module with wasmtime (strictly isolated, no fs/net)."""
        if not self.probe.has("wasmtime"):
            return {"success": False, "error": "wasmtime not installed", "backend": "wasm"}
        cmd = ["wasmtime", "run", "--dir=.", "--env=", "-S", "threads=false", str(module_path), *args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=int(timeout))
            return {"success": r.returncode == 0, "stdout": r.stdout[:4000], "stderr": r.stderr[:2000],
                    "returncode": r.returncode, "backend": "wasm"}
        except Exception as e:
            return {"success": False, "error": str(e), "backend": "wasm"}

    # ------------------------------------------------------------------ backends
    def _workdirs(self, pol: SandboxPolicy, cwd: Optional[str], sandbox_id: str) -> tuple[Path, str]:
        workdir = self._spawn_dir(sandbox_id)
        mounted = str(cwd) if cwd else str(Path(config.base_dir))
        if pol.confine_to_workspace and cwd:
            try:
                base = Path(config.base_dir).resolve()
                target = Path(cwd).resolve()
                if not str(target).startswith(str(base)):
                    mounted = str(workdir)
            except Exception:
                mounted = str(workdir)
        return workdir, mounted

    def _docker_args(self, binary: str, command: str, pol: SandboxPolicy, workdir: Path,
                     mounted_cwd: str) -> tuple[list[str], str]:
        name = f"hermus-{sandbox_tag()}-{uuid.uuid4().hex[:6]}"
        args = [binary, "run", "--rm", "-i", "--name", name,
                "--label", "hermus.sandbox=1",
                f"--memory={max(32, int(pol.memory_mb))}m",
                f"--memory-swap={max(32, int(pol.memory_mb))}m",
                f"--cpus={float(pol.cpus):g}",
                f"--pids-limit={max(16, int(pol.pids))}",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--network=none" if not pol.network else "--network=bridge",
                "--workdir=/hermus"]
        if pol.runtime:
            args.append(f"--runtime={pol.runtime}")
        elif pol.backend in ("auto", "docker") and self.probe.gvisor():
            args.append("--runtime=runsc")
        if pol.read_only_rootfs:
            args += ["--read-only", f"--tmpfs=/tmp:rw,size={max(16, int(pol.tmpfs_size_mb))}m,noexec,nosuid"]
        for cap in pol.add_capabilities:
            args.append(f"--cap-add={cap}")
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        gid = os.getgid() if hasattr(os, "getgid") else 1000
        if binary == "podman":
            args.append("--userns=keep-id")
        args.append(f"--user={uid}:{gid}")
        args += ["-v", f"{workdir}:/hermus:{'ro' if pol.workspace_mode == 'ro' else 'rw'}"]
        if mounted_cwd and Path(mounted_cwd).is_dir() and pol.workspace_mode != "none":
            try:
                if Path(mounted_cwd).resolve() != workdir.resolve():
                    args += ["-v", f"{mounted_cwd}:/workspace:{'ro' if pol.workspace_mode == 'ro' else 'rw'}",
                             "--workdir=/workspace"]
            except Exception:
                pass
        args.append(pol.image or "alpine:latest")
        args += ["/bin/sh", "-lc", command]
        return args, name

    def _run_container(self, binary: str, command: str, pol: SandboxPolicy, workdir: Path,
                       mounted_cwd: str, sandbox_id: str, env: Optional[dict[str, str]],
                       input_text: Optional[str], reason: str) -> SandboxResult:
        args, name = self._docker_args(binary, command, pol, workdir, mounted_cwd)
        for k, v in (self._env(pol, env)).items():
            args[3:3] = ["-e", f"{k}={v}"]
        limits = {
            "cpus": pol.cpus, "memory_mb": pol.memory_mb, "pids": pol.pids,
            "disk_mb": pol.disk_mb, "read_only_rootfs": pol.read_only_rootfs,
            "network": pol.network, "runtime": pol.runtime or ("runsc" if self.probe.gvisor() else ""),
            "capabilities_dropped": list(pol.drop_capabilities),
            "image": pol.image or "alpine:latest",
        }
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=pol.timeout,
                input=input_text or "",
            )
            return SandboxResult(
                success=proc.returncode == 0,
                stdout=proc.stdout[: pol.max_output_chars],
                stderr=proc.stderr[: pol.max_output_chars // 2],
                returncode=proc.returncode, backend=binary, sandbox_id=sandbox_id,
                container=name, workdir=str(workdir), limits=limits,
                artifacts=str(workdir) if pol.keep_artifacts else "",
            )
        except subprocess.TimeoutExpired as e:
            _kill_container(binary, name)
            return SandboxResult(
                success=False, stdout=(e.stdout or b"").decode(errors="ignore")[: pol.max_output_chars]
                if isinstance(e.stdout, bytes) else str(e.stdout or "")[: pol.max_output_chars],
                stderr="container killed at timeout", returncode=124, timeout=True,
                backend=binary, sandbox_id=sandbox_id, container=name, limits=limits,
            )
        except Exception as e:
            return SandboxResult(success=False, error=f"{binary} run failed: {e}", backend=binary,
                                 sandbox_id=sandbox_id)

    def _exec_cwd(self, pol: SandboxPolicy, workdir: Path) -> str:
        """Where a jail runs: the project root when writable, else the scratch dir."""
        if pol.workspace_mode == "none":
            return str(workdir)
        root = Path(config.base_dir)
        return str(root) if root.is_dir() else str(workdir)

    def _run_local(self, command: str, pol: SandboxPolicy, workdir: Path, sandbox_id: str,
                   env: Optional[dict[str, str]], input_text: Optional[str], reason: str) -> SandboxResult:
        """Hardened local execution: rlimits + new session + no new privs."""
        argv: list[str] = ["/bin/sh", "-c", command]
        if not pol.network and self.probe.unshare_net():
            # A real network cut (empty netns), not just an env hint.
            argv = [self.probe.binary("unshare"), "-n", *argv]
        limits = {
            "memory_mb": pol.memory_mb, "cpu_seconds": pol.timeout, "pids": pol.pids,
            "disk_mb": pol.disk_mb, "network": pol.network,
            "network_dropped": argv[0].endswith("unshare"),
            "rlimits": resource is not None, "no_new_privs": pol.soft_no_new_privs,
            "setsid": os.name == "posix", "cwd": self._exec_cwd(pol, workdir), "reason": reason,
        }
        return self._exec(argv, "local", pol, workdir, sandbox_id, env, input_text, limits)

    def _run_bwrap(self, command: str, pol: SandboxPolicy, workdir: Path, sandbox_id: str,
                   env: Optional[dict[str, str]], input_text: Optional[str], reason: str) -> SandboxResult:
        """bubblewrap jail: read-only /, writable scratch, no network, clean env."""
        exec_cwd = self._exec_cwd(pol, workdir)
        argv = [
            self.probe.binary("bwrap") or "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--bind", str(workdir), str(workdir),
        ]
        if pol.workspace_mode == "rw" and exec_cwd != str(workdir):
            argv += ["--bind", exec_cwd, exec_cwd]
        if not pol.network:
            argv += ["--unshare-net"]
        argv += ["--clearenv", "--die-with-parent", "--setenv", "PATH", os.environ.get("PATH", "/usr/bin:/bin")]
        for k, v in (self._env(pol, env)).items():
            if k == "PATH":
                continue
            argv += ["--setenv", k, v]
        limits = {
            "memory_mb": pol.memory_mb, "cpu_seconds": pol.timeout, "pids": pol.pids,
            "disk_mb": pol.disk_mb, "network": pol.network, "network_dropped": not pol.network,
            "read_only_rootfs": pol.read_only_rootfs, "rlimits": resource is not None,
            "cwd": exec_cwd, "reason": reason, "tool": "bubblewrap",
        }
        argv += ["/bin/sh", "-c", command]
        return self._exec(argv, "bwrap", pol, workdir, sandbox_id, env, input_text, limits)

    def _exec(self, argv: list[str], backend: str, pol: SandboxPolicy, workdir: Path, sandbox_id: str,
              env: Optional[dict[str, str]], input_text: Optional[str],
              limits: dict[str, Any]) -> SandboxResult:
        """Shared hardened execution for the non-container backends."""
        env_final = self._env(pol, env)
        proc = None
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                cwd=limits.get("cwd") or str(workdir), env=env_final, stdin=subprocess.PIPE,
                preexec_fn=self._preexec(pol, pol.timeout),
            )
            try:
                out, err = proc.communicate(input=input_text or "", timeout=pol.timeout)
                rc, timed_out = proc.returncode, False
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                try:
                    out, err = proc.communicate(timeout=5)
                except Exception:
                    out, err = "", "killed after timeout"
                rc, timed_out = 124, True
            return SandboxResult(
                success=(rc == 0) and not timed_out,
                stdout=(out or "")[: pol.max_output_chars],
                stderr=(err or "")[: pol.max_output_chars // 2],
                returncode=int(rc if rc is not None else 1), timeout=bool(timed_out),
                backend="local", sandbox_id=sandbox_id, workdir=str(workdir), limits=limits,
                artifacts=str(workdir) if pol.keep_artifacts else "",
            )
        except FileNotFoundError as e:
            return SandboxResult(success=False, error=f"shell not available for sandbox exec: {e}",
                                 backend="local", sandbox_id=sandbox_id, limits=limits)
        except Exception as e:
            if proc is not None:
                _kill_group(proc)
            return SandboxResult(success=False, error=str(e), backend="local",
                                 sandbox_id=sandbox_id, limits=limits)

    def _run_raw(self, command: str, pol: SandboxPolicy, workdir: Path, sandbox_id: str,
                 env: Optional[dict[str, str]], input_text: Optional[str], reason: str) -> SandboxResult:
        try:
            proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                                  timeout=pol.timeout, cwd=str(workdir), input=input_text or "")
            return SandboxResult(success=proc.returncode == 0, stdout=proc.stdout[: pol.max_output_chars],
                                 stderr=proc.stderr[: pol.max_output_chars // 2],
                                 returncode=proc.returncode, backend="off", sandbox_id=sandbox_id,
                                 limits={"enforced": False, "reason": reason})
        except Exception as e:
            return SandboxResult(success=False, error=str(e), backend="off", sandbox_id=sandbox_id)


def sandbox_tag() -> str:
    return datetime.now().strftime("%H%M%S")


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _kill_container(binary: str, name: str) -> None:
    try:
        subprocess.run([binary, "kill", "-s", "KILL", name], capture_output=True, timeout=10)
    except Exception:
        pass


sandbox = Sandbox()
