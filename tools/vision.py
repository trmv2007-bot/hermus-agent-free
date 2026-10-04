"""Vision analysis tool routed through the canonical dynamic ModelGateway."""
from __future__ import annotations

import base64
from pathlib import Path

from core.contracts import FailureClass
from core.models import ModelGatewayError, get_model_gateway

OLLAMA_AVAILABLE = True


def _encode_image_to_base64(image_path: str) -> str:
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""


def _vision_error(exc: Exception, model: str | None) -> dict:
    fc = getattr(exc, "failure_class", "")
    if fc == FailureClass.MODEL_UNAVAILABLE.value:
        return {
            "success": False,
            "error": f"Selected vision model is unavailable: {model or 'dynamic selection'} — run \'ollama pull {model or '<model>'}\' to install it.",
        }
    if fc in (FailureClass.NETWORK.value, FailureClass.PROVIDER_UNAVAILABLE.value):
        return {
            "success": False,
            "error": f"Ollama not running or unreachable for {model or 'dynamic selection'}: {exc}. Start Ollama with \'ollama serve\'.",
        }
    return {"success": False, "error": f"Vision analyze failed: {exc}"}


def vision_analyze(
    image_path: str,
    prompt: str = "Describe this image in detail",
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Analyze an image with the selected or dynamically discovered vision model."""
    p = Path(image_path)
    if not p.exists():
        return {"success": False, "error": f"Image not found: {image_path}"}

    base64_image = _encode_image_to_base64(image_path)
    if not base64_image:
        return {"success": False, "error": "Failed to encode image"}

    try:
        description = get_model_gateway().vision_complete(
            base64_image,
            prompt,
            model=model,
            provider=provider,
        )
    except ModelGatewayError as exc:
        return _vision_error(exc, model)
    except Exception as exc:
        return {"success": False, "error": f"Vision analyze failed: {exc}"}

    selected = model
    try:
        selected_provider, selected_model = get_model_gateway().resolve_model(
            "vision", required=["vision"], provider=provider
        )
        if not selected and selected_model:
            selected = f"{selected_provider}/{selected_model}"
    except Exception:
        pass
    return {
        "success": True,
        "model": selected or model,
        "prompt": prompt,
        "image": image_path,
        "description": description,
        "description_truncated": description[:2000],
    }


def vision_analyze_multiple(
    image_paths: list[str],
    prompt: str = "Describe these images",
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    results = [vision_analyze(img_path, prompt, model, provider) for img_path in image_paths]
    return {"results": results, "count": len(results)}


def vision_available_models() -> dict:
    """Return dynamically discovered vision-capable models with a legacy fallback."""
    gateway = get_model_gateway()
    try:
        catalog = gateway.catalog(probe=True, refresh=True)
        rows = list(catalog.get("models") or [])
        all_models = [row.get("ref") for row in rows if row.get("ref")]
        models = [row for row in rows if (row.get("capabilities") or {}).get("vision") == "yes"]
        if models or all_models:
            return {
                "models": models,
                "vision_models": [row.get("ref") for row in models if row.get("ref")],
                "all_models": all_models,
                "providers": catalog.get("providers") or [],
                "generated_at": catalog.get("generated_at"),
            }
    except Exception:
        pass
    try:
        all_models = list(gateway.vision_models() or [])
        markers = ("llava", "bakllava", "moondream", "minicpm-v", "vision")
        vision_models = [m for m in all_models if any(tag in str(m).lower() for tag in markers)]
        return {"models": vision_models, "vision_models": vision_models, "all_models": all_models}
    except Exception as exc:
        return {"error": str(exc), "vision_models": [], "all_models": [], "models": []}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": "Analyze an image with the selected or automatically discovered vision-capable model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "Path to image file"},
                    "prompt": {
                        "type": "string",
                        "description": "Prompt for vision analysis",
                        "default": "Describe this image in detail",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional provider/model reference. Omit for automatic selection.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Optional provider override. Omit for automatic selection.",
                    },
                },
                "required": ["image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_available_models",
            "description": "List currently discovered vision-capable model deployments.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOL_MAP = {
    "vision_analyze": vision_analyze,
    "vision_available_models": vision_available_models,
}
