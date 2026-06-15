"""Tests for daemon idle enforcement mechanisms."""
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from ironclaude.db import init_db
from ironclaude.orchestrator_mcp import OrchestratorTools


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def mock_registry():
    reg = MagicMock()
    reg.get_worker.return_value = {"id": "test-1", "tmux_session": "ic-test-1", "spawned_at": "2026-06-07T10:00:00"}
    reg.get_running_workers.return_value = []
    return reg


@pytest.fixture
def mock_tmux():
    tmux = MagicMock()
    tmux.list_pane_pid.return_value = "12345"
    return tmux


@pytest.fixture
def orchestrator(db_conn, mock_registry, mock_tmux):
    return OrchestratorTools(
        registry=mock_registry,
        tmux=mock_tmux,
        ledger_path="",
        db_conn=db_conn,
    )


def _insert_directive(db_conn, directive_id, status, interpretation):
    db_conn.execute(
        "INSERT INTO directives (id, source_ts, source_text, interpretation, status) "
        "VALUES (?, 'ts-test', 'src', ?, ?)",
        (directive_id, interpretation, status),
    )
    db_conn.commit()


class TestKillWorkerEnrichment:
    def test_kill_worker_returns_remaining_directives(self, orchestrator, db_conn, mock_registry):
        """After successful kill, response includes unworked directives."""
        _insert_directive(db_conn, 5, "confirmed", "Build feature X")
        _insert_directive(db_conn, 3, "in_progress", "Fix bug Y")
        mock_registry.get_running_workers.return_value = []

        result = orchestrator.kill_worker("test-1")

        assert isinstance(result, dict)
        assert "remaining_work" in result
        assert result["remaining_work"]["action_required"] is True
        directives = result["remaining_work"]["unworked_directives"]
        assert len(directives) == 2
        ids = {d["id"] for d in directives}
        assert ids == {5, 3}

    def test_kill_worker_no_remaining_work(self, orchestrator, db_conn, mock_registry):
        """When no pending directives exist, action_required is False."""
        _insert_directive(db_conn, 1, "completed", "Done task")
        mock_registry.get_running_workers.return_value = []

        result = orchestrator.kill_worker("test-1")

        assert isinstance(result, dict)
        assert result["remaining_work"]["action_required"] is False
        assert result["remaining_work"]["unworked_directives"] == []

    def test_kill_worker_grader_rejection_no_enrichment(self, orchestrator, db_conn):
        """When grader rejects kill, no remaining_work is included."""
        _insert_directive(db_conn, 5, "confirmed", "Build feature X")

        with patch.object(orchestrator, "_call_grader", return_value={"grade": "D", "approved": False, "feedback": "Incomplete"}):
            result = orchestrator.kill_worker("test-1", original_objective="Do X", evidence="I did it")

        assert isinstance(result, dict)
        assert "error" in result
        assert "remaining_work" not in result


from ironclaude.main import IroncladeDaemon


@pytest.fixture
def brain():
    b = MagicMock()
    b.send_message.return_value = True
    return b


@pytest.fixture
def daemon(db_conn, brain):
    config = {"operator_name": "TestOp"}
    slack = MagicMock()
    slack._operator_user_id = "U_OPERATOR"
    d = IroncladeDaemon(
        config=config,
        slack=slack,
        socket_handler=None,
        registry=MagicMock(),
        tmux_manager=MagicMock(),
        brain=brain,
        db_conn=db_conn,
    )
    return d


def _insert_confirmed_directive(db_conn, directive_id, interpretation):
    db_conn.execute(
        "INSERT INTO directives (id, source_ts, source_text, interpretation, status) "
        "VALUES (?, 'ts-test', 'src', ?, 'confirmed')",
        (directive_id, interpretation),
    )
    db_conn.commit()


class TestIdleEnforcement:
    def test_idle_escalation_tier1(self, daemon, db_conn):
        """Zero workers + confirmed directive → INFO nudge to Brain."""
        _insert_confirmed_directive(db_conn, 1, "Build feature")
        daemon.registry.get_recent_workers.return_value = []
        daemon.tmux.has_session.return_value = False

        daemon.check_idle_enforcement()

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "1" in msg
        assert "unworked" in msg.lower() or "directive" in msg.lower()

    def test_idle_escalation_tier2(self, daemon, db_conn):
        """240s idle → WARNING with directive list."""
        _insert_confirmed_directive(db_conn, 7, "Fix performance")
        daemon.registry.get_recent_workers.return_value = []
        daemon.tmux.has_session.return_value = False
        daemon._idle_enforcement_start = time.time() - 240
        daemon._idle_escalation_tier = 1
        daemon._last_idle_check = 0.0

        daemon.check_idle_enforcement()

        msg = daemon.brain.send_message.call_args[0][0]
        assert "WARNING" in msg or "warning" in msg.lower()
        assert "#7" in msg or "7" in msg

    def test_idle_escalation_tier3_notifies_operator(self, daemon, db_conn):
        """700s idle → CRITICAL message + operator notification."""
        _insert_confirmed_directive(db_conn, 2, "Deploy service")
        daemon.registry.get_recent_workers.return_value = []
        daemon.tmux.has_session.return_value = False
        daemon._idle_enforcement_start = time.time() - 700
        daemon._idle_escalation_tier = 2
        daemon._last_idle_check = 0.0

        daemon.check_idle_enforcement()

        msg = daemon.brain.send_message.call_args[0][0]
        assert "CRITICAL" in msg
        daemon.slack.post_message.assert_called()
        slack_msg = daemon.slack.post_message.call_args[0][0]
        assert "idle" in slack_msg.lower() or "alert" in slack_msg.lower()

    def test_idle_resets_when_worker_spawns(self, daemon, db_conn):
        """Worker present → idle state resets."""
        _insert_confirmed_directive(db_conn, 1, "Build feature")
        daemon._idle_enforcement_start = time.time() - 500
        daemon._idle_escalation_tier = 2
        daemon._last_idle_check = 0.0
        daemon.registry.get_recent_workers.return_value = [
            {"id": "w1", "tmux_session": "ic-w1"}
        ]
        daemon.tmux.has_session.return_value = True

        daemon.check_idle_enforcement()

        assert daemon._idle_escalation_tier == 0
        assert daemon._idle_enforcement_start == 0.0
        daemon.brain.send_message.assert_not_called()

    def test_idle_throttles_to_120s(self, daemon, db_conn):
        """Two calls within 120s → only one message sent."""
        _insert_confirmed_directive(db_conn, 1, "Build feature")
        daemon.registry.get_recent_workers.return_value = []
        daemon.tmux.has_session.return_value = False

        daemon.check_idle_enforcement()
        daemon.brain.send_message.reset_mock()
        daemon.check_idle_enforcement()

        daemon.brain.send_message.assert_not_called()
