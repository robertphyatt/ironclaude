"""Tests for stuck worker detection."""
import os
import sqlite3
import time
from unittest.mock import MagicMock, patch

import psutil
import pytest

from ironclaude.db import init_db
from ironclaude.main import (
    IroncladeDaemon,
    STALENESS_ALERT_SECONDS,
    STALENESS_KILL_SECONDS,
    STALENESS_PROMPT_ALERT,
    STALENESS_PROMPT_KILL,
    STALENESS_CHECK_INTERVAL,
    STAGE_STALENESS_MULTIPLIER,
)


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def daemon(tmp_path, db_conn):
    config = {"tmp_dir": str(tmp_path)}
    slack = MagicMock()
    registry = MagicMock()
    registry.get_running_workers.return_value = []
    tmux = MagicMock()
    tmux.log_dir = str(tmp_path / "logs")
    os.makedirs(tmux.log_dir, exist_ok=True)
    brain = MagicMock()
    brain.send_message.return_value = True
    d = IroncladeDaemon(
        config=config, slack=slack, socket_handler=None,
        registry=registry, tmux_manager=tmux, brain=brain,
        db_conn=db_conn,
    )
    return d


class TestStalenessSchema:
    def test_init_db_creates_worker_staleness_table(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_staleness'"
        ).fetchall()
        assert len(tables) == 1
        conn.close()


class TestStalenessStatePersistence:
    def test_persist_and_load_round_trip(self, daemon):
        daemon._stuck_hash["w1"] = 12345
        daemon._stuck_since["w1"] = 1000.0
        daemon._stuck_alert_sent["w1"] = True
        daemon._persist_staleness_state("w1")

        daemon._stuck_hash.clear()
        daemon._stuck_since.clear()
        daemon._stuck_alert_sent.clear()

        daemon._load_staleness_state()
        assert daemon._stuck_hash["w1"] == 12345
        assert daemon._stuck_since["w1"] == 1000.0
        assert daemon._stuck_alert_sent["w1"] is True

    def test_persist_deletes_removed_worker(self, daemon):
        daemon._stuck_hash["w1"] = 12345
        daemon._stuck_since["w1"] = 1000.0
        daemon._persist_staleness_state("w1")

        del daemon._stuck_since["w1"]
        daemon._persist_staleness_state("w1")

        rows = daemon._db.execute("SELECT * FROM worker_staleness").fetchall()
        assert len(rows) == 0

    def test_persist_failure_logs_warning(self, daemon, caplog):
        daemon._stuck_since["w1"] = 1000.0
        daemon._stuck_hash["w1"] = 123
        daemon._stuck_alert_sent["w1"] = False
        daemon._db.execute("DROP TABLE worker_staleness")
        daemon._persist_staleness_state("w1")
        assert "Failed to persist" in caplog.text


