"""Tests for daemon enforcement mechanisms (d1048 + d1054)."""

import os
import sqlite3
import time

import pytest
from unittest.mock import MagicMock

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon


@pytest.fixture
def db_conn(tmp_path):
    """SQLite DB with full schema in tmp dir."""
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return conn


@pytest.fixture
def daemon(db_conn, tmp_path):
    """IroncladeDaemon with real DB and mock Slack/Brain/Registry/Tmux."""
    config = {
        "tmp_dir": str(tmp_path),
        "slack_operator_user_id": "U_OPERATOR",
    }
    slack = MagicMock()
    registry = MagicMock()
    tmux = MagicMock()
    tmux.log_dir = str(tmp_path / "logs")
    os.makedirs(tmux.log_dir, exist_ok=True)
    brain = MagicMock()
    brain.get_token_usage.return_value = {
        "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
    }
    brain.send_message.return_value = True
    d = IroncladeDaemon(config, slack, None, registry, tmux, brain, db_conn=db_conn)
    return d


class TestGetUnprocessedMessages:
    def test_returns_old_messages_without_directives(self, daemon):
        """Messages >30min old with no matching directive source_ts are returned."""
        old_ts = str(time.time() - 2400)  # 40 min ago
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix the login bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        result = daemon._get_unprocessed_messages()
        assert len(result) == 1
        assert result[0]["ts"] == old_ts

    def test_excludes_recent_messages(self, daemon):
        """Messages <30min old are excluded even without directives."""
        recent_ts = str(time.time() - 600)  # 10 min ago
        daemon.slack.get_recent_messages.return_value = [
            {"text": "New task", "ts": recent_ts, "user": "U_OPERATOR"},
        ]
        result = daemon._get_unprocessed_messages()
        assert len(result) == 0

    def test_excludes_messages_with_matching_directive(self, daemon):
        """Messages with a matching directive source_ts are excluded."""
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix the login bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        daemon._db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, ?, ?)",
            (old_ts, "Fix the login bug", "Fix auth bug"),
        )
        daemon._db.commit()
        result = daemon._get_unprocessed_messages()
        assert len(result) == 0

    def test_excludes_non_operator_messages(self, daemon):
        """Messages from non-operator users are excluded."""
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Random message", "ts": old_ts, "user": "U_OTHER"},
        ]
        result = daemon._get_unprocessed_messages()
        assert len(result) == 0

    def test_returns_empty_on_slack_failure(self, daemon):
        """Returns empty list when Slack API fails."""
        daemon.slack.get_recent_messages.side_effect = Exception("Slack down")
        result = daemon._get_unprocessed_messages()
        assert result == []


class TestValidateBrainMessage:
    def test_valid_message_with_hash_ref_and_reason(self, daemon):
        msg = "Status update for #42: worker completed testing and all tests pass."
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is True
        assert reason == ""

    def test_valid_message_with_d_ref(self, daemon):
        msg = "d1048 progress — investigating root cause of idle detection failure."
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is True

    def test_valid_message_with_directive_ref(self, daemon):
        msg = "Directive #7 update: spawning worker to handle auth refactor."
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is True

    def test_missing_directive_ref(self, daemon):
        msg = "Everything is going well, tests pass."
        daemon._grader.grade = MagicMock(return_value={"valid": False, "reason": "Missing directive reference"})
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is False
        assert "directive reference" in reason.lower()

    def test_missing_reason_keyword(self, daemon):
        msg = "About #42 — nothing else to say."
        daemon._grader.grade = MagicMock(return_value={"valid": False, "reason": "Missing reason clause"})
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is False
        assert "reason" in reason.lower()

    def test_missing_both(self, daemon):
        msg = "Hello world"
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is False

    def test_empty_message(self, daemon):
        valid, reason = daemon._validate_brain_message("")
        assert valid is False

    @pytest.mark.parametrize("ref", ["#42", "d42", "D42", "directive 42", "directive #42"])
    def test_directive_ref_patterns(self, daemon, ref):
        msg = f"{ref} status update — testing completed successfully."
        valid, reason = daemon._validate_brain_message(msg)
        assert valid is True, f"Pattern '{ref}' should be recognized as directive reference"


