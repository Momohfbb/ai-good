"""Paths and default settings.

Settings live in the database (table `settings`) so they can be changed from
the dashboard. The values below are only the defaults used on first run.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ULTRA_DATA_DIR", BASE_DIR / "data")).resolve()
WEB_DIR = Path(__file__).resolve().parent / "web"

DB_PATH = DATA_DIR / "ultra.db"
KEY_PATH = DATA_DIR / "secret.key"
ADMIN_KEY_PATH = DATA_DIR / "ADMIN_KEY.txt"
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
CLEAN_SHUTDOWN_MARK = DATA_DIR / ".clean_shutdown"

DEFAULT_SETTINGS: dict = {
    # Interface
    "ui_language": "ar",            # ar | en
    "assistant_language": "auto",   # auto | ar | en | fr ...
    "theme": "dark",
    # Server
    "host": "127.0.0.1",            # 0.0.0.0 to reach the dashboard from the phone on the LAN
    "port": 8765,
    # Task engine
    "max_parallel_tasks": 3,
    "step_retries": 2,
    "low_resource_mode": False,
    # Files agent is jailed inside this folder
    "workspace_dir": str(DATA_DIR / "workspace"),
    # Model routing: task type -> ordered candidates "provider:model".
    # The first candidate whose provider is configured is used; the next is a fallback.
    "routes": {
        "plan":     ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen3:8b"],
        "chat":     ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen3:8b"],
        "research": ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen3:8b"],
        "writing":  ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen3:8b"],
        "coding":   ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen3-coder"],
        "vision":   ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen2.5vl"],
        "verify":   ["anthropic:claude-opus-5-5", "openai:gpt-5", "ollama:qwen3:8b"],
    },
    # Effort per task type for Claude models (low | medium | high | xhigh | max)
    "effort": {"plan": "medium", "chat": "low", "research": "high", "writing": "medium",
               "coding": "high", "vision": "medium", "verify": "low"},
    "web_search_enabled": False,     # Claude server-side web search (billed per search)
    "cross_check_enabled": False,    # ask a second model to review answers
    "monthly_budget_usd": 20.0,      # paid model calls are refused above this
    # OpenAI prices in USD per million tokens (input, output), editable because they change.
    # "*" is used for models not listed so OpenAI spend always counts toward the budget.
    "openai_prices": {"gpt-5": [1.25, 10.0], "*": [1.25, 10.0]},
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_num_ctx": 16384,         # context asked of Ollama; its own default (4k) is too small for planning
    "openai_base_url": "https://api.openai.com/v1",
    # Telegram
    "telegram_enabled": False,
    "telegram_notify": True,
    # Backups
    "auto_backup_hours": 24,
    "backups_keep": 10,
    # Optional external heartbeat URL (e.g. healthchecks.io) pinged every 5 minutes
    "heartbeat_url": "",
}

# Secrets are never stored in settings; they live encrypted in the vault.
SECRET_NAMES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, BACKUP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
