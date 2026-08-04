# Multi-API Keys + Multi-AI Collaboration - Free

Two new free features as you requested: Use multiple API keys at once to complete quickly + Multiple AIs talk to each other for anything

## 1. Multi-API Keys - Complete Task Quickly

### Why?

- Groq free tier: 30 req/min per key - with 1 key you hit rate limit
- With 3 keys: 90 req/min - 3x faster, no waiting
- If one key fails / rate limited, auto fallback to next key - no interruption
- Parallel tasks: Use different keys for different sub-tasks in parallel = complete quickly

### How It Works (Free Load Balancing)

**Round-robin + failure tracking + cooldown:**

```
Keys for Groq: [gsk_key1, gsk_key2, gsk_key3]
Request 1 -> key1
Request 2 -> key2
Request 3 -> key3
Request 4 -> key1 again (round-robin)

If key2 gets 429 rate limit:
- Mark failed, failure count +1
- If failures >=3, cooldown 5 minutes, skip it
- Auto try next key immediately
- After cooldown, reset failures
```

Stored in `data/api_keys.json` - simple JSON file, free, no DB cost.

### CLI - Multi-Key

```bash
# Add multiple Groq keys (free tier keys from console.groq.com)
python hermus.py multikey add --provider groq --key gsk_abc... --name groq_key_1
python hermus.py multikey add --provider groq --key gsk_def... --name groq_key_2
python hermus.py multikey add --provider groq --key gsk_ghi... --name groq_key_3

# List
python hermus.py multikey list
python hermus.py multikey list --provider groq

# Output:
# Multi-API Keys:
#  groq: 3 keys
#    - groq_key_1: gsk_abc... usage=5 added=2026-08-04
#    - groq_key_2: gsk_def... usage=3
#    - groq_key_3: gsk_ghi... usage=0

# Remove
python hermus.py multikey remove --provider groq --key groq_key_2

# Test parallel execution with multiple keys - completes quickly
python hermus.py multikey parallel --provider groq --tasks "What is Python?" "What is Rust?" "What is Go?"

# Each task uses different API key in parallel via multiprocessing
# Result: 3 tasks completed in ~2 sec instead of 6 sec sequential = 3x faster
```

### Supported Providers

- `groq` - Groq free tier (30 req/min per key) - best for free fast cloud
- `hf` - HuggingFace free inference tokens
- `openai` - If you have multiple OpenAI keys
- `custom` - Any custom provider

### Agent Integration (Automatic)

Once you add multiple keys via `multikey add`, the agent's LLM calls automatically use round-robin:

```python
# In core/llm.py _call_groq_free()
api_key = multi_key_manager.get_key("groq")  # Returns next key round-robin
client = Groq(api_key=api_key)
# On success: mark_key_success() -> reset failures
# On failure: mark_key_failed() -> failure count +1, auto fallback to next key
```

No code change needed - just add keys and agent uses them.

### Parallel Tasks - Complete Quickly

```python
from core.multi_key import multi_key_manager

tasks = [
    {"prompt": "Research Python async", "messages": [...]},
    {"prompt": "Research Rust async", "messages": [...]},
    {"prompt": "Research Go concurrency", "messages": [...]}
]

# Each task uses different API key in parallel process
results = multi_key_manager.execute_parallel_with_keys("groq", tasks)
# Results: 3 tasks in parallel, each with different key, completes quickly
```

**Example: Research 3 topics that would take 30 sec sequential with 1 key, takes 10 sec with 3 keys in parallel.**

## 2. Multi-AI Collaboration - Multiple AIs Talk to Each Other

### Why?

Single AI can be biased, miss edge cases. Multiple AIs with different personas debating = better answers:

- Researcher (thorough, facts, sources)
- Coder (practical, clean code, tools)
- Reviewer (critical, security, edge cases)
- Writer, Planner, Debater, Optimist, Pessimist...

They talk to each other for anything - research, coding, writing, planning.

### How It Works (Free)

```
User: "Should we use Python async or Rust async for our project?"

Round 1:
- researcher: Searches, provides facts about Python async (easy, GIL) vs Rust async (fast, no GIL, steep learning)
- coder: Writes example code for both
- reviewer: Checks researcher facts, coder code for errors, security

Round 2:
- researcher: Builds on coder's code, adds benchmarks
- coder: Fixes reviewer comments
- reviewer: Re-checks

Round 3:
- All discuss trade-offs, aim for consensus

Judge: Summarizes final answer with agreements, disagreements, best path
```

Each agent is `HermusAgent` with different persona + system prompt + same or different model.

Conversation history saved to `data/memory.db` for cross-session recall.

### CLI - Multi-AI

