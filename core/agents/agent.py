"""
Agent - Individual AI entity with persistence and communication capabilities.

Each Agent has:
- Unique ID and name
- Provider and model configuration
- State (IDLE, WORKING, THINKING, ERROR)
- Memory (conversation history, context)
- Ability to send/receive messages to other agents
- Resource tracking (tokens used, API calls)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from core.log import get_logger
from core.config import config

logger = get_logger(__name__)


class AgentState(Enum):
    """Agent operational states."""

    SPAWNING = "spawning"
    IDLE = "idle"
    WORKING = "working"
    THINKING = "thinking"
    WAITING = "waiting"
    ERROR = "error"
    SLEEPING = "sleeping"
    DESTROYED = "destroyed"


class AgentRole(Enum):
    """Agent specialization roles."""

    GENERAL = "general"
    RESEARCHER = "researcher"
    CODER = "coder"
    VERIFIER = "verifier"
    CHAIR = "chair"
    CRITIC = "critic"
    SYNTHESIZER = "synthesizer"
    TOOL_RUNNER = "tool_runner"


@dataclass
class AgentConfig:
    """Configuration for creating an agent."""

    name: str = None
    provider: str = "ollama"
    model: str = "mistral:7b"
    role: AgentRole = AgentRole.GENERAL
    api_key: str = None
    base_url: str = None
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 120
    retry_attempts: int = 3

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "role": self.role.value,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "retry_attempts": self.retry_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        return cls(
            name=data.get("name"),
            provider=data.get("provider", "ollama"),
            model=data.get("model", "mistral:7b"),
            role=AgentRole(data.get("role", "general")),
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
            max_tokens=data.get("max_tokens", 4096),
            temperature=data.get("temperature", 0.7),
            timeout=data.get("timeout", 120),
            retry_attempts=data.get("retry_attempts", 3),
        )


@dataclass
class AgentStats:
    """Runtime statistics for an agent."""

    tokens_used: int = 0
    api_calls: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    uptime: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "api_calls": self.api_calls,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "uptime": self.uptime,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat(),
        }


@dataclass
class AgentMemory:
    """Agent memory and context."""

    conversation: list[dict] = field(default_factory=list)
    context: list[dict] = field(default_factory=list)
    knowledge: dict = field(default_factory=dict)
    max_context_length: int = 4000

    def add_message(self, role: str, content: str, metadata: dict = None) -> None:
        """Add a message to conversation history."""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.conversation.append(message)

        # Prune if too long
        if len(self.conversation) > 1000:
            self.conversation = self.conversation[-500:]

    def get_context(self, max_tokens: int = None) -> list[dict]:
        """Get conversation context, respecting token limit."""
        # TODO: Implement token counting
        return self.conversation[-50:]  # Last 50 messages

    def to_dict(self) -> dict:
        return {
            "conversation": self.conversation,
            "context": self.context,
            "knowledge": self.knowledge,
            "max_context_length": self.max_context_length,
        }


class Agent:
    """
    Persistent AI Agent with communication capabilities.

    Features:
    - Persistent state across tasks
    - Send/receive messages to other agents
    - Resource tracking
    - Configurable timeout and cleanup
    """

    # Class-level registry for all agents
    _registry: dict[str, "Agent"] = {}
    _message_queues: dict[str, asyncio.Queue] = {}

    def __init__(self, agent_id: str = None, config: AgentConfig = None, **kwargs):
        self.agent_id = agent_id or str(uuid.uuid4())
        self.config = config or AgentConfig(**kwargs)
        self.config.name = self.config.name or f"Agent-{self.agent_id[:8]}"

        self.state = AgentState.SPAWNING
        self.memory = AgentMemory()
        self.stats = AgentStats()
        self.role = self.config.role

        # Agent relationships
        self.connections: dict[str, "Agent"] = {}  # agent_id -> Agent
        self.team_id: str = None

        # Resource management
        self.last_activity: float = time.time()
        self.last_task_time: float = 0
        self.current_task: str = None

        # Message queue for incoming messages
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running: bool = True
        self._listener_task: asyncio.Task = None

        # Register this agent
        Agent._registry[self.agent_id] = self
        Agent._message_queues[self.agent_id] = self._message_queue

        # Initialize
        self._init_message_listener()
        self.state = AgentState.IDLE

        logger.info(f"🤖 Agent {self.config.name} ({self.agent_id[:8]}) spawned")

    def _init_message_listener(self):
        """Start the message listener task."""

        async def listener():
            while self._running:
                try:
                    message = await self._message_queue.get()
                    await self._process_message(message)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Agent {self.agent_id} message listener error: {e}")

        self._listener_task = asyncio.create_task(listener())

    async def _process_message(self, message: dict) -> None:
        """Process an incoming message."""
        self.stats.messages_received += 1
        self.last_activity = time.time()

        sender_id = message.get("sender_id")
        content = message.get("content", "")
        message_type = message.get("type", "text")

        logger.info(f"💬 Agent {self.config.name} received from {sender_id}: {content[:50]}...")

        # Add to memory
        self.memory.add_message(
            role="user" if sender_id else "system", content=content, metadata={"sender_id": sender_id, "type": message_type}
        )

        # Handle based on type
        if message_type == "task":
            await self._handle_task_message(message)
        elif message_type == "collaborate":
            await self._handle_collaboration(message)
        else:
            # Default: just acknowledge
            await self.reply(sender_id, f"Received: {content[:100]}")

    async def _handle_task_message(self, message: dict) -> None:
        """Handle a task message."""
        self.state = AgentState.WORKING
        self.current_task = message.get("task_id")

        try:
            # TODO: Actually process the task
            # For now, just simulate work
            await asyncio.sleep(0.5)

            self.stats.tasks_completed += 1
            response = f"Task completed: {message.get('content', 'unknown')}"

            if message.get("sender_id"):
                await self.reply(message["sender_id"], response)

        except Exception as e:
            self.stats.tasks_failed += 1
            logger.error(f"Agent {self.agent_id} task failed: {e}")

        finally:
            self.state = AgentState.IDLE
            self.current_task = None

    async def _handle_collaboration(self, message: dict) -> None:
        """Handle a collaboration request."""
        self.state = AgentState.THINKING

        # TODO: Implement actual collaboration logic
        await asyncio.sleep(0.3)

        response = f"Collaborating on: {message.get('content', 'unknown')}"
        if message.get("sender_id"):
            await self.reply(message["sender_id"], response)

        self.state = AgentState.IDLE

    async def send(self, target_id: str, content: str, message_type: str = "text") -> bool:
        """
        Send a message to another agent.

        Args:
            target_id: ID of the target agent
            content: Message content
            message_type: Type of message (text, task, collaborate, etc.)

        Returns:
            True if message was queued successfully
        """
        if target_id not in Agent._registry:
            logger.warning(f"Target agent {target_id} not found")
            return False

        message = {
            "sender_id": self.agent_id,
            "target_id": target_id,
            "content": content,
            "type": message_type,
            "timestamp": time.time(),
        }

        # Add to our sent stats
        self.stats.messages_sent += 1
        self.stats.tokens_used += len(content) // 4  # Rough estimate

        # Queue the message
        await Agent._message_queues[target_id].put(message)

        logger.info(f"📤 Agent {self.config.name} sent to {target_id}: {content[:50]}...")
        return True

    async def reply(self, target_id: str, content: str) -> bool:
        """Send a reply message."""
        return await self.send(target_id, content, "reply")

    async def broadcast(self, content: str, message_type: str = "text") -> int:
        """
        Send a message to all connected agents.

        Returns:
            Number of agents that received the message
        """
        count = 0
        for agent_id, agent in Agent._registry.items():
            if agent.agent_id != self.agent_id:
                if await self.send(agent_id, content, message_type):
                    count += 1
        return count

    def connect(self, other: "Agent") -> None:
        """Establish a connection to another agent."""
        self.connections[other.agent_id] = other
        other.connections[self.agent_id] = self
        logger.info(f"🔗 Agent {self.config.name} connected to {other.config.name}")

    def disconnect(self, other: "Agent") -> None:
        """Remove connection to another agent."""
        if other.agent_id in self.connections:
            del self.connections[other.agent_id]
        if self.agent_id in other.connections:
            del other.connections[self.agent_id]
        logger.info(f"🔓 Agent {self.config.name} disconnected from {other.config.name}")

    async def run_task(self, task: str, task_id: str = None) -> str:
        """
        Run a task.

        Args:
            task: The task to perform
            task_id: Optional task ID

        Returns:
            Result of the task
        """
        self.state = AgentState.WORKING
        self.current_task = task_id or f"task-{time.time()}"
        self.last_task_time = time.time()

        # Add to memory
        self.memory.add_message(role="user", content=task)

        try:
            # TODO: Actually run the task through LLM
            # For now, simulate processing
            await asyncio.sleep(0.5)

            result = f"Completed: {task}"
            self.memory.add_message(role="assistant", content=result)
            self.stats.tasks_completed += 1

            return result

        except Exception as e:
            self.stats.tasks_failed += 1
            error_msg = f"Error: {str(e)}"
            self.memory.add_message(role="assistant", content=error_msg)
            return error_msg

        finally:
            self.state = AgentState.IDLE
            self.current_task = None

    def mark_activity(self) -> None:
        """Mark that this agent has been active."""
        self.last_activity = time.time()

    def is_idle(self, timeout: float = 0) -> bool:
        """Check if agent is idle for specified timeout."""
        return (time.time() - self.last_activity) > timeout

    async def sleep(self) -> None:
        """Put agent to sleep (reduces resource usage)."""
        self.state = AgentState.SLEEPING
        logger.info(f"😴 Agent {self.config.name} sleeping")

    async def wake(self) -> None:
        """Wake agent from sleep."""
        self.state = AgentState.IDLE
        logger.info(f"👀 Agent {self.config.name} woke up")

    def destroy(self) -> None:
        """Destroy this agent."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()

        # Remove from registry
        if self.agent_id in Agent._registry:
            del Agent._registry[self.agent_id]
        if self.agent_id in Agent._message_queues:
            del Agent._message_queues[self.agent_id]

        self.state = AgentState.DESTROYED
        self.stats.uptime = time.time() - self.stats.created_at.timestamp()

        logger.info(f"💀 Agent {self.config.name} ({self.agent_id[:8]}) destroyed")

    def to_dict(self) -> dict:
        """Serialize agent state."""
        return {
            "agent_id": self.agent_id,
            "config": self.config.to_dict(),
            "state": self.state.value,
            "role": self.role.value,
            "stats": self.stats.to_dict(),
            "last_activity": self.last_activity,
            "current_task": self.current_task,
            "team_id": self.team_id,
            "connections": list(self.connections.keys()),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Agent":
        """Deserialize agent from dict."""
        config = AgentConfig.from_dict(data.get("config", {}))
        agent = cls(agent_id=data.get("agent_id"), config=config)

        # Restore state
        agent.state = AgentState(data.get("state", "idle"))
        agent.role = AgentRole(data.get("role", "general"))
        agent.team_id = data.get("team_id")
        agent.current_task = data.get("current_task")

        # Note: Memory and stats would need to be restored separately

        return agent

    @classmethod
    def get_agent(cls, agent_id: str) -> Optional["Agent"]:
        """Get an agent by ID."""
        return cls._registry.get(agent_id)

    @classmethod
    def get_all_agents(cls) -> list["Agent"]:
        """Get all active agents."""
        return list(cls._registry.values())

    @classmethod
    async def broadcast_all(cls, content: str, sender_id: str = None, message_type: str = "text") -> int:
        """Broadcast a message to all agents."""
        count = 0
        for agent_id, agent in cls._registry.items():
            if sender_id and agent.agent_id == sender_id:
                continue
            message = {
                "sender_id": sender_id or "system",
                "target_id": agent_id,
                "content": content,
                "type": message_type,
                "timestamp": time.time(),
            }
            await agent._message_queue.put(message)
            count += 1
        return count

    @classmethod
    def cleanup_idle(cls, timeout: float = 600) -> list[str]:
        """
        Cleanup agents that have been idle for too long.

        Args:
            timeout: Seconds of inactivity before cleanup

        Returns:
            List of destroyed agent IDs
        """
        destroyed = []
        current_time = time.time()

        for agent_id, agent in list(cls._registry.items()):
            if agent.is_idle(timeout) and agent.state != AgentState.WORKING:
                agent.destroy()
                destroyed.append(agent_id)

        return destroyed

    @classmethod
    def get_stats(cls) -> dict:
        """Get statistics for all agents."""
        total = len(cls._registry)
        states = {}
        for agent in cls._registry.values():
            state = agent.state.value
            states[state] = states.get(state, 0) + 1

        return {
            "total_agents": total,
            "states": states,
            "agent_ids": list(cls._registry.keys()),
        }
