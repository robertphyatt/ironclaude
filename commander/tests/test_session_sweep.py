import os
import sqlite3
import pytest
from ironclaude.main import (
    _scan_session_id_files,
    _sweep_stale_sessions,
    _SESSION_SWEEP_MAX_AGE_HOURS,
)

_UUID_A = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
_UUID_LIVE = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"


def _make_sessions_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE sessions ("
        " terminal_session TEXT PRIMARY KEY,"
        " professional_mode TEXT NOT NULL DEFAULT 'undecided',"
        " workflow_stage TEXT NOT NULL DEFAULT 'idle',"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    return conn


def _insert(conn, uuid, pm, stage, updated_sql):
    conn.execute(
        "INSERT INTO sessions (terminal_session, professional_mode, workflow_stage, updated_at) "
        f"VALUES (?, ?, ?, datetime('now', '{updated_sql}'))",
        (uuid, pm, stage),
    )
    conn.commit()


def test_default_max_age_is_24h():
    assert _SESSION_SWEEP_MAX_AGE_HOURS == 24


class TestSweepStaleSessions:
    def test_deletes_only_stale_idle_undecided_not_live(self, tmp_path):
        db = tmp_path / "ironclaude.db"
        conn = _make_sessions_db(db)
        _insert(conn, "A-old-idle-undecided", "undecided", "idle", "-25 hours")  # DELETE
        _insert(conn, _UUID_LIVE, "undecided", "idle", "-25 hours")              # PROTECTED (live)
        _insert(conn, "C-recent", "undecided", "idle", "-1 hours")               # PROTECTED (recent)
        _insert(conn, "D-active", "on", "executing", "-25 hours")                # PROTECTED (active)
        _insert(conn, "E-decided", "on", "idle", "-25 hours")                    # PROTECTED (decided)
        _insert(conn, "F-brainstorming", "undecided", "brainstorming", "-25 hours")  # PROTECTED (non-idle stage)
        conn.close()
        deleted = _sweep_stale_sessions(str(db), {_UUID_LIVE}, 24)
        assert deleted == 1
        conn = sqlite3.connect(str(db))
        remaining = {r[0] for r in conn.execute("SELECT terminal_session FROM sessions")}
        conn.close()
        assert remaining == {_UUID_LIVE, "C-recent", "D-active", "E-decided", "F-brainstorming"}

    def test_empty_live_set_does_not_raise_and_still_filters(self, tmp_path):
        db = tmp_path / "ironclaude.db"
        conn = _make_sessions_db(db)
        _insert(conn, "A-old", "undecided", "idle", "-25 hours")    # DELETE
        _insert(conn, "C-recent", "undecided", "idle", "-1 hours")  # PROTECTED
        conn.close()
        assert _sweep_stale_sessions(str(db), set(), 24) == 1


class TestScanSessionIdFiles:
    def test_partitions_live_and_dead(self, tmp_path, monkeypatch):
        live = tmp_path / "ironclaude-session-1001.id"
        live.write_text(_UUID_LIVE)
        dead = tmp_path / "ironclaude-session-2002.id"
        dead.write_text(_UUID_A)

        def fake_kill(pid, sig):
            if pid == 2002:
                raise ProcessLookupError
            return None

        monkeypatch.setattr(os, "kill", fake_kill)
        live_uuids, dead_files = _scan_session_id_files(tmp_path)
        assert live_uuids == {_UUID_LIVE}
        assert dead_files == [dead]

    def test_malformed_pid_name_skipped(self, tmp_path, monkeypatch):
        bad = tmp_path / "ironclaude-session-notapid.id"
        bad.write_text(_UUID_A)
        monkeypatch.setattr(os, "kill", lambda p, s: None)
        assert _scan_session_id_files(tmp_path) == (set(), [])

    def test_non_uuid_content_not_added_to_live(self, tmp_path, monkeypatch):
        f = tmp_path / "ironclaude-session-1001.id"
        f.write_text("not-a-uuid")
        monkeypatch.setattr(os, "kill", lambda p, s: None)
        assert _scan_session_id_files(tmp_path) == (set(), [])

    def test_permission_error_treated_as_alive(self, tmp_path, monkeypatch):
        f = tmp_path / "ironclaude-session-3003.id"
        f.write_text(_UUID_LIVE)

        def fake_kill(pid, sig):
            raise PermissionError

        monkeypatch.setattr(os, "kill", fake_kill)
        live_uuids, dead_files = _scan_session_id_files(tmp_path)
        assert live_uuids == {_UUID_LIVE}
        assert dead_files == []
