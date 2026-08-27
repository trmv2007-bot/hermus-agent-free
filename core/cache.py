"""Cache - Optimize everything - Free - LRU cache for LLM, memory, tools, etc."""
import time
import json
import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional
from threading import Lock

class LRUCache:
    """Simple LRU cache with TTL - free, no external deps"""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.cache = OrderedDict()
        self.timestamps = {}
        self.lock = Lock()
        self.hits = 0
        self.misses = 0

    def _is_expired(self, key: str) -> bool:
        if key not in self.timestamps:
            return True
        return time.time() - self.timestamps[key] > self.ttl

    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key not in self.cache or self._is_expired(key):
                if key in self.cache:
                    del self.cache[key]
                    del self.timestamps[key]
                self.misses += 1
                return None
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]

    def set(self, key: str, value: Any):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            self.timestamps[key] = time.time()
            # Evict oldest if over size
            if len(self.cache) > self.max_size:
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                del self.timestamps[oldest_key]

    def clear(self):
        with self.lock:
            self.cache.clear()
            self.timestamps.clear()

    def stats(self) -> dict:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 3),
            "ttl_seconds": self.ttl
        }

    def make_key(self, *args, **kwargs) -> str:
        """Make cache key from args"""
        # Simple hash of args
        data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.md5(data.encode()).hexdigest()

# Global caches - free, optimized
llm_cache = LRUCache(max_size=100, ttl_seconds=600)  # LLM responses cached 10 min
memory_search_cache = LRUCache(max_size=50, ttl_seconds=300)  # Memory search cached 5 min
web_search_cache = LRUCache(max_size=50, ttl_seconds=600)  # Web search cached 10 min
tool_result_cache = LRUCache(max_size=100, ttl_seconds=300)  # Tool results cached 5 min
skill_cache = LRUCache(max_size=20, ttl_seconds=600)  # Skills cached 10 min

class OptimizedFileCache:
    """Optimized file cache - cache file reads"""

    def __init__(self):
        self.cache = {}
        self.mtimes = {}

    def read(self, path: str) -> Optional[str]:
        p = Path(path)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        if path in self.cache and self.mtimes.get(path) == mtime:
            return self.cache[path]
        try:
            content = p.read_text(encoding='utf-8', errors='ignore')
            # Only cache if < 1MB
            if len(content) < 1024*1024:
                self.cache[path] = content
                self.mtimes[path] = mtime
            return content
        except OSError:
            return None

    def clear(self):
        self.cache.clear()
        self.mtimes.clear()

file_cache = OptimizedFileCache()

def clear_all_caches():
    """Clear all caches - for /clear or memory management"""
    llm_cache.clear()
    memory_search_cache.clear()
    web_search_cache.clear()
    tool_result_cache.clear()
    skill_cache.clear()
    file_cache.clear()
    return {"cleared": True, "message": "All caches cleared"}

def get_cache_stats() -> dict:
    """Get stats for all caches - for dashboard analytics"""
    return {
        "llm_cache": llm_cache.stats(),
        "memory_search_cache": memory_search_cache.stats(),
        "web_search_cache": web_search_cache.stats(),
        "tool_result_cache": tool_result_cache.stats(),
        "skill_cache": skill_cache.stats(),
        "total_hit_rate": round(
            (llm_cache.hits + memory_search_cache.hits + web_search_cache.hits) /
            max(1, llm_cache.hits + llm_cache.misses + memory_search_cache.hits + memory_search_cache.misses + web_search_cache.hits + web_search_cache.misses),
            3
        )
    }
