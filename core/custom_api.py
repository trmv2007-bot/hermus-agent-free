"""Custom API Feature - Free - Add any API as tool for Hermus Agent"""

import json
import re
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from .config import config

class CustomAPIManager:
    """Manage custom APIs - 100% free, user-defined APIs as tools"""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or config.resolve_path("data/custom_apis.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.db_path.write_text("[]")

    def _load(self) -> List[Dict]:
        try:
            return json.loads(self.db_path.read_text())
        except:
            return []

    def _save(self, apis: List[Dict]):
        self.db_path.write_text(json.dumps(apis, indent=2))

    def list_apis(self) -> List[Dict]:
        return self._load()

    MAX_KEYS_PER_API_NAME = 10  # Increased to 10 as user requested (was 3 example)

    def add_api(self, api_def: Dict) -> Dict:
        """Add custom API - free - now supports up to 10 keys for same API name from different websites as requested"""
        apis = self._load()

        # Validate required fields
        required = ["name", "description", "url"]
        for field in required:
            if field not in api_def or not api_def[field]:
                return {"success": False, "error": f"Missing required field: {field}"}

        # Sanitize name
        name = re.sub(r'[^a-zA-Z0-9_]', '_', api_def["name"]).lower()
        api_def["name"] = name
        api_def["id"] = f"custom_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(apis)}"
        api_def["created"] = datetime.now().isoformat()

        # Default values
        api_def.setdefault("method", "GET")
        api_def.setdefault("headers", {})
        api_def.setdefault("auth", {})  # {"type": "bearer", "token": "..."} or {"type": "apikey", "key": "X-API-Key", "value": "..."}
        api_def.setdefault("parameters", {})  # JSON schema for params
        api_def.setdefault("enabled", True)

        # Check limit for same API name - allow up to 10 keys as requested
        existing_same_name = [a for a in apis if a["name"] == name]
        if len(existing_same_name) >= self.MAX_KEYS_PER_API_NAME:
            return {"success": False, "error": f"Max {self.MAX_KEYS_PER_API_NAME} keys per custom API name reached for '{name}'. You have {len(existing_same_name)} keys from different websites already. Remove old with 'api remove {name}' or 'api remove <id>'."}

        new_token = api_def.get("auth", {}).get("token") or api_def.get("auth", {}).get("value") or ""

        # If exact same name and same token exists, replace it (update)
        apis_filtered = []
        replaced = False
        for a in apis:
            if a["name"] == name:
                existing_token = a.get("auth", {}).get("token") or a.get("auth", {}).get("value") or ""
                if existing_token == new_token and new_token != "":
                    replaced = True
                    continue
                apis_filtered.append(a)
            else:
                apis_filtered.append(a)

        apis = apis_filtered
        apis.append(api_def)
        self._save(apis)

        # Also add to multi-key manager for custom provider for load balancing
        try:
            from .multi_key import multi_key_manager
            if new_token:
                provider_key = f"custom_{name}"
                # Add token to multi-key manager for this custom API
                multi_key_manager.add_key(provider_key, new_token, name=f"{name}_{len([a for a in apis if a['name']==name])}")
        except:
            pass

        if replaced:
            return {"success": True, "api": api_def, "message": f"Custom API '{name}' updated (same token replaced). Now {len([a for a in apis if a['name']==name])} key(s) for this API."}
        else:
            count = len([a for a in apis if a["name"] == name])
            if count > 1:
                return {"success": True, "api": api_def, "message": f"Custom API '{name}' added with NEW key. Now {count} keys for same API - will use round-robin + fallback to complete quickly! Multi-key for custom APIs enabled."}
            else:
                return {"success": True, "api": api_def, "message": f"Custom API '{name}' added. Now available as tool for agent."}

    def remove_api(self, name: str) -> Dict:
        apis = self._load()
        original_len = len(apis)
        apis = [a for a in apis if a["name"] != name and a["id"] != name]
        if len(apis) == original_len:
            return {"success": False, "error": f"API '{name}' not found"}
        self._save(apis)
        return {"success": True, "message": f"Removed custom API '{name}'"}

    def get_api(self, name: str) -> Optional[Dict]:
        apis = self._load()
        for api in apis:
            if api["name"] == name or api["id"] == name:
                return api
        return None

    def get_tool_definitions(self) -> List[Dict]:
        """Convert custom APIs to LLM tool definitions - free - deduplicated by name for multi-key support"""
        apis = self._load()
        # Deduplicate by name - for multi-key support, multiple APIs with same name (different keys) should only appear as 1 tool for LLM
        # Otherwise OpenAI/Groq would error duplicate function names
        seen_names = {}
        unique_apis = []
        for api in apis:
            if not api.get("enabled", True):
                continue
            name = api["name"]
            if name not in seen_names:
                seen_names[name] = api
                unique_apis.append(api)
            else:
                # If same name exists, merge parameters if needed (keep first description but note multi-key)
                # For multi-key, we keep first definition as tool, but execution will round-robin
                pass

        tools = []
        for api in unique_apis:
            params = api.get("parameters", {})
            properties = {}
            required = []

            if isinstance(params, dict):
                if "properties" in params:
                    properties = params["properties"]
                    required = params.get("required", [])
                else:
                    for key, val in params.items():
                        if isinstance(val, dict):
                            properties[key] = val
                            if val.get("required", True):
                                required.append(key)
                        elif isinstance(val, str):
                            properties[key] = {"type": "string", "description": val}
                            required.append(key)
                        else:
                            properties[key] = {"type": "string", "description": str(val)}
                            required.append(key)

            # Count how many keys exist for this API name for multi-key badge
            count_keys = len([a for a in apis if a["name"] == api["name"]])
            multi_key_badge = f" | Multi-key: {count_keys} keys from different websites - round-robin + fallback + completes quickly!" if count_keys > 1 else ""

            tool_def = {
                "type": "function",
                "function": {
                    "name": api["name"],
                    "description": f"[CUSTOM API] {api['description']} | URL: {api['url']} | Method: {api.get('method','GET')}{multi_key_badge} | Custom API defined by user, free",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required
                    }
                }
            }
            tools.append(tool_def)
        return tools

    def execute_api(self, name: str, arguments: Dict) -> Dict:
        """Execute custom API - free, uses requests + multi-key round-robin for same API name with multiple keys"""
        # Check if multiple APIs with same name exist (multi-key for custom APIs)
        all_apis = self._load()
        matching = [a for a in all_apis if a["name"] == name]

        if len(matching) > 1:
            # Multiple keys for same custom API - use multi-key manager round-robin
            try:
                from .multi_key import multi_key_manager
                provider_key = f"custom_{name}"
                # Ensure all tokens are in multi-key manager
                for api in matching:
                    token = api.get("auth", {}).get("token") or api.get("auth", {}).get("value") or ""
                    if token:
                        existing = multi_key_manager.list_keys().get(provider_key, [])
                        existing_tokens = [k if isinstance(k, str) else k.get("key","") for k in existing]
                        if token not in existing_tokens:
                            multi_key_manager.add_key(provider_key, token, name=f"{name}_{len(existing)}")

                # Get next key via round-robin
                chosen_token = multi_key_manager.get_key(provider_key)
                if chosen_token:
                    # Find API that has this token
                    for api in matching:
                        api_token = api.get("auth", {}).get("token") or api.get("auth", {}).get("value") or ""
                        if api_token == chosen_token:
                            # Execute this specific API variant
                            result = self._execute_single_api(api, arguments)
                            # Track success/failure for load balancing
                            if result.get("success") and result.get("status_code", 200) < 400:
                                multi_key_manager.mark_key_success(provider_key, chosen_token)
                            else:
                                multi_key_manager.mark_key_failed(provider_key, chosen_token, result.get("error",""))
                            result["used_key"] = f"{chosen_token[:10]}... (multi-key {len(matching)} keys round-robin)"
                            result["total_keys_for_this_api"] = len(matching)
                            return result
            except Exception as e:
                print(f"[Custom API Multi-Key] Failed to use multi-key, falling back to single: {e}")

        # Single key or fallback - original logic
        api = self.get_api(name)
        if not api:
            return {"success": False, "error": f"Custom API '{name}' not found"}

        return self._execute_single_api(api, arguments)

    def _execute_single_api(self, api: Dict, arguments: Dict) -> Dict:
        """Execute single custom API definition - internal"""
        try:
            url = api["url"]
            method = api.get("method", "GET").upper()
            headers = api.get("headers", {}).copy()
            auth = api.get("auth", {})

            if auth.get("type") == "bearer" and auth.get("token"):
                headers["Authorization"] = f"Bearer {auth['token']}"
            elif auth.get("type") == "apikey" and auth.get("key") and auth.get("value"):
                headers[auth["key"]] = auth["value"]

            for key, value in arguments.items():
                placeholder = "{" + key + "}"
                if placeholder in url:
                    url = url.replace(placeholder, str(value))

            request_kwargs = {"headers": headers, "timeout": 30}
            if auth.get("type") == "basic":
                request_kwargs["auth"] = (auth.get("username",""), auth.get("password",""))

            if method == "GET":
                params = {k: v for k, v in arguments.items() if "{"+k+"}" not in api["url"]}
                request_kwargs["params"] = params
                resp = requests.get(url, **request_kwargs)
            elif method == "POST":
                request_kwargs["json"] = arguments
                resp = requests.post(url, **request_kwargs)
            elif method == "PUT":
                request_kwargs["json"] = arguments
                resp = requests.put(url, **request_kwargs)
            elif method == "DELETE":
                request_kwargs["params"] = arguments
                resp = requests.delete(url, **request_kwargs)
            else:
                request_kwargs["params"] = arguments
                resp = requests.get(url, **request_kwargs)

            try:
                data = resp.json()
                data_str = json.dumps(data)[:5000]
                return {
                    "success": True,
                    "api": api["name"],
                    "api_id": api.get("id",""),
                    "status_code": resp.status_code,
                    "url": url,
                    "data": data,
                    "data_str": data_str,
                    "headers": dict(resp.headers)
                }
            except:
                text = resp.text[:5000]
                return {
                    "success": True,
                    "api": api["name"],
                    "api_id": api.get("id",""),
                    "status_code": resp.status_code,
                    "url": url,
                    "data": text,
                    "data_str": text
                }
        except Exception as e:
            return {"success": False, "api": api.get("name",""), "error": str(e), "url": api.get("url","")}

# Global manager free
custom_api_manager = CustomAPIManager()
