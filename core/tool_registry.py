"""
Tool Registry - Auto-discover TOOLS + TOOL_MAP from modules.
Replaces giant if/elif chains in agent._execute_tool.
"""
from __future__ import annotations

import importlib
import inspect
import json
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

# Modules that expose TOOLS (list of OpenAI-style defs) and/or TOOL_MAP (name -> callable)
# Also supports TOOL_DEFINITION (single) which gets wrapped.
DISCOVER_MODULES = [
    "tools.web_search",
    "tools.file_tools",
    "tools.shell",
    "tools.browser",
    "tools.vision",
    "tools.voice",
    "tools.internet_eyes",
    "tools.agent_reach_doctor",
    "tools.facebook",
    "tools.updater",
    "tools.self_improvement",
    "tools.pentest",
    "tools.mcp_tools",
    "tools.embeddings_tools",
    "tools.fleet_tools",
    "core.trajectory",
    "core.response_tester",
    "backends.backend_manager",
]


def _normalize_tool_def(item: Any) -> Optional[Dict]:
    """Normalize various tool definition shapes to OpenAI function-calling format."""
    if not isinstance(item, dict):
        return None
    if item.get("type") == "function" and "function" in item:
        return item
    if "name" in item and "parameters" in item:
        return {
            "type": "function",
            "function": {
                "name": item["name"],
                "description": item.get("description", ""),
                "parameters": item.get("parameters", {"type": "object", "properties": {}}),
            },
        }
    if "function" in item and isinstance(item["function"], dict):
        return {"type": "function", "function": item["function"]}
    return None


def _wrap_callable(fn: Callable) -> Callable:
    """Wrap callables so they always accept **kwargs safely and return a dict."""

    def runner(**kwargs):
        try:
            sig = inspect.signature(fn)
            # Filter kwargs to what the function accepts (unless **kwargs present)
            accepts_var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if not accepts_var_kw:
                allowed = {
                    name
                    for name, p in sig.parameters.items()
                    if p.kind
                    in (
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )
                }
                kwargs = {k: v for k, v in kwargs.items() if k in allowed}
            result = fn(**kwargs)
            if isinstance(result, dict):
                return result
            if isinstance(result, list):
                return {"results": result, "count": len(result)}
            return {"result": result}
        except TypeError as e:
            return {"error": f"Bad arguments for tool: {e}", "args": kwargs}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()[-800:]}

    runner.__name__ = getattr(fn, "__name__", "tool")
    return runner


