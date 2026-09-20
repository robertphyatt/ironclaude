"""Tests for the idle-worker TTL reaper in the Commander daemon.

The daemon leaves finished workers alive so the Brain can reuse them. These
tests cover the TTL that reaps a worker idle past its threshold WITHOUT losing
reviewed work and WITHOUT ever killing a worker mid finalization-recovery.

Daemon instances are built via ``__new__`` (bypassing ``__init__``); every
attribute the tested code path reads is set explicitly (no getattr defaults).
"""
import time
from unittest.mock import MagicMock, Mock, call

from ironclaude.main import IroncladeDaemon, IDLE_ACTIVITY_GRACE_SECONDS
from ironclaude.notifications import format_worker_idle_ttl_reaped


def _make_reap_daemon():
    """Daemon wired for the _reap_idle_worker ordering/disposition cases."""
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._worker_idle_since = {}
    daemon.config = {}
    daemon.tmux = MagicMock()
    daemon.brain = MagicMock()
    daemon.slack = MagicMock()
    daemon.registry = MagicMock()
    daemon.registry.get_worker.return_value = {"status": "completed"}
    daemon._finalize_drift_retry = {}
    daemon._finalize_recovery_alerted = set()
    daemon._finalize_marker_seen = {}
    daemon._commit_failure_alerted = set()
    daemon._session_died_notified = set()
    daemon._finalize_and_release_worker = Mock()
    daemon._drive_finalization_recovery = Mock(return_value="cleaned")
    daemon._routine_prompt_active = Mock(return_value=False)
    return daemon


def _make_gate_daemon(worker_id, session_name):
    """Daemon wired to drive check_workers through the gate + (dead-session) exit.

    The worker's tmux session is reported dead so the loop body after the gate
    lands in the self-contained session-died branch, which never calls
    kill_session and continues — a clean, cheap exit that isolates the gate.
    """
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._worker_idle_since = {}
    daemon.config = {}
    daemon.tmux = MagicMock()
    daemon.tmux.log_dir = "/tmp/ic-logs-nonexistent-test"
    daemon.tmux.has_session.return_value = False  # -> session-died branch, continues
    daemon.brain = MagicMock()
    daemon.slack = MagicMock()
    daemon.registry = MagicMock()
    daemon.registry.get_running_workers.return_value = [
        {"id": worker_id, "tmux_session": session_name}
    ]
    daemon.registry.get_worker.return_value = {"status": "completed"}
    daemon._finalize_drift_retry = {}
    daemon._finalize_recovery_alerted = set()
    daemon._finalize_marker_seen = {}
    daemon._commit_failure_alerted = set()
    daemon._session_died_notified = set()
    daemon._prompt_store = Mock(return_value=None)
    daemon._resolve_worker_ssh = Mock(return_value=(None, None))
    daemon._resolve_worker_prompt = Mock()
    daemon._finalize_and_release_worker = Mock(return_value="cleaned")
    daemon._drive_finalization_recovery = Mock(return_value="cleaned")
    daemon._routine_prompt_active = Mock(return_value=False)
    return daemon


