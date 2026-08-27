"""
Free / Universal LLM Abstraction
- Ollama local
- Any OpenAI-compatible API key (Groq, OpenRouter, OpenAI, Together, Gemini, DeepSeek, ...)
- HF legacy text generation
- Mock
+ multi-key round-robin, rate-limit awareness, tool calling
"""
from __future__ import annotations

import os
import json
import requests
from typing import Optional
from collections.abc import Generator

from .config import config
from .token_counter import token_counter
from .cache import llm_cache
from .providers import get_provider, parse_model_ref


class LLMResponse:
    def __init__(self, content: str, tool_calls: Optional[list[dict]] = None, usage: Optional[dict] = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}


class FreeLLM:
    """Abstraction for free + any OpenAI-compatible providers."""

    def __init__(
        self,
        model: str = None,
        api_key: str = None,
        base_url: str = None,
        provider: str = None,
        temperature: Optional[float] = None,
    ):
        self.model = model or config.model
        self.temperature = temperature
        # Allow override of provider/model
        if provider:
            self.provider = provider.lower()
            self.model_name = model.split("/", 1)[-1] if model and "/" in model else (model or get_provider(provider).get("default_model"))
        else:
            self.provider, self.model_name = parse_model_ref(self.model)
        self.api_key_override = api_key
        self.base_url_override = base_url

    def _parse_model(self, model_str: str) -> tuple:
        return parse_model_ref(model_str)

    def _resolve_bundle(self) -> dict:
        """Resolve api_key + base_url from override or multi_key_manager."""
        if self.api_key_override is not None or self.base_url_override:
            preset = get_provider(self.provider)
            return {
                "key": self.api_key_override if self.api_key_override is not None else "",
                "base_url": self.base_url_override or preset.get("base_url") or "",
                "default_model": self.model_name or preset.get("default_model"),
                "provider": self.provider,
            }
        try:
            from .multi_key import multi_key_manager

            bundle = multi_key_manager.get_key_bundle(self.provider)
            if bundle:
                return bundle
            # Unknown/custom providers with no own key → shared "custom" pool
            from .providers import PROVIDER_PRESETS

            if self.provider not in PROVIDER_PRESETS:
                custom_bundle = multi_key_manager.get_key_bundle("custom")
                if custom_bundle:
                    return custom_bundle
        except Exception:
            pass
        preset = get_provider(self.provider)
        env_name = preset.get("env_key")
        key = os.getenv(env_name) if env_name else None
        if self.provider == "groq":
            key = key or config.groq_api_key
        if self.provider in ("hf", "huggingface"):
            key = key or config.hf_token
        return {
            "key": key or "",
            "base_url": preset.get("base_url") or "",
            "default_model": self.model_name or preset.get("default_model"),
            "provider": self.provider,
        }

    def _fallback_bundle(self) -> Optional[dict]:
        """First usable API key bundle across providers (custom preferred)."""
        try:
            from .multi_key import multi_key_manager

            return multi_key_manager.first_available_bundle()
        except Exception:
            return None

    @staticmethod
    def _tools_for_provider(tools: Optional[list[dict]], provider: str) -> Optional[list[dict]]:
        """Return a tool set accepted by the selected provider.

        The registry can contain more functions than hosted APIs permit.  In
        particular Groq validates ``tools`` at a hard maximum of 128 items.
        Keep the local registry intact, but advertise only the provider's
        supported maximum for each model request.
        """
        if not tools:
            return None
        preset = get_provider(provider)
        if preset.get("supports_tools") is False:
            return None
        max_tools = preset.get("max_tools")
        if max_tools:
            return tools[: int(max_tools)]
        return tools

    def _call_openai_compat(self, messages: list[dict], tools: list[dict] = None) -> LLMResponse:
        """Universal path for any OpenAI-compatible provider."""
        from .openai_compat import chat_completions, CompatAPIError
        from .multi_key import multi_key_manager

        bundle = self._resolve_bundle()
        api_key = bundle.get("key") or ""
        base_url = bundle.get("base_url") or ""
        model = self.model_name or bundle.get("default_model")
        preset = get_provider(self.provider)
        used_provider = self.provider
        requested_tools = tools
        tools = self._tools_for_provider(requested_tools, used_provider)
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)

        if not api_key and not preset.get("no_auth"):
            # No key for the requested provider → try any configured key when
            # the user did not pin a specific known provider (default model,
            # "custom", or an unknown OpenAI-compatible provider name).
            from .providers import PROVIDER_PRESETS

            fallback_allowed = (
                self.model == config.model
                or self.provider == "custom"
                or self.provider not in PROVIDER_PRESETS
            )
            fb = self._fallback_bundle() if fallback_allowed else None
            if fb:
                used_provider = fb.get("provider") or self.provider
                api_key = fb.get("key") or ""
                base_url = fb.get("base_url") or base_url
                fb_model = fb.get("default_model") or ""
                if not model or model == preset.get("default_model") or model == "default":
                    model = fb_model or model
            if not api_key:
                err = (
                    f"No API key for provider '{self.provider}' and no other keys configured. "
                    f"Add one: hermus multikey add --provider {self.provider} --key YOUR_KEY"
                    + (f" --base-url https://..." if self.provider in ('custom','vllm','azure') else "")
                )
                usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model=f"{self.provider}/{model}")
                return LLMResponse(err, usage=usage)

        # A fallback can change provider capabilities/limits (for example from
        # local Ollama to Groq), so enforce the final provider's tool contract.
        tools = self._tools_for_provider(requested_tools, used_provider)
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)

        # Optional cache (skip when tools present — side effects)
        if not tools:
            cache_key = llm_cache.make_key(
                used_provider,
                model,
                (messages[-1].get("content", "")[:200] if messages else ""),
                len(messages),
                api_key[-6:] if api_key else "nokey",
            )
            cached = llm_cache.get(cache_key)
            if cached:
                return cached

        tries = 0
        last_err = None
        current_key = api_key
        while tries < 3:
            tries += 1
            try:
                extra_kwargs = {}
                if self.temperature is not None:
                    extra_kwargs["temperature"] = self.temperature
                resp = chat_completions(
                    provider=used_provider,
                    model=model,
                    messages=messages,
                    api_key=current_key,
                    base_url=base_url,
                    tools=tools,
                    timeout=120,
                    **extra_kwargs,
                )
                try:
                    multi_key_manager.mark_key_success(
                        used_provider,
                        current_key,
                        tokens=resp.usage.get("total_tokens", 0),
                        latency_ms=resp.latency_ms,
                        rate_limit=resp.headers or resp.usage.get("rate_limit"),
                    )
                except Exception:
                    pass
                out = LLMResponse(resp.content, resp.tool_calls, usage=resp.usage)
                if not tools:
                    try:
                        llm_cache.set(cache_key, out)
                    except Exception:
                        pass
                return out
            except CompatAPIError as e:
                last_err = e
                try:
                    multi_key_manager.mark_key_failed(
                        used_provider, current_key, e.message, rate_limit=e.rate_limit
                    )
                except Exception:
                    pass
                # Retry with next key on rate limit / auth / 5xx
                if e.is_rate_limit or e.status_code >= 500 or e.is_auth_error:
                    nxt = multi_key_manager.get_key(used_provider)
                    if nxt and nxt != current_key:
                        current_key = nxt
                        b2 = multi_key_manager.get_entry(used_provider, nxt) or {}
                        base_url = b2.get("base_url") or base_url
                        continue
                break
            except Exception as e:
                last_err = e
                break

        err = f"{used_provider} error (base_url={base_url or 'preset'}): {last_err}"
        usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model=f"{used_provider}/{model}")
        return LLMResponse(err, usage=usage)

    def _call_ollama(self, messages: list[dict], tools: list[dict] = None) -> LLMResponse:
        """Ollama — prefer OpenAI-compatible /v1, fallback to native /api/chat."""
        # Try openai compat first (tool calling better on newer ollama)
        try:
            from .openai_compat import chat_completions, CompatAPIError

            base = config.ollama_base_url.rstrip("/")
            if not base.endswith("/v1"):
                base_v1 = base + "/v1"
            else:
                base_v1 = base
            try:
                resp = chat_completions(
                    provider="ollama",
                    model=self.model_name,
                    messages=messages,
                    api_key="ollama",
                    base_url=base_v1,
                    tools=tools,
                    timeout=120,
                )
                return LLMResponse(resp.content, resp.tool_calls, usage=resp.usage)
            except CompatAPIError:
                pass
        except Exception:
            pass

        # Native Ollama chat
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
        cache_key = llm_cache.make_key("ollama", self.model_name, messages[-1].get("content", "")[:200] if messages else "", len(messages))
        cached = llm_cache.get(cache_key)
        if cached and not tools:
            cached.usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(cached.content), model=f"ollama/{self.model_name}")
            return cached

        url = f"{config.ollama_base_url.rstrip('/')}/api/chat"
        payload = {"model": self.model_name, "messages": messages, "stream": False}
        if self.temperature is not None:
            payload["options"] = {"temperature": self.temperature}
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "") or data.get("response", "")
            tool_calls = []
            if "message" in data and "tool_calls" in data["message"]:
                for tc in data["message"]["tool_calls"]:
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    tool_calls.append(
                        {
                            "name": tc.get("function", {}).get("name"),
                            "arguments": args,
                            "id": tc.get("id", ""),
                        }
                    )
            completion_tokens = token_counter.count_text(content)
            usage = token_counter.estimate_cost(prompt_tokens, completion_tokens, model=f"ollama/{self.model_name}")
            response = LLMResponse(content, tool_calls, usage=usage)
            if not tools:
                llm_cache.set(cache_key, response)
            return response
        except requests.exceptions.ConnectionError:
            # Ollama is not running — fall back to any configured API key
            # (custom URL / groq / openai / ...) so chat keeps working.
            fb = self._fallback_bundle()
            if fb:
                fb_provider = fb.get("provider") or "custom"
                fb_model = fb.get("default_model") or ""
                ollama_default = get_provider("ollama").get("default_model")
                model = self.model_name
                if not model or model == ollama_default:
                    model = fb_model or model
                try:
                    from .openai_compat import chat_completions, CompatAPIError
                    from .multi_key import multi_key_manager

                    fallback_tools = self._tools_for_provider(tools, fb_provider)
                    resp = chat_completions(
                        provider=fb_provider,
                        model=model or "default",
                        messages=messages,
                        api_key=fb.get("key") or "",
                        base_url=fb.get("base_url") or "",
                        tools=fallback_tools,
                        timeout=120,
                    )
                    try:
                        multi_key_manager.mark_key_success(
                            fb_provider,
                            fb.get("key") or "",
                            tokens=resp.usage.get("total_tokens", 0),
                            latency_ms=resp.latency_ms,
                        )
                    except Exception:
                        pass
                    return LLMResponse(resp.content, resp.tool_calls, usage=resp.usage)
                except Exception as e:
                    fb_err = f"Ollama not running and fallback key failed: {e}"
                    usage = token_counter.estimate_cost(
                        prompt_tokens, token_counter.count_text(fb_err), model=f"{fb_provider}/{model}"
                    )
                    return LLMResponse(fb_err, usage=usage)
            mock_content = (
                f"⚠️ Ollama not running at {config.ollama_base_url} and no API keys configured. "
                f"Start with: ollama serve && ollama pull {self.model_name} — or add any key: "
                f"hermus multikey add --provider custom --base-url https://... --key sk-...\n\n"
                f"Fallback mock for: {messages[-1].get('content','')[:100]}"
            )
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(mock_content), model="ollama/mock")
            return LLMResponse(mock_content, usage=usage)
        except Exception as e:
            err_content = f"Ollama error: {e}"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err_content), model="ollama/mock")
            return LLMResponse(err_content, usage=usage)

    def _call_hf_free(self, messages: list[dict], tools: list[dict] = None) -> LLMResponse:
        """HF — try OpenAI-compatible router first, then legacy text_generation."""
        # Prefer router openai path
        compat = self._call_openai_compat(messages, tools=None)  # tools often unsupported
        compat_content = (compat.content or "").strip()
        lowered = compat_content.lower()
        # Treat the compat answer as usable only when it clearly is NOT an
        # error/help message emitted by the fallback paths above.
        error_like = (
            not compat_content
            or "no api key" in lowered
            or "hf error" in lowered
            or "huggingface error" in lowered
            or lowered.startswith(("error", "huggingface"))
            or lowered[:40].find("error:") >= 0
        )
        if not error_like:
            return compat

        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
        current_token: Optional[str] = None
        try:
            from .multi_key import multi_key_manager

            token = self.api_key_override or multi_key_manager.get_key("hf") or config.hf_token or os.getenv("HF_TOKEN")
            # Bind early so the except-path below can always report the key
            # that was attempted (previously `current_token` could be
            # unbound when the client constructor itself raised).
            current_token = token
            from huggingface_hub import InferenceClient

            client = InferenceClient(token=token)
            prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            response = client.text_generation(
                prompt=prompt,
                model=self.model_name,
                max_new_tokens=512,
                temperature=0.7,
            )
            content = response if isinstance(response, str) else str(response)
            if content.startswith(prompt):
                content = content[len(prompt):].strip()
            completion_tokens = token_counter.count_text(content)
            try:
                multi_key_manager.mark_key_success("hf", current_token, tokens=completion_tokens)
            except Exception:
                pass
            usage = token_counter.estimate_cost(prompt_tokens, completion_tokens, model=f"hf/{self.model_name}")
            return LLMResponse(content, usage=usage)
        except ImportError:
            err = "huggingface_hub not installed. pip install huggingface_hub or use ollama/"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model="hf/mock")
            return LLMResponse(err, usage=usage)
        except Exception as e:
            try:
                if current_token:
                    from .multi_key import multi_key_manager

                    multi_key_manager.mark_key_failed("hf", current_token, str(e))
            except Exception:
                pass
            # Fall back to compat error message if we had one
            if compat and compat.content:
                return compat
            err = f"HF error: {e} - Try ollama/ for free offline"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model=f"hf/{self.model_name}")
            return LLMResponse(err, usage=usage)

    def _call_mock(self, messages: list[dict], tools: list[dict] = None) -> LLMResponse:
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
        last = messages[-1].get("content", "") if messages else ""
        if "search" in last.lower() or "weather" in last.lower():
            content = "I'll search for that."
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model="mock/mock")
            return LLMResponse(
                content,
                tool_calls=[{"name": "web_search", "arguments": {"query": last[:100]}, "id": "call_1"}],
                usage=usage,
            )
        if "write" in last.lower() or "file" in last.lower():
            content = "I'll handle file operation."
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model="mock/mock")
            return LLMResponse(content, usage=usage)
        content = (
            f"[MOCK {self.model_name}] Echo: {last[:200]} - "
            "This is mock free LLM. Install Ollama or add any API key: "
            "hermus multikey add --provider groq --key gsk_..."
        )
        usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model="mock/mock")
        return LLMResponse(content, usage=usage)

    def chat(self, messages: list[dict], tools: list[dict] = None) -> LLMResponse:
        """Route to provider. Any unknown provider → OpenAI-compatible HTTP."""
        p = self.provider
        if p == "mock":
            return self._call_mock(messages, tools)
        if p == "ollama":
            return self._call_ollama(messages, tools)
        if p in ("hf", "huggingface"):
            # HF gets special dual path
            return self._call_hf_free(messages, tools)
        # Everything else (groq, openai, openrouter, together, gemini, custom, ...)
        return self._call_openai_compat(messages, tools)

    # ------------------------------------------------------------- streaming
    def _stream_target(self) -> Optional[dict]:
        """Resolve (provider, model, base_url, api_key) for a streaming request.

        Returns None when the provider needs a key we do not have — the caller
        then degrades to the non-streaming path (still chunked for subscribers).
        """
        preset = get_provider(self.provider)
        bundle = self._resolve_bundle()
        api_key = bundle.get("key") or ""
        base_url = bundle.get("base_url") or ""
        model = self.model_name or bundle.get("default_model")
        if self.provider == "ollama":
            base = (base_url or config.ollama_base_url).rstrip("/")
            if not base.endswith("/v1"):
                base = base + "/v1"
            return {"provider": "ollama", "model": model, "base_url": base,
                    "api_key": api_key or "ollama"}
        if not api_key and not preset.get("no_auth"):
            fb = self._fallback_bundle()
            if not fb:
                return None
            return {"provider": fb.get("provider") or self.provider, "model": fb.get("default_model") or model,
                    "base_url": fb.get("base_url") or base_url, "api_key": fb.get("key") or ""}
        return {"provider": self.provider, "model": model, "base_url": base_url, "api_key": api_key}

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        on_delta: Optional[callable] = None,
    ) -> LLMResponse:
        """Streaming chat: pushes text deltas to ``on_delta`` and returns the full response.

        Providers that accept SSE (OpenAI-compatible endpoints, Ollama /v1) stream
        natively. Anything else — mock, HF, offline — falls back to a single
        request whose text is emitted in small chunks, so downstream consumers
        (SSE, WebSocket, TUI) keep a uniform incremental contract.
        """
        if self.provider == "mock":
            resp = self._call_mock(messages, tools)
            _emit_chunks(resp.content, on_delta)
            return resp

        target = self._stream_target()
        if target:
            try:
                from .openai_compat import stream_chat_completions

                stream_tools = self._tools_for_provider(tools, target["provider"])
                resp = stream_chat_completions(
                    provider=target["provider"],
                    model=target["model"],
                    messages=messages,
                    api_key=target.get("api_key"),
                    base_url=target.get("base_url"),
                    tools=stream_tools,
                    temperature=self.temperature if self.temperature is not None else 0.7,
                    timeout=120,
                    on_delta=on_delta,
                )
                return LLMResponse(resp.content, resp.tool_calls, usage=resp.usage)
            except Exception:
                pass  # provider rejected streaming → fall back below

        resp = self.chat(messages, tools)
        _emit_chunks(resp.content, on_delta)
        return resp

    def chat_stream(self, messages: list[dict], tools: list[dict] = None) -> Generator[str, None, None]:
        """Generator view of :meth:`stream_chat` — yields deltas as they arrive."""
        import queue
        import threading

        q: "queue.Queue" = queue.Queue()

        def pump(piece: str) -> None:
            q.put(piece)

        def worker() -> None:
            try:
                resp = self.stream_chat(messages, tools, on_delta=pump)
                q.put(("__done__", resp))
            except Exception as e:  # pragma: no cover - defensive
                q.put(("__error__", str(e)))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "__done__":
                return
            if isinstance(item, tuple) and item[0] == "__error__":
                yield f"[stream error] {item[1]}"
                return
            yield item


def _emit_chunks(text: str, on_delta: Optional[callable], size: int = 24) -> None:
    """Emit ``text`` in small pieces for subscribers when the provider cannot stream."""
    if not on_delta or not text:
        return
    for i in range(0, len(text), size):
        on_delta(text[i : i + size])


free_llm = FreeLLM()
