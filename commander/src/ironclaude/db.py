# src/ic/db.py
"""SQLite database initialization and schema for IronClaude daemon."""

from __future__ import annotations

import sqlite3
import logging
import re
from pathlib import Path

logger = logging.getLogger("ironclaude.db")

DIRECT_REPLY_FALLBACK_REASON = "Marked direct reply classified operator message as non-actionable."
_SLACK_TIMESTAMP_RE = re.compile(r"^[0-9]+\.[0-9]+$")

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
    finished_at TEXT,
    client TEXT,
    model TEXT,
    native_session_id TEXT
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

CREATE TABLE IF NOT EXISTS operator_message_acknowledgements (
    source_ts TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS reject_acknowledgement_for_directive
BEFORE INSERT ON operator_message_acknowledgements
WHEN EXISTS (SELECT 1 FROM directives WHERE source_ts = NEW.source_ts)
BEGIN
    SELECT RAISE(ABORT, 'operator message already has a directive');
END;

CREATE TRIGGER IF NOT EXISTS reject_directive_for_acknowledgement
BEFORE INSERT ON directives
WHEN EXISTS (
    SELECT 1 FROM operator_message_acknowledgements WHERE source_ts = NEW.source_ts
)
BEGIN
    SELECT RAISE(ABORT, 'operator message already acknowledged');
END;

CREATE TABLE IF NOT EXISTS directive_capability_blocks (
    directive_id INTEGER PRIMARY KEY REFERENCES directives(id),
    capabilities_json TEXT NOT NULL,
    denial_scope TEXT NOT NULL,
    target TEXT NOT NULL,
    reason TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('blocked', 'recovered')),
    first_observed_at REAL NOT NULL,
    last_observed_at REAL NOT NULL,
    next_recheck_at REAL NOT NULL,
    backoff_seconds REAL NOT NULL,
    notification_state TEXT NOT NULL DEFAULT 'pending',
    generation INTEGER NOT NULL DEFAULT 1,
    recovery_dispatch_state TEXT NOT NULL DEFAULT 'none'
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

CREATE TABLE IF NOT EXISTS provider_role_state (
    role TEXT PRIMARY KEY,
    current_client TEXT NOT NULL CHECK (current_client IN ('claude', 'codex')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_capability_state (
    host TEXT NOT NULL,
    client TEXT NOT NULL CHECK (client IN ('claude', 'codex')),
    role TEXT NOT NULL,
    tier TEXT NOT NULL,
    configured INTEGER NOT NULL DEFAULT 0,
    supported INTEGER NOT NULL DEFAULT 0,
    installed INTEGER NOT NULL DEFAULT 0,
    authenticated INTEGER,
    available INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    reason TEXT,
    observed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (host, client, role, tier)
);
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


def persist_operator_message_acknowledgement(
    conn: sqlite3.Connection, source_ts: str, reason: str
) -> dict:
    """Persist, or return, an immutable no-action disposition for one message."""
    if conn is None:
        raise RuntimeError("Database connection required for operator message acknowledgement")
    if not isinstance(source_ts, str) or not _SLACK_TIMESTAMP_RE.fullmatch(source_ts):
        raise ValueError("source_ts must be an exact Slack timestamp string")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-blank string")
    if conn.in_transaction:
        raise RuntimeError(
            "Database connection already has an active transaction; operator message "
            "acknowledgement requires an idle connection."
        )

    def result_from(row) -> dict:
        return {"source_ts": row[0], "reason": row[1], "created_at": row[2]}

    existing = conn.execute(
        "SELECT source_ts, reason, created_at FROM operator_message_acknowledgements "
        "WHERE source_ts=?",
        (source_ts,),
    ).fetchone()
    if existing is not None:
        return result_from(existing)

    try:
        conn.execute(
            "INSERT INTO operator_message_acknowledgements (source_ts, reason) VALUES (?, ?)",
            (source_ts, reason),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        concurrent = conn.execute(
            "SELECT source_ts, reason, created_at FROM operator_message_acknowledgements "
            "WHERE source_ts=?",
            (source_ts,),
        ).fetchone()
        if concurrent is not None:
            return result_from(concurrent)
        raise
    except sqlite3.Error:
        conn.rollback()
        raise

    persisted = conn.execute(
        "SELECT source_ts, reason, created_at FROM operator_message_acknowledgements "
        "WHERE source_ts=?",
        (source_ts,),
    ).fetchone()
    return result_from(persisted)


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
    # Migrate pre-existing `workers` tables that predate provider identity
    # columns persisted at spawn.
    for _wcol in ("client", "model", "native_session_id"):
        try:
            conn.execute(f"ALTER TABLE workers ADD COLUMN {_wcol} TEXT")
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column name" in msg or "already has column" in msg:
                logger.debug("workers.%s already present, skipping migration: %s", _wcol, exc)
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