class TestReapIdleWorker:
    def test_reap_order_integrate_then_kill_then_finalize(self):
        """(b) reap order: pre-finalize(non-terminal) -> drive -> mtime re-read ->
        kill_session -> finalize(terminal) -> drive, in that exact order."""
        daemon = _make_reap_daemon()
        wid, session, ssh_host = "w1", "sess-w1", None
        armed = time.time() - 2000
        # Numeric mtime <= armed+grace so the pre-kill re-read does NOT abort.
        daemon.tmux.get_log_mtime.return_value = armed
        parent = Mock()
        parent.attach_mock(daemon._finalize_and_release_worker, "finalize")
        parent.attach_mock(daemon._drive_finalization_recovery, "drive")
        parent.attach_mock(daemon.tmux.kill_session, "kill")
        parent.attach_mock(daemon.tmux.get_log_mtime, "mtime")

        daemon._reap_idle_worker(wid, session, ssh_host, None, armed)

        names = [
            c[0]
            for c in parent.mock_calls
            if c[0] in {"finalize", "drive", "kill", "mtime"}
        ]
        assert names == ["finalize", "drive", "mtime", "kill", "finalize", "drive"]
        assert daemon._finalize_and_release_worker.call_args_list == [
            call(wid, reason="idle-ttl", terminal=False),
            call(wid, reason="idle-ttl", terminal=True),
        ]
        daemon.tmux.kill_session.assert_called_once_with(session, ssh_host=ssh_host)
        # completed worker -> worker_finished logged; Brain + Slack surfaced
        daemon.registry.log_event.assert_called_once_with(
            "worker_finished", worker_id=wid
        )
        daemon.slack.post_message.assert_called_once()
        daemon.brain.send_message.assert_called_once()

    def test_fresh_activity_before_kill_aborts_reap(self):
        """(obs2) mtime advanced past armed+grace at the pre-kill re-read -> abort:
        no kill, idle clock kept, result False."""
        daemon = _make_reap_daemon()
        wid, session = "w1b", "sess-w1b"
        armed = time.time() - 2000
        daemon._worker_idle_since = {wid: armed}
        daemon.tmux.get_log_mtime.return_value = armed + IDLE_ACTIVITY_GRACE_SECONDS + 1

        result = daemon._reap_idle_worker(wid, session, None, None, armed)

        assert result is False
        daemon.tmux.kill_session.assert_not_called()
        assert daemon._worker_idle_since[wid] == armed
        daemon.tmux.get_log_mtime.assert_called_once_with(
            session, ssh_host=None, remote_log_dir=None
        )

    def test_held_disposition_defers_reap(self):
        """(c) held: first drive returns 'held' -> no kill_session, idle clock kept."""
        daemon = _make_reap_daemon()
        daemon._drive_finalization_recovery = Mock(return_value="held")
        wid, session = "w2", "sess-w2"
        daemon._worker_idle_since[wid] = 111.0
        daemon.tmux.get_log_mtime.return_value = time.time() - 2000

        result = daemon._reap_idle_worker(wid, session, None, None, time.time() - 2000)

        assert result is False
        daemon.tmux.kill_session.assert_not_called()
        # exactly one finalize (the non-terminal pre) and one drive occurred
        daemon._finalize_and_release_worker.assert_called_once_with(
            wid, reason="idle-ttl", terminal=False
        )
        assert daemon._drive_finalization_recovery.call_count == 1
        # idle clock NOT popped (worker left running)
        assert daemon._worker_idle_since[wid] == 111.0

    def test_retrying_and_surfaced_also_defer(self):
        for disp in ("retrying", "surfaced"):
            daemon = _make_reap_daemon()
            daemon._drive_finalization_recovery = Mock(return_value=disp)
            daemon.tmux.get_log_mtime.return_value = time.time() - 2000
            result = daemon._reap_idle_worker("w", "sess", None, None, time.time() - 2000)
            assert result is False
            daemon.tmux.kill_session.assert_not_called()


