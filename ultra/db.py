"""SQLite storage. One file, WAL mode, safe for the threads the app uses."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL, updated_at REAL);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'dashboard',
    chat_id INTEGER,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    plan TEXT,
    steps TEXT NOT NULL DEFAULT '[]',
    current_step INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    files TEXT NOT NULL DEFAULT '[]',
    approval_reason TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    attachments TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL, ts REAL NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_task ON task_logs(task_id);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT, level TEXT NOT NULL DEFAULT 'info'
);
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'personal',
    project TEXT,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active', deadline TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'medium',
    due TEXT, status TEXT NOT NULL DEFAULT 'todo', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS telegram_users (
    user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, username TEXT, name TEXT, paired_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing_codes (code TEXT PRIMARY KEY, expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pair_attempts (user_id INTEGER NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE, pw_hash TEXT NOT NULL, totp_secret TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'browser', token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL, last_seen REAL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, route TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0, ok INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS health (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, cpu REAL, ram REAL, disk REAL, note TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def all(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def backup_to(self, dest: Path) -> None:
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()

    # ---- settings -------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    # ---- audit ----------------------------------------------------------
    def audit(self, actor: str, action: str, detail: str = "", level: str = "info") -> None:
        self.execute("INSERT INTO audit(ts,actor,action,detail,level) VALUES(?,?,?,?,?)",
                     (time.time(), actor, action, detail[:2000], level))
