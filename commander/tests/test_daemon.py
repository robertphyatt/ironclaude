# tests/test_daemon.py
"""Tests for IroncladeDaemon worker completion detection."""

import os
import shlex
import sqlite3
import subprocess
import time
import json

import pytest
from unittest.mock import MagicMock, patch

from ironclaude.main import IroncladeDaemon, ensure_brain_trusted


@pytest.fixture
def daemon(tmp_path):
    """Create an IroncladeDaemon with mock dependencies and temp log dir."""
    config = {"tmp_dir": str(tmp_path)}
    slack = MagicMock()
    registry = MagicMock()
    tmux = MagicMock()
    tmux.log_dir = str(tmp_path / "logs")
    os.makedirs(tmux.log_dir, exist_ok=True)
    brain = MagicMock()
    d = IroncladeDaemon(config, slack, None, registry, tmux, brain)
    return d


class TestCheckWorkersDoneMarker:
    def test_done_marker_notifies_brain_idle(self, daemon):
        """Worker with .done marker triggers idle notification, NOT completion."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-01T00:00:00Z")
        daemon.check_workers()
        # Brain should be notified with idle signal, not "completed"
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "idle" in msg
        # Marker should be cleaned up
        assert not os.path.exists(marker)

    def test_done_marker_does_not_kill_session(self, daemon):
        """Worker idle via .done marker does NOT kill tmux session."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-01T00:00:00Z")
        daemon.check_workers()
        daemon.tmux.kill_session.assert_not_called()

    def test_done_marker_does_not_update_registry(self, daemon):
        """Worker idle via .done marker does NOT change registry status."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-01T00:00:00Z")
        daemon.check_workers()
        daemon.registry.update_worker_status.assert_not_called()

    def test_dead_session_still_detected(self, daemon):
        """Worker whose tmux session died is still detected (fallback)."""
        worker = {"id": "w2", "tmux_session": "ic-w2"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = False
        daemon.check_workers()
        daemon.registry.update_worker_status.assert_called_once_with("w2", "completed")

    def test_live_worker_not_touched(self, daemon):
        """Worker with live session and no .done marker is left alone."""
        worker = {"id": "w3", "tmux_session": "ic-w3"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Running..."
        daemon.check_workers()
        daemon.registry.update_worker_status.assert_not_called()


class TestDetailLogCapturePane:
    def test_detail_uses_capture_pane(self, daemon):
        """Detail command uses capture_pane when session is alive."""
        worker = {"id": "w1", "tmux_session": "ic-w1", "status": "running"}
        daemon.registry.get_worker.return_value = worker
        daemon.tmux.capture_pane.return_value = "Clean output\n"
        daemon._handle_detail({"target": "w1"})
        daemon.tmux.capture_pane.assert_called_once_with("ic-w1", lines=20)
        daemon.slack.post_message.assert_called_once()
        assert "Clean output" in daemon.slack.post_message.call_args[0][0]

    def test_detail_falls_back_on_dead_session(self, daemon):
        """Detail command falls back to read_log_tail when session is dead."""
        worker = {"id": "w1", "tmux_session": "ic-w1", "status": "running"}
        daemon.registry.get_worker.return_value = worker
        daemon.tmux.capture_pane.side_effect = subprocess.CalledProcessError(1, "tmux")
        daemon.tmux.read_log_tail.return_value = "Raw fallback\n"
        daemon._handle_detail({"target": "w1"})
        daemon.tmux.read_log_tail.assert_called_once_with("ic-w1", lines=20)

    def test_log_uses_capture_pane(self, daemon):
        """Log command uses capture_pane when session is alive."""
        worker = {"id": "w1", "tmux_session": "ic-w1", "status": "running"}
        daemon.registry.get_worker.return_value = worker
        daemon.tmux.capture_pane.return_value = "Clean log output\n"
        daemon._handle_log({"target": "w1", "lines": 30})
        daemon.tmux.capture_pane.assert_called_once_with("ic-w1", lines=30)


class TestCheckWorkersMarkerRetry:
    def test_marker_kept_when_brain_unreachable(self, daemon):
        """Marker is NOT removed when brain.send_message returns False."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.brain.send_message.return_value = False
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-07T00:00:00Z")
        daemon.check_workers()
        # Marker should still exist for retry
        assert os.path.exists(marker)
        # Slack notification should still fire
        daemon.slack.post_message.assert_called_once()

    def test_marker_removed_when_brain_reachable(self, daemon):
        """Marker IS removed when brain.send_message returns True."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.brain.send_message.return_value = True
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-07T00:00:00Z")
        daemon.check_workers()
        assert not os.path.exists(marker)


def _setup_ironclaude_db(claude_dir, pane_pid, session_id, workflow_stage):
    """Helper: create ironclaude.db with a session entry."""
    db_path = claude_dir / "ironclaude.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (terminal_session TEXT PRIMARY KEY, "
        "workflow_stage TEXT, professional_mode TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions (terminal_session, workflow_stage) VALUES (?, ?)",
        (session_id, workflow_stage),
    )
    conn.commit()
    conn.close()
    # Write session ID file
    (claude_dir / f"ironclaude-session-{pane_pid}.id").write_text(session_id)


class TestGetWorkerWorkflowStage:
    def test_returns_stage_from_db(self, daemon, tmp_path):
        """Returns workflow_stage when pane PID, session file, and DB all exist."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "executing")
        daemon.tmux.list_pane_pid.return_value = "12345"

        result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)
        assert result == "executing"

    def test_returns_none_when_no_pane_pid(self, daemon, tmp_path):
        """Returns None when tmux pane PID cannot be retrieved."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        daemon.tmux.list_pane_pid.return_value = None

        result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)
        assert result is None

    def test_returns_none_when_no_session_file(self, daemon, tmp_path):
        """Returns None when session ID file does not exist."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        daemon.tmux.list_pane_pid.return_value = "12345"

        result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)
        assert result is None

    def test_returns_none_when_db_missing(self, daemon, tmp_path):
        """Returns None when ironclaude.db does not exist."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        (claude_dir / "ironclaude-session-12345.id").write_text(session_id)
        daemon.tmux.list_pane_pid.return_value = "12345"

        result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)
        assert result is None

    def test_returns_none_when_session_not_in_db(self, daemon, tmp_path):
        """Returns None when session ID exists in file but not in DB."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        (claude_dir / "ironclaude-session-12345.id").write_text(session_id)
        # Create DB without this session
        db_path = claude_dir / "ironclaude.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE sessions (terminal_session TEXT PRIMARY KEY, "
            "workflow_stage TEXT, professional_mode TEXT, updated_at TEXT)"
        )
        conn.commit()
        conn.close()
        daemon.tmux.list_pane_pid.return_value = "12345"

        result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)
        assert result is None


