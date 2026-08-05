"""Vision LLaVA via Ollama Free - No API key, local free vision model"""
from pathlib import Path
from typing import Dict, List
import base64
import requests
import os

from core.config import config

OLLAMA_AVAILABLE = True  # We use requests to Ollama, same as llm.py

def _encode_image_to_base64(image_path: str) -> str:
    """Encode image to base64 for Ollama"""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        return ""

def vision_analyze(image_path: str, prompt: str = "Describe this image in detail", model: str = "llava:7b") -> Dict:
    """Vision analysis via Ollama LLaVA free - no API key, local"""
    # Check if image exists
    p = Path(image_path)
    if not p.exists():
        return {"success": False, "error": f"Image not found: {image_path}"}

    # Try Ollama LLaVA
    try:
        base64_image = _encode_image_to_base64(image_path)
        if not base64_image:
            return {"success": False, "error": "Failed to encode image"}

        url = f"{config.ollama_base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [base64_image],
            "stream": False
        }

        resp = requests.post(url, json=payload, timeout=120)
        if resp.status_code == 404:
            return {"success": False, "error": f"Model {model} not found. Pull with: ollama pull {model} (free)"}
        resp.raise_for_status()
        data = resp.json()
        response_text = data.get("response", "")

        return {
            "success": True,
            "model": model,
            "prompt": prompt,
            "image": image_path,
            "description": response_text,
            "description_truncated": response_text[:2000]
        }

    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": f"Ollama not running at {config.ollama_base_url}. Start: ollama serve && ollama pull {model}. Free offline vision."
        }
    except Exception as e:
        return {"success": False, "error": f"Vision analyze failed: {e}"}

def vision_analyze_multiple(image_paths: List[str], prompt: str = "Describe these images", model: str = "llava:7b") -> Dict:
    """Analyze multiple images - free"""
    results = []
    for img_path in image_paths:
        result = vision_analyze(img_path, prompt, model)
        results.append(result)
    return {"results": results, "count": len(results)}

def vision_available_models() -> Dict:
    """List available vision models in Ollama - free"""
    try:
        url = f"{config.ollama_base_url}/api/tags"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        models = [m["name"] for m in data.get("models", [])]
        vision_models = [m for m in models if "llava" in m.lower() or "vision" in m.lower() or "bakllava" in m.lower()]
        return {"all_models": models, "vision_models": vision_models, "ollama_url": config.ollama_base_url}
    except Exception as e:
        return {"error": str(e), "vision_models": [], "suggestion": "Install Ollama and pull free vision model: ollama pull llava:7b"}

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
                    "model": {"type": "string", "description": "Vision model, e.g., llava:7b, llava:13b, bakllava", "default": "llava:7b"}
                },
                "required": ["image_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "vision_available_models",
            "description": "List available vision models in Ollama - free",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]

TOOL_MAP = {
    "vision_analyze": vision_analyze,
    "vision_available_models": vision_available_models,
}
