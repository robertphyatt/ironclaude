"""Tests for the permalink-enrichment path in IroncladeDaemon._maybe_capture_operator_wait."""
from unittest.mock import MagicMock

from ironclaude.main import IroncladeDaemon


def _make_daemon():
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._operator_wait_alerted = {}
    daemon._operator_waits = {}
    daemon.config = {"operator_name": "Robert"}
    daemon.slack = MagicMock()
    return daemon


def _grade_awaiting(worker_id="d1267", question="Should I use approach A or B?"):
    return {
        "awaiting_operator": True,
        "worker_id": worker_id,
        "question": question,
    }


class TestOperatorWaitPermalink:
    def test_operator_wait_updates_message_with_permalink(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = "1234.5678"
        daemon.slack.get_permalink.return_value = "https://workspace.slack.com/archives/C123/p12345678"
        daemon.slack.prefix = "[IRONCLAUDE] "

        captured = daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        assert captured is True
        daemon.slack.update_message.assert_called_once_with(
            "1234.5678",
            "[IRONCLAUDE] ⏳ *Waiting on Robert:* `d1267` — Should I use approach A or B?\nLink: https://workspace.slack.com/archives/C123/p12345678",
        )

    def test_operator_wait_skips_update_when_post_returns_none(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = None

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_skips_update_when_permalink_returns_none(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = "1234.5678"
        daemon.slack.get_permalink.return_value = None

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.update_message.assert_not_called()
