"""Modes - Agent Mode, Chat Mode, Multi Agent Mode, Multi Chat Mode, SWE Mode - Free as requested"""

from enum import Enum
from dataclasses import dataclass

class AgentMode(str, Enum):
    """Modes available in Hermus"""
    AGENT = "agent"  # Can control everything
    CHAT = "chat"  # Let's u chat
    MULTI_AGENT = "multi-agent"  # Can use multiple keys at once and reach goal no matter how difficult
    MULTI_CHAT = "multi-chat"  # Can get u as accurate and reliable information as possible with working of multiple ai models and api keys
    SWE = "swe"  # Software Engineer Mode - 8-phase repo-level coding, building, testing, and verified repair lifecycle
    ENGINEER = "engineer"

@dataclass
class ModeConfig:
    name: str
    description: str
    tools_allowed: list[str]  # List of tool names or "all" or "none" or categories
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
        tools_allowed=["all"],
        max_tool_calls_per_turn=10,
        use_multi_key=True,
        use_multi_ai=False,
        use_custom_api=True,
        use_memory=True,
        use_skills=True,
        system_prompt_addition="""
You are in AGENT MODE - You can control everything.

You have full access to all tools:
- File: read/write/edit/search, shell
- Web: web_search DuckDuckGo, web_read Jina, rss_read, youtube_transcript/search, github_read/search
- Browser: navigate, click, type, screenshot, extract, close
- Vision: vision_analyze via LLaVA Ollama free
- Voice: transcribe_audio via faster-whisper free
- Mission & SWE: mission_start, swe_develop, domain_verify, artifact_list, rollback_checkpoint
- Backends, Pentest, Skills, Memory, Subagents

Use tools aggressively to complete any task no matter how complex.
"""
    ),
    AgentMode.SWE: ModeConfig(
        name="Software Engineer Mode",
        description="Repository-level development lifecycle: Inspect → Plan → Edit → Build → Test → Debug/Repair → Review Diff → Package with automated toolchain detection and domain verification.",
        tools_allowed=["all"],
        max_tool_calls_per_turn=15,
        use_multi_key=True,
        use_multi_ai=True,
        use_custom_api=True,
        use_memory=True,
        use_skills=True,
        system_prompt_addition="""
You are in SOFTWARE ENGINEER (SWE) MODE - Dedicated repository-level development lifecycle.

Workflow:
1. INSPECT: Understand directory layout, language/framework toolchain (Python, Node/TypeScript, Rust, Go, Android).
2. PLAN: Formulate precise patch sequence and test strategy.
3. EDIT: Apply small logical patches with AST syntax validation.
4. BUILD: Run build commands in sandbox or workspace.
5. TEST & DEBUG: Run test suite; on failures, capture tracebacks and execute repair iterations.
6. REVIEW DIFF: Review final diff against requirements.
7. VERIFY: Run domain verifiers (Python, Android, Web, etc.).
8. PACKAGE: Produce deliverables and generate change report.

Rule: "Code generated" is NOT equivalent to "task completed" — always verify tests pass and artifacts exist.
"""
    ),
    AgentMode.CHAT: ModeConfig(
        name="Chat Mode",
        description="Let's u chat - Simple conversation, no system tools - just chat and any custom APIs you added. Fast, low token usage",
        tools_allowed=["none"],
        max_tool_calls_per_turn=5,
        use_multi_key=False,
        use_multi_ai=False,
        use_custom_api=True,
        use_memory=True,
        use_skills=False,
        system_prompt_addition="""
You are in CHAT MODE - Friendly conversational AI without system tools.
"""
    ),
    AgentMode.MULTI_AGENT: ModeConfig(
        name="Multi Agent Mode",
        description="Can use multiple keys at once and reach the goal given to u no matter how difficult it is - Parallel execution with different API keys, subagents, multi-AI collaboration",
        tools_allowed=["all"],
        max_tool_calls_per_turn=15,
        use_multi_key=True,
        use_multi_ai=True,
        use_custom_api=True,
        use_memory=True,
        use_skills=True,
        system_prompt_addition="""
You are in MULTI AGENT MODE - Parallel execution with subagents and DAG orchestration.
"""
    ),
    AgentMode.MULTI_CHAT: ModeConfig(
        name="Multi Chat Mode",
        description="Can get u as accurate and reliable information as possible with working of multiple ai models and api keys - Multiple AIs with different models and API keys debate and consensus.",
        tools_allowed=["web_search", "web_read", "rss_read", "youtube_transcript", "youtube_search", "github_read", "github_search", "twitter_read", "bilibili_search", "reddit_read", "v2ex_hot", "memory_search", "doctor_check_all"],
        max_tool_calls_per_turn=5,
        use_multi_key=True,
        use_multi_ai=True,
        use_custom_api=True,
        use_memory=True,
        use_skills=False,
        system_prompt_addition="""
You are in MULTI CHAT MODE - Diverse AI models and perspectives debate to deliver verified consensus answers.
"""
    )
}

# Alias engineer to swe
MODE_CONFIGS[AgentMode.ENGINEER] = MODE_CONFIGS[AgentMode.SWE]

def get_mode_config(mode: AgentMode | str) -> ModeConfig:
    if isinstance(mode, str):
        try:
            mode = AgentMode(mode.lower())
        except ValueError:
            return MODE_CONFIGS[AgentMode.AGENT]
    return MODE_CONFIGS.get(mode, MODE_CONFIGS[AgentMode.AGENT])

def list_modes() -> dict[str, dict]:
    """List all modes"""
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
        if mode != AgentMode.ENGINEER
    }
