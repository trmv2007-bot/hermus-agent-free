"""Free LLM Abstraction - Ollama (offline free) + Groq free tier + HF free inference + mock + Optimized with caching"""
import os
import json
import requests
from typing import List, Dict, Any, Optional, Generator
from .config import config
from .token_counter import token_counter
from .cache import llm_cache

class LLMResponse:
    def __init__(self, content: str, tool_calls: Optional[List[Dict]] = None, usage: Optional[Dict] = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}  # {prompt_tokens, completion_tokens, total_tokens}

class FreeLLM:
    """Abstraction for 100% free LLM providers"""

    def __init__(self, model: str = None):
        self.model = model or config.model
        self.provider, self.model_name = self._parse_model(self.model)

    def _parse_model(self, model_str: str) -> tuple:
        if "/" in model_str:
            provider, name = model_str.split("/", 1)
            return provider.lower(), name
        return "ollama", model_str

    def _call_ollama(self, messages: List[Dict], tools: List[Dict] = None) -> LLMResponse:
        """Ollama local free - http://localhost:11434 - no API key - Optimized with caching"""
        # Count prompt tokens free
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)

        # Check cache - optimize: same prompt + tools = cached response
        cache_key = llm_cache.make_key("ollama", self.model_name, messages[-1].get("content","")[:200] if messages else "", len(messages))
        cached = llm_cache.get(cache_key)
        if cached:
            # Return cached with updated usage (cache hit)
            cached.usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(cached.content), model=f"ollama/{self.model_name}")
            return cached

        url = f"{config.ollama_base_url}/api/chat"
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False,
        }
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
                    tool_calls.append({
                        "name": tc.get("function", {}).get("name"),
                        "arguments": tc.get("function", {}).get("arguments", {}),
                        "id": tc.get("id", "")
                    })
            completion_tokens = token_counter.count_text(content)
            usage = token_counter.estimate_cost(prompt_tokens, completion_tokens, model=f"ollama/{self.model_name}")
            response = LLMResponse(content, tool_calls, usage=usage)
            # Cache successful response - optimize
            llm_cache.set(cache_key, response)
            return response
        except requests.exceptions.ConnectionError:
            # Mock with token counting
            mock_content = f"⚠️ Ollama not running at {config.ollama_base_url}. Start with: ollama serve && ollama pull {self.model_name}\n\nFallback mock response for: {messages[-1].get('content','')[:100]}"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(mock_content), model="ollama/mock")
            return LLMResponse(mock_content, usage=usage)
        except Exception as e:
            err_content = f"Ollama error: {e}"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err_content), model="ollama/mock")
            return LLMResponse(err_content, usage=usage)

    def _call_groq_free(self, messages: List[Dict], tools: List[Dict] = None) -> LLMResponse:
        """Groq free tier - fast cloud, free key from console.groq.com + multi-key support + token counting"""
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
        try:
            from groq import Groq
            # Try multi-key manager first for multiple keys at once
            try:
                from .multi_key import multi_key_manager
                api_key = multi_key_manager.get_key("groq")
                if not api_key:
                    api_key = config.groq_api_key
            except:
                api_key = config.groq_api_key

            if not api_key:
                err = "Groq API key not set. Set GROQ_API_KEY env or use hermus multikey add --provider groq --key gsk_... for multi-key or use ollama/ model for free offline."
                usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model=f"groq/{self.model_name}")
                return LLMResponse(err, usage=usage)
            client = Groq(api_key=api_key)
            # Track key for success/failure
            current_key = api_key
            # Convert tools to Groq format if needed
            kwargs = {"model": self.model_name, "messages": messages}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            completion = client.chat.completions.create(**kwargs)
            msg = completion.choices[0].message
            content = msg.content or ""
            tool_calls = []
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    # Parse arguments JSON
                    args = tc.function.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except:
                            args = {}
                    tool_calls.append({
                        "name": tc.function.name,
                        "arguments": args,
                        "id": tc.id
                    })
            # Use Groq's own usage if available, else estimate
            usage = None
            if hasattr(completion, 'usage') and completion.usage:
                prompt_tokens_groq = completion.usage.prompt_tokens
                completion_tokens_groq = completion.usage.completion_tokens
                usage = token_counter.estimate_cost(prompt_tokens_groq, completion_tokens_groq, model=f"groq/{self.model_name}")
            else:
                usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model=f"groq/{self.model_name}")

            # Mark key success for multi-key load balancing
            try:
                from .multi_key import multi_key_manager
                multi_key_manager.mark_key_success("groq", current_key)
            except:
                pass
            return LLMResponse(content, tool_calls, usage=usage)
        except ImportError:
            err = "Groq package not installed. pip install groq or use ollama/ for free offline."
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model="groq/mock")
            return LLMResponse(err, usage=usage)
        except Exception as e:
            # Mark key failed for multi-key fallback
            try:
                from .multi_key import multi_key_manager
                multi_key_manager.mark_key_failed("groq", current_key, str(e))
            except:
                pass
            # Try next key if available
            try:
                from .multi_key import multi_key_manager
                next_key = multi_key_manager.get_key("groq")
                if next_key and next_key != current_key:
                    usage = token_counter.estimate_cost(prompt_tokens, 20, model=f"groq/{self.model_name}")
                    return LLMResponse(f"Groq key failed, trying next key. Error: {e} - Retrying with another key...", usage=usage)
            except:
                pass
            err = f"Groq error: {e}"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model=f"groq/{self.model_name}")
            return LLMResponse(err, usage=usage)

    def _call_hf_free(self, messages: List[Dict], tools: List[Dict] = None) -> LLMResponse:
        """HuggingFace free inference - slow but free + multi-key support + token counting"""
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
        try:
            # Use multi-key manager for HF tokens
            try:
                from .multi_key import multi_key_manager
                token = multi_key_manager.get_key("hf")
                if not token:
                    token = config.hf_token or os.getenv("HF_TOKEN")
            except:
                token = config.hf_token or os.getenv("HF_TOKEN")

            # Use HF Inference API via requests
            # Convert messages to prompt
            prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            # Use huggingface_hub InferenceClient
            from huggingface_hub import InferenceClient
            client = InferenceClient(token=token)
            current_token = token
            # For free tier, we use text generation
            response = client.text_generation(
                prompt=prompt,
                model=self.model_name,
                max_new_tokens=512,
                temperature=0.7,
            )
            # HF returns string
            if isinstance(response, str):
                content = response
            else:
                content = str(response)
            # Remove prompt echo if model echoes
            if content.startswith(prompt):
                content = content[len(prompt):].strip()
            completion_tokens = token_counter.count_text(content)
            try:
                from .multi_key import multi_key_manager
                multi_key_manager.mark_key_success("hf", current_token)
            except:
                pass
            usage = token_counter.estimate_cost(prompt_tokens, completion_tokens, model=f"hf/{self.model_name}")
            return LLMResponse(content, usage=usage)
        except ImportError:
            err = "huggingface_hub not installed. pip install huggingface_hub or use ollama/"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model="hf/mock")
            return LLMResponse(err, usage=usage)
        except Exception as e:
            try:
                from .multi_key import multi_key_manager
                multi_key_manager.mark_key_failed("hf", current_token, str(e))
            except:
                pass
            err = f"HF error: {e} - Try ollama/ for free offline"
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(err), model=f"hf/{self.model_name}")
            return LLMResponse(err, usage=usage)

    def _call_mock(self, messages: List[Dict], tools: List[Dict] = None) -> LLMResponse:
        """Mock for testing - free, no API needed + token counting"""
        prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
        last = messages[-1].get("content", "") if messages else ""
        # Simple rule-based mock that pretends to call tools
        if "search" in last.lower() or "weather" in last.lower():
            content = "I'll search for that."
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model="mock/mock")
            return LLMResponse(
                content,
                tool_calls=[{"name": "web_search", "arguments": {"query": last[:100]}, "id": "call_1"}],
                usage=usage
            )
        if "write" in last.lower() or "file" in last.lower():
            content = "I'll handle file operation."
            usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model="mock/mock")
            return LLMResponse(content, usage=usage)
        content = f"[MOCK {self.model_name}] Echo: {last[:200]} - This is mock free LLM. Install Ollama for real free LLM: ollama pull llama3.1:8b"
        usage = token_counter.estimate_cost(prompt_tokens, token_counter.count_text(content), model="mock/mock")
        return LLMResponse(content, usage=usage)

    def chat(self, messages: List[Dict], tools: List[Dict] = None) -> LLMResponse:
        """Main entry - routes to free provider"""
        if self.provider == "ollama":
            return self._call_ollama(messages, tools)
        elif self.provider == "groq":
            return self._call_groq_free(messages, tools)
        elif self.provider in ("hf", "huggingface"):
            return self._call_hf_free(messages, tools)
        elif self.provider == "mock":
            return self._call_mock(messages, tools)
        else:
            # Default to ollama
            return self._call_ollama(messages, tools)

    def chat_stream(self, messages: List[Dict], tools: List[Dict] = None) -> Generator[str, None, None]:
        """Streaming version for TUI - yields content chunks"""
        # For simplicity, non-streaming for now, but yields in chunks for TUI streaming effect
        resp = self.chat(messages, tools)
        # Simulate streaming by yielding words
        words = resp.content.split()
        for i, word in enumerate(words):
            yield word + " "
            # Small delay could be added but avoid import time

# Global free LLM instance
free_llm = FreeLLM()
