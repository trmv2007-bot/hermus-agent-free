"""Local defensive scanning tools."""
from __future__ import annotations

from core.local_defense_scanner import scan_folder


def local_folder_defensive_scan(
    path: str,
    max_files: int = 500,
    max_bytes: int = 4096,
    follow_symlinks: bool = False,
    save_report: bool = False,
    mission_id: str = "",
) -> dict:
    """Read-only local folder scan for suspicious indicators.

    Returns file paths/reasons only, never file contents.
    """
    return scan_folder(
        path,
        max_files=max_files,
        max_bytes=max_bytes,
        follow_symlinks=follow_symlinks,
        save_report=save_report,
        mission_id=mission_id,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "local_folder_defensive_scan",
            "description": "Read-only defensive scan of an approved local folder for suspicious indicators. Returns paths/reasons only, never file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Approved local folder path, e.g. ~/Downloads"},
                    "max_files": {"type": "integer", "default": 500},
                    "max_bytes": {"type": "integer", "default": 4096},
                    "follow_symlinks": {"type": "boolean", "default": False},
                    "save_report": {"type": "boolean", "default": False, "description": "Write a Markdown report artifact"},
                    "mission_id": {"type": "string", "description": "Optional mission id to attach report evidence"},
                },
                "required": ["path"],
            },
        },
    }
]

TOOL_MAP = {"local_folder_defensive_scan": local_folder_defensive_scan}