```bash
# Debate on topic with default team (researcher, coder, reviewer) - 2 rounds
python hermus.py multiai debate "Should we use Python or Rust for async?" --rounds 2

# Output:
# === Multi-AI Debate: Should we use Python or Rust... ===
# Agents: researcher, coder, reviewer | Rounds: 2
#
# [researcher - Round 1]: Python async is easy...
# [coder - Round 1]: Here's Python code...
# [reviewer - Round 1]: Coder missed GIL issue...
# [researcher - Round 2]: Building on coder...
# ...
# === Final Answer ===
# Consensus: Use Python for prototyping, Rust for performance critical...

# Collaborative chat on task
python hermus.py multiai chat "Research best Python async libraries and write file watcher" --rounds 3

# Custom team
python hermus.py multiai debate "Is AI dangerous?" --agents debater optimist pessimist --rounds 3

# List persona presets
python hermus.py multiai personas
# Output:
#  - researcher: You are a thorough researcher...
#  - coder: You are an expert coder...
#  - reviewer: You are a critical reviewer...
#  - writer, planner, debater, optimist, pessimist...
```

### Persona Presets (Free)

```python
PERSONA_PRESETS = {
    "researcher": "You are a thorough researcher. Search, analyze, provide facts and sources.",
    "coder": "You are an expert coder. Write clean, efficient code, use tools.",
    "reviewer": "You are a critical reviewer. Check errors, security, edge cases.",
    "writer": "You are a creative writer.",
    "planner": "You are a project planner. Break tasks into steps.",
    "debater": "You are a debater. Argue pros/cons, balanced.",
    "optimist": "You are an optimist. Focus on opportunities.",
    "pessimist": "You are a pessimist (devil's advocate). Focus on risks.",
}
```

Add custom persona:

```bash
python hermus.py multiai debate "Topic" --agents researcher coder --model ollama/llama3.1:8b
# You can pass any persona name, if not in presets, it becomes "You are a {name}"
```

### Python API - Multi-AI

```python
from core.multi_ai import MultiAIChat

chat = MultiAIChat()
chat.add_agent("researcher", "You are a thorough researcher", model="ollama/llama3.1:8b")
chat.add_agent("coder", "You are an expert coder", model="groq/llama-3.1-70b-versatile")  # Mix models free
chat.add_agent("reviewer", "You are a critical reviewer")

result = chat.debate("Should we use Python or Rust?", rounds=2)
print(result["final_answer"])

# Or collaborate with tools
result = chat.collaborate_on_task("Research Python async and write file watcher", tools=[...], rounds=3)
```

### Mixing Multi-Key + Multi-AI for Speed

**Best combo for completing quickly:**

- 3 agents: researcher, coder, reviewer
- Each agent uses different Groq API key via multi-key manager
- They work in parallel, debate, consensus

```
You: 3 tasks: research Python, research Rust, research Go

With single key, single AI: 30 sec sequential
With 3 keys + 3 AIs parallel: 10 sec parallel + debate = 15 sec total = 2x faster + better quality
```

Implementation:

```python
from core.multi_key import multi_key_manager
from core.multi_ai import MultiAIChat

# Add 3 Groq keys free
multi_key_manager.add_key("groq", "gsk_key1")
multi_key_manager.add_key("groq", "gsk_key2")
multi_key_manager.add_key("groq", "gsk_key3")

# Multi-AI debate with parallel subagents each using different key
chat = MultiAIChat()
chat.add_default_team(model="groq/llama-3.1-70b-versatile")  # Will auto use 3 keys round-robin
result = chat.debate("Python vs Rust vs Go for async", rounds=2)
# Each agent call uses different key via multi_key_manager.get_key() -> fast, no rate limit
```

### Gateway Integration

Multi-AI works in gateway too:

```
User on Telegram: "Debate Python vs Rust"
Bot: Spawns 3 subagents (researcher, coder, reviewer) each with different API keys
     They debate for 2 rounds
     Bot replies with final consensus + individual perspectives
```

### Free and No Paywall

- Multi-Key: No paid load balancer, just JSON file + deque round-robin + failure tracking free
- Multi-AI: No paid orchestration, just multiprocessing + SQLite memory free
- Both work with Ollama local free (no API key) or Groq free tier multiple keys

## Tests

```bash
# Test multi-key
python hermus.py multikey add --provider groq --key test_key_1
python hermus.py multikey list
python hermus.py multikey parallel --provider groq

# Test multi-AI with mock (no API key needed)
python hermus.py multiai debate "Is Python better than Rust?" --model mock/mock --rounds 1
python hermus.py multiai personas
```

## Future Free Improvements

- [ ] More persona presets
- [ ] Judge as separate agent with different model (e.g., researcher=coder=groq, judge=ollama)
- [ ] Streaming multi-AI debate in TUI
- [ ] Vote-based consensus
- [ ] Web UI for multi-AI visualization
