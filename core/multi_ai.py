"""Multi-AI Collaboration - Multiple AIs can talk to each other for anything, free"""
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from .config import config
from .llm import FreeLLM
from .memory import memory

class AgentPersona:
    """Persona for multi-AI chat"""
    def __init__(self, name: str, persona: str, model: str = None, color: str = "white"):
        self.name = name
        self.persona = persona  # e.g., "You are a researcher", "You are a coder", "You are a reviewer"
        self.model = model or config.model
        self.llm = FreeLLM(self.model)
        self.color = color
        self.agent_id = f"{name}_{uuid.uuid4().hex[:4]}"

class MultiAIChat:
    """Multiple AIs talking to each other for anything - free collaboration, debate, consensus"""

    def __init__(self, session_id: str = None):
        self.session_id = session_id or f"multi_ai_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        self.agents: List[AgentPersona] = []
        self.conversation_history: List[Dict] = []
        self.rounds = 0

    def add_agent(self, name: str, persona: str, model: str = None, color: str = "white") -> AgentPersona:
        """Add AI agent with persona"""
        agent = AgentPersona(name, persona, model, color)
        self.agents.append(agent)
        print(f"[MultiAI] Added agent {name} with persona: {persona[:60]}... model {model or config.model}")
        return agent

    def add_default_team(self, model: str = None):
        """Add default team: researcher, coder, reviewer - free"""
        self.add_agent("researcher", "You are a thorough researcher. You search, analyze, and provide facts, sources, and deep insights. Be curious and detailed.", model=model, color="cyan")
        self.add_agent("coder", "You are an expert coder. You write clean, efficient code, use tools, and implement solutions. Be practical and precise.", model=model, color="green")
        self.add_agent("reviewer", "You are a critical reviewer. You check for errors, security issues, edge cases, and improvements. Be skeptical and thorough.", model=model, color="yellow")

    def _build_agent_prompt(self, agent: AgentPersona, topic: str, include_history: bool = True) -> List[Dict]:
        """Build prompt for agent with persona + history"""
        system_content = f"{agent.persona}\n\nYou are {agent.name} in a multi-AI collaboration. Current topic/task: {topic}\n\nOther agents in team: {', '.join([f'{a.name} ({a.persona[:40]}...)' for a in self.agents if a.name != agent.name])}\n\nRules:\n- Collaborate, not compete\n- Build on others' ideas\n- Be concise but thorough\n- If you have tools, use them\n- You can disagree respectfully and propose better ideas\n- Aim for consensus but highlight trade-offs\n"

        messages = [{"role": "system", "content": system_content}]

        if include_history and self.conversation_history:
            # Add conversation history (last 10 turns)
            for turn in self.conversation_history[-10:]:
                # turn has agent, content, round
                role = "user" if turn["agent"] != agent.name else "assistant"
                # For multi-AI, we show as user messages from other agents
                messages.append({"role": "user" if turn["agent"] != agent.name else "assistant", "content": f"[{turn['agent']} - Round {turn['round']}]: {turn['content']}"})

        # Current topic as user message
        messages.append({"role": "user", "content": f"Topic: {topic}\n\nYour turn as {agent.name}. Respond with your perspective, building on prior discussion. If you agree, add value. If you disagree, explain why with alternatives."})

        return messages

    def chat_round(self, topic: str, max_rounds: int = 3, tools: List[Dict] = None) -> List[Dict]:
        """Run multi-AI chat rounds - each agent talks in turn per round"""
        print(f"\n[MultiAI] Starting collaboration on: {topic}\nAgents: {[a.name for a in self.agents]} | Rounds: {max_rounds}")

        # Track multi-AI session in task tracker
        try:
            from .task_tracker import task_tracker
            multi_task_id = task_tracker.add_task(f"multi_ai_{self.session_id}", "multi-ai", topic, model=",".join(set([a.model for a in self.agents])), agent=",".join([a.name for a in self.agents]))
            for ag in self.agents:
                task_tracker.add_agent(ag.agent_id, ag.name, ag.model, persona=ag.persona[:60], task=topic[:100])
        except:
            multi_task_id = None

        for round_num in range(1, max_rounds + 1):
            print(f"\n--- Round {round_num} ---")
            for agent in self.agents:
                messages = self._build_agent_prompt(agent, topic, include_history=True)
                try:
                    # Update tracker
                    try:
                        from .task_tracker import task_tracker
                        task_tracker.update_agent(agent.agent_id, status="thinking", progress=f"Round {round_num} thinking")
                    except:
                        pass

                    response = agent.llm.chat(messages, tools=tools or [])
                    content = response.content

                    # Save to history
                    turn = {
                        "round": round_num,
                        "agent": agent.name,
                        "content": content,
                        "timestamp": datetime.now().isoformat(),
                        "model": agent.model,
                        "tool_calls": response.tool_calls
                    }
                    self.conversation_history.append(turn)

                    # Update tracker
                    try:
                        from .task_tracker import task_tracker
                        task_tracker.update_agent(agent.agent_id, status="done", progress=f"Round {round_num} done: {content[:40]}")
                    except:
                        pass

                    # Print with color if rich available
                    print(f"\n[{agent.name} - Round {round_num}]: {content[:500]}...")
                    if response.tool_calls:
                        print(f"  [Tools: {', '.join([tc.get('name','') for tc in response.tool_calls])}]")

                    # Save to memory for cross-session recall
                    memory.add_session_message(self.session_id, f"agent_{agent.name}", content)

                except Exception as e:
                    print(f"[{agent.name} Error]: {e}")
                    self.conversation_history.append({
                        "round": round_num,
                        "agent": agent.name,
                        "content": f"Error: {e}",
                        "timestamp": datetime.now().isoformat()
                    })

        # Complete tracking
        try:
            from .task_tracker import task_tracker
            if multi_task_id:
                task_tracker.complete_task(multi_task_id, status="done", result=f"Completed {len(self.conversation_history)} turns")
            for ag in self.agents:
                task_tracker.remove_agent(ag.agent_id, final_status="done")
        except:
            pass

        return self.conversation_history

    def get_final_answer(self, topic: str) -> str:
        """Get final consensus answer via judge agent or summarization - free"""
        if not self.conversation_history:
            return "No conversation history"

        # Build summary prompt for judge
        history_text = "\n\n".join([f"Round {t['round']} - {t['agent']}: {t['content']}" for t in self.conversation_history])

        judge_messages = [
            {"role": "system", "content": "You are a judge/summarizer for multi-AI collaboration. Given conversation history from multiple AI agents (researcher, coder, reviewer), provide final consensus answer, highlighting agreements, disagreements, and best path forward. Be concise and actionable."},
            {"role": "user", "content": f"Topic: {topic}\n\nConversation history:\n{history_text}\n\nProvide final consensus answer:"}
        ]

        # Use first agent's LLM as judge (or free mock)
        try:
            judge_llm = FreeLLM(self.agents[0].model if self.agents else config.model)
            resp = judge_llm.chat(judge_messages)
            final = resp.content

            # Save final
            memory.add_session_message(self.session_id, "judge_final", final)
            self.conversation_history.append({
                "round": self.rounds + 1,
                "agent": "judge_final",
                "content": final,
                "timestamp": datetime.now().isoformat()
            })

            return final
        except Exception as e:
            return f"Final answer generation failed: {e}\n\nHistory:\n{history_text[:2000]}"

    def debate(self, topic: str, rounds: int = 2, model: str = None) -> Dict:
        """Quick debate mode - free"""
        if not self.agents:
            self.add_default_team(model=model)

        history = self.chat_round(topic, max_rounds=rounds)
        final = self.get_final_answer(topic)

        return {
            "topic": topic,
            "rounds": rounds,
            "agents": [a.name for a in self.agents],
            "history": history,
            "final_answer": final,
            "session_id": self.session_id
        }

    def collaborate_on_task(self, task: str, tools: List[Dict] = None, rounds: int = 3) -> Dict:
        """Collaborate on task with tools - e.g., research + code + review in parallel then discuss"""
        if not self.agents:
            self.add_default_team()

        # First round: each agent does own research/coding with tools
        # Then discussion rounds
        history = self.chat_round(task, max_rounds=rounds, tools=tools)
        final = self.get_final_answer(task)

        return {
            "task": task,
            "history": history,
            "final": final,
            "session_id": self.session_id
        }

# Global multi-AI manager
multi_ai_manager = MultiAIChat()

# Example personas for quick use
PERSONA_PRESETS = {
    "researcher": "You are a thorough researcher. Search, analyze, provide facts and sources.",
    "coder": "You are an expert coder. Write clean, efficient code, use tools.",
    "reviewer": "You are a critical reviewer. Check errors, security, edge cases.",
    "writer": "You are a creative writer. Write engaging, clear content.",
    "planner": "You are a project planner. Break tasks into steps, estimate, prioritize.",
    "debater": "You are a debater. Argue pros/cons, consider trade-offs, be balanced.",
    "optimist": "You are an optimist. Focus on opportunities, positive angles.",
    "pessimist": "You are a pessimist (devil's advocate). Focus on risks, what could go wrong.",
}
