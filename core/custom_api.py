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

    def add_api(self, api_def: Dict) -> Dict:
        """Add custom API - free"""
        apis = self._load()

        # Validate required fields
        required = ["name", "description", "url"]
        for field in required:
            if field not in api_def or not api_def[field]:
                return {"success": False, "error": f"Missing required field: {field}"}

        # Sanitize name
        name = re.sub(r'[^a-zA-Z0-9_]', '_', api_def["name"]).lower()
        api_def["name"] = name
        api_def["id"] = f"custom_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        api_def["created"] = datetime.now().isoformat()

        # Default values
        api_def.setdefault("method", "GET")
        api_def.setdefault("headers", {})
        api_def.setdefault("auth", {})  # {"type": "bearer", "token": "..."} or {"type": "apikey", "key": "X-API-Key", "value": "..."}
        api_def.setdefault("parameters", {})  # JSON schema for params
        api_def.setdefault("enabled", True)

        # Check if name already exists - replace
        apis = [a for a in apis if a["name"] != name]
        apis.append(api_def)
        self._save(apis)

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
        """Convert custom APIs to LLM tool definitions - free"""
        apis = self._load()
        tools = []
        for api in apis:
            if not api.get("enabled", True):
                continue

            # Build JSON schema for parameters from api definition
            # api["parameters"] should be dict like {"city": {"type": "string", "description": "City name"}}
            # If it's already a JSON schema, use it; if it's simple, convert
            params = api.get("parameters", {})
            properties = {}
            required = []

            # Handle different formats
            if isinstance(params, dict):
                # If params is already a schema with type object
                if "properties" in params:
                    properties = params["properties"]
                    required = params.get("required", [])
                else:
                    # Simple dict: key -> description or key -> {type, description}
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

            tool_def = {
                "type": "function",
                "function": {
                    "name": api["name"],
                    "description": f"[CUSTOM API] {api['description']} | URL: {api['url']} | Method: {api.get('method','GET')} | Custom API defined by user, free",
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
        """Execute custom API - free, uses requests"""
        api = self.get_api(name)
        if not api:
            return {"success": False, "error": f"Custom API '{name}' not found"}

        try:
            url = api["url"]
            method = api.get("method", "GET").upper()
            headers = api.get("headers", {}).copy()
            auth = api.get("auth", {})

            # Handle auth - free handling of common types
            if auth.get("type") == "bearer" and auth.get("token"):
                headers["Authorization"] = f"Bearer {auth['token']}"
            elif auth.get("type") == "apikey" and auth.get("key") and auth.get("value"):
                headers[auth["key"]] = auth["value"]
            elif auth.get("type") == "basic" and auth.get("username"):
                # Basic auth handled via requests auth param, not header
                pass

            # Replace URL template variables like {city} with arguments
            # Example URL: https://api.example.com/weather/{city}
            for key, value in arguments.items():
                placeholder = "{" + key + "}"
                if placeholder in url:
                    url = url.replace(placeholder, str(value))

            # Prepare request
            request_kwargs = {
                "headers": headers,
                "timeout": 30
            }

            # Handle auth
            if auth.get("type") == "basic":
                request_kwargs["auth"] = (auth.get("username",""), auth.get("password",""))

            # For GET, params = arguments that are not in URL
            # For POST, json = arguments
            if method == "GET":
                # Only include args not already in URL
                params = {k: v for k, v in arguments.items() if "{"+k+"}" not in api["url"]}
                request_kwargs["params"] = params
                resp = requests.get(url, **request_kwargs)
            elif method == "POST":
                # If content-type json, send as json, else as data
                if headers.get("Content-Type", "").lower().find("json") >= 0 or "application/json" in str(headers).lower():
                    request_kwargs["json"] = arguments
                else:
                    # Try json by default for custom APIs
                    request_kwargs["json"] = arguments
                resp = requests.post(url, **request_kwargs)
            elif method == "PUT":
                request_kwargs["json"] = arguments
                resp = requests.put(url, **request_kwargs)
            elif method == "DELETE":
                request_kwargs["params"] = arguments
                resp = requests.delete(url, **request_kwargs)
            else:
                # Default GET
                request_kwargs["params"] = arguments
                resp = requests.get(url, **request_kwargs)

            # Try parse JSON, fallback to text
            try:
                data = resp.json()
                # Truncate large responses
                data_str = json.dumps(data)[:5000]
                return {
                    "success": True,
                    "api": name,
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
                    "api": name,
                    "status_code": resp.status_code,
                    "url": url,
                    "data": text,
                    "data_str": text
                }

        except Exception as e:
            return {"success": False, "api": name, "error": str(e), "url": api.get("url")}

# Global manager free
custom_api_manager = CustomAPIManager()
