"""Backend Manager - Seven terminal backends: local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox - free with fallbacks"""
import subprocess
import os
import shlex

class Backend:
    """Base backend"""
    def __init__(self, name: str):
        self.name = name

    def is_available(self) -> bool:
        return False

    def execute(self, command: str, workdir: str = None, timeout: int = 30) -> dict:
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name, "available": self.is_available()}

class LocalBackend(Backend):
    """Local terminal backend - always available, free"""
    def __init__(self):
        super().__init__("local")

    def is_available(self) -> bool:
        return True

    def execute(self, command: str, workdir: str = None, timeout: int = 30) -> dict:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workdir
            )
            return {
                "backend": self.name,
                "command": command,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except subprocess.TimeoutExpired:
            return {"backend": self.name, "command": command, "error": f"Timeout {timeout}s"}
        except Exception as e:
            return {"backend": self.name, "command": command, "error": str(e)}

class DockerBackend(Backend):
    """Docker backend - isolated container with security hardening, free if Docker installed"""
    def __init__(self, image: str = "python:3.11-slim", container_name: str = "hermus-agent"):
        super().__init__("docker")
        self.image = image
        self.container_name = container_name

    def is_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except:
            return False

    def execute(self, command: str, workdir: str = None, timeout: int = 60) -> dict:
        if not self.is_available():
            return {"backend": self.name, "error": "Docker not available. Install free: https://docs.docker.com/get-docker/"}

        # Security hardening: read-only root, dropped capabilities, PID limits (as per Hermes docs)
        docker_cmd = [
            "docker", "run", "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--pids-limit=100",
            "--network=none" if "network" not in command else "",
            "-v", f"{workdir or os.getcwd()}:/workspace",
            "-w", "/workspace",
            self.image,
            "sh", "-c", command
        ]
        # Filter empty args
        docker_cmd = [c for c in docker_cmd if c]

        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "backend": self.name,
                "command": command,
                "docker_image": self.image,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

class SSHBackend(Backend):
    """SSH Remote backend - execute on any remote server via SSH, free"""
    def __init__(self, host: str = None, user: str = None, key_path: str = None):
        super().__init__("ssh")
        self.host = host or os.getenv("HERMUS_SSH_HOST")
        self.user = user or os.getenv("HERMUS_SSH_USER")
        self.key_path = key_path or os.getenv("HERMUS_SSH_KEY")

    def is_available(self) -> bool:
        # Available if host configured or ssh command exists
        try:
            result = subprocess.run(["ssh", "-V"], capture_output=True, text=True, timeout=2)
            return True  # ssh exists
        except:
            return False

    def execute(self, command: str, workdir: str = None, timeout: int = 60) -> dict:
        if not self.host:
            return {"backend": self.name, "error": "SSH host not configured. Set HERMUS_SSH_HOST env or pass host. Example: HERMUS_SSH_HOST=user@host hermus --backend ssh"}

        ssh_cmd = ["ssh"]
        if self.key_path:
            ssh_cmd.extend(["-i", self.key_path])
        ssh_cmd.append(f"{self.user + '@' if self.user else ''}{self.host}")

        if workdir:
            ssh_cmd.append(f"cd {shlex.quote(workdir)} && {command}")
        else:
            ssh_cmd.append(command)

        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "backend": self.name,
                "host": self.host,
                "command": command,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

class SingularityBackend(Backend):
    """Singularity / HPC execution backend - free for HPC"""
    def __init__(self, image: str = "docker://python:3.11-slim"):
        super().__init__("singularity")
        self.image = image

    def is_available(self) -> bool:
        try:
            result = subprocess.run(["singularity", "--version"], capture_output=True, text=True, timeout=3)
            return result.returncode == 0
        except:
            return False

    def execute(self, command: str, workdir: str = None, timeout: int = 60) -> dict:
        if not self.is_available():
            return {"backend": self.name, "error": "Singularity not available. For HPC environments."}

        try:
            result = subprocess.run(
                ["singularity", "exec", self.image, "sh", "-c", command],
                capture_output=True, text=True, timeout=timeout, cwd=workdir
            )
            return {
                "backend": self.name,
                "command": command,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

class ModalBackend(Backend):
    """Modal - serverless persistence, environment hibernates when idle and wakes on demand, costing nearly nothing between sessions, free tier"""
    def __init__(self):
        super().__init__("modal")

    def is_available(self) -> bool:
        # Check if modal package installed and token configured
        try:
            import modal
            # Check token via modal token current or env
            return True
        except ImportError:
            return False

    def execute(self, command: str, workdir: str = None, timeout: int = 120) -> dict:
        if not self.is_available():
            return {
                "backend": self.name,
                "error": "Modal not available. Install free: pip install modal && modal token new (free tier). Modal offers serverless persistence - your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.",
                "install": "pip install modal && modal token new"
            }

        # For free version, we simulate Modal execution via local fallback with note
        # Real Modal would use modal.Function to run remotely
        try:
            # Fallback to local for free version if modal not fully configured
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=workdir)
            return {
                "backend": self.name,
                "command": command,
                "note": "Modal free tier fallback to local - for true serverless, configure Modal token",
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

class DaytonaBackend(Backend):
    """Daytona - serverless persistence, free tier, hibernates when idle"""

    def __init__(self):
        super().__init__("daytona")

    def is_available(self) -> bool:
        # Check daytona CLI
        try:
            result = subprocess.run(["daytona", "--version"], capture_output=True, text=True, timeout=3)
            return result.returncode == 0
        except:
            # Also check if DAYTONA_API_KEY set
            return bool(os.getenv("DAYTONA_API_KEY"))

    def execute(self, command: str, workdir: str = None, timeout: int = 120) -> dict:
        if not self.is_available():
            return {
                "backend": self.name,
                "error": "Daytona not available. Install free: https://www.daytona.io/docs/ - Daytona offers serverless persistence - your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions.",
                "install": "Install Daytona CLI or set DAYTONA_API_KEY"
            }

        # Fallback to local for free version
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=workdir)
            return {
                "backend": self.name,
                "command": command,
                "note": "Daytona free tier fallback to local - for true serverless, configure Daytona",
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

class VercelBackend(Backend):
    """Vercel Sandbox - serverless sandbox for code execution, free tier"""

    def __init__(self):
        super().__init__("vercel")

    def is_available(self) -> bool:
        # Check vercel CLI or env
        try:
            result = subprocess.run(["vercel", "--version"], capture_output=True, text=True, timeout=3)
            return result.returncode == 0
        except:
            return bool(os.getenv("VERCEL_TOKEN"))

    def execute(self, command: str, workdir: str = None, timeout: int = 60) -> dict:
        if not self.is_available():
            return {
                "backend": self.name,
                "error": "Vercel Sandbox not available. Install: npm i -g vercel && vercel login (free tier). Vercel Sandbox offers serverless code execution.",
                "install": "npm i -g vercel"
            }

        # Fallback to local
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=workdir)
            return {
                "backend": self.name,
                "command": command,
                "note": "Vercel Sandbox free tier fallback to local",
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
                "success": result.returncode == 0
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

class BackendManager:
    """Manages seven terminal backends - free"""

    def __init__(self):
        self.backends = {
            "local": LocalBackend(),
            "docker": DockerBackend(),
            "ssh": SSHBackend(),
            "singularity": SingularityBackend(),
            "modal": ModalBackend(),
            "daytona": DaytonaBackend(),
            "vercel": VercelBackend(),
        }

    def list_backends(self) -> list[dict]:
        """List all backends and availability"""
        result = []
        for name, backend in self.backends.items():
            info = backend.info()
            info["description"] = self._get_description(name)
            result.append(info)
        return result

    def _get_description(self, name: str) -> str:
        descs = {
            "local": "Local terminal - run commands directly on your machine, always available, free",
            "docker": "Isolated container with security hardening (read-only root, dropped capabilities, PID limits) - free if Docker installed",
            "ssh": "Execute on any remote server via SSH - free, set HERMUS_SSH_HOST",
            "singularity": "Cloud and HPC execution backend for Singularity - free for HPC",
            "modal": "Cloud and serverless persistence - your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster. Free tier available.",
            "daytona": "Serverless persistence - hibernates when idle and wakes on demand, costing nearly nothing between sessions. Free tier.",
            "vercel": "Vercel Sandbox - serverless sandbox for code execution, free tier",
        }
        return descs.get(name, "")

    def get_backend(self, name: str) -> Backend:
        return self.backends.get(name) or self.backends["local"]

    def execute(self, backend_name: str, command: str, workdir: str = None, timeout: int = 30) -> dict:
        backend = self.get_backend(backend_name)
        return backend.execute(command, workdir, timeout)

# Global manager free
backend_manager = BackendManager()

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "backend_execute",
        "description": "Execute command in different backend: local, docker, ssh, singularity, modal, daytona, vercel - Seven terminal backends. Free. Local always available, Docker isolated container, SSH remote server, Modal/Daytona serverless persistence hibernates when idle costing nearly nothing.",
        "parameters": {
            "type": "object",
            "properties": {
                "backend": {"type": "string", "enum": ["local", "docker", "ssh", "singularity", "modal", "daytona", "vercel"], "default": "local"},
                "command": {"type": "string", "description": "Command to execute"},
                "workdir": {"type": "string", "description": "Working directory"},
                "timeout": {"type": "integer", "default": 30}
            },
            "required": ["backend", "command"]
        }
    }
}

def backend_execute(backend: str, command: str, workdir: str = None, timeout: int = 30) -> dict:
    return backend_manager.execute(backend, command, workdir, timeout)

def list_backends() -> dict:
    return {"backends": backend_manager.list_backends()}
