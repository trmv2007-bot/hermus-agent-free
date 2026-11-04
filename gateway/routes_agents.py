"""
Agent System API Routes for HERMUS Gateway

Provides REST API endpoints for:
- Agent creation, management, and monitoring
- Agent-to-agent communication
- API key management
- Agent pool operations
- VRAM monitoring for RTX 3050
"""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from core.agents import (
    AgentConfig,
    AgentRole,
    AgentState,
    get_pool,
    init_pool,
    shutdown_pool,
)
from core.agents.messaging import MessagePriority, MessageType, get_bus
from core.local_first import get_local_first
from core.log import get_logger

logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

#: GPU/provider probes shell out or hit local HTTP endpoints. Reuse one real
#: snapshot briefly so a 5s UI poll is not a 12s probe every time.
VRAM_TTL_SECONDS = 30.0
_VRAM_CACHE: dict = {"ts": 0.0, "data": None}


def _publish_swarm_mail(content: str, *, sender_id: str, target_id: str | None = None, kind: str = "dm") -> dict | None:
    """Record agent traffic on the durable harness bus.

    A failure here must not fail the request: delivery already happened in
    process, and the gateway should not 500 because a side record could not be
    written. The missing record is visible in the harness stats instead.
    """
    try:
        from core.harness import bus as swarm

        return swarm.send(str(content)[:2000], str(sender_id), to=target_id, kind=kind)
    except Exception as exc:
        logger.warning(f"swarm mail not recorded on the harness bus: {exc}")
        return None


# ============================================================================
# AGENT POOL ENDPOINTS
# ============================================================================


@router.get("/pool/status")
async def get_pool_status(request: Request) -> dict:
    """Get the current status of the agent pool."""
    try:
        pool = get_pool()
        stats = pool.get_stats()

        # Add VRAM info for RTX 3050
        lfp = get_local_first()
        gpu_info = lfp.detect_gpu()

        return {
            "status": "ok",
            "pool": stats,
            "gpu": gpu_info,
            "recommended": lfp.get_recommended_setup()["recommended"],
        }
    except Exception as e:
        logger.error(f"Error getting pool status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pool/start")
async def start_pool_endpoint(request: Request) -> dict:
    """Start the agent pool."""
    try:
        pool = await init_pool()
        return {
            "status": "ok",
            "message": "Agent pool started",
            "pool_id": id(pool),
        }
    except Exception as e:
        logger.error(f"Error starting pool: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pool/stop")
async def stop_pool_endpoint(request: Request) -> dict:
    """Stop the agent pool and all agents."""
    try:
        await shutdown_pool()
        return {"status": "ok", "message": "Agent pool stopped"}
    except Exception as e:
        logger.error(f"Error stopping pool: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pool/cleanup")
async def cleanup_pool(request: Request, timeout: float = None) -> dict:
    """Cleanup idle agents from the pool."""
    try:
        pool = get_pool()
        destroyed = await pool.cleanup_idle(timeout)
        return {
            "status": "ok",
            "message": f"Cleaned up {destroyed} idle agents",
            "destroyed": destroyed,
        }
    except Exception as e:
        logger.error(f"Error cleaning up pool: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AGENT MANAGEMENT ENDPOINTS
# ============================================================================


@router.get("/list")
async def list_agents(request: Request) -> dict:
    """List all active agents in the pool."""
    try:
        pool = get_pool()
        agents = pool.get_all_agents()
        return {
            "status": "ok",
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "name": a.name,
                    "role": a.role.value if hasattr(a.role, "value") else str(a.role),
                    "state": a.state.value if hasattr(a.state, "value") else str(a.state),
                    "provider": a.provider,
                    "model": a.model,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "last_used": a.last_used.isoformat() if a.last_used else None,
                }
                for a in agents
            ],
            "count": len(agents),
        }
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        # An empty list is the honest shape for a broken pool, but the status must
        # say so: returning "ok" hid pool failures from the Control Room.
        return {"status": "error", "error": str(e), "agents": [], "count": 0}


