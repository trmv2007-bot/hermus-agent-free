"""
Collaboration System - Multi-agent teamwork and mission coordination.

Features:
- Agent teams with specialized roles
- Collaborative missions
- Task decomposition and assignment
- Consensus building
- Voting systems
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Callable

from core.log import get_logger
from .agent import Agent, AgentState, AgentRole
from .pool import get_pool
from .messaging import MessageType, MessagePriority

logger = get_logger(__name__)


class MissionStatus(Enum):
    """Status of a collaborative mission."""
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(Enum):
    """Status of an individual task within a mission."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class MissionTask:
    """A task within a collaborative mission."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    role: AgentRole = AgentRole.GENERAL
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str = None
    dependencies: list[str] = field(default_factory=list)
    result: Any = None
    error: str = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime = None
    completed_at: datetime = None
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "role": self.role.value,
            "status": self.status.value,
            "assigned_agent": self.assigned_agent,
            "dependencies": self.dependencies,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "MissionTask":
        return cls(
            task_id=data.get("task_id", str(uuid.uuid4())),
            description=data.get("description", ""),
            role=AgentRole(data.get("role", "general")),
            status=TaskStatus(data.get("status", "pending")),
            assigned_agent=data.get("assigned_agent"),
            dependencies=data.get("dependencies", []),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class AgentTeam:
    """
    A team of agents working together.
    
    Attributes:
        team_id: Unique team identifier
        name: Team name
        agents: List of agent IDs in the team
        chair: Agent ID of the team chair/coordinator
        created_at: When the team was created
    """
    team_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Team"
    agents: list[str] = field(default_factory=list)
    chair: str = None
    created_at: datetime = field(default_factory=datetime.now)
    
    def add_agent(self, agent_id: str) -> None:
        """Add an agent to the team."""
        if agent_id not in self.agents:
            self.agents.append(agent_id)
    
    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent from the team."""
        if agent_id in self.agents:
            self.agents.remove(agent_id)
            if self.chair == agent_id:
                self.chair = None
    
    def set_chair(self, agent_id: str) -> None:
        """Set the chair/coordinator for the team."""
        if agent_id in self.agents:
            self.chair = agent_id
    
    def to_dict(self) -> dict:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "agents": self.agents,
            "chair": self.chair,
            "created_at": self.created_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentTeam":
        return cls(
            team_id=data.get("team_id", str(uuid.uuid4())),
            name=data.get("name", "Team"),
            agents=data.get("agents", []),
            chair=data.get("chair"),
        )


