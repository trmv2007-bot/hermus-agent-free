"""
Agent Messaging System - Enables communication between agents.

Features:
- Direct agent-to-agent messaging
- Message types (text, task, collaborate, etc.)
- Message history and tracking
- Broadcast to multiple agents
- Message prioritization
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from core.log import get_logger

logger = get_logger(__name__)


class MessageType(Enum):
    """Types of messages agents can send."""

    TEXT = "text"
    TASK = "task"
    COLLABORATE = "collaborate"
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    URGENT = "urgent"
    SYSTEM = "system"


class MessagePriority(Enum):
    """Message priority levels."""

    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass
class AgentMessage:
    """
    A message sent between agents.

    Attributes:
        message_id: Unique identifier
        sender_id: ID of the sending agent
        target_id: ID of the target agent (or None for broadcast)
        content: Message content
        message_type: Type of message
        priority: Message priority
        timestamp: When the message was sent
        metadata: Additional data
        response_to: ID of message this is responding to
    """

    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str = None
    target_id: str = None
    content: str = ""
    message_type: MessageType = MessageType.TEXT
    priority: MessagePriority = MessagePriority.NORMAL
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)
    response_to: str = None

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "target_id": self.target_id,
            "content": self.content,
            "message_type": self.message_type.value,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "response_to": self.response_to,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentMessage:
        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())),
            sender_id=data.get("sender_id"),
            target_id=data.get("target_id"),
            content=data.get("content", ""),
            message_type=MessageType(data.get("message_type", "text")),
            priority=MessagePriority(int(data.get("priority", 2))),
            timestamp=data.get("timestamp", time.time()),
            metadata=data.get("metadata", {}),
            response_to=data.get("response_to"),
        )


class MessageBus:
    """
    Central message bus for agent communication.

    Features:
    - Route messages between agents
    - Track message history
    - Handle message priorities
    - Support for message patterns (pub/sub, request/response)
    """

    def __init__(self):
        self._message_history: list[AgentMessage] = []
        self._pending_responses: dict[str, asyncio.Event] = {}  # message_id -> Event
        self._subscriptions: dict[str, list[str]] = {}  # topic -> [agent_ids]
        self._max_history = 1000
        self._lock = asyncio.Lock()

    def _publish(self, message: AgentMessage, kind: str) -> None:
        """Project agent traffic onto the canonical event bus.

        This inbox is how a message is delivered; it is not a durable record. A
        second bus that only lives in this process means agent conversations
        disappear on restart and cannot be replayed by the workspace or the
        harness, so each one is mirrored onto ``core.events`` in the same shape
        the dashboard bridge already uses.
        """
        try:
            from ..contracts import CommandStatus, EventEnvelope, EventType
            from ..events import get_bus

            get_bus().publish(
                EventEnvelope(
                    type=EventType.STATE_CHANGED.value,
                    command=kind,
                    target=message.target_id or "broadcast",
                    args_redacted={
                        "message_id": message.message_id,
                        "sender_id": message.sender_id,
                        "target_id": message.target_id,
                        "message_type": message.message_type.value,
                        "priority": message.priority.value,
                        # A preview, not the transcript: the event log is read by
                        # surfaces that do not need every payload in full.
                        "preview": str(message.content or "")[:200],
                    },
                    status=CommandStatus.PENDING.value,
                    source="agents.messaging",
                )
            )
        except Exception as exc:  # a mirror failing must never eat the delivery
            logger.warning(f"could not mirror {kind} onto the event bus: {exc}")

    async def send(
        self,
        sender_id: str,
        target_id: str,
        content: str,
        message_type: MessageType = MessageType.TEXT,
        priority: MessagePriority = MessagePriority.NORMAL,
        metadata: dict = None,
        response_to: str = None,
    ) -> AgentMessage:
        """
        Send a message from one agent to another.

        Args:
            sender_id: ID of the sending agent
            target_id: ID of the target agent
            content: Message content
            message_type: Type of message
            priority: Message priority
            metadata: Additional metadata
            response_to: ID of message being responded to

        Returns:
            The created AgentMessage
        """
        message = AgentMessage(
            sender_id=sender_id,
            target_id=target_id,
            content=content,
            message_type=message_type,
            priority=priority,
            metadata=metadata or {},
            response_to=response_to,
        )

        # Store in history
        async with self._lock:
            self._message_history.append(message)
            if len(self._message_history) > self._max_history:
                self._message_history = self._message_history[-self._max_history // 2 :]

        # If this is a response, notify any waiters
        if response_to and response_to in self._pending_responses:
            self._pending_responses[response_to].set()
            del self._pending_responses[response_to]

        logger.debug(f"📮 Message {message.message_id[:8]}: {sender_id} -> {target_id} [{message_type.value}]")
        self._publish(message, "agent.message")

        return message

    async def broadcast(
        self,
        sender_id: str,
        content: str,
        message_type: MessageType = MessageType.BROADCAST,
        priority: MessagePriority = MessagePriority.NORMAL,
        metadata: dict = None,
        exclude: list[str] = None,
    ) -> AgentMessage:
        """
        Broadcast a message to all agents.

        Args:
            sender_id: ID of the sending agent
            content: Message content
            message_type: Type of message
            priority: Message priority
            metadata: Additional metadata
            exclude: Agent IDs to exclude from broadcast

        Returns:
            The created AgentMessage
        """
        message = AgentMessage(
            sender_id=sender_id,
            target_id=None,  # None means broadcast
            content=content,
            message_type=message_type,
            priority=priority,
            metadata=metadata or {},
        )

        # Store in history
        async with self._lock:
            self._message_history.append(message)
            if len(self._message_history) > self._max_history:
                self._message_history = self._message_history[-self._max_history // 2 :]

        logger.info(f"📢 Broadcast from {sender_id}: {content[:50]}...")
        self._publish(message, "agent.broadcast")

        return message

    async def request(
        self, sender_id: str, target_id: str, content: str, timeout: float = 30.0, **kwargs
    ) -> AgentMessage | None:
        """
        Send a request and wait for a response.

        Args:
            sender_id: ID of the sending agent
            target_id: ID of the target agent
            content: Message content
            timeout: Timeout in seconds to wait for response
            **kwargs: Additional message parameters

        Returns:
            The response message, or None if timeout
        """
        # Send the request
        message = await self.send(
            sender_id=sender_id, target_id=target_id, content=content, message_type=MessageType.REQUEST, **kwargs
        )

        # Wait for response
        event = asyncio.Event()
        async with self._lock:
            self._pending_responses[message.message_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            # Find the response
            async with self._lock:
                for msg in reversed(self._message_history):
                    if msg.response_to == message.message_id:
                        return msg
            return None
        except asyncio.TimeoutError:
            async with self._lock:
                if message.message_id in self._pending_responses:
                    del self._pending_responses[message.message_id]
            return None

    async def publish(self, topic: str, message: AgentMessage) -> int:
        """
        Publish a message to a topic (pub/sub pattern).

        Args:
            topic: Topic to publish to
            message: Message to publish

        Returns:
            Number of subscribers that received the message
        """
        async with self._lock:
            subscribers = self._subscriptions.get(topic, [])

        count = 0
        delivered: list[AgentMessage] = []
        for agent_id in subscribers:
            # Create a copy of the message for each subscriber
            msg = AgentMessage(
                message_id=str(uuid.uuid4()),
                sender_id=message.sender_id,
                target_id=agent_id,
                content=message.content,
                message_type=message.message_type,
                priority=message.priority,
                metadata=message.metadata,
            )
            delivered.append(msg)
            self._publish(msg, "agent.topic")
            count += 1

        # The copies have to be recorded to count as delivered. This loop used to
        # build one per subscriber, discard it, and return the subscriber count —
        # a caller could not tell an ignored topic broadcast from a read one.
        if delivered:
            async with self._lock:
                self._message_history.extend(delivered)
                if len(self._message_history) > self._max_history:
                    self._message_history = self._message_history[-self._max_history // 2 :]

        return count

    async def subscribe(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a topic."""
        async with self._lock:
            if topic not in self._subscriptions:
                self._subscriptions[topic] = []
            if agent_id not in self._subscriptions[topic]:
                self._subscriptions[topic].append(agent_id)

        logger.debug(f"📰 Agent {agent_id} subscribed to topic: {topic}")

    async def unsubscribe(self, agent_id: str, topic: str) -> None:
        """Unsubscribe an agent from a topic."""
        async with self._lock:
            if topic in self._subscriptions:
                if agent_id in self._subscriptions[topic]:
                    self._subscriptions[topic].remove(agent_id)

        logger.debug(f"📵 Agent {agent_id} unsubscribed from topic: {topic}")

    def get_history(
        self,
        limit: int = 100,
        agent_id: str = None,
        message_type: MessageType = None,
    ) -> list[AgentMessage]:
        """
        Get message history.

        Args:
            limit: Maximum number of messages to return
            agent_id: Filter by agent ID (sender or target)
            message_type: Filter by message type

        Returns:
            List of AgentMessage objects
        """
        messages = []

        for msg in reversed(self._message_history):
            if len(messages) >= limit:
                break

            if agent_id:
                if msg.sender_id != agent_id and msg.target_id != agent_id:
                    continue

            if message_type and msg.message_type != message_type:
                continue

            messages.append(msg)

        return messages

    async def get_stats(self) -> dict:
        """Get message bus statistics."""
        async with self._lock:
            return {
                "total_messages": len(self._message_history),
                "pending_responses": len(self._pending_responses),
                "subscriptions": {k: len(v) for k, v in self._subscriptions.items()},
            }

    async def clear_history(self) -> int:
        """Clear message history."""
        async with self._lock:
            count = len(self._message_history)
            self._message_history = []
            return count


# Global message bus instance
_bus: MessageBus | None = None


def get_bus() -> MessageBus:
    """Get the global message bus instance."""
    global _bus
    if _bus is None:
        _bus = MessageBus()
    return _bus
