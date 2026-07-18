"""Tests for the permalink-enrichment path in IroncladeDaemon._maybe_capture_operator_wait."""
from unittest.mock import MagicMock

from ironclaude.main import IroncladeDaemon


def _make_daemon():
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._operator_wait_alerted = {}
    daemon._operator_waits = {}
    daemon._last_brain_context = None
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
        daemon._last_brain_context = ("9999.0001", "Status for d1267: choose approach A or B")
        daemon.slack.post_message.return_value = "1234.5678"
        daemon.slack.get_permalink.return_value = "https://workspace.slack.com/archives/C123/p12345678"
        daemon.slack.prefix = "[IRONCLAUDE] "

        captured = daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        assert captured is True
        daemon.slack.get_permalink.assert_called_once_with("9999.0001")
        daemon.slack.update_message.assert_called_once_with(
            "1234.5678",
            "[IRONCLAUDE] ⏳ *Waiting on Robert:* `d1267` — Should I use approach A or B?\nLink: https://workspace.slack.com/archives/C123/p12345678",
        )

    def test_operator_wait_skips_update_when_post_returns_none(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon._last_brain_context = ("9999.0001", "Status for d1267: choose approach A or B")
        daemon.slack.post_message.return_value = None

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_skips_update_when_permalink_returns_none(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon._last_brain_context = ("9999.0001", "Status for d1267: choose approach A or B")
        daemon.slack.post_message.return_value = "1234.5678"
        daemon.slack.get_permalink.return_value = None

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_omits_link_for_unrelated_context(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="d1267")
        daemon._last_brain_context = ("9999.0001", "Status for d9999: unrelated work")
        daemon.slack.post_message.return_value = "1234.5678"

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_for_brain_is_always_linkless(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting(worker_id=None)
        daemon._last_brain_context = ("9999.0001", "Brain decision context")
        daemon.slack.post_message.return_value = "1234.5678"

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_omits_link_when_no_prior_brain_post(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting()
        daemon.slack.post_message.return_value = "1234.5678"

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()


class TestPostBrainMessageTracksLastTs:
    def test_post_brain_message_tracks_complete_top_level_context(self):
        daemon = _make_daemon()
        daemon.slack.post_message.return_value = "1111.2222"

        result = daemon._post_brain_message("hello")

        assert result == "1111.2222"
        assert daemon._last_brain_context == ("1111.2222", "hello")

    def test_threaded_chatter_does_not_replace_top_level_context(self):
        daemon = _make_daemon()
        daemon.slack.post_message.side_effect = ["decision-ts", "chatter-ts"]

        daemon._post_brain_message("Decision context for d1267")
        daemon._post_brain_message("Unrelated chatter", thread_ts="heartbeat-ts")

        assert daemon._last_brain_context == ("decision-ts", "Decision context for d1267")

    def test_partial_top_level_delivery_does_not_replace_context(self):
        daemon = _make_daemon()
        daemon._last_brain_context = ("old-ts", "Decision context for d1267")
        daemon.slack.post_message.side_effect = ["first-ts", None]

        result = daemon._post_brain_message("x" * 39001)

        assert result is None
        assert daemon._last_brain_context == ("old-ts", "Decision context for d1267")
