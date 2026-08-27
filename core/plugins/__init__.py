"""Plugin / MCP ecosystem for Hermus (Phase D).

A small, convention-driven plugin registry.  Any Python module discovered in a
``plugins/`` directory (or this ``core/plugins/`` package) may declare ``PLUGIN``
metadata and a ``register(api)`` entry point.  The ``api`` handed to a plugin
lets it add tools, subscribe to computer-event-bus events and publish its own
events, so extensions stay inside the same event-driven architecture as the
rest of Hermus rather than becoming special-cased globs.

Discovery is lazy and every plugin is loaded in an isolated exception guard, so
one broken plugin cannot take down the gateway.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEARCH_DIRS = ("core/plugins", "plugins")


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class PluginError(Exception):
    pass


class PluginAPI:
    """Surface given to a plugin's ``register(api)`` call."""

    def __init__(self, plugin_name: str, registry: "PluginRegistry"):
        self._name = plugin_name
        self._registry = registry

    @property
    def name(self) -> str:
        return self._name

    def register_tool(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str = "",
        params: Optional[Dict[str, Any]] = None,
        required: Optional[List[str]] = None,
    ) -> None:
        self._registry.register_tool(self._name, name, fn, description, params or {}, required or [])

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        self._registry.subscribe(self._name, event_type, handler)

    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        self._registry.publish(self._name, event_type, data)

    def log(self, message: str) -> None:
        self._registry.log(self._name, message)


class PluginRegistry:
    def __init__(self, search_dirs: Optional[List[str]] = None):
        self._lock = None  # lazy; replaced on first use
        self._search_dirs = list(search_dirs or DEFAULT_SEARCH_DIRS)
        self._plugins: Dict[str, Dict[str, Any]] = {}
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._event_handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._logs: List[Dict[str, str]] = []

    def _sync(self):
        if self._lock is None:
            import threading

            self._lock = threading.RLock()
        return self._lock

    # -- discovery ------------------------------------------------------
    def discover_paths(self) -> List[Path]:
        found: List[Path] = []
        for directory in self._search_dirs:
            base = Path(directory).expanduser()
            if not base.is_absolute():
                base = REPO_ROOT / base
            if base.is_dir():
                found.extend(p for p in sorted(base.glob("*.py")) if not p.name.startswith("_"))
        return found

    def load_all(self, reload: bool = False) -> Dict[str, Any]:
        lock = self._sync()
        loaded, failed = [], []
        for path in self.discover_paths():
            name = path.stem
            with lock:
                if name in self._plugins and not reload:
                    loaded.append(name)
                    continue
            try:
                record = self._load_plugin(path, name)
                with lock:
                    self._plugins[name] = record
                loaded.append(name)
            except Exception as exc:  # noqa: BLE001
                failed.append({"name": name, "path": str(path), "error": str(exc)})
        return {"loaded": loaded, "failed": failed}

    def _load_plugin(self, path: Path, name: str) -> Dict[str, Any]:
        module = self._import_module(path, name)
        metadata = dict(getattr(module, "PLUGIN", {}) or {})
        if "name" not in metadata:
            metadata["name"] = name
        api = PluginAPI(name, self)
        register = getattr(module, "register", None)
        if not callable(register):
            raise PluginError(f"plugin '{name}' has no callable register(api)")
        lock = self._sync()
        with lock:
            self._deregister_plugin_locked(name)
        register(api)
        with lock:
            tools = self._tools_for_plugin(name)
        return {
            "name": metadata.get("name", name),
            "module": name,
            "path": str(path),
            "version": metadata.get("version", "0.0.0"),
            "description": metadata.get("description", ""),
            "author": metadata.get("author", ""),
            "tools": tools,
            "loaded_at": _now(),
        }

    def _import_module(self, path: Path, name: str):
        module_name = f"hermus_plugin_{name}"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise PluginError(f"cannot build import spec for '{name}'")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module

    # -- registration ---------------------------------------------------
    def register_tool(self, plugin: str, name: str, fn: Callable[..., Any],
                      description: str = "", params: Optional[Dict[str, Any]] = None,
                      required: Optional[List[str]] = None) -> None:
        with self._sync():
            self._tools[name] = {
                "name": name, "plugin": plugin, "fn": fn,
                "description": description,
                "params": dict(params or {}),
                "required": list(required or []),
            }

    def subscribe(self, plugin: str, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        handler.__plugin__ = plugin
        with self._sync():
            self._event_handlers.setdefault(event_type, []).append(handler)

    def publish(self, plugin: str, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        try:
            from ..computer.events import publish as bus_publish

            bus_publish(event_type, {"plugin": plugin, **(data or {})})
        except Exception:  # noqa: BLE001
            pass

    def log(self, plugin: str, message: str) -> None:
        with self._sync():
            self._logs.append({"plugin": plugin, "message": str(message), "ts": _now()})
            self._logs = self._logs[-500:]

    def _deregister_plugin_locked(self, plugin: str) -> None:
        for key in [k for k, v in self._tools.items() if v.get("plugin") == plugin]:
            self._tools.pop(key, None)
        for event_type in list(self._event_handlers.keys()):
            self._event_handlers[event_type] = [
                h for h in self._event_handlers[event_type]
                if getattr(h, "__plugin__", None) != plugin
            ]

    def _tools_for_plugin(self, plugin: str) -> List[str]:
        return [k for k, v in self._tools.items() if v.get("plugin") == plugin]

    # -- dispatch -------------------------------------------------------
    def invoke_tool(self, name: str, **kwargs: Any) -> Any:
        with self._sync():
            record = self._tools.get(name)
            if record is None:
                raise PluginError(f"no plugin tool named '{name}'")
            fn = record["fn"]
        return fn(**kwargs)

    def dispatch_event(self, event_type: str, event: Dict[str, Any]) -> None:
        with self._sync():
            handlers = list(self._event_handlers.get(event_type, []))
        for handler in handlers:
            try:
                handler(dict(event))
            except Exception:  # noqa: BLE001
                pass

    # -- inspection -----------------------------------------------------
    def list(self) -> List[Dict[str, Any]]:
        with self._sync():
            return [dict(r) for r in self._plugins.values()]

    def tools(self) -> List[Dict[str, Any]]:
        with self._sync():
            return [{k: v for k, v in r.items() if k != "fn"} for r in self._tools.values()]

    def logs(self, limit: int = 50) -> List[Dict[str, str]]:
        with self._sync():
            return list(self._logs)[-max(1, int(limit)):]


plugin_registry = PluginRegistry()