class CollaborativeMission:
    """
    A collaborative mission involving multiple agents.
    
    Features:
    - Task decomposition
    - Agent assignment
    - Progress tracking
    - Consensus building
    - Result aggregation
    """
    
    def __init__(
        self,
        mission_id: str = None,
        description: str = "",
        team: AgentTeam = None,
    ):
        self.mission_id = mission_id or str(uuid.uuid4())
        self.description = description
        self.team = team or AgentTeam(name=f"Mission-{self.mission_id[:8]}")
        
        self.tasks: dict[str, MissionTask] = {}
        self.status = MissionStatus.PENDING
        self.result: Any = None
        self.error: str = None
        
        self.created_at = datetime.now()
        self.started_at: datetime = None
        self.completed_at: datetime = None
        
        self._lock = asyncio.Lock()
        self._callbacks: list[Callable] = []
        
        logger.info(f"🎯 Mission {self.mission_id[:8]} created: {description}")
    
    async def add_task(
        self,
        description: str,
        role: AgentRole = AgentRole.GENERAL,
        dependencies: list[str] = None,
    ) -> MissionTask:
        """
        Add a task to the mission.
        
        Args:
            description: Task description
            role: Required agent role
            dependencies: Task IDs this task depends on
        
        Returns:
            The created MissionTask
        """
        task = MissionTask(
            description=description,
            role=role,
            dependencies=dependencies or [],
        )
        
        async with self._lock:
            self.tasks[task.task_id] = task
        
        logger.debug(f"✅ Task {task.task_id[:8]} added to mission {self.mission_id[:8]}")
        return task
    
    async def decompose_task(
        self,
        task_description: str,
        max_subtasks: int = 5,
    ) -> list[MissionTask]:
        """
        Decompose a complex task into subtasks.
        
        Args:
            task_description: The complex task to decompose
            max_subtasks: Maximum number of subtasks
        
        Returns:
            List of decomposed tasks
        """
        # For now, create a simple decomposition
        # TODO: Use LLM to intelligently decompose
        
        subtasks = []
        
        # Simple decomposition based on common patterns
        if "research" in task_description.lower():
            subtasks.append(await self.add_task(
                f"Research: {task_description}",
                AgentRole.RESEARCHER,
            ))
            subtasks.append(await self.add_task(
                f"Analyze findings from research",
                AgentRole.RESEARCHER,
            ))
        
        elif "code" in task_description.lower() or "write" in task_description.lower():
            subtasks.append(await self.add_task(
                f"Write code: {task_description}",
                AgentRole.CODER,
            ))
            subtasks.append(await self.add_task(
                f"Test the code",
                AgentRole.CODER,
            ))
        
        elif "verify" in task_description.lower():
            subtasks.append(await self.add_task(
                f"Verify: {task_description}",
                AgentRole.VERIFIER,
            ))
        
        else:
            # Default: single task
            subtasks.append(await self.add_task(task_description))
        
        return subtasks
    
    async def start(self) -> None:
        """Start the mission."""
        async with self._lock:
            if self.status != MissionStatus.PENDING:
                raise Exception(f"Cannot start mission in {self.status.value} state")
            
            self.status = MissionStatus.PLANNING
            self.started_at = datetime.now()
        
        logger.info(f"▶️ Mission {self.mission_id[:8]} started")
        
        # Start planning phase
        await self._plan()
        
        # Move to execution
        async with self._lock:
            self.status = MissionStatus.EXECUTING
        
        # Start executing tasks
        await self._execute_tasks()
    
    async def _plan(self) -> None:
        """Plan the mission tasks."""
        # If no tasks, decompose the description
        if not self.tasks:
            await self.decompose_task(self.description)
        
        logger.info(f"📋 Mission {self.mission_id[:8]} planning complete: {len(self.tasks)} tasks")
    
    async def _execute_tasks(self) -> None:
        """Execute mission tasks."""
        pool = get_pool()
        
        while True:
            # Check for completed tasks
            async with self._lock:
                all_completed = all(
                    t.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]
                    for t in self.tasks.values()
                )
                
                if all_completed:
                    # All tasks done, move to review
                    self.status = MissionStatus.REVIEWING
                    break
            
            # Find pending tasks with no dependencies
            for task_id, task in self.tasks.items():
                if task.status == TaskStatus.PENDING:
                    # Check dependencies
                    deps_met = all(
                        self.tasks[dep].status == TaskStatus.COMPLETED
                        if dep in self.tasks
                        else True
                        for dep in task.dependencies
                    )
                    
                    if deps_met:
                        # Assign and execute
                        async with self._lock:
                            task.status = TaskStatus.ASSIGNED
                        
                        await self._execute_task(task)
            
            # Small delay to prevent busy loop
            await asyncio.sleep(0.1)
        
        # Review results
        await self._review()
    
    async def _execute_task(self, task: MissionTask) -> None:
        """Execute a single task."""
        pool = get_pool()
        
        try:
            # Find an agent with the right role
            agent = await pool.assign_task(task.description, task.role)
            
            if not agent:
                task.status = TaskStatus.BLOCKED
                task.error = "No available agent with required role"
                return
            
            async with self._lock:
                task.assigned_agent = agent.agent_id
                task.status = TaskStatus.IN_PROGRESS
                task.started_at = datetime.now()
            
            logger.info(f"🔄 Task {task.task_id[:8]} assigned to {agent.config.name}")
            
            # Execute the task
            result = await agent.run_task(task.description, task.task_id)
            
            async with self._lock:
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now()
            
            logger.info(f"✅ Task {task.task_id[:8]} completed")
            
            # Notify callbacks
            await self._notify_progress()
            
        except Exception as e:
            async with self._lock:
                task.error = str(e)
                task.status = TaskStatus.FAILED
            
            logger.error(f"❌ Task {task.task_id[:8]} failed: {e}")
            
            # Notify callbacks
            await self._notify_progress()
    
    async def _review(self) -> None:
        """Review mission results."""
        # Check if all tasks completed successfully
        all_success = all(
            t.status == TaskStatus.COMPLETED
            for t in self.tasks.values()
        )
        
        if all_success:
            # Aggregate results
            results = {t.task_id: t.result for t in self.tasks.values()}
            self.result = {"status": "success", "results": results}
            self.status = MissionStatus.COMPLETED
            self.completed_at = datetime.now()
            logger.info(f"🎉 Mission {self.mission_id[:8]} completed successfully")
        else:
            # Some tasks failed
            errors = {t.task_id: t.error for t in self.tasks.values() if t.status == TaskStatus.FAILED}
            self.result = {"status": "partial", "results": {}, "errors": errors}
            self.status = MissionStatus.FAILED
            self.completed_at = datetime.now()
            logger.warning(f"⚠️ Mission {self.mission_id[:8]} completed with errors")
        
        await self._notify_progress()
    
    async def cancel(self) -> None:
        """Cancel the mission."""
        async with self._lock:
            if self.status in [MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED]:
                return
            
            self.status = MissionStatus.CANCELLED
            self.completed_at = datetime.now()
        
        logger.info(f"⏹️ Mission {self.mission_id[:8]} cancelled")
        await self._notify_progress()
    
    def get_progress(self) -> dict:
        """Get mission progress."""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
        pending = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
        in_progress = sum(1 for t in self.tasks.values() if t.status == TaskStatus.IN_PROGRESS)
        
        return {
            "mission_id": self.mission_id,
            "status": self.status.value,
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "in_progress": in_progress,
            "progress_percent": (completed / total * 100) if total > 0 else 0,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    def get_tasks(self) -> list[dict]:
        """Get all mission tasks."""
        return [t.to_dict() for t in self.tasks.values()]
    
    def on_progress(self, callback: Callable) -> None:
        """Register a progress callback."""
        self._callbacks.append(callback)
    
    async def _notify_progress(self) -> None:
        """Notify all progress callbacks."""
        progress = self.get_progress()
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(progress)
                else:
                    callback(progress)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")
    
    def to_dict(self) -> dict:
        """Serialize mission state."""
        return {
            "mission_id": self.mission_id,
            "description": self.description,
            "status": self.status.value,
            "team": self.team.to_dict(),
            "tasks": self.get_tasks(),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CollaborativeMission":
        mission = cls(
            mission_id=data.get("mission_id", str(uuid.uuid4())),
            description=data.get("description", ""),
            team=AgentTeam.from_dict(data.get("team", {})),
        )
        
        mission.status = MissionStatus(data.get("status", "pending"))
        mission.result = data.get("result")
        mission.error = data.get("error")
        
        for task_data in data.get("tasks", []):
            task = MissionTask.from_dict(task_data)
            mission.tasks[task.task_id] = task
        
        return mission


class VotingSystem:
    """
    Voting system for agent consensus building.
    
    Features:
    - Proposal submission
    - Agent voting
    - Consensus calculation
    - Tie-breaking
    """
    
    def __init__(self, agents: list[Agent]):
        self.agents = agents
        self.proposals: dict[str, dict] = {}
        self.votes: dict[str, dict[str, str]] = {}  # proposal_id -> {agent_id: vote}
        self._lock = asyncio.Lock()
    
    async def submit_proposal(self, agent_id: str, proposal: str, options: list[str]) -> str:
        """
        Submit a proposal for voting.
        
        Args:
            agent_id: ID of the proposing agent
            proposal: Description of the proposal
            options: List of voting options
        
        Returns:
            Proposal ID
        """
        proposal_id = str(uuid.uuid4())
        
        async with self._lock:
            self.proposals[proposal_id] = {
                "agent_id": agent_id,
                "proposal": proposal,
                "options": options,
                "submitted_at": datetime.now().isoformat(),
            }
            self.votes[proposal_id] = {}
        
        logger.info(f"📝 Proposal {proposal_id[:8]} submitted by {agent_id}: {proposal[:50]}...")
        return proposal_id
    
    async def vote(self, agent_id: str, proposal_id: str, choice: str) -> bool:
        """
        Cast a vote on a proposal.
        
        Args:
            agent_id: ID of the voting agent
            proposal_id: ID of the proposal
            choice: Voting choice (must be one of the options)
        
        Returns:
            True if vote was recorded
        """
        async with self._lock:
            if proposal_id not in self.proposals:
                return False
            
            proposal = self.proposals[proposal_id]
            if choice not in proposal["options"]:
                return False
            
            # Only one vote per agent
            if agent_id in self.votes[proposal_id]:
                return False
            
            self.votes[proposal_id][agent_id] = choice
        
        logger.info(f"✅ Agent {agent_id} voted on proposal {proposal_id[:8]}: {choice}")
        return True
    
    async def get_results(self, proposal_id: str) -> dict:
        """
        Get voting results for a proposal.
        
        Args:
            proposal_id: ID of the proposal
        
        Returns:
            Voting results with counts and winner
        """
        async with self._lock:
            if proposal_id not in self.proposals:
                return {"error": "Proposal not found"}
            
            proposal = self.proposals[proposal_id]
            votes = self.votes.get(proposal_id, {})
            
            # Count votes per option
            counts = {option: 0 for option in proposal["options"]}
            for choice in votes.values():
                counts[choice] = counts.get(choice, 0) + 1
            
            # Find winner
            winner = max(counts.items(), key=lambda x: x[1])
            
            return {
                "proposal_id": proposal_id,
                "proposal": proposal["proposal"],
                "options": counts,
                "winner": winner[0],
                "winner_votes": winner[1],
                "total_votes": len(votes),
                "quorum": len(votes) / len(self.agents) if self.agents else 0,
            }
    
    async def wait_for_consensus(
        self,
        proposal_id: str,
        quorum: float = 0.5,
        timeout: float = 60.0,
    ) -> dict:
        """
        Wait for consensus on a proposal.
        
        Args:
            proposal_id: ID of the proposal
            quorum: Minimum participation ratio (0-1)
            timeout: Maximum time to wait
        
        Returns:
            Voting results when consensus reached or timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            results = await self.get_results(proposal_id)
            if results.get("error"):
                return results
            
            # Check if quorum reached
            if results["quorum"] >= quorum:
                return results
            
            await asyncio.sleep(1.0)
        
        # Timeout
        return await self.get_results(proposal_id)


# Global mission registry
_missions: dict[str, CollaborativeMission] = {}


def create_mission(description: str, team: AgentTeam = None) -> CollaborativeMission:
    """Create a new collaborative mission."""
    mission = CollaborativeMission(description=description, team=team)
    _missions[mission.mission_id] = mission
    return mission


def get_mission(mission_id: str) -> Optional[CollaborativeMission]:
    """Get a mission by ID."""
    return _missions.get(mission_id)


def get_all_missions() -> list[CollaborativeMission]:
    """Get all active missions."""
    return list(_missions.values())


async def start_mission(description: str, team: AgentTeam = None) -> CollaborativeMission:
    """Create and start a new mission."""
    mission = create_mission(description, team)
    await mission.start()
    return mission
