"""Plugins: add new agents or actions without touching the core.

A plugin is one Python file in the plugins folder (data/plugins/ or the bundled
plugins/ folder) with a PLUGIN dict and a register(api) function:

    PLUGIN = {"name": "weather", "version": "1.0", "description": "...", "api": 1}

    def register(api):
        async def today(ctx, city: str):
            ...
            return api.StepResult(f"Weather in {city}: ...")
        api.add_action("research", "weather", "Weather for a city", {"city": "text"}, today)

Plugins run with the same rights as the program, so a new plugin is OFF until the
owner turns it on in Settings → Plugins. A plugin that fails to load is reported
and skipped; it never stops the system.
"""
from __future__ import annotations

import hashlib
import importlib.util
import traceback
from pathlib import Path

from .agents import Action, ActionError, Agent, StepResult
from .db import Database

PLUGIN_API_VERSION = 1


class PluginAPI:
    StepResult = StepResult
    ActionError = ActionError

    def __init__(self, agents: dict[str, Agent], plugin: str):
        self.agents = agents
        self.plugin = plugin
        self.added: list[str] = []

    def add_agent(self, key: str, name_ar: str, name_en: str, note_ar: str = "", note_en: str = "") -> Agent:
        if key in self.agents:
            raise ValueError(f"agent {key} exists")
        self.agents[key] = Agent(key, name_ar, name_en, "ready", note_ar, note_en)
        self.added.append(key)
        return self.agents[key]

    def add_action(self, agent: str, name: str, description: str, params: dict, fn, risk: str = "safe") -> None:
        if agent not in self.agents:
            raise ValueError(f"unknown agent {agent}")
        if name in self.agents[agent].actions:
            raise ValueError(f"action {agent}.{name} exists")
        if risk not in ("safe", "approval"):
            raise ValueError("risk must be safe or approval")
        self.agents[agent].actions[name] = Action(name, f"{description} [plugin {self.plugin}]", params, risk, fn)
        self.added.append(f"{agent}.{name}")


def discover(folders: list[Path]) -> list[Path]:
    files = []
    for d in folders:
        if d.is_dir():
            files += sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))
    return files


def file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def load_plugins(db: Database, agents: dict[str, Agent], folders: list[Path]) -> list[dict]:
    """Load enabled plugins. A plugin whose file changed since it was enabled is turned off again."""
    enabled: dict = db.get_setting("plugins_enabled", {}) or {}
    report = []
    for path in discover(folders):
        key = path.stem
        entry = {"name": key, "file": str(path), "hash": file_hash(path), "enabled": False, "loaded": False,
                 "error": "", "adds": [], "description": "", "version": ""}
        try:
            spec = importlib.util.spec_from_file_location(f"ultra_plugin_{key}", path)
            src = path.read_text(encoding="utf-8")
            meta_ok = "PLUGIN" in src and "def register" in src
            if not meta_ok:
                raise ValueError("missing PLUGIN or register()")
            if enabled.get(key) and enabled[key] != entry["hash"]:
                entry["error"] = "file_changed_reenable"
                enabled.pop(key)
                db.set_setting("plugins_enabled", enabled)
                db.audit("plugins", "disabled_changed_file", key, "warning")
            elif enabled.get(key):
                entry["enabled"] = True
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                meta = getattr(mod, "PLUGIN", {})
                if int(meta.get("api", 1)) > PLUGIN_API_VERSION:
                    raise ValueError(f"needs plugin api {meta.get('api')}")
                entry.update(description=meta.get("description", ""), version=meta.get("version", ""))
                api = PluginAPI(agents, key)
                mod.register(api)
                entry.update(loaded=True, adds=api.added)
            else:
                # read the description without running the plugin's code
                for line in src.splitlines():
                    if '"description"' in line:
                        entry["description"] = line.split('"description"', 1)[1].split('"')[1]
                        break
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            db.audit("plugins", "load_failed", f"{key}: {traceback.format_exc()[-500:]}", "warning")
        report.append(entry)
    return report
