"""
Universal OpenAI-compatible HTTP client.
Works with: OpenAI, Groq, OpenRouter, Together, Fireworks, DeepSeek, Mistral,
Gemini OpenAI mode, Cerebras, SambaNova, HF router, GitHub Models, Azure,
Ollama /v1, LM Studio, vLLM, and any custom base_url.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

from .providers import (
    build_auth_headers,
    get_provider,
    parse_model_ref,
    resolve_endpoint,
)
from .token_counter import token_counter


class CompatResponse:
    def __init__(
        self,
        content: str,
        tool_calls: Optional[List[Dict]] = None,
        usage: Optional[Dict] = None,
        raw: Optional[Dict] = None,
        model: str = "",
        latency_ms: int = 0,
        headers: Optional[Dict] = None,
    ):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.usage = usage or {}
        self.raw = raw or {}
        self.model = model
        self.latency_ms = latency_ms
        self.headers = headers or {}


def _normalize_messages(messages: List[Dict]) -> List[Dict]:
    out = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        # Drop empty assistant tool_calls-only for strict APIs if no content
        entry = {"role": role, "content": content if content is not None else ""}
        # Pass through tool_calls if present (OpenAI format)
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        if m.get("name"):
            entry["name"] = m["name"]
        if m.get("tool_call_id"):
            entry["tool_call_id"] = m["tool_call_id"]
        out.append(entry)
    return out


def _normalize_tools(tools: List[Dict] = None) -> Optional[List[Dict]]:
    if not tools:
        return None
    # Already OpenAI style
    return tools


def _parse_tool_calls(msg: Dict) -> List[Dict]:
    tool_calls = []
    raw_tcs = msg.get("tool_calls") or []
    for tc in raw_tcs:
        fn = tc.get("function") or {}
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except Exception:
                args = {"raw": args}
        tool_calls.append(
            {
                "name": fn.get("name"),
                "arguments": args if isinstance(args, dict) else {},
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
            }
        )
    return tool_calls


def _extract_rate_headers(headers: Dict) -> Dict[str, Any]:
    """Parse common rate-limit headers from providers."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    def pick(*keys):
        for k in keys:
            if k in h:
                return h[k]
        return None

    out = {
        "limit_requests": pick(
            "x-ratelimit-limit-requests",
            "x-ratelimit-limit",
            "ratelimit-limit",
        ),
        "remaining_requests": pick(
            "x-ratelimit-remaining-requests",
            "x-ratelimit-remaining",
            "ratelimit-remaining",
        ),
        "reset_requests": pick(
            "x-ratelimit-reset-requests",
            "x-ratelimit-reset",
            "ratelimit-reset",
        ),
        "limit_tokens": pick(
            "x-ratelimit-limit-tokens",
            "x-ratelimit-limit-tokens-minute",
        ),
        "remaining_tokens": pick(
            "x-ratelimit-remaining-tokens",
            "x-ratelimit-remaining-tokens-minute",
        ),
        "reset_tokens": pick("x-ratelimit-reset-tokens"),
        "retry_after": pick("retry-after"),
    }
    # Coerce numbers
    for k, v in list(out.items()):
        if v is None:
            continue
        try:
            if isinstance(v, str) and v.isdigit():
                out[k] = int(v)
            elif isinstance(v, str):
                try:
                    out[k] = float(v)
                except Exception:
                    pass
        except Exception:
            pass
    return {k: v for k, v in out.items() if v is not None}


