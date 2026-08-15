"""Standardised computer-control benchmark tasks.

Each task is a realistic desktop interaction that Hermus might be asked to
perform.  Tasks span difficulty levels and failure modes so the benchmark
measures not just success but recovery capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskSpec:
    """Specification for one benchmark task."""

    id: str
    category: str          # app, browser, file, install, dialog, recovery, multi
    difficulty: int        # 1=easy, 2=medium, 3=hard
    prompt: str            # The natural-language instruction for the agent
    expected_states: List[str] = field(default_factory=list)
    failure_modes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    timeout: int = 120     # Max seconds
    min_steps: int = 1
    max_steps: int = 30
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "difficulty": self.difficulty,
            "prompt": self.prompt,
            "expected_states": list(self.expected_states),
            "failure_modes": list(self.failure_modes),
            "tags": list(self.tags),
            "timeout": self.timeout,
            "min_steps": self.min_steps,
            "max_steps": self.max_steps,
            "description": self.description or self.prompt[:80],
        }


# ---------------------------------------------------------------------------
# 30 benchmark tasks
# ---------------------------------------------------------------------------

COMPUTER_TASKS: List[TaskSpec] = [
    # === APP CATEGORY: Launch and verify applications ===
    TaskSpec(
        id="COMPUTER-001",
        category="app",
        difficulty=1,
        prompt="Open the Calculator application and verify it is running",
        expected_states=["Calculator window is visible", "Calculator shows its interface"],
        failure_modes=["app_not_found", "app_crashed", "wrong_window"],
        tags=["open_app", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-002",
        category="app",
        difficulty=1,
        prompt="Open the Text Editor (Notepad) and type 'Hello Hermus'",
        expected_states=["Notepad is open", "Text 'Hello Hermus' is visible in the editor"],
        failure_modes=["app_not_found", "input_focus_missed", "wrong_window"],
        tags=["open_app", "type_text", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-003",
        category="app",
        difficulty=2,
        prompt="Open the File Manager, navigate to the Downloads folder, and list the files visible",
        expected_states=["File Manager is open", "Downloads folder is shown", "Files are listed"],
        failure_modes=["wrong_window", "navigation_failed", "timeout"],
        tags=["open_app", "navigate", "verify"],
    ),

    # === BROWSER: Web navigation tasks ===
    TaskSpec(
        id="COMPUTER-004",
        category="browser",
        difficulty=1,
        prompt="Open the web browser and go to example.com",
        expected_states=["Browser is open", "example.com is loaded"],
        failure_modes=["app_not_found", "url_not_loaded", "wrong_window"],
        tags=["open_browser", "navigate"],
    ),
    TaskSpec(
        id="COMPUTER-005",
        category="browser",
        difficulty=2,
        prompt="Open Firefox or Chrome, go to 'wikipedia.org', and search for 'Python programming'",
        expected_states=["Browser is open", "Wikipedia is loaded", "Search results are visible"],
        failure_modes=["app_not_found", "search_failed", "wrong_window"],
        tags=["browser", "search", "multi_step"],
    ),
    TaskSpec(
        id="COMPUTER-006",
        category="browser",
        difficulty=2,
        prompt="Open the browser, navigate to any website, and zoom in using Ctrl+Plus",
        expected_states=["Browser is open", "Page is zoomed in"],
        failure_modes=["hotkey_not_recognized", "wrong_window"],
        tags=["browser", "hotkey"],
    ),

    # === DIALOG: Handle popups and prompts ===
    TaskSpec(
        id="COMPUTER-007",
        category="dialog",
        difficulty=2,
        prompt="Press Escape to dismiss any popup or dialog that might be open, then verify it's gone",
        expected_states=["No dialog or popup is visible"],
        failure_modes=["no_dialog_to_close", "dialog_persists"],
        tags=["dialog", "dismiss", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-008",
        category="dialog",
        difficulty=3,
        prompt="There might be a permission prompt on screen. Click 'Allow' or 'Yes' to accept it",
        expected_states=["Permission prompt is no longer visible"],
        failure_modes=["wrong_button_clicked", "dialog_not_found"],
        tags=["dialog", "permission", "click_target"],
    ),

    # === FILE: Download and manage files ===
    TaskSpec(
        id="COMPUTER-009",
        category="file",
        difficulty=1,
        prompt="Open the Downloads folder using the File Manager",
        expected_states=["Downloads folder is open and visible"],
        failure_modes=["app_not_found", "navigation_failed"],
        tags=["file", "navigate"],
    ),
    TaskSpec(
        id="COMPUTER-010",
        category="file",
        difficulty=2,
        prompt="Create a new folder on the Desktop named 'Hermus-Test'",
        expected_states=["A new folder named 'Hermus-Test' is on the Desktop"],
        failure_modes=["folder_not_created", "wrong_location"],
        tags=["file", "create", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-011",
        category="file",
        difficulty=2,
        prompt="Find any text file on the Desktop, open it, and read its contents",
        expected_states=["A text file is open", "File contents are visible"],
        failure_modes=["no_text_file_found", "app_not_found", "wrong_window"],
        tags=["file", "open", "read"],
    ),

    # === INSTALL: Software installation simulation ===
    TaskSpec(
        id="COMPUTER-012",
        category="install",
        difficulty=2,
        prompt="Check if Calculator is installed. If not, find and install it",
        expected_states=["Calculator is installed and running"],
        failure_modes=["already_installed", "install_failed", "wrong_source"],
        tags=["install", "check", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-013",
        category="install",
        difficulty=3,
        prompt="Download a small portable app from the web, extract it, and run it",
        expected_states=["The program is running and its window is visible"],
        failure_modes=["download_failed", "extract_failed", "app_not_found"],
        tags=["install", "download", "extract", "multi_step"],
    ),

    # === RECOVERY: Error handling and repair ===
    TaskSpec(
        id="COMPUTER-014",
        category="recovery",
        difficulty=2,
        prompt="Try to open 'nonexistent-app' and handle the error gracefully",
        expected_states=["The error dialog is dismissed", "Agent reports the app was not found"],
        failure_modes=["app_not_found", "stuck_on_error"],
        tags=["recovery", "error_handling"],
    ),
    TaskSpec(
        id="COMPUTER-015",
        category="recovery",
        difficulty=3,
        prompt="The last click may have failed. Recover by finding and clicking the correct target again",
        expected_states=["The correct target was clicked successfully"],
        failure_modes=["stale_target", "wrong_target"],
        tags=["recovery", "retry", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-016",
        category="recovery",
        difficulty=3,
        prompt="If any application window is minimized, restore it and continue",
        expected_states=["All application windows are restored"],
        failure_modes=["window_not_found", "restore_failed"],
        tags=["recovery", "window_management"],
    ),
    TaskSpec(
        id="COMPUTER-017",
        category="recovery",
        difficulty=3,
        prompt="If a dialog is blocking the screen, dismiss it before proceeding with the original task",
        expected_states=["Dialog is dismissed", "Original task can continue"],
        failure_modes=["dialog_not_handled"],
        tags=["recovery", "dialog", "blocking"],
    ),

    # === MULTI-STEP: Complex sequences ===
    TaskSpec(
        id="COMPUTER-018",
        category="multi",
        difficulty=2,
        prompt="Open the web browser, go to any website, and take a screenshot",
        expected_states=["Browser is open", "Website is loaded"],
        failure_modes=["app_not_found", "navigation_failed"],
        tags=["multi_step", "browser"],
    ),
    TaskSpec(
        id="COMPUTER-019",
        category="multi",
        difficulty=2,
        prompt="Open Notepad, type 'Task 1 complete', then open Calculator and verify both are running",
        expected_states=["Notepad has text", "Calculator is visible", "Both apps are running"],
        failure_modes=["app_not_found", "wrong_window"],
        tags=["multi_step", "multiple_apps", "verify"],
    ),
    TaskSpec(
        id="COMPUTER-020",
        category="multi",
        difficulty=3,
        prompt="Open the browser, find the downloads page, and check if any downloads completed",
        expected_states=["Browser downloads page is visible", "Download status is visible"],
        failure_modes=["navigation_failed", "wrong_window"],
        tags=["multi_step", "browser", "downloads"],
    ),
    TaskSpec(
        id="COMPUTER-021",
        category="multi",
        difficulty=3,
        prompt="Switch to the currently open browser window, refresh the page, and verify it reloaded",
        expected_states=["Browser page was refreshed", "Page content is visible"],
        failure_modes=["wrong_window", "hotkey_failed"],
        tags=["multi_step", "window_switch", "browser"],
    ),
    TaskSpec(
        id="COMPUTER-022",
        category="multi",
        difficulty=3,
        prompt="Open a text editor, write a short note, save it to the Desktop as 'hermus-note.txt', then open it in the same editor",
        expected_states=["Note is saved to Desktop", "Note can be re-opened"],
        failure_modes=["save_failed", "file_not_found", "wrong_window"],
        tags=["multi_step", "file_save", "verify"],
    ),

    # === EDGE CASES ===
    TaskSpec(
        id="COMPUTER-023",
        category="recovery",
        difficulty=3,
        prompt="Click the 'Submit' button even if it might be hidden behind a popup",
        expected_states=["The popup is handled", "Submit button was clicked"],
        failure_modes=["blocked_by_dialog", "target_not_found"],
        tags=["edge_case", "dialog_blocking"],
    ),
    TaskSpec(
        id="COMPUTER-024",
        category="recovery",
        difficulty=3,
        prompt="The UI might have changed since the plan was made. Re-observe the screen and adapt",
        expected_states=["The current screen state is correctly observed"],
        failure_modes=["stale_observation"],
        tags=["edge_case", "adapt", "replan"],
    ),
    TaskSpec(
        id="COMPUTER-025",
        category="multi",
        difficulty=2,
        prompt="Open any two applications side by side (e.g., Calculator and Notepad)",
        expected_states=["Two application windows are visible"],
        failure_modes=["app_not_found", "window_arrangement_failed"],
        tags=["multi_step", "windows"],
    ),
    TaskSpec(
        id="COMPUTER-026",
        category="dialog",
        difficulty=2,
        prompt="There is an update notification or system dialog. Close it without clicking any update buttons",
        expected_states=["The dialog is closed"],
        failure_modes=["wrong_button", "dialog_persists"],
        tags=["dialog", "dismiss"],
    ),
    TaskSpec(
        id="COMPUTER-027",
        category="app",
        difficulty=1,
        prompt="List all open windows and applications",
        expected_states=["At least one window is listed"],
        failure_modes=["no_windows_detected"],
        tags=["observe", "window_list"],
    ),
    TaskSpec(
        id="COMPUTER-028",
        category="browser",
        difficulty=2,
        prompt="Open a browser, go to any URL, and use Ctrl+D to bookmark the page",
        expected_states=["Bookmark dialog appeared or page was bookmarked"],
        failure_modes=["hotkey_failed", "wrong_window"],
        tags=["browser", "bookmark", "hotkey"],
    ),
    TaskSpec(
        id="COMPUTER-029",
        category="file",
        difficulty=2,
        prompt="Find the largest file on the Desktop and report its name",
        expected_states=["The largest file's name is reported"],
        failure_modes=["no_files_found", "access_denied"],
        tags=["file", "analyze"],
    ),
    TaskSpec(
        id="COMPUTER-030",
        category="multi",
        difficulty=3,
        prompt="Install Firefox by downloading it from the web and handling the installation dialogs",
        expected_states=["Firefox is installed and running"],
        failure_modes=["download_failed", "install_blocked", "dialog_not_handled"],
        tags=["install", "download", "dialog", "multi_step", "flagship"],
    ),
]


# Map id -> task
_TASK_MAP = {task.id: task for task in COMPUTER_TASKS}


def get_task(task_id: str) -> Optional[TaskSpec]:
    """Get a benchmark task by id (e.g. 'COMPUTER-005')."""
    return _TASK_MAP.get(task_id)


def list_tasks(
    category: Optional[str] = None,
    max_difficulty: int = 3,
    min_difficulty: int = 1,
) -> List[TaskSpec]:
    """List tasks with optional filters."""
    return [
        t for t in COMPUTER_TASKS
        if (category is None or t.category == category)
        and min_difficulty <= t.difficulty <= max_difficulty
    ]


def get_categories() -> Dict[str, List[TaskSpec]]:
    """Group tasks by category."""
    groups: Dict[str, List[TaskSpec]] = {}
    for task in COMPUTER_TASKS:
        groups.setdefault(task.category, []).append(task)
    return groups