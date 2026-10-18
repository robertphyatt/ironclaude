"""Private, transactional PM state transfer between verified native root sessions.

The controller supplies observed UUIDs and a pre-stop snapshot. This module never
discovers a root from environment variables or creates a missing state database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path


_COLUMNS = {
    "sessions": ("terminal_session", "professional_mode", "workflow_stage", "active_skill", "brainstorming_active", "plan_name", "plan_json", "current_wave", "review_pending", "review_block_count", "plan_lineage", "inherit_review", "circuit_breaker", "memory_search_required", "testing_theatre_checked", "project_hash", "updated_at"),
    "wave_tasks": ("id", "terminal_session", "task_id", "wave_number", "task_name", "description", "allowed_files", "status", "created_at", "updated_at"),
    "plan_history": ("id", "terminal_session", "plan_name", "design_file", "completed_tasks", "total_tasks", "retreat_reason", "created_at"),
    "audit_log": ("id", "terminal_session", "actor", "action", "old_value", "new_value", "context", "created_at"),
    "registered_designs": ("design_file", "registered_at", "terminal_session", "consumed"),
    "subagent_sessions": ("child_session", "parent_session", "task_number", "created_at"),
    "pending_subagent_parents": ("id", "parent_session", "task_number", "created_at"),
    "review_grades": ("id", "terminal_session", "wave_number", "task_ids", "grade", "task_boundary", "created_at"),
    "tier_up_reviews": ("id", "terminal_session", "plan_lineage", "plan_hash", "reviewer_model", "verdict", "created_at"),
}
_HISTORY_EXTRA = ("plan_lineage", "repository_identity")
_STAGES = {"idle", "brainstorming", "debugging", "design_ready", "design_marked_for_use", "plan_ready", "plan_marked_for_use", "final_plan_prep", "executing", "reviewing", "plan_interrupted"}
_TASK_STATES = {"pending", "in_progress", "submitted", "review_passed", "review_failed"}
_CANONICAL = {"SOLID", "HAS-ISSUES", "top-tier-self"}
_ACTION = "worker_pm_handoff_prepared"


def _connect(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=rw"
    try:
        db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        return db
    except sqlite3.OperationalError as exc:
        raise ValueError("PM schema database missing or inaccessible") from exc


def _schema(db: sqlite3.Connection) -> None:
    for table, columns in _COLUMNS.items():
        actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        allowed = (actual == set(columns) or
                   (table == "plan_history" and actual == set(columns) | set(_HISTORY_EXTRA)))
        if not allowed:
            raise ValueError(f"unsupported PM schema: {table}")
        if table == "plan_history" and actual == set(columns) | set(_HISTORY_EXTRA):
            metadata = {row[1]: (row[2].upper(), row[3]) for row in db.execute("PRAGMA table_info(plan_history)")}
            if metadata["plan_lineage"] != ("INTEGER", 0) or metadata["repository_identity"] != ("TEXT", 0):
                raise ValueError("unsupported PM schema: plan_history metadata")
    trigger = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='prevent_duplicate_blind_tier_up_review'").fetchone()
    if not trigger or "BEFORE INSERT ON tier_up_reviews" not in trigger[0]:
        raise ValueError("unsupported PM schema: blind-review trigger missing")


def _uuid(value: str) -> None:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("native root must be a canonical UUID") from exc


def _rows(db: sqlite3.Connection, table: str, where: str, args: tuple, order: str) -> list[dict]:
    columns = _COLUMNS[table]
    if table == "plan_history" and {row[1] for row in db.execute("PRAGMA table_info(plan_history)")} == set(columns) | set(_HISTORY_EXTRA):
        columns += _HISTORY_EXTRA
    return [dict(row) for row in db.execute(f"SELECT {','.join(columns)} FROM {table} WHERE {where} ORDER BY {order}", args)]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _capture(db: sqlite3.Connection, source: str, project_hash: str, *, after_transfer: bool = False) -> dict:
    session = _rows(db, "sessions", "terminal_session=?", (source,), "terminal_session")
    if len(session) != 1 or session[0]["project_hash"] != project_hash:
        raise ValueError("source root or project mismatch")
    s = session[0]
    if s["professional_mode"] != "on" or s["workflow_stage"] not in _STAGES:
        raise ValueError("source PM mode or stage unsupported")
    for field in ("current_wave", "review_pending", "review_block_count", "plan_lineage", "inherit_review", "circuit_breaker", "memory_search_required", "testing_theatre_checked", "brainstorming_active"):
        if not isinstance(s[field], int) or s[field] < 0 or (field not in ("current_wave", "review_block_count", "plan_lineage") and s[field] > 1):
            raise ValueError(f"source {field} invalid")
    try:
        plan = json.loads(s["plan_json"]) if s["plan_json"] is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError("source plan_json invalid") from exc
    if plan is not None:
        if (not isinstance(plan, dict) or not isinstance(plan.get("design_file"), str)
                or not isinstance(plan.get("name"), str) or plan["name"] != s["plan_name"]
                or not isinstance(plan.get("goal"), str) or not isinstance(plan.get("tasks"), list)):
            raise ValueError("source plan shape invalid")
        plan_tasks = plan["tasks"]
        if (not all(isinstance(item, dict) and type(item.get("id")) is int and item["id"] > 0
                    and isinstance(item.get("allowed_files"), list) for item in plan_tasks)
                or len({item["id"] for item in plan_tasks}) != len(plan_tasks)):
            raise ValueError("source plan tasks invalid")
    tasks = _rows(db, "wave_tasks", "terminal_session=?", (source,), "id")
    if len({t["task_id"] for t in tasks}) != len(tasks) or any(t["status"] not in _TASK_STATES for t in tasks):
        raise ValueError("source task state invalid")
    if plan and (any(t["task_id"] not in {item["id"] for item in plan["tasks"]} or t["wave_number"] < 1 for t in tasks)
                 or (tasks and s["current_wave"] < 1)):
        raise ValueError("source task plan or wave mismatch")
    if tasks and plan is None:
        raise ValueError("source tasks without plan")
    if s["workflow_stage"] in ("executing", "reviewing", "final_plan_prep") and plan is None:
        raise ValueError("source execution stage without plan")
    if s["review_pending"] and not any(t["status"] == "submitted" and t["wave_number"] == s["current_wave"] for t in tasks):
        raise ValueError("source review flag lacks submitted current task")
    if s["review_pending"] and s["workflow_stage"] not in ("executing", "reviewing"):
        raise ValueError("source review flag outside execution stage")
    history = _rows(db, "plan_history", "terminal_session=?", (source,), "id")
    grades = _rows(db, "review_grades", "terminal_session=?", (source,), "id")
    reviews = _rows(db, "tier_up_reviews", "terminal_session=? AND plan_lineage=?", (source, s["plan_lineage"]), "id")
    canonical = [r for r in reviews if r["verdict"] in _CANONICAL]
    if len(canonical) > 1:
        raise ValueError("source lineage has multiple canonical reviews")
    design = plan["design_file"] if plan else None
    registrations = _rows(db, "registered_designs", "design_file=?", (design,), "design_file") if design else _rows(db, "registered_designs", "terminal_session=?", (source,), "design_file")
    if plan is None and len(registrations) > 1:
        raise ValueError("source current design registration ambiguous")
    if not after_transfer and any(r["terminal_session"] != source for r in registrations):
        raise ValueError("source design registration owner changed")
    if not after_transfer and s["workflow_stage"] in ("design_marked_for_use", "plan_ready", "plan_marked_for_use", "final_plan_prep", "executing", "reviewing", "plan_interrupted") and (len(registrations) != 1 or registrations[0]["consumed"] != 1):
        raise ValueError("source consumed design registration missing")
    return {"source": source, "project_hash": project_hash, "session": s, "tasks": tasks, "history": history, "grades": grades, "reviews": reviews, "registrations": registrations}


def snapshot_pm_state(db_path: str | Path, source_uuid: str, project_hash: str) -> dict:
    """Capture immutable source evidence before the old native process stops."""
    _uuid(source_uuid)
    if not project_hash:
        raise ValueError("project hash required")
    db = _connect(db_path)
    try:
        db.execute("BEGIN")
        _schema(db)
        snapshot = _capture(db, source_uuid, project_hash)
        db.commit()
    finally:
        db.close()
    snapshot["digest"] = _digest(snapshot)
    return snapshot


def _target_state(db: sqlite3.Connection, target: str) -> dict:
    tables = {name: _rows(db, name, "terminal_session=?", (target,), "id") for name in ("wave_tasks", "plan_history", "review_grades", "tier_up_reviews")}
    session = _rows(db, "sessions", "terminal_session=?", (target,), "terminal_session")
    return {"session": session[0] if len(session) == 1 else None, **tables}


def _insert_copy(db: sqlite3.Connection, table: str, row: dict, target: str, **changes: object) -> int:
    columns = _COLUMNS[table] + (_HISTORY_EXTRA if table == "plan_history" and all(key in row for key in _HISTORY_EXTRA) else ())
    values = {key: changes.get(key, row[key]) for key in columns if key != "id"}
    values["terminal_session"] = target
    db.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _audit_record(db: sqlite3.Connection, source: str, target: str, generation: int) -> dict | None:
    rows = db.execute("SELECT context FROM audit_log WHERE terminal_session=? AND action=?", (target, _ACTION)).fetchall()
    matches = []
    for row in rows:
        try:
            context = json.loads(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("PM handoff audit corrupt") from exc
        if context.get("source") == source and context.get("target") == target and context.get("generation") == generation:
            matches.append(context)
    if len(matches) > 1:
        raise ValueError("PM handoff audit duplicated")
    return matches[0] if matches else None


def _verify_replay(db: sqlite3.Connection, snapshot: dict, target: str, generation: int, record: dict) -> dict:
    if record.get("snapshot_digest") != snapshot["digest"]:
        raise ValueError("source snapshot changed since PM transfer")
    current = _capture(db, snapshot["source"], snapshot["project_hash"], after_transfer=True)
    current["registrations"] = snapshot["registrations"]
    if _digest(current) != snapshot["digest"]:
        raise ValueError("source historical state changed since PM transfer")
    for registration in snapshot["registrations"]:
        actual = _rows(db, "registered_designs", "design_file=?", (registration["design_file"],), "design_file")
        if len(actual) != 1 or actual[0] != {**registration, "terminal_session": target}:
            raise ValueError("design registration changed since PM transfer")
    if _digest(_target_state(db, target)) != record.get("target_digest"):
        raise ValueError("target PM state changed since transfer")
    return record["result"]


def prepare_pm_handoff(db_path: str | Path, snapshot: dict, target_uuid: str, generation: int) -> dict:
    """Prepare fresh PM root atomically; exact unchanged replay returns stored proof."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("source"), str):
        raise ValueError("source snapshot required")
    _uuid(snapshot["source"])
    _uuid(target_uuid)
    if snapshot["source"] == target_uuid or type(generation) is not int or generation < 1:
        raise ValueError("distinct target UUID and positive generation required")
    expected_digest = snapshot.get("digest")
    if not isinstance(expected_digest, str) or _digest({k: v for k, v in snapshot.items() if k != "digest"}) != expected_digest:
        raise ValueError("source snapshot digest mismatch")
    db = _connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        old = _audit_record(db, snapshot["source"], target_uuid, generation)
        if old:
            result = _verify_replay(db, snapshot, target_uuid, generation, old)
            db.commit()
            return result
        # A prior generation or another source cannot claim this target.
        if db.execute("SELECT 1 FROM audit_log WHERE terminal_session=? AND action=?", (target_uuid, _ACTION)).fetchone():
            raise ValueError("target PM root already transferred")
        current = _capture(db, snapshot["source"], snapshot["project_hash"])
        if _digest(current) != expected_digest:
            raise ValueError("source snapshot changed before PM prepare")
        target_state = _target_state(db, target_uuid)
        target_session = target_state["session"]
        if target_session is None or target_session["professional_mode"] != "on" or target_session["project_hash"] != snapshot["project_hash"]:
            raise ValueError("target PM root or project mismatch")
        clean_fields = ("current_wave", "review_pending", "review_block_count", "plan_lineage", "inherit_review", "circuit_breaker", "memory_search_required", "testing_theatre_checked", "brainstorming_active")
        if target_session["workflow_stage"] != "idle" or target_session["plan_json"] is not None or target_session["plan_name"] is not None or any(target_session[field] != 0 for field in clean_fields) or target_session["active_skill"] or any(target_state[t] for t in ("wave_tasks", "plan_history", "review_grades", "tier_up_reviews")):
            raise ValueError("target PM root already has work")
        if db.execute("SELECT 1 FROM subagent_sessions WHERE child_session=? OR parent_session=?", (target_uuid,target_uuid)).fetchone() or db.execute("SELECT 1 FROM pending_subagent_parents WHERE parent_session=?", (target_uuid,)).fetchone():
            raise ValueError("target PM root has child bindings")
        if db.execute("SELECT 1 FROM registered_designs WHERE terminal_session=?", (target_uuid,)).fetchone():
            raise ValueError("target PM root already owns a design")
        source = snapshot["session"]
        stage = source["workflow_stage"]
        interrupted_review = stage in ("executing", "reviewing") and bool(source["review_pending"]) and any(t["status"] == "submitted" and t["wave_number"] == source["current_wave"] for t in snapshot["tasks"])
        next_stage = "executing" if interrupted_review else stage
        fields = {k: source[k] for k in _COLUMNS["sessions"] if k not in ("terminal_session", "project_hash", "active_skill", "testing_theatre_checked", "workflow_stage", "review_pending")}
        fields.update(workflow_stage=next_stage, active_skill=None, testing_theatre_checked=0, review_pending=0 if interrupted_review else source["review_pending"])
        db.execute(f"UPDATE sessions SET {','.join(k+'=?' for k in fields)} WHERE terminal_session=?", (*fields.values(), target_uuid))
        ids = {"tasks": [], "history": [], "grades": [], "reviews": [], "registrations": []}
        for row in snapshot["tasks"]:
            status = "pending" if row["status"] in ("in_progress", "submitted") else row["status"]
            ids["tasks"].append([row["id"], _insert_copy(db, "wave_tasks", row, target_uuid, status=status)])
        design_file = snapshot["registrations"][0]["design_file"] if snapshot["registrations"] else None
        for row in snapshot["history"]:
            if row["design_file"] == design_file:
                ids["history"].append([row["id"], _insert_copy(db, "plan_history", row, target_uuid)])
        passed = {t["task_id"] for t in snapshot["tasks"] if t["status"] == "review_passed"}
        for row in snapshot["grades"]:
            try:
                members = json.loads(row["task_ids"])
            except (TypeError, ValueError) as exc:
                raise ValueError("source review grade task_ids invalid") from exc
            if not isinstance(members, list) or any(not isinstance(task_id, int) for task_id in members):
                raise ValueError("source review grade task_ids invalid")
            if members and all(member in passed for member in members):
                ids["grades"].append([row["id"], _insert_copy(db, "review_grades", row, target_uuid)])
        for row in snapshot["reviews"]:
            ids["reviews"].append([row["id"], _insert_copy(db, "tier_up_reviews", row, target_uuid)])
        for row in snapshot["registrations"]:
            changed = db.execute("UPDATE registered_designs SET terminal_session=? WHERE design_file=? AND terminal_session=? AND registered_at=? AND consumed=?", (target_uuid,row["design_file"],snapshot["source"],row["registered_at"],row["consumed"])).rowcount
            if changed != 1:
                raise ValueError("source design registration ownership changed")
            ids["registrations"].append(row)
        target_digest = _digest(_target_state(db, target_uuid))
        result = {"source": snapshot["source"], "target": target_uuid, "generation": generation, "snapshot_digest": expected_digest, "target_digest": target_digest, "stage": next_stage, "copied_ids": ids}
        record = {"source": snapshot["source"], "target": target_uuid, "generation": generation, "snapshot_digest": expected_digest, "target_digest": target_digest, "snapshot": snapshot, "result": result}
        db.execute("INSERT INTO audit_log (terminal_session,actor,action,old_value,new_value,context) VALUES (?,?,?,?,?,?)", (target_uuid,"worker_handoff",_ACTION,snapshot["source"],target_uuid,json.dumps(record,sort_keys=True)))
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def readback_pm_handoff(db_path: str | Path, source_uuid: str, target_uuid: str, generation: int) -> dict | None:
    """Return durable proof only while source and prepared target remain unchanged."""
    _uuid(source_uuid)
    _uuid(target_uuid)
    if type(generation) is not int or generation < 1:
        raise ValueError("positive integer generation required")
    db = _connect(db_path)
    try:
        db.execute("BEGIN")
        _schema(db)
        record = _audit_record(db, source_uuid, target_uuid, generation)
        if record is None:
            return None
        result = _verify_replay(db, record["snapshot"], target_uuid, generation, record)
        db.commit()
        return result
    finally:
        db.close()
