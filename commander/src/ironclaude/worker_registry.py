# src/ic/worker_registry.py
"""SQLite-backed registry for objectives, tasks, workers, and events."""

from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger("ironclaude.registry")


class WorkerRegistry:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    # --- Objectives ---

    def create_objective(self, text: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO objectives (text) VALUES (?)", (text,)
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_active_objective(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM objectives WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def complete_objective(self, obj_id: int) -> None:
        self._conn.execute(
            "UPDATE objectives SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
            (obj_id,),
        )
        self._conn.commit()

    # --- Tasks ---

    def create_task(self, objective_id: int, description: str, order_index: int = 0) -> int:
        cursor = self._conn.execute(
            "INSERT INTO tasks (objective_id, description, order_index) VALUES (?, ?, ?)",
            (objective_id, description, order_index),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_pending_tasks(self, objective_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE objective_id = ? AND status = 'pending' ORDER BY order_index",
            (objective_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_task_status(self, task_id: int, status: str, worker_id: str | None = None) -> None:
        if worker_id:
            self._conn.execute(
                "UPDATE tasks SET status = ?, worker_id = ? WHERE id = ?",
                (status, worker_id, task_id),
            )
        else:
            self._conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
            )
        if status == "completed":
            self._conn.execute(
                "UPDATE tasks SET completed_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
        self._conn.commit()

    def get_task_description(self, task_id: int | None) -> str | None:
        if task_id is None:
            return None
        row = self._conn.execute(
            "SELECT description FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return row[0] if row else None

    # --- Workers ---

    def register_worker(
        self, worker_id: str, worker_type: str, tmux_session: str,
        machine: str | None = None, repo: str | None = None, task_id: int | None = None,
        description: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO workers (id, type, machine, repo, tmux_session, task_id, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (worker_id, worker_type, machine, repo, tmux_session, task_id, description),
        )
        self._conn.commit()

    def get_worker(self, worker_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM workers WHERE id = ?", (worker_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_worker_status(self, worker_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE workers SET status = ? WHERE id = ?", (status, worker_id)
        )
        if status in ("completed", "failed", "killed"):
            self._conn.execute(
                "UPDATE workers SET finished_at = datetime('now') WHERE id = ?",
                (worker_id,),
            )
        self._conn.commit()

    def get_running_workers(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM workers WHERE status = 'running'"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_running_workers_by_type(self, worker_type: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM workers WHERE status = 'running' AND type = ?",
            (worker_type,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Events ---

    def log_event(
        self, event_type: str, worker_id: str | None = None, details: dict | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (event_type, worker_id, details) VALUES (?, ?, ?)",
            (event_type, worker_id, json.dumps(details) if details else None),
        )
        self._conn.commit()

    def get_recent_events(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