class TestCheckPostKillSweep:
    def test_sends_message_when_pending_work(self, daemon, db_conn):
        """Sweep message sent when worker_finished event exists with pending directives."""
        daemon._last_kill_sweep_check = time.time() - 60
        db_conn.execute(
            "INSERT INTO events (event_type, worker_id) VALUES ('worker_finished', 'w1')"
        )
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1234', 'Fix bug', 'Fix auth bug', 'confirmed')"
        )
        db_conn.commit()
        daemon.check_post_kill_sweep()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[MANDATORY SWEEP]" in msg
        assert "w1" in msg

    def test_no_message_when_no_pending_work(self, daemon, db_conn):
        """No sweep message when worker_finished but no pending directives."""
        daemon._last_kill_sweep_check = time.time() - 60
        db_conn.execute(
            "INSERT INTO events (event_type, worker_id) VALUES ('worker_finished', 'w1')"
        )
        db_conn.commit()
        daemon.check_post_kill_sweep()
        daemon.brain.send_message.assert_not_called()

    def test_skips_already_processed_events(self, daemon, db_conn):
        """Events before _last_kill_sweep_check are skipped."""
        db_conn.execute(
            "INSERT INTO events (event_type, worker_id) VALUES ('worker_finished', 'w1')"
        )
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1234', 'Fix bug', 'Fix auth bug', 'confirmed')"
        )
        db_conn.commit()
        daemon._last_kill_sweep_check = time.time() + 60  # future = skip all
        daemon.check_post_kill_sweep()
        daemon.brain.send_message.assert_not_called()

    def test_multiple_kills_send_multiple_messages(self, daemon, db_conn):
        """Each worker_finished event triggers its own sweep message."""
        daemon._last_kill_sweep_check = time.time() - 60
        for wid in ("w1", "w2", "w3"):
            db_conn.execute(
                "INSERT INTO events (event_type, worker_id) VALUES ('worker_finished', ?)",
                (wid,),
            )
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1234', 'Fix bug', 'Fix auth bug', 'confirmed')"
        )
        db_conn.commit()
        daemon.check_post_kill_sweep()
        assert daemon.brain.send_message.call_count == 3


class TestIdleEnforcement:
    def test_fires_on_pending_ledger_tasks(self, daemon, tmp_path):
        """Idle enforcement fires when zero workers + pending ledger tasks."""
        import json as _json
        ledger_path = str(tmp_path / "task-ledger.json")
        with open(ledger_path, "w") as f:
            _json.dump({"tasks": [{"id": 1, "status": "pending", "description": "Fix bug"}]}, f)
        daemon._ledger_path = ledger_path
        daemon._last_idle_check = 0.0
        daemon.registry.get_recent_workers.return_value = []
        daemon.slack.get_recent_messages.return_value = []
        daemon.check_idle_enforcement()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "pending" in msg.lower() or "ledger" in msg.lower() or "task" in msg.lower()

    def test_fires_on_unprocessed_messages(self, daemon, db_conn):
        """Idle enforcement fires when zero workers + unprocessed operator messages."""
        daemon._last_idle_check = 0.0
        daemon.registry.get_recent_workers.return_value = []
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix login", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        daemon.check_idle_enforcement()
        daemon.brain.send_message.assert_called_once()

    def test_silent_when_legitimately_idle(self, daemon):
        """No enforcement when zero workers + no directives + no tasks + no messages."""
        daemon._last_idle_check = 0.0
        daemon.registry.get_recent_workers.return_value = []
        daemon.slack.get_recent_messages.return_value = []
        daemon.check_idle_enforcement()
        daemon.brain.send_message.assert_not_called()

    def test_throttle_60s(self, daemon, db_conn):
        """Check only runs once per 60 seconds."""
        daemon._last_idle_check = time.time() - 30  # 30s ago = too soon
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1234', 'Fix bug', 'Fix auth', 'confirmed')"
        )
        db_conn.commit()
        daemon.registry.get_recent_workers.return_value = []
        daemon.check_idle_enforcement()
        daemon.brain.send_message.assert_not_called()

    def test_tier_escalation(self, daemon, db_conn):
        """Tiers escalate: INFO -> WARNING -> CRITICAL with operator notification."""
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1234', 'Fix bug', 'Fix auth bug', 'confirmed')"
        )
        db_conn.commit()
        daemon.registry.get_recent_workers.return_value = []
        daemon.slack.get_recent_messages.return_value = []

        # Tier 1
        daemon._last_idle_check = 0.0
        daemon._idle_enforcement_start = time.time()
        daemon.check_idle_enforcement()
        assert daemon._idle_escalation_tier == 1

        # Tier 2
        daemon._last_idle_check = 0.0
        daemon._idle_enforcement_start = time.time() - 120
        daemon.check_idle_enforcement()
        assert daemon._idle_escalation_tier == 2

        # Tier 3
        daemon._last_idle_check = 0.0
        daemon._idle_enforcement_start = time.time() - 400
        daemon._idle_escalation_tier = 2
        daemon.check_idle_enforcement()
        assert daemon._idle_escalation_tier == 3
        daemon.slack.post_message.assert_called()  # operator notification


