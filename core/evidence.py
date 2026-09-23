"""Evidence: what the system observed, stored apart from the conversation.

A model should receive a short digest with references, not the underlying
material, and a reader should be able to open any reference afterwards and find
the same thing — or discover that it has since changed. Both of those need a
place to live that is not the prompt and not the mission document, which grows
without bound across repair rounds.

``source`` comes from :func:`core.verifier_registry.classify_check`, the same
vocabulary the verifier uses, so "the worker said so" can never be laundered
into "the system saw it" by passing through storage.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic_io import file_lock
from .log import get_logger
from .verifier_registry import SOURCE_OBSERVED, SOURCE_UNCLASSIFIED, SOURCE_WORKER_REPORTED, classify_check
from .workspace import workspace

logger = get_logger(__name__)

DIGEST_MAX_CHARS = 1200
#: An artifact larger than this is hashed by prefix + size rather than read whole;
#: evidence is a pointer to a fact, not a copy of a gigabyte.
HASH_CAP_BYTES = 32 * 1024 * 1024


@dataclass
class EvidenceRecord:
    id: str
    mission_id: str
    created_at: str
    kind: str = ""
    check: str = ""
    source: str = SOURCE_UNCLASSIFIED
    summary: str = ""
    stage: str = ""
    artifact_path: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def grounded(self) -> bool:
        return self.source == SOURCE_OBSERVED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceRecord:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def hash_artifact(path: Any) -> tuple[str | None, int | None]:
    """Content hash + size of a file, or (None, None) when it is not there.

    ``missing`` is a result, not an error: an evidence record whose artifact has
    vanished is the case the whole store exists to be able to say out loud.
    """
    if not path:
        return None, None
    try:
        p = Path(path)
        if not p.is_file():
            return None, None
        digest = hashlib.sha256()
        read = 0
        with p.open("rb") as handle:
            while chunk := handle.read(65536):
                digest.update(chunk)
                read += len(chunk)
                if read >= HASH_CAP_BYTES:
                    digest.update(f"|capped:{p.stat().st_size}".encode())
                    break
        return digest.hexdigest(), p.stat().st_size
    except OSError:
        return None, None


class EvidenceStore:
    """Append-only evidence log, one JSONL document per mission."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else (workspace.root / "missions" / "evidence")

    # ------------------------------------------------------------------ write
    def _path(self, mission_id: str) -> Path:
        safe = "".join(ch for ch in str(mission_id) if ch.isalnum() or ch in "-_.")[:80] or "default"
        return self.base_dir / f"{safe}.jsonl"

    def record(
        self,
        *,
        mission_id: str,
        kind: str = "",
        check: str = "",
        summary: str = "",
        stage: str = "",
        payload: dict[str, Any] | None = None,
        artifact_path: Any = None,
        source: str | None = None,
    ) -> EvidenceRecord:
        """Store one observation and return it, deduplicated on identity.

        A repair round re-runs the same checks; without dedupe the log would
        fill with N copies of "pytest passed" and the last one would look no
        stronger than the first.
        """
        sha, size = hash_artifact(artifact_path)
        body = payload or {}
        identity = json.dumps(
            [mission_id, kind, check, str(artifact_path or ""), summary[:200], sha, _stable(body)],
            sort_keys=True,
            default=str,
        )
        record_id = "ev_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        existing = self.get(record_id)
        if existing is not None:
            return existing

        entry = EvidenceRecord(
            id=record_id,
            mission_id=str(mission_id),
            created_at=datetime.now().isoformat(),
            kind=str(kind or ""),
            check=str(check or ""),
            source=str(source or classify_check(check)),
            summary=str(summary or "")[:500],
            stage=str(stage or ""),
            artifact_path=str(artifact_path) if artifact_path else None,
            sha256=sha,
            size_bytes=size,
            payload=body,
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(mission_id)
        try:
            with file_lock(path):
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry.to_dict(), default=str) + "\n")
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
        except OSError as exc:
            # Evidence storage must never fail a mission that is otherwise
            # proceeding — but the gap has to be visible, not silent.
            logger.warning("[Evidence] write failed for %s: %s", mission_id, exc)
        return entry

    def record_many(self, mission_id: str, items: list[dict[str, Any]] | list[Any], *, stage: str = "") -> list[EvidenceRecord]:
        """File a batch of evidence dicts, honouring any stage each item carries.

        Items that are not dicts are skipped rather than coerced: a bare string
        has no check to classify, and filing it as ``unclassified`` would look
        like evidence the system never ran.
        """
        out: list[EvidenceRecord] = []
        for item in items or []:
            if isinstance(item, dict):
                out.append(
                    self.record(
                        mission_id=mission_id,
                        kind=str(item.get("type") or item.get("kind") or ""),
                        check=str(item.get("check") or ""),
                        summary=_summarize(item),
                        stage=str(item.get("stage") or stage or ""),
                        payload=item,
                        artifact_path=item.get("file") or item.get("path"),
                    )
                )
        return out

    # ------------------------------------------------------------------- read
    def list(self, mission_id: str, *, limit: int = 200, source: str | None = None, kind: str | None = None) -> list[EvidenceRecord]:
        out: list[EvidenceRecord] = []
        try:
            lines = self._path(mission_id).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            if len(out) >= limit:
                break
            try:
                entry = EvidenceRecord.from_dict(json.loads(line))
            except (ValueError, TypeError):
                continue
            if source and entry.source != source:
                continue
            if kind and entry.kind != kind:
                continue
            out.append(entry)
        out.reverse()
        return out

    def get(self, record_id: str) -> EvidenceRecord | None:
        needle = str(record_id or "")
        if not needle:
            return None
        for path in self._all_paths():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                if needle not in line:
                    continue
                try:
                    entry = EvidenceRecord.from_dict(json.loads(line))
                except (ValueError, TypeError):
                    continue
                if entry.id == needle:
                    return entry
        return None

    def recheck(self, record_id: str) -> dict[str, Any]:
        """Look at the artifact again, now, and say whether it still holds.

        This is the deterministic half of "did the requested thing actually
        happen" — the claim is not re-read, the file is.
        """
        entry = self.get(record_id)
        if entry is None:
            return {"id": record_id, "found": False, "state": "unknown"}
        if not entry.artifact_path:
            return {"id": entry.id, "found": True, "state": "no_artifact", "grounded": entry.grounded}
        sha, size = hash_artifact(entry.artifact_path)
        if sha is None:
            state = "missing"
        elif sha == entry.sha256:
            state = "unchanged"
        else:
            state = "drifted"
        return {
            "id": entry.id,
            "found": True,
            "state": state,
            "artifact": entry.artifact_path,
            "recorded_sha256": entry.sha256,
            "current_sha256": sha,
            "recorded_size": entry.size_bytes,
            "current_size": size,
            "grounded": entry.grounded,
        }

    # ----------------------------------------------------------- prompt face
    def digest(self, mission_id: str, *, limit: int = 10, max_chars: int = DIGEST_MAX_CHARS) -> str:
        """What belongs in a prompt: references and one line each, capped.

        The payload stays here. A model told "ev_a1b2 pytest passed" can reason
        about it; a model handed 4000 characters of stdout mostly repeats it.
        """
        # The header describes the mission's evidence, the body shows a slice of
        # it — so count over a generous read instead of the displayed window,
        # which would understate the log and read as a fact about the mission.
        entries = self.list(mission_id, limit=1000)
        picked = entries[-limit:] if entries else []
        if not picked:
            return ""
        counts: dict[str, int] = {SOURCE_OBSERVED: 0, SOURCE_WORKER_REPORTED: 0, SOURCE_UNCLASSIFIED: 0}
        for entry in entries:
            counts[entry.source] = counts.get(entry.source, 0) + 1
        header = (
            f"Evidence for mission {mission_id}: {counts[SOURCE_OBSERVED]} observed, "
            f"{counts[SOURCE_WORKER_REPORTED]} worker-reported, {counts[SOURCE_UNCLASSIFIED]} unclassified. "
            "Open a reference with context_read(topic='evidence', query='<id>') — it was deliberately not expanded here."
        )
        lines = [header]
        for entry in picked:
            mark = "observed" if entry.grounded else entry.source
            suffix = f" ({entry.size_bytes}B)" if entry.size_bytes else ""
            lines.append(f"- {entry.id} [{mark}{('/' + entry.kind) if entry.kind else ''}] {entry.check or entry.kind} {entry.summary}{suffix}".rstrip())
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    def _all_paths(self) -> list[Path]:
        try:
            return sorted(self.base_dir.glob("*.jsonl"))
        except OSError:
            return []


def _summarize(item: dict[str, Any]) -> str:
    for key in ("status", "summary", "detail", "message", "output"):
        value = item.get(key)
        if value:
            return str(value).strip().replace("\n", " ")[:160]
    return ""


def _stable(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, default=str)[:400]
    except (TypeError, ValueError):
        return str(payload)[:400]


evidence_store = EvidenceStore()
