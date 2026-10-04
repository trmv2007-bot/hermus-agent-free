"""HERMUS Workshop workspace transport.

This is a thin HTTP projection over the canonical Workspace + ToolGateway
boundaries. It never becomes a second filesystem or execution owner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/workshop", tags=["workshop"])

_MAX_TREE_FILES = 500
_MAX_READ_BYTES = 200_000
_MAX_WRITE_BYTES = 500_000
_TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".scss",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".sh",
    ".ps1",
    ".sql",
    ".xml",
    ".ini",
    ".cfg",
    ".env.example",
    ".gitignore",
}


def _workspace_root(project: str | None = None) -> Path:
    from core.workspace import workspace

    name = str(project or workspace.current_project() or workspace.active_project() or "").strip()
    if not name:
        raise ValueError("no active workspace project")
    path = workspace.project_dir(name).resolve()
    if not (path / "project.yaml").exists():
        raise ValueError(f"project '{name}' not found")
    return path


def _safe_path(root: Path, relative_path: str) -> Path:
    raw = str(relative_path or "").strip()
    if not raw:
        raise ValueError("path required")
    candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes the active workspace")
    return candidate


def _entry(root: Path, path: Path) -> dict[str, Any]:
    rel = "." if path == root else str(path.relative_to(root)).replace("\\", "/")
    is_dir = path.is_dir()
    size = None
    if path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = None
    return {
        "name": path.name or root.name,
        "path": rel,
        "type": "directory" if is_dir else "file",
        "size": size,
    }


@router.get("/snapshot")
async def workshop_snapshot(project: str | None = None):
    from core.world_awareness import world_awareness
    from core.workspace import workspace

    requested = str(project or "").strip()
    try:
        projects = workspace.list_projects()
    except Exception:
        projects = []

    # A product UI needs a valid empty state. Opening Workshop before a project
    # exists/has been selected must not look like a broken route.
    if not requested and not (workspace.current_project() or workspace.active_project()):
        return {
            "success": True,
            "project": None,
            "root": None,
            "tree": [],
            "truncated": False,
            "skipped": 0,
            "projects": projects,
            "current": None,
            "world": world_awareness.status(),
        }

    try:
        root = _workspace_root(project)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)

    tree: list[dict[str, Any]] = []
    skipped = 0
    for path in sorted(root.rglob("*"), key=lambda p: (not p.is_dir(), str(p).lower())):
        if any(part.startswith(".git") for part in path.relative_to(root).parts):
            continue
        if any(part in {"__pycache__", "node_modules", ".venv"} for part in path.relative_to(root).parts):
            continue
        if len(tree) >= _MAX_TREE_FILES:
            skipped += 1
            continue
        try:
            tree.append(_entry(root, path))
        except OSError:
            skipped += 1

    world = world_awareness.status()
    return {
        "success": True,
        "project": root.name,
        "root": str(root),
        "tree": tree,
        "truncated": skipped > 0,
        "skipped": skipped,
        "projects": projects,
        "current": root.name,
        "world": world,
    }


@router.get("/file")
async def workshop_file(path: str, project: str | None = None):
    try:
        root = _workspace_root(project)
        target = _safe_path(root, path)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    if not target.exists():
        return JSONResponse({"success": False, "error": "file not found"}, status_code=404)
    if not target.is_file():
        return JSONResponse({"success": False, "error": "path is not a file"}, status_code=400)

    try:
        size = target.stat().st_size
        if size > _MAX_READ_BYTES:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"file exceeds workshop read limit ({_MAX_READ_BYTES} bytes)",
                    "size": size,
                },
                status_code=413,
            )
        data = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    return {
        "success": True,
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "content": data,
        "size": size,
        "editable": target.suffix.lower() in _TEXT_EXTENSIONS or target.name in {".gitignore", ".env.example"},
    }


@router.put("/file")
async def workshop_file_write(payload: dict[str, Any] | None = None):
    payload = payload or {}
    content = str(payload.get("content") or "")
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return JSONResponse({"success": False, "error": "content exceeds workshop write limit"}, status_code=413)

    try:
        root = _workspace_root(payload.get("project"))
        target = _safe_path(root, str(payload.get("path") or ""))
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    target.parent.mkdir(parents=True, exist_ok=True)

    # The Workshop editor is an explicit user action against a path already
    # confined to the active project. Keep the safety boundary here (workspace
    # root + size + text-file check) instead of routing the interactive editor
    # through a tool permission that can leave the UI permanently waiting for an
    # unrelated approval request.
    if target.suffix.lower() not in _TEXT_EXTENSIONS and target.name not in {".gitignore", ".env.example"}:
        return JSONResponse({"success": False, "error": "workshop can only edit supported text files"}, status_code=415)

    try:
        current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        if target.exists() and current == content:
            return {"success": True, "changed": False, "path": str(target.relative_to(root)).replace("\\", "/"), "size": len(content.encode("utf-8"))}
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    return {
        "success": True,
        "changed": True,
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "size": len(content.encode("utf-8")),
    }


@router.post("/project/use")
async def workshop_project_use(payload: dict[str, Any] | None = None):
    from core.workspace import workspace

    payload = payload or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return JSONResponse({"success": False, "error": "project name required"}, status_code=400)
    result = workspace.set_current_project(name)
    if not result.get("success"):
        return JSONResponse(result, status_code=404)
    return result


__all__ = ["router"]
