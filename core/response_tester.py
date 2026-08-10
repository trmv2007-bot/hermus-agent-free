"""Response Time Tester - Test how much time API key takes to get response from AI model - free"""

import time
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from .config import config

class ResponseTimeTester:
    """Test response time for API keys - free"""

    def __init__(self):
        self.results_path = config.resolve_path("data/response_times.json")
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.results_path.exists():
            self.results_path.write_text("[]")

    def _load(self) -> List[Dict]:
        try:
            return json.loads(self.results_path.read_text())
        except:
            return []

    def _save(self, data: List[Dict]):
        self.results_path.write_text(json.dumps(data, indent=2))

    def test_llm_key(
        self,
        provider: str,
        api_key: str,
        model: str = None,
        prompt: str = "Hello, what is Python async?",
        timeout: int = 30,
        base_url: str = None,
    ) -> Dict:
        """Test response time for ANY LLM API key (OpenAI-compatible)."""
        start = time.time()
        success = False
        error = None
        response_text = ""
        tokens = 0
        rate_limit = {}
        health = {}

        try:
            from .providers import get_provider
            from .openai_compat import health_ping, chat_completions, CompatAPIError

            preset = get_provider(provider)
            model_name = model or preset.get("default_model") or "gpt-3.5-turbo"
            # Full health includes models + tiny chat
            health = health_ping(
                provider,
                api_key=api_key,
                base_url=base_url or preset.get("base_url"),
                model=model_name,
                timeout=timeout,
            )
            end = time.time()
            elapsed = end - start
            success = bool(health.get("healthy") or health.get("success"))
            response_text = health.get("response_preview") or ""
            error = health.get("error")
            rate_limit = health.get("rate_limit") or {}
            usage = health.get("usage") or {}
            tokens = usage.get("total_tokens") or 0
            if health.get("model_tested"):
                model_name = health["model_tested"]

        except Exception as e:
            end = time.time()
            elapsed = end - start
            error = str(e)

        result = {
            "provider": provider,
            "api_key_preview": f"{api_key[:6]}...{api_key[-4:]}" if api_key and len(api_key) > 10 else "****",
            "api_key_full": api_key,
            "model": model or f"{provider}/default",
            "model_tested": health.get("model_tested") if isinstance(health, dict) else model,
            "prompt": prompt[:100],
            "response_time_seconds": round(elapsed, 3),
            "response_time_ms": int(elapsed * 1000),
            "success": success,
            "error": error,
            "response_preview": response_text[:200] if success else "",
            "tokens": tokens,
            "rate_limit": rate_limit,
            "models_sample": (health.get("models_probe") or {}).get("sample") if isinstance(health, dict) else [],
            "models_count": (health.get("models_probe") or {}).get("count") if isinstance(health, dict) else 0,
            "health_status": health.get("status") if isinstance(health, dict) else None,
            "timestamp": datetime.now().isoformat(),
            "test_id": f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{provider}",
        }

        # Save to history
        history = self._load()
        history.append(result)
        # Keep last 50
        if len(history) > 50:
            history = history[-50:]
        self._save(history)

        # Also update multi-key manager with response time
        try:
            from .multi_key import multi_key_manager
            # Store response time in key metadata
            data = multi_key_manager._load()
            for k in data.get(provider, []):
                key_val = k if isinstance(k, str) else k.get("key","")
                if key_val == api_key:
                    if isinstance(k, dict):
                        # Update dict with response time history
                        if "response_times" not in k:
                            k["response_times"] = []
                        k["response_times"].append(elapsed)
                        # Keep last 10
                        if len(k["response_times"]) > 10:
                            k["response_times"] = k["response_times"][-10:]
                        k["avg_response_time"] = sum(k["response_times"]) / len(k["response_times"])
                        k["last_tested"] = datetime.now().isoformat()
                        k["last_response_time"] = elapsed
            multi_key_manager._save(data)
        except Exception as e:
            print(f"Failed to update multi-key with response time: {e}")

        return result

    def test_custom_api_key(self, api_name: str, api_key: str = None, test_args: Dict = None, timeout: int = 30) -> Dict:
        """Test response time for custom API key from different website"""
        from .custom_api import custom_api_manager
        import time as time_module

        # Find API by name
        apis = custom_api_manager.list_apis()
        matching = [a for a in apis if a["name"] == api_name]
        if not matching:
            return {"success": False, "error": f"Custom API {api_name} not found"}

        # If api_key provided, find specific API variant with that key, else use first
        target_api = None
        if api_key:
            for api in matching:
                token = api.get("auth",{}).get("token") or api.get("auth",{}).get("value") or ""
                if token == api_key or api_key in token or token in api_key:
                    target_api = api
                    break
        if not target_api:
            target_api = matching[0]

        test_args = test_args or {"id": "1"}  # Default test args for jsonplaceholder style

        start = time_module.time()
        try:
            result = custom_api_manager._execute_single_api(target_api, test_args)
            end = time_module.time()
            elapsed = end - start
            success = result.get("success", False) and result.get("status_code", 500) < 400

            test_result = {
                "api_name": api_name,
                "api_id": target_api.get("id",""),
                "api_key_preview": f"{api_key[:6]}...{api_key[-4:]}" if api_key and len(api_key)>10 else f"{(target_api.get('auth',{}).get('token','') or '')[:6]}...{ (target_api.get('auth',{}).get('token','') or '')[-4:]}" if (target_api.get('auth',{}).get('token','') or '') else "no-token",
                "url": target_api.get("url",""),
                "method": target_api.get("method","GET"),
                "test_args": test_args,
                "response_time_seconds": round(elapsed, 3),
                "response_time_ms": int(elapsed * 1000),
                "success": success,
                "status_code": result.get("status_code"),
                "error": result.get("error"),
                "response_preview": str(result.get("data",""))[:300] if success else "",
                "timestamp": datetime.now().isoformat(),
                "test_id": f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{api_name}"
            }

            # Save
            history = self._load()
            history.append(test_result)
            if len(history) > 50:
                history = history[-50:]
            self._save(history)

            return test_result

        except Exception as e:
            end = time_module.time()
            elapsed = end - start
            return {
                "api_name": api_name,
                "response_time_seconds": round(elapsed, 3),
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def test_all_keys_for_provider(self, provider: str, prompt: str = "Hello", model: str = None) -> List[Dict]:
        """Test all keys for provider and rank by response time - free, to find fastest key"""
        from .multi_key import multi_key_manager
        data = multi_key_manager.list_keys()
        keys = data.get(provider, [])

        results = []
        for key_entry in keys:
            key_val = key_entry if isinstance(key_entry, str) else key_entry.get("key","")
            key_name = key_entry.get("name","") if isinstance(key_entry, dict) else ""
            if not key_val:
                continue
            print(f"[ResponseTime] Testing {provider} key {key_name or key_val[:10]}...")
            result = self.test_llm_key(provider, key_val, model=model, prompt=prompt)
            results.append(result)

        # Sort by response time ascending (fastest first), successful first
        results.sort(key=lambda x: (0 if x["success"] else 1, x["response_time_seconds"]))

        return results

    def test_all_keys_for_custom_api(self, api_name: str, test_args: Dict = None) -> List[Dict]:
        """Test all keys for same custom API name from different websites and rank by response time"""
        from .custom_api import custom_api_manager
        apis = custom_api_manager.list_apis()
        matching = [a for a in apis if a["name"] == api_name]

        results = []
        for api in matching:
            token = api.get("auth",{}).get("token") or api.get("auth",{}).get("value") or "no-token"
            result = self.test_custom_api_key(api_name, api_key=token, test_args=test_args)
            results.append(result)

        results.sort(key=lambda x: (0 if x["success"] else 1, x["response_time_seconds"]))
        return results

    def get_history(self, limit: int = 20) -> List[Dict]:
        history = self._load()
        return history[-limit:][::-1]  # Most recent first

    def get_stats(self) -> Dict:
        history = self._load()
        if not history:
            return {"total_tests": 0, "avg_response_time": 0, "fastest": None, "slowest": None}

        successful = [h for h in history if h.get("success")]
        if not successful:
            return {"total_tests": len(history), "successful": 0, "failed": len(history)}

        avg = sum(h["response_time_seconds"] for h in successful) / len(successful) if successful else 0
        fastest = min(successful, key=lambda x: x["response_time_seconds"]) if successful else None
        slowest = max(successful, key=lambda x: x["response_time_seconds"]) if successful else None

        return {
            "total_tests": len(history),
            "successful": len(successful),
            "failed": len(history) - len(successful),
            "avg_response_time_seconds": round(avg, 3),
            "fastest": fastest,
            "slowest": slowest,
            "recent": history[-5:][::-1]
        }

# Global tester free
response_tester = ResponseTimeTester()

# Tool definitions for free LLM
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "test_api_key_response_time",
            "description": "Test response time for API key - how much time does API key take to get response from AI model - free, measures latency, ranks keys by speed",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "enum": ["groq", "hf", "openai", "custom"], "description": "Provider"},
                    "api_key": {"type": "string", "description": "API key to test (or will test all keys for provider if not provided)"},
                    "model": {"type": "string", "description": "Model name e.g., llama-3.1-8b-instant, mistralai/Mistral-7B-Instruct-v0.3"},
                    "prompt": {"type": "string", "description": "Test prompt", "default": "Hello, what is Python async?"}
                },
                "required": ["provider"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "test_custom_api_response_time",
            "description": "Test response time for custom API key from different website - how much time does custom API take - free, measures latency for keys from different websites you know, ranks fastest",
            "parameters": {
                "type": "object",
                "properties": {
                    "api_name": {"type": "string", "description": "Custom API name e.g., weather_api"},
                    "api_key": {"type": "string", "description": "Specific API key to test (optional, if not provided tests all keys for same API name)"},
                    "test_args": {"type": "object", "description": "Test args as JSON e.g., {\"id\": \"1\"} or {\"q\": \"London\"}"}
                },
                "required": ["api_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_response_time_stats",
            "description": "Get response time stats - average, fastest, slowest API key, history",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]

TOOL_MAP = {
    "test_api_key_response_time": lambda provider, api_key=None, model=None, prompt="Hello, what is Python async?": response_tester.test_llm_key(provider, api_key, model, prompt) if api_key else {"results": response_tester.test_all_keys_for_provider(provider, prompt, model)},
    "test_custom_api_response_time": lambda api_name, api_key=None, test_args=None: response_tester.test_custom_api_key(api_name, api_key, test_args) if api_key else {"results": response_tester.test_all_keys_for_custom_api(api_name, test_args)},
    "get_response_time_stats": lambda: response_tester.get_stats(),
}
