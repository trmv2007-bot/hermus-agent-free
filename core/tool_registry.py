"""Tool Registry - Auto-discover TOOLS + TOOL_MAP from modules.
Replaces giant if/elif chains in agent._execute_tool.

Phase 4: registry-level tool failure fallbacks — if a tool errors, walk its
fallback chain (retry / alternate tool) and attach a fallback_trail to the
result so callers and the lessons loop can see what happened.
"""
from __future__ import annotations

import importlib
import inspect
import json
import traceback
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable
from urllib.parse import quote

# Modules that expose TOOLS (list of OpenAI-style defs) and/or TOOL_MAP (name -> callable)
# Also supports TOOL_DEFINITION (single) which gets wrapped.
DISCOVER_MODULES = [
    "tools.web_search",
    "tools.public_apis",
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
    "tools.harness_tools",
    "tools.mission_tools",
    "core.trajectory",
    "core.response_tester",
    "backends.backend_manager",
]

# Fallback chains (Phase 4, P3): tool -> ordered fallbacks.
# entry: {"retry": True} = call the same tool once more,
#        {"tool": name, "args": dict | callable} = call another tool.
TOOL_FALLBACK_CHAINS: dict[str, list[dict]] = {
    "web_search": [
        {"retry": True},
        {
            "tool": "browser_navigate",
            "args": lambda a: {"url": "https://html.duckduckgo.com/html/?q=" + quote((a.get("query") or "")[:200])},
        },
    ],
    "web_read": [
        {"retry": True},
        {"tool": "browser_navigate", "args": lambda a: {"url": a.get("url") or a.get("link") or ""}},
    ],
    "browser_navigate": [{"retry": True}],
    "file_read": [{"retry": True}],
}


