"""Vision LLaVA via Ollama Free - No API key, local free vision model.

All model traffic flows through the canonical :class:`ModelGateway` so there is a
single owner of provider/credential/capability resolution and outcome recording —
this module never issues a request to a model backend directly. The free-local
Ollama LLaVA path is the model the tool advertises by default.
"""
from pathlib import Path
import base64

from core.config import config
from core.models import get_model_gateway, ModelGatewayError
from core.contracts import FailureClass

OLLAMA_AVAILABLE = True  # Vision via the free-local Ollama node (no API key).


def _encode_image_to_base64(image_path: str) -> str:
    """Encode image to base64 for Ollama."""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""


def _vision_error(exc: Exception, model: str) -> dict:
    """Map a ModelGatewayError to the legacy vision-tool error contract."""
    fc = getattr(exc, "failure_class", "")
    if fc == FailureClass.MODEL_UNAVAILABLE.value:
        return {
            "success": False,
            "error": f"Model {model} not found. Pull with: ollama pull {model} (free)",
        }
    if fc in (FailureClass.NETWORK.value, FailureClass.PROVIDER_UNAVAILABLE.value):
        return {
            "success": False,
            "error": (
                f"Ollama not running at {config.ollama_base_url}. "
                f"Start: ollama serve && ollama pull {model}. Free offline vision."
            ),
        }
    return {"success": False, "error": f"Vision analyze failed: {exc}"}


def vision_analyze(image_path: str, prompt: str = "Describe this image in detail", model: str = "llava:7b") -> dict:
    """Vision analysis via Ollama LLaVA free - no API key, local."""
    p = Path(image_path)
    if not p.exists():
        return {"success": False, "error": f"Image not found: {image_path}"}

    base64_image = _encode_image_to_base64(image_path)
    if not base64_image:
        return {"success": False, "error": "Failed to encode image"}

    try:
        description = get_model_gateway().vision_complete(
            base64_image, prompt, model=model, provider="ollama"
        )
    except ModelGatewayError as exc:
        return _vision_error(exc, model)
    except Exception as exc:
        return {"success": False, "error": f"Vision analyze failed: {exc}"}

    return {
        "success": True,
        "model": model,
        "prompt": prompt,
        "image": image_path,
        "description": description,
        "description_truncated": description[:2000],
    }


def vision_analyze_multiple(image_paths: list[str], prompt: str = "Describe these images", model: str = "llava:7b") -> dict:
    """Analyze multiple images - free."""
    results = []
    for img_path in image_paths:
        results.append(vision_analyze(img_path, prompt, model))
    return {"results": results, "count": len(results)}


def vision_available_models() -> dict:
    """List available vision models in Ollama - free."""
    try:
        all_models = get_model_gateway().vision_models()
    except ModelGatewayError as exc:
        return {
            "error": str(exc),
            "vision_models": [],
            "suggestion": "Install Ollama and pull free vision model: ollama pull llava:7b",
        }
    vision_models = [
        m for m in all_models
        if any(k in m.lower() for k in ("llava", "vision", "bakllava"))
    ]
    return {
        "all_models": all_models,
        "vision_models": vision_models,
        "ollama_url": config.ollama_base_url,
    }


# Tool definitions for free LLM
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": "Analyze image via Ollama LLaVA free local vision model - no API key, describe image, read text in image, etc. Requires Ollama and llava model: ollama pull llava:7b (free)",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "Path to image file"},
                    "prompt": {"type": "string", "description": "Prompt for vision analysis", "default": "Describe this image in detail"},
                    "model": {"type": "string", "description": "Vision model, e.g., llava:7b, llava:13b, bakllava", "default": "llava:7b"},
                },
                "required": ["image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_available_models",
            "description": "List available vision models in Ollama - free",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_MAP = {
    "vision_analyze": vision_analyze,
    "vision_available_models": vision_available_models,
}