class TestCheckStuckWorkers:
    def _make_worker(self, wid="w1"):
        return {"id": wid, "tmux_session": f"ic-{wid}", "spawned_at": "2026-06-10T00:00:00"}

    def test_hash_change_resets_staleness(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "output A"

        daemon.check_stuck_workers()
        daemon.tmux.capture_pane.return_value = "output B"
        daemon._last_stuck_check = 0.0
        daemon.check_stuck_workers()

        assert daemon._stuck_alert_sent.get("w1") is False
        daemon.brain.send_message.assert_not_called()

    def test_stale_below_threshold_no_action(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "static output"

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 1200  # 20min

        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_not_called()

    def test_stale_at_alert_threshold_sends_brain_message(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "static output"
        daemon._get_worker_workflow_stage = MagicMock(return_value="idle")

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 1801  # just over 30min

        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "STUCK" in msg
        assert "w1" in msg

    def test_alert_not_re_sent(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "static output"
        daemon._get_worker_workflow_stage = MagicMock(return_value="idle")

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 2000
        daemon.check_stuck_workers()

        daemon.brain.send_message.reset_mock()
        daemon._last_stuck_check = 0.0
        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_not_called()

    def test_prompt_waiting_accelerated_alert(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "AskUserQuestion some prompt"
        daemon._get_worker_workflow_stage = MagicMock(return_value="executing")
        daemon._grader.grade = MagicMock(return_value={"waiting": True})

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 901  # just over 15min

        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "STUCK" in msg

    def test_prompt_waiting_accelerated_kill(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "AskUserQuestion some prompt"
        daemon._get_worker_workflow_stage = MagicMock(return_value="executing")
        daemon._confirm_and_kill_stuck_worker = MagicMock()
        daemon._grader.grade = MagicMock(return_value={"waiting": True})

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 1801  # just over 30min
        daemon._stuck_alert_sent["w1"] = True

        daemon.check_stuck_workers()
        daemon._confirm_and_kill_stuck_worker.assert_called_once()

    def test_no_prompt_uses_default_thresholds(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "normal output no prompt"
        daemon._get_worker_workflow_stage = MagicMock(return_value="idle")

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 1000  # ~17min, above prompt threshold but below default

        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_not_called()

    def test_executing_stage_extends_threshold(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "normal output"
        daemon._get_worker_workflow_stage = MagicMock(return_value="executing")

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 2000  # 33min, above default 30 but below 45 (30*1.5)

        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_not_called()

    def test_brainstorming_stage_shortens_threshold(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "normal output"
        daemon._get_worker_workflow_stage = MagicMock(return_value="brainstorming")

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        # 0.75 * 1800 = 1350. Set to 1400 (above 1350 threshold)
        daemon._stuck_since["w1"] = time.time() - 1400

        daemon.check_stuck_workers()
        daemon.brain.send_message.assert_called_once()

    def test_cleanup_removes_dead_worker_entries(self, daemon):
        daemon._stuck_since["dead-worker"] = 1000.0
        daemon._stuck_hash["dead-worker"] = 999
        daemon._stuck_alert_sent["dead-worker"] = True
        daemon.registry.get_running_workers.return_value = []

        daemon.check_stuck_workers()

        assert "dead-worker" not in daemon._stuck_since
        assert "dead-worker" not in daemon._stuck_hash


from ironclaude.notifications import format_worker_stuck_killed


class TestConfirmAndKillStuckWorker:
    def _make_worker(self, wid="w1"):
        return {"id": wid, "tmux_session": f"ic-{wid}", "spawned_at": "2026-06-10T00:00:00"}

    def test_liveness_check_blocks_kill(self, daemon):
        daemon._stuck_since["w1"] = time.time() - 3700
        daemon._stuck_hash["w1"] = 123
        daemon._stuck_alert_sent["w1"] = True
        daemon.tmux.list_pane_pid.return_value = "9999"

        mock_child = MagicMock()
        mock_child.cpu_percent.side_effect = [None, 5.0]

        with patch("ironclaude.main.psutil") as mock_psutil:
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_psutil.Process.return_value = mock_parent
            mock_psutil.NoSuchProcess = psutil.NoSuchProcess
            mock_psutil.AccessDenied = psutil.AccessDenied

            daemon._confirm_and_kill_stuck_worker("w1", "ic-w1", 3700, "executing", False, None)

        daemon.tmux.kill_session.assert_not_called()
        assert "w1" in daemon._stuck_kill_deferred

    def test_liveness_extension_eventually_kills(self, daemon):
        daemon._stuck_since["w1"] = time.time() - 3700
        daemon._stuck_hash["w1"] = 123
        daemon._stuck_alert_sent["w1"] = True
        daemon.tmux.list_pane_pid.return_value = "9999"

        mock_child = MagicMock()
        mock_child.cpu_percent.side_effect = [None, 0.0]

        with patch("ironclaude.main.psutil") as mock_psutil:
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_psutil.Process.return_value = mock_parent
            mock_psutil.NoSuchProcess = psutil.NoSuchProcess
            mock_psutil.AccessDenied = psutil.AccessDenied

            daemon._confirm_and_kill_stuck_worker("w1", "ic-w1", 3700, "executing", False, None)

        daemon.tmux.kill_session.assert_called_once_with("ic-w1", ssh_host=None)
        daemon.registry.update_worker_status.assert_called_once_with("w1", "completed")
        daemon.slack.post_message.assert_called_once()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "MANDATORY SWEEP" in msg

    def test_worker_recovers_mid_escalation(self, daemon):
        worker = self._make_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "static output"
        daemon._get_worker_workflow_stage = MagicMock(return_value="idle")

        daemon.check_stuck_workers()
        daemon._last_stuck_check = 0.0
        daemon._stuck_since["w1"] = time.time() - 2000
        daemon.check_stuck_workers()
        assert daemon._stuck_alert_sent["w1"] is True

        daemon._last_stuck_check = 0.0
        daemon.tmux.capture_pane.return_value = "NEW output — worker recovered"
        daemon.check_stuck_workers()
        assert daemon._stuck_alert_sent["w1"] is False

    def test_stuck_kill_triggers_mandatory_sweep_with_directives(self, daemon, db_conn):
        db_conn.execute(
            "INSERT INTO directives (id, source_ts, source_text, interpretation, status) "
            "VALUES (1, 'ts', 'src', 'Build feature', 'confirmed')"
        )
        db_conn.commit()
        daemon.tmux.list_pane_pid.return_value = None

        daemon._confirm_and_kill_stuck_worker("w1", "ic-w1", 3700, "idle", True, None)

        msg = daemon.brain.send_message.call_args[0][0]
        assert "MANDATORY SWEEP" in msg
        assert "#1" in msg
        assert "Build feature" in msg


class TestFormatWorkerStuckKilled:
    def test_format_includes_all_fields(self):
        result = format_worker_stuck_killed("w1", 62, "executing", False)
        assert "w1" in result
        assert "62" in result
        assert "executing" in result
        assert "no" in result.lower()

    def test_format_prompt_waiting(self):
        result = format_worker_stuck_killed("w2", 30, "brainstorming", True)
        assert "yes" in result.lower()