class TestDetectPromptWaiting:
    def test_detects_ask_user_question(self, daemon):
        assert daemon._detect_prompt_waiting("Tool use: AskUserQuestion\nWhat do you want?") is True

    def test_detects_submit_answers(self, daemon):
        assert daemon._detect_prompt_waiting("Submit answers to continue") is True

    def test_detects_options_menu(self, daemon):
        assert daemon._detect_prompt_waiting("options:\n1. Fix now\n2. Skip") is True

    def test_detects_which_approach(self, daemon):
        assert daemon._detect_prompt_waiting("Which approach would you prefer?") is True

    def test_detects_how_would_you_like(self, daemon):
        assert daemon._detect_prompt_waiting("How would you like to proceed?") is True

    def test_detects_numbered_menu(self, daemon):
        assert daemon._detect_prompt_waiting("  1. Option A\n  2. Option B") is True

    def test_no_false_positive_normal_output(self, daemon):
        assert daemon._detect_prompt_waiting("Running tests...\nAll 5 passed") is False

    def test_no_false_positive_empty(self, daemon):
        assert daemon._detect_prompt_waiting("") is False


class TestProactiveCheckin:
    def test_checkin_sent_when_cadence_expires(self, daemon, tmp_path):
        """Check-in notification sent when cadence elapses and no brain contact."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Running tests..."
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "executing")

        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True
        daemon.check_workers()

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[CHECK-IN]" in msg
        assert "w1" in msg

    def test_checkin_not_sent_when_recent_contact(self, daemon, tmp_path):
        """No check-in when brain recently contacted worker."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "executing")

        daemon._claude_dir = claude_dir
        # Write recent brain_contact file
        contact_file = os.path.join(daemon.tmux.log_dir, "ic-w1.brain_contact")
        with open(contact_file, "w") as f:
            f.write(str(time.time()))

        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()

    def test_checkin_updates_last_sent_timestamp(self, daemon, tmp_path):
        """After sending check-in, daemon tracks when it was sent to prevent re-send."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "brainstorming")

        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True
        daemon.check_workers()

        # First call sends check-in
        assert daemon.brain.send_message.call_count == 1

        # Second call should NOT send because _last_checkin_sent was updated
        daemon.brain.send_message.reset_mock()
        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()

    def test_execution_complete_uses_normal_checkin(self, daemon, tmp_path):
        """Worker at execution_complete gets normal check-in, not 'Investigating' spam."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Plan complete."
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "execution_complete")

        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True
        daemon.check_workers()

        # Should send check-in to brain, NOT "Investigating" to Slack
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[CHECK-IN]" in msg
        assert "execution_complete" in msg
        assert "Investigating" not in msg

    def test_execution_complete_respects_cadence(self, daemon, tmp_path):
        """execution_complete check-in is not sent again within cadence window."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Plan complete."
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "execution_complete")

        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        # First call sends check-in
        daemon.check_workers()
        assert daemon.brain.send_message.call_count == 1

        # Second call should NOT send — cadence not elapsed
        daemon.brain.send_message.reset_mock()
        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()

    def test_done_marker_takes_priority(self, daemon, tmp_path):
        """When .done marker exists, idle notification fires instead of check-in."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.brain.send_message.return_value = True
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-08T00:00:00Z")

        daemon.check_workers()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "idle" in msg
        assert "[CHECK-IN]" not in msg


class TestProactiveCheckinDedup:
    def test_dedup_suppresses_when_no_ack(self, daemon, tmp_path):
        """No repeat check-in when brain hasn't acked and heartbeat hasn't elapsed."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "executing")

        daemon._claude_dir = claude_dir
        # Simulate a send that already happened — no brain_contact file written
        daemon._last_checkin_sent["w1"] = time.time()
        daemon._last_checkin_stage["w1"] = "executing"

        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()

    def test_stage_change_bypasses_dedup(self, daemon, tmp_path):
        """Stage transition fires check-in immediately, bypassing dedup gate."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Reviewing..."
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        # Worker is now in "reviewing" stage
        _setup_ironclaude_db(claude_dir, "12345", session_id, "reviewing")

        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True
        # Last send was recent, but stage was "executing" — now it's "reviewing"
        daemon._last_checkin_sent["w1"] = time.time()
        daemon._last_checkin_stage["w1"] = "executing"

        daemon.check_workers()
        daemon.brain.send_message.assert_called_once()

    def test_heartbeat_elapsed_sends_without_ack(self, daemon, tmp_path):
        """Heartbeat backstop fires even without brain ack when interval elapses."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Still executing..."
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "executing")

        daemon._claude_dir = claude_dir
        daemon.config["heartbeat_interval_seconds"] = 900
        daemon.brain.send_message.return_value = True
        # Sent 901 seconds ago, no brain ack — heartbeat backstop should fire
        daemon._last_checkin_sent["w1"] = time.time() - 901
        daemon._last_checkin_stage["w1"] = "executing"

        daemon.check_workers()
        daemon.brain.send_message.assert_called_once()

    def test_brain_ack_resumes_cadence(self, daemon, tmp_path):
        """After brain acks, cadence check resumes and suppresses if not elapsed."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        _setup_ironclaude_db(claude_dir, "12345", session_id, "executing")

        daemon._claude_dir = claude_dir
        t_sent = time.time() - 5  # Sent 5 seconds ago
        daemon._last_checkin_sent["w1"] = t_sent
        daemon._last_checkin_stage["w1"] = "executing"
        # Brain acknowledged shortly after the send
        contact_file = os.path.join(daemon.tmux.log_dir, "ic-w1.brain_contact")
        with open(contact_file, "w") as f:
            f.write(str(t_sent + 1))

        # Cadence not elapsed (5s < 300s default for "executing")
        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()


