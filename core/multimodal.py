"""Multimodal observation and evidence layer for HERMUS.

This module unifies image, document and browser visual observations into a
structured evidence contract. It delegates actual vision inference to the
existing vision tool/ModelGateway and never executes arbitrary computer
actions or grants new permissions.
"""
from __future__ import annotations

import mimetypes
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .document_ingest import extract_document
from .world_model import WorldModel, world_model


@dataclass
class MultimodalEvidence:
    source: str
    modality: str
    target: str
    success: bool
    observation: str = ""
    confidence: float = 0.0
    model: str | None = None
    artifacts: list[str] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MultimodalIntelligence:
    """Read/analyze visual evidence through existing capabilities."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}

    def __init__(self, *, world: WorldModel | None = None, workspace_root: str | Path | None = None):
        self.world = world or world_model
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()

    def _safe_path(self, path: str | Path, *, require_file: bool = True) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError("multimodal path must remain inside the workspace") from exc
        if require_file and not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def analyze_image(self, path: str | Path, *, prompt: str = "Describe this image in detail",
                      model: str | None = None) -> dict[str, Any]:
        target = self._safe_path(path)
        from tools.vision import vision_analyze

        result = vision_analyze(str(target), prompt=prompt, model=model)
        evidence = MultimodalEvidence(
            source="vision",
            modality="image",
            target=str(target),
            success=bool(result.get("success")),
            observation=str(result.get("description") or ""),
            confidence=0.85 if result.get("success") else 0.0,
            model=model,
            artifacts=[str(target)],
            note=result.get("error"),
        )
        return self._record(evidence)

    def analyze_document(self, path: str | Path, *, prompt: str = "Describe the visual contents and important text",
                         model: str | None = None) -> dict[str, Any]:
        target = self._safe_path(path)
        data = target.read_bytes()
        extracted = extract_document(
            target.name,
            data,
            mimetypes.guess_type(target.name)[0] or "",
        )
        if target.suffix.lower() in self.IMAGE_EXTENSIONS:
            return self.analyze_image(target, prompt=prompt, model=model)

        evidence = MultimodalEvidence(
            source="document_ingest",
            modality="document",
            target=str(target),
            success=bool(extracted.text),
            observation=extracted.text or "",
            confidence=0.9 if extracted.text else 0.0,
            artifacts=[str(target)],
            note=extracted.note,
        )
        return self._record(evidence)

    def analyze_browser(self, *, path: str = "data/multimodal/browser.png",
                        prompt: str = "Describe the current browser page, visible UI, text and important state",
                        model: str | None = None, full_page: bool = False) -> dict[str, Any]:
        # Browser screenshots are output artifacts; the target is expected to be
        # absent before the screenshot provider creates it.
        target = self._safe_path(path, require_file=False)
        from tools.browser import browser_screenshot

        screenshot = browser_screenshot(str(target), full_page=full_page)
        if not screenshot.get("success"):
            evidence = MultimodalEvidence(
                source="browser",
                modality="browser_visual",
                target=str(target),
                success=False,
                note=screenshot.get("error"),
            )
            return self._record(evidence)
        result = self.analyze_image(target, prompt=prompt, model=model)
        result["source"] = "browser"
        result["modality"] = "browser_visual"
        result["screenshot"] = str(target)
        return result

    def _record(self, evidence: MultimodalEvidence) -> dict[str, Any]:
        payload = evidence.to_dict()
        self.world.observe(
            "multimodal",
            f"{evidence.modality}:{evidence.target}",
            payload,
            source=evidence.source,
            confidence=evidence.confidence,
            permission_scope="multimodal.read",
        )
        self.world.emit(
            "multimodal_observation",
            {
                "modality": evidence.modality,
                "target": evidence.target,
                "success": evidence.success,
                "confidence": evidence.confidence,
            },
            source=evidence.source,
        )
        return payload


multimodal = MultimodalIntelligence()

__all__ = ["MultimodalEvidence", "MultimodalIntelligence", "multimodal"]
