"""Multimodal evidence layer for HERMUS."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import mimetypes

from .world_model import WorldModel, world_model

try:
    from .document_extract import extract_document
except Exception:  # pragma: no cover
    extract_document = None


@dataclass
class MultimodalEvidence:
    source: str
    modality: str
    target: str
    success: bool
    observation: str = ""
    confidence: float = 0.0
    model: str = ""
    artifacts: list[str] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "modality": self.modality,
            "target": self.target,
            "success": self.success,
            "observation": self.observation,
            "confidence": self.confidence,
            "model": self.model,
            "artifacts": list(self.artifacts),
            "note": self.note,
        }


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
                      model: str = "llava:7b") -> dict[str, Any]:
        target = self._safe_path(path)
        from tools.vision import vision_analyze

        result = vision_analyze(str(target), prompt=prompt, model=model)
        evidence = MultimodalEvidence(
            source="vision", modality="image", target=str(target), success=bool(result.get("success")),
            observation=str(result.get("description") or ""), confidence=0.85 if result.get("success") else 0.0,
            model=model, artifacts=[str(target)], note=result.get("error"),
        )
        return self._record(evidence)

    def analyze_document(self, path: str | Path, *, prompt: str = "Describe the visual contents and important text",
                         model: str = "llava:7b") -> dict[str, Any]:
        target = self._safe_path(path)
        data = target.read_bytes()
        if extract_document is None:
            raise RuntimeError("document extraction is unavailable")
        extracted = extract_document(target.name, data, mimetypes.guess_type(target.name)[0] or "")
        if target.suffix.lower() in self.IMAGE_EXTENSIONS:
            return self.analyze_image(target, prompt=prompt, model=model)
        evidence = MultimodalEvidence(
            source="document_ingest", modality="document", target=str(target), success=bool(extracted.text),
            observation=extracted.text or "", confidence=0.9 if extracted.text else 0.0,
            artifacts=[str(target)], note=extracted.note,
        )
        return self._record(evidence)

    def analyze_browser(self, *, path: str = "data/multimodal/browser.png",
                        prompt: str = "Describe the current browser page, visible UI, text and important state",
                        model: str = "llava:7b", full_page: bool = False) -> dict[str, Any]:
        # A browser screenshot is an output artifact, so it must not be required
        # to exist before the screenshot provider has had a chance to create it.
        target = self._safe_path(path, require_file=False)
        from tools.browser import browser_screenshot

        screenshot = browser_screenshot(str(target), full_page=full_page)
        if not screenshot.get("success"):
            evidence = MultimodalEvidence(
                source="browser", modality="browser_visual", target=str(target), success=False,
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
        self.world.observe("multimodal", f"{evidence.modality}:{evidence.target}", payload,
                           source=evidence.source, confidence=evidence.confidence,
                           permission_scope="multimodal.read")
        self.world.emit("multimodal_observation", {
            "modality": evidence.modality, "target": evidence.target,
            "success": evidence.success, "confidence": evidence.confidence,
        }, source=evidence.source)
        return payload


multimodal = MultimodalIntelligence()

__all__ = ["MultimodalEvidence", "MultimodalIntelligence", "multimodal"]
