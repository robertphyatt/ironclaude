"""Tests for check_confirmed_directives in IroncladeDaemon."""
import sqlite3
import time
from unittest.mock import MagicMock

import pytest

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon, _worker_matches_directive


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def brain():
    b = MagicMock()
    b.send_message.return_value = True
    return b


@pytest.fixture
def daemon(db_conn, brain):
    config = {"operator_name": "TestOp"}
    d = IroncladeDaemon(
        config=config,
        slack=MagicMock(),
        socket_handler=None,
        registry=MagicMock(),
        tmux_manager=MagicMock(),
        brain=brain,
        db_conn=db_conn,
    )
    return d


def _insert_confirmed(db_conn, directive_id, interpretation, age_seconds=360):
    """Insert a directive with status='confirmed' and updated_at age_seconds ago."""
    db_conn.execute(
        "INSERT INTO directives (id, source_ts, source_text, interpretation, status, updated_at) "
        "VALUES (?, 'ts-test', 'src', ?, 'confirmed', datetime('now', ? || ' seconds'))",
        (directive_id, interpretation, f"-{age_seconds}"),
    )
    db_conn.commit()


def _insert_pending(db_conn, directive_id, interpretation, ts):
    """Insert a directive with status='pending_confirmation' and matching interpretation_ts."""
    db_conn.execute(
        "INSERT INTO directives "
        "(id, source_ts, source_text, interpretation, status, interpretation_ts) "
        "VALUES (?, 'src-ts', 'src', ?, 'pending_confirmation', ?)",
        (directive_id, interpretation, ts),
    )
    db_conn.commit()


class TestReactionNotification:
    def test_reaction_notifies_brain_on_confirmation(self, daemon, db_conn):
        """👍 reaction triggers brain.send_message with confirmation text."""
        ts = "1780078185.633749"
        _insert_pending(db_conn, 100, "Do the thing", ts)
        daemon.brain.send_message.return_value = True

        daemon._handle_directive_reaction("+1", ts)

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "#100" in msg
        assert "confirmed" in msg.lower()

    def test_reaction_marks_reminder_sent_on_delivery(self, daemon, db_conn):
        """Delivered confirmation sets _directive_reminder_sent[directive_id]."""
        ts = "1780078185.633749"
        _insert_pending(db_conn, 101, "Do another thing", ts)
        daemon.brain.send_message.return_value = True

        daemon._handle_directive_reaction("+1", ts)

        assert 101 in daemon._directive_reminder_sent

    def test_reaction_does_not_mark_when_brain_down(self, daemon, db_conn):
        """Failed delivery leaves _directive_reminder_sent[directive_id] absent."""
        ts = "1780078185.633750"
        _insert_pending(db_conn, 102, "Third thing", ts)
        daemon.brain.send_message.return_value = False

        daemon._handle_directive_reaction("+1", ts)

        assert 102 not in daemon._directive_reminder_sent


class TestCheckConfirmedDirectives:
    def test_sends_reminder_when_no_worker(self, daemon, db_conn):
        """Confirmed directive previously notified but no worker fires ACTION REQUIRED reminder."""
        _insert_confirmed(db_conn, 999, "Do something", age_seconds=360)
        daemon.registry.get_running_workers.return_value = []
        # Seed as previously notified 11 minutes ago so reminder path fires
        daemon._directive_reminder_sent[999] = time.time() - 660

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "#999" in msg
        assert "ACTION REQUIRED" in msg

    def test_no_reminder_when_worker_has_directive_id(self, daemon, db_conn):
        """No reminder when a running worker's description contains #N."""
        _insert_confirmed(db_conn, 888, "Do something else", age_seconds=360)
        daemon.registry.get_running_workers.return_value = [
            {"id": "w1", "tmux_session": "ic-w1", "description": "Implement directive #888"},
        ]

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_not_called()

    def test_immediate_notification_when_never_notified(self, daemon, db_conn):
        """Confirmed directive with no prior notification fires immediately regardless of age."""
        _insert_confirmed(db_conn, 333, "Fresh directive", age_seconds=30)
        daemon.registry.get_running_workers.return_value = []

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "#333" in msg

    def test_dedup_suppresses_second_reminder(self, daemon, db_conn):
        """Second call within 10 minutes does not send a second reminder."""
        _insert_confirmed(db_conn, 666, "Dedup test", age_seconds=360)
        daemon.registry.get_running_workers.return_value = []

        daemon.check_confirmed_directives()
        assert daemon.brain.send_message.call_count == 1

        daemon.brain.send_message.reset_mock()
        daemon.check_confirmed_directives()
        daemon.brain.send_message.assert_not_called()

    def test_dedup_resends_after_interval(self, daemon, db_conn):
        """Reminder re-fires after 10 minutes have elapsed since last send."""
        _insert_confirmed(db_conn, 555, "Resend test", age_seconds=360)
        daemon.registry.get_running_workers.return_value = []

        # Seed as if sent 11 minutes ago
        daemon._directive_reminder_sent[555] = time.time() - 660

        daemon.check_confirmed_directives()
        daemon.brain.send_message.assert_called_once()

    def test_skips_worker_with_null_description(self, daemon, db_conn):
        """Worker with None description does not crash the worker_found check."""
        _insert_confirmed(db_conn, 444, "Null desc test", age_seconds=360)
        daemon.registry.get_running_workers.return_value = [
            {"id": "w2", "tmux_session": "ic-w2", "description": None},
        ]

        daemon.check_confirmed_directives()

        # None description treated as no match — reminder still fires
        daemon.brain.send_message.assert_called_once()

    def test_no_reminder_when_worker_id_has_directive_prefix(self, daemon, db_conn):
        """No reminder when a running worker's ID is prefixed with d{N}-."""
        _insert_confirmed(db_conn, 900, "Persist knowledge", age_seconds=360)
        daemon.registry.get_running_workers.return_value = [
            {"id": "d900-knowledge-persistence", "tmux_session": "ic-d900-kp", "description": ""},
        ]

        daemon.check_confirmed_directives()

        daemon.brain.send_message.assert_not_called()