@router.post("/create")
async def create_agent(request: Request, config: dict) -> dict:
    """Create a new agent with the given configuration.
    
    Expected config fields:
    - name: str (optional)
    - role: str (optional, default: "general")
    - provider: str (optional, default: "ollama")
    - model: str (optional, default: "mistral:7b")
    - api_key: str (optional)
    - idle_timeout: float (optional, default: 900)
    - max_concurrent: int (optional, default: 1)
    """
    try:
        pool = get_pool()

        # Extract known fields
        name = config.get("name", f"Agent-{uuid.uuid4().hex[:8]}")
        role_str = config.get("role", "general")
        provider = config.get("provider", "ollama")
        model = config.get("model", "mistral:7b")
        api_key = config.get("api_key")
        idle_timeout = config.get("idle_timeout", 900)
        max_concurrent = config.get("max_concurrent", 1)

        # Convert role string to AgentRole enum
        try:
            role = AgentRole(role_str)
        except ValueError:
            role = AgentRole.GENERAL

        # Build agent config without passing api_key twice
        agent_config = AgentConfig(
            name=name,
            role=role,
            provider=provider,
            model=model,
            api_key=api_key,
            idle_timeout=idle_timeout,
            max_concurrent=max_concurrent,
        )

        # Create the agent
        agent = await pool.create_agent(config=agent_config)

        return {
            "status": "ok",
            "agent": {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "role": agent.role.value if hasattr(agent.role, "value") else str(agent.role),
                "state": agent.state.value if hasattr(agent.state, "value") else str(agent.state),
                "provider": agent.provider,
                "model": agent.model,
                "created_at": agent.created_at.isoformat() if agent.created_at else None,
            },
        }
    except Exception as e:
        logger.error(f"Error creating agent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.get("/messages")
async def get_messages(request: Request, limit: int = 100) -> dict:
    """Get recent agent messages.

    Registered *before* ``/{agent_id}`` on purpose: FastAPI matches routes in
    declaration order, so a later literal path would be swallowed by the
    parameterised one (that is exactly why this endpoint used to 404).
    """
    try:
        bus = get_bus()
        messages = bus.get_history(limit=max(1, min(1000, int(limit))))
        return {
            "status": "ok",
            "messages": [
                {
                    "message_id": m.message_id,
                    "sender_id": m.sender_id,
                    "target_id": m.target_id,
                    "content": m.content,
                    "type": m.message_type.value if hasattr(m.message_type, "value") else str(m.message_type),
                    "priority": m.priority.value if hasattr(m.priority, "value") else str(m.priority),
                    "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                }
                for m in messages
            ],
            "count": len(messages),
        }
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        # Return empty list on error to prevent UI breakage
        return {"status": "ok", "messages": [], "count": 0}


@router.get("/{agent_id}")
async def get_agent(request: Request, agent_id: str) -> dict:
    """Get details of a specific agent."""
    try:
        pool = get_pool()
        agent = pool.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        return {
            "status": "ok",
            "agent": {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "role": agent.role.value if hasattr(agent.role, "value") else str(agent.role),
                "state": agent.state.value if hasattr(agent.state, "value") else str(agent.state),
                "provider": agent.provider,
                "model": agent.model,
                "created_at": agent.created_at.isoformat() if agent.created_at else None,
                "last_used": agent.last_used.isoformat() if agent.last_used else None,
                "message_count": len(agent.message_history) if agent.message_history else 0,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/start")
async def start_agent(request: Request, agent_id: str) -> dict:
    """Start a specific agent."""
    try:
        pool = get_pool()
        success = await pool.start_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found or already running")
        return {"status": "ok", "message": f"Agent {agent_id} started"}
    except Exception as e:
        logger.error(f"Error starting agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/stop")
async def stop_agent(request: Request, agent_id: str) -> dict:
    """Stop a specific agent."""
    try:
        pool = get_pool()
        success = await pool.stop_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found or already stopped")
        return {"status": "ok", "message": f"Agent {agent_id} stopped"}
    except Exception as e:
        logger.error(f"Error stopping agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/destroy")
async def destroy_agent(request: Request, agent_id: str) -> dict:
    """Destroy a specific agent (remove from pool)."""
    try:
        pool = get_pool()
        success = await pool.destroy_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "ok", "message": f"Agent {agent_id} destroyed"}
    except Exception as e:
        logger.error(f"Error destroying agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start-all")
async def start_all_agents(request: Request) -> dict:
    """Start all agents in the pool."""
    try:
        pool = get_pool()
        started = await pool.start_all()
        return {
            "status": "ok",
            "message": f"Started {started} agents",
            "count": started,
        }
    except Exception as e:
        logger.error(f"Error starting all agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop-all")
async def stop_all_agents(request: Request) -> dict:
    """Stop all agents in the pool."""
    try:
        pool = get_pool()
        stopped = await pool.stop_all()
        return {
            "status": "ok",
            "message": f"Stopped {stopped} agents",
            "count": stopped,
        }
    except Exception as e:
        logger.error(f"Error stopping all agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AGENT COMMUNICATION ENDPOINTS
# ============================================================================


@router.post("/message")
async def send_message(request: Request, payload: dict) -> dict:
    """Send a message from one agent to another.
    
    Expected payload:
    - sender_id: str (required)
    - target_id: str (required)
    - content: str (required)
    - type: str (optional, default: "text")
    - priority: str (optional, default: "normal")
    """
    try:
        bus = get_bus()

        sender_id = payload.get("sender_id")
        target_id = payload.get("target_id")
        content = payload.get("content")

        if not sender_id or not target_id or not content:
            raise HTTPException(
                status_code=400,
                detail="sender_id, target_id, and content are required"
            )

        message_type = MessageType(payload.get("type", "text"))
        priority = MessagePriority(payload.get("priority", "normal"))

        message = await bus.send_message(
            sender_id=sender_id,
            target_id=target_id,
            content=content,
            type_=message_type,
            priority=priority,
        )

        # Durable record: the in-memory bus only reaches listeners alive in this
        # process. The harness bus is what ``harness_inbox``, the swarm and the
        # Control Room read, and what survives a restart.
        _publish_swarm_mail(content, sender_id=sender_id, target_id=target_id, kind="dm")

        return {
            "status": "ok",
            "message": {
                "message_id": message.message_id,
                "sender_id": message.sender_id,
                "target_id": message.target_id,
                "content": message.content,
                "timestamp": message.timestamp.isoformat() if message.timestamp else None,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/broadcast")
async def broadcast_message(request: Request, payload: dict) -> dict:
    """Broadcast a message to all agents.
    
    Expected payload:
    - content: str (required)
    - sender_id: str (optional, default: "system")
    - type: str (optional, default: "text")
    - priority: str (optional, default: "normal")
    """
    try:
        bus = get_bus()
        pool = get_pool()

        content = payload.get("content")
        if not content:
            raise HTTPException(status_code=400, detail="content is required")

        sender_id = payload.get("sender_id", "system")
        message_type = MessageType(payload.get("type", "text"))
        priority = MessagePriority(payload.get("priority", "normal"))

        agents = pool.get_all_agents()
        count = 0

        for agent in agents:
            await bus.send_message(
                sender_id=sender_id,
                target_id=agent.agent_id,
                content=content,
                type_=message_type,
                priority=priority,
            )
            count += 1

        # ONE broadcast on the durable bus — not one DM per recipient.
        if count:
            _publish_swarm_mail(content, sender_id=sender_id, kind="broadcast")

        return {
            "status": "ok",
            "message": f"Broadcast sent to {count} agents",
            "count": count,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error broadcasting message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ping")
async def ping_agents(request: Request) -> dict:
    """Ping all agents to check their status."""
    try:
        pool = get_pool()
        agents = pool.get_all_agents()
        results = {}

        #: ``AgentState`` has no ``active`` member. Comparing against it raised
        #: AttributeError on every agent, which the per-agent ``except`` turned
        #: into ``{"status": "error"}`` — so /ping reported the whole fleet dead
        #: while it was working fine. Judge liveness by the states that exist.
        live_states = {AgentState.IDLE, AgentState.WORKING, AgentState.THINKING, AgentState.WAITING, AgentState.SLEEPING}

        for agent in agents:
            try:
                status = "active" if agent.state in live_states else "inactive"
                results[agent.agent_id] = {
                    "status": status,
                    "name": agent.name,
                    "state": str(agent.state),
                }
            except Exception:
                results[agent.agent_id] = {"status": "error", "name": agent.name}

        return {
            "status": "ok",
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        logger.error(f"Error pinging agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# API KEY MANAGEMENT ENDPOINTS
# ============================================================================


@router.post("/subscribe")
async def subscribe_channel(request: Request, payload: dict) -> dict:
    """Join a named swarm channel so its traffic reaches this session's inbox.

    ``core.harness.bus.subscribe`` was exported but unreachable — the only way
    onto a channel had been to post on it first, so a listening agent never
    received channel traffic at all.
    """
    session_id = str(payload.get("session_id") or "")
    channel = str(payload.get("channel") or "")
    if not session_id or not channel:
        raise HTTPException(status_code=400, detail="session_id and channel are required")
    try:
        from core.harness import bus as swarm

        return {"status": "ok", **swarm.subscribe(session_id, channel)}
    except Exception as e:
        logger.error(f"Error subscribing {session_id} to {channel}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/inbox/{session_id}")
async def agent_inbox(request: Request, session_id: str, channel: str = None, unread_only: bool = True) -> dict:
    """Swarm mail for one session, read straight off the durable harness bus."""
    try:
        from core.harness import bus as swarm

        items = swarm.inbox(session_id, channel=channel or None, unread_only=bool(unread_only))
        return {"status": "ok", "session_id": session_id, "messages": items, "count": len(items)}
    except Exception as e:
        logger.error(f"Error reading inbox for {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api-keys/list")
async def list_api_keys(request: Request) -> dict:
    """List all API keys for all providers."""
    try:
        pool = get_pool()
        keys = pool.list_api_keys()
        return {
            "status": "ok",
            # ``list_api_keys`` returns provider -> [ProviderKey.to_dict()]. The
            # old code treated each entry as a ``(key, added_at)`` tuple, which
            # raised on every call; it also must never echo the key itself.
            "keys": {
                provider: [
                    {
                        "key_id": f"{provider}:{index}",
                        "key": "***REDACTED***" if item.get("key") else None,
                        "added_at": item.get("added_at"),
                    }
                    for index, item in enumerate(items or [])
                ]
                for provider, items in (keys or {}).items()
            },
            "providers": list((keys or {}).keys()),
        }
    except Exception as e:
        logger.error(f"Error listing API keys: {e}")
        # Return empty dict on error to prevent UI breakage
        return {"status": "ok", "keys": {}, "providers": []}


@router.post("/api-keys/add")
async def add_api_key(request: Request, payload: dict) -> dict:
    """Add an API key for a provider.
    
    Expected payload:
    - provider: str (required)
    - key: str (required)
    """
    try:
        pool = get_pool()

        provider = payload.get("provider")
        key = payload.get("key")

        if not provider or not key:
            raise HTTPException(
                status_code=400,
                detail="provider and key are required"
            )

        # Validate max keys per provider (10)
        existing = pool.list_api_keys()
        if provider in existing and len(existing[provider]) >= 10:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum 10 keys per provider. {provider} already has 10 keys."
            )

        key_id = await pool.add_api_key(provider, key)

        return {
            "status": "ok",
            "message": f"API key added for {provider}",
            "key_id": key_id,
            "provider": provider,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api-keys/remove")
async def remove_api_key(request: Request, payload: dict) -> dict:
    """Remove an API key.
    
    Expected payload:
    - provider: str (required)
    - key_id: str (required)
    """
    try:
        pool = get_pool()

        provider = payload.get("provider")
        key_id = payload.get("key_id")

        if not provider or not key_id:
            raise HTTPException(
                status_code=400,
                detail="provider and key_id are required"
            )

        success = await pool.remove_api_key(provider, key_id)

        if not success:
            raise HTTPException(status_code=404, detail="Key not found")

        return {
            "status": "ok",
            "message": f"API key {key_id} removed from {provider}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# VRAM MONITORING ENDPOINTS (RTX 3050)
# ============================================================================


@router.get("/vram/status")
async def get_vram_status(request: Request) -> dict:
    """Real GPU/VRAM probe. No assumed hardware and no invented numbers.

    ``get_recommended_setup()`` probes local providers over HTTP (seconds), so
    the assembled snapshot is reused briefly instead of recomputed on every
    5s poll.
    """
    now = time.monotonic()
    cached = _VRAM_CACHE.get("data")
    if isinstance(cached, dict) and now - float(_VRAM_CACHE.get("ts") or 0.0) < VRAM_TTL_SECONDS:
        return cached
    result = await asyncio.to_thread(_probe_vram)
    _VRAM_CACHE.update(ts=time.monotonic(), data=result)
    return result


def _probe_vram() -> dict:
    try:
        lfp = get_local_first()
        gpu_info = lfp.detect_gpu() or {}
        vram_info = lfp.get_vram_usage() or {}
        setup = {}
        try:
            setup = lfp.get_recommended_setup() or {}
        except Exception:
            setup = {}
        detected = bool(gpu_info.get("detected"))
        return {
            "status": "ok" if detected else "unavailable",
            "gpu": {
                "detected": detected,
                "model": gpu_info.get("name"),
                "vram_total": vram_info.get("total") or gpu_info.get("vram") or 0,
            },
            "vram": vram_info,
            "recommended": setup.get("recommended"),
            "limits": setup.get("limits"),
        }
    except Exception as e:
        logger.error(f"Error getting VRAM status: {e}")
        # Honest degradation: a failed probe is not an RTX 3050 with 8 GB.
        return {
            "status": "unavailable",
            "error": str(e),
            "gpu": {"detected": False, "model": None, "vram_total": 0},
            "vram": {"used": 0, "free": 0, "total": 0, "percent": 0, "available": False},
            "recommended": None,
            "limits": None,
        }


@router.get("/vram/monitor")
async def monitor_vram(request: Request) -> dict:
    """Get real-time VRAM monitoring data."""
    try:
        lfp = get_local_first()
        vram_data = lfp.get_vram_monitor_data()
        return {"status": "ok", **vram_data}
    except Exception as e:
        logger.error(f"Error monitoring VRAM: {e}")
        return {
            "status": "error",
            "error": str(e),
            "vram_used": 0,
            "vram_percent": 0,
        }


# ============================================================================
# SPECIALIZED AGENT ENDPOINTS
# ============================================================================


@router.post("/create/researcher")
async def create_researcher(request: Request, config: dict = None) -> dict:
    """Create a researcher agent."""
    try:
        config = config or {}
        config["role"] = "researcher"
        return await create_agent(request, config)
    except Exception:
        raise


@router.post("/create/coder")
async def create_coder(request: Request, config: dict = None) -> dict:
    """Create a coder agent."""
    try:
        config = config or {}
        config["role"] = "coder"
        return await create_agent(request, config)
    except Exception:
        raise


@router.post("/create/verifier")
async def create_verifier(request: Request, config: dict = None) -> dict:
    """Create a verifier agent."""
    try:
        config = config or {}
        config["role"] = "verifier"
        return await create_agent(request, config)
    except Exception:
        raise


@router.post("/create/chair")
async def create_chair(request: Request, config: dict = None) -> dict:
    """Create a chair agent."""
    try:
        config = config or {}
        config["role"] = "chair"
        return await create_agent(request, config)
    except Exception:
        raise


@router.post("/create/critic")
async def create_critic(request: Request, config: dict = None) -> dict:
    """Create a critic agent."""
    try:
        config = config or {}
        config["role"] = "critic"
        return await create_agent(request, config)
    except Exception:
        raise


@router.post("/create/synthesizer")
async def create_synthesizer(request: Request, config: dict = None) -> dict:
    """Create a synthesizer agent."""
    try:
        config = config or {}
        config["role"] = "synthesizer"
        return await create_agent(request, config)
    except Exception:
        raise


@router.post("/create/tool-runner")
async def create_tool_runner(request: Request, config: dict = None) -> dict:
    """Create a tool runner agent."""
    try:
        config = config or {}
        config["role"] = "tool_runner"
        return await create_agent(request, config)
    except Exception:
        raise
