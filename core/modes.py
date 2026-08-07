"""Modes - Agent Mode, Chat Mode, Multi Agent Mode, Multi Chat Mode - Free as requested"""

from enum import Enum
from typing import List, Dict
from dataclasses import dataclass

class AgentMode(str, Enum):
    """4 modes as user requested"""
    AGENT = "agent"  # Can control everything
    CHAT = "chat"  # Let's u chat
    MULTI_AGENT = "multi-agent"  # Can use multiple keys at once and reach goal no matter how difficult
    MULTI_CHAT = "multi-chat"  # Can get u as accurate and reliable information as possible with working of multiple ai models and api keys

@dataclass
class ModeConfig:
    name: str
    description: str
    tools_allowed: List[str]  # List of tool names or "all" or "none" or categories
    max_tool_calls_per_turn: int
    use_multi_key: bool
    use_multi_ai: bool
    use_custom_api: bool
    use_memory: bool
    use_skills: bool
    system_prompt_addition: str

MODE_CONFIGS = {
    AgentMode.AGENT: ModeConfig(
        name="Agent Mode",
        description="Can control everything - Full access to all tools, can execute commands, file ops, browser, vision, voice, internet eyes, pentest, backends, custom APIs, etc. - The agent that grows with you, controls everything",
        tools_allowed=["all"],  # All 88+ tools
        max_tool_calls_per_turn=10,
        use_multi_key=True,
        use_multi_ai=False,  # Single agent controlling everything
        use_custom_api=True,
        use_memory=True,
        use_skills=True,
        system_prompt_addition="""
You are in AGENT MODE - You can control everything.

You have full access to all 88+ tools:
- File: read/write/edit/search, shell
- Web: web_search DuckDuckGo, web_read Jina, rss_read, youtube_transcript/search, github_read/search, v2ex_hot, bilibili_search, twitter_read, reddit_read, facebook_search, instagram, xiaohongshu, linkedin, xiaoyuzhou
- Browser: navigate, click, type, screenshot, extract, close - Playwright free
- Vision: vision_analyze via LLaVA Ollama free
- Voice: transcribe_audio via faster-whisper free
- Backends: 7 backends local/Docker/SSH/Singularity/Modal/Daytona/Vercel - backend_execute, list_backends
- Pentest: 32+ tools recon, exploitation, vuln KB, scanner OWASP Top10, multi-agent Graph, reporting, viewer, SAST/DAST, CI/CD, bug bounty, DevSecOps, continuous learning, compliance
- Trajectory: batch generation, compression, stats
- Response Time: test API key response time
- Updater: check_update, do_update
- Custom API: Any API user added, up to 10 keys same name round-robin
- Memory: memory_search, memory_add
- Skills: skill_list, skill_use, auto skill creation
- Subagents: subagent_spawn parallel
- Multi-Key: multikey add/list/remove/parallel - 20 per provider, 10 per custom API same name
- Multi-AI: multiai debate/chat - multiple AIs talk (but in Agent Mode you are single agent controlling everything)
- Token Counting: free
- Doctor: doctor_check_all real probing

In Agent Mode you can control everything - file system, shell, browser, vision, voice, internet, pentesting, backends, custom APIs, etc.
Use tools aggressively to complete any task no matter how complex.
"""
    ),
    AgentMode.CHAT: ModeConfig(
        name="Chat Mode",
        description="Let's u chat - Simple conversation, no tools or limited tools, just chat - Fast, low token usage, for casual conversation",
        tools_allowed=["none"],  # No tools, or only memory_search for context
        max_tool_calls_per_turn=0,
        use_multi_key=False,
        use_multi_ai=False,
        use_custom_api=False,
        use_memory=True,  # Still uses memory for context but no tools
        use_skills=False,
        system_prompt_addition="""
You are in CHAT MODE - Let's you chat.

You are a friendly conversational AI, no tools, just chat.
You can still use memory_search to recall prior sessions for context, but no file ops, no shell, no web search, no browser, no pentest.

Use for:
- Casual conversation
- Quick questions
- Brainstorming without tools
- Low token usage, fast response

If user asks to do something requiring tools (write file, search web, etc.), ask them to switch to Agent Mode via /mode agent or --mode agent.
"""
    ),
    AgentMode.MULTI_AGENT: ModeConfig(
        name="Multi Agent Mode",
        description="Can use multiple keys at once and reach the goal given to u no matter how difficult it is - Parallel execution with different API keys, subagents, multi-AI collaboration, no matter how difficult",
        tools_allowed=["all"],
        max_tool_calls_per_turn=15,  # More tool calls allowed
        use_multi_key=True,
        use_multi_ai=True,  # Use subagents and parallel
        use_custom_api=True,
        use_memory=True,
        use_skills=True,
        system_prompt_addition="""
You are in MULTI AGENT MODE - Can use multiple keys at once and reach the goal given to you no matter how difficult it is.

You have:
- Multi-Key: Up to 20 keys per provider (groq, hf, openai, custom) + 10 per custom API same name from different websites, round-robin, failure tracking 5 min cooldown, fallback, parallel execution with different keys = 3x faster
- Subagents: Spawn isolated subagents in parallel via multiprocessing, each with own session, write Python scripts via RPC zero-context-cost
- Multi-AI Collaboration: Multiple AIs with personas researcher/coder/reviewer/writer/planner/debater/optimist/pessimist can talk to each other, chat_round, debate, collaborate_on_task with tools, judge final consensus

Strategy for difficult goals:
1. Break difficult goal into subtasks
2. Spawn subagents in parallel, each subagent uses different API key via multi_key_manager (different Groq keys for parallel = faster)
3. Each subagent works isolated on subtask
4. Main agent merges results
5. If still difficult, spawn multi-AI debate with researcher/coder/reviewer personas using different models (Ollama + Groq + HF) with different API keys
6. Judge gives final consensus

Example: User says "Research best Python async libraries, best Rust async libraries, and best Go concurrency - no matter how difficult"

In Multi Agent Mode you would:
- Spawn 3 subagents in parallel:
  - Subagent 1: Research Python async using groq_key_1
  - Subagent 2: Research Rust async using groq_key_2
  - Subagent 3: Research Go concurrency using groq_key_3
- All 3 run parallel with different keys = 3x faster, complete in ~10 sec vs 30 sec sequential
- Merge reports
- If goal still difficult, spawn multi-AI debate with researcher, coder, reviewer using different models and keys for accurate reliable info

You can use multiple keys at once and reach the goal no matter how difficult it is.

Tools: All 88+ tools, plus parallel execution via multi_key_manager.execute_parallel_with_keys() and subagents.spawn_parallel_subagents()
"""
    ),
    AgentMode.MULTI_CHAT: ModeConfig(
        name="Multi Chat Mode",
        description="Can get u as accurate and reliable information as possible with working of multiple ai models and api keys - Multiple AIs with different models and API keys debate and consensus for accurate reliable info",
        tools_allowed=["web_search", "web_read", "rss_read", "youtube_transcript", "youtube_search", "github_read", "github_search", "twitter_read", "bilibili_search", "reddit_read", "v2ex_hot", "memory_search", "doctor_check_all"],  # Limited to research tools for accurate info, not file write/shell for safety
        max_tool_calls_per_turn=5,
        use_multi_key=True,
        use_multi_ai=True,
        use_custom_api=False,
        use_memory=True,
        use_skills=False,
        system_prompt_addition="""
You are in MULTI CHAT MODE - Can get you as accurate and reliable information as possible with working of multiple AI models and API keys.

You have:
- Multi-AI Collaboration: Multiple AIs with different personas and different models talk to each other for anything
  - Personas: researcher (thorough facts, sources), coder (practical code), reviewer (critical security edge cases), writer, planner, debater (pros/cons), optimist (opportunities), pessimist (devil's advocate risks)
  - Each agent can use different model: Ollama llama3.1:8b local free offline, Groq llama-3.1-70b fast free tier, HF Mistral free
  - Each agent can use different API keys via multi_key_manager: groq_key_1 from siteA, groq_key_2 from siteB, hf_key_1, etc. - round-robin + fallback

Strategy for accurate reliable info:
1. Spawn multi-AI team: researcher, coder, reviewer (or debater, optimist, pessimist for controversial topics)
2. Each AI uses different model and different API key: researcher uses Ollama local, coder uses Groq key1, reviewer uses Groq key2, etc. - parallel, no rate limit, accurate via diverse perspectives
3. They debate for N rounds (default 2-3): Each round each agent talks building on prior discussion
   - Researcher: Provides facts, sources, deep insights
   - Coder: Writes example code, practical implementation
   - Reviewer: Checks errors, security, edge cases, improvements
   - Debater: Argues pros/cons balanced
   - Optimist: Focuses on opportunities positive angles
   - Pessimist: Focuses on risks what could go wrong (devil's advocate)
4. Judge agent (first agent) summarizes final consensus answer with agreements, disagreements, best path forward, trade-offs

Example: User asks "Should we use Python or Rust for async?"

Multi Chat Mode would:
- Spawn 3 agents: researcher (Ollama llama3.1:8b, groq_key_1), coder (Groq llama-3.1-70b key2), reviewer (HF Mistral key)
- Round 1:
  - researcher: Python async easy, GIL issue, ... (via web_search free)
  - coder: Here's Python code example ... (via file_write tools if allowed, but in multi-chat limited to research)
  - reviewer: Coder missed GIL, security issue...
- Round 2: Each builds on prior
- Judge: Final consensus: Use Python for prototyping, Rust for performance critical, trade-offs...

Result: More accurate and reliable than single AI because multiple models and API keys with different knowledge cutoffs and perspectives debate and reach consensus.

Tools allowed: Limited to research tools for accurate info (web_search, web_read, youtube_transcript, github_read, etc.) + memory_search, not file_write/shell for safety in multi-chat mode.
But you can still use multi-key to use multiple API keys at once for parallel research = accurate + reliable + fast.

You can get user as accurate and reliable information as possible with working of multiple AI models and API keys.
"""
    )
}

def get_mode_config(mode: AgentMode) -> ModeConfig:
    return MODE_CONFIGS.get(mode, MODE_CONFIGS[AgentMode.AGENT])

def list_modes() -> Dict[str, Dict]:
    """List all 4 modes as requested"""
    return {
        mode.value: {
            "name": config.name,
            "description": config.description,
            "tools_allowed": config.tools_allowed,
            "max_tool_calls": config.max_tool_calls_per_turn,
            "use_multi_key": config.use_multi_key,
            "use_multi_ai": config.use_multi_ai,
        }
        for mode, config in MODE_CONFIGS.items()
    }
