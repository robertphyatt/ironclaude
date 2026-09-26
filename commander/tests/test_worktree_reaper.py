"""Managed-worktree reaper: release-only sweep for leaked worktrees.

Two separate SQLite databases are involved in production (the commander
`workers` table and the workspace-manager `assignments` table), so these
tests build each with the real schema and pass real sqlite3 connections —
only the workspace client and tmux manager are stubbed (never pattern-kill;
no os.kill by name anywhere in this file).

One deliberate deviation from a literal "reserved/ownerless row -> cleanup"
expectation: reading the workspace-manager CLI source
(worker/mcp-servers/workspace-manager/src/workspace-service.ts,
`getWorkspaceAssignment`) shows `cleanup` and `abandon` both require an EXACT
match against the assignment's stored `owner_session_id` — including when it
is NULL, which a non-empty `owner_session_id` string can never satisfy. A
worker-reserved assignment is created with `owner_session_id: null` and only
gains an owner via a successful `bind` (see `reserveWorkerWorktree` /
`bindWorkerWorktree`); a commander `workers` row is only ever inserted AFTER
that bind succeeds (see orchestrator_mcp.py's spawn flow). So an ownerless
assignment can never be joined to a commander worker row, and cleanup/abandon
would always fail with a binding-mismatch error at the real CLI boundary.

The reaper instead RELEASES an ownerless leak via the owner-free `reap` CLI
verb, which does not require a stored `owner_session_id` match: it derives
`repository_path` from the assignment's `worktree_path` by splitting on the
managed-worktree marker `/.ironclaude/worktrees/`. Surfacing (logging,
never force-deleting) remains the FALLBACK for the case that marker cannot
derive a `repository_path` — an unmanaged or malformed `worktree_path` — or
the `reap` call itself fails, the same treatment already specified for a
stale `integration_locks` row.
"""

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from ironclaude.main import (
    _find_leaked_worktrees,
    _has_push_pending,
    _is_protected,
    _live_worker_worktree_paths,
    _managed_repositories,
    _reap_leaked_worktrees,
    _reap_row_less_orphans,
    _sync_idle_worktrees,
    _WORKTREE_REAP_TTL_HOURS,
)
from ironclaude.workspace_client import WorkspaceClient

_W1 = "11111111-1111-4111-8111-111111111111"
_W2 = "22222222-2222-4222-8222-222222222222"
_OWNER = "33333333-3333-4333-8333-333333333333"
_PP_DISPOSITION = json.dumps({
    "phase": "push-pending", "candidateCommit": "abc123", "frozenCommit": "def456",
    "remoteName": "origin", "remoteUrl": "https://example.invalid/r.git",
    "destinationRef": "refs/heads/main", "expectedRemoteOldOid": "000",
})


