import sqlite3
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import psutil
import pytest

from ironclaude.workspace_handoff_authority import (
    WorkspaceHandoffAuthority, WorkspaceHandoffAuthorityError, mutation_owner_status,
)

_LIVE_START = int(psutil.Process(os.getpid()).create_time())


def _db(tmp_path):
    path = tmp_path / "workspace.db"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE assignments (workspace_guid TEXT PRIMARY KEY, repository_identity TEXT, worker_id TEXT,
      worktree_path TEXT, branch TEXT, base_commit TEXT, current_head TEXT, integration_target TEXT,
      owner_session_id TEXT, lifecycle_status TEXT);
    CREATE TABLE workspace_handoffs (workspace_guid TEXT PRIMARY KEY, generation INTEGER, capability_token_hash TEXT,
      grant_expires_at TEXT, source_owner_session_id TEXT, target_owner_session_id TEXT, repository_identity TEXT,
      worker_id TEXT, worktree_path TEXT, branch TEXT, base_commit TEXT, current_head TEXT, integration_target TEXT,
      phase TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE workspace_mutations (workspace_guid TEXT PRIMARY KEY, operation TEXT, nonce TEXT, process_identity TEXT, acquired_at TEXT);
    INSERT INTO assignments VALUES ('11111111-1111-4111-8111-111111111111','repo','worker','/wt','ironclaude/w','base','head','main','22222222-2222-4222-8222-222222222222','active');
    """)
    con.commit(); con.close()
    return path


SCOPE = dict(workspace_guid="11111111-1111-4111-8111-111111111111", generation=1,
    repository_identity="repo", worker_id="worker", worktree_path="/wt", branch="ironclaude/w",
    base_commit="base", current_head="head", integration_target="main",
    source_owner_session_id="22222222-2222-4222-8222-222222222222")


def test_grant_is_scoped_and_single_use(tmp_path):
    authority = WorkspaceHandoffAuthority(_db(tmp_path))
    grant = authority.grant(**SCOPE)
    assert grant.capability_token
    with pytest.raises(WorkspaceHandoffAuthorityError):
        authority.grant(**(SCOPE | {"generation": 2}))


def test_idempotency_revalidates_assignment_and_expiry_and_hides_token(tmp_path):
    path = _db(tmp_path); authority = WorkspaceHandoffAuthority(path); grant = authority.grant(**SCOPE)
    assert authority.grant(**SCOPE, capability_token=grant.capability_token) == grant
    con = sqlite3.connect(path)
    con.execute("UPDATE assignments SET owner_session_id='33333333-3333-4333-8333-333333333333'")
    con.commit(); con.close()
    with pytest.raises(WorkspaceHandoffAuthorityError): authority.grant(**SCOPE, capability_token=grant.capability_token)
    state = authority.readback(workspace_guid=SCOPE["workspace_guid"])
    assert "capability_token" not in state and "capability_token_hash" not in state and "grant_expires_at" not in state


def test_grant_refuses_mutation_missing_database_and_expired_existing(tmp_path):
    with pytest.raises(WorkspaceHandoffAuthorityError): WorkspaceHandoffAuthority(tmp_path / "gone.db").grant(**SCOPE)
    path = _db(tmp_path); con = sqlite3.connect(path)
    con.execute("INSERT INTO workspace_mutations VALUES (?, 'x', 'n', 'p', 'now')", (SCOPE["workspace_guid"],)); con.commit(); con.close()
    with pytest.raises(WorkspaceHandoffAuthorityError): WorkspaceHandoffAuthority(path).grant(**SCOPE)
    con = sqlite3.connect(path); con.execute("DELETE FROM workspace_mutations")
    con.execute("INSERT INTO workspace_handoffs VALUES (?,1,?, ?, ?,NULL,'repo','worker','/wt','ironclaude/w','base','head','main','granted','now','now')", (SCOPE["workspace_guid"], hashlib.sha256(b"old").hexdigest(), (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), SCOPE["source_owner_session_id"])); con.commit(); con.close()
    with pytest.raises(WorkspaceHandoffAuthorityError): WorkspaceHandoffAuthority(path).grant(**SCOPE, capability_token="old")


@pytest.mark.parametrize("identity,blocks", [
    (f"{os.getpid()}:{_LIVE_START}", True),
    (f"{os.getpid()}", True),
    ("p", True),
    ("2147483647:1", False),
    ("2147483647", False),
    (f"{os.getpid()}:{_LIVE_START - 1}", False),
])
def test_grant_reaps_only_a_proved_dead_mutation_claim(tmp_path, identity, blocks):
    path = _db(tmp_path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO workspace_mutations VALUES (?, 'x', 'n', ?, 'now')",
                (SCOPE["workspace_guid"], identity))
    con.commit(); con.close()
    authority = WorkspaceHandoffAuthority(path)
    if blocks:
        with pytest.raises(WorkspaceHandoffAuthorityError, match="mutation is active"):
            authority.grant(**SCOPE)
    else:
        assert authority.grant(**SCOPE).phase == "granted"
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM workspace_mutations").fetchone()[0] == (1 if blocks else 0)
    con.close()


def test_python_and_typescript_share_mutation_identity_representation(tmp_path):
    root = Path(__file__).parents[2] / "worker/mcp-servers/workspace-manager"
    vite = root / "node_modules/.bin/vite-node"
    primary = tmp_path / "primary"
    primary.mkdir()
    for argv in (["git", "init", "-b", "main", str(primary)],
                 ["git", "-C", str(primary), "config", "user.email", "test@example.com"],
                 ["git", "-C", str(primary), "config", "user.name", "Test"]):
        subprocess.run(argv, check=True, capture_output=True)
    (primary / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "base"], check=True, capture_output=True)
    workspace_db = tmp_path / "workspace.db"

    def cli(command, payload):
        result = subprocess.run(
            [str(vite), "--script", str(root / "src/cli.ts"), command, json.dumps(payload)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "WORKSPACE_MANAGER_DB_PATH": str(workspace_db)})
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    guids = [str(uuid.uuid4()) for _ in range(2)]
    for index, guid in enumerate(guids):
        cli("allocate", {"repository_path": str(primary), "workspace_guid": guid,
                         "worker_id": f"worker-{index}", "integration_target": "main"})
    live, dead = f"{os.getpid()}:{_LIVE_START}", f"{os.getpid()}:{_LIVE_START - 1}"
    assert (mutation_owner_status(live), mutation_owner_status(dead)) == ("live", "dead")
    con = sqlite3.connect(workspace_db)
    con.execute("INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) "
                "VALUES (?, 'live', 'live', ?)", (guids[0], live))
    con.execute("INSERT INTO workspace_mutations(workspace_guid, operation, nonce, process_identity) "
                "VALUES (?, 'dead', 'dead', ?)", (guids[1], dead))
    con.commit(); con.close()
    assert cli("reap-stale-mutations", {}) == {"protocol_version": 1, "reaped": [guids[1]]}
    con = sqlite3.connect(workspace_db)
    assert con.execute("SELECT nonce FROM workspace_mutations").fetchall() == [("live",)]
    con.close()


def test_database_removed_after_presence_check_is_not_recreated(tmp_path, monkeypatch):
    path = tmp_path / "removed.db"
    monkeypatch.setattr(type(path), "is_file", lambda _path: True)
    with pytest.raises(WorkspaceHandoffAuthorityError, match="unavailable"):
        WorkspaceHandoffAuthority(path).grant(**SCOPE)
    assert not path.exists()


def test_target_authorization_requires_begun_scope_and_is_not_replayable(tmp_path):
    path = _db(tmp_path); authority = WorkspaceHandoffAuthority(path); grant = authority.grant(**SCOPE)
    with pytest.raises(WorkspaceHandoffAuthorityError): authority.authorize_target(**{k: SCOPE[k] for k in ("workspace_guid", "generation", "source_owner_session_id")}, capability_token=grant.capability_token, target_owner_session_id="33333333-3333-4333-8333-333333333333")
    con = sqlite3.connect(path); con.execute("UPDATE workspace_handoffs SET phase='begun'"); con.commit(); con.close()
    args = {k: SCOPE[k] for k in ("workspace_guid", "generation", "source_owner_session_id")}
    args |= {"capability_token": grant.capability_token, "target_owner_session_id": "33333333-3333-4333-8333-333333333333"}
    authority.authorize_target(**args)
    with pytest.raises(WorkspaceHandoffAuthorityError): authority.authorize_target(**args)
