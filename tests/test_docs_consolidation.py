"""Documentation consolidation guards.

The repo accumulated 15 top-level Markdown files, several of them superseded
point-in-time records sitting next to live references (one of them, a 45 KB
merge report, outranked the canonical architecture doc in a directory
listing). These are *structural* gates for the doc set, in the spirit of
``tests/test_architecture_gates.py``:

* load-bearing documents stay at the repository root, because code and tests
  read them by name;
* no Markdown link points at a file that does not exist;
* superseded documents live in ``docs/archive/`` and say so.

They are not behavioural tests and they do not check prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Read by name from production code and/or tests — moving them breaks runtime.
LOAD_BEARING = ("RED_LINES.md", "AUTONOMY_BOUNDARIES.md", "CAPABILITY_LEDGER.md")

#: Directories whose history is intentionally frozen (and verbatim).
ARCHIVE = ROOT / "docs" / "archive"

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target"}

_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_BANNER = "**Archived — historical document.**"


def _markdown_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.md") if not any(part in _SKIP_DIRS for part in p.relative_to(ROOT).parts)]


def _links(md: Path) -> list[str]:
    """Relative link targets in a Markdown file (URLs/anchors excluded)."""
    text = md.read_text(encoding="utf-8", errors="ignore")
    out = []
    for raw in _LINK.findall(text):
        target = raw.strip().split("#")[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "tel:", "/")):
            continue
        out.append(target)
    return out


# ---------------------------------------------------------------------------
# Load-bearing documents
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", LOAD_BEARING)
def test_load_bearing_doc_stays_at_root(name):
    """core/evolution.py, core/capability_ledger.py and the red-line tests read
    these by name; they must not move to docs/."""
    assert (ROOT / name).is_file(), f"{name} must remain at the repository root"


def test_evolution_protects_the_same_files_that_exist():
    """The protected-path list in core/evolution.py must not drift from reality."""
    from core.evolution import RED_LINE_PATTERNS

    for name in LOAD_BEARING:
        assert any(name in str(p) for p in RED_LINE_PATTERNS), f"{name} should be protected"
    # Entries without a glob are concrete paths and must still exist.
    for entry in RED_LINE_PATTERNS:
        if "*" in str(entry):
            continue
        assert (ROOT / str(entry)).exists(), f"protected path missing: {entry}"


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
def test_no_broken_relative_links():
    """Every relative Markdown link resolves to a file that exists.

    ``docs/archive/`` is excluded on purpose: those files are frozen verbatim
    snapshots and may reference paths that have since moved.
    """
    broken: list[str] = []
    for md in _markdown_files():
        if ARCHIVE in md.parents:
            continue
        for target in _links(md):
            if not (md.parent / target).exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, "broken documentation links:\n  " + "\n  ".join(broken)


def test_root_docs_are_live_references_only():
    """Superseded point-in-time records belong in docs/archive/, not the root."""
    # README.md legitimately exists both at the root and in the archive index.
    archived_names = {p.name for p in ARCHIVE.glob("*.md") if p.name != "README.md"} if ARCHIVE.is_dir() else set()
    stragglers = sorted(p.name for p in ROOT.glob("*.md") if p.name in archived_names and p.name not in LOAD_BEARING)
    assert not stragglers, f"archived docs still at the root: {stragglers}"


# ---------------------------------------------------------------------------
# Archive hygiene
# ---------------------------------------------------------------------------
def test_archive_exists_and_is_marked():
    assert ARCHIVE.is_dir(), "docs/archive/ should hold superseded documents"
    archived = [p for p in ARCHIVE.glob("*.md") if p.name != "README.md"]
    assert archived, "the archive should not be empty"


@pytest.mark.parametrize(
    "name",
    sorted(p.name for p in (ROOT / "docs" / "archive").glob("*.md") if p.name != "README.md"),
)
def test_archived_doc_declares_itself_historical(name):
    """A reader who lands on an archived file must be told it is not current."""
    text = (ARCHIVE / name).read_text(encoding="utf-8")
    assert _BANNER in text, f"{name} needs the 'archived' banner"
    # The banner must link back out to the document that replaced it.
    head = text[: text.index("\n# ") if "\n# " in text else 600]
    assert "](../../" in head, f"{name}'s banner must link to its replacement"


def test_archive_readme_exists():
    readme = ARCHIVE / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    for name in LOAD_BEARING:
        assert name in text, "the archive index must say which docs are load-bearing"