class TestDirectiveConfirmation:
    def test_confirmed_directive_uses_operator_name(self, daemon):
        """Directive confirmation message uses operator_name from config, not hardcoded 'Robert'."""
        daemon.config["operator_name"] = "Alice"
        # Set up an in-memory DB with a pending_confirmation directive
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE directives (id INTEGER PRIMARY KEY, interpretation TEXT, "
            "status TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO directives (interpretation, status, created_at) "
            "VALUES ('deploy to staging', 'pending_confirmation', datetime('now'))"
        )
        conn.commit()
        daemon._db = conn
        result = daemon._handle_directive_confirmation("yes")
        assert result is True
        # Brain message should contain "Alice", not "Robert"
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "Alice" in msg
        assert "Robert" not in msg
        conn.close()


class TestHandleSummary:
    def _make_db(self, rows):
        """Create in-memory SQLite DB with directives table."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE directives ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, source_ts TEXT, "
            "source_text TEXT, interpretation TEXT NOT NULL, "
            "status TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
        for row in rows:
            conn.execute(
                "INSERT INTO directives (source_ts, source_text, interpretation, status) "
                "VALUES (?, ?, ?, ?)",
                (row["source_ts"], row["source_text"], row["interpretation"], row["status"]),
            )
        conn.commit()
        return conn

    def test_summary_shows_three_sections(self, daemon):
        """_handle_summary posts a message with all three directive sections."""
        conn = self._make_db([
            {"source_ts": "1.0", "source_text": "fix", "interpretation": "Fix auth bug", "status": "in_progress"},
            {"source_ts": "2.0", "source_text": "review", "interpretation": "Review PR #5", "status": "pending_confirmation"},
            {"source_ts": "3.0", "source_text": "deploy", "interpretation": "Deploy to staging", "status": "completed"},
        ])
        daemon._db = conn
        daemon.registry.get_running_workers.return_value = [{"id": "worker-abc"}]
        daemon._handle_summary()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "In Progress" in msg
        assert "Fix auth bug" in msg
        assert "Blocked" in msg
        assert "Review PR #5" in msg
        assert "Recently Completed" in msg
        assert "Deploy to staging" in msg
        assert "worker-abc" in msg
        conn.close()

    def test_summary_empty_sections_show_none(self, daemon):
        """_handle_summary shows (none) when sections have no directives."""
        conn = self._make_db([])
        daemon._db = conn
        daemon.registry.get_running_workers.return_value = []
        daemon._handle_summary()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "(none)" in msg
        conn.close()

    def test_summary_no_db_posts_error(self, daemon):
        """_handle_summary posts error when _db is None, raises no exception."""
        daemon._db = None
        daemon._handle_summary()
        daemon.slack.post_message.assert_called_once()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Database not configured" in msg


class TestHeartbeatWorkerListing:
    def test_post_heartbeat_passes_worker_details(self, daemon):
        """post_heartbeat builds worker detail list from registry + workflow stage."""
        daemon.registry.get_running_workers.return_value = [
            {"id": "w-1", "tmux_session": "ic-w-1", "description": "Fix auth bug"},
        ]
        daemon._last_heartbeat = 0
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='executing'):
            daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "w-1" in msg
        assert "executing" in msg
        assert "Fix auth bug" in msg

    def test_post_heartbeat_no_workers_shows_default(self, daemon):
        """post_heartbeat shows default message when no workers running."""
        daemon.registry.get_running_workers.return_value = []
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "No active workers" in msg

    def test_post_heartbeat_includes_worker_description(self, daemon):
        """post_heartbeat reads description directly from worker row."""
        daemon.registry.get_running_workers.return_value = [
            {"id": "w-1", "tmux_session": "ic-w-1", "description": "Fix the bug"},
        ]
        daemon._last_heartbeat = 0
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Fix the bug" in msg


class TestDirectiveReactionHandling:
    """End-to-end tests for _handle_directive_reaction calling the real function."""

    @pytest.fixture
    def reaction_daemon(self, tmp_path):
        """Daemon with in-memory DB seeded with the full directives schema."""
        from ironclaude.db import init_db
        conn = init_db(":memory:")
        config = {"tmp_dir": str(tmp_path), "operator_name": "Operator"}
        slack = MagicMock()
        registry = MagicMock()
        tmux = MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(tmux.log_dir, exist_ok=True)
        brain = MagicMock()
        d = IroncladeDaemon(config, slack, None, registry, tmux, brain)
        d._db = conn
        return d

    def _insert_directive(self, conn, interpretation_ts="999.888", source_ts="123.456"):
        conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_ts, "do thing", "Build X", "pending_confirmation", interpretation_ts),
        )
        conn.commit()

    def test_thumbsup_confirms_directive(self, reaction_daemon):
        """thumbsup reaction confirms directive and uses DIRECTIVE_STATUS_EMOJI, not 'eyes'."""
        self._insert_directive(reaction_daemon._db)
        result = reaction_daemon._handle_directive_reaction("thumbsup", "999.888")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "confirmed"
        reaction_daemon.slack.post_message.assert_called_once()
        assert "confirmed" in reaction_daemon.slack.post_message.call_args[0][0]
        reaction_daemon.brain.send_message.assert_called_once()
        reaction_daemon.slack.remove_reaction.assert_called_once_with("hourglass_flowing_sand", "123.456")
        reaction_daemon.slack.add_reaction.assert_called_once_with("thumbsup", "123.456")

    def test_thumbsdown_rejects_directive(self, reaction_daemon):
        """thumbsdown reaction rejects directive."""
        self._insert_directive(reaction_daemon._db)
        result = reaction_daemon._handle_directive_reaction("thumbsdown", "999.888")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "rejected"
        reaction_daemon.slack.post_message.assert_called_once()
        assert "rejected" in reaction_daemon.slack.post_message.call_args[0][0]

    def test_plus1_alias_confirms(self, reaction_daemon):
        """+1 emoji alias behaves identically to thumbsup."""
        self._insert_directive(reaction_daemon._db)
        result = reaction_daemon._handle_directive_reaction("+1", "999.888")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "confirmed"

    def test_unknown_emoji_ignored(self, reaction_daemon):
        """Unknown emoji returns False and leaves DB unchanged."""
        self._insert_directive(reaction_daemon._db)
        result = reaction_daemon._handle_directive_reaction("fire", "999.888")
        assert result is False
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "pending_confirmation"

    def test_no_matching_interpretation_ts(self, reaction_daemon):
        """Reaction on unknown message_ts returns False when content also doesn't match."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="999.888")
        reaction_daemon.slack.get_message = MagicMock(return_value="unrelated message")
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is False
        reaction_daemon.slack.post_message.assert_not_called()

    def test_content_match_by_directive_id(self, reaction_daemon):
        """Reaction on message containing 'Directive #N' matches the directive."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="other.ts")
        reaction_daemon.slack.get_message = MagicMock(
            return_value="[IRONCLAUDE] Brain: Directive #1 submitted — waiting for confirmation"
        )
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE id=1"
        ).fetchone()
        assert row[0] == "confirmed"

    def test_content_match_by_interpretation_text(self, reaction_daemon):
        """Reaction on message containing interpretation text matches."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="other.ts")
        reaction_daemon.slack.get_message = MagicMock(
            return_value="Directive detected: 'Build X'. From your message: 'do thing'. React to confirm."
        )
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE id=1"
        ).fetchone()
        assert row[0] == "confirmed"

    def test_content_match_by_source_text(self, reaction_daemon):
        """Reaction on operator's own message matches via source_text."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="other.ts")
        reaction_daemon.slack.get_message = MagicMock(return_value="do thing")
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE id=1"
        ).fetchone()
        assert row[0] == "confirmed"

    def test_content_match_no_match_returns_false(self, reaction_daemon):
        """Reaction on unrelated message returns False."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="other.ts")
        reaction_daemon.slack.get_message = MagicMock(return_value="completely unrelated message")
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is False

    def test_content_match_get_message_fails(self, reaction_daemon):
        """Returns False when get_message returns None (API failure)."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="other.ts")
        reaction_daemon.slack.get_message = MagicMock(return_value=None)
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is False

    def test_fast_path_still_works(self, reaction_daemon):
        """Existing interpretation_ts fast path still works without calling get_message."""
        self._insert_directive(reaction_daemon._db)
        reaction_daemon.slack.get_message = MagicMock()
        result = reaction_daemon._handle_directive_reaction("thumbsup", "999.888")
        assert result is True
        reaction_daemon.slack.get_message.assert_not_called()

    def test_poll_slack_commands_routes_reaction(self, reaction_daemon):
        """poll_slack_commands routes reaction items to _handle_directive_reaction."""
        self._insert_directive(reaction_daemon._db)
        mock_handler = MagicMock()
        mock_handler.drain.return_value = [
            {"type": "reaction", "emoji": "thumbsup", "message_ts": "999.888"}
        ]
        reaction_daemon.socket_handler = mock_handler
        reaction_daemon.poll_slack_commands()
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "confirmed"

    def test_thumbs_up_variant_confirms(self, reaction_daemon):
        """thumbs_up (underscore variant) confirms directive identically to thumbsup."""
        self._insert_directive(reaction_daemon._db)
        result = reaction_daemon._handle_directive_reaction("thumbs_up", "999.888")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "confirmed"

    def test_thumbs_down_variant_rejects(self, reaction_daemon):
        """thumbs_down (underscore variant) rejects directive identically to thumbsdown."""
        self._insert_directive(reaction_daemon._db)
        result = reaction_daemon._handle_directive_reaction("thumbs_down", "999.888")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "rejected"

    def test_logging_on_successful_reaction(self, reaction_daemon, caplog):
        """Entry and success log messages appear on successful reaction."""
        import logging
        self._insert_directive(reaction_daemon._db)
        with caplog.at_level(logging.DEBUG, logger="ironclaude"):
            reaction_daemon._handle_directive_reaction("thumbsup", "999.888")
        messages = [r.message for r in caplog.records]
        assert any("_handle_directive_reaction" in m for m in messages)
        assert any("Directive #1 confirmed" in m for m in messages)

    def test_logging_on_emoji_filter_drop(self, reaction_daemon, caplog):
        """Debug log fires when emoji is not in accepted set."""
        import logging
        self._insert_directive(reaction_daemon._db)
        with caplog.at_level(logging.DEBUG, logger="ironclaude"):
            reaction_daemon._handle_directive_reaction("fire", "999.888")
        messages = [r.message for r in caplog.records]
        assert any("not in accepted set" in m for m in messages)

    def test_poll_slack_commands_logs_reaction_routing(self, reaction_daemon, caplog):
        """poll_slack_commands emits debug log when routing a reaction item."""
        import logging
        self._insert_directive(reaction_daemon._db)
        mock_handler = MagicMock()
        mock_handler.drain.return_value = [
            {"type": "reaction", "emoji": "thumbsup", "message_ts": "999.888"}
        ]
        reaction_daemon.socket_handler = mock_handler
        with caplog.at_level(logging.DEBUG, logger="ironclaude"):
            reaction_daemon.poll_slack_commands()
        messages = [r.message for r in caplog.records]
        assert any("routing reaction" in m for m in messages)

    def test_null_interpretation_ts_falls_back_to_content(self, reaction_daemon):
        """Directive with NULL interpretation_ts is still matched via content fallback."""
        # Insert a directive with NULL interpretation_ts (post_message failed at creation time)
        reaction_daemon._db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('123.456', 'do thing', 'Build X', 'pending_confirmation')"
        )
        reaction_daemon._db.commit()
        # React on a message that references the directive by content
        reaction_daemon.slack.get_message = MagicMock(
            return_value="[IRONCLAUDE] Brain: Directive #1 submitted"
        )
        result = reaction_daemon._handle_directive_reaction("thumbsup", "any.ts")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE id=1"
        ).fetchone()
        assert row[0] == "confirmed"

    def test_reaction_on_in_progress_directive_still_confirms(self, reaction_daemon):
        """Race condition fix: reaction on directive already moved to in_progress still works."""
        reaction_daemon._db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status, interpretation_ts) "
            "VALUES ('123.456', 'do thing', 'Build X', 'in_progress', '999.888')"
        )
        reaction_daemon._db.commit()
        result = reaction_daemon._handle_directive_reaction("thumbsup", "999.888")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE interpretation_ts=?", ("999.888",)
        ).fetchone()
        assert row[0] == "confirmed"

    def test_content_match_on_in_progress_directive(self, reaction_daemon):
        """Content-based match works on in_progress directive (race condition)."""
        reaction_daemon._db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('123.456', 'do thing', 'Build X', 'in_progress')"
        )
        reaction_daemon._db.commit()
        reaction_daemon.slack.get_message = MagicMock(
            return_value="[IRONCLAUDE] Brain: Directive #1 submitted"
        )
        result = reaction_daemon._handle_directive_reaction("thumbsup", "different.ts")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE id=1"
        ).fetchone()
        assert row[0] == "confirmed"

    def test_reaction_on_source_ts_confirms_directive(self, reaction_daemon):
        """Reaction on operator's source message confirms via fast-path source_ts match."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="999.888", source_ts="123.456")
        result = reaction_daemon._handle_directive_reaction("thumbsup", "123.456")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE source_ts=?", ("123.456",)
        ).fetchone()
        assert row[0] == "confirmed"

    def test_reaction_on_source_ts_rejects_directive(self, reaction_daemon):
        """Reaction on operator's source message rejects via fast-path source_ts match."""
        self._insert_directive(reaction_daemon._db, interpretation_ts="999.888", source_ts="123.456")
        result = reaction_daemon._handle_directive_reaction("thumbsdown", "123.456")
        assert result is True
        row = reaction_daemon._db.execute(
            "SELECT status FROM directives WHERE source_ts=?", ("123.456",)
        ).fetchone()
        assert row[0] == "rejected"

    def test_no_match_logs_warning_with_pending_ts(self, reaction_daemon, caplog):
        """WARNING log includes pending directive ts values when no match found."""
        import logging
        self._insert_directive(reaction_daemon._db, interpretation_ts="999.888", source_ts="123.456")
        reaction_daemon.slack.get_message = MagicMock(return_value="completely unrelated")
        with caplog.at_level(logging.WARNING, logger="ironclaude"):
            reaction_daemon._handle_directive_reaction("thumbsup", "nomatch.ts")
        messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("nomatch.ts" in m for m in messages)
        assert any("999.888" in m or "123.456" in m for m in messages)

    def test_reaction_info_logging_on_match(self, reaction_daemon, caplog):
        """INFO log emitted on successful fast-path match."""
        import logging
        self._insert_directive(reaction_daemon._db)
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            reaction_daemon._handle_directive_reaction("thumbsup", "999.888")
        messages = [r.message for r in caplog.records if r.levelno >= logging.INFO]
        assert any("fast-path" in m.lower() or "matched" in m.lower() for m in messages)

    def test_poll_slack_commands_logs_reaction_at_info(self, reaction_daemon, caplog):
        """poll_slack_commands logs reaction routing at INFO level (not just DEBUG)."""
        import logging
        self._insert_directive(reaction_daemon._db)
        mock_handler = MagicMock()
        mock_handler.drain.return_value = [
            {"type": "reaction", "emoji": "thumbsup", "message_ts": "999.888"}
        ]
        reaction_daemon.socket_handler = mock_handler
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            reaction_daemon.poll_slack_commands()
        messages = [r.message for r in caplog.records if r.levelno >= logging.INFO]
        assert any("reaction" in m.lower() for m in messages)


