"""Main Agent Loop - Free Hermes Clone with memory, skills, tools, self-improvement"""
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

from .config import config
from .llm import free_llm, FreeLLM
from .memory import memory
from .skill_manager import skill_manager
from .task_tracker import task_tracker

# Import tools
from tools.web_search import web_search, TOOL_DEFINITION as WEB_SEARCH_TOOL
from tools.file_tools import file_read, file_write, file_edit, file_search, TOOLS as FILE_TOOLS
from tools.shell import shell_execute, TOOL_DEFINITION as SHELL_TOOL
from core.custom_api import custom_api_manager

class HermusAgent:
    """Free Hermes-like agent - self-improving with memory and skills"""

    def __init__(self, model: str = None, session_id: str = None):
        self.model_name = model or config.model
        self.llm = FreeLLM(self.model_name)
        self.session_id = session_id or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
        self.trajectory: List[Dict] = []  # For skill creation + research-ready batch
        self.tools = self._get_tools()
        self.agent_tracker_id = None

        print(f"[Hermus Free] Session {self.session_id} | Model {self.model_name} | Free stack (no paywall)")
        # Track agent in task tracker for slide panel
        try:
            self.agent_tracker_id = task_tracker.add_agent(
                agent_id=self.session_id,
                name=f"agent_{self.session_id[:6]}",
                model=self.model_name,
                persona="main",
                task="idle"
            )
        except:
            pass

    def _get_tools(self) -> List[Dict]:
        """40+ free tools - we implement core free ones + custom APIs + browser, vision, voice, backends, trajectory + internet eyes (Agent Reach)"""
        tools = []
        tools.append(WEB_SEARCH_TOOL)
        tools.extend(FILE_TOOLS)
        tools.append(SHELL_TOOL)

        # Browser automation Playwright free
        try:
            from tools.browser import TOOLS as BROWSER_TOOLS
            tools.extend(BROWSER_TOOLS)
        except:
            pass

        # Vision LLaVA via Ollama free
        try:
            from tools.vision import TOOLS as VISION_TOOLS
            tools.extend(VISION_TOOLS)
        except:
            pass

        # Voice memo transcription faster-whisper free
        try:
            from tools.voice import TOOLS as VOICE_TOOLS
            tools.extend(VOICE_TOOLS)
        except:
            pass

        # Internet Eyes - Agent Reach features - Give AI agent eyes to see entire internet, zero API fees
        try:
            from tools.internet_eyes import TOOLS as INTERNET_EYES_TOOLS
            tools.extend(INTERNET_EYES_TOOLS)
        except Exception as e:
            print(f"[Internet Eyes] Failed to load: {e}")

        # Agent Reach Doctor - real probing
        try:
            from tools.agent_reach_doctor import TOOLS as DOCTOR_TOOLS
            tools.extend(DOCTOR_TOOLS)
        except:
            pass

        # More platforms: Facebook, Instagram, XiaoHongShu, LinkedIn, Xiaoyuzhou
        try:
            from tools.facebook import TOOLS as FB_TOOLS
            tools.extend(FB_TOOLS)
        except:
            pass

        # Backends: Docker, SSH, Modal free tier, Daytona, Vercel Sandbox
        try:
            from backends.backend_manager import TOOL_DEFINITION as BACKEND_TOOL
            from backends.backend_manager import list_backends as list_backends_tool
            tools.append(BACKEND_TOOL)
            tools.append({
                "type": "function",
                "function": {
                    "name": "list_backends",
                    "description": "List seven terminal backends: local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox - free, with availability and descriptions",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            })
        except:
            pass

        # Trajectory batch generation + compression
        try:
            from core.trajectory import TOOLS as TRAJECTORY_TOOLS
            tools.extend(TRAJECTORY_TOOLS)
        except:
            pass

        # Response time tester - how much time API key takes to get response from AI model - free
        try:
            from core.response_tester import TOOLS as RESPONSE_TESTER_TOOLS
            tools.extend(RESPONSE_TESTER_TOOLS)
        except:
            pass

        # Pentest tools - Strix features - Open-source AI penetration testing tool
        try:
            from tools.pentest import TOOLS as PENTEST_TOOLS
            tools.extend(PENTEST_TOOLS)
        except Exception as e:
            print(f"[Pentest] Failed to load pentest tools: {e}")

        # Add custom APIs as tools - free custom API feature
        try:
            custom_tools = custom_api_manager.get_tool_definitions()
            tools.extend(custom_tools)
        except Exception as e:
            print(f"[Custom API] Failed to load custom tools: {e}")
        # Memory tools
        tools.append({
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": "Search prior sessions via free FTS5",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
            }
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "memory_add",
                "description": "Add to curated memory - agent decides what to remember",
                "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}
            }
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "skill_list",
                "description": "List available auto-created skills",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        })
        tools.append({
            "type": "function",
            "function": {
                "name": "skill_use",
                "description": "Use a skill by name",
                "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
            }
        })
        # Subagent spawn free
        tools.append({
            "type": "function",
            "function": {
                "name": "subagent_spawn",
                "description": "Spawn isolated subagent for parallel work",
                "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}
            }
        })
        return tools

    def _execute_tool(self, name: str, args: Dict) -> Dict:
        """Execute free tool"""
        try:
            if name == "web_search":
                from tools.web_search import execute
                return execute(**args)
            elif name == "file_read":
                return file_read(**args)
            elif name == "file_write":
                return file_write(**args)
            elif name == "file_edit":
                return file_edit(**args)
            elif name == "file_search":
                return file_search(**args)
            elif name == "shell_execute":
                return shell_execute(**args)
            elif name == "memory_search":
                results = memory.search_sessions(args.get("query",""), limit=5)
                summary = memory.summarize_search_results(args.get("query",""), results)
                return {"query": args.get("query"), "results": results, "summary": summary}
            elif name == "memory_add":
                memory.curate_memory(args.get("key"), args.get("value"), source_session=self.session_id)
                return {"success": True, "key": args.get("key")}
            elif name == "skill_list":
                skills = skill_manager.list_skills()
                return {"skills": skills, "count": len(skills)}
            elif name == "skill_use":
                skill = skill_manager.get_skill(args.get("name"))
                if not skill:
                    return {"error": f"Skill {args.get('name')} not found"}
                # Try execute skill.py
                try:
                    import importlib.util
                    skill_path = Path(skill["path"]) / "skill.py"
                    if skill_path.exists():
                        spec = importlib.util.spec_from_file_location("skill", skill_path)
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        result = None
                        if hasattr(mod, "run"):
                            result = mod.run()
                        skill_manager.log_skill_usage(args.get("name"), success=True, feedback="Executed via skill_use")
                        return {"skill": args.get("name"), "result": str(result)[:1000], "code": skill.get("code","")[:1000]}
                    return skill
                except Exception as e:
                    skill_manager.log_skill_usage(args.get("name"), success=False, feedback=str(e))
                    return {"error": f"Skill exec failed: {e}", "skill": skill}
            elif name == "subagent_spawn":
                # Free subagent - spawn via subagents/subagent.py
                try:
                    from subagents.subagent import spawn_subagent
                    result = spawn_subagent(args.get("task"))
                    return result
                except Exception as e:
                    return {"error": f"Subagent spawn failed: {e}, task: {args.get('task')}"}
            # Browser automation free
            elif name in ("browser_navigate", "browser_click", "browser_type", "browser_screenshot", "browser_extract", "browser_close"):
                try:
                    from tools.browser import TOOL_MAP as BROWSER_MAP
                    func = BROWSER_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Browser tool {name} not found"}
                except Exception as e:
                    return {"error": f"Browser tool {name} failed: {e}"}
            # Vision free
            elif name in ("vision_analyze", "vision_available_models"):
                try:
                    from tools.vision import TOOL_MAP as VISION_MAP
                    func = VISION_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Vision tool {name} not found"}
                except Exception as e:
                    return {"error": f"Vision tool {name} failed: {e}"}
            # Voice memo transcription free
            elif name in ("transcribe_audio", "transcribe_voice_memo", "voice_available_models"):
                try:
                    from tools.voice import TOOL_MAP as VOICE_MAP
                    func = VOICE_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Voice tool {name} not found"}
                except Exception as e:
                    return {"error": f"Voice tool {name} failed: {e}"}
            # Backends free
            elif name == "backend_execute":
                try:
                    from backends.backend_manager import backend_execute
                    return backend_execute(**args)
                except Exception as e:
                    return {"error": f"Backend execute failed: {e}"}
            elif name == "list_backends":
                try:
                    from backends.backend_manager import list_backends
                    return list_backends()
                except Exception as e:
                    return {"error": f"List backends failed: {e}"}
            # Trajectory batch generation + compression free
            elif name in ("trajectory_batch_generate", "trajectory_compress", "trajectory_stats"):
                try:
                    from core.trajectory import TOOL_MAP as TRAJECTORY_MAP
                    func = TRAJECTORY_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Trajectory tool {name} not found"}
                except Exception as e:
                    return {"error": f"Trajectory tool {name} failed: {e}"}
            # Response time tester - how much time API key takes
            elif name in ("test_api_key_response_time", "test_custom_api_response_time", "get_response_time_stats"):
                try:
                    from core.response_tester import TOOL_MAP as RESPONSE_MAP
                    func = RESPONSE_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Response tester tool {name} not found"}
                except Exception as e:
                    return {"error": f"Response tester tool {name} failed: {e}"}
            # Pentest tools - Strix features - free
            elif name in ("subdomain_enum", "fingerprinting", "attack_surface_mapping", "browser_xss_test", "shell_exploit", "custom_exploit_runtime", "http_proxy_intercept", "search_vuln_kb", "get_owasp_categories", "comprehensive_scan", "scan_api_spec", "pentest_distribute_task", "pentest_chain_vulns", "pentest_scalable_scan", "pentest_create_run", "pentest_add_finding", "pentest_generate_patch", "pentest_generate_report", "pentest_view_run"):
                try:
                    from tools.pentest import TOOL_MAP as PENTEST_MAP
                    func = PENTEST_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Pentest tool {name} not found"}
                except Exception as e:
                    return {"error": f"Pentest tool {name} failed: {e}"}
            # Internet Eyes - Agent Reach features - free, zero API fees
            elif name in ("web_read", "rss_read", "youtube_transcript", "youtube_search", "github_read", "github_search", "twitter_read", "twitter_search", "bilibili_search", "reddit_read", "reddit_search", "v2ex_hot", "xueqiu_stock_search"):
                try:
                    from tools.internet_eyes import TOOL_MAP as INTERNET_MAP
                    func = INTERNET_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Internet Eyes tool {name} not found"}
                except Exception as e:
                    return {"error": f"Internet Eyes tool {name} failed: {e}"}
            # Agent Reach Doctor
            elif name in ("doctor_check_all", "doctor_text_report"):
                try:
                    from tools.agent_reach_doctor import TOOL_MAP as DOCTOR_MAP
                    func = DOCTOR_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Doctor tool {name} not found"}
                except Exception as e:
                    return {"error": f"Doctor tool {name} failed: {e}"}
            # Facebook, Instagram, XiaoHongShu, LinkedIn, Xiaoyuzhou
            elif name in ("facebook_search", "instagram_user_search", "xiaohongshu_search", "linkedin_read", "xiaoyuzhou_transcribe"):
                try:
                    from tools.facebook import TOOL_MAP as FB_MAP
                    func = FB_MAP.get(name)
                    if func:
                        return func(**args)
                    return {"error": f"Social tool {name} not found"}
                except Exception as e:
                    return {"error": f"Social tool {name} failed: {e}"}
            else:
                # Check if it's a custom API - free custom API feature
                try:
                    # If tool name matches a custom API, execute it
                    custom_apis = custom_api_manager.list_apis()
                    custom_names = [api["name"] for api in custom_apis]
                    if name in custom_names:
                        return custom_api_manager.execute_api(name, args)
                except Exception as e:
                    print(f"[Custom API] Execution check failed: {e}")

                return {"error": f"Unknown tool {name}"}
        except Exception as e:
            return {"error": f"Tool {name} execution failed: {e}"}

    def _build_system_prompt(self) -> str:
        """System prompt with memory + user model + skills context (free)"""
        curated = memory.get_curated_memory(limit=10)
        curated_text = "\n".join([f"- {m['key']}: {m['value'][:200]}" for m in curated]) if curated else "No curated memory yet."

        user_model = memory.load_user_model()
        user_model_text = json.dumps(user_model, indent=2)[:1000] if user_model else "No user model yet."

        skills = skill_manager.list_skills()
        skills_text = ", ".join([s["name"] for s in skills[:10]]) if skills else "No skills yet."

        nudges = memory.periodic_nudges()
        nudges_text = "\n".join(nudges) if nudges else "No nudges."

        return f"""You are Hermus Agent Free - a self-improving AI agent that grows with user.

You have:
- Persistent memory across sessions via free SQLite FTS5
- Auto-created skills that self-improve
- Free tools: web_search (DuckDuckGo), file_read/write/edit/search, shell_execute, memory_search/add, skill_list/use, subagent_spawn

Curated Memory (agent-curated, what you decided to remember):
{curated_text}

User Model (free Honcho alternative, your model of who user is):
{user_model_text}

Available Skills (auto-created from past trajectories):
{skills_text}

Periodic Nudges (things to consider persisting):
{nudges_text}

Rules:
- Use tools when needed, don't hallucinate
- After complex task (3+ tool calls), curated memory and skill creation will trigger automatically
- Prefer reusing skills via skill_use for zero-context-cost turns
- Be helpful, concise, and learn from user
- You are free, MIT, no paywall, runs on local Ollama or free Groq/HF
- Current session: {self.session_id}
- Model: {self.model_name}
"""

    def chat(self, user_message: str) -> Dict[str, Any]:
        """Main chat loop - free version of Hermes agent loop"""
        # Track task in slide panel
        task_id = None
        try:
            task_id = task_tracker.add_task(
                task_id=f"chat_{self.session_id[:6]}_{datetime.now().strftime('%H%M%S')}",
                task_type="chat",
                description=user_message[:100],
                model=self.model_name,
                agent=self.session_id
            )
            task_tracker.update_agent(self.agent_tracker_id, task=user_message[:100], status="running", progress="Thinking...")
        except:
            pass

        # Add user message to memory + trajectory
        memory.add_session_message(self.session_id, "user", user_message)
        self.trajectory.append({"role": "user", "content": user_message, "tool_calls": []})

        # Build messages with system prompt + recent history + memory search
        system_prompt = self._build_system_prompt()

        # Search memory for relevant prior sessions (free FTS5)
        memory_results = memory.search_sessions(user_message, limit=3)
        memory_summary = memory.summarize_search_results(user_message, memory_results) if memory_results else ""

        messages = [
            {"role": "system", "content": system_prompt + f"\n\nRelevant prior sessions summary:\n{memory_summary}"},
        ]
        # Add recent trajectory (last 10)
        for turn in self.trajectory[-10:]:
            messages.append({"role": turn["role"], "content": turn["content"]})

        # Call free LLM with tools
        response = self.llm.chat(messages, tools=self.tools)

        # Handle tool calls loop (like Hermes)
        tool_results = []
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except:
                        tool_args = {}
                print(f"[Tool] {tool_name}({tool_args})")
                result = self._execute_tool(tool_name, tool_args)
                tool_results.append({"tool": tool_name, "args": tool_args, "result": result})

                # Add tool result to memory
                memory.add_session_message(
                    self.session_id,
                    "tool",
                    f"Tool {tool_name} result: {json.dumps(result)[:1000]}",
                    tool_calls=[tc],
                    metadata={"tool": tool_name}
                )
                self.trajectory.append({"role": "tool", "content": f"{tool_name} result: {json.dumps(result)[:500]}", "tool_calls": [tc]})

            # After tool calls, get final response from LLM
            messages.append({"role": "assistant", "content": response.content, "tool_calls": response.tool_calls})
            # Add tool results as assistant context for next turn (simplified)
            for tr in tool_results:
                messages.append({"role": "user", "content": f"Tool {tr['tool']} returned: {json.dumps(tr['result'])[:1000]}"})

            final_resp = self.llm.chat(messages)
            final_content = final_resp.content
        else:
            final_content = response.content
            tool_results = []

        # Save assistant response to memory + token usage free tracking
        memory.add_session_message(self.session_id, "assistant", final_content)
        self.trajectory.append({"role": "assistant", "content": final_content, "tool_calls": response.tool_calls})

        # Track token usage free
        try:
            # response.usage from LLM, plus final response usage
            usage = getattr(response, 'usage', None) or {}
            if usage:
                memory.add_token_usage(self.session_id, usage)
            # Also track final response if different
            if 'final_resp' in locals():
                final_usage = getattr(final_resp, 'usage', None)
                if final_usage:
                    memory.add_token_usage(self.session_id, final_usage)
        except:
            pass

        # Complete task tracking for slide panel
        try:
            if task_id:
                task_tracker.complete_task(task_id, status="done", result=final_content[:200])
            task_tracker.update_agent(self.agent_tracker_id, status="idle", progress="Done", task="idle")
        except:
            pass

        # Post-execution: Check if should create skill (autonomous skill creation)
        skill_created = None
        if skill_manager.should_create_skill(self.trajectory):
            print("[Skill] Complex trajectory detected, auto-creating skill...")
            skill_created = skill_manager.create_skill_from_trajectory(self.trajectory, self.session_id)

        # Post-execution: Curate memory - agent decides what to remember (free)
        try:
            # Simple heuristic: if user said "remember" or trajectory had success, curate
            if "remember" in user_message.lower() or len(tool_results) >= 2:
                memory.curate_memory(
                    key=f"session_{self.session_id[:8]}_topic",
                    value=user_message[:200] + " -> " + final_content[:300],
                    source_session=self.session_id,
                    importance=6
                )
        except:
            pass

        return {
            "session_id": self.session_id,
            "response": final_content,
            "tool_results": tool_results,
            "tool_calls": response.tool_calls,
            "skill_created": skill_created,
            "memory_results": memory_results[:2] if memory_results else []
        }

    def new_session(self):
        """Start fresh conversation - /new"""
        self.session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"
        self.trajectory = []
        print(f"[Hermus] New session {self.session_id}")
        return self.session_id

