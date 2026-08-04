# Token Counting - Free - Tracks How Many Tokens You Use

Yes! This free version **counts tokens** for all models, no paywall.

## How It Works (Free)

### Free Token Counter

`core/token_counter.py` - Uses `tiktoken` if installed (accurate, free), else fallback approximation (len(text)/4) - no API needed.

- **Accurate mode:** If `pip install tiktoken` (free), uses `cl100k_base` encoding for precise counts
- **Fallback mode:** If no tiktoken, approximates: natural language ~4 chars/token, code ~3.5 chars/token

### LLM Integration

All free LLM providers now return `usage` with every response:

```python
# In core/llm.py
prompt_tokens = token_counter.count_messages(messages) + token_counter.count_tools(tools)
# ... call LLM ...
completion_tokens = token_counter.count_text(content)
usage = token_counter.estimate_cost(prompt_tokens, completion_tokens, model="ollama/llama3.1:8b")
return LLMResponse(content, tool_calls, usage=usage)
```

For Groq, it uses Groq's own usage if available (`completion.usage.prompt_tokens`), else estimated.

### Cost Estimation (Free Models = $0)

```python
pricing = {
    "ollama": {"prompt": 0.0, "completion": 0.0},  # local free
    "mock": {"prompt": 0.0, "completion": 0.0},
    "groq/llama-3.1-70b-versatile": {"prompt": 0.59, "completion": 0.79},  # per 1M tokens
    "groq/llama-3.1-8b-instant": {"prompt": 0.05, "completion": 0.08},
    "hf/...": {"prompt": 0.0, "completion": 0.0},  # HF free
}
cost = (tokens / 1M) * price_per_1M
```

Ollama, mock, HF free = $0 cost, but still counts tokens for context window management.

### Storage - SQLite Free

New table `token_usage` in `data/memory.db` (SQLite, no vector DB cost):

```sql
CREATE TABLE token_usage (
    session_id TEXT,
    timestamp TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost REAL,
    is_free BOOLEAN
)
```

Every LLM call does `memory.add_token_usage(session_id, usage)`

### CLI - Check Usage

Original Hermes has `/usage`, `/compress`, `/insights` - we implemented same, free:

```bash
# In TUI
You> Hello

Hermus> Hi...

You> /usage

Curated memory (2 items):
  - session_abc_topic: Hello -> Hi...
Trajectory length: 2 turns
Session: session_20260804_...

--- Token Usage (Free Tracking) ---
Session session_20260804_...:
  Prompt tokens: 150
  Completion tokens: 50
  Total tokens: 200
  Total cost: $0.000000 (0.0 = free Ollama/mock/HF free)
  Recent calls: 2
    - 2026-08-04T07:30:00 ollama/llama3.1:8b : 100+30=130 tokens cost=$0.000000 free=True
    - 2026-08-04T07:30:05 groq/llama-3.1-70b : 50+20=70 tokens cost=$0.000080 free=False

Global totals (all sessions):
  Prompt: 1000, Completion: 500, Total: 1500, Cost: $0.000985

You> /compress

Trajectory tokens: 850 - Use /new to reset if too large for context window
```

### Python API

```python
from core.memory import memory

# Session usage
usage = memory.get_token_usage(session_id="session_abc")
print(usage["totals"])  # {prompt_tokens, completion_tokens, total_tokens, total_cost}

# Global usage
global_usage = memory.get_token_usage(limit=100)
print(global_usage["totals"]["total_tokens"])
print(f"Total cost: ${global_usage['totals']['total_cost']:.6f}")

# Count text directly
from core.token_counter import count_tokens
count = count_tokens("Hello world about Python async")
print(f"{count} tokens")
```

### Multi-Key + Multi-AI Integration

- **Multi-Key:** Each key usage tracked separately, see which key used how many tokens
- **Multi-AI:** Each agent's token usage tracked in same session_id, final judge summarization also tracked

### Why Count Tokens If Free?

- **Context window management:** Ollama llama3.1:8b has 128k context - if trajectory tokens > 100k, need /compress or /new
- **Rate limits:** Groq free tier counts tokens for rate limit - tracking helps avoid 429
- **Cost tracking for paid models:** If you use Groq 70b (paid after free tier), you see cost $0.000985 etc
- **Research-ready:** trajectory.jsonl + token counts for training next-gen models

### Free vs Paywalled

Original Hermes maybe has usage tracking via OpenRouter dashboard (paid). This free version tracks locally in SQLite, no API needed, works offline with Ollama.

No paywall, MIT, fully self-hosted - you own `data/memory.db` token_usage table.

## Test

```bash
pip install tiktoken  # optional for accurate counting, free
python tests/test_token_count.py
# Uses mock LLM, no API key, tests token counting + cost estimation + memory tracking
```