class ToolRegistry:
    """Central registry: definitions for LLM + executors for runtime."""

    def __init__(self):
        self.definitions: List[Dict] = []
        self.executors: Dict[str, Callable] = {}
        self.sources: Dict[str, str] = {}  # tool_name -> module path
        self._loaded = False
        self._errors: List[str] = []

    def clear(self):
        self.definitions = []
        self.executors = {}
        self.sources = {}
        self._loaded = False
        self._errors = []

    def register(
        self,
        name: str,
        executor: Callable,
        definition: Optional[Dict] = None,
        source: str = "manual",
        overwrite: bool = True,
    ):
        if name in self.executors and not overwrite:
            return
        self.executors[name] = _wrap_callable(executor)
        self.sources[name] = source
        if definition:
            norm = _normalize_tool_def(definition)
            if norm:
                # Replace existing def with same name
                self.definitions = [
                    d
                    for d in self.definitions
                    if d.get("function", {}).get("name") != name
                ]
                self.definitions.append(norm)

    def register_builtin_memory_and_skills(self):
        """Register core memory/skill/subagent tools that live in the agent."""
        from core.memory import memory
        from core.skill_manager import skill_manager

        def memory_search(query: str, limit: int = 5, hybrid: bool = True) -> Dict:
            # Prefer hybrid semantic+FTS when available
            if hybrid:
                try:
                    from core.embeddings import embedding_store

                    if embedding_store.available():
                        return embedding_store.hybrid_search(query, limit=limit)
                except Exception:
                    pass
            results = memory.search_sessions(query, limit=limit)
            summary = memory.summarize_search_results(query, results)
            return {"query": query, "results": results, "summary": summary, "mode": "fts5"}

        def memory_add(key: str, value: str, importance: int = 5) -> Dict:
            memory.curate_memory(key, value, importance=importance)
            # Also embed for semantic recall
            try:
                from core.embeddings import embedding_store

                embedding_store.add_text(
                    f"{key}: {value}",
                    metadata={"type": "curated", "key": key},
                    source="curated_memory",
                )
            except Exception:
                pass
            return {"success": True, "key": key}

        def memory_semantic_search(query: str, limit: int = 5) -> Dict:
            from core.embeddings import embedding_store

            return embedding_store.search(query, limit=limit)

        def memory_ingest(path: str, source: str = None) -> Dict:
            from core.embeddings import embedding_store

            return embedding_store.ingest_path(path, source=source)

        def skill_list() -> Dict:
            skills = skill_manager.list_skills()
            return {"skills": skills, "count": len(skills)}

        def skill_use(name: str, task: str = "", query: str = "", **kwargs) -> Dict:
            skill = skill_manager.get_skill(name)
            if not skill:
                return {"error": f"Skill {name} not found"}
            try:
                import importlib.util

                skill_path = Path(skill["path"]) / "skill.py"
                if not skill_path.exists():
                    return skill
                spec = importlib.util.spec_from_file_location(f"skill_{name}", skill_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                result = None
                context = {"task": task or query, "query": query or task, **kwargs}
                if hasattr(mod, "run"):
                    sig = inspect.signature(mod.run)
                    params = list(sig.parameters.keys())
                    call_kwargs = {}
                    # Map common arg names
                    for p in params:
                        if p in context and context[p]:
                            call_kwargs[p] = context[p]
                        elif p in ("query", "task", "text", "input", "prompt") and (
                            task or query
                        ):
                            call_kwargs[p] = task or query
                        elif p == "kwargs" or p.startswith("*"):
                            continue
                    # If run takes no required args, call bare; else pass what we can
                    try:
                        if call_kwargs:
                            result = mod.run(**call_kwargs)
                        else:
                            # Try with task/query positional if signature allows
                            if params:
                                first = params[0]
                                if first not in ("self",):
                                    result = mod.run(task or query or "default")
                                else:
                                    result = mod.run()
                            else:
                                result = mod.run()
                    except TypeError:
                        result = mod.run()
                skill_manager.log_skill_usage(
                    name, success=True, feedback=f"Executed with task={str(task or query)[:80]}"
                )
                return {
                    "skill": name,
                    "result": result if not isinstance(result, str) else result[:3000],
                    "task": task or query,
                }
            except Exception as e:
                skill_manager.log_skill_usage(name, success=False, feedback=str(e))
                return {"error": f"Skill exec failed: {e}", "skill": name}

        def subagent_spawn(task: str) -> Dict:
            from subagents.subagent import spawn_subagent

            return spawn_subagent(task)

        def counsel_convoke(goal: str, execute: bool = True) -> Dict:
            """Convene the Council of AIs: members talk, vote on a plan, then optionally execute it."""
            try:
                from core.counsel.council import CouncilSession

                cs = CouncilSession(goal, execute=execute)
                result = cs.run()
                return {
                    "success": True,
                    "session_id": result.get("session_id"),
                    "members": [m["name"] for m in result.get("members", [])],
                    "votes": result.get("votes"),
                    "plan": result.get("plan"),
                    "replanned": result.get("replanned"),
                    "final_answer": result.get("final_answer"),
                    "transcript_turns": result.get("transcript_turns"),
                }
            except Exception as e:
                return {"success": False, "error": str(e), "goal": goal}

        defs = [
            (
                "memory_search",
                memory_search,
                "Search prior sessions via free FTS5 + optional semantic hybrid search",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                    "hybrid": {"type": "boolean", "default": True},
                },
                ["query"],
            ),
            (
                "memory_add",
                memory_add,
                "Add to curated memory - agent decides what to remember (also embeds for semantic recall)",
                {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "importance": {"type": "integer", "default": 5},
                },
                ["key", "value"],
            ),
            (
                "memory_semantic_search",
                memory_semantic_search,
                "Semantic vector search over embedded memory/docs (Ollama embeddings free local)",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                ["query"],
            ),
            (
                "memory_ingest",
                memory_ingest,
                "Ingest a file or directory into semantic memory (md/txt/py/pdf text chunks + embeddings)",
                {
                    "path": {"type": "string"},
                    "source": {"type": "string"},
                },
                ["path"],
            ),
            (
                "skill_list",
                skill_list,
                "List available auto-created skills",
                {},
                [],
            ),
            (
                "skill_use",
                skill_use,
                "Use a skill by name with optional task/query context",
                {
                    "name": {"type": "string"},
                    "task": {"type": "string", "description": "Task/context for the skill"},
                    "query": {"type": "string", "description": "Alias for task"},
                },
                ["name"],
            ),
            (
                "subagent_spawn",
                subagent_spawn,
                "Spawn isolated subagent for parallel work",
                {"task": {"type": "string"}},
                ["task"],
            ),
            (
                "counsel_convoke",
                counsel_convoke,
                "Convene the Council of AIs: multiple AI members talk, critique, vote on a plan, then execute it (self-upgrading system)",
                {
                    "goal": {"type": "string", "description": "The task/goal for the council"},
                    "execute": {"type": "boolean", "default": True, "description": "Execute the voted plan with tools"},
                },
                ["goal"],
            ),
        ]

        for name, fn, desc, props, required in defs:
            definition = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }
            self.register(name, fn, definition, source="core.builtins")

    def _load_module(self, module_path: str):
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:
            self._errors.append(f"{module_path}: import failed: {e}")
            return

        # Collect TOOL_MAP executors first
        tool_map = getattr(mod, "TOOL_MAP", None) or {}
        if isinstance(tool_map, dict):
            for name, fn in tool_map.items():
                if callable(fn):
                    self.register(name, fn, source=module_path)

        # TOOLS list of definitions
        tools_list = getattr(mod, "TOOLS", None)
        if isinstance(tools_list, list):
            for item in tools_list:
                norm = _normalize_tool_def(item)
                if not norm:
                    continue
                name = norm["function"]["name"]
                # Keep def even if executor missing (will error at runtime with clear msg)
                existing_names = {d.get("function", {}).get("name") for d in self.definitions}
                if name not in existing_names:
                    self.definitions.append(norm)
                # If no executor yet, try to find function on module with same name
                if name not in self.executors:
                    fn = getattr(mod, name, None)
                    if callable(fn):
                        self.register(name, fn, source=module_path)
                    elif name in tool_map and callable(tool_map[name]):
                        self.register(name, tool_map[name], source=module_path)

        # Single TOOL_DEFINITION
        single = getattr(mod, "TOOL_DEFINITION", None)
        if single:
            norm = _normalize_tool_def(single)
            if norm:
                name = norm["function"]["name"]
                existing_names = {d.get("function", {}).get("name") for d in self.definitions}
                if name not in existing_names:
                    self.definitions.append(norm)
                if name not in self.executors:
                    # Common patterns
                    for candidate in (name, "execute", name.replace("_execute", "")):
                        fn = getattr(mod, candidate, None)
                        if callable(fn):
                            self.register(name, fn, source=module_path)
                            break
                    # backends special-case
                    if name == "backend_execute" and hasattr(mod, "backend_execute"):
                        self.register(name, mod.backend_execute, source=module_path)
                    if name == "web_search" and hasattr(mod, "execute"):
                        self.register(name, mod.execute, source=module_path)
                    if name == "shell_execute" and hasattr(mod, "shell_execute"):
                        self.register(name, mod.shell_execute, source=module_path)

        # list_backends helper if present
        if hasattr(mod, "list_backends") and "list_backends" not in self.executors:
            self.register(
                "list_backends",
                mod.list_backends,
                {
                    "type": "function",
                    "function": {
                        "name": "list_backends",
                        "description": "List seven terminal backends with availability",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                },
                source=module_path,
            )

    def load(self, force: bool = False) -> "ToolRegistry":
        if self._loaded and not force:
            return self
        self.clear()
        for mod_path in DISCOVER_MODULES:
            self._load_module(mod_path)
        self.register_builtin_memory_and_skills()
        # Custom APIs as tools
        try:
            from core.custom_api import custom_api_manager

            for tdef in custom_api_manager.get_tool_definitions():
                norm = _normalize_tool_def(tdef)
                if not norm:
                    continue
                name = norm["function"]["name"]
                self.definitions.append(norm)

                def make_exec(api_name: str):
                    def _exec(**kwargs):
                        return custom_api_manager.execute_api(api_name, kwargs)

                    return _exec

                self.register(name, make_exec(name), source="custom_api")
        except Exception as e:
            self._errors.append(f"custom_api: {e}")

        # MCP tools (dynamic, may be empty if no servers configured)
        try:
            from core.mcp_client import mcp_manager

            mcp_defs, mcp_execs = mcp_manager.get_tools_and_executors()
            for tdef in mcp_defs:
                norm = _normalize_tool_def(tdef)
                if norm:
                    name = norm["function"]["name"]
                    existing = {d.get("function", {}).get("name") for d in self.definitions}
                    if name not in existing:
                        self.definitions.append(norm)
            for name, fn in mcp_execs.items():
                self.register(name, fn, source="mcp")
        except Exception as e:
            self._errors.append(f"mcp: {e}")

        self._loaded = True
        return self

    def get_definitions(self, allowed: Optional[Set[str]] = None) -> List[Dict]:
        self.load()
        if allowed is None or "all" in allowed:
            return list(self.definitions)
        if "none" in allowed:
            return []
        out = []
        for d in self.definitions:
            name = d.get("function", {}).get("name", "")
            if name in allowed:
                out.append(d)
        # Always keep memory_search if anything allowed
        if out and not any(d.get("function", {}).get("name") == "memory_search" for d in out):
            for d in self.definitions:
                if d.get("function", {}).get("name") == "memory_search":
                    out.append(d)
                    break
        return out

    def execute(self, name: str, args: Optional[Dict] = None) -> Dict:
        self.load()
        args = args or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"input": args}
        fn = self.executors.get(name)
        if not fn:
            # Last-chance custom API
            try:
                from core.custom_api import custom_api_manager

                names = [a["name"] for a in custom_api_manager.list_apis()]
                if name in names:
                    return custom_api_manager.execute_api(name, args)
            except Exception:
                pass
            return {
                "error": f"Unknown tool {name}",
                "available_sample": list(self.executors.keys())[:20],
                "hint": "Tool not registered. Check tool_registry DISCOVER_MODULES.",
            }
        return fn(**args)

    def list_tools(self) -> Dict:
        self.load()
        return {
            "count": len(self.executors),
            "definitions": len(self.definitions),
            "tools": sorted(self.executors.keys()),
            "sources": dict(self.sources),
            "errors": self._errors,
        }


# Global singleton
tool_registry = ToolRegistry()
