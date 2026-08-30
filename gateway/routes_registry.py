"""Read-mostly registry endpoints: tools, public-API catalog, MCP, embeddings,
providers, eval/counsel status, model fleet, and key health/rates/models."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/tools")
async def tools_list():
    """List registered tools from auto tool registry"""
    from core.tool_registry import tool_registry

    return tool_registry.list_tools()


@router.get("/public-apis/search")
async def public_apis_search(
    query: str = "",
    category: str = "",
    auth: str = "any",
    https_only: bool = True,
    cors: str = "any",
    limit: int = 10,
    refresh: bool = False,
):
    """Discover APIs from the bundled/updatable public-apis catalog."""
    from tools.public_apis import public_api_catalog

    return public_api_catalog.search(
        query=query,
        category=category,
        auth=auth,
        https_only=https_only,
        cors=cors,
        limit=limit,
        refresh=refresh,
    )


@router.get("/public-apis/categories")
async def public_apis_categories():
    from tools.public_apis import public_api_catalog

    return public_api_catalog.categories()


@router.post("/public-apis/refresh")
async def public_apis_refresh():
    from tools.public_apis import public_api_catalog

    return public_api_catalog.refresh()


@router.get("/mcp/servers")
async def mcp_servers():
    from core.mcp_client import mcp_manager

    return {"servers": mcp_manager.list_servers()}


@router.post("/mcp/connect")
async def mcp_connect():
    from core.mcp_client import mcp_manager
    from core.tool_registry import tool_registry

    result = mcp_manager.connect_enabled()
    tool_registry.load(force=True)
    return {**result, "tools": tool_registry.list_tools()}


@router.get("/embeddings/status")
async def embeddings_status():
    from core.embeddings import embedding_store

    return embedding_store.backend_info()


@router.get("/providers")
async def providers_list():
    from core.providers import list_providers

    return {"providers": list_providers()}


@router.get("/providers/available")
async def providers_available():
    """Unified provider view: known vs configured vs usable (incl .env-only).

    This is the resolver the agent/fallback/fleet use, so the dashboard, CLI
    and setup wizard all see the same answer instead of re-implementing
    provider discovery.
    """
    from core.provider_resolver import list_available_providers, diagnose

    return {
        "providers": list_available_providers(),
        "diagnosis": diagnose(),
    }


@router.get("/eval/summary")
async def eval_summary():
    """Eval harness summary (Phase 4) for the dashboard Reasoning pane."""
    try:
        from core.reasoning.eval import eval_harness

        return eval_harness.summary()
    except Exception as e:
        return {"error": str(e)}


@router.get("/counsel/status")
async def counsel_status():
    """Counsel constitution + amendment status (Phase 2/4) for the dashboard."""
    try:
        from core.counsel.meta import meta_counsel

        return meta_counsel.status()
    except Exception as e:
        return {"error": str(e)}


@router.get("/fleet/workers")
async def fleet_workers():
    from core.model_fleet import model_fleet

    return model_fleet.list_workers()


@router.post("/fleet/run")
async def fleet_run(payload: dict):
    from core.model_fleet import model_fleet

    goal = payload.get("goal") or payload.get("prompt") or ""
    if not goal:
        return JSONResponse({"error": "goal required"}, status_code=400)
    strategy = payload.get("strategy", "auto")
    models = payload.get("models")
    if isinstance(models, str):
        models = [m.strip() for m in models.split(",") if m.strip()]
    providers = payload.get("providers")
    if isinstance(providers, str):
        providers = [p.strip() for p in providers.split(",") if p.strip()]
    return model_fleet.auto_distribute(
        goal,
        strategy=strategy,
        models=models,
        providers=providers,
        max_workers=int(payload.get("max_workers") or 4),
    )


@router.get("/keys/health")
async def keys_health(provider: str = None):
    from core.multi_key import multi_key_manager

    return {"results": multi_key_manager.check_all_health(provider)}


@router.get("/keys/rates")
async def keys_rates(provider: str = None):
    from core.multi_key import multi_key_manager

    return multi_key_manager.rate_status(provider)


@router.get("/keys/models")
async def keys_models(provider: str, key: str = None, base_url: str = None):
    from core.multi_key import multi_key_manager

    return multi_key_manager.discover_models(provider, api_key=key, base_url=base_url)


@router.post("/embeddings/ingest")
async def embeddings_ingest(payload: dict):
    from core.embeddings import embedding_store

    path = payload.get("path")
    if not path:
        return JSONResponse({"error": "path required"}, status_code=400)
    return embedding_store.ingest_path(path, source=payload.get("source"))


@router.post("/embeddings/search")
async def embeddings_search(payload: dict):
    from core.embeddings import embedding_store

    query = payload.get("query", "")
    limit = int(payload.get("limit", 5))
    hybrid = payload.get("hybrid", True)
    if hybrid:
        return embedding_store.hybrid_search(query, limit=limit)
    return embedding_store.search(query, limit=limit)