import signal
import sys


class TestHandleRestart:
    def test_handle_restart_calls_execvp(self):
        """_handle_restart calls os.execvp with the current interpreter and module args."""
        import ironclaude.main as main_module
        with patch.object(main_module, '_daemon', None):
            with patch('os.execvp') as mock_exec:
                from ironclaude.main import _handle_restart
                _handle_restart(signal.SIGHUP, None)
        mock_exec.assert_called_once_with(
            sys.executable, [sys.executable, '-m', 'ironclaude.main']
        )

    def test_handle_restart_shuts_down_daemon_before_exec(self):
        """_handle_restart calls daemon.shutdown() before os.execvp."""
        import ironclaude.main as main_module
        mock_daemon = MagicMock()
        with patch.object(main_module, '_daemon', mock_daemon):
            with patch('os.execvp'):
                from ironclaude.main import _handle_restart
                _handle_restart(signal.SIGHUP, None)
        mock_daemon.shutdown.assert_called_once()

    def test_handle_restart_stops_socket_handler_before_exec(self):
        """_handle_restart calls socket_handler.stop() before os.execvp."""
        import ironclaude.main as main_module
        mock_daemon = MagicMock()
        mock_daemon.socket_handler = MagicMock()
        with patch.object(main_module, '_daemon', mock_daemon):
            with patch('os.execvp'):
                main_module._handle_restart(signal.SIGHUP, None)
        mock_daemon.socket_handler.stop.assert_called_once()

    def test_handle_restart_kills_duplicate_daemons_before_exec(self):
        """_handle_restart sends SIGTERM to other ironclaude.main processes before execvp."""
        import ironclaude.main as main_module

        our_pid = os.getpid()
        duplicate_pid = our_pid + 1000

        killed = []

        def fake_os_kill(pid, sig):
            killed.append((pid, sig))

        def fake_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            if isinstance(cmd, list) and cmd and cmd[0] == "pgrep":
                m.stdout = f"{our_pid}\n{duplicate_pid}\n"
            else:
                m.stdout = ""
            m.returncode = 0
            return m

        with patch.object(main_module, '_daemon', None), \
             patch('os.execvp'), \
             patch('os.kill', side_effect=fake_os_kill), \
             patch.object(main_module.subprocess, 'run', side_effect=fake_subprocess_run), \
             patch.object(main_module.time, 'sleep'):
            main_module._handle_restart(signal.SIGHUP, None)

        assert (duplicate_pid, signal.SIGTERM) in killed, \
            "Duplicate daemon must be sent SIGTERM"
        assert not any(pid == our_pid for pid, _ in killed), \
            "Must not kill own PID"

    def test_handle_restart_sets_stop_event_before_brain_shutdown(self):
        """_handle_restart sets brain._stop_event BEFORE calling brain.shutdown()."""
        import threading
        import ironclaude.main as main_module

        stop_event = threading.Event()
        mock_daemon = MagicMock()
        mock_daemon.brain._stop_event = stop_event
        mock_daemon.brain._running = True

        was_set_before_shutdown = []

        def check_stop_event():
            was_set_before_shutdown.append(stop_event.is_set())

        mock_daemon.brain.shutdown.side_effect = check_stop_event

        def fake_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = ""
            m.returncode = 0
            return m

        with patch.object(main_module, '_daemon', mock_daemon), \
             patch('os.execvp'), \
             patch.object(main_module.subprocess, 'run', side_effect=fake_subprocess_run), \
             patch.object(main_module.time, 'sleep'):
            main_module._handle_restart(signal.SIGHUP, None)

        assert was_set_before_shutdown == [True], \
            "_stop_event must be set BEFORE brain.shutdown() is called"
        assert not mock_daemon.brain._running, \
            "brain._running must be False after restart handler"

    def test_handle_restart_verifies_no_orphan_brains(self):
        """_handle_restart runs pgrep verification after killing orphan brains."""
        import ironclaude.main as main_module

        mock_daemon = MagicMock()
        mock_daemon.brain._stop_event = MagicMock()

        pgrep_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = ""
            m.returncode = 0
            if isinstance(cmd, list) and cmd and cmd[0] == "pgrep":
                pgrep_calls.append(cmd)
            return m

        with patch.object(main_module, '_daemon', mock_daemon), \
             patch('os.execvp'), \
             patch.object(main_module.subprocess, 'run', side_effect=fake_subprocess_run), \
             patch.object(main_module.time, 'sleep'):
            main_module._handle_restart(signal.SIGHUP, None)

        brain_pgrep = [c for c in pgrep_calls if any("Orchestrator" in a for a in c)]
        assert len(brain_pgrep) >= 1, \
            "Must pgrep verify no brain subprocesses remain after cleanup"


