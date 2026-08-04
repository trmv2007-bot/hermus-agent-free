"""Config for Hermus Free - No paywalls"""
import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

class Config(BaseModel):
    # LLM Provider - free options
    model: str = "ollama/llama3.1:8b"  # ollama/..., groq/..., hf/..., mock/...
    ollama_base_url: str = "http://localhost:11434"
    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    hf_token: Optional[str] = os.getenv("HF_TOKEN")

    # Memory
    memory_db_path: str = "data/memory.db"
    user_model_path: str = "data/user_model.json"
    trajectory_path: str = "data/trajectories.jsonl"

    # Skills
    skills_dir: str = "skills"
    auto_skill_threshold: int = 3  # auto-create skill after 3+ tool calls

    # Gateway
    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    discord_bot_token: Optional[str] = os.getenv("DISCORD_BOT_TOKEN")
    gateway_port: int = 8000

    # Scheduler
    scheduler_db: str = "data/scheduler.db"

    # TUI
    history_file: str = "data/tui_history.txt"

    # Paths
    @property
    def base_dir(self) -> Path:
        # Find project root (where this file's parent's parent has README)
        return Path(__file__).parent.parent

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        return self.base_dir / path

config = Config()
