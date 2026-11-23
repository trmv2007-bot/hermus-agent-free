"""Free File Tools - no API key - Optimized with file cache"""

from pathlib import Path

from core.cache import file_cache


def file_read(path: str) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if p.is_dir():
            files = [str(x) for x in p.iterdir()]
            return {"type": "directory", "path": path, "files": files[:50]}
        # Use optimized file cache
        content = file_cache.read(path)
        if content is None:
            content = p.read_text(encoding="utf-8", errors="ignore")[:10000]
        else:
            content = content[:10000]
        return {"type": "file", "path": path, "content": content, "size": p.stat().st_size}
    except Exception as e:
        return {"error": str(e)}


def file_write(path: str, content: str) -> dict:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        # Clear file cache for this path - optimize
        if path in file_cache.cache:
            del file_cache.cache[path]
        return {"success": True, "path": path, "size": len(content)}
    except Exception as e:
        return {"error": str(e)}


def file_edit(path: str, old_text: str, new_text: str) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        content = file_cache.read(path) or p.read_text(encoding="utf-8")
        if old_text not in content:
            return {"error": f"Old text not found in {path}"}
        new_content = content.replace(old_text, new_text, 1)
        p.write_text(new_content, encoding="utf-8")
        # Clear cache
        if path in file_cache.cache:
            del file_cache.cache[path]
        return {"success": True, "path": path}
    except Exception as e:
        return {"error": str(e)}


def file_search(query: str, directory: str = ".") -> dict:
    try:
        p = Path(directory)
        matches = []
        for file in p.rglob("*"):
            if file.is_file() and query.lower() in file.name.lower():
                matches.append(str(file))
                if len(matches) >= 20:
                    break
        return {"matches": matches, "count": len(matches)}
    except Exception as e:
        return {"error": str(e)}


def append_file(path: str, content: str) -> dict:
    """Append to a file, creating it when absent. Journaled by the tool registry."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="") as handle:
            handle.write(content)
        file_cache.cache.pop(path, None)
        return {"success": True, "path": path, "appended": len(content), "size": p.stat().st_size}
    except Exception as e:
        return {"error": str(e)}


def delete_file(path: str) -> dict:
    """Delete one file. Journaled by the tool registry before the unlink."""
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if p.is_dir():
            return {"error": f"{path} is a directory; refusing to remove a tree"}
        p.unlink()
        file_cache.cache.pop(path, None)
        return {"success": True, "path": path}
    except Exception as e:
        return {"error": str(e)}


def move_file(path: str, destination: str) -> dict:
    """Move one file. Journaled by the tool registry before the rename."""
    try:
        p = Path(path)
        dest = Path(destination)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        p.replace(dest)
        file_cache.cache.pop(path, None)
        return {"success": True, "path": path, "destination": str(dest)}
    except Exception as e:
        return {"error": str(e)}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read a file or list directory",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "Edit file by replacing old_text with new_text",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "Append content to a file, creating it when absent",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete one file (journaled; undoable)",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move one file to a new path (journaled; undoable)",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "destination": {"type": "string"}},
                "required": ["path", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "Search files by name",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "directory": {"type": "string", "default": "."}},
                "required": ["query"],
            },
        },
    },
]