def _normalize_tool_def(item: Any) -> Optional[dict]:
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
        self.definitions: list[dict] = []
        self.executors: dict[str, Callable] = {}
        self.sources: dict[str, str] = {}  # tool_name -> module path
        self._loaded = False
        self._errors: list[str] = []

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
        definition: Optional[dict] = None,
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

        def memory_search(query: str, limit: int = 5, hybrid: bool = True, project: str = "") -> dict:
            # Phase 4 (P4): optional project-scoped recall
            proj = project or None
            # Prefer hybrid semantic+FTS when available
            if hybrid:
                try:
                    from core.embeddings import embedding_store

                    if embedding_store.available():
                        return embedding_store.hybrid_search(query, limit=limit)
                except Exception:
                    pass
            results = memory.search_sessions(query, limit=limit, project=proj)
            summary = memory.summarize_search_results(query, results)
            return {"query": query, "results": results, "summary": summary, "mode": "fts5", "project": proj or "all"}

        def memory_add(key: str, value: str, importance: int = 5) -> dict:
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

        def memory_semantic_search(query: str, limit: int = 5) -> dict:
            from core.embeddings import embedding_store

            return embedding_store.search(query, limit=limit)

        def memory_ingest(path: str, source: str = None) -> dict:
            from core.embeddings import embedding_store

            return embedding_store.ingest_path(path, source=source)

        def skill_list() -> dict:
            skills = skill_manager.list_skills()
            return {"skills": skills, "count": len(skills)}

        def skill_use(name: str, task: str = "", query: str = "", **kwargs) -> dict:
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
                try:
                    from core.reasoning.lessons import lessons_store

                    lessons_store.distill_skill_failure(name, str(e))
                except Exception:
                    pass
                return {"error": f"Skill exec failed: {e}", "skill": name}

        def subagent_spawn(task: str, max_steps: int = 4, timeout: float = 0) -> dict:
            from subagents.subagent import spawn_subagent

            return spawn_subagent(task, max_steps=int(max_steps or 4),
                                  timeout=float(timeout) or None)

        def delegate_tasks(
            goal: str,
            tasks: Optional[list[str]] = None,
            max_children: int = 4,
            aggregate: str = "synthesize",
        ) -> dict:
            """Plan → fan out parallel sub-agents → aggregate structured results."""
            try:
                from core.delegation import delegation

                if tasks:
                    return delegation.fanout(
                        [str(t) for t in tasks], goal=goal, max_children=int(max_children),
                        aggregate=str(aggregate or "synthesize"),
                    )
                return delegation.decompose_and_run(
                    goal, max_children=int(max_children), aggregate=str(aggregate or "synthesize")
                )
            except Exception as e:
                return {"ok": False, "error": str(e)}

        def sandbox_run(command: str, timeout: int = 30, network: bool = False,
                        backend: str = "", allow_dangerous: bool = False) -> dict:
            """Run a command in an ephemeral sandbox (containers when available, else rlimit jail)."""
            try:
                from core.sandbox import sandbox

                return sandbox.run(
                    command, timeout=int(timeout or 30),
                    network=bool(network), backend=(backend or None),
                    allow_dangerous=bool(allow_dangerous), purpose="tool:sandbox_run",
                )
            except Exception as e:
                return {"error": str(e)}

        def memory_hybrid_search(
            query: str, limit: int = 8, kinds: Optional[list[str]] = None,
            project: str = "", explain: bool = False,
        ) -> dict:
            """Hybrid (BM25 + vectors, RRF-fused) recall over typed memory 2.0."""
            try:
                from core.memory import memory

                if explain:
                    return memory.explain(query, limit=int(limit),
                                          project=project or None, kinds=kinds or None)
                hits = memory.hybrid_recall(query, limit=int(limit),
                                            project=project or None, kinds=kinds or None)
                return {
                    "query": query, "mode": "hybrid", "count": len(hits),
                    "index": memory.index_stats(),
                    "results": [
                        {"id": h.get("id"), "kind": h.get("kind"), "score": h.get("score"),
                         "rrf_score": h.get("rrf_score"), "decay": h.get("decay"),
                         "retrieval": h.get("retrieval"), "signals": h.get("signals"),
                         "content": (h.get("content") or "")[:700]}
                        for h in hits
                    ],
                }
            except Exception as e:
                return {"query": query, "error": str(e), "results": []}

        def memory_sweep(dry_run: bool = True, project: str = "") -> dict:
            """Run the decay lifecycle pass (archive stale, purge dead, consolidate dupes)."""
            try:
                from core.memory import memory

                return memory.sweep(project=project or None, dry_run=bool(dry_run))
            except Exception as e:
                return {"error": str(e)}

        def skill_harvest(session_recent: bool = True, dry_run: bool = False, goal: str = "") -> dict:
            """Distill the current session's trajectory into a validated SKILL.md skill."""
            try:
                from .agent import HermusAgent  # noqa: F401  (only to confirm loop exists)
                from core.skill_forge import skill_forge

                traj = getattr(self, "_last_trajectory", None) or []
                if not traj:
                    return {"created": False,
                            "error": "no trajectory in this registry context; "
                                     "the agent loop auto-harvests after each turn"}
                return skill_forge.harvest(goal or "recent task", traj, dry_run=bool(dry_run))
            except Exception as e:
                return {"created": False, "error": str(e)}

        def skill_forge_stats() -> dict:
            try:
                from core.skill_forge import skill_forge

                return {"stats": skill_forge.stats(), "registry": skill_forge.index()["count"]}
            except Exception as e:
                return {"error": str(e)}

        def counsel_convoke(goal: str, execute: bool = True) -> dict:
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
                "Search prior sessions via free FTS5 + optional semantic hybrid search (project filters to a project scope)",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                    "hybrid": {"type": "boolean", "default": True},
                    "project": {"type": "string", "description": "Restrict to a project (default: all projects)"},
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
                "Spawn one isolated subagent (separate process, JSON-RPC worker) for a single task",
                {"task": {"type": "string"}, "max_steps": {"type": "integer", "default": 4},
                 "timeout": {"type": "number", "default": 0}},
                ["task"],
            ),
            (
                "delegate_tasks",
                delegate_tasks,
                "Hierarchical delegation: split work into parallel sub-agents (own processes, JSON-RPC) and aggregate their structured results",
                {
                    "goal": {"type": "string", "description": "What the whole delegation is for"},
                    "tasks": {"type": "array", "items": {"type": "string"},
                              "description": "Explicit workstreams; omit to have them planned from `goal`"},
                    "max_children": {"type": "integer", "default": 4},
                    "aggregate": {"type": "string", "enum": ["synthesize", "concat", "vote", "best"]},
                },
                ["goal"],
            ),
            (
                "sandbox_run",
                sandbox_run,
                "Run a shell command in an ephemeral sandbox: dropped capabilities, read-only rootfs, CPU/memory/pid caps, no network unless allowed",
                {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                    "network": {"type": "boolean", "default": False},
                    "backend": {"type": "string", "enum": ["", "auto", "docker", "podman", "bwrap", "local"]},
                    "allow_dangerous": {"type": "boolean", "default": False},
                },
                ["command"],
            ),
            (
                "memory_hybrid_search",
                memory_hybrid_search,
                "Hybrid memory recall: FTS5 BM25 + dense embeddings fused with Reciprocal Rank Fusion (handles paraphrased queries)",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 8},
                    "kinds": {"type": "array", "items": {"type": "string"}},
                    "project": {"type": "string"},
                    "explain": {"type": "boolean", "default": False,
                                "description": "Return rank/contribution diagnostics instead of hits"},
                },
                ["query"],
            ),
            (
                "memory_sweep",
                memory_sweep,
                "Apply memory decay lifecycle: archive stale memories, purge dead ones, consolidate duplicates (dry_run by default)",
                {
                    "dry_run": {"type": "boolean", "default": True},
                    "project": {"type": "string"},
                },
                [],
            ),
            (
                "skill_harvest",
                skill_harvest,
                "Distill the current session trajectory into a SKILL.md skill (evaluation-gated, sandbox-validated)",
                {"goal": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}},
                [],
            ),
            (
                "skill_forge_stats",
                skill_forge_stats,
                "Skill forge health: harvested count, quarantine, recent outcome success rate",
                {},
                [],
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
        # Architecture-upgrade tools (research / memory2 / router / workspace / screen)
        try:
            from .integrations import register_architecture_tools

            register_architecture_tools(self)
        except Exception as e:
            self._errors.append(f"integrations: {e}")
        # Android control tools (consent-gated, audited; single Android boundary)
        try:
            from .android.tools import register_android_tools

            register_android_tools(self)
        except Exception as e:
            self._errors.append(f"android: {e}")
        # Computer control tools (honest computer_control_unavailable reporting)
        try:
            from .computer.tools import register_computer_tools

            register_computer_tools(self)
        except Exception as e:
            self._errors.append(f"computer: {e}")
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

    def get_definitions(self, allowed: Optional[set[str]] = None) -> list[dict]:
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

    def execute(self, name: str, args: Optional[dict] = None) -> dict:
        self.load()
        args = args or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"input": args}

        # ---- Permission gate (architecture upgrade) ------------------------
        # Runs before the existence check so DENY-listed tools are blocked even
        # when no executor is registered (e.g. credential_access / GUI actions).
        # Always audited; blocks DENY (and ASK when ask_policy=deny).
        permission_denied = self._check_permission(name, args)
        if permission_denied is not None:
            return permission_denied

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

        # Phase 4 (P3): registry-level fallback chains
        try:
            result = fn(**args)
            if not self._looks_like_error(result):
                return result
            trail = self._walk_fallback(name, args, result)
            if trail:
                return trail
            return result
        except Exception as e:
            trail = self._walk_fallback(name, args, {"error": str(e)})
            if trail:
                return trail
            return {"error": f"{name} failed: {e}"}

    def _check_permission(self, name: str, args: dict) -> Optional[dict]:
        """Return a denial result dict to short-circuit execution, or None to allow."""
        try:
            from .config import config
            from .permissions import Decision, permission_manager

            decision = permission_manager.check(name, args=args)
            if not getattr(config, "permissions_enforce", True):
                return None
            d = Decision(decision["decision"])
            if d == Decision.ALLOW:
                return None
            if d == Decision.DENY:
                return {
                    "error": f"Permission DENIED for tool '{name}'",
                    "permission": decision,
                    "hint": "Enable with: hermus perms set %s allow" % name,
                }
            if d == Decision.ASK:
                if getattr(config, "ask_policy", "allow") == "allow":
                    # audited but allowed (backward-compatible default)
                    return None
                return {
                    "error": f"Permission requires confirmation (ASK) for tool '{name}'",
                    "permission": decision,
                    "hint": "Enable with: hermus perms set %s allow  (or set HERMUS_ASK_POLICY=allow)" % name,
                }
        except Exception:
            pass
        return None

    @staticmethod
    def _looks_like_error(result: Any) -> bool:
        if isinstance(result, dict):
            err = result.get("error")
            return bool(err) or str(result.get("success", "")).lower() == "false"
        return False

    def _walk_fallback(self, name: str, args: dict, original: Any) -> Optional[dict]:
        """Walk the fallback chain for a failed tool. Returns result with trail or None."""
        chain = TOOL_FALLBACK_CHAINS.get(name)
        if not chain:
            return None
        trail = [f"{name}: failed"]
        for entry in chain:
            if entry.get("retry"):
                fn2 = self.executors.get(name)
                if fn2:
                    try:
                        res = fn2(**args)
                        if not self._looks_like_error(res):
                            trail.append(f"{name}: retry ok")
                            return self._with_trail(res, trail)
                    except Exception:
                        trail.append(f"{name}: retry failed")
                continue
            tool = entry.get("tool")
            fn2 = self.executors.get(tool)
            if not fn2:
                continue
            fargs = entry.get("args")
            if callable(fargs):
                try:
                    fargs = fargs(args)
                except Exception:
                    continue
            if fargs is None:
                fargs = {}
            if not isinstance(fargs, dict):
                continue
            try:
                res = fn2(**fargs)
                if not self._looks_like_error(res):
                    trail.append(f"{tool}: ok")
                    # Feed the lessons loop: this tool needed a fallback
                    try:
                        from core.reasoning.lessons import lessons_store

                        lessons_store.add(
                            f"Tool {name} failed; fell back to {tool} successfully. Try {tool} earlier next time.",
                            category="tool_fallback",
                            keywords=f"{name} {tool} fallback retry",
                            source="tool_registry",
                        )
                    except Exception:
                        pass
                    return self._with_trail(res, trail)
                trail.append(f"{tool}: failed")
            except Exception:
                trail.append(f"{tool}: failed")
        return self._with_trail(original, trail) if isinstance(original, dict) else None

    @staticmethod
    def _with_trail(result: dict, trail: list[str]) -> dict:
        out = dict(result)
        out["fallback_trail"] = trail
        return out

    def list_tools(self) -> dict:
        """Registry snapshot for dashboards and CLIs.

        ``tools`` stays a plain list of name strings — CLIs and older dashboards
        iterate it directly (``t.startswith("mcp_")``, ``name in tools``).
        ``catalog`` carries the display details (description, parameters,
        source) the web dashboard renders; it is built lazily from the same
        OpenAI-style definitions the agent loop already uses.
        """
        self.load()
        defs_by_name: dict[str, dict] = {}
        for d in self.definitions:
            fn = d.get("function") if isinstance(d, dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                defs_by_name[str(fn["name"])] = fn
        catalog = [
            {
                "name": name,
                "description": defs_by_name.get(name, {}).get("description", ""),
                "parameters": defs_by_name.get(name, {}).get("parameters")
                or {"type": "object", "properties": {}},
                "source": self.sources.get(name, ""),
            }
            for name in sorted(self.executors.keys())
        ]
        return {
            "count": len(self.executors),
            "definitions": len(self.definitions),
            "tools": sorted(self.executors.keys()),
            "catalog": catalog,
            "sources": dict(self.sources),
            "errors": self._errors,
        }


# Global singleton
tool_registry = ToolRegistry()