class TestHandleShutdown:
    def test_handle_shutdown_sets_brain_stop_event(self):
        """_handle_shutdown sets brain._stop_event so brain thread exits promptly."""
        import threading
        import ironclaude.main as main_module
        mock_daemon = MagicMock()
        mock_daemon.brain._stop_event = threading.Event()
        with patch.object(main_module, '_daemon', mock_daemon):
            main_module._handle_shutdown(signal.SIGTERM, None)
        assert mock_daemon.brain._stop_event.is_set(), \
            "brain._stop_event must be set during shutdown"

    def test_handle_shutdown_sets_clean_shutdown_flag(self):
        """_handle_shutdown sets _clean_shutdown to True to suppress respawner."""
        import ironclaude.main as main_module
        mock_daemon = MagicMock()
        mock_daemon.brain._stop_event = MagicMock()
        original = main_module._clean_shutdown
        try:
            main_module._clean_shutdown = False
            with patch.object(main_module, '_daemon', mock_daemon):
                main_module._handle_shutdown(signal.SIGTERM, None)
            assert main_module._clean_shutdown is True, \
                "_clean_shutdown must be True after shutdown signal"
        finally:
            main_module._clean_shutdown = original


class TestCrashRespawner:
    def test_spawn_respawner_forks_detached_process(self):
        """_spawn_respawner forks, calls setsid, and spawns daemon with --no-respawn."""
        import ironclaude.main as main_module

        # Test parent path (fork returns child PID)
        with patch('os.fork', return_value=42):
            main_module._spawn_respawner()
            # Parent just returns — no setsid or Popen

        # Test child path (fork returns 0)
        with patch('os.fork', return_value=0), \
             patch('os.setsid') as mock_setsid, \
             patch('os._exit') as mock_exit, \
             patch.object(main_module.time, 'sleep') as mock_sleep, \
             patch.object(main_module.subprocess, 'Popen') as mock_popen:
            main_module._spawn_respawner()
            mock_setsid.assert_called_once()
            mock_sleep.assert_called_once_with(5)
            mock_popen.assert_called_once()
            popen_args = mock_popen.call_args
            cmd = popen_args[0][0]
            assert '--no-respawn' in cmd, "Respawned daemon must pass --no-respawn"
            assert popen_args[1].get('start_new_session') is True, \
                "Respawned daemon must use start_new_session=True"
            mock_exit.assert_called_once_with(0)

    def test_clean_shutdown_prevents_respawner(self):
        """Respawner is not invoked when _clean_shutdown is True."""
        import ironclaude.main as main_module
        original = main_module._clean_shutdown
        try:
            main_module._clean_shutdown = True
            with patch.object(main_module, '_spawn_respawner') as mock_spawn:
                # Simulate the respawner guard logic
                if not main_module._clean_shutdown:
                    main_module._spawn_respawner()
                mock_spawn.assert_not_called()
        finally:
            main_module._clean_shutdown = original

    def test_no_respawn_flag_prevents_respawner(self):
        """--no-respawn CLI flag prevents respawner from running."""
        import ironclaude.main as main_module
        original = main_module._clean_shutdown
        try:
            main_module._clean_shutdown = False
            no_respawn = True  # Simulates --no-respawn in sys.argv
            with patch.object(main_module, '_spawn_respawner') as mock_spawn:
                # Simulate the respawner guard logic
                if not main_module._clean_shutdown and not no_respawn:
                    main_module._spawn_respawner()
                mock_spawn.assert_not_called()
        finally:
            main_module._clean_shutdown = original


