"""Updater - If you update anything in GitHub, it should show update in dashboard and CLI too - Free"""

import subprocess
import requests
import os

from .config import config

class Updater:
    """Check for GitHub updates and show in dashboard and CLI - free"""

    def __init__(self, repo_owner: str = "trmv2007-bot", repo_name: str = "hermus-agent-free"):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.repo_url = f"https://github.com/{repo_owner}/{repo_name}"
        self.api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}"

    def get_local_commit(self) -> dict:
        """Get local current commit via git rev-parse HEAD"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(config.resolve_path("."))
            )
            if result.returncode == 0:
                commit = result.stdout.strip()
                # Get commit message and date
                result2 = subprocess.run(
                    ["git", "log", "-1", "--format=%H|%s|%an|%ad", "--date=short"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=str(config.resolve_path("."))
                )
                if result2.returncode == 0:
                    parts = result2.stdout.strip().split("|")
                    return {
                        "commit": parts[0] if len(parts) > 0 else commit,
                        "message": parts[1] if len(parts) > 1 else "",
                        "author": parts[2] if len(parts) > 2 else "",
                        "date": parts[3] if len(parts) > 3 else "",
                        "short": commit[:7]
                    }
                return {"commit": commit, "short": commit[:7]}
            else:
                # Not a git repo or no commits
                return {"commit": "unknown", "error": result.stderr[:200]}
        except Exception as e:
            return {"commit": "unknown", "error": str(e)}

    def get_remote_commit(self) -> dict:
        """Get remote latest commit via GitHub API - free, no API key needed for public repos, but private needs token"""
        try:
            # Try via git ls-remote first (works with token if private)
            # Use GITHUB_TOKEN if available
            token = os.getenv("GITHUB_TOKEN") or config.github_token if hasattr(config, 'github_token') else None
            # For free version, try API without token first (public repo)
            headers = {"User-Agent": "Hermus Free Updater"}
            if token:
                headers["Authorization"] = f"token {token}"

            # Get latest commit from main branch via API
            url = f"{self.api_url}/commits/main"
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "commit": data.get("sha",""),
                    "short": data.get("sha","")[:7],
                    "message": data.get("commit",{}).get("message",""),
                    "author": data.get("commit",{}).get("author",{}).get("name",""),
                    "date": data.get("commit",{}).get("author",{}).get("date",""),
                    "url": data.get("html_url","")
                }
            elif resp.status_code == 404:
                # Try via git ls-remote as fallback
                result = subprocess.run(
                    ["git", "ls-remote", self.repo_url, "main"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout:
                    commit = result.stdout.split()[0]
                    return {"commit": commit, "short": commit[:7], "method": "git ls-remote"}
                return {"error": f"GitHub API 404: {resp.text[:200]}", "status_code": 404}
            else:
                return {"error": f"GitHub API {resp.status_code}: {resp.text[:500]}", "status_code": resp.status_code}
        except Exception as e:
            return {"error": str(e)}

    def check_for_updates(self) -> dict:
        """Check if local is behind remote - shows update in dashboard and CLI"""
        local = self.get_local_commit()
        remote = self.get_remote_commit()

        if "error" in local and local["commit"] == "unknown":
            return {
                "update_available": False,
                "error": f"Local git error: {local.get('error')}",
                "local": local,
                "remote": remote,
                "message": "Not a git repo or no commits - cannot check updates"
            }

        if "error" in remote:
            return {
                "update_available": False,
                "error": f"Remote check failed: {remote.get('error')}",
                "local": local,
                "remote": remote,
                "message": f"Failed to check remote: {remote.get('error','')[:100]}"
            }

        local_commit = local.get("commit","")
        remote_commit = remote.get("commit","")

        if not local_commit or not remote_commit:
            return {
                "update_available": False,
                "local": local,
                "remote": remote,
                "message": "Could not get commits"
            }

        if local_commit == remote_commit:
            return {
                "update_available": False,
                "up_to_date": True,
                "local": local,
                "remote": remote,
                "message": f"Up to date! Local {local.get('short')} == Remote {remote.get('short')}",
                "local_commit": local_commit,
                "remote_commit": remote_commit
            }
        else:
            # Check how many commits behind via git rev-list or API compare
            try:
                # Try git rev-list --count local..remote to get behind count if remote is fetched
                # First fetch to get remote info without merging
                subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, timeout=15, cwd=str(config.resolve_path(".")))
                result = subprocess.run(
                    ["git", "rev-list", "--count", f"{local_commit}..{remote_commit}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=str(config.resolve_path("."))
                )
                behind_count = int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else 1
            except:
                behind_count = 1  # At least 1 behind

            return {
                "update_available": True,
                "up_to_date": False,
                "local": local,
                "remote": remote,
                "behind_by": behind_count,
                "message": f"Update available! Local {local.get('short')} behind remote {remote.get('short')} by {behind_count} commit(s). Remote: {remote.get('message','')[:80]} by {remote.get('author','')} on {remote.get('date','')[:10]}",
                "local_commit": local_commit,
                "remote_commit": remote_commit,
                "remote_message": remote.get("message",""),
                "remote_url": remote.get("url",""),
                "action": "Run 'hermus update' or 'git pull' to update, shows in dashboard and CLI"
            }

    def update(self) -> dict:
        """Update from GitHub - pulls latest and reinstalls dependencies - like hermes update - free"""
        try:
            # Git pull
            result_pull = subprocess.run(
                ["git", "pull", "origin", "main"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(config.resolve_path("."))
            )

            # Pip install requirements
            result_pip = subprocess.run(
                ["pip", "install", "-r", "requirements.txt", "--quiet"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(config.resolve_path("."))
            )

            # Get new local commit after pull
            new_local = self.get_local_commit()

            return {
                "success": result_pull.returncode == 0,
                "pull_stdout": result_pull.stdout[:2000],
                "pull_stderr": result_pull.stderr[:1000],
                "pip_stdout": result_pip.stdout[:1000],
                "pip_stderr": result_pip.stderr[:1000],
                "new_commit": new_local,
                "message": f"Updated via git pull - new commit {new_local.get('short')} - {new_local.get('message','')[:80]}" if result_pull.returncode == 0 else f"Update failed: {result_pull.stderr[:500]}"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

# Global updater free
updater = Updater()

# Allow custom repo owner/name via env or config
def get_updater_for_current_repo() -> Updater:
    """Try to detect current repo owner/name from git remote origin"""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(config.resolve_path("."))
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Parse https://github.com/owner/repo.git or git@github.com:owner/repo.git
            import re
            # https://github.com/owner/repo.git
            m = re.search(r'github\.com[:/]([^/]+)/([^/.]+)', url)
            if m:
                owner = m.group(1)
                repo = m.group(2).replace('.git','')
                return Updater(repo_owner=owner, repo_name=repo)
    except:
        pass
    return Updater()
