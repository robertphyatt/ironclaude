"""Controller-only SQLite capability grants for private workspace handoff CLI calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import uuid

import psutil


class WorkspaceHandoffAuthorityError(RuntimeError):
    pass


def mutation_owner_status(identity: object) -> str:
    """Classify workspace_mutations.process_identity "<pid>:<start epoch seconds>" (H11).

    The start time is int(psutil create_time()), the same integer workspace-manager
    writes from `ps -o lstart=` in UTC. A bare "<pid>" is the legacy form: it blocks
    while that PID exists. Anything else is unproved and blocks.
    """
    if not isinstance(identity, str):
        return "unproved"
    pid_text, separator, start_text = identity.partition(":")
    if not pid_text.isdigit() or (separator and not start_text.isdigit()):
        return "unproved"
    pid = int(pid_text)
    if pid <= 0:
        return "unproved"
    try:
        started = int(psutil.Process(pid).create_time())
    except psutil.NoSuchProcess:
        return "dead"
    except (psutil.Error, OSError, OverflowError, ValueError):
        return "unproved"
    if not separator:
        return "unproved"
    return "live" if started == int(start_text) else "dead"


@dataclass(frozen=True)
class WorkspaceHandoffGrant:
    capability_token: str
    workspace_guid: str
    generation: int
    phase: str


class WorkspaceHandoffAuthority:
    """Not a worker tool: callers are trusted Commander controllers on assignment host."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path or os.environ.get(
            "WORKSPACE_MANAGER_DB_PATH", Path.home() / ".claude/ironclaude-workspaces.db"))

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.is_file():
            raise WorkspaceHandoffAuthorityError("workspace-manager database does not exist")
        try:
            con = sqlite3.connect(f"{self._db_path.resolve().as_uri()}?mode=rw", uri=True)
        except sqlite3.Error as exc:
            raise WorkspaceHandoffAuthorityError("workspace-manager database is unavailable") from exc
        con.row_factory = sqlite3.Row
        return con

    def grant(self, *, workspace_guid: str, generation: int, repository_identity: str, worker_id: str,
              worktree_path: str, branch: str, base_commit: str, current_head: str,
              integration_target: str, source_owner_session_id: str, expires_in_seconds: int = 300,
              capability_token: str | None = None) -> WorkspaceHandoffGrant:
        if not isinstance(generation, int) or generation <= 0 or expires_in_seconds <= 0:
            raise WorkspaceHandoffAuthorityError("handoff generation and expiry must be positive")
        if isinstance(generation, bool) or isinstance(expires_in_seconds, bool):
            raise WorkspaceHandoffAuthorityError("handoff generation and expiry must be positive")
        try:
            uuid.UUID(workspace_guid); uuid.UUID(source_owner_session_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise WorkspaceHandoffAuthorityError("handoff identifiers must be UUIDs") from exc
        fields = (repository_identity, worker_id, worktree_path, branch, base_commit, current_head,
                  integration_target, source_owner_session_id)
        if any(not isinstance(value, str) or not value for value in fields):
            raise WorkspaceHandoffAuthorityError("handoff scope contains empty fields")
        token = capability_token or secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).isoformat()
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            mutation = con.execute(
                "SELECT nonce, process_identity FROM workspace_mutations WHERE workspace_guid = ?",
                (workspace_guid,)).fetchone()
            if mutation and not (
                    mutation_owner_status(mutation["process_identity"]) == "dead"
                    and con.execute(
                        "DELETE FROM workspace_mutations WHERE workspace_guid=? AND nonce=? "
                        "AND process_identity=?",
                        (workspace_guid, mutation["nonce"], mutation["process_identity"]),
                    ).rowcount == 1):
                raise WorkspaceHandoffAuthorityError("assignment mutation is active")
            assignment = con.execute("""SELECT 1 FROM assignments WHERE workspace_guid=? AND repository_identity=?
                AND worker_id=? AND worktree_path=? AND branch=? AND base_commit=? AND current_head=?
                AND integration_target=? AND owner_session_id=? AND lifecycle_status='active'""",
                (workspace_guid, repository_identity, worker_id, worktree_path, branch, base_commit, current_head,
                 integration_target, source_owner_session_id)).fetchone()
            if not assignment:
                raise WorkspaceHandoffAuthorityError("assignment does not match handoff source scope")
            existing = con.execute("SELECT * FROM workspace_handoffs WHERE workspace_guid = ?", (workspace_guid,)).fetchone()
            if existing and existing["phase"] != "finished":
                same_scope = all(existing[key] == value for key, value in {
                    "generation": generation, "repository_identity": repository_identity, "worker_id": worker_id,
                    "worktree_path": worktree_path, "branch": branch, "base_commit": base_commit,
                    "current_head": current_head, "integration_target": integration_target,
                    "source_owner_session_id": source_owner_session_id,
                }.items())
                try:
                    valid_expiry = datetime.fromisoformat(existing["grant_expires_at"]).astimezone(timezone.utc) > datetime.now(timezone.utc)
                except (TypeError, ValueError):
                    valid_expiry = False
                if (same_scope and valid_expiry and existing["phase"] == "granted" and capability_token
                        and existing["capability_token_hash"] == hashlib.sha256(token.encode()).hexdigest()):
                    con.commit()
                    return WorkspaceHandoffGrant(token, workspace_guid, generation, "granted")
                raise WorkspaceHandoffAuthorityError("unfinished handoff already exists")
            con.execute("""INSERT INTO workspace_handoffs (workspace_guid,generation,capability_token_hash,grant_expires_at,
                source_owner_session_id,target_owner_session_id,repository_identity,worker_id,worktree_path,branch,
                base_commit,current_head,integration_target,phase,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'granted',datetime('now'),datetime('now'))
                ON CONFLICT(workspace_guid) DO UPDATE SET generation=excluded.generation,
                capability_token_hash=excluded.capability_token_hash,grant_expires_at=excluded.grant_expires_at,
                source_owner_session_id=excluded.source_owner_session_id,target_owner_session_id=NULL,
                repository_identity=excluded.repository_identity,worker_id=excluded.worker_id,
                worktree_path=excluded.worktree_path,branch=excluded.branch,base_commit=excluded.base_commit,
                current_head=excluded.current_head,integration_target=excluded.integration_target,phase='granted',updated_at=datetime('now')""",
                (workspace_guid, generation, hashlib.sha256(token.encode()).hexdigest(), expires,
                 source_owner_session_id, None, repository_identity, worker_id, worktree_path, branch, base_commit,
                 current_head, integration_target))
            con.commit()
        except sqlite3.Error as exc:
            con.rollback()
            raise WorkspaceHandoffAuthorityError("workspace handoff grant database failure") from exc
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return WorkspaceHandoffGrant(token, workspace_guid, generation, "granted")

    def authorize_target(self, *, workspace_guid: str, generation: int, capability_token: str,
                         source_owner_session_id: str, target_owner_session_id: str) -> None:
        """Trusted controller calls only after independently observed source quiescence."""
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise WorkspaceHandoffAuthorityError("target authorization generation is invalid")
        try:
            uuid.UUID(workspace_guid); uuid.UUID(source_owner_session_id); uuid.UUID(target_owner_session_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise WorkspaceHandoffAuthorityError("target authorization identifiers must be UUIDs") from exc
        if not capability_token or source_owner_session_id == target_owner_session_id:
            raise WorkspaceHandoffAuthorityError("target authorization scope is invalid")
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            changed = con.execute("""UPDATE workspace_handoffs SET target_owner_session_id=?, updated_at=datetime('now')
                WHERE workspace_guid=? AND generation=? AND capability_token_hash=?
                AND source_owner_session_id=? AND phase='begun' AND target_owner_session_id IS NULL""",
                (target_owner_session_id, workspace_guid, generation,
                 hashlib.sha256(capability_token.encode()).hexdigest(), source_owner_session_id)).rowcount
            if changed != 1:
                raise WorkspaceHandoffAuthorityError("target authorization scope or phase changed")
            con.commit()
        except sqlite3.Error as exc:
            con.rollback(); raise WorkspaceHandoffAuthorityError("workspace handoff target database failure") from exc
        except Exception:
            con.rollback(); raise
        finally:
            con.close()

    def readback(self, *, workspace_guid: str) -> dict[str, object]:
        """Safe held-generation state for controller reconciliation; excludes token/hash/expiry."""
        con = self._connect()
        try:
            row = con.execute("""SELECT workspace_guid,generation,source_owner_session_id,target_owner_session_id,
                repository_identity,worker_id,worktree_path,branch,base_commit,current_head,integration_target,phase
                FROM workspace_handoffs WHERE workspace_guid=?""", (workspace_guid,)).fetchone()
            if row is None:
                raise WorkspaceHandoffAuthorityError("handoff state not found")
            return {"protocol_version": 1, **dict(row)}
        finally:
            con.close()
