# src/ic/worker_registry.py
"""SQLite-backed registry for objectives, tasks, workers, and events."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

import psutil

logger = logging.getLogger("ironclaude.registry")


class WorkerRegistry:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._owner_token = uuid.uuid4().hex
        self._operation_depth: dict[str, int] = {}

    @contextmanager
    def _immediate(self):
        if self._conn.in_transaction:
            raise RuntimeError("registry operation requires an idle connection")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

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
        with self._immediate():
            if self._conn.execute(
                "SELECT 1 FROM worker_handoffs h JOIN workers w ON w.id=h.worker_id "
                "JOIN tasks t ON t.id=w.task_id WHERE t.objective_id=? "
                "AND h.completed_at IS NULL", (obj_id,),
            ).fetchone():
                raise RuntimeError("active handoff blocks objective completion")
            self._conn.execute(
                "UPDATE objectives SET status='completed', completed_at=datetime('now') WHERE id=?", (obj_id,),
            )

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
        with self._immediate():
            held = self._conn.execute(
                "SELECT 1 FROM worker_handoffs h JOIN workers w ON w.id=h.worker_id "
                "WHERE h.completed_at IS NULL AND (w.task_id=? OR w.id=? OR "
                "EXISTS (SELECT 1 FROM tasks t WHERE t.id=? AND t.worker_id=w.id))",
                (task_id, worker_id, task_id),
            ).fetchone()
            if held:
                raise RuntimeError("active handoff blocks task status update")
            if worker_id:
                self._conn.execute("UPDATE tasks SET status=?, worker_id=? WHERE id=?", (status, worker_id, task_id))
            else:
                self._conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
            if status == "completed":
                self._conn.execute("UPDATE tasks SET completed_at=datetime('now') WHERE id=?", (task_id,))

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
        client: str | None = None, model: str | None = None,
        native_session_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO workers "
            "(id, type, machine, repo, tmux_session, task_id, description, "
            "client, model, native_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                worker_id, worker_type, machine, repo, tmux_session, task_id,
                description, client, model, native_session_id,
            ),
        )
        self._conn.commit()

    def set_worker_provider(self, worker_id: str, client: str, model: str) -> None:
        """Persist the resolved provider client + model for a spawned worker."""
        with self._immediate():
            if self._active_handoff(worker_id):
                raise RuntimeError("active handoff blocks worker provider update")
            self._conn.execute("UPDATE workers SET client=?, model=? WHERE id=?", (client, model, worker_id))

    def set_worker_workspace(self, worker_id: str, assignment: dict) -> None:
        """Persist the exact durable workspace assignment returned by workspace-manager."""
        fields = (
            "workspace_guid",
            "repository_identity",
            "worktree_path",
            "branch",
            "base_commit",
            "integration_target",
        )
        missing = [field for field in fields if not isinstance(assignment.get(field), str) or not assignment[field]]
        if missing:
            raise ValueError(f"workspace assignment missing required fields: {', '.join(missing)}")
        with self._immediate():
            if self._active_handoff(worker_id):
                raise RuntimeError("active handoff blocks workspace update")
            cursor = self._conn.execute(
                "UPDATE workers SET workspace_guid=?, workspace_repository_identity=?, "
                "workspace_path=?, workspace_branch=?, workspace_base_commit=?, "
                "workspace_integration_target=? WHERE id=?",
                (assignment["workspace_guid"], assignment["repository_identity"],
                 assignment["worktree_path"], assignment["branch"],
                 assignment["base_commit"], assignment["integration_target"], worker_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"worker not found: {worker_id}")

    def get_worker(self, worker_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM workers WHERE id = ?", (worker_id,)
        ).fetchone()
        if not row:
            return None
        worker = dict(row)
        if worker["task_id"] is not None:
            task = self._conn.execute("SELECT * FROM tasks WHERE id=?", (worker["task_id"],)).fetchone()
            worker["task"] = dict(task) if task else None
            objective = self._conn.execute("SELECT * FROM objectives WHERE id=?", (task["objective_id"],)).fetchone() if task else None
            worker["objective"] = dict(objective) if objective else None
        return worker

    def update_worker_status(self, worker_id: str, status: str) -> None:
        # Idempotent terminal stamp: finished_at is set only on a real status
        # change, so re-completing an already-completed worker is a no-op for
        # finished_at (no re-stamp on a repeated kill/finalize).
        # Consequence: a commit_worker-recycled worker's leaked-worktree TTL
        # (main._find_leaked_worktrees) runs from its FIRST completion; the
        # reaper's liveness gate still protects a live session.
        with self._immediate():
            if self._active_handoff(worker_id):
                raise RuntimeError("active handoff blocks worker status update")
            if status in ("completed", "failed", "killed"):
                self._conn.execute(
                    "UPDATE workers SET finished_at = datetime('now') "
                    "WHERE id = ? AND status IS NOT ?", (worker_id, status),
                )
            self._conn.execute("UPDATE workers SET status = ? WHERE id = ?", (status, worker_id))

    _SNAPSHOT_FIELDS = (
        "id", "type", "repo", "description", "task_id", "spawned_at", "finished_at",
        "status", "client", "model", "native_session_id", "tmux_session", "machine",
        "workspace_guid", "workspace_repository_identity", "workspace_path", "workspace_branch",
        "workspace_base_commit", "workspace_integration_target",
    )

    def _snapshot(self, worker: dict) -> dict:
        return {**{field: worker.get(field) for field in self._SNAPSHOT_FIELDS},
                "task": worker.get("task"), "objective": worker.get("objective")}

    def _active_handoff(self, worker_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM worker_handoffs WHERE worker_id=? AND completed_at IS NULL", (worker_id,)
        ).fetchone() is not None

    def _exact_worker(self, snapshot: dict) -> dict:
        worker_id = snapshot.get("id")
        row = self._conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        if row is None:
            raise RuntimeError("worker snapshot missing")
        worker = dict(row)
        if any(worker.get(field) != snapshot.get(field) for field in self._SNAPSHOT_FIELDS):
            raise RuntimeError("worker runtime snapshot changed")
        task_id = worker.get("task_id")
        if task_id is not None:
            task = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None or snapshot.get("task") != dict(task):
                raise RuntimeError("worker task snapshot changed")
            objective = self._conn.execute("SELECT * FROM objectives WHERE id=?", (task["objective_id"],)).fetchone()
            if objective is None or snapshot.get("objective") != dict(objective):
                raise RuntimeError("worker objective snapshot changed")
        return worker

    def acquire_worker_operation(self, snapshot: dict, *, operation: str) -> dict:
        if operation == "claim_worker_handoff":
            raise ValueError("claim_worker_handoff cannot acquire an operation lease")
        existing = self._conn.execute(
            "SELECT nonce, operation, snapshot_json, owner_token FROM worker_operations WHERE worker_id=?",
            (snapshot.get("id"),),
        ).fetchone()
        if existing and existing["owner_token"] == self._owner_token and existing["operation"] == operation and json.loads(existing["snapshot_json"]) == self._snapshot(snapshot):
            self._operation_depth[existing["nonce"]] = self._operation_depth.get(existing["nonce"], 1) + 1
            return {"worker_id": snapshot["id"], "nonce": existing["nonce"], "operation": operation}
        nonce = uuid.uuid4().hex
        with self._immediate():
            worker = self._exact_worker(snapshot)
            if self._active_handoff(worker["id"]):
                raise RuntimeError("active handoff blocks worker operation")
            self._reap_dead_owner_operation(worker["id"])
            try:
                self._conn.execute(
                    "INSERT INTO worker_operations(worker_id, nonce, operation, snapshot_json, owner_token, "
                    "owner_pid, owner_started, phase) VALUES (?, ?, ?, ?, ?, ?, ?, 'claimed')",
                    (worker["id"], nonce, operation, json.dumps(self._snapshot(snapshot), sort_keys=True),
                     self._owner_token, os.getpid(), psutil.Process(os.getpid()).create_time()),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("unreconciled worker operation blocks handoff") from exc
        self._operation_depth[nonce] = 1
        return {"worker_id": worker["id"], "nonce": nonce, "operation": operation}

    def _reap_dead_owner_operation(self, worker_id: str) -> bool:
        """Inside BEGIN IMMEDIATE, reap a lease whose owner PID incarnation is proved dead (H10).

        Phases claimed and effects_started reap alike; a live or unproved owner still
        blocks. Returns True when a row was reaped and False when no lease exists.
        """
        prior = self._conn.execute(
            "SELECT * FROM worker_operations WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if prior is None:
            return False
        if (prior["phase"] not in {"claimed", "effects_started"}
                or not self._operation_owner_proved_dead(prior)):
            raise RuntimeError("unreconciled worker operation blocks handoff")
        removed = self._conn.execute(
            "DELETE FROM worker_operations WHERE worker_id=? AND nonce=? AND phase=? "
            "AND owner_pid=? AND owner_started=?",
            (worker_id, prior["nonce"], prior["phase"], prior["owner_pid"], prior["owner_started"]),
        )
        if removed.rowcount != 1:
            raise RuntimeError("worker operation claim changed")
        # log_event() commits; a raw insert keeps the reap and its event in one transaction.
        self._conn.execute(
            "INSERT INTO events (event_type, worker_id, details) VALUES (?, ?, ?)",
            ("worker_operation_reaped", worker_id, json.dumps({
                "nonce": prior["nonce"], "operation": prior["operation"], "phase": prior["phase"],
                "owner_pid": prior["owner_pid"], "owner_started": prior["owner_started"],
            }, sort_keys=True)),
        )
        return True

    def reap_dead_worker_operations(self) -> int:
        """Startup sweep (H12): reap every dead-owner lease; live and unproved leases stay."""
        reaped = 0
        for row in self._conn.execute("SELECT worker_id FROM worker_operations").fetchall():
            try:
                with self._immediate():
                    if self._reap_dead_owner_operation(row["worker_id"]):
                        reaped += 1
            except RuntimeError:
                continue
        return reaped

    @staticmethod
    def _operation_owner_status(row) -> str:
        pid, started = row["owner_pid"], row["owner_started"]
        if type(pid) is not int or pid <= 0 or not isinstance(started, (int, float)):
            return "unproved"
        try:
            return "dead" if psutil.Process(pid).create_time() != started else "live"
        except psutil.NoSuchProcess:
            return "dead"
        except (psutil.AccessDenied, OSError):
            return "unproved"

    @classmethod
    def _operation_owner_proved_dead(cls, row) -> bool:
        return cls._operation_owner_status(row) == "dead"

    def acquire_worker_operation_waiting(self, snapshot: dict, *, operation: str,
                                         wait_seconds: float, poll_seconds: float = 0.2) -> dict:
        """Wait briefly while another live process (the daemon cycle) holds this lease (H13)."""
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                return self.acquire_worker_operation(snapshot, operation=operation)
            except RuntimeError as exc:
                if (str(exc) != "unreconciled worker operation blocks handoff"
                        or not self._operation_held_by_live_peer(snapshot.get("id"))):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"worker {snapshot.get('id')} is busy (daemon cycle in progress); retry shortly"
                    ) from exc
                time.sleep(poll_seconds)

    def _operation_held_by_live_peer(self, worker_id) -> bool:
        row = self._conn.execute(
            "SELECT owner_token, owner_pid, owner_started FROM worker_operations WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        return (row is not None and row["owner_token"] != self._owner_token
                and self._operation_owner_status(row) == "live")

    def mark_worker_operation_effects_started(self, lease: dict, snapshot: dict) -> None:
        """Close the crash-safe claimed window before any caller body can run."""
        with self._immediate():
            worker = self._exact_worker(snapshot)
            row = self._conn.execute(
                "SELECT * FROM worker_operations WHERE worker_id=? AND nonce=?",
                (lease["worker_id"], lease["nonce"]),
            ).fetchone()
            if (row is None or worker["id"] != lease["worker_id"]
                    or row["owner_token"] != self._owner_token
                    or row["operation"] != lease["operation"]
                    or json.loads(row["snapshot_json"]) != self._snapshot(snapshot)
                    or row["phase"] not in {"claimed", "effects_started"}):
                raise RuntimeError("worker operation lease missing or changed")
            self._conn.execute(
                "UPDATE worker_operations SET phase='effects_started' "
                "WHERE worker_id=? AND nonce=? AND owner_token=? AND phase='claimed'",
                (lease["worker_id"], lease["nonce"], self._owner_token),
            )

    def claim_worker_operation_recovery_warning(self, snapshot: dict) -> dict | None:
        """Claim one durable warning for an unreconciled exact-runtime lease."""
        with self._immediate():
            worker = self._exact_worker(snapshot)
            row = self._conn.execute(
                "SELECT * FROM worker_operations WHERE worker_id=?", (worker["id"],)
            ).fetchone()
            if row is None or row["recovery_warning_claimed"]:
                return None
            lease_snapshot = json.loads(row["snapshot_json"])
            same_runtime = lease_snapshot == self._snapshot(snapshot)
            owner_status = self._operation_owner_status(row)
            if owner_status in {"live", "dead"}:
                # A live owner is still working; a proved-dead owner is reaped at the
                # next acquire or by the startup sweep. Only an unproved owner warns.
                return None
            changed = self._conn.execute(
                "UPDATE worker_operations SET recovery_warning_claimed=1 "
                "WHERE worker_id=? AND nonce=? AND recovery_warning_claimed=0",
                (worker["id"], row["nonce"]),
            )
            if changed.rowcount != 1:
                return None
            return {"nonce": row["nonce"], "operation": row["operation"],
                    "phase": row["phase"], "snapshot": lease_snapshot,
                    "disposition": "runtime_scope_changed" if not same_runtime
                    else "effects_readback_required" if row["phase"] != "claimed"
                    else "owner_identity_unproved"}

    def release_worker_operation(self, worker_id: str, nonce: str, snapshot: dict) -> None:
        with self._immediate():
            row = self._conn.execute(
                "SELECT snapshot_json, owner_token FROM worker_operations WHERE worker_id=? AND nonce=?", (worker_id, nonce)
            ).fetchone()
            if row is None or row["owner_token"] != self._owner_token:
                raise RuntimeError("worker operation lease missing")
            if json.loads(row[0]) != self._snapshot(snapshot):
                raise RuntimeError("worker operation runtime changed")
            depth = self._operation_depth.get(nonce, 1)
            if depth == 1:
                self._conn.execute("DELETE FROM worker_operations WHERE worker_id=? AND nonce=? AND owner_token=?", (worker_id, nonce, self._owner_token))
            self._operation_depth[nonce] = depth - 1
        if self._operation_depth[nonce] == 0:
            del self._operation_depth[nonce]

    @contextmanager
    def worker_operation(self, snapshot: dict, *, operation: str, wait_seconds: float = 0.0):
        lease = (self.acquire_worker_operation_waiting(snapshot, operation=operation,
                                                       wait_seconds=wait_seconds)
                 if wait_seconds > 0 else self.acquire_worker_operation(snapshot, operation=operation))
        try:
            self.mark_worker_operation_effects_started(lease, snapshot)
            yield lease
        finally:
            self.release_worker_operation(lease["worker_id"], lease["nonce"], snapshot)

    _PHASES = ("claimed", "source_stopped", "target_ready", "pm_prepared", "owner_transferred", "runtime_registered", "continuation_dispatched")

    def claim_worker_handoff(self, snapshot: dict, *, generation: int,
                             reconcile_nonce: str | None = None,
                             initial_evidence: dict | None = None) -> dict:
        if type(generation) is not int or generation <= 0:
            raise ValueError("handoff generation must be positive integer")
        if reconcile_nonce is not None and (not isinstance(reconcile_nonce, str) or not reconcile_nonce):
            raise ValueError("handoff reconcile nonce required")
        if initial_evidence is not None and not isinstance(initial_evidence, dict):
            raise ValueError("handoff initial evidence must be an object")
        if initial_evidence and "reconcile_owner" in initial_evidence:
            raise ValueError("handoff initial evidence cannot set reconcile owner")
        owner = ({"nonce": reconcile_nonce, "pid": os.getpid(),
                  "started": psutil.Process(os.getpid()).create_time()}
                 if reconcile_nonce is not None else None)
        evidence = {**(initial_evidence or {})}
        if owner:
            evidence["reconcile_owner"] = owner
        with self._immediate():
            worker = self._exact_worker(snapshot)
            if worker["status"] != "running":
                raise RuntimeError("handoff claim requires running worker")
            try:
                uuid.UUID(worker["native_session_id"])
            except (ValueError, AttributeError, TypeError) as exc:
                raise RuntimeError("handoff source native session must be a valid UUID") from exc
            if worker["client"] not in {"claude", "codex"} or not all(worker[field] for field in (
                "model", "tmux_session", "workspace_guid",
                "workspace_repository_identity", "workspace_path", "workspace_branch",
                "workspace_base_commit", "workspace_integration_target",
            )) or worker["machine"] == "":
                raise RuntimeError("handoff source runtime or workspace incomplete")
            if snapshot.get("task") and (snapshot["task"]["status"] == "completed" or snapshot["objective"]["status"] == "completed"):
                raise RuntimeError("handoff requires unfinished task and objective")
            if self._active_handoff(worker["id"]):
                raise RuntimeError("active handoff already exists")
            latest = self.get_worker_handoff(worker["id"])
            if latest and generation <= latest["generation"]:
                raise RuntimeError("handoff generation must increase")
            self._reap_dead_owner_operation(worker["id"])
            self._conn.execute(
                "INSERT INTO worker_handoffs(worker_id, generation, phase, snapshot_json, evidence_json) "
                "VALUES (?, ?, 'claimed', ?, ?)",
                (worker["id"], generation, json.dumps(self._snapshot(snapshot), sort_keys=True),
                 json.dumps(evidence, sort_keys=True)),
            )
            cursor = self._conn.execute(
                "UPDATE workers SET status='handoff_pending' WHERE id=? AND status='running'", (worker["id"],)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff claim runtime changed")
        return self.get_worker_handoff(worker["id"])

    def get_worker_handoff(self, worker_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM worker_handoffs WHERE worker_id=? ORDER BY generation DESC LIMIT 1", (worker_id,)).fetchone()
        return dict(row) if row else None

    def effective_worker_tier(self, snapshot: dict) -> str | None:
        """Return the concrete tier proven for this exact running runtime.

        A completed handoff's target tier supersedes the semantic worker type.
        Changed or malformed successor evidence must not fall back to that type.
        """
        from ironclaude.orchestrator_mcp import _WORKER_TYPE_TIER

        try:
            worker = self._exact_worker(snapshot)
        except (AttributeError, RuntimeError, TypeError):
            return None
        row = self._conn.execute(
            "SELECT phase,evidence_json FROM worker_handoffs WHERE worker_id=? "
            "AND completed_at IS NOT NULL AND (phase='completed' OR (phase='released' "
            "AND json_extract(evidence_json, '$.disposition')='released_to_target')) "
            "ORDER BY generation DESC LIMIT 1",
            (worker["id"],),
        ).fetchone()
        if row is None:
            return _WORKER_TYPE_TIER.get(worker["type"])
        try:
            evidence = json.loads(row["evidence_json"])
            target = evidence["target_runtime"]
            tier = evidence["target_tier"]
            if (row["phase"] not in {"completed", "released"} or not evidence.get("assignment_finished")
                    or tier not in {"sonnet", "opus", "fable"}
                    or not isinstance(target, dict)
                    or any(target.get(key) != worker.get(key) for key in (
                        "client", "model", "native_session_id", "tmux_session", "machine"))):
                return None
            return tier
        except (KeyError, TypeError, ValueError):
            return None

    def list_pending_worker_handoffs(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM worker_handoffs WHERE completed_at IS NULL ORDER BY created_at, worker_id").fetchall()
        return [dict(row) for row in rows]

    def claim_worker_handoff_reconcile(self, worker_id: str, generation: int, nonce: str,
                                       *, allow_paused: bool = False) -> bool:
        """Exclusively own effects for this generation until explicit release.

        A persisted owner is replaceable only after its exact PID incarnation is
        proved gone. The caller must then reconcile persisted effect intents.
        """
        pid = os.getpid()
        started = psutil.Process(pid).create_time()
        with self._immediate():
            row = self.get_worker_handoff(worker_id)
            if (not row or row["generation"] != generation or row["completed_at"]
                    or (row["pause_reason"] and not allow_paused)):
                return False
            evidence = json.loads(row["evidence_json"])
            owner = evidence.get("reconcile_owner")
            if owner:
                if owner.get("pid") == pid and owner.get("started") == started:
                    return False  # Another controller in this process is still alive.
                try:
                    old = psutil.Process(owner["pid"])
                    gone = old.create_time() != owner["started"]
                except psutil.NoSuchProcess:
                    gone = True
                except (psutil.Error, KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("handoff reconcile owner death unproved") from exc
                if not gone:
                    return False
            evidence["reconcile_owner"] = {"nonce": nonce, "pid": pid, "started": started}
            self._conn.execute(
                "UPDATE worker_handoffs SET evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND completed_at IS NULL",
                (json.dumps(evidence, sort_keys=True), worker_id, generation),
            )
        return True

    def release_worker_handoff_reconcile(self, worker_id: str, generation: int, nonce: str) -> None:
        with self._immediate():
            row = self.get_worker_handoff(worker_id)
            if not row or row["generation"] != generation:
                raise RuntimeError("handoff reconcile generation changed")
            evidence = json.loads(row["evidence_json"])
            owner = evidence.get("reconcile_owner")
            if not owner or owner.get("nonce") != nonce or owner.get("pid") != os.getpid():
                raise RuntimeError("handoff reconcile owner changed")
            evidence.pop("reconcile_owner")
            self._conn.execute(
                "UPDATE worker_handoffs SET evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=?",
                (json.dumps(evidence, sort_keys=True), worker_id, generation),
            )

    def claim_worker_handoff_notification(self, worker_id: str, generation: int) -> bool:
        with self._immediate():
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET notification_claimed=1 WHERE worker_id=? AND generation=? "
                "AND completed_at IS NULL AND notification_claimed=0", (worker_id, generation),
            )
            return cursor.rowcount == 1

    def record_worker_handoff_evidence(self, worker_id: str, generation: int, phase: str, evidence: dict) -> dict:
        """Persist controller intent or observation before the next uncertain effect."""
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("handoff evidence required")
        with self._immediate():
            current = self.get_worker_handoff(worker_id)
            if not current or current["generation"] != generation or current["phase"] != phase or current["completed_at"] or current["pause_reason"]:
                raise RuntimeError("handoff generation or phase changed")
            merged = {**json.loads(current["evidence_json"]), **evidence}
            self._conn.execute(
                "UPDATE worker_handoffs SET evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND phase=? AND completed_at IS NULL AND pause_reason IS NULL",
                (json.dumps(merged, sort_keys=True), worker_id, generation, phase),
            )
        return self.get_worker_handoff(worker_id)

    def resume_worker_handoff(self, worker_id: str, generation: int, phase: str) -> dict:
        with self._immediate():
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET pause_reason=NULL, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND phase=? AND completed_at IS NULL AND pause_reason IS NOT NULL",
                (worker_id, generation, phase),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff generation, phase, or pause changed")
        return self.get_worker_handoff(worker_id)

    def advance_worker_handoff(self, worker_id: str, generation: int, previous_phase: str, phase: str, *, evidence: dict | None = None) -> dict:
        if previous_phase not in self._PHASES or self._PHASES.index(previous_phase) + 1 >= len(self._PHASES) or self._PHASES[self._PHASES.index(previous_phase) + 1] != phase:
            raise ValueError("illegal handoff phase transition")
        with self._immediate():
            current = self.get_worker_handoff(worker_id)
            if not current or current["generation"] != generation or current["phase"] != previous_phase or current["pause_reason"]:
                raise RuntimeError("handoff generation, phase, or pause changed")
            merged = {**json.loads(current["evidence_json"]), **(evidence or {})}
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET phase=?, evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND phase=? AND completed_at IS NULL AND pause_reason IS NULL",
                (phase, json.dumps(merged, sort_keys=True), worker_id, generation, previous_phase),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff generation or phase changed")
        return self.get_worker_handoff(worker_id)

    def pause_worker_handoff(self, worker_id: str, generation: int, reason: str) -> dict:
        if not reason.strip():
            raise ValueError("handoff pause reason required")
        with self._immediate():
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET pause_reason=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND completed_at IS NULL", (reason, worker_id, generation)
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff generation changed")
        return self.get_worker_handoff(worker_id)

    def release_worker_handoff(self, worker_id: str, generation: int, expected_pause_reason: str,
                               *, after_stop: bool = False, disposition: str = "released") -> dict:
        """CAS a paused handoff back to running; the row completes with disposition released.

        Before the source stop (H5) release is refused once a target, a stop intent, or
        a stop proof was recorded. After the transfer (H6a) the target runtime is kept
        and the disposition is released_to_target.
        """
        with self._immediate():
            handoff = self.get_worker_handoff(worker_id)
            if (not handoff or handoff["generation"] != generation or handoff["completed_at"]
                    or not expected_pause_reason or handoff["pause_reason"] != expected_pause_reason):
                raise RuntimeError("handoff generation, phase, or pause changed")
            evidence = json.loads(handoff["evidence_json"])
            stopped = (handoff["phase"] != "claimed" or bool(evidence.get("stop_intent"))
                       or bool(evidence.get("source_stop_proof")))
            if not after_stop and (stopped or evidence.get("target_id") or evidence.get("bootstrap_intent")):
                raise RuntimeError("handoff target or source stop recorded; release refused")
            if disposition not in {"released", "released_to_target"}:
                raise ValueError("handoff release disposition invalid")
            source = json.loads(handoff["snapshot_json"])
            target = evidence.get("target_runtime")
            # H6a: once the target runtime is registered the worker keeps it, as
            # complete_worker_handoff expects.
            expected = ({**source, **target, "status": "handoff_pending", "finished_at": None}
                        if isinstance(target, dict) else {**source, "status": "handoff_pending"})
            current = self.get_worker(worker_id)
            if current is None or self._snapshot(current) != expected:
                raise RuntimeError("handoff source runtime changed")
            evidence.update({"disposition": disposition, "released_from_phase": handoff["phase"],
                             "released_reason": expected_pause_reason})
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET phase='released', evidence_json=?, completed_at=datetime('now'), "
                "updated_at=datetime('now') WHERE worker_id=? AND generation=? AND completed_at IS NULL "
                "AND pause_reason=?",
                (json.dumps(evidence, sort_keys=True), worker_id, generation, expected_pause_reason),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff generation or phase changed")
            worker = self._conn.execute(
                "UPDATE workers SET status='running' WHERE id=? AND status='handoff_pending'", (worker_id,))
            if worker.rowcount != 1:
                raise RuntimeError("handoff worker runtime changed")
        return self.get_worker_handoff(worker_id)

    def complete_worker_handoff(self, worker_id: str, generation: int, previous_phase: str) -> dict:
        if previous_phase != "continuation_dispatched":
            raise ValueError("handoff completion requires dispatched continuation")
        with self._immediate():
            handoff = self.get_worker_handoff(worker_id)
            if not handoff or handoff["generation"] != generation or handoff["phase"] != previous_phase:
                raise RuntimeError("handoff generation or phase changed")
            evidence = json.loads(handoff["evidence_json"])
            target = evidence.get("target_runtime")
            if not evidence.get("assignment_finished") or not isinstance(target, dict):
                raise RuntimeError("handoff completion requires verified target and assignment finish")
            source = json.loads(handoff["snapshot_json"])
            current = self.get_worker(worker_id)
            expected = {**source, **target, "status": "handoff_pending", "finished_at": None}
            if current is None or self._snapshot(current) != expected:
                raise RuntimeError("handoff target runtime or job snapshot changed")
            cursor = self._conn.execute(
                "UPDATE worker_handoffs SET phase='completed', completed_at=datetime('now'), updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND phase=? AND completed_at IS NULL AND pause_reason IS NULL",
                (worker_id, generation, previous_phase),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("handoff generation or phase changed")
            worker = self._conn.execute("UPDATE workers SET status='running' WHERE id=? AND status='handoff_pending'", (worker_id,))
            if worker.rowcount != 1:
                raise RuntimeError("handoff worker runtime changed")
        return self.get_worker_handoff(worker_id)

    def replace_worker_runtime(self, worker_id: str, generation: int, previous_phase: str, *, client: str, model: str, native_session_id: str, tmux_session: str, machine: str | None) -> dict:
        if previous_phase != "owner_transferred":
            raise ValueError("runtime replacement requires owner_transferred phase")
        try:
            uuid.UUID(native_session_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("target native session must be a valid UUID") from exc
        if client not in {"claude", "codex"} or not all(isinstance(value, str) and value for value in (model, tmux_session)):
            raise ValueError("target client, model, and tmux required")
        if machine == "":
            raise ValueError("target host must be local or nonempty")
        with self._immediate():
            handoff = self.get_worker_handoff(worker_id)
            if not handoff or handoff["generation"] != generation or handoff["completed_at"]:
                raise RuntimeError("handoff generation changed")
            if handoff["phase"] != previous_phase or handoff["pause_reason"]:
                raise RuntimeError("handoff phase changed")
            source = json.loads(handoff["snapshot_json"])
            # H6: after a post-stop retry the source's own client may be the target,
            # always with a new native session on the same host.
            if native_session_id == source["native_session_id"] or machine != source["machine"]:
                raise RuntimeError("target runtime identity invalid")
            current = self.get_worker(worker_id)
            expected = {**source, "status": "handoff_pending"}
            if current is None or self._snapshot(current) != expected:
                raise RuntimeError("source runtime or job snapshot changed")
            cursor = self._conn.execute(
                "UPDATE workers SET client=?, model=?, native_session_id=?, tmux_session=?, machine=?, "
                "finished_at=NULL WHERE id=? AND status='handoff_pending'",
                (client, model, native_session_id, tmux_session, machine, worker_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("worker runtime changed")
            target = {"client": client, "model": model, "native_session_id": native_session_id,
                      "tmux_session": tmux_session, "machine": machine}
            evidence = {**json.loads(handoff["evidence_json"]), "target_runtime": target}
            self._conn.execute(
                "UPDATE worker_handoffs SET evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND phase='owner_transferred'",
                (json.dumps(evidence, sort_keys=True), worker_id, generation),
            )
        return self.get_worker(worker_id)

    def record_native_quota_verdict(self, worker_id: str, generation: int, verdict: dict | None, *, snapshot: dict) -> None:
        """Persist a bounded native-log cursor for restart reconciliation."""
        if type(generation) is not int or generation <= 0:
            raise ValueError("quota generation must be positive integer")
        if not isinstance(verdict, dict) or verdict.get("native_session_id") != snapshot.get("native_session_id") or verdict.get("client") != snapshot.get("client") or str(verdict.get("generation")) != str(generation):
            raise ValueError("quota verdict native identity or generation mismatch")
        with self._immediate():
            worker = self._exact_worker(snapshot)
            if worker["id"] != worker_id or self._active_handoff(worker_id):
                raise RuntimeError("quota source runtime changed or handoff active")
            prior = self._conn.execute(
                "SELECT generation, cursor, verdict, evidence_json FROM worker_quota_evidence WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            cursor = int((verdict or {}).get("cursor", 0))
            if cursor < 0 or prior and (generation < int(prior["generation"]) or generation == int(prior["generation"]) and cursor < prior["cursor"]):
                raise RuntimeError("stale quota evidence")
            persisted = dict(verdict)
            if prior and prior["generation"] == generation and prior["verdict"] == "exhausted" and verdict.get("state") == "exhausted":
                old = json.loads(prior["evidence_json"])
                if (old.get("client") == verdict.get("client")
                        and old.get("native_session_id") == verdict.get("native_session_id")
                        and old.get("file_identity")
                        and old.get("file_identity") == verdict.get("file_identity")
                        and isinstance(old.get("handoff_rejection"), str)):
                    persisted["handoff_rejection"] = old["handoff_rejection"]
            self._conn.execute(
                "INSERT INTO worker_quota_evidence(worker_id, generation, cursor, event_key, verdict, evidence_json) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(worker_id) DO UPDATE SET "
                "generation=excluded.generation, cursor=excluded.cursor, event_key=excluded.event_key, "
                "verdict=excluded.verdict, evidence_json=excluded.evidence_json, updated_at=datetime('now')",
                (worker_id, generation, cursor, (verdict or {}).get("event_key"),
                 verdict.get("state", "ordinary"), json.dumps(persisted, sort_keys=True)),
            )

    def get_native_quota_evidence(self, worker_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT generation, evidence_json FROM worker_quota_evidence WHERE worker_id=?",
            (worker_id,),
        ).fetchone()
        if row is None:
            return None
        return {"generation": row["generation"], "verdict": json.loads(row["evidence_json"])}

    def record_worker_handoff_quota_progress(
        self, worker_id: str, generation: int, progress: dict, *,
        snapshot: dict, owner_nonce: str, expected_pause_reason: str | None,
    ) -> None:
        """Persist only a forward native scan under the claimed effect owner."""
        if (not isinstance(progress, dict) or progress.get("state") != "unknown"
                or progress.get("reason") != "native_quota_log_scan_budget_exceeded"
                or not isinstance(progress.get("reader_state"), dict)
                or progress["reader_state"].get("cursor") != progress.get("cursor")
                or not progress.get("file_identity") or not owner_nonce):
            raise ValueError("bounded native scan progress required")
        with self._immediate():
            handoff = self.get_worker_handoff(worker_id)
            if (not handoff or handoff["generation"] != generation
                    or handoff["completed_at"] or handoff["phase"] != "claimed"
                    or handoff["pause_reason"] != expected_pause_reason):
                raise RuntimeError("handoff generation or phase changed")
            worker = self._exact_worker(snapshot)
            source = json.loads(handoff["snapshot_json"])
            if worker["id"] != worker_id or self._snapshot(snapshot) != {**source, "status": "handoff_pending"}:
                raise RuntimeError("handoff source runtime changed")
            evidence = json.loads(handoff["evidence_json"])
            owner = evidence.get("reconcile_owner")
            if (not isinstance(owner, dict) or owner.get("nonce") != owner_nonce
                    or owner.get("pid") != os.getpid()
                    or owner.get("started") != psutil.Process(os.getpid()).create_time()
                    or any(key.endswith("_intent") and value for key, value in evidence.items())):
                raise RuntimeError("handoff effect owner or intent changed")
            original = evidence.get("quota_verdict")
            prior = evidence.get("quota_progress") or original
            if (not isinstance(original, dict) or not isinstance(prior, dict)
                    or original.get("file_identity") != progress["file_identity"]
                    or progress.get("client") != source.get("client")
                    or progress.get("native_session_id") != source.get("native_session_id")
                    or progress["reader_state"].get("workspace_path") != source.get("workspace_path")
                    or str(progress.get("generation")) != str(generation)
                    or type(progress.get("cursor")) is not int
                    or progress["cursor"] <= prior.get("cursor", -1)):
                raise RuntimeError("stale or foreign native scan progress")
            evidence["quota_progress"] = progress
            self._conn.execute(
                "UPDATE worker_handoffs SET evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=? AND phase='claimed' AND completed_at IS NULL",
                (json.dumps(evidence, sort_keys=True), worker_id, generation),
            )

    def record_native_handoff_rejection(
        self, worker_id: str, generation: int, reason: str, *, snapshot: dict,
    ) -> bool:
        """Deduplicate a pre-claim rejection without replacing trusted quota proof."""
        if type(generation) is not int or generation <= 0 or not isinstance(reason, str) or not reason:
            raise ValueError("handoff rejection generation and reason required")
        with self._immediate():
            worker = self._exact_worker(snapshot)
            if worker["id"] != worker_id or self._active_handoff(worker_id):
                raise RuntimeError("handoff rejection source runtime changed")
            row = self._conn.execute(
                "SELECT generation, verdict, evidence_json FROM worker_quota_evidence WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            if row is None or row["generation"] != generation or row["verdict"] != "exhausted":
                raise RuntimeError("trusted exhausted quota evidence missing")
            evidence = json.loads(row["evidence_json"])
            if (evidence.get("native_session_id") != snapshot.get("native_session_id")
                    or evidence.get("client") != snapshot.get("client")):
                raise RuntimeError("trusted quota native identity changed")
            if evidence.get("handoff_rejection") == reason:
                return False
            evidence["handoff_rejection"] = reason
            self._conn.execute(
                "UPDATE worker_quota_evidence SET evidence_json=?, updated_at=datetime('now') "
                "WHERE worker_id=? AND generation=?",
                (json.dumps(evidence, sort_keys=True), worker_id, generation),
            )
            return True

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

    def get_recent_workers(self, lookback_hours: int = 1) -> list[dict]:
        """Running workers + recently-finished within lookback window.

        Used by heartbeat to find workers alive even if incorrectly marked
        completed by check_workers() due to transient tmux check failures.
        """
        rows = self._conn.execute(
            """SELECT * FROM workers
               WHERE status = 'running'
               OR (finished_at IS NOT NULL AND finished_at > datetime('now', ?))""",
            (f"-{lookback_hours} hours",)
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

    def get_events_for_worker(self, worker_id: str, event_type: str | None = None) -> list[dict]:
        if event_type:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE worker_id = ? AND event_type = ? ORDER BY id",
                (worker_id, event_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE worker_id = ? ORDER BY id",
                (worker_id,),
            ).fetchall()
        return [dict(r) for r in rows]
