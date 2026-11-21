"""Shared CLI plumbing: context object + reused argument builders."""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass
class CLIContext:
    """Runtime context handed to every command handler."""

    parser: argparse.ArgumentParser


def add_screen_start_args(command_parser):
    command_parser.add_argument("--fps", type=float, default=10.0)
    command_parser.add_argument("--buffer-seconds", type=float, default=30.0)
    command_parser.add_argument("--format", choices=["mp4", "webm"], default="mp4")


def add_computer_task_args(command_parser):
    command_parser.add_argument("task", nargs="+", help="Natural-language desktop task")
    command_parser.add_argument("--task-id", default=None)
    command_parser.add_argument("--model", default=None, help="Ollama vision model for semantic verification")
    command_parser.add_argument("--retries", type=int, default=2)
    command_parser.add_argument("--no-skill", action="store_true", help="Do not learn/update a skill")
    command_parser.add_argument("--dry-run", action="store_true", help="Plan and simulate without touching the machine")
