# src/ic/db.py
"""SQLite database initialization and schema for IronClaude daemon."""

from __future__ import annotations

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger("ironclaude.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS objectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    objective_id INTEGER NOT NULL REFERENCES objectives(id),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    worker_id TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    machine TEXT,
    repo TEXT,
    description TEXT NOT NULL DEFAULT '',
    tmux_session TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    task_id INTEGER REFERENCES tasks(id),
    spawned_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    worker_id TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS brain_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    session_active INTEGER NOT NULL DEFAULT 0,
    last_heartbeat TEXT,
    state_snapshot_path TEXT,
    restart_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS directives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_ts TEXT NOT NULL,
    source_text TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_confirmation',
    interpretation_ts TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize database with schema. Returns connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.executescript(SCHEMA)
    # Ensure brain_state singleton row exists
    conn.execute(
        "INSERT OR IGNORE INTO brain_state (id, session_active, restart_count) VALUES (1, 0, 0)"
    )
    conn.commit()
    return conn