def _make_commander_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE workers (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            machine TEXT,
            repo TEXT,
            description TEXT NOT NULL DEFAULT '',
            tmux_session TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            task_id INTEGER,
            spawned_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            client TEXT,
            model TEXT,
            native_session_id TEXT,
            workspace_guid TEXT,
            workspace_repository_identity TEXT,
            workspace_path TEXT,
            workspace_branch TEXT,
            workspace_base_commit TEXT,
            workspace_integration_target TEXT
        )"""
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _make_workspace_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE assignments (
            workspace_guid TEXT PRIMARY KEY,
            repository_identity TEXT NOT NULL,
            worktree_path TEXT NOT NULL,
            branch TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            current_head TEXT NOT NULL,
            owner_session_id TEXT,
            worker_id TEXT,
            lifecycle_status TEXT NOT NULL DEFAULT 'reserved',
            integration_target TEXT NOT NULL,
            integrated_commit TEXT,
            recovery_ref TEXT,
            disposition TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE primary_checkout_owners (
            repository_identity TEXT PRIMARY KEY,
            workspace_guid TEXT NOT NULL,
            owner_session_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE integration_locks (
            repository_identity TEXT PRIMARY KEY,
            workspace_guid TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            expected_target TEXT NOT NULL,
            acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    return conn


def _insert_worker(conn, id_, *, status, finished_ago=None, workspace_guid=None,
                    repo="/repo", tmux_session="ic-worker", client="claude"):
    finished_sql = f"datetime('now', '-{finished_ago}')" if finished_ago else "NULL"
    conn.execute(
        f"INSERT INTO workers (id, type, repo, tmux_session, status, finished_at, "
        f"client, workspace_guid) VALUES (?, 'claude', ?, ?, ?, {finished_sql}, ?, ?)",
        (id_, repo, tmux_session, status, client, workspace_guid),
    )
    conn.commit()


def _insert_assignment(conn, guid, *, repository_identity="repo-id", owner_session_id=None,
                        lifecycle_status="active", integration_target="main",
                        current_head="abc123", updated_ago=None, worktree_path="/wt",
                        disposition=None):
    updated_sql = f"datetime('now', '-{updated_ago}')" if updated_ago else "datetime('now')"
    conn.execute(
        f"INSERT INTO assignments (workspace_guid, repository_identity, worktree_path, "
        f"branch, base_commit, current_head, owner_session_id, lifecycle_status, "
        f"integration_target, disposition, updated_at) VALUES (?, ?, ?, 'ironclaude/x', 'abc123', "
        f"?, ?, ?, ?, ?, {updated_sql})",
        (guid, repository_identity, worktree_path, current_head, owner_session_id, lifecycle_status,
         integration_target, disposition),
    )
    conn.commit()


class TestReapLeakedWorktrees:
    def test_push_pending_row_is_preserved_and_warned_exactly_once(self, tmp_path, caplog):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                           updated_ago="25 hours", disposition=_PP_DISPOSITION)
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False
        alerted: set[str] = set()

        with caplog.at_level("WARNING"):
            counts1 = _reap_leaked_worktrees(commander, client, tmux,
                                             workspace_db_path=str(ws), push_pending_alerted=alerted)
        first = [r for r in caplog.records if "pending push" in r.getMessage()]
        caplog.clear()
        with caplog.at_level("WARNING"):
            counts2 = _reap_leaked_worktrees(commander, client, tmux,
                                             workspace_db_path=str(ws), push_pending_alerted=alerted)
        second = [r for r in caplog.records if "pending push" in r.getMessage()]

        assert len(first) == 1
        assert second == []
        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        client.reap.assert_not_called()
        assert counts1["push_pending"] == 1
        assert counts2["push_pending"] == 1

    def test_sweep_does_not_crash_on_valid_json_non_object_disposition(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                           updated_ago="25 hours", disposition="null")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False
        # Pre-fix: _has_push_pending("null") raises AttributeError out of the candidate
        # loop; this call would propagate it. Post-fix the sweep completes.
        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))
        assert counts["push_pending"] == 0

    def test_reused_guid_rewarns_after_resolution(self, tmp_path, caplog):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                           updated_ago="25 hours", disposition=_PP_DISPOSITION)
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False
        alerted: set[str] = set()

        # Episode 1: push-pending → warns once, guid recorded in the shared set.
        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux,
                                   workspace_db_path=str(ws), push_pending_alerted=alerted)
        first = [r for r in caplog.records if "pending push" in r.getMessage()]

        # Resolution: row is cleaned up → no longer a candidate → sweep prunes it from the set.
        ws_conn2 = sqlite3.connect(str(ws))
        ws_conn2.execute("UPDATE assignments SET lifecycle_status='cleaned' WHERE workspace_guid=?", (_W1,))
        ws_conn2.commit()
        ws_conn2.close()
        caplog.clear()
        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux,
                                   workspace_db_path=str(ws), push_pending_alerted=alerted)

        # Re-stuck on the SAME guid → must warn again (pre-fix the guid stays in the set forever → silent).
        ws_conn3 = sqlite3.connect(str(ws))
        ws_conn3.execute(
            "UPDATE assignments SET lifecycle_status='integrated', disposition=?, "
            "updated_at=datetime('now', '-25 hours') WHERE workspace_guid=?",
            (_PP_DISPOSITION, _W1),
        )
        ws_conn3.commit()
        ws_conn3.close()
        caplog.clear()
        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux,
                                   workspace_db_path=str(ws), push_pending_alerted=alerted)
        third = [r for r in caplog.records if "pending push" in r.getMessage()]

        assert len(first) == 1
        assert len(third) == 1

    def test_finished_worker_integrated_assignment_is_cleaned_up(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_called_once_with(
            {"repository_path": "/repo", "workspace_guid": _W1, "owner_session_id": _OWNER},
        )
        client.abandon.assert_not_called()
        assert counts["released"] == 1

    def test_unrelated_primary_owner_does_not_protect_finished_worker(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(
            ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
            updated_ago="25 hours",
        )
        _insert_assignment(
            ws_conn, _W2, owner_session_id=_W2, lifecycle_status="active",
            updated_ago="25 hours",
        )
        ws_conn.execute(
            "INSERT INTO primary_checkout_owners "
            "(repository_identity, workspace_guid, owner_session_id) VALUES ('repo-id', ?, ?)",
            (_W2, _W2),
        )
        ws_conn.commit()
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_called_once_with(
            {"repository_path": "/repo", "workspace_guid": _W1, "owner_session_id": _OWNER},
        )
        assert counts == {"released": 1, "surfaced": 0, "protected": 0, "errors": 0, "push_pending": 0}
        owner = sqlite3.connect(str(ws)).execute(
            "SELECT workspace_guid, owner_session_id FROM primary_checkout_owners "
            "WHERE repository_identity = 'repo-id'"
        ).fetchone()
        assert owner == (_W2, _W2)
        other = sqlite3.connect(str(ws)).execute(
            "SELECT lifecycle_status, owner_session_id FROM assignments WHERE workspace_guid = ?",
            (_W2,),
        ).fetchone()
        assert other == ("active", _W2)

    def test_real_workspace_cleanup_reaps_finished_worker_under_unrelated_primary_owner(self, tmp_path, monkeypatch):
        repository = tmp_path / "repository"
        repository.mkdir()

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(repository), *args], check=True,
                capture_output=True, text=True,
            ).stdout.strip()

        git("init", "--initial-branch=main")
        git("config", "user.name", "Reaper Test")
        git("config", "user.email", "reaper@example.invalid")
        (repository / "README.md").write_text("initial\n")
        git("add", "README.md")
        git("commit", "-m", "initial")

        package = Path(__file__).resolve().parents[2] / "worker/mcp-servers/workspace-manager"
        plugin_root = tmp_path / "plugin"
        temporary_cli = plugin_root / "mcp-servers/workspace-manager/dist/cli.js"
        temporary_cli.parent.mkdir(parents=True)
        subprocess.run([
            str(package / "node_modules/.bin/esbuild"), "src/cli.ts", "--bundle",
            "--platform=node", "--format=esm", f"--outfile={temporary_cli}",
            "--external:fsevents", "--external:better-sqlite3",
        ], cwd=package, check=True, capture_output=True, text=True)
        (temporary_cli.parents[1] / "node_modules").symlink_to(
            package / "node_modules", target_is_directory=True,
        )

        workspace_db = tmp_path / "workspaces.db"
        monkeypatch.setenv("WORKSPACE_MANAGER_DB_PATH", str(workspace_db))
        client = WorkspaceClient(plugin_root, commander_version="1.1.6")
        w1 = client.allocate({
            "repository_path": str(repository), "workspace_guid": _W1,
            "owner_session_id": _OWNER, "worker_id": "w1", "integration_target": "main",
        })
        w2 = client.allocate({
            "repository_path": str(repository), "workspace_guid": _W2,
            "owner_session_id": _W2, "worker_id": "w2", "integration_target": "main",
        })
        w1_path = Path(w1["worktree_path"])
        (w1_path / "integrated.txt").write_text("integrated\n")
        subprocess.run(["git", "-C", str(w1_path), "add", "integrated.txt"], check=True)
        subprocess.run(["git", "-C", str(w1_path), "commit", "-m", "integrated work"], check=True)
        integrated = subprocess.run(
            ["git", "-C", str(w1_path), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        git("merge", "--ff-only", integrated)

        ws_conn = sqlite3.connect(str(workspace_db))
        ws_conn.execute(
            "UPDATE assignments SET lifecycle_status = 'integrated', integrated_commit = ?, "
            "updated_at = datetime('now', '-25 hours') WHERE workspace_guid = ?",
            (integrated, _W1),
        )
        ws_conn.execute(
            "INSERT INTO integration_records (workspace_guid, repository_identity, target_ref, integrated_commit) "
            "VALUES (?, ?, 'refs/heads/main', ?)",
            (_W1, w1["repository_identity"], integrated),
        )
        ws_conn.execute(
            "INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id) VALUES (?, ?, ?)",
            (w2["repository_identity"], _W2, _W2),
        )
        ws_conn.commit()
        ws_conn.close()

        (repository / "README.md").write_text("operator staged\n")
        git("add", "README.md")
        (repository / "README.md").write_text("operator unstaged\n")
        (repository / "operator-notes.txt").write_text("operator untracked\n")
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        readme_hash = git("hash-object", "README.md")
        readme_index = git("ls-files", "-s", "--", "README.md")
        notes_hash = git("hash-object", "operator-notes.txt")
        notes_index = git("ls-files", "-s", "--", "operator-notes.txt")
        primary_status = git("status", "--porcelain=v1", "--untracked-files=all")

        commander = _make_commander_db(tmp_path / "commander.db")
        _insert_worker(
            commander, "w1", status="completed", finished_ago="25 hours",
            workspace_guid=_W1, repo=str(repository),
        )
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(workspace_db))

        assert counts == {"released": 1, "surfaced": 0, "protected": 0, "errors": 0, "push_pending": 0}
        assert not w1_path.exists()
        assert git("branch", "--list", w1["branch"]) == ""
        check = sqlite3.connect(str(workspace_db))
        assert check.execute(
            "SELECT lifecycle_status FROM assignments WHERE workspace_guid = ?", (_W1,),
        ).fetchone() == ("cleaned",)
        assert git("symbolic-ref", "--quiet", "--short", "HEAD") == branch
        assert git("hash-object", "README.md") == readme_hash
        assert git("ls-files", "-s", "--", "README.md") == readme_index
        assert git("hash-object", "operator-notes.txt") == notes_hash
        assert git("ls-files", "-s", "--", "operator-notes.txt") == notes_index
        assert git("status", "--porcelain=v1", "--untracked-files=all") == primary_status
        assert check.execute(
            "SELECT lifecycle_status, owner_session_id FROM assignments WHERE workspace_guid = ?", (_W2,),
        ).fetchone() == ("active", _W2)
        assert check.execute(
            "SELECT workspace_guid, owner_session_id FROM primary_checkout_owners WHERE repository_identity = ?",
            (w2["repository_identity"],),
        ).fetchone() == (_W2, _W2)
        check.close()

    def test_finished_worker_unintegrated_assignment_is_abandon_rescued(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="failed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.abandon.assert_called_once_with(
            {"repository_path": "/repo", "workspace_guid": _W1, "owner_session_id": _OWNER, "mode": "rescue"},
        )
        client.cleanup.assert_not_called()
        assert counts["released"] == 1

    def test_running_worker_status_is_never_a_candidate_even_past_ttl(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        # A resumed/misreported worker: status flipped back to running but a
        # stale finished_at from an earlier lifecycle is still on the row.
        _insert_worker(commander, "w1", status="running", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["released"] == 0

    def test_live_tmux_session_protects_a_finished_worker_past_ttl(self, tmp_path):
        """Safety-critical: a live tmux session must never be reaped, even
        when the commander row says 'completed' and is past TTL."""
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours",
                        workspace_guid=_W1, tmux_session="ic-live")
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = True  # session is actually still alive

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["protected"] == 1
        assert counts["released"] == 0

    def test_operator_owned_assignment_is_never_in_scope(self, tmp_path):
        """owner_session_id == workspace_guid marks the operator's own
        primary-checkout row (workspace-manager mints them equal); the reaper
        must refuse to touch it even if it is otherwise TTL-eligible and
        joined to a finished worker row."""
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W2)
        _insert_assignment(ws_conn, _W2, owner_session_id=_W2, lifecycle_status="active",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["released"] == 0

    def test_ownerless_reserved_underivable_path_stays_surfaced(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_assignment(ws_conn, _W2, owner_session_id=None, lifecycle_status="reserved",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        client.reap.assert_not_called()
        assert counts["surfaced"] == 1

    def test_ownerless_active_underivable_path_stays_surfaced(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_assignment(ws_conn, _W2, owner_session_id="", lifecycle_status="active",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        client.reap.assert_not_called()
        assert counts["surfaced"] == 1

    def test_ownerless_active_row_with_managed_path_is_reaped(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_assignment(
            ws_conn, _W2, owner_session_id="", lifecycle_status="active",
            updated_ago="25 hours",
            worktree_path=f"/repo/.ironclaude/worktrees/{_W2}",
        )
        ws_conn.close()
        client = Mock()
        tmux = Mock()

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.reap.assert_called_once_with(
            {"repository_path": "/repo", "workspace_guid": _W2},
        )
        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["released"] == 1

    def test_held_integration_lock_is_surfaced_and_never_deleted(self, tmp_path, caplog):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        ws_conn.execute(
            "INSERT INTO integration_locks (repository_identity, workspace_guid, target_ref, "
            "expected_target) VALUES ('repo-id', ?, 'refs/heads/main', 'abc123')",
            (_W1,),
        )
        ws_conn.commit()
        ws_conn.close()
        client = Mock()
        tmux = Mock()

        with caplog.at_level("WARNING"):
            _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        assert any("integration_locks" in r.message for r in caplog.records)
        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        remaining = sqlite3.connect(str(ws)).execute(
            "SELECT COUNT(*) FROM integration_locks"
        ).fetchone()[0]
        assert remaining == 1

    def test_held_integration_lock_protects_its_own_assignment(self, tmp_path):
        """A finished worker whose workspace currently holds the integration
        lock is mid-finalization — protect it, do not release underneath it."""
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="ready_for_integration",
                            updated_ago="25 hours")
        ws_conn.execute(
            "INSERT INTO integration_locks (repository_identity, workspace_guid, target_ref, "
            "expected_target) VALUES ('repo-id', ?, 'refs/heads/main', 'abc123')",
            (_W1,),
        )
        ws_conn.commit()
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["protected"] == 1

    def test_default_ttl_is_24_hours(self):
        assert _WORKTREE_REAP_TTL_HOURS == 24

    def test_finished_worker_within_ttl_is_not_reaped(self, tmp_path):
        """Falsifiability: deleting the TTL guard must break this test — a
        worker that finished 1 hour ago (well under the 24h TTL) must not be
        released even though it is owned, terminal, and otherwise leak-shaped."""
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="1 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="integrated",
                            updated_ago="1 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.return_value = False

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["released"] == 0

    def test_reserved_row_within_ttl_is_not_surfaced(self, tmp_path):
        """Falsifiability: deleting the TTL guard must break this test — a
        reserved row updated 1 hour ago must not even be surfaced yet."""
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_assignment(ws_conn, _W2, owner_session_id=None, lifecycle_status="reserved",
                            updated_ago="1 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["surfaced"] == 0

    def test_any_liveness_exception_fails_safe_toward_protect(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            updated_ago="25 hours")
        ws_conn.close()
        client = Mock()
        tmux = Mock()
        tmux.has_session.side_effect = RuntimeError("tmux not reachable")

        counts = _reap_leaked_worktrees(commander, client, tmux, workspace_db_path=str(ws))

        client.cleanup.assert_not_called()
        client.abandon.assert_not_called()
        assert counts["protected"] == 1


class TestIsProtected:
    def test_no_signals_does_not_protect(self):
        assignment = {"workspace_guid": _W1, "repository_identity": "repo-id", "updated_at": None}
        tmux = Mock()
        tmux.has_session.return_value = False
        assert _is_protected(assignment, None, tmux, set(), now=0.0) is False

    def test_push_pending_disposition_protects(self):
        assignment = {"workspace_guid": _W1, "repository_identity": "repo-id",
                      "updated_at": None, "disposition": _PP_DISPOSITION}
        tmux = Mock()
        tmux.has_session.return_value = False
        assert _is_protected(assignment, None, tmux, set(), now=0.0) is True


class TestHasPushPending:
    def test_true_for_push_phases(self):
        for phase in ("push-pending", "push-succeeded", "push-failed"):
            assert _has_push_pending(json.dumps({"phase": phase})) is True

    def test_false_for_non_push(self):
        for value in (None, "", "{}", json.dumps({"phase": "integration-pending"}), "not-json"):
            assert _has_push_pending(value) is False

    def test_false_for_valid_json_non_object(self):
        # json.loads returns None/list/int for these — .get would raise AttributeError.
        for value in ("null", "[1]", "3"):
            assert _has_push_pending(value) is False


class TestFindLeakedWorktrees:
    def test_cleaned_assignments_are_never_candidates(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", finished_ago="25 hours", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="cleaned")
        candidates = _find_leaked_worktrees(commander, {}, ws_conn, 24)
        assert candidates == []


class TestSyncIdleWorktrees:
    def test_behind_main_idle_worktree_gets_synced(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            current_head="old-head", integration_target="main")
        ws_conn.close()
        client = Mock()
        git_runner = Mock(return_value=Mock(returncode=0, stdout="new-head\n", stderr=""))

        counts = _sync_idle_worktrees(commander, client, workspace_db_path=str(ws), git_runner=git_runner)

        client.sync.assert_called_once_with(
            {"repository_path": "/repo", "workspace_guid": _W1, "owner_session_id": _OWNER},
        )
        assert counts["synced"] == 1

    def test_current_head_worktree_is_not_synced(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            current_head="same-head", integration_target="main")
        ws_conn.close()
        client = Mock()
        git_runner = Mock(return_value=Mock(returncode=0, stdout="same-head\n", stderr=""))

        counts = _sync_idle_worktrees(commander, client, workspace_db_path=str(ws), git_runner=git_runner)

        client.sync.assert_not_called()
        assert counts["current"] == 1

    def test_running_worker_worktree_is_not_synced(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="running", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            current_head="old-head", integration_target="main")
        ws_conn.close()
        client = Mock()
        git_runner = Mock(return_value=Mock(returncode=0, stdout="new-head\n", stderr=""))

        counts = _sync_idle_worktrees(commander, client, workspace_db_path=str(ws), git_runner=git_runner)

        client.sync.assert_not_called()
        git_runner.assert_not_called()
        assert counts == {"synced": 0, "current": 0, "errors": 0}

    def test_git_error_is_logged_and_skipped_not_raised(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_worker(commander, "w1", status="completed", workspace_guid=_W1)
        _insert_assignment(ws_conn, _W1, owner_session_id=_OWNER, lifecycle_status="active",
                            current_head="old-head", integration_target="main")
        ws_conn.close()
        client = Mock()
        git_runner = Mock(return_value=Mock(returncode=128, stdout="", stderr="fatal: bad ref"))

        counts = _sync_idle_worktrees(commander, client, workspace_db_path=str(ws), git_runner=git_runner)

        client.sync.assert_not_called()
        assert counts["errors"] == 1


class TestLiveWorkerWorktreePaths:
    def test_returns_only_live_worker_paths_excludes_dead_and_null(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        _insert_worker(commander, "w-live", status="running")
        commander.execute(
            "UPDATE workers SET workspace_path=? WHERE id='w-live'",
            ("/repo/.ironclaude/worktrees/live-guid",),
        )
        _insert_worker(commander, "w-dead", status="completed", finished_ago="1 hour")
        commander.execute(
            "UPDATE workers SET workspace_path=? WHERE id='w-dead'",
            ("/repo/.ironclaude/worktrees/dead-guid",),
        )
        # A running worker whose workspace_path is NULL must never appear —
        # the SQL WHERE clause excludes it before liveness is even checked.
        _insert_worker(commander, "w-null-path", status="running")
        commander.commit()
        tmux = Mock()
        tmux.has_session.return_value = False

        paths = _live_worker_worktree_paths(commander, tmux)

        assert paths == ["/repo/.ironclaude/worktrees/live-guid"]


class TestManagedRepositories:
    def test_dedupes_local_and_remote_and_unions_workspace_manager_repos(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        # Local group: two workers, same repo, no machine -> deduped, worker=None.
        _insert_worker(commander, "w1", status="completed", finished_ago="1 hour", repo="/repoA")
        _insert_worker(commander, "w2", status="completed", finished_ago="2 hours", repo="/repoA")
        # Remote group: machine set -> representative worker dict, not None.
        commander.execute(
            "INSERT INTO workers (id, type, repo, machine, tmux_session, status) "
            "VALUES ('w3', 'claude', '/repoB', 'remote-host', 'ic-worker', 'completed')"
        )
        commander.commit()
        ws = tmp_path / "workspaces.db"
        ws_conn = _make_workspace_db(ws)
        _insert_assignment(
            ws_conn, _W1, repository_identity="repo-id",
            worktree_path="/repoC/.ironclaude/worktrees/some-guid",
        )
        ws_conn.close()

        repos = dict(_managed_repositories(commander, workspace_db_path=str(ws)))

        assert repos["/repoA"] is None
        assert repos["/repoB"] is not None
        assert repos["/repoB"]["id"] == "w3"
        assert repos["/repoB"]["machine"] == "remote-host"
        assert repos["/repoC"] is None
        assert len(repos) == 3


class TestReapRowLessOrphans:
    def test_partial_repo_failure_does_not_abort_sweep_and_transport_is_remote_only(self, tmp_path):
        commander = _make_commander_db(tmp_path / "commander.db")
        # Live worker in repoA: its worktree path must be protected.
        _insert_worker(commander, "w-live", status="running", repo="/repoA")
        commander.execute(
            "UPDATE workers SET workspace_path=? WHERE id='w-live'",
            ("/repoA/.ironclaude/worktrees/live-guid",),
        )
        # Remote-group worker anchors repoB as a remote repo.
        commander.execute(
            "INSERT INTO workers (id, type, repo, machine, tmux_session, status) "
            "VALUES ('w-remote', 'claude', '/repoB', 'remote-host', 'ic-worker', 'completed')"
        )
        commander.commit()
        ws = tmp_path / "workspaces.db"
        _make_workspace_db(ws).close()

        def reap_side_effect(payload, **_transport):
            if payload["repository_path"] == "/repoA":
                raise RuntimeError("unreachable")
            return {
                "reaped": [], "preservedDirty": [],
                "preservedUnmerged": ["ironclaude/y", "ironclaude/z"],
                "skippedLive": [], "skippedYoung": [], "errors": [],
                "reapedWorktreeOnly": ["ironclaude/w"],
            }

        client = Mock()
        client.reap_orphans.side_effect = reap_side_effect
        tmux = Mock()
        tmux.has_session.return_value = False
        resolve_transport = Mock(return_value={"ssh_host": "remote-host"})

        summary = _reap_row_less_orphans(
            commander, client, tmux,
            workspace_db_path=str(ws), resolve_transport=resolve_transport,
        )

        assert summary["preservedUnmerged"] == 2
        assert summary["repo_failures"] == 1
        assert summary["reapedWorktreeOnly"] == 1
        assert len(summary["preserved_unmerged_names"]) == 2

        calls_by_repo = {
            call.args[0]["repository_path"]: call
            for call in client.reap_orphans.call_args_list
        }
        assert set(calls_by_repo) == {"/repoA", "/repoB"}
        for call in calls_by_repo.values():
            assert call.args[0]["protected_paths"] == ["/repoA/.ironclaude/worktrees/live-guid"]
        # Transport kwargs applied only to the worker-bearing (remote) group.
        assert calls_by_repo["/repoA"].kwargs == {}
        assert calls_by_repo["/repoB"].kwargs == {"ssh_host": "remote-host"}
        resolve_transport.assert_called_once()

    def test_preserved_detail_excludes_muted_and_tags_repository_path(self, tmp_path):
        """workspace-manager's reap_orphans now returns preservedDetail — a
        richer per-orphan dict (id/guid/branch/category/tip/worktreePresent/
        evidence/muted). The reaper must drop muted entries and stamp each
        surviving one with the repo it came from, so a multi-repo daemon can
        disambiguate ids that only need to be unique within one repo."""
        commander = _make_commander_db(tmp_path / "commander.db")
        _insert_worker(commander, "w1", status="completed", repo="/repo")
        ws = tmp_path / "workspaces.db"
        _make_workspace_db(ws).close()

        detail_entries = [
            {"id": "d1", "guid": "g1", "branch": "ironclaude/g1",
             "category": "genuinely-unmerged", "tip": "aaaaaaa1111",
             "worktreePresent": True, "evidence": "2 ahead of main", "muted": False},
            {"id": "d2", "guid": "g2", "branch": "ironclaude/g2",
             "category": "squash-merged", "tip": "bbbbbbb2222",
             "worktreePresent": True, "evidence": "squash of #4", "muted": False},
            {"id": "d3", "guid": "g3", "branch": "ironclaude/g3",
             "category": "dirty", "tip": "ccccccc3333",
             "worktreePresent": True, "evidence": "uncommitted changes", "muted": True},
        ]
        client = Mock()
        client.reap_orphans.return_value = {
            "reaped": [], "preservedDirty": [], "preservedUnmerged": [],
            "skippedLive": [], "skippedYoung": [], "errors": [], "reapedWorktreeOnly": [],
            "preservedDetail": detail_entries,
        }
        tmux = Mock()
        tmux.has_session.return_value = False

        summary = _reap_row_less_orphans(commander, client, tmux, workspace_db_path=str(ws))

        preserved_detail = summary["preserved_detail"]
        ids = {d["id"] for d in preserved_detail}
        assert ids == {"d1", "d2"}  # muted d3 excluded
        assert all(d["repository_path"] == "/repo" for d in preserved_detail)

        # Heartbeat-count semantics: non-squash-merged, non-muted only.
        non_squash = [d for d in preserved_detail if d["category"] != "squash-merged"]
        assert len(non_squash) == 1
        assert non_squash[0]["id"] == "d1"

    def test_preserved_detail_falls_back_to_preserved_unmerged_names(self, tmp_path):
        """Back-compat: a reap_orphans response that predates preservedDetail
        (only preservedUnmerged branch names) must still populate
        preserved_detail so downstream count/surface logic has one shape."""
        commander = _make_commander_db(tmp_path / "commander.db")
        _insert_worker(commander, "w1", status="completed", repo="/repo")
        ws = tmp_path / "workspaces.db"
        _make_workspace_db(ws).close()

        client = Mock()
        client.reap_orphans.return_value = {
            "reaped": [], "preservedDirty": [],
            "preservedUnmerged": ["ironclaude/y", "ironclaude/z"],
            "skippedLive": [], "skippedYoung": [], "errors": [],
        }
        tmux = Mock()
        tmux.has_session.return_value = False

        summary = _reap_row_less_orphans(commander, client, tmux, workspace_db_path=str(ws))

        preserved_detail = summary["preserved_detail"]
        assert len(preserved_detail) == 2
        assert {d["repository_path"] for d in preserved_detail} == {"/repo"}
        assert all(d.get("category") != "squash-merged" for d in preserved_detail)
        ids = {d["id"] for d in preserved_detail}
        assert "/repo:ironclaude/y" in ids
        assert "/repo:ironclaude/z" in ids


class TestSurfacePreservedOrphans:
    def test_change_gated_on_id_tip_map_and_reposts_on_tip_change(self):
        """_surface_preserved_orphans posts once for a given {id: tip} set,
        stays silent on a repeat call with the same set, and re-posts when a
        tip changes (a genuinely different orphan state, not just a re-sweep
        of the same one)."""
        from types import SimpleNamespace

        from ironclaude.main import IroncladeDaemon

        fake = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=1,
        )
        details = [{
            "id": "d1", "category": "genuinely-unmerged", "branch": "ironclaude/g1",
            "tip": "aaaaaaa1111", "evidence": "2 ahead of main", "repository_path": "/repo",
        }]

        IroncladeDaemon._surface_preserved_orphans(fake, details)
        assert fake.slack.post_message.call_count == 1

        # Same id+tip set on a re-sweep -> no repost.
        IroncladeDaemon._surface_preserved_orphans(fake, list(details))
        assert fake.slack.post_message.call_count == 1

        # Tip changed (new commit) -> re-posts.
        changed = [dict(details[0], tip="bbbbbbb2222")]
        IroncladeDaemon._surface_preserved_orphans(fake, changed)
        assert fake.slack.post_message.call_count == 2

    def test_empty_details_posts_nothing(self):
        from types import SimpleNamespace

        from ironclaude.main import IroncladeDaemon

        fake = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=1,
        )
        IroncladeDaemon._surface_preserved_orphans(fake, [])
        fake.slack.post_message.assert_not_called()

    def test_reposts_on_category_change_with_same_id_and_tip(self):
        """A same-id, same-tip orphan whose category changes (e.g. a clean
        worktree going dirty) is a genuinely different state the operator
        has not seen yet -> must re-post, not stay silently gated on tip
        alone."""
        from types import SimpleNamespace

        from ironclaude.main import IroncladeDaemon

        fake = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=1,
        )
        details = [{
            "id": "d1", "category": "genuinely-unmerged", "branch": "ironclaude/g1",
            "tip": "aaaaaaa1111", "evidence": "2 ahead of main", "repository_path": "/repo",
        }]

        IroncladeDaemon._surface_preserved_orphans(fake, details)
        assert fake.slack.post_message.call_count == 1

        # Identical id+tip+category repeat -> no repost.
        IroncladeDaemon._surface_preserved_orphans(fake, list(details))
        assert fake.slack.post_message.call_count == 1

        # Same id, same tip, but category changed -> must repost.
        recategorized = [dict(details[0], category="dirty")]
        IroncladeDaemon._surface_preserved_orphans(fake, recategorized)
        assert fake.slack.post_message.call_count == 2

    def test_brain_notified_once_per_change_for_reviewable_set(self):
        """A surfaced set that has something needing review pushes the Brain one
        PRESERVED ORPHANS SURFACED notice per set-change; silent on a repeat, on an
        empty set, and on a squash-merged-only set (nothing to review)."""
        from types import SimpleNamespace

        from ironclaude.main import IroncladeDaemon

        fake = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=1,
        )
        details = [{
            "id": "d1", "category": "genuinely-unmerged", "branch": "ironclaude/g1",
            "tip": "aaaaaaa1111", "evidence": "2 ahead of main", "repository_path": "/repo",
        }]
        IroncladeDaemon._surface_preserved_orphans(fake, details)
        assert fake.brain.send_message.call_count == 1
        msg = fake.brain.send_message.call_args.args[0]
        assert "PRESERVED ORPHANS SURFACED" in msg and "/repo" in msg and "d1" in msg

        IroncladeDaemon._surface_preserved_orphans(fake, list(details))
        assert fake.brain.send_message.call_count == 1  # repeat -> no re-notify

        fake_sm = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=0,
        )
        sm = [{
            "id": "s1", "category": "squash-merged", "branch": "ironclaude/g2",
            "tip": "bbbbbbb2222", "evidence": "merged", "repository_path": "/repo",
        }]
        IroncladeDaemon._surface_preserved_orphans(fake_sm, sm)
        fake_sm.slack.post_message.assert_called_once()
        fake_sm.brain.send_message.assert_not_called()

        fake_empty = SimpleNamespace(
            _orphaned_surface_state={}, slack=Mock(), brain=Mock(),
            _orphaned_unmerged_count=0,
        )
        IroncladeDaemon._surface_preserved_orphans(fake_empty, [])
        fake_empty.brain.send_message.assert_not_called()