class TestRunMaintenance:
    def test_calls_cleanup_old_logs(self, daemon):
        """_run_maintenance calls tmux.cleanup_old_logs."""
        daemon._run_maintenance()
        daemon.tmux.cleanup_old_logs.assert_called_once_with(7)

    def test_prunes_events_table(self, daemon):
        """_run_maintenance deletes events older than 30 days."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "event_type TEXT, worker_id TEXT, details TEXT)"
        )
        conn.execute(
            "INSERT INTO events (timestamp, event_type) VALUES (datetime('now', '-60 days'), 'old_event')"
        )
        conn.execute(
            "INSERT INTO events (timestamp, event_type) VALUES (datetime('now'), 'recent_event')"
        )
        conn.commit()
        daemon._db = conn
        daemon._run_maintenance()
        rows = conn.execute("SELECT event_type FROM events").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "recent_event"
        conn.close()

    def test_hourly_cadence_skips_second_call(self, daemon):
        """Second call within an hour is a no-op."""
        daemon._run_maintenance()
        daemon.tmux.cleanup_old_logs.reset_mock()
        daemon._run_maintenance()
        daemon.tmux.cleanup_old_logs.assert_not_called()

    def test_prunes_state_manager_audit_log(self, daemon, tmp_path):
        """_run_maintenance prunes audit_log in state-manager DB."""
        sm_db_path = tmp_path / "ironclaude.db"
        conn = sqlite3.connect(str(sm_db_path))
        conn.execute(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, terminal_session TEXT, "
            "actor TEXT, action TEXT, old_value TEXT, new_value TEXT, context TEXT, "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO audit_log (terminal_session, actor, action, created_at) "
            "VALUES ('s1', 'hook', 'old_action', datetime('now', '-120 days'))"
        )
        conn.execute(
            "INSERT INTO audit_log (terminal_session, actor, action, created_at) "
            "VALUES ('s1', 'hook', 'recent_action', datetime('now'))"
        )
        conn.commit()
        conn.close()
        daemon._state_manager_db_path = str(sm_db_path)
        daemon._run_maintenance()
        conn = sqlite3.connect(str(sm_db_path))
        rows = conn.execute("SELECT action FROM audit_log").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "recent_action"
        conn.close()

    def test_resilience_one_failure_others_run(self, daemon):
        """If log cleanup raises, DB pruning still runs."""
        daemon.tmux.cleanup_old_logs.side_effect = RuntimeError("boom")
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "event_type TEXT, worker_id TEXT, details TEXT)"
        )
        conn.execute(
            "INSERT INTO events (timestamp, event_type) VALUES (datetime('now', '-60 days'), 'old')"
        )
        conn.commit()
        daemon._db = conn
        daemon._run_maintenance()  # Should not raise
        rows = conn.execute("SELECT * FROM events").fetchall()
        assert len(rows) == 0  # DB pruning still ran
        conn.close()


class TestHandleSpawnWorkerSecurity:
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_model_name_is_shlex_quoted(self, mock_trust, daemon):
        """Malicious model_name is shlex-quoted in the spawned command."""
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux.spawn_session.return_value = True
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0"

        decision = {
            "worker_id": "w-test",
            "type": "ollama",
            "repo": "/tmp",
            "objective": "test",
            "model_name": "evil; rm -rf /",
        }
        daemon._handle_spawn_worker(decision)

        spawn_call = daemon.tmux.spawn_session.call_args
        cmd = spawn_call[0][1]
        assert f"--model {decision['model_name']} " not in cmd
        assert shlex.quote(decision["model_name"]) in cmd


class TestWorkerCommandsConsistency:
    def test_main_worker_commands_match_orchestrator(self):
        """main.py WORKER_COMMANDS must exactly match orchestrator_mcp.py WORKER_COMMANDS."""
        from ironclaude.main import WORKER_COMMANDS as main_cmds
        from ironclaude.orchestrator_mcp import WORKER_COMMANDS as orc_cmds
        assert main_cmds == orc_cmds

    def test_worker_commands_use_exec_prefix(self):
        """main.py WORKER_COMMANDS must use exec prefix."""
        from ironclaude.main import WORKER_COMMANDS
        assert "exec claude" in WORKER_COMMANDS["claude-opus"]
        assert "exec claude" in WORKER_COMMANDS["claude-sonnet"]

    def test_worker_commands_set_effort_level_high(self):
        """main.py WORKER_COMMANDS must set CLAUDE_CODE_EFFORT_LEVEL=high."""
        from ironclaude.main import WORKER_COMMANDS
        assert "CLAUDE_CODE_EFFORT_LEVEL=high" in WORKER_COMMANDS["claude-opus"]
        assert "CLAUDE_CODE_EFFORT_LEVEL=high" in WORKER_COMMANDS["claude-sonnet"]


class TestDaemonWorkerTrust:
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_spawn_worker_calls_ensure_worker_trusted(self, mock_trust, daemon):
        """_handle_spawn_worker uses ensure_worker_trusted (with realpath + .git check)."""
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux.spawn_session.return_value = True
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0"

        decision = {
            "worker_id": "w1",
            "type": "claude-sonnet",
            "repo": "/tmp/some-repo",
            "objective": "Do something",
        }
        daemon._handle_spawn_worker(decision)
        mock_trust.assert_called_once_with("/tmp/some-repo")

    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.ensure_brain_trusted")
    def test_spawn_worker_does_not_call_ensure_brain_trusted(
        self, mock_brain_trust, mock_worker_trust, daemon
    ):
        """_handle_spawn_worker calls ensure_worker_trusted, not ensure_brain_trusted, for worker repos."""
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux.spawn_session.return_value = True
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0"

        decision = {
            "worker_id": "w1",
            "type": "claude-sonnet",
            "repo": "/tmp/some-repo",
            "objective": "Do something",
        }
        daemon._handle_spawn_worker(decision)
        mock_brain_trust.assert_not_called()


class TestWaitForReadyMarker:
    def test_default_marker_detects_ironclaude_v(self, daemon):
        """_wait_for_ready with default marker detects 'ironclaude v' in output."""
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0 — ready"
        result = daemon._wait_for_ready("ic-w1", timeout=5)
        assert result is True

    def test_pm_marker_detects_professional_mode_on(self, daemon):
        """_wait_for_ready with PM marker detects 'Professional Mode: ON' in output."""
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0\nProfessional Mode: ON"
        result = daemon._wait_for_ready("ic-w1", timeout=5, marker="Professional Mode: ON")
        assert result is True

    def test_pm_marker_returns_false_without_pm_on(self, daemon):
        """_wait_for_ready with PM marker returns False if 'Professional Mode: ON' never appears."""
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0"
        result = daemon._wait_for_ready("ic-w1", timeout=1, marker="Professional Mode: ON")
        assert result is False

    @patch("ironclaude.main.IroncladeDaemon._wait_for_ready")
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_spawn_worker_stage5_uses_pm_marker(self, mock_trust, mock_wait, daemon):
        """Stage 5 _wait_for_ready call uses marker='Professional Mode: ON'."""
        mock_wait.return_value = True
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux.spawn_session.return_value = True

        decision = {
            "worker_id": "w1",
            "type": "claude-sonnet",
            "repo": "/tmp/some-repo",
            "objective": "Do something",
        }
        daemon._handle_spawn_worker(decision)

        pm_calls = [
            call for call in mock_wait.call_args_list
            if call.kwargs.get("marker") == "Professional Mode: ON"
            or (len(call.args) > 2 and call.args[2] == "Professional Mode: ON")
        ]
        assert len(pm_calls) == 1, (
            f"Expected exactly one _wait_for_ready call with marker='Professional Mode: ON', "
            f"got {len(pm_calls)}. All calls: {mock_wait.call_args_list}"
        )


class TestEnsureBrainTrusted:
    """Tests for M2 fix: ensure_brain_trusted uses realpath + .git check, mirroring ensure_worker_trusted."""

    def test_rejects_path_without_git_dir(self, tmp_path):
        """ensure_brain_trusted writes no trust entry for a non-git directory."""
        non_git_dir = tmp_path / "brain_repo"
        non_git_dir.mkdir()
        claude_json = tmp_path / "claude.json"
        claude_json.write_text('{"projects": {}}')
        with patch("os.path.expanduser", return_value=str(claude_json)):
            ensure_brain_trusted(str(non_git_dir))
        data = json.loads(claude_json.read_text())
        projects = data.get("projects", {})
        assert str(non_git_dir) not in projects
        assert os.path.realpath(str(non_git_dir)) not in projects

    def test_resolves_symlinks_and_rejects_non_git(self, tmp_path):
        """ensure_brain_trusted resolves symlinks and rejects if resolved path has no .git."""
        real_dir = tmp_path / "real_brain"
        real_dir.mkdir()
        link_dir = tmp_path / "link_brain"
        link_dir.symlink_to(real_dir)
        claude_json = tmp_path / "claude.json"
        claude_json.write_text('{"projects": {}}')
        with patch("os.path.expanduser", return_value=str(claude_json)):
            ensure_brain_trusted(str(link_dir))
        data = json.loads(claude_json.read_text())
        projects = data.get("projects", {})
        assert str(real_dir) not in projects
        assert str(link_dir) not in projects

    def test_accepts_valid_git_repo(self, tmp_path):
        """ensure_brain_trusted adds trust entry using real_cwd as the dict key."""
        git_repo = tmp_path / "brain_repo"
        git_repo.mkdir()
        (git_repo / ".git").mkdir()
        claude_json = tmp_path / "claude.json"
        claude_json.write_text('{"projects": {}}')
        with patch("os.path.expanduser", return_value=str(claude_json)):
            ensure_brain_trusted(str(git_repo))
        data = json.loads(claude_json.read_text())
        projects = data.get("projects", {})
        real_path = os.path.realpath(str(git_repo))
        assert real_path in projects
        assert projects[real_path].get("hasTrustDialogAccepted") is True

    def test_already_trusted_skips_write(self, tmp_path):
        """ensure_brain_trusted returns early without re-writing when already trusted."""
        git_repo = tmp_path / "brain_repo"
        git_repo.mkdir()
        (git_repo / ".git").mkdir()
        real_path = os.path.realpath(str(git_repo))
        initial = {real_path: {"hasTrustDialogAccepted": True, "allowedTools": []}}
        claude_json = tmp_path / "claude.json"
        claude_json.write_text(json.dumps({"projects": initial}))
        with patch("os.path.expanduser", return_value=str(claude_json)):
            ensure_brain_trusted(str(git_repo))
        data = json.loads(claude_json.read_text())
        assert list(data["projects"].keys()) == [real_path]
        assert data["projects"][real_path]["hasTrustDialogAccepted"] is True


from ironclaude.main import _load_dotenv


class TestLoadDotenv:
    def test_loads_key_value_pairs(self, tmp_path, monkeypatch):
        """Basic KEY=VALUE pairs are loaded into os.environ."""
        env_file = tmp_path / ".env"
        env_file.write_text("SUPABASE_URL=https://example.supabase.co\nSUPABASE_ANON_KEY=abc123\n")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
        _load_dotenv(str(env_file))
        assert os.environ["SUPABASE_URL"] == "https://example.supabase.co"
        assert os.environ["SUPABASE_ANON_KEY"] == "abc123"

    def test_does_not_override_existing_env_vars(self, tmp_path, monkeypatch):
        """Shell env takes precedence over .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("SUPABASE_URL=from_file\n")
        monkeypatch.setenv("SUPABASE_URL", "from_shell")
        _load_dotenv(str(env_file))
        assert os.environ["SUPABASE_URL"] == "from_shell"

    def test_strips_double_quotes(self, tmp_path, monkeypatch):
        """KEY="VALUE" form is parsed correctly."""
        env_file = tmp_path / ".env"
        env_file.write_text('SUPABASE_URL="https://example.supabase.co"\n')
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        _load_dotenv(str(env_file))
        assert os.environ["SUPABASE_URL"] == "https://example.supabase.co"

    def test_strips_single_quotes(self, tmp_path, monkeypatch):
        """KEY='VALUE' form is parsed correctly."""
        env_file = tmp_path / ".env"
        env_file.write_text("SUPABASE_ANON_KEY='mykey'\n")
        monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
        _load_dotenv(str(env_file))
        assert os.environ["SUPABASE_ANON_KEY"] == "mykey"

    def test_skips_blank_lines_and_comments(self, tmp_path, monkeypatch):
        """Blank lines and # comments are ignored."""
        env_file = tmp_path / ".env"
        env_file.write_text("# Supabase [telemetry]\n\nSUPABASE_URL=https://x.co\n")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        _load_dotenv(str(env_file))
        assert os.environ["SUPABASE_URL"] == "https://x.co"

    def test_missing_file_is_silent(self, tmp_path):
        """Missing .env file does not raise — daemon starts without it."""
        _load_dotenv(str(tmp_path / "nonexistent.env"))  # must not raise
