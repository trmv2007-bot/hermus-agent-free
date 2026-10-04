"""
HERMUS Agent System - Persistent, Communicating AI Agents

This module provides:
- Agent Pool: Manage multiple persistent agents
- Agent Messaging: Agents can communicate with each other
- Multi-Key Support: Distribute across multiple API keys
- Local-First: Prefer local models, fallback to API
- RTX 3050 Optimized: Smart VRAM and resource management
"""

from .agent import Agent, AgentConfig, AgentState
from .collaboration import (
    AgentTeam,
    CollaborativeMission,
    MissionStatus,
    MissionTask,
    TaskStatus,
    create_mission,
    get_all_missions,
    get_mission,
    start_mission,
)
from .messaging import AgentMessage, MessageBus, MessagePriority, MessageType, get_bus
from .orchestrator import AgentOrchestrator, agent_orchestrator
from .pool import AgentCapacityError, AgentPool, PoolConfig, get_pool, init_pool, shutdown_pool
from .specialization import (
    ROLE_DEFINITIONS,
    AgentRole,
    Chair,
    Coder,
    Critic,
    Researcher,
    Synthesizer,
    ToolRunner,
    Verifier,
    create_by_role,
    create_chair,
    create_coder,
    create_critic,
    create_researcher,
    create_synthesizer,
    create_tool_runner,
    create_verifier,
)

__all__ = [
    "Agent",
    "AgentState",
    "AgentConfig",
    "AgentPool",
    "AgentCapacityError",
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
    "AgentOrchestrator",
    "agent_orchestrator",
]
