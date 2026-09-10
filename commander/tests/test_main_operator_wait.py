"""Tests for the permalink-enrichment path in IroncladeDaemon._maybe_capture_operator_wait."""
import threading
from unittest.mock import MagicMock

from ironclaude.main import IroncladeDaemon


def _make_daemon():
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    # FIX 2 (in-flight bounded-grade cap): __new__ skips __init__, so mirror the two
    # attributes _grade_bounded dereferences (the operator-wait path calls it).
    daemon._grade_state_lock = threading.Lock()
    daemon._grade_in_flight = False
    daemon._operator_wait_alerted = {}
    daemon._operator_waits = {}
    daemon._brain_waits = {}
    daemon._last_brain_context = None
    daemon._db = None
    daemon.config = {"operator_name": "Robert"}
    daemon.slack = MagicMock()
    return daemon


class _FakeCursor:
    def __init__(self, db, sql, params):
        self._db = db
        self._sql = sql
        self._params = params

    def fetchall(self):
        if "pending_confirmation" in self._sql:
            return [(pid, "interp") for pid in self._db.pending_ids]
        return []

    def fetchone(self):
        if "SELECT status" in self._sql:
            did = self._params[0]
            return (self._db.statuses[did],) if did in self._db.statuses else None
        return None


class _FakeDB:
    """Minimal stand-in for the directives sqlite conn used by R6 dedup."""
    def __init__(self, pending_ids=(), statuses=None):
        self.pending_ids = tuple(pending_ids)
        self.statuses = dict(statuses or {})

    def execute(self, sql, params=()):
        return _FakeCursor(self, sql, params)


def _grade_awaiting(worker_id="d1267", question="Should I use approach A or B?"):
    return {
        "waiting_on": "operator",
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


class TestOperatorWaitDedup:
    """R6: one operator_wait alert per pending directive, not one per sweep/paraphrase."""

    def test_same_pending_directive_alerts_once_across_sweeps(self):
        daemon = _make_daemon()
        daemon._db = _FakeDB(pending_ids=(), statuses={1267: "in_progress"})
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="w1")
        daemon.slack.post_message.return_value = "1234.5678"
        text = "Still holding on d1267, awaiting your decision"
        assert daemon._maybe_capture_operator_wait(text) is True
        assert daemon._maybe_capture_operator_wait(text) is True
        assert daemon.slack.post_message.call_count == 1

    def test_skip_alert_when_directive_already_pending_confirmation(self):
        daemon = _make_daemon()
        # d1267 sits in pending_confirmation → the heartbeat already surfaces it.
        daemon._db = _FakeDB(pending_ids=(1267,), statuses={1267: "pending_confirmation"})
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="w1")
        captured = daemon._maybe_capture_operator_wait("Still holding on d1267, awaiting your decision")
        assert captured is True  # wait still recorded
        daemon.slack.post_message.assert_not_called()  # but no duplicate alert

    def test_paraphrased_question_same_directive_status_dedups(self):
        daemon = _make_daemon()
        daemon._db = _FakeDB(pending_ids=(), statuses={1267: "in_progress"})
        daemon._grader.grade.side_effect = [
            _grade_awaiting(worker_id="w1", question="Should I use approach A or B?"),
            _grade_awaiting(worker_id="w1", question="A or B — which do you prefer?"),
        ]
        daemon.slack.post_message.return_value = "1234.5678"
        daemon._maybe_capture_operator_wait("Still holding on d1267, awaiting your decision")
        daemon._maybe_capture_operator_wait("Still holding on d1267, need your call")
        assert daemon.slack.post_message.call_count == 1

    def test_status_change_re_alerts(self):
        daemon = _make_daemon()
        db = _FakeDB(pending_ids=(), statuses={1267: "in_progress"})
        daemon._db = db
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="w1")
        daemon.slack.post_message.return_value = "1234.5678"
        daemon._maybe_capture_operator_wait("Still holding on d1267, awaiting your decision")
        db.statuses[1267] = "blocked"  # directive status advanced → worth re-alerting
        daemon._maybe_capture_operator_wait("Still holding on d1267, awaiting your decision")
        assert daemon.slack.post_message.call_count == 2

    def test_alerted_marker_survives_ttl_prune(self):
        """R6: a still-pending wait whose TTL entry is pruned must not re-alert."""
        import time as _t
        daemon = _make_daemon()
        daemon._db = _FakeDB(pending_ids=(), statuses={1267: "in_progress"})
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="w1")
        daemon.slack.post_message.return_value = "1234.5678"
        daemon._maybe_capture_operator_wait("Still holding on d1267, awaiting your decision")
        # Force the wait entry stale so the next call's TTL prune drops it...
        daemon._operator_waits["w1"]["updated_at"] = _t.time() - 10_000
        daemon._maybe_capture_operator_wait("Still holding on d1267, awaiting your decision")
        # ...but the alerted marker persisted, so no second alert.
        assert daemon.slack.post_message.call_count == 1


