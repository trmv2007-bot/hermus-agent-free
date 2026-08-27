"""Management endpoints: API key CRUD, custom APIs, response-time testing,
updater, and the plugin registry."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/keys/list")
async def keys_list():
    """List API keys - redacted preview + health/models metadata for dashboard"""
    try:
        from core.multi_key import multi_key_manager
        from core.custom_api import custom_api_manager
        # Prefer rich redacted listing from multi_key manager
        redacted = multi_key_manager.list_keys(redact=True)
        llm_raw = multi_key_manager.list_keys(redact=False)
        total_llm = sum(len(v) for v in llm_raw.values())

        custom_apis = custom_api_manager.list_apis()
        custom_redacted = []
        for api in custom_apis:
            token = api.get("auth", {}).get("token") or api.get("auth", {}).get("value") or ""
            custom_redacted.append({
                "name": api["name"],
                "description": api.get("description", ""),
                "url": api.get("url", ""),
                "method": api.get("method", "GET"),
                "preview": f"{token[:6]}...{token[-4:]}" if token and len(token) > 10 else ("no-token" if not token else "****"),
                "id": api.get("id", ""),
                "created": api.get("created", ""),
            })

        return {
            "llm_keys": redacted,
            "custom_apis": custom_redacted,
            "total_llm_keys": total_llm,
            "total_custom_apis": len(custom_apis),
            "rates": multi_key_manager.rate_status(),
            "note": "Add any OpenAI-compatible key via dashboard Keys pane or hermus multikey add. Local only.",
        }
    except Exception as e:
        return {"error": str(e)}

@router.post("/keys/add")
async def keys_add(payload: dict):
    """Add ANY AI API key — auto health + model discovery"""
    try:
        from core.multi_key import multi_key_manager
        provider = payload.get("provider", "groq")
        key = payload.get("key") or payload.get("api_key") or payload.get("token")
        name = payload.get("name")
        if not key and provider not in ("ollama", "lmstudio"):
            return JSONResponse({"success": False, "error": "Missing key/api_key/token"}, status_code=400)

        result = multi_key_manager.add_key(
            provider,
            key or "",
            name=name,
            base_url=payload.get("base_url"),
            default_model=payload.get("model") or payload.get("default_model"),
            rpm_limit=payload.get("rpm") or payload.get("rpm_limit"),
            tpm_limit=payload.get("tpm") or payload.get("tpm_limit"),
            auto_discover=payload.get("auto_discover", True),
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@router.post("/keys/remove")
async def keys_remove(payload: dict):
    """Remove API key via Settings"""
    try:
        from core.multi_key import multi_key_manager
        provider = payload.get("provider")
        key = payload.get("key") or payload.get("name")
        if not provider or not key:
            return JSONResponse({"success": False, "error": "Need provider and key/name"}, status_code=400)
        result = multi_key_manager.remove_key(provider, key)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@router.post("/custom-apis/add")
async def custom_apis_add(payload: dict):
    """Add custom API via Settings panel - free"""
    try:
        from core.custom_api import custom_api_manager
        result = custom_api_manager.add_api(payload)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@router.get("/custom-apis/list")
async def custom_apis_list():
    """List custom APIs"""
    try:
        from core.custom_api import custom_api_manager
        apis = custom_api_manager.list_apis()
        return {"custom_apis": apis, "count": len(apis)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/custom-apis/remove")
async def custom_apis_remove(payload: dict):
    """Remove custom API by name or id - for Settings panel add API key in settings"""
    try:
        from core.custom_api import custom_api_manager
        name = payload.get("name")
        api_id = payload.get("id")
        # Try by id first, then name
        if api_id:
            result = custom_api_manager.remove_api(api_id)
            if not result.get("success"):
                # Try by name
                result = custom_api_manager.remove_api(name)
        else:
            result = custom_api_manager.remove_api(name)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/response-times")
async def response_times_list():
    """Get response time history - for Settings panel response test"""
    try:
        from core.response_tester import response_tester
        history = response_tester.get_history(limit=50)
        stats = response_tester.get_stats()
        return {"history": history, "stats": stats}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/response-times/test")
async def response_times_test(payload: dict):
    """Test response time for API key - how much time does API key take to get response from AI model - free"""
    try:
        from core.response_tester import response_tester
        provider = payload.get("provider", "groq")
        api_key = payload.get("api_key") or payload.get("key")
        model = payload.get("model")
        prompt = payload.get("prompt", "Hello, what is Python async?")
        api_name = payload.get("api_name")

        if api_name:
            # Test custom API response time
            test_args = payload.get("test_args") or {}
            if isinstance(test_args, str):
                try:
                    import json
                    test_args = json.loads(test_args)
                except Exception:
                    test_args = {}
            if api_key:
                result = response_tester.test_custom_api_key(api_name, api_key=api_key, test_args=test_args)
            else:
                # Test all keys for same custom API name
                results = response_tester.test_all_keys_for_custom_api(api_name, test_args=test_args)
                return {"results": results, "count": len(results), "api_name": api_name}
        else:
            # Test LLM provider key
            if api_key:
                result = response_tester.test_llm_key(provider, api_key, model=model, prompt=prompt)
                return result
            else:
                # Test all keys for provider
                results = response_tester.test_all_keys_for_provider(provider, prompt=prompt, model=model)
                return {"results": results, "count": len(results), "provider": provider}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/update/check")
async def update_check():
    """Check if update available from GitHub - shows update in dashboard and CLI too - free"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        result = updater.check_for_updates()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/update/pull")
async def update_pull():
    """Update from GitHub via git pull + pip install - like hermes update - free - shows update in dashboard and CLI"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        result = updater.update()
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/update/local")
async def update_local():
    """Get local commit info"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        return updater.get_local_commit()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/update/remote")
async def update_remote():
    """Get remote commit info from GitHub API free"""
    try:
        from core.updater import get_updater_for_current_repo
        updater = get_updater_for_current_repo()
        return updater.get_remote_commit()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/plugins")
async def plugins_list():
    """Discover + list loaded plugins and their registered tools."""
    from core.plugins import plugin_registry

    plugin_registry.load_all()
    return {
        "plugins": plugin_registry.list(),
        "tools": plugin_registry.tools(),
        "logs": plugin_registry.logs(20),
    }


@router.post("/plugins/reload")
async def plugins_reload():
    """Reload all plugins (re-discovers modules and re-runs register())."""
    from core.plugins import plugin_registry

    return {"result": plugin_registry.load_all(reload=True), "tools": plugin_registry.tools()}


@router.post("/plugins/invoke")
async def plugins_invoke(payload: dict = None):
    """Invoke a plugin-registered tool by name with keyword arguments."""
    payload = payload or {}
    from core.plugins import plugin_registry, PluginError

    name = str(payload.get("tool", ""))
    kwargs = dict(payload.get("args") or {})
    try:
        return {"success": True, "tool": name, "result": plugin_registry.invoke_tool(name, **kwargs)}
    except PluginError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


