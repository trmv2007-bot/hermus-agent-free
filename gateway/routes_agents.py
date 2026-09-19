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
import json
import time
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from core.log import get_logger
from core.agents import (
    Agent,
    AgentCapacityError,
    AgentConfig,
    AgentRole,
    AgentState,
    get_pool,
    init_pool,
    shutdown_pool,
)
from core.agents.pool import PoolConfig
from core.agents.messaging import MessageBus, MessageType, MessagePriority, get_bus
from core.agents import agent_orchestrator
from core.local_first import get_local_first, detect_and_configure

logger = get_logger(__name__)

# Create router
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ============================================================================
# AGENT POOL ENDPOINTS
# ============================================================================


@router.get("/pool/status")
async def get_pool_status(request: Request) -> dict:
    """Get the current status of the agent pool."""
    try:
        pool = get_pool()
        stats = pool.get_stats()

        # Add live hardware information.
        lfp = get_local_first()
        gpu_info = lfp.detect_gpu()

        return {
            "status": "ok",
            "pool": stats,
            "key_usage": pool.get_key_usage(),
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
                    "key_name": a.config.key_name,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "last_used": a.last_used.isoformat() if a.last_used else None,
                }
                for a in agents
            ],
            "count": len(agents),
        }
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        # Return empty list on error to prevent UI breakage
        return {"status": "ok", "agents": [], "count": 0}


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
     - max_agents_per_key: configured pool limit (default: 2)
    """
    try:
        pool = get_pool()
        
        # Extract known fields
        name = config.get("name", f"Agent-{uuid.uuid4().hex[:8]}")
        role_str = config.get("role", "general")
        provider = config.get("provider", "ollama")
        model = config.get("model", "mistral:7b")
        api_key = config.get("api_key")
        key_name = config.get("key_name")
        base_url = config.get("base_url")
        idle_timeout = config.get("idle_timeout", 900)
        max_concurrent = config.get("max_concurrent", 1)

        if api_key and provider not in {"ollama", "nollama", "lmstudio"}:
            from core.multi_key import multi_key_manager

            stored = multi_key_manager.add_key(
                provider,
                api_key,
                name=key_name,
                base_url=base_url,
                default_model=model,
                auto_discover=False,
            )
            if stored.get("success"):
                key_name = stored.get("key_name")
                base_url = stored.get("base_url") or base_url
            else:
                existing = multi_key_manager.get_entry(provider, api_key)
                if not existing:
                    raise HTTPException(status_code=400, detail=stored.get("error") or "Could not store API key")
                key_name = existing.get("name")
                base_url = existing.get("base_url") or base_url
        
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
            key_name=key_name,
            base_url=base_url,
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
                "key_name": agent.config.key_name,
                "created_at": agent.created_at.isoformat() if agent.created_at else None,
            },
        }
    except HTTPException:
        raise
    except AgentCapacityError as e:
        logger.warning(f"Agent capacity reached: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating agent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.get("/orchestrations")
async def list_orchestrations(limit: int = 30) -> dict:
    """List durable team runs for the dashboard."""
    return {"status": "ok", "orchestrations": agent_orchestrator.list(limit=limit)}


@router.post("/orchestrate")
async def orchestrate_agents(payload: dict) -> dict:
    """Start a persistent-team run without blocking the dashboard request."""
    goal = str(payload.get("goal") or payload.get("task") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal is required")
    raw_ids = payload.get("agent_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    record = agent_orchestrator.create(
        goal,
        agent_ids=list(raw_ids),
        max_agents=int(payload.get("max_agents") or 4),
    )
    asyncio.create_task(agent_orchestrator.run(record["orchestration_id"]))
    return {"status": "accepted", "orchestration": record}


@router.get("/orchestrations/{orchestration_id}")
async def get_orchestration(orchestration_id: str) -> dict:
    record = agent_orchestrator.get(orchestration_id)
    if not record:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return {"status": "ok", "orchestration": record}


@router.post("/{agent_id}/task")
async def run_agent_task(agent_id: str, payload: dict) -> dict:
    """Run a real task on one persistent specialist."""
    pool = get_pool()
    agent = pool.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    task = str(payload.get("task") or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    result = await agent.run_task(task, task_id=f"task_{uuid.uuid4().hex[:10]}")
    return {"status": "ok", "agent_id": agent_id, "response": result}


@router.post("/{agent_id}/start")
async def start_agent(request: Request, agent_id: str) -> dict:
    """Start a specific agent."""
    try:
        pool = get_pool()
        success = await pool.start_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found or already running")
        return {"status": "ok", "message": f"Agent {agent_id} started"}
    except HTTPException:
        raise
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
    except HTTPException:
        raise
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
    except HTTPException:
        raise
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


@router.get("/messages")
async def get_messages(request: Request, limit: int = 100) -> dict:
    """Get recent agent messages."""
    try:
        bus = get_bus()
        messages = bus.get_recent_messages(limit)
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
                    "timestamp": m.timestamp,
                }
                for m in messages
            ],
            "count": len(messages),
        }
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        # Return empty list on error to prevent UI breakage
        return {"status": "ok", "messages": [], "count": 0}


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
        priority = MessagePriority[payload.get("priority", "normal").upper()]
        
        message = await bus.send(
            sender_id=sender_id,
            target_id=target_id,
            content=content,
            message_type=message_type,
            priority=priority,
        )
        
        return {
            "status": "ok",
            "message": {
                "message_id": message.message_id,
                "sender_id": message.sender_id,
                "target_id": message.target_id,
                "content": message.content,
                "timestamp": message.timestamp,
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
        priority = MessagePriority[payload.get("priority", "normal").upper()]
        
        agents = pool.get_all_agents()
        count = 0
        
        for agent in agents:
            await bus.send(
                sender_id=sender_id,
                target_id=agent.agent_id,
                content=content,
                message_type=message_type,
                priority=priority,
            )
            count += 1
        
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
        
        for agent in agents:
            try:
                # Check if agent is responsive
                status = "active" if agent.state not in {AgentState.SLEEPING, AgentState.DESTROYED} else "inactive"
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


@router.get("/api-keys/list")
async def list_api_keys(request: Request) -> dict:
    """List all API keys for all providers."""
    try:
        pool = get_pool()
        keys = pool.list_api_keys()
        return {
            "status": "ok",
            "keys": keys,
            "key_usage": pool.get_key_usage(),
            "providers": list(keys.keys()),
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
        
        from core.multi_key import multi_key_manager

        stored = multi_key_manager.add_key(provider, key, auto_discover=False)
        if not stored.get("success"):
            raise HTTPException(status_code=400, detail=stored.get("error") or "Could not store API key")
        pool.add_api_key(provider, key, name=stored.get("key_name"))
        key_id = stored.get("key_name")
        
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
        
        from core.multi_key import multi_key_manager

        result = multi_key_manager.remove_key(provider, key_id)
        if result.get("success"):
            pool.remove_api_key(provider, key_id)
        success = result.get("success", False)
        
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
    """Get current VRAM usage for RTX 3050."""
    try:
        lfp = get_local_first()
        gpu_info = lfp.detect_gpu()
        
        # Get VRAM usage
        vram_info = lfp.get_vram_usage()
        setup = lfp.get_recommended_setup()
        rtx = setup.get("rtx3050", {})
        
        return {
            "status": "ok",
            "gpu": gpu_info,
            "vram": vram_info,
            "recommended": setup.get("recommended", {}),
            "limits": {
                "vram_safe": rtx.get("vram_safe_limit"),
                "max_models": 1 if rtx else None,
            },
        }
    except Exception as e:
        logger.error(f"Error getting VRAM status: {e}")
        # Return an explicit unavailable response rather than inventing hardware.
        return {
            "status": "unavailable",
            "gpu": {"available": False},
            "vram": {"available": False},
            "recommended": {},
            "limits": {},
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
                "message_count": len(agent.message_history),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