class TestCheckMessageAging:
    def test_alerts_on_old_unprocessed(self, daemon):
        """Alert sent for operator message >30min old with no directive."""
        daemon._last_message_aging_check = 0.0
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix the login bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        daemon.check_message_aging()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[UNPROCESSED MESSAGE]" in msg
        assert "Fix the login bug" in msg

    def test_no_alert_for_recent_messages(self, daemon):
        """No alert for messages <30min old."""
        daemon._last_message_aging_check = 0.0
        recent_ts = str(time.time() - 600)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "New task", "ts": recent_ts, "user": "U_OPERATOR"},
        ]
        daemon.check_message_aging()
        daemon.brain.send_message.assert_not_called()

    def test_no_alert_for_processed_messages(self, daemon, db_conn):
        """No alert when directive exists with matching source_ts."""
        daemon._last_message_aging_check = 0.0
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix the login bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, ?, ?)",
            (old_ts, "Fix the login bug", "Fix auth bug"),
        )
        db_conn.commit()
        daemon.check_message_aging()
        daemon.brain.send_message.assert_not_called()

    def test_dedup_across_cycles(self, daemon):
        """Same message not alerted twice across check cycles."""
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        daemon._last_message_aging_check = 0.0
        daemon.check_message_aging()
        assert daemon.brain.send_message.call_count == 1

        daemon._last_message_aging_check = 0.0
        daemon.check_message_aging()
        assert daemon.brain.send_message.call_count == 1  # not 2

    def test_clears_stale_alerts(self, daemon, db_conn):
        """Alerted ts removed from set when directive created."""
        old_ts = str(time.time() - 2400)
        daemon._message_aging_alerted.add(old_ts)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        db_conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, ?, ?)",
            (old_ts, "Fix bug", "Fix auth"),
        )
        db_conn.commit()
        daemon._last_message_aging_check = 0.0
        daemon.check_message_aging()
        assert old_ts not in daemon._message_aging_alerted

    def test_throttle_300s(self, daemon):
        """Check only runs once per 300 seconds."""
        daemon._last_message_aging_check = time.time() - 100  # 100s ago = too soon
        old_ts = str(time.time() - 2400)
        daemon.slack.get_recent_messages.return_value = [
            {"text": "Fix bug", "ts": old_ts, "user": "U_OPERATOR"},
        ]
        daemon.check_message_aging()
        daemon.brain.send_message.assert_not_called()


class TestPollBrainResponsesEnforcement:
    def test_valid_message_posted(self, daemon):
        """Brain returns valid message (directive ref + reason keyword) -> posted to Slack."""
        msg = "#42 status update: worker completed testing and all tests pass."
        daemon.brain.get_pending_responses.return_value = [msg]
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_called_once_with(f"*Brain:* {msg}")

    def test_invalid_message_blocked(self, daemon):
        """Brain returns invalid message (no directive ref) -> blocked, correction sent."""
        msg = "Everything is going well, tests pass."
        daemon.brain.get_pending_responses.return_value = [msg]
        daemon._grader.grade = MagicMock(return_value={"valid": False, "reason": "No directive reference"})
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_not_called()
        daemon.brain.send_message.assert_called_once()
        correction = daemon.brain.send_message.call_args[0][0]
        assert "[CONTEXT REQUIRED]" in correction

    def test_validation_before_chunking(self, daemon):
        """Invalid message >39000 chars blocked BEFORE chunking (no slack posts)."""
        msg = "No context here. " * 3000  # ~48000 chars, no directive ref or reason
        assert len(msg) > 39000
        daemon.brain.get_pending_responses.return_value = [msg]
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_not_called()
        daemon.brain.send_message.assert_called_once()
        correction = daemon.brain.send_message.call_args[0][0]
        assert "[CONTEXT REQUIRED]" in correction

    def test_valid_large_message_chunked(self, daemon):
        """Valid message >39000 chars gets chunked and all chunks posted."""
        prefix = "#42 status update: deployment completed. "
        msg = prefix + "x" * 39000
        assert len(msg) > 39000
        daemon.brain.get_pending_responses.return_value = [msg]
        daemon._grader.grade = MagicMock(return_value={"valid": True})
        daemon.poll_brain_responses()
        import math
        expected_chunks = math.ceil(len(msg) / 39000)
        assert daemon.slack.post_message.call_count == expected_chunks
        # Verify each chunk has Brain prefix
        for call in daemon.slack.post_message.call_args_list:
            assert call[0][0].startswith("*Brain:* ")