# For CLI usage
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermus Agent Free - Self-improving AI")
    parser.add_argument("--model", default=config.model, help="Model: ollama/llama3.1:8b, groq/llama-3.1-70b-versatile, hf/mistralai/Mistral-7B, mock/mock")
    args = parser.parse_args()

    agent = HermusAgent(model=args.model)
    print(f"Hermus Free ready. Model {args.model}. Type /new, /skills, /model, /exit")
    while True:
        try:
            user_input = input("\nYou> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("/exit", "exit", "quit"):
                break
            if user_input.lower().startswith("/new"):
                agent.new_session()
                print("New session started")
                continue
            if user_input.lower().startswith("/skills"):
                skills = skill_manager.list_skills()
                print(f"Skills ({len(skills)}):")
                for s in skills:
                    print(f" - {s['name']}: {s['description'][:100]}")
                continue
            if user_input.lower().startswith("/model"):
                parts = user_input.split()
                if len(parts) > 1:
                    agent = HermusAgent(model=parts[1])
                    print(f"Switched to {parts[1]}")
                else:
                    print(f"Current model: {agent.model_name}")
                continue

            result = agent.chat(user_input)
            print(f"\nHermus> {result['response']}")
            if result['tool_results']:
                print(f"[Tools used: {', '.join([tr['tool'] for tr in result['tool_results']])}]")
            if result['skill_created'] and result['skill_created'].get('created'):
                print(f"[New skill created: {result['skill_created']['name']}]")

        except KeyboardInterrupt:
            print("\nUse /exit to quit")
        except Exception as e:
            print(f"Error: {e}")