def _grade_brain(worker_id="d5", question="Waiting for approval of the execution mode menu"):
    return {"waiting_on": "brain", "worker_id": worker_id, "question": question}


class TestBrainWaitRouting:
    def test_brain_wait_routes_to_brain_waits_no_alert(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_brain()
        captured = daemon._maybe_capture_operator_wait("Still holding, waiting for approval of the execution mode menu")
        assert captured is True
        assert "d5" in daemon._brain_waits
        assert daemon._brain_waits["d5"]["question"] == "Waiting for approval of the execution mode menu"
        assert daemon._operator_waits == {}
        daemon.slack.post_message.assert_not_called()

    def test_operator_wait_still_routes_to_operator_waits(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting_on": "operator", "worker_id": "d1267", "question": "A or B?"}
        captured = daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")
        assert captured is True
        assert "d1267" in daemon._operator_waits
        assert daemon._brain_waits == {}

    def test_neither_captures_nothing(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting_on": "neither", "worker_id": None, "question": None}
        captured = daemon._maybe_capture_operator_wait("holding until the build finishes")
        assert captured is False
        assert daemon._operator_waits == {}
        assert daemon._brain_waits == {}


class TestOperatorWaitReengageAfterPrune:
    def test_marker_cleared_on_input_even_after_prune(self):
        """I2: after a TTL prune empties _operator_waits, an operator message must still
        clear the dedup marker so a later legitimate re-alert fires (not suppressed)."""
        daemon = _make_daemon()
        daemon._db = _FakeDB(pending_ids=(), statuses={1267: "in_progress"})
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="w1")
        daemon.slack.post_message.return_value = "1.0"
        text = "Still holding on d1267, awaiting your decision"
        assert daemon._maybe_capture_operator_wait(text) is True
        assert daemon.slack.post_message.call_count == 1
        # Simulate a TTL prune that emptied the waits but left the alerted marker.
        daemon._operator_waits.clear()
        assert "w1" in daemon._operator_wait_alerted
        # Operator re-engages via Slack. `pause` is the most inert parsed command:
        # _handle_directive_confirmation short-circuits on text before touching _db,
        # "parsed" in item skips plugin_registry, the branch only sets _paused and
        # posts one ack. _auth_relay.tick() runs after the loop; stub it idle.
        daemon.socket_handler = MagicMock()
        daemon.socket_handler.drain.return_value = [
            {"parsed": {"type": "pause"}, "original_text": "pause"}
        ]
        daemon._auth_relay = MagicMock()
        daemon._auth_relay.tick.return_value = None
        daemon.poll_slack_commands()
        assert daemon._operator_wait_alerted == {}  # cleared despite empty waits
        # A fresh identical wait now RE-alerts.
        daemon.slack.post_message.reset_mock()
        daemon.slack.post_message.return_value = "2.0"
        assert daemon._maybe_capture_operator_wait(text) is True
        assert daemon.slack.post_message.call_count == 1


def test_grade_bounded_clears_in_flight_when_thread_start_fails(monkeypatch):
    """obs 4: if the grader thread fails to start, the in-flight flag must be
    cleared (the clearing finally lives inside the worker that never ran), else
    every future bounded grade is permanently skipped."""
    daemon = _make_daemon()

    def _boom(self):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(threading.Thread, "start", _boom)
    assert daemon._grade_bounded("sys", "usr", {"type": "object"}) is None
    assert daemon._grade_in_flight is False

    # The flag was cleared, so a subsequent grade is not skipped.
    monkeypatch.undo()
    daemon._grader.grade.return_value = {"ok": True}
    assert daemon._grade_bounded("sys", "usr", {"type": "object"}) == {"ok": True}


def test_distinct_questions_without_directive_id_both_alert():
    """obs 5: two DIFFERENT questions from the same worker with no directive id
    must each alert — pre-fix the (worker, None, None) key collapses them and the
    second is suppressed."""
    daemon = _make_daemon()
    text = "Still holding, awaiting your decision"  # awaiting phrase, no directive marker
    daemon.slack.post_message.return_value = "1.1"
    daemon._grader.grade.return_value = _grade_awaiting(
        worker_id="w1", question="Should I use approach A or B?"
    )
    daemon._maybe_capture_operator_wait(text)
    first_calls = daemon.slack.post_message.call_count
    daemon._grader.grade.return_value = _grade_awaiting(
        worker_id="w1", question="Which database should I pick?"
    )
    daemon._maybe_capture_operator_wait(text)
    assert daemon.slack.post_message.call_count == first_calls + 1
