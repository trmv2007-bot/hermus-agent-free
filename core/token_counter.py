"""Token Counter - Free - Counts tokens for usage tracking, no paywall"""
import re

# Try tiktoken for accurate counting (optional, free)
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

class TokenCounter:
    """Free token counter - uses tiktoken if available, else approximates"""

    def __init__(self, model: str = "gpt-3.5-turbo"):
        self.model = model
        self.encoding = None
        if TIKTOKEN_AVAILABLE:
            try:
                # Try get encoding for model, fallback to cl100k_base
                try:
                    self.encoding = tiktoken.encoding_for_model(model)
                except:
                    self.encoding = tiktoken.get_encoding("cl100k_base")
            except:
                self.encoding = None

    def count_text(self, text: str) -> int:
        """Count tokens in text"""
        if not text:
            return 0

        if self.encoding:
            try:
                return len(self.encoding.encode(text))
            except:
                pass

        # Fallback approximations - free, no API
        # Rule of thumb: 1 token ~ 4 chars or 0.75 words
        # For code, more tokens
        # Simple: len(text) / 4 + len(text.split()) * 0.25
        # Or more accurate: count words * 1.3

        # Detect if code (lots of symbols)
        code_symbols = len(re.findall(r'[{}()\[\]=:;.,]', text))
        if code_symbols > len(text) * 0.1:  # code-like
            # Code has more tokens
            return int(len(text) / 3.5)
        else:
            # Natural language: ~4 chars per token
            return max(1, int(len(text) / 4))

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens in messages list (like OpenAI format)"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            # Add role overhead (approx 4 tokens per message)
            total += self.count_text(content) + 4
            # Tool calls overhead
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                total += self.count_text(str(tc))
        return total

    def count_tools(self, tools: list[dict]) -> int:
        """Count tokens in tool definitions"""
        if not tools:
            return 0
        text = str(tools)
        return self.count_text(text)

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: str = None) -> dict:
        """Estimate cost - free for Ollama, but show for Groq etc for tracking"""
        model = model or self.model

        # Pricing per 1M tokens (approx, free models are 0)
        pricing = {
            "ollama": {"prompt": 0.0, "completion": 0.0},  # local free
            "mock": {"prompt": 0.0, "completion": 0.0},
            "groq/llama-3.1-70b-versatile": {"prompt": 0.59, "completion": 0.79},  # Groq pricing per 1M
            "groq/llama-3.1-8b-instant": {"prompt": 0.05, "completion": 0.08},
            "groq/openai/gpt-oss-20b": {"prompt": 0.0, "completion": 0.0},  # Groq OSS free tier
            "groq/openai/gpt-oss-120b": {"prompt": 0.0, "completion": 0.0},  # Groq OSS free tier
            "hf/mistralai/Mistral-7B-Instruct-v0.3": {"prompt": 0.0, "completion": 0.0},  # HF free
        }

        # Find pricing
        cost_per_1m = pricing.get(model, {"prompt": 0.0, "completion": 0.0})
        # Also try provider prefix
        for key, val in pricing.items():
            if key in model or model in key:
                cost_per_1m = val
                break

        prompt_cost = (prompt_tokens / 1_000_000) * cost_per_1m["prompt"]
        completion_cost = (completion_tokens / 1_000_000) * cost_per_1m["completion"]
        total_cost = prompt_cost + completion_cost

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_cost": prompt_cost,
            "completion_cost": completion_cost,
            "total_cost": total_cost,
            "model": model,
            "is_free": total_cost == 0.0
        }

# Global counter free
token_counter = TokenCounter()

def count_tokens(text: str, model: str = None) -> int:
    counter = TokenCounter(model or "gpt-3.5-turbo")
    return counter.count_text(text)
