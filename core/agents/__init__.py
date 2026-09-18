"""
HERMUS Agent System - Persistent, Communicating AI Agents

This module provides:
- Agent Pool: Manage multiple persistent agents
- Agent Messaging: Agents can communicate with each other
- Multi-Key Support: Distribute across multiple API keys
- Local-First: Prefer local models, fallback to API
- RTX 3050 Optimized: Smart VRAM and resource management
"""

from .agent import Agent, AgentState, AgentConfig
from .pool import AgentPool, get_pool, init_pool, shutdown_pool, PoolConfig
from .messaging import AgentMessage, MessageBus, get_bus, MessageType, MessagePriority
from .specialization import AgentRole, Researcher, Coder, Verifier, Chair, Synthesizer, Critic, ToolRunner
from .specialization import ROLE_DEFINITIONS, create_by_role, create_researcher, create_coder
from .specialization import create_verifier, create_chair, create_critic, create_synthesizer, create_tool_runner
from .collaboration import CollaborativeMission, AgentTeam, MissionStatus, TaskStatus, MissionTask
from .collaboration import create_mission, get_mission, get_all_missions, start_mission

__all__ = [
    "Agent",
    "AgentState",
    "AgentConfig",
    "AgentPool",
    "get_pool",
    "init_pool",
    "shutdown_pool",
    "PoolConfig",
    "AgentMessage",
    "MessageBus",
    "get_bus",
    "MessageType",
    "MessagePriority",
    "AgentRole",
    "Researcher",
    "Coder",
    "Verifier",
    "Chair",
    "Synthesizer",
    "Critic",
    "ToolRunner",
    "ROLE_DEFINITIONS",
    "create_by_role",
    "create_researcher",
    "create_coder",
    "create_verifier",
    "create_chair",
    "create_critic",
    "create_synthesizer",
    "create_tool_runner",
    "CollaborativeMission",
    "AgentTeam",
    "MissionStatus",
    "TaskStatus",
    "MissionTask",
    "create_mission",
    "get_mission",
    "get_all_missions",
    "start_mission",
]
