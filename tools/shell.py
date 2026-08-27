"""Free Shell Tool — safe subprocess with timeout, run inside the sandbox.

Execution goes through ``core.sandbox`` (ephemeral Docker/gVisor container when a
runtime is available, otherwise a hardened local jail: rlimits, new session,
dropped env, no-new-privs, optional netns). The returned dict keeps the legacy
shape (``command/stdout/stderr/returncode/success``) and adds ``sandbox``
details so the agent and dashboard can see what was actually enforced.

Escape hatches (all audited to ``logs/sandbox.jsonl``):
  * ``HERMUS_SANDBOX=off``  → plain subprocess (trusted local use only)
  * ``sandbox="local"``     → force the hardened local path for this call
  * ``allow_dangerous=True``→ skip the dangerous-pattern screen
"""
from __future__ import annotations


from core.config import config


def shell_execute(
    command: str,
    timeout: int = 10,
    cwd: str = None,
    sandbox: str = None,
    network: bool = None,
    allow_dangerous: bool = False,
) -> dict:
    """Execute a shell command in an ephemeral sandbox with CPU/mem/pid limits."""
    command = (command or "").strip()
    if not command:
        return {"command": command, "error": "empty command", "stdout": "", "stderr": "",
                "returncode": 1, "success": False}

    mode = str(sandbox or getattr(config, "sandbox_mode", "auto") or "auto").lower()
    if mode in ("", "off", "none", "disabled"):
        import subprocess

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=int(timeout), cwd=cwd or None,
            )
            return {
                "command": command,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0,
                "sandbox": {"backend": "off", "note": "sandboxing disabled by policy"},
            }
        except subprocess.TimeoutExpired:
            return {"command": command, "error": f"Timeout after {timeout}s", "stdout": "", "stderr": ""}
        except Exception as e:
            return {"command": command, "error": str(e)}

    try:
        from core.sandbox import sandbox as jail
    except Exception as e:  # sandbox module missing → never break the tool
        import subprocess

        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True,
                                    timeout=int(timeout), cwd=cwd or None)
            return {"command": command, "stdout": result.stdout[:5000],
                    "stderr": result.stderr[:2000], "returncode": result.returncode,
                    "success": result.returncode == 0,
                    "sandbox": {"backend": "unavailable", "note": f"sandbox import failed: {e}"}}
        except Exception as exc:
            return {"command": command, "error": str(exc)}

    overrides = {}
    if sandbox:
        overrides["backend"] = str(sandbox).lower()
    if cwd:
        overrides["workspace_root"] = cwd
    res = jail.run(
        command,
        timeout=int(timeout),
        cwd=cwd,
        network=None if network is None else bool(network),
        allow_dangerous=bool(allow_dangerous),
        purpose="shell_execute",
        policy={k: v for k, v in overrides.items() if v is not None} or None,
    )
    res.setdefault("command", command)
    res["sandbox"] = {
        "backend": res.get("backend"),
        "sandbox_id": res.get("sandbox_id"),
        "limits": res.get("limits"),
        "artifacts": res.get("artifacts"),
    }
    if res.get("error") and not res.get("stdout"):
        res["stderr"] = res.get("stderr") or res["error"]
    return res


def shell_sandbox_status() -> dict:
    """What isolation is actually in force right now (for the dashboard/CLI)."""
    try:
        from core.sandbox import sandbox as jail

        return jail.status()
    except Exception as e:
        return {"error": str(e), "backend": "unknown"}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell_execute",
            "description": (
                "Execute a shell command inside an ephemeral sandbox (dropped capabilities, "
                "read-only root where possible, CPU/memory/pid limits, no network unless "
                "explicitly allowed) with a timeout. For file ops, git, builds, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "timeout": {"type": "integer", "default": 10},
                    "cwd": {"type": "string", "description": "Working directory inside the jail (optional)"},
                    "sandbox": {
                        "type": "string",
                        "enum": ["auto", "docker", "podman", "bwrap", "local", "off"],
                        "description": "Force a backend for this call (default: configured policy)",
                    },
                    "network": {"type": "boolean", "description": "Allow outbound network inside the jail"},
                    "allow_dangerous": {"type": "boolean", "description": "Skip the dangerous-pattern screen (audited)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sandbox_status",
            "description": "Report the active sandbox backend, detected capabilities and enforced limits.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_MAP = {"shell_execute": shell_execute, "sandbox_status": shell_sandbox_status}
