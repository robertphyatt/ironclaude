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

-- status enum: pending_confirmation, awaiting_changes, superseded,
-- confirmed, rejected, in_progress, completed (see VALID_DIRECTIVE_STATUSES
-- in orchestrator_mcp.py)
CREATE TABLE IF NOT EXISTS directives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_ts TEXT NOT NULL,
    source_text TEXT NOT NULL,
    interpretation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_confirmation',
    interpretation_ts TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    planned_worker_type TEXT,
    planned_use_goal INTEGER DEFAULT 0,
    planned_prompt TEXT,
    planned_worker_type_reason TEXT,
    planned_use_goal_reason TEXT,
    planned_prompt_reason TEXT,
    superseded_by INTEGER REFERENCES directives(id)
);

CREATE TABLE IF NOT EXISTS push_requests (
    id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    remote TEXT NOT NULL,
    branch TEXT NOT NULL,
    commit_summary TEXT NOT NULL,
    diff_stats TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message_ts TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_staleness (
    worker_id TEXT PRIMARY KEY,
    hash_value INTEGER NOT NULL,
    stale_since REAL NOT NULL,
    alert_sent INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shadow_concordance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    opus_grade TEXT,
    opus_approved INTEGER,
    shadow_grade TEXT,
    shadow_approved INTEGER,
    concordance TEXT NOT NULL CHECK (concordance IN ('A', 'B', 'C', 'F')),
    confidence_in_disagreement TEXT,
    test_mode INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_shadow_concordance_worker_id ON shadow_concordance(worker_id);
CREATE INDEX IF NOT EXISTS idx_shadow_concordance_created_at ON shadow_concordance(created_at);
"""

# Indexes that depend on columns added by _DIRECTIVES_MIGRATION_COLUMNS.
# These must run AFTER init_db()'s ADD-COLUMN loop, not inside SCHEMA,
# because SCHEMA runs before the migration and would crash on an old DB
# that has the `directives` table but not the migrated column.
_POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_directives_superseded_by ON directives(superseded_by)",
]

# Columns added to `directives` after its initial release. Each is applied via
# an independent ALTER TABLE in init_db() so that pre-existing DBs (which
# already have the CREATE TABLE IF NOT EXISTS'd `directives` without these
# columns) get migrated in place, and so that one already-present column
# doesn't block the rest from being added.
_DIRECTIVES_MIGRATION_COLUMNS = [
    ("planned_worker_type", "TEXT"),
    ("planned_use_goal", "INTEGER DEFAULT 0"),
    ("planned_prompt", "TEXT"),
    ("planned_worker_type_reason", "TEXT"),
    ("planned_use_goal_reason", "TEXT"),
    ("planned_prompt_reason", "TEXT"),
    ("superseded_by", "INTEGER REFERENCES directives(id)"),
]


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize database with schema. Returns connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    # Migrate pre-existing `directives` tables that predate these columns.
    # Each ALTER TABLE runs independently so that one column already being
    # present (a partially-migrated DB) doesn't prevent the rest from
    # being added.
    for column_name, column_def in _DIRECTIVES_MIGRATION_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE directives ADD COLUMN {column_name} {column_def}"
            )
        except sqlite3.OperationalError as exc:
            # SQLite raises "duplicate column name: X" (and, on some
            # versions, "table directives already has column named X")
            # when the column already exists from a prior migration.
            msg = str(exc).lower()
            if "duplicate column name" in msg or "already has column" in msg:
                logger.debug(
                    "directives.%s already present, skipping migration: %s",
                    column_name, exc,
                )
            else:
                raise
    for stmt in _POST_MIGRATION_INDEXES:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            # These statements use CREATE INDEX IF NOT EXISTS, so the
            # already-exists case never raises — ANY error here is
            # unexpected (e.g. the referenced column missing because the
            # ADD-COLUMN loop above regressed). Keep startup fail-open,
            # but log loudly so the regression is visible.
            logger.warning(
                "Post-migration index failed unexpectedly: %s (%s)", stmt, exc
            )
    # Ensure brain_state singleton row exists
    conn.execute(
        "INSERT OR IGNORE INTO brain_state (id, session_active, restart_count) VALUES (1, 0, 0)"
    )
    conn.commit()
    # Deterministic row shape: sqlite3.Row supports BOTH integer indexing
    # (row[0]) and name indexing (row["col"]), so every existing tuple-index
    # call site keeps working while dict(row)-style code becomes safe on ANY
    # connection from init_db. Previously Row was only set as a side effect
    # of WorkerRegistry.__init__, making dict(row) code construction-order-
    # dependent (crash if reached before WorkerRegistry was built).
    conn.row_factory = sqlite3.Row
    return conn
