"""
Agent System API Routes for HERMUS Gateway

Provides REST API endpoints for:
- Agent creation, management, and monitoring
- Agent-to-agent communication
- API key management
- Agent pool operations
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from core.log import get_logger
from core.agents import (
    Agent,
    AgentConfig,
    AgentRole,
    AgentState,
    get_pool,
    init_pool,
    shutdown_pool,
)
from core.agents.pool import PoolConfig
from core.agents.messaging import MessageBus, MessageType, MessagePriority, get_bus
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
            "destroyed_count": len(destroyed),
            "destroyed_ids": destroyed,
        }
    except Exception as e:
        logger.error(f"Error cleaning up pool: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pool/config")
async def get_pool_config(request: Request) -> dict:
    """Get the current pool configuration."""
    try:
        pool = get_pool()
        return {
            "status": "ok",
            "config": {
                "max_agents": pool.config.max_agents,
                "max_concurrent": pool.config.max_concurrent,
                "idle_timeout": pool.config.idle_timeout,
                "cleanup_interval": pool.config.cleanup_interval,
                "max_agents_per_provider": pool.config.max_agents_per_provider,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pool/config")
async def update_pool_config(request: Request, config: dict) -> dict:
    """Update pool configuration."""
    try:
        pool = get_pool()
        
        if "max_agents" in config:
            pool.config.max_agents = config["max_agents"]
        if "max_concurrent" in config:
            pool.config.max_concurrent = config["max_concurrent"]
        if "idle_timeout" in config:
            pool.config.idle_timeout = config["idle_timeout"]
        if "cleanup_interval" in config:
            pool.config.cleanup_interval = config["cleanup_interval"]
        
        return {"status": "ok", "message": "Configuration updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AGENT ENDPOINTS
# ============================================================================

@router.post("/create")
async def create_agent(request: Request, config: dict) -> dict:
    """
    Create a new agent.
    
    Request body:
    {
        "name": "Agent name",
        "provider": "ollama|groq|mistral|...",
        "model": "model name",
        "role": "general|researcher|coder|verifier|chair|critic|synthesizer|tool_runner",
        "api_key": "API key (optional)",
        "base_url": "Base URL (optional)",
        "max_tokens": 4096,
        "temperature": 0.7,
        "timeout": 120,
        "retry_attempts": 3
    }
    """
    try:
        pool = get_pool()
        
        # Convert role string to enum
        role = AgentRole.GENERAL
        if "role" in config:
            try:
                role = AgentRole(config["role"])
            except ValueError:
                logger.warning(f"Unknown role: {config['role']}, using general")
        
        # Create agent
        agent = await pool.create_agent(
            name=config.get("name"),
            role=role,
            provider=config.get("provider", "ollama"),
            model=config.get("model", "mistral:7b"),
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            max_tokens=config.get("max_tokens", 4096),
            temperature=config.get("temperature", 0.7),
            timeout=config.get("timeout", 120),
            retry_attempts=config.get("retry_attempts", 3),
        )
        
        return {
            "status": "ok",
            "agent": agent.to_dict(),
        }
    except Exception as e:
        logger.error(f"Error creating agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_agents(request: Request) -> dict:
    """List all active agents."""
    try:
        pool = get_pool()
        agents = pool.get_all_agents()
        
        return {
            "status": "ok",
            "agents": [agent.to_dict() for agent in agents],
            "count": len(agents),
        }
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}")
async def get_agent(request: Request, agent_id: str) -> dict:
    """Get information about a specific agent."""
    try:
        pool = get_pool()
        agent = pool.get_agent(agent_id)
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        return {"status": "ok", "agent": agent.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/run")
async def run_agent_task(request: Request, agent_id: str, task: dict) -> dict:
    """
    Run a task on a specific agent.
    
    Request body:
    {
        "task": "Task description",
        "task_id": "Optional task ID"
    }
    """
    try:
        pool = get_pool()
        agent = pool.get_agent(agent_id)
        
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        result = await agent.run_task(
            task.get("task", ""),
            task.get("task_id"),
        )
        
        return {
            "status": "ok",
            "agent_id": agent_id,
            "task_id": task.get("task_id"),
            "result": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running agent task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/stop")
async def stop_agent(request: Request, agent_id: str) -> dict:
    """Stop/destroy a specific agent."""
    try:
        pool = get_pool()
        success = await pool.destroy_agent(agent_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        return {"status": "ok", "message": f"Agent {agent_id} stopped"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start-all")
async def start_all_agents(request: Request) -> dict:
    """Start all agents in the pool."""
    try:
        pool = get_pool()
        agents = pool.get_all_agents()
        
        count = 0
        for agent in agents:
            if agent.state == AgentState.SLEEPING:
                await agent.wake()
                count += 1
        
        return {
            "status": "ok",
            "message": f"Started {count} sleeping agents",
            "count": count,
        }
    except Exception as e:
        logger.error(f"Error starting all agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop-all")
async def stop_all_agents(request: Request) -> dict:
    """Stop all agents in the pool."""
    try:
        pool = get_pool()
        count = await pool.destroy_all()
        
        return {
            "status": "ok",
            "message": f"Stopped {count} agents",
            "count": count,
        }
    except Exception as e:
        logger.error(f"Error stopping all agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ping")
async def ping_all_agents(request: Request) -> dict:
    """Ping all agents to check their status."""
    try:
        pool = get_pool()
        agents = pool.get_all_agents()
        
        results = {}
        for agent in agents:
            results[agent.agent_id] = {
                "name": agent.config.name,
                "state": agent.state.value,
                "last_activity": agent.last_activity,
                "is_responsive": True,
            }
        
        return {
            "status": "ok",
            "count": len(agents),
            "agents": results,
        }
    except Exception as e:
        logger.error(f"Error pinging agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AGENT MESSAGING ENDPOINTS
# ============================================================================

@router.post("/message")
async def send_message(request: Request, message: dict) -> dict:
    """
    Send a message from one agent to another.
    
    Request body:
    {
        "sender_id": "Sending agent ID",
        "target_id": "Target agent ID",
        "content": "Message content",
        "type": "text|task|collaborate|request|response|broadcast|urgent|system"
    }
    """
    try:
        pool = get_pool()
        bus = get_bus()
        
        sender = pool.get_agent(message.get("sender_id"))
        if not sender:
            raise HTTPException(status_code=404, detail="Sender agent not found")
        
        target = pool.get_agent(message.get("target_id"))
        if not target:
            raise HTTPException(status_code=404, detail="Target agent not found")
        
        # Convert message type
        msg_type = MessageType.TEXT
        if "type" in message:
            try:
                msg_type = MessageType(message["type"])
            except ValueError:
                logger.warning(f"Unknown message type: {message['type']}")
        
        # Send the message
        success = await sender.send(
            message["target_id"],
            message["content"],
            msg_type.value,
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="Failed to send message")
        
        # Also record in message bus
        await bus.send(
            sender_id=message["sender_id"],
            target_id=message["target_id"],
            content=message["content"],
            message_type=msg_type,
        )
        
        return {
            "status": "ok",
            "message_id": str(time.time()),
            "from": message["sender_id"],
            "to": message["target_id"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/broadcast")
async def broadcast_message(request: Request, message: dict) -> dict:
    """
    Broadcast a message to all agents.
    
    Request body:
    {
        "sender_id": "Sending agent ID (optional)",
        "content": "Message content",
        "type": "text|task|collaborate|request|response|broadcast|urgent|system"
    }
    """
    try:
        pool = get_pool()
        bus = get_bus()
        
        sender_id = message.get("sender_id")
        
        # Convert message type
        msg_type = MessageType.BROADCAST
        if "type" in message:
            try:
                msg_type = MessageType(message["type"])
            except ValueError:
                logger.warning(f"Unknown message type: {message['type']}")
        
        # Broadcast via message bus
        msg = await bus.broadcast(
            sender_id=sender_id or "system",
            content=message["content"],
            message_type=msg_type,
        )
        
        # Count recipients
        count = len(pool.get_all_agents())
        
        return {
            "status": "ok",
            "message_id": msg.message_id,
            "count": count,
        }
    except Exception as e:
        logger.error(f"Error broadcasting message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages")
async def get_messages(request: Request, limit: int = 100) -> dict:
    """Get recent agent messages."""
    try:
        bus = get_bus()
        messages = bus.get_history(limit=limit)
        
        return {
            "status": "ok",
            "messages": [msg.to_dict() for msg in messages],
            "count": len(messages),
        }
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages/{agent_id}")
async def get_agent_messages(request: Request, agent_id: str, limit: int = 100) -> dict:
    """Get messages for a specific agent."""
    try:
        bus = get_bus()
        messages = bus.get_history(limit=limit, agent_id=agent_id)
        
        return {
            "status": "ok",
            "messages": [msg.to_dict() for msg in messages],
            "count": len(messages),
        }
    except Exception as e:
        logger.error(f"Error getting agent messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# API KEY MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/api-keys/add")
async def add_api_key(request: Request, key_data: dict) -> dict:
    """
    Add an API key for a provider.
    
    Request body:
    {
        "provider": "groq|mistral|openrouter|together|fireworks|deepseek|nvidia",
        "key": "API key"
    }
    
    Note: Max 10 keys per provider.
    """
    try:
        pool = get_pool()
        
        provider = key_data.get("provider")
        key = key_data.get("key")
        
        if not provider:
            raise HTTPException(status_code=400, detail="Provider required")
        if not key:
            raise HTTPException(status_code=400, detail="Key required")
        
        # Check limit
        current_keys = pool.get_available_keys(provider)
        if len(current_keys) >= 10:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum 10 keys per provider reached for {provider}"
            )
        
        # Add the key
        success = pool.add_api_key(provider, key)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to add key")
        
        return {
            "status": "ok",
            "message": f"API key added for {provider}",
            "provider": provider,
            "key_count": len(current_keys) + 1,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-keys/list")
async def list_api_keys(request: Request) -> dict:
    """List all configured API keys (redacted)."""
    try:
        pool = get_pool()
        keys = pool.list_api_keys()
        
        return {
            "status": "ok",
            "keys": keys,
        }
    except Exception as e:
        logger.error(f"Error listing API keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api-keys/remove")
async def remove_api_key(request: Request, key_data: dict) -> dict:
    """
    Remove an API key.
    
    Request body:
    {
        "provider": "groq|mistral|...",
        "key": "API key to remove"
    }
    """
    try:
        pool = get_pool()
        
        provider = key_data.get("provider")
        key = key_data.get("key")
        
        if not provider:
            raise HTTPException(status_code=400, detail="Provider required")
        if not key:
            raise HTTPException(status_code=400, detail="Key required")
        
        success = pool.remove_api_key(provider, key)
        
        if not success:
            raise HTTPException(status_code=404, detail="Key not found")
        
        return {
            "status": "ok",
            "message": f"API key removed for {provider}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing API key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api-keys/providers")
async def get_api_providers(request: Request) -> dict:
    """Get list of supported API providers."""
    try:
        from core.local_first import FREE_API_PROVIDERS
        
        providers = []
        for provider_id, info in FREE_API_PROVIDERS.items():
            providers.append({
                "id": provider_id,
                "name": info["name"],
                "default_model": info.get("default_model", ""),
                "rate_limit": info.get("rate_limit", ""),
                "env_key": info.get("env_key", ""),
                "description": info.get("description", ""),
                "free_tier": info.get("free_tier", False),
            })
        
        return {
            "status": "ok",
            "providers": providers,
        }
    except Exception as e:
        logger.error(f"Error getting providers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# LOCAL PROVIDER ENDPOINTS
# ============================================================================

@router.get("/providers/local")
async def get_local_providers(request: Request) -> dict:
    """Get status of local providers (Ollama, NoLlama, etc.)."""
    try:
        lfp = get_local_first()
        local = lfp.detect_local_providers()
        
        return {
            "status": "ok",
            "local_providers": local,
        }
    except Exception as e:
        logger.error(f"Error getting local providers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers/free")
async def get_free_providers(request: Request) -> dict:
    """Get status of free API providers."""
    try:
        lfp = get_local_first()
        free = lfp.detect_free_api_keys()
        
        return {
            "status": "ok",
            "free_api_providers": free,
        }
    except Exception as e:
        logger.error(f"Error getting free providers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers/priority")
async def get_provider_priority(request: Request) -> dict:
    """Get the ordered list of providers based on Local-First principle."""
    try:
        lfp = get_local_first()
        priority = lfp.get_provider_priority_list()
        
        return {
            "status": "ok",
            "priority_list": priority,
        }
    except Exception as e:
        logger.error(f"Error getting provider priority: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/providers/ollama/start")
async def start_ollama(request: Request) -> dict:
    """Attempt to start Ollama if installed but not running."""
    try:
        lfp = get_local_first()
        started = await lfp.auto_start_ollama()
        
        if started:
            return {"status": "ok", "message": "Ollama started successfully"}
        else:
            return {
                "status": "warning",
                "message": "Ollama not installed or failed to start",
                "install_command": "curl -fsSL https://ollama.com/install.sh | sh",
            }
    except Exception as e:
        logger.error(f"Error starting Ollama: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers/recommended")
async def get_recommended_provider(request: Request) -> dict:
    """Get the recommended provider configuration."""
    try:
        config = await detect_and_configure()
        return {"status": "ok", **config}
    except Exception as e:
        logger.error(f"Error getting recommended provider: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# RTX 3050 OPTIMIZATION ENDPOINTS
# ============================================================================

@router.get("/rtx3050/status")
async def get_rtx3050_status(request: Request) -> dict:
    """Get RTX 3050 optimization status and configuration."""
    try:
        from core.local_first import RTX3050_CONFIG, get_rtx3050_config
        
        lfp = get_local_first()
        gpu_info = lfp.detect_gpu()
        
        return {
            "status": "ok",
            "is_rtx3050": gpu_info.get("is_rtx3050", False),
            "gpu_info": gpu_info,
            "config": RTX3050_CONFIG,
            "recommended_models": [
                "llama3.2:3b",
                "phi3:3.8b",
                "mistral:7b",
            ],
        }
    except Exception as e:
        logger.error(f"Error getting RTX 3050 status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vram/monitor")
async def monitor_vram(request: Request) -> dict:
    """Get current VRAM usage (simulated or real if available)."""
    try:
        lfp = get_local_first()
        gpu_info = lfp.detect_gpu()
        
        # Try to get real VRAM usage
        vram_used = None
        vram_total = gpu_info.get("vram", 8)  # Default to 8GB for RTX 3050
        
        try:
            import torch
            if torch.cuda.is_available():
                vram_used = torch.cuda.memory_allocated(0) / (1024 ** 3)  # GB
                vram_total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        except Exception:
            pass
        
        if vram_used is None:
            # Simulate VRAM usage
            pool = get_pool()
            agents = pool.get_all_agents()
            agent_count = len(agents)
            
            # Estimate VRAM based on agent count (rough estimate)
            # Each agent with a model loaded uses ~3-4GB
            vram_used = min(7, agent_count * 3.5)  # Cap at 7GB safe limit
        
        vram_percent = (vram_used / vram_total) * 100 if vram_total > 0 else 0
        
        return {
            "status": "ok",
            "vram_used_gb": round(vram_used, 2) if vram_used else 0,
            "vram_total_gb": round(vram_total, 2),
            "vram_percent": round(vram_percent, 1),
            "safe_limit_gb": 7,
            "is_over_limit": vram_percent > 85,
            "recommendation": "Reduce agents" if vram_percent > 85 else "OK",
        }
    except Exception as e:
        logger.error(f"Error monitoring VRAM: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WEB SOCKET FOR REAL-TIME AGENT UPDATES
# ============================================================================

class AgentWebSocket:
    """WebSocket connection for real-time agent updates."""
    
    def __init__(self):
        self.connections: list[WebSocket] = []
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.connections.append(websocket)
        
        logger.info(f"WebSocket connected: {id(websocket)}")
        
        # Send initial state
        try:
            pool = get_pool()
            agents = pool.get_all_agents()
            await websocket.send_json({
                "type": "initial",
                "agents": [agent.to_dict() for agent in agents],
            })
        except Exception as e:
            logger.error(f"Error sending initial state: {e}")
    
    async def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        async with self._lock:
            if websocket in self.connections:
                self.connections.remove(websocket)
        
        logger.info(f"WebSocket disconnected: {id(websocket)}")
    
    async def broadcast(self, message: dict):
        """Broadcast a message to all connected WebSockets."""
        async with self._lock:
            disconnected = []
            for connection in self.connections:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"WebSocket send error: {e}")
                    disconnected.append(connection)
            
            # Remove disconnected clients
            for connection in disconnected:
                self.connections.remove(connection)


# Global WebSocket manager
agent_ws = AgentWebSocket()


@router.websocket("/ws")
async def agent_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time agent updates."""
    await agent_ws.connect(websocket)
    
    try:
        while True:
            # Just keep the connection open
            # Actual updates are pushed via broadcast
            data = await websocket.receive_text()
            # Could handle commands here if needed
    except WebSocketDisconnect:
        await agent_ws.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await agent_ws.disconnect(websocket)


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@router.get("/stats")
async def get_agent_stats(request: Request) -> dict:
    """Get comprehensive agent system statistics."""
    try:
        pool = get_pool()
        bus = get_bus()
        
        pool_stats = pool.get_stats()
        bus_stats = bus.get_stats()
        
        return {
            "status": "ok",
            "pool": pool_stats,
            "bus": bus_stats,
            "timestamp": time.time(),
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def agent_health_check(request: Request) -> dict:
    """Health check for agent system."""
    try:
        pool = get_pool()
        agents = pool.get_all_agents()
        
        return {
            "status": "ok",
            "agent_count": len(agents),
            "pool_running": True,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


# ============================================================================
# SPECIALIZED AGENT ENDPOINTS
# ============================================================================

@router.post("/specialized/create")
async def create_specialized_agent(request: Request, config: dict) -> dict:
    """
    Create a specialized agent by role.
    
    Request body:
    {
        "role": "researcher|coder|verifier|chair|critic|synthesizer|tool_runner",
        "name": "Agent name",
        "provider": "ollama|groq|...",
        "model": "model name"
    }
    """
    try:
        from core.agents.specialization import create_by_role
        
        role = config.get("role", "general")
        
        # Convert role string to enum
        try:
            role_enum = AgentRole(role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown role: {role}")
        
        agent = await create_by_role(
            role=role_enum,
            name=config.get("name"),
            provider=config.get("provider", "ollama"),
            model=config.get("model", "mistral:7b"),
        )
        
        # Add to pool
        pool = get_pool()
        pool._agents[agent.agent_id] = agent
        
        return {
            "status": "ok",
            "agent": agent.to_dict(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating specialized agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/roles")
async def get_agent_roles(request: Request) -> dict:
    """Get list of available agent roles."""
    try:
        roles = []
        for role in AgentRole:
            roles.append({
                "value": role.value,
                "name": role.name,
            })
        
        return {
            "status": "ok",
            "roles": roles,
        }
    except Exception as e:
        logger.error(f"Error getting roles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Initialize pool on import
# ============================================================================

# Start the pool when this module is imported
import asyncio

async def _init_agent_system():
    """Initialize the agent system."""
    try:
        await init_pool()
        logger.info("Agent system initialized")
    except Exception as e:
        logger.error(f"Error initializing agent system: {e}")


# Run initialization in background
try:
    loop = asyncio.get_event_loop()
    loop.create_task(_init_agent_system())
except Exception:
    pass


def get_agent_router() -> APIRouter:
    """Get the agent router for mounting in the main app."""
    return router
