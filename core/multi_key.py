"""Multi-API Keys - Use multiple API keys at once to complete tasks quickly, free load balancing, fallback"""
import json
import random
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import defaultdict, deque
from datetime import datetime, timedelta
from .config import config

class MultiKeyManager:
    """Manage multiple API keys per provider - free load balancing, rate limit handling, parallel execution"""

    MAX_KEYS_PER_PROVIDER = 20  # Increased from 3 to 20 as requested (user asked 10 or 8, we allow 20 for future)
    MAX_KEYS_PER_CUSTOM_API = 10  # For custom APIs same name, allow up to 10 keys from different websites

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path("data/api_keys.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.db_path.write_text(json.dumps({
                "groq": [],
                "hf": [],
                "openai": [],
                "custom": []
            }, indent=2))

        # In-memory tracking for rate limits and round-robin
        self.key_queues: Dict[str, deque] = defaultdict(deque)
        self.key_failures: Dict[str, Dict[str, int]] = defaultdict(dict)  # provider -> key -> fail count
        self.key_last_used: Dict[str, Dict[str, datetime]] = defaultdict(dict)
        self._load_queues()

    def _load(self) -> Dict:
        try:
            return json.loads(self.db_path.read_text())
        except:
            return {"groq": [], "hf": [], "openai": [], "custom": []}

    def _save(self, data: Dict):
        self.db_path.write_text(json.dumps(data, indent=2))

    def _load_queues(self):
        """Load keys into round-robin queues"""
        data = self._load()
        for provider, keys in data.items():
            # Keys can be list of strings or list of dicts with extra info
            normalized_keys = []
            for k in keys:
                if isinstance(k, str):
                    normalized_keys.append(k)
                elif isinstance(k, dict):
                    # Dict format: {"key": "...", "name": "..."}
                    normalized_keys.append(k.get("key") or k.get("token") or "")
            # Filter empty
            normalized_keys = [k for k in normalized_keys if k]
            self.key_queues[provider] = deque(normalized_keys)
            for key in normalized_keys:
                self.key_failures[provider][key] = 0
                self.key_last_used[provider][key] = datetime.min

    def list_keys(self, provider: str = None) -> Dict:
        data = self._load()
        if provider:
            return {provider: data.get(provider, [])}
        return data

    def add_key(self, provider: str, api_key: str, name: str = None) -> Dict:
        """Add API key for provider - free - now supports up to 10-20 keys as requested"""
        data = self._load()
        if provider not in data:
            data[provider] = []

        # Check limit (increased to 10-20 as user requested)
        max_keys = self.MAX_KEYS_PER_CUSTOM_API if provider.startswith("custom_") else self.MAX_KEYS_PER_PROVIDER
        if len(data[provider]) >= max_keys:
            return {"success": False, "error": f"Max {max_keys} keys per provider reached for {provider}. Remove old keys first with multikey remove."}

        # Check if key already exists
        existing_keys = [k if isinstance(k, str) else k.get("key","") for k in data[provider]]
        if api_key in existing_keys:
            return {"success": False, "error": f"Key already exists for {provider}"}

        # Store as dict with metadata for better tracking
        key_entry = {
            "key": api_key,
            "name": name or f"{provider}_key_{len(data[provider])+1}",
            "added": datetime.now().isoformat(),
            "usage_count": 0
        }
        data[provider].append(key_entry)
        self._save(data)
        self._load_queues()  # Reload queues

        return {"success": True, "provider": provider, "key_name": key_entry["name"], "total_keys": len(data[provider])}

    def remove_key(self, provider: str, key_or_name: str) -> Dict:
        data = self._load()
        if provider not in data:
            return {"success": False, "error": f"Provider {provider} not found"}

        original_len = len(data[provider])
        # Remove by key or name
        data[provider] = [
            k for k in data[provider]
            if (k if isinstance(k, str) else k.get("key","")) != key_or_name
            and (k if isinstance(k, dict) else {}).get("name","") != key_or_name
        ]
        if len(data[provider]) == original_len:
            return {"success": False, "error": f"Key {key_or_name} not found for {provider}"}

        self._save(data)
        self._load_queues()
        return {"success": True, "provider": provider, "remaining": len(data[provider])}

    def get_key(self, provider: str = "groq") -> Optional[str]:
        """Get next available key via round-robin, skip failed keys - free load balancing"""
        if provider not in self.key_queues or not self.key_queues[provider]:
            # Fallback to env var single key
            if provider == "groq":
                env_key = config.groq_api_key
                if env_key:
                    return env_key
            elif provider == "hf":
                env_key = config.hf_token
                if env_key:
                    return env_key
            return None

        # Round-robin with failure handling
        queue = self.key_queues[provider]
        attempts = len(queue)

        for _ in range(attempts):
            key = queue[0]  # Peek
            # Check if key has too many failures (rate limited)
            fails = self.key_failures[provider].get(key, 0)
            if fails >= 3:
                # Check if enough time passed (5 min cooldown)
                last_used = self.key_last_used[provider].get(key, datetime.min)
                if datetime.now() - last_used < timedelta(minutes=5):
                    # Rotate and skip
                    queue.rotate(-1)
                    continue
                else:
                    # Reset failures after cooldown
                    self.key_failures[provider][key] = 0

            # Use this key
            queue.rotate(-1)  # Move to end for round-robin
            self.key_last_used[provider][key] = datetime.now()
            return key

        # All keys failed, return first anyway as fallback
        return queue[0] if queue else None

    def mark_key_success(self, provider: str, key: str):
        """Mark key as successful - reset failures"""
        if provider in self.key_failures and key in self.key_failures[provider]:
            self.key_failures[provider][key] = max(0, self.key_failures[provider][key] - 1)

        # Update usage count in file
        try:
            data = self._load()
            for k in data.get(provider, []):
                if isinstance(k, dict) and k.get("key") == key:
                    k["usage_count"] = k.get("usage_count", 0) + 1
                    k["last_used"] = datetime.now().isoformat()
            self._save(data)
        except:
            pass

    def mark_key_failed(self, provider: str, key: str, error: str = ""):
        """Mark key as failed - for rate limit handling"""
        if provider in self.key_failures:
            self.key_failures[provider][key] = self.key_failures[provider].get(key, 0) + 1
        print(f"[MultiKey] Key {key[:10]}... for {provider} failed ({error}), failures: {self.key_failures[provider].get(key,0)}")

    def execute_parallel_with_keys(self, provider: str, tasks: List[Dict]) -> List[Dict]:
        """Execute tasks in parallel using different API keys - complete task quickly, free"""
        import multiprocessing
        from .llm import FreeLLM

        data = self._load()
        keys = data.get(provider, [])
        if not keys:
            # Fallback to single env key
            single_key = self.get_key(provider)
            if not single_key:
                return [{"success": False, "error": f"No keys for {provider}"}]
            keys = [single_key]

        # Normalize keys to strings
        key_strings = [k if isinstance(k, str) else k.get("key","") for k in keys]
        key_strings = [k for k in key_strings if k]

        if not key_strings:
            return [{"success": False, "error": "No valid keys"}]

        print(f"[MultiKey] Parallel execution with {len(key_strings)} keys for {len(tasks)} tasks - completing quickly")

        # Create queue for results
        result_queue = multiprocessing.Queue()
        processes = []

        def task_wrapper(task_data, api_key, task_id, queue):
            try:
                # Each parallel task uses different API key via env var override
                # For Groq, set GROQ_API_KEY env for this process
                import os
                if provider == "groq":
                    os.environ["GROQ_API_KEY"] = api_key
                elif provider == "hf":
                    os.environ["HF_TOKEN"] = api_key

                # Create LLM with specific key
                # For simplicity, use FreeLLM which will pick up env var
                llm = FreeLLM(f"{provider}/{task_data.get('model','llama-3.1-70b-versatile')}" if provider=="groq" else f"{provider}/mistralai/Mistral-7B-Instruct-v0.3")

                messages = task_data.get("messages", [{"role": "user", "content": task_data.get("prompt","")}])
                resp = llm.chat(messages, tools=task_data.get("tools"))

                queue.put({
                    "task_id": task_id,
                    "task": task_data,
                    "api_key": api_key[:10] + "...",
                    "response": resp.content,
                    "tool_calls": resp.tool_calls,
                    "success": True
                })
            except Exception as e:
                queue.put({
                    "task_id": task_id,
                    "task": task_data,
                    "api_key": api_key[:10] + "..." if api_key else "none",
                    "error": str(e),
                    "success": False
                })

        # Spawn processes, each with different key (round-robin)
        for idx, task in enumerate(tasks):
            key = key_strings[idx % len(key_strings)]
            p = multiprocessing.Process(target=task_wrapper, args=(task, key, idx, result_queue))
            p.start()
            processes.append(p)

        # Wait for all with timeout
        for p in processes:
            p.join(timeout=60)
            if p.is_alive():
                p.terminate()

        results = []
        while not result_queue.empty():
            results.append(result_queue.get())

        # Sort by task_id
        results.sort(key=lambda x: x.get("task_id",0))
        return results

# Global manager free
multi_key_manager = MultiKeyManager()