def chat_completions(
    provider: str,
    model: str,
    messages: List[Dict],
    api_key: str = None,
    base_url: str = None,
    tools: List[Dict] = None,
    temperature: float = 0.7,
    max_tokens: int = None,
    timeout: int = 120,
    extra_headers: Dict = None,
    extra_body: Dict = None,
) -> CompatResponse:
    """
    POST {base}/chat/completions — OpenAI-compatible.
    Also handles Anthropic native if provider=anthropic and path is /messages.
    """
    preset = get_provider(provider)
    start = time.time()

    # Anthropic native path
    if preset.get("native_anthropic") and (not base_url or "anthropic.com" in (base_url or preset.get("base_url") or "")):
        return _anthropic_messages(
            model=model,
            messages=messages,
            api_key=api_key,
            base_url=base_url or preset.get("base_url"),
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens or 2048,
            timeout=timeout,
        )

    url = resolve_endpoint(provider, base_url=base_url, path_key="chat_path")
    headers = build_auth_headers(provider, api_key, extra=extra_headers)
    body: Dict[str, Any] = {
        "model": model,
        "messages": _normalize_messages(messages),
        "temperature": temperature,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    norm_tools = _normalize_tools(tools)
    if norm_tools and preset.get("supports_tools", True):
        body["tools"] = norm_tools
        body["tool_choice"] = "auto"
    if extra_body:
        body.update(extra_body)

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        latency_ms = int((time.time() - start) * 1000)
        rate = _extract_rate_headers(dict(resp.headers))
        text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": text[:2000]}

        if resp.status_code >= 400:
            err = data.get("error") if isinstance(data, dict) else None
            if isinstance(err, dict):
                msg = err.get("message") or err.get("code") or str(err)
            else:
                msg = str(err or text)[:800]
            usage = token_counter.estimate_cost(
                token_counter.count_messages(messages),
                0,
                model=f"{provider}/{model}",
            )
            usage["error"] = msg
            usage["status_code"] = resp.status_code
            usage["rate_limit"] = rate
            raise CompatAPIError(
                msg,
                status_code=resp.status_code,
                rate_limit=rate,
                body=data,
                latency_ms=latency_ms,
            )

        # Parse success
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or choice.get("text") or ""
        # Some providers put reasoning separately
        if not content and msg.get("reasoning_content"):
            content = msg.get("reasoning_content")
        tool_calls = _parse_tool_calls(msg)

        usage_raw = data.get("usage") or {}
        pt = usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or token_counter.count_messages(messages)
        ct = usage_raw.get("completion_tokens") or usage_raw.get("output_tokens") or token_counter.count_text(content or "")
        usage = token_counter.estimate_cost(pt, ct, model=f"{provider}/{model}")
        usage["rate_limit"] = rate
        usage["latency_ms"] = latency_ms

        return CompatResponse(
            content=content or "",
            tool_calls=tool_calls,
            usage=usage,
            raw=data,
            model=data.get("model") or model,
            latency_ms=latency_ms,
            headers=rate,
        )
    except CompatAPIError:
        raise
    except requests.exceptions.Timeout:
        raise CompatAPIError("Request timeout", status_code=408, latency_ms=int((time.time() - start) * 1000))
    except requests.exceptions.ConnectionError as e:
        raise CompatAPIError(f"Connection error: {e}", status_code=0, latency_ms=int((time.time() - start) * 1000))
    except Exception as e:
        raise CompatAPIError(str(e), status_code=0, latency_ms=int((time.time() - start) * 1000))


class CompatAPIError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 0,
        rate_limit: Dict = None,
        body: Any = None,
        latency_ms: int = 0,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.rate_limit = rate_limit or {}
        self.body = body
        self.latency_ms = latency_ms
        self.message = message

    @property
    def is_rate_limit(self) -> bool:
        if self.status_code == 429:
            return True
        msg = (self.message or "").lower()
        return "rate limit" in msg or "too many requests" in msg or "quota" in msg

    @property
    def is_auth_error(self) -> bool:
        if self.status_code in (401, 403):
            return True
        msg = (self.message or "").lower()
        return "invalid api key" in msg or "unauthorized" in msg or "authentication" in msg


def list_models(
    provider: str,
    api_key: str = None,
    base_url: str = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """GET {base}/models — discover what this key can run."""
    preset = get_provider(provider)
    start = time.time()

    # Ollama native tags fallback if openai path fails
    try:
        url = resolve_endpoint(provider, base_url=base_url, path_key="models_path")
    except ValueError as e:
        return {"success": False, "error": str(e), "models": []}

    headers = build_auth_headers(provider, api_key)
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        latency_ms = int((time.time() - start) * 1000)
        rate = _extract_rate_headers(dict(resp.headers))
        try:
            data = resp.json()
        except Exception:
            data = {}

        if resp.status_code >= 400:
            # Ollama native fallback
            if provider == "ollama":
                return _ollama_native_tags(base_url, latency_ms)
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": str(data.get("error") or resp.text)[:500],
                "models": [],
                "rate_limit": rate,
                "latency_ms": latency_ms,
            }

        models = _normalize_models_list(data, provider)
        return {
            "success": True,
            "provider": provider,
            "base_url": (base_url or preset.get("base_url")),
            "models": models,
            "count": len(models),
            "rate_limit": rate,
            "latency_ms": latency_ms,
            "default_model": preset.get("default_model"),
        }
    except Exception as e:
        if provider == "ollama":
            return _ollama_native_tags(base_url, int((time.time() - start) * 1000))
        return {"success": False, "error": str(e), "models": []}


def _normalize_models_list(data: Any, provider: str) -> List[Dict]:
    items = []
    if isinstance(data, dict):
        raw = data.get("data") or data.get("models") or data.get("items") or []
    elif isinstance(data, list):
        raw = data
    else:
        raw = []

    for m in raw:
        if isinstance(m, str):
            items.append({"id": m, "owned_by": "", "provider": provider})
            continue
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("name") or m.get("model") or ""
        if not mid:
            continue
        items.append(
            {
                "id": mid,
                "owned_by": m.get("owned_by") or m.get("organization") or "",
                "created": m.get("created"),
                "context_length": m.get("context_length") or m.get("context_window") or (m.get("top_provider") or {}).get("context_length"),
                "pricing": m.get("pricing"),
                "provider": provider,
                "raw_keys": list(m.keys())[:12],
            }
        )
    # Sort chat-likely first
    def score(x):
        i = (x.get("id") or "").lower()
        if any(k in i for k in ("embed", "whisper", "tts", "moderation", "dall-e", "tts")):
            return 2
        return 0

    items.sort(key=lambda x: (score(x), x.get("id") or ""))
    return items


def _ollama_native_tags(base_url: str = None, latency_ms: int = 0) -> Dict:
    root = (base_url or "http://localhost:11434").replace("/v1", "").rstrip("/")
    try:
        resp = requests.get(f"{root}/api/tags", timeout=10)
        data = resp.json()
        models = []
        for m in data.get("models") or []:
            models.append(
                {
                    "id": m.get("name") or m.get("model"),
                    "owned_by": "ollama",
                    "size": m.get("size"),
                    "provider": "ollama",
                }
            )
        return {
            "success": True,
            "provider": "ollama",
            "models": models,
            "count": len(models),
            "latency_ms": latency_ms,
            "source": "ollama_native_tags",
        }
    except Exception as e:
        return {"success": False, "error": str(e), "models": [], "provider": "ollama"}


def _anthropic_messages(
    model: str,
    messages: List[Dict],
    api_key: str,
    base_url: str,
    tools: List[Dict] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> CompatResponse:
    """Minimal Anthropic Messages API support."""
    start = time.time()
    url = (base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key or "",
        "anthropic-version": "2023-06-01",
    }
    system = ""
    conv = []
    for m in messages:
        if m.get("role") == "system":
            system += (m.get("content") or "") + "\n"
        else:
            role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
            conv.append({"role": role, "content": m.get("content") or ""})
    body = {
        "model": model,
        "messages": conv or [{"role": "user", "content": "hi"}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system.strip():
        body["system"] = system.strip()
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    latency_ms = int((time.time() - start) * 1000)
    rate = _extract_rate_headers(dict(resp.headers))
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code >= 400:
        err = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error") or resp.text
        raise CompatAPIError(str(err)[:800], status_code=resp.status_code, rate_limit=rate, body=data, latency_ms=latency_ms)
    content_blocks = data.get("content") or []
    texts = []
    for b in content_blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            texts.append(b.get("text") or "")
    content = "\n".join(texts)
    usage_raw = data.get("usage") or {}
    pt = usage_raw.get("input_tokens") or 0
    ct = usage_raw.get("output_tokens") or 0
    usage = token_counter.estimate_cost(pt, ct, model=f"anthropic/{model}")
    usage["rate_limit"] = rate
    usage["latency_ms"] = latency_ms
    return CompatResponse(content=content, usage=usage, raw=data, model=model, latency_ms=latency_ms, headers=rate)


def _is_model_error(msg: str, status_code: int) -> bool:
    """Detect model-not-found / deprecated model errors."""
    if status_code == 404:
        return True
    m = (msg or "").lower()
    return any(
        phrase in m
        for phrase in (
            "model not found",
            "model does not exist",
            "model has been deprecated",
            "deprecated",
            "invalid model",
            "unknown model",
            "no such model",
            "not support this model",
        )
    )


def health_ping(
    provider: str,
    api_key: str = None,
    base_url: str = None,
    model: str = None,
    timeout: int = 25,
) -> Dict[str, Any]:
    """
    Lightweight health check:
    1) list models if possible
    2) tiny chat completion (with fallback to other discovered models)
    Returns health, latency, rate limits, usable models sample.
    """
    preset = get_provider(provider)
    original_model = model or preset.get("default_model") or "gpt-3.5-turbo"
    model = original_model
    result: Dict[str, Any] = {
        "provider": provider,
        "base_url": base_url or preset.get("base_url"),
        "model_tested": model,
        "success": False,
        "healthy": False,
        "timestamp": time.time(),
    }

    models_info = list_models(provider, api_key=api_key, base_url=base_url, timeout=min(timeout, 20))
    result["models_probe"] = {
        "success": models_info.get("success"),
        "count": models_info.get("count", 0),
        "error": models_info.get("error"),
        "sample": [m.get("id") for m in (models_info.get("models") or [])[:15]],
        "rate_limit": models_info.get("rate_limit"),
    }

    # Build a candidate model list: preferred model first, then chat-like models from discovery
    candidate_models = [model]
    if models_info.get("success") and models_info.get("models"):
        ids = [m["id"] for m in models_info["models"] if m.get("id")]
        # If preferred model missing from catalog, prepend discovered chat-like models
        chat_like = []
        for mid in ids:
            low = mid.lower()
            if any(x in low for x in ("embed", "whisper", "tts", "moderation", "dall", "image", "vision")):
                continue
            chat_like.append(mid)
        # Move chat-like discovered models to the front when the preset model isn't in the list
        if model not in ids and chat_like:
            candidate_models = chat_like + candidate_models
        else:
            # Still append other chat-like models as fallbacks
            for mid in chat_like:
                if mid not in candidate_models:
                    candidate_models.append(mid)

    last_error = None
    for try_model in candidate_models[:5]:
        model = try_model
        result["model_tested"] = model
        try:
            resp = chat_completions(
                provider=provider,
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                api_key=api_key,
                base_url=base_url,
                tools=None,
                temperature=0,
                max_tokens=16,
                timeout=timeout,
            )
            result["success"] = True
            result["healthy"] = True
            result["latency_ms"] = resp.latency_ms
            result["response_preview"] = (resp.content or "")[:120]
            result["usage"] = resp.usage
            result["rate_limit"] = resp.headers or resp.usage.get("rate_limit")
            result["status"] = "ok"
            if try_model != original_model:
                result["note"] = f"Default model '{original_model}' unavailable; used fallback '{try_model}'"
            return result
        except CompatAPIError as e:
            last_error = e
            # If it's a model error, try next candidate
            if _is_model_error(e.message, e.status_code):
                continue
            # Otherwise record and stop
            result["success"] = False
            result["healthy"] = False
            result["latency_ms"] = e.latency_ms
            result["error"] = e.message
            result["status_code"] = e.status_code
            result["rate_limit"] = e.rate_limit
            result["is_rate_limit"] = e.is_rate_limit
            result["is_auth_error"] = e.is_auth_error
            if e.is_auth_error:
                result["status"] = "auth_failed"
            elif e.is_rate_limit:
                result["status"] = "rate_limited"
            else:
                result["status"] = "error"
            # Models list alone can still be useful
            if models_info.get("success") and not e.is_auth_error:
                result["partial"] = True
                result["note"] = "Models listable but chat failed — check default model id"
            return result
        except Exception as e:
            last_error = e
            result["error"] = str(e)
            result["status"] = "error"
            return result

    # All candidates exhausted — most likely model-not-found for every candidate
    if last_error and isinstance(last_error, CompatAPIError):
        result["latency_ms"] = last_error.latency_ms
        result["error"] = last_error.message
        result["status_code"] = last_error.status_code
        result["rate_limit"] = last_error.rate_limit
        if _is_model_error(last_error.message, last_error.status_code):
            result["status"] = "model_not_found"
            result["note"] = f"Tried {len(candidate_models[:5])} models including '{original_model}'; all returned model-not-found. The provider may have deprecated this model."
        elif last_error.is_auth_error:
            result["status"] = "auth_failed"
        elif last_error.is_rate_limit:
            result["status"] = "rate_limited"
        else:
            result["status"] = "error"
    else:
        result["error"] = str(last_error) if last_error else "All model candidates failed"
        result["status"] = "error"

    return result
