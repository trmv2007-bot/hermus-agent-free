"""multiai — Multi-AI - multiple AIs talk to each other for anything."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    multiai_parser = subparsers.add_parser("multiai", help="Multi-AI - multiple AIs talk to each other for anything")
    multiai_sub = multiai_parser.add_subparsers(dest="multiai_action")
    multiai_debate = multiai_sub.add_parser("debate", help="Multi-AI debate on topic")
    multiai_debate.add_argument("topic", help="Topic for debate")
    multiai_debate.add_argument("--rounds", type=int, default=2, help="Rounds")
    multiai_debate.add_argument("--model", default=None, help="Model for all agents")
    multiai_debate.add_argument(
        "--agents",
        nargs="+",
        default=None,
        help="Agent personas: researcher coder reviewer writer planner debater optimist pessimist",
    )

    multiai_chat = multiai_sub.add_parser("chat", help="Multi-AI collaborative chat")
    multiai_chat.add_argument("task", help="Task for collaborative chat")
    multiai_chat.add_argument("--rounds", type=int, default=3)
    multiai_chat.add_argument("--model", default=None)

    multiai_sub.add_parser("personas", help="List persona presets")


def run(args, ctx: CLIContext) -> None:
    from core.multi_ai import PERSONA_PRESETS

    if args.multiai_action == "debate":
        from core.multi_ai import MultiAIChat

        chat = MultiAIChat()
        # Add agents based on personas or default team
        if args.agents:
            for persona_name in args.agents:
                persona_desc = PERSONA_PRESETS.get(persona_name, f"You are a {persona_name}")
                chat.add_agent(persona_name, persona_desc, model=args.model)
        else:
            chat.add_default_team(model=args.model)
        result = chat.debate(args.topic, rounds=args.rounds, model=args.model)
        print(f"\n=== Multi-AI Debate: {result['topic']} ===")
        print(f"Agents: {', '.join(result['agents'])} | Rounds: {result['rounds']}")
        for turn in result["history"]:
            print(f"\n[{turn['agent']} - Round {turn['round']}]: {turn['content'][:500]}")
        print(f"\n=== Final Answer ===\n{result['final_answer']}")
    elif args.multiai_action == "chat":
        from core.multi_ai import MultiAIChat

        chat = MultiAIChat()
        chat.add_default_team(model=args.model)
        result = chat.collaborate_on_task(args.task, rounds=args.rounds)
        print(f"\n=== Multi-AI Collaborative Chat: {result['task']} ===")
        for turn in result["history"]:
            print(f"\n[{turn['agent']} - R{turn['round']}]: {turn['content'][:500]}")
        print(f"\n=== Final ===\n{result['final']}")
    elif args.multiai_action == "personas":
        print("Persona presets free:")
        for name, desc in PERSONA_PRESETS.items():
            print(f" - {name}: {desc}")
    else:
        ctx.parser.parse_args(["multiai", "--help"])