class TestIdleGate:
    def test_arm_records_first_sighting_and_does_not_overwrite(self):
        """(a) arm: a marker sighting sets _worker_idle_since via setdefault; a
        second sighting does NOT overwrite the original arm time."""
        wid, session = "gw1", "sess-gw1"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 1800}
        # Marker present so the marker branch's setdefault arms the clock.
        daemon.tmux.file_exists.return_value = True
        # Keep the marker (Brain "unreachable") so the second sighting still sees it.
        daemon.brain.send_message.return_value = False
        # ssh path avoids touching the local filesystem for the marker check.
        daemon._resolve_worker_ssh = Mock(return_value=("remote-host", "/remote/logs"))
        # Idle log: mtime not after arm, well under TTL -> no disarm, no reap.
        daemon.tmux.get_log_mtime.return_value = 0.0

        before = time.time()
        daemon.check_workers()
        armed_first = daemon._worker_idle_since[wid]
        assert armed_first >= before
        daemon.tmux.kill_session.assert_not_called()

        # Second sighting must not overwrite the recorded arm time.
        daemon.check_workers()
        assert daemon._worker_idle_since[wid] == armed_first
        daemon.tmux.kill_session.assert_not_called()

    def test_activity_disarms_and_does_not_reap(self):
        """(d) disarm on activity: mtime > armed + grace -> pop, no reap."""
        wid, session = "gw2", "sess-gw2"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 1800}
        armed = time.time() - 100.0
        daemon._worker_idle_since[wid] = armed
        daemon.tmux.get_log_mtime.return_value = armed + IDLE_ACTIVITY_GRACE_SECONDS + 1

        daemon.check_workers()

        assert wid not in daemon._worker_idle_since
        daemon.tmux.kill_session.assert_not_called()

    def test_ttl_zero_disables_reaping(self):
        """(e) ttl 0 disables the reaper entirely."""
        wid, session = "gw3", "sess-gw3"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 0}
        armed = time.time() - 100000.0
        daemon._worker_idle_since[wid] = armed
        daemon.tmux.get_log_mtime.return_value = armed  # no activity

        daemon.check_workers()

        daemon.tmux.kill_session.assert_not_called()

    def test_no_log_mtime_never_reaps(self):
        """(f) mtime None (no pane log) -> fail-safe, never reaps."""
        wid, session = "gw4", "sess-gw4"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 1800}
        armed = time.time() - 100000.0
        daemon._worker_idle_since[wid] = armed
        daemon.tmux.get_log_mtime.return_value = None

        daemon.check_workers()

        daemon.tmux.kill_session.assert_not_called()

    def test_ttl_elapsed_reaps_when_idle(self):
        """Armed past TTL, no activity, no routine prompt -> _reap_idle_worker fires."""
        wid, session = "gw5", "sess-gw5"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 1800}
        armed = time.time() - 2000.0
        daemon._worker_idle_since[wid] = armed
        daemon.tmux.get_log_mtime.return_value = armed  # no activity since arm
        daemon._reap_idle_worker = Mock(return_value=True)

        daemon.check_workers()

        daemon._reap_idle_worker.assert_called_once()
        called_args = daemon._reap_idle_worker.call_args
        assert called_args.args == (wid, session, None, None, armed)

    def test_deferred_reap_falls_through_to_normal_handling(self):
        """(obs1a) reap returns False -> gate does NOT continue; the still-running
        worker reaches the normal session-died handling and its Brain notify."""
        wid, session = "gw6", "sess-gw6"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 1800}
        armed = time.time() - 2000.0
        daemon._worker_idle_since[wid] = armed
        daemon.tmux.get_log_mtime.return_value = armed  # no activity since arm
        daemon._reap_idle_worker = Mock(return_value=False)

        daemon.check_workers()

        assert wid in daemon._session_died_notified
        daemon.brain.send_message.assert_called_once()

    def test_successful_reap_continues_and_skips_normal_handling(self):
        """(obs1b) reap returns True -> gate continues; normal handling skipped."""
        wid, session = "gw7", "sess-gw7"
        daemon = _make_gate_daemon(wid, session)
        daemon.config = {"idle_worker_ttl_seconds": 1800}
        armed = time.time() - 2000.0
        daemon._worker_idle_since[wid] = armed
        daemon.tmux.get_log_mtime.return_value = armed  # no activity since arm
        daemon._reap_idle_worker = Mock(return_value=True)

        daemon.check_workers()

        daemon.brain.send_message.assert_not_called()
        assert wid not in daemon._session_died_notified


class TestReapedMessage:
    def test_reaped_message_is_disposition_neutral(self):
        """(obs3) the reaped surface must not claim work was integrated/rescued."""
        msg = format_worker_idle_ttl_reaped("w9", 5)
        assert "integrated/rescued" not in msg
        assert "w9" in msg
        assert "reaped" in msg.lower()
