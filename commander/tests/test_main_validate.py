"""Tests for IroncladeDaemon._validate_brain_message and _detect_prompt_waiting."""
import logging
import sqlite3
import time
import pytest
from unittest.mock import MagicMock, patch

from ironclaude import fable_availability as fa
from ironclaude.main import IroncladeDaemon, PROMPT_WAITING_CACHE_TTL


def _make_daemon():
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._prompt_waiting_cache = {}
    return daemon


class TestValidateBrainMessage:
    def test_empty_message_short_circuits(self):
        daemon = _make_daemon()
        valid, reason = daemon._validate_brain_message("")
        assert valid is False
        assert reason == "Empty message"
        daemon._grader.grade.assert_not_called()

    def test_whitespace_only_short_circuits(self):
        daemon = _make_daemon()
        valid, reason = daemon._validate_brain_message("   \n  ")
        assert valid is False
        assert reason == "Empty message"
        daemon._grader.grade.assert_not_called()

    def test_grader_valid_true_returns_true(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"valid": True}
        valid, reason = daemon._validate_brain_message("d1083 completed — fixed the bug")
        assert valid is True
        assert reason == ""

    def test_grader_valid_false_with_reason_returns_false_and_reason(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"valid": False, "reason": "Missing reason clause"}
        # Include a directive ref so the message passes the pre-filter and the
        # grader verdict (and its reason) is what flows back.
        valid, reason = daemon._validate_brain_message("#42 everything is good")
        assert valid is False
        assert reason == "Missing reason clause"

    def test_grader_valid_false_no_reason_uses_fallback(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"valid": False}
        valid, reason = daemon._validate_brain_message("some message here")
        assert valid is False
        assert len(reason) > 0

    def test_infrastructure_error_fails_open(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "Ollama unreachable"}
        valid, reason = daemon._validate_brain_message("d1083 completed work")
        assert valid is True
        assert reason == ""


class TestDetectPromptWaiting:
    def test_waiting_true_returned(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": True}
        result = daemon._detect_prompt_waiting("AskUserQuestion\nsome log output")
        assert result is True

    def test_waiting_false_returned(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": False}
        result = daemon._detect_prompt_waiting("Worker is coding normally")
        assert result is False

    def test_infrastructure_error_returns_false(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "timeout"}
        result = daemon._detect_prompt_waiting("some log tail")
        assert result is False

    def test_infrastructure_error_not_cached(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "timeout"}
        log_tail = "some log tail"
        daemon._detect_prompt_waiting(log_tail)
        daemon._detect_prompt_waiting(log_tail)
        assert daemon._grader.grade.call_count == 2

    def test_cache_hit_skips_grader(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": True}
        log_tail = "AskUserQuestion displayed"
        daemon._detect_prompt_waiting(log_tail)
        daemon._detect_prompt_waiting(log_tail)
        assert daemon._grader.grade.call_count == 1

    def test_cache_expires_after_ttl(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": False}
        log_tail = "Worker working normally"
        cache_key = hash(log_tail)
        daemon._prompt_waiting_cache[cache_key] = (time.time() - PROMPT_WAITING_CACHE_TTL - 1, False)
        daemon._detect_prompt_waiting(log_tail)
        assert daemon._grader.grade.call_count == 1

    def test_log_tail_truncated_to_2000_chars(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = {"waiting": False}
        long_tail = "x" * 3000
        daemon._detect_prompt_waiting(long_tail)
        call_args = daemon._grader.grade.call_args
        user_prompt = call_args[0][1]
        assert "x" * 2001 not in user_prompt
        assert len(user_prompt) <= 2020


class TestSpawnWorkerClaudeFable:
    """_handle_spawn_worker must resolve worker_type 'claude-fable' to a Fable
    command instead of falling through to 'Unknown worker type'.

    Hermetic isolation: fable_availability._STATE_PATH is redirected into a tmp
    file so this test never reads a stale ~/.ironclaude/state/fable_unavailable.json
    left by another test run. Without this guard, a leaked real state file would
    make the redirect fire and the test would see make_opus_command("opus", ...)
    instead of ("fable", ...)."""

    @pytest.fixture(autouse=True)
    def _isolate_fable_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fa, "_STATE_PATH", tmp_path / "state.json")

    def _spawn_daemon(self):
        daemon = IroncladeDaemon.__new__(IroncladeDaemon)
        daemon.config = {"effort_level": "high", "advisor": {}}
        daemon.slack = MagicMock()
        daemon.registry = MagicMock()
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux = MagicMock()
        daemon.tmux.spawn_session.return_value = True
        daemon._wait_for_ready = MagicMock(return_value=True)  # avoid real sleeps
        return daemon

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="FABLE_CMD")
    def test_claude_fable_builds_fable_command(self, mock_make, mock_trust, mock_fmt, mock_logev):
        daemon = self._spawn_daemon()
        decision = {"worker_id": "w-fable", "type": "claude-fable", "repo": "/tmp", "objective": "hard task"}
        daemon._handle_spawn_worker(decision)
        # Fable worker command is built via make_opus_command("fable", effort) —
        # pre-fix this branch didn't exist and the type fell through to the
        # "Unknown worker type" path, so make_opus_command was never called.
        mock_make.assert_called_once_with("fable", "high")
        daemon.tmux.spawn_session.assert_called_once()

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="FABLE_CMD")
    def test_claude_fable_not_unknown_worker_type(self, mock_make, mock_trust, mock_fmt, mock_logev):
        daemon = self._spawn_daemon()
        decision = {"worker_id": "w-fable", "type": "claude-fable", "repo": "/tmp", "objective": "hard task"}
        daemon._handle_spawn_worker(decision)
        for call in daemon.slack.post_message.call_args_list:
            assert "Unknown worker type" not in call[0][0]


class TestSpawnWorkerFableAvailabilityRedirect:
    """_handle_spawn_worker must redirect worker_type 'claude-fable' to
    'claude-opus' when fable_availability flags Fable unavailable, mirroring
    the redirect wired into the MCP spawn path.

    The file-decision spawn path (_handle_spawn_worker) has no "died before
    ready" detection — unlike orchestrator_mcp's MCP spawn path, it never
    inspects _wait_for_ready's return value — so there is no death-triggered
    mark_fable_unavailable/Slack-alert hook to test here. Only the redirect
    is covered.
    """

    @pytest.fixture(autouse=True)
    def _isolate_state_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fa, "_STATE_PATH", tmp_path / "state.json")

    def _spawn_daemon(self):
        daemon = IroncladeDaemon.__new__(IroncladeDaemon)
        daemon.config = {"effort_level": "high", "advisor": {}}
        daemon.slack = MagicMock()
        daemon.registry = MagicMock()
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux = MagicMock()
        daemon.tmux.spawn_session.return_value = True
        daemon._wait_for_ready = MagicMock(return_value=True)  # avoid real sleeps
        return daemon

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="OPUS_CMD")
    def test_fable_unavailable_routes_to_opus_command(self, mock_make, mock_trust, mock_fmt, mock_logev):
        fa.mark_fable_unavailable("test")
        daemon = self._spawn_daemon()
        decision = {"worker_id": "w-fable", "type": "claude-fable", "repo": "/tmp", "objective": "hard task"}
        daemon._handle_spawn_worker(decision)
        # default_opus_model defaults to "opus" when not set in config.
        mock_make.assert_called_once_with("opus", "high")
        daemon.tmux.spawn_session.assert_called_once()

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="FABLE_CMD")
    def test_fable_available_passes_through(self, mock_make, mock_trust, mock_fmt, mock_logev):
        daemon = self._spawn_daemon()
        decision = {"worker_id": "w-fable", "type": "claude-fable", "repo": "/tmp", "objective": "hard task"}
        daemon._handle_spawn_worker(decision)
        mock_make.assert_called_once_with("fable", "high")
        daemon.tmux.spawn_session.assert_called_once()

    def test_help_text_omits_claude_fable_when_flag_active(self):
        """MP-04: when Fable is flagged unavailable, the 'Unknown worker type'
        error message must NOT advertise claude-fable as a supported type —
        it would silently redirect if requested, contradicting the alert."""
        fa.mark_fable_unavailable("test")
        daemon = self._spawn_daemon()
        decision = {
            "worker_id": "w-bogus", "type": "claude-bogus",
            "repo": "/tmp", "objective": "task",
        }
        daemon._handle_spawn_worker(decision)
        posted = daemon.slack.post_message.call_args[0][0]
        assert "Unknown worker type" in posted
        assert "claude-fable" not in posted

    def test_help_text_lists_claude_fable_when_flag_inactive(self):
        """Counterpart: with no flag set, claude-fable IS advertised (baseline)."""
        daemon = self._spawn_daemon()
        decision = {
            "worker_id": "w-bogus", "type": "claude-bogus",
            "repo": "/tmp", "objective": "task",
        }
        daemon._handle_spawn_worker(decision)
        posted = daemon.slack.post_message.call_args[0][0]
        assert "Unknown worker type" in posted
        assert "claude-fable" in posted


class TestSpawnWorkerAdvisorTiering:
    """_handle_spawn_worker must select the advisor model per worker_type via
    advisor.advisor_models (one-tier-up map), skip the advisor entirely for
    claude-fable (top tier, no higher advisor), and optionally dispatch via
    /goal instead of the raw objective when dispatch.use_goal is set."""

    @pytest.fixture(autouse=True)
    def _isolate_fable_state(self, tmp_path, monkeypatch):
        # _handle_spawn_worker now redirects claude-fable through
        # fable_availability.resolve_worker_type, which reads the real
        # ~/.ironclaude/state/fable_unavailable.json unless isolated — this
        # would make test_claude_fable_sends_no_advisor flaky under real
        # concurrent Fable-unavailable state.
        monkeypatch.setattr(fa, "_STATE_PATH", tmp_path / "state.json")

    def _spawn_daemon(self, config):
        daemon = IroncladeDaemon.__new__(IroncladeDaemon)
        daemon.config = config
        daemon.slack = MagicMock()
        daemon.registry = MagicMock()
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux = MagicMock()
        daemon.tmux.spawn_session.return_value = True
        daemon._wait_for_ready = MagicMock(return_value=True)  # avoid real sleeps
        return daemon

    def _advisor_sends(self, daemon):
        return [
            call[0][1] for call in daemon.tmux.send_keys.call_args_list
            if call[0][1].startswith("/advisor")
        ]

    def _goal_sends(self, daemon):
        return [
            call[0][1] for call in daemon.tmux.send_keys.call_args_list
            if call[0][1].startswith("/goal")
        ]

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_claude_sonnet_advisor_is_opus(self, mock_trust, mock_fmt, mock_logev):
        config = {
            "effort_level": "high",
            "advisor": {
                "enabled": True,
                "advisor_model": "opus",
                "advisor_models": {"claude-sonnet": "opus", "claude-opus": "fable"},
            },
            "dispatch": {"use_goal": False},
        }
        daemon = self._spawn_daemon(config)
        decision = {"worker_id": "w-sonnet", "type": "claude-sonnet", "repo": "/tmp", "objective": "task"}
        daemon._handle_spawn_worker(decision)
        assert self._advisor_sends(daemon) == ["/advisor opus"]

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="OPUS_CMD")
    def test_claude_opus_advisor_is_fable(self, mock_make, mock_trust, mock_fmt, mock_logev):
        config = {
            "effort_level": "high",
            "advisor": {
                "enabled": True,
                "advisor_model": "opus",
                "advisor_models": {"claude-sonnet": "opus", "claude-opus": "fable"},
            },
            "dispatch": {"use_goal": False},
        }
        daemon = self._spawn_daemon(config)
        decision = {"worker_id": "w-opus", "type": "claude-opus", "repo": "/tmp", "objective": "task"}
        daemon._handle_spawn_worker(decision)
        assert self._advisor_sends(daemon) == ["/advisor fable"]

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="FABLE_CMD")
    def test_claude_fable_sends_no_advisor(self, mock_make, mock_trust, mock_fmt, mock_logev):
        config = {
            "effort_level": "high",
            "advisor": {
                "enabled": True,
                "advisor_model": "opus",
                "advisor_models": {"claude-sonnet": "opus", "claude-opus": "fable"},
            },
            "dispatch": {"use_goal": False},
        }
        daemon = self._spawn_daemon(config)
        decision = {"worker_id": "w-fable", "type": "claude-fable", "repo": "/tmp", "objective": "task"}
        daemon._handle_spawn_worker(decision)
        assert self._advisor_sends(daemon) == []

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    @patch("ironclaude.main.make_opus_command", return_value="OPUS_CMD")
    def test_fable_unavailable_swaps_advisor_to_opus(self, mock_make, mock_trust, mock_fmt, mock_logev):
        """When fable_availability flags Fable unavailable, Stage 5.5 sends
        /advisor opus instead of /advisor fable for a claude-opus worker
        whose tier map says fable."""
        fa.mark_fable_unavailable("test")
        config = {
            "effort_level": "high",
            "advisor": {
                "enabled": True,
                "advisor_model": "opus",
                "advisor_models": {"claude-opus": "fable"},
            },
            "dispatch": {"use_goal": False},
        }
        daemon = self._spawn_daemon(config)
        decision = {"worker_id": "w-opus", "type": "claude-opus", "repo": "/tmp", "objective": "task"}
        daemon._handle_spawn_worker(decision)
        assert self._advisor_sends(daemon) == ["/advisor opus"]

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_goal_dispatch_sent_when_configured(self, mock_trust, mock_fmt, mock_logev):
        config = {
            "effort_level": "high",
            "advisor": {"enabled": False},
            "dispatch": {"use_goal": True},
        }
        daemon = self._spawn_daemon(config)
        decision = {"worker_id": "w-goal", "type": "claude-sonnet", "repo": "/tmp", "objective": "task"}
        daemon._handle_spawn_worker(decision)
        assert self._goal_sends(daemon) == [
            "/goal the assigned objective is complete and code review has passed"
        ]

    @patch("ironclaude.main.log_worker_event")
    @patch("ironclaude.main.format_worker_spawned", return_value="spawned")
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_goal_dispatch_not_sent_when_disabled(self, mock_trust, mock_fmt, mock_logev):
        config = {
            "effort_level": "high",
            "advisor": {"enabled": False},
            "dispatch": {"use_goal": False},
        }
        daemon = self._spawn_daemon(config)
        decision = {"worker_id": "w-nogoal", "type": "claude-sonnet", "repo": "/tmp", "objective": "task"}
        daemon._handle_spawn_worker(decision)
        assert self._goal_sends(daemon) == []


def _make_poll_daemon():
    d = IroncladeDaemon.__new__(IroncladeDaemon)
    d._grader = MagicMock()
    d.slack = MagicMock()
    d.brain = MagicMock()
    d._operator_waits = {}
    d._operator_wait_alerted = {}
    d.config = {}
    d._heartbeat_state_history = {}
    d._heartbeat_stuck_notified = set()
    d._last_heartbeat_ts = None
    from ironclaude.auth_relay import AuthRelay
    d._auth_relay = AuthRelay()   # __new__ bypasses __init__; the new tick() needs this
    return d


def _posts(daemon):
    return " ".join(str(c.args[0]) for c in daemon.slack.post_message.call_args_list)


class TestOperatorWaits:
    def test_awaiting_operator_message_captures_and_alerts(self):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["Still holding. Awaiting Robert's Slack response."]
        d._grader.grade.return_value = {"awaiting_operator": True, "worker_id": "d1267", "question": "approve the migration?"}
        d.poll_brain_responses()
        assert "d1267" in d._operator_waits
        assert d._operator_waits["d1267"]["question"] == "approve the migration?"
        posts = _posts(d)
        assert "Waiting on Operator" in posts and "d1267" in posts and "approve the migration?" in posts
        assert "*Brain:*" not in posts  # not posted as a normal brain message
        for c in d.brain.send_message.call_args_list:
            assert "CONTEXT REQUIRED" not in str(c.args[0])  # loop-break preserved

    def test_conversational_message_threaded_not_dropped(self):
        d = _make_poll_daemon()
        d._last_heartbeat_ts = "1700.1"
        d.brain.get_pending_responses.return_value = ["Everything looks fine so far."]
        d.poll_brain_responses()
        assert d._operator_waits == {}
        d.slack.post_message.assert_called_once()
        _, kwargs = d.slack.post_message.call_args
        assert kwargs.get("thread_ts") == "1700.1"
        d._grader.grade.assert_not_called()  # no awaiting-phrase, no directive -> no grader call

    def test_reply_marker_threads_under_operator_message(self):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = [
            "[reply-to:1699.5] Fair challenge — need to verify Stage 6"
        ]
        d.poll_brain_responses()
        d.slack.post_message.assert_called_once()
        args, kwargs = d.slack.post_message.call_args
        assert kwargs.get("thread_ts") == "1699.5"
        assert "[reply-to:" not in args[0]
        assert "Fair challenge" in args[0]
        d.slack.add_reaction.assert_called_once_with("white_check_mark", "1699.5")

    def test_reply_with_empty_body_does_not_post_or_react(self):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["[reply-to:1699.5]   "]
        d.poll_brain_responses()
        d.slack.post_message.assert_not_called()
        d.slack.add_reaction.assert_not_called()

    def test_refless_chatter_threads_under_heartbeat(self):
        d = _make_poll_daemon()
        d._last_heartbeat_ts = "1700.1"
        d.brain.get_pending_responses.return_value = ["tactical: grader 0.82, retrying chunk 3"]
        d.poll_brain_responses()
        d.slack.post_message.assert_called_once()
        _, kwargs = d.slack.post_message.call_args
        assert kwargs.get("thread_ts") == "1700.1"
        d.brain.send_message.assert_not_called()

    def test_refless_chatter_falls_back_to_main_when_no_heartbeat(self):
        d = _make_poll_daemon()
        d._last_heartbeat_ts = None
        d.brain.get_pending_responses.return_value = ["tactical note"]
        d.poll_brain_responses()
        _, kwargs = d.slack.post_message.call_args
        assert kwargs.get("thread_ts") is None

    def test_control_echo_still_dropped(self):
        d = _make_poll_daemon()
        d._last_heartbeat_ts = "1700.1"
        d.brain.get_pending_responses.return_value = ["[FYI] ok I'll tie to a directive"]
        d.poll_brain_responses()
        d.slack.post_message.assert_not_called()
        d.brain.send_message.assert_not_called()

    def test_malformed_reply_marker_treated_as_chatter(self):
        d = _make_poll_daemon()
        d._last_heartbeat_ts = "1700.1"
        d.brain.get_pending_responses.return_value = ["[reply-to:abc] not a real ts"]
        d.poll_brain_responses()
        # _REPLY_TO_RE only matches [0-9.]+, so a non-numeric marker falls through to chatter
        d.slack.post_message.assert_called_once()
        _, kwargs = d.slack.post_message.call_args
        assert kwargs.get("thread_ts") == "1700.1"
        d.slack.add_reaction.assert_not_called()

    def test_reply_reaction_skipped_when_post_fails(self):
        d = _make_poll_daemon()
        d.slack.post_message.return_value = None  # the reply post fails
        d.brain.get_pending_responses.return_value = ["[reply-to:1699.5] answer body"]
        d.poll_brain_responses()
        d.slack.add_reaction.assert_not_called()

    def test_reply_all_chunks_semantics_skips_reaction_on_chunk_failure(self):
        d = _make_poll_daemon()
        # chunk 1 succeeds (truthy ts), chunk 2 fails (None) - all-chunks gate should hold
        d.slack.post_message.side_effect = ["1699.6", None]
        body = "y" * 40000  # forces 2 chunks under _BRAIN_POST_CHUNK=39000
        d.brain.get_pending_responses.return_value = [f"[reply-to:1699.5] {body}"]
        d.poll_brain_responses()
        assert d.slack.post_message.call_count == 2
        d.slack.add_reaction.assert_not_called()

    def test_reply_undelivered_logs_info(self, caplog):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["[reply-to:1699.5]   "]
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            d.poll_brain_responses()
        assert any(
            "Brain reply not delivered" in r.message and "1699.5" in r.message
            for r in caplog.records
        )

    def test_reply_long_text_is_chunked(self):
        d = _make_poll_daemon()
        body = "y" * 40000
        d.brain.get_pending_responses.return_value = [f"[reply-to:1699.5] {body}"]
        d.poll_brain_responses()
        assert d.slack.post_message.call_count == 2
        for c in d.slack.post_message.call_args_list:
            assert c.kwargs.get("thread_ts") == "1699.5"
            assert len(c.args[0]) <= 39000 + len("*Brain:* ")
        d.slack.add_reaction.assert_called_once_with("white_check_mark", "1699.5")

    def test_chatter_long_text_is_chunked_under_heartbeat(self):
        d = _make_poll_daemon()
        d._last_heartbeat_ts = "1700.1"
        d.brain.get_pending_responses.return_value = ["z" * 40000]
        d.poll_brain_responses()
        assert d.slack.post_message.call_count == 2
        for c in d.slack.post_message.call_args_list:
            assert c.kwargs.get("thread_ts") == "1700.1"

    def test_awaiting_phrase_but_grader_says_no_falls_through(self):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["#1267 done waiting for tests to finish, all green"]
        d._grader.grade.return_value = {"awaiting_operator": False, "worker_id": None, "question": None}
        d.poll_brain_responses()
        assert d._operator_waits == {}
        # has a directive ref (#1267) so it validates+posts normally
        assert "*Brain:*" in _posts(d)

    def test_d1362_style_system_narration_falls_through_when_grader_says_no(self):
        """Regression anchor for the confirmed false positive (daemon.log:14222):
        'operator_wait recorded for d1362: Waiting for the heartbeat labels to become idle.'
        This asserts the WIRING correctly excludes a wait when the grader says False for
        this exact phrasing — it does not prove the tightened prompt drives the real grader
        to say False (unverifiable without a live grader call)."""
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = [
            "#1362 heartbeat two-section labels shipped, waiting for the heartbeat labels to become idle"
        ]
        d._grader.grade.return_value = {"awaiting_operator": False, "worker_id": None, "question": None}
        d.poll_brain_responses()
        assert d._operator_waits == {}
        assert "*Brain:*" in _posts(d)

    def test_operator_slack_message_clears_waits(self):
        d = _make_poll_daemon()
        d._operator_waits = {"d1267": {"question": "q", "updated_at": time.time()}}
        d._operator_wait_alerted = {"d1267": "q"}
        d.socket_handler = MagicMock()
        d.socket_handler.drain.return_value = [{"parsed": {"type": "help"}, "original_text": "/help"}]
        d._handle_directive_confirmation = MagicMock(return_value=False)
        d.plugin_registry = MagicMock()
        with patch("ironclaude.main.format_help_text", return_value="help"):
            d.poll_slack_commands()
        assert d._operator_waits == {}

    def test_post_heartbeat_passes_waits(self):
        d = _make_poll_daemon()
        d._last_heartbeat = 0.0
        d.config = {"heartbeat_interval_seconds": 0}
        d.registry = MagicMock()
        d.registry.get_recent_workers.return_value = []
        d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
        d._db = None
        d._operator_waits = {"d1267": {"question": "approve?", "updated_at": time.time()}}
        with patch("ironclaude.main.format_heartbeat", return_value="hb") as fh:
            d.post_heartbeat()
        _, kwargs = fh.call_args
        assert "d1267" in (kwargs.get("waits") or {})
        assert kwargs.get("operator_name") == "Operator"

    def test_post_heartbeat_captures_ts(self):
        d = _make_poll_daemon()
        d._last_heartbeat = 0.0
        d.config = {"heartbeat_interval_seconds": 0}
        d.registry = MagicMock()
        d.registry.get_recent_workers.return_value = []
        d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
        d._db = None
        d.slack.post_message.return_value = "1700000000.000100"
        with patch("ironclaude.main.format_heartbeat", return_value="hb"):
            d.post_heartbeat()
        assert d._last_heartbeat_ts == "1700000000.000100"

    def test_post_heartbeat_ts_none_on_failure(self):
        d = _make_poll_daemon()
        d._last_heartbeat = 0.0
        d.config = {"heartbeat_interval_seconds": 0}
        d.registry = MagicMock()
        d.registry.get_recent_workers.return_value = []
        d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
        d._db = None
        d.slack.post_message.return_value = None   # simulate a failed heartbeat post
        with patch("ironclaude.main.format_heartbeat", return_value="hb"):
            d.post_heartbeat()
        assert d._last_heartbeat_ts is None

    def test_alert_deduped_for_same_question(self):
        d = _make_poll_daemon()
        d._grader.grade.return_value = {"awaiting_operator": True, "worker_id": "d1267", "question": "approve?"}
        d.brain.get_pending_responses.return_value = ["holding, awaiting your decision"]
        d.poll_brain_responses()
        d.brain.get_pending_responses.return_value = ["still holding, awaiting your decision"]
        d.poll_brain_responses()
        alerts = [c for c in d.slack.post_message.call_args_list if "Waiting on Operator" in str(c.args[0])]
        assert len(alerts) == 1

    def test_pending_confirmation_directive_surfaces_in_heartbeat_waits(self):
        """Uses the REAL format_heartbeat (not mocked) to confirm a directive-sourced
        entry renders sensibly in the actual Slack message, per the design's explicit
        instruction to verify this rendering."""
        d = _make_poll_daemon()
        d._last_heartbeat = 0.0
        d.config = {"heartbeat_interval_seconds": 0}
        d.registry = MagicMock()
        d.registry.get_recent_workers.return_value = []
        d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
        d._db = sqlite3.connect(":memory:")
        d._db.execute("CREATE TABLE directives (id INTEGER PRIMARY KEY, interpretation TEXT, status TEXT)")
        d._db.execute(
            "INSERT INTO directives (id, interpretation, status) VALUES "
            "(1362, 'Heartbeat two-section waits', 'pending_confirmation')"
        )
        d._db.commit()
        d._operator_waits = {}
        d.post_heartbeat()
        posted = _posts(d)
        assert "d1362" in posted
        assert "Heartbeat two-section waits" in posted

    def test_no_pending_confirmation_and_no_operator_waits_suppresses_section(self):
        """Uses the REAL format_heartbeat (not mocked) — this is the actual regression
        guard for the reported symptom: no directive pending, no classified wait, so
        the real rendered Slack message must contain no WAITING ON section at all."""
        d = _make_poll_daemon()
        d._last_heartbeat = 0.0
        d.config = {"heartbeat_interval_seconds": 0}
        d.registry = MagicMock()
        d.registry.get_recent_workers.return_value = []
        d.brain.get_token_usage.return_value = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0}
        d._db = sqlite3.connect(":memory:")
        d._db.execute("CREATE TABLE directives (id INTEGER PRIMARY KEY, interpretation TEXT, status TEXT)")
        d._db.commit()
        d._operator_waits = {}
        d.post_heartbeat()
        posted = _posts(d)
        assert "WAITING ON" not in posted
        assert "⏳" not in posted

    def test_not_awaiting_fast_path_skips_grader_for_fable_review(self):
        """No directive ref in the message, matching the existing
        test_conversational_message_not_captured_and_dropped pattern — this ensures
        _validate_brain_message's own grader.grade call (for message-format
        validation, a separate concern) never fires either, so assert_not_called()
        is unambiguous."""
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["holding for the Fable review result"]
        d.poll_brain_responses()
        assert d._operator_waits == {}
        d._grader.grade.assert_not_called()

    def test_not_awaiting_fast_path_skips_grader_for_subagent_verdict(self):
        d = _make_poll_daemon()
        d.brain.get_pending_responses.return_value = ["waiting on subagent verdict before proceeding"]
        d.poll_brain_responses()
        assert d._operator_waits == {}
        d._grader.grade.assert_not_called()

    def test_not_awaiting_fast_path_does_not_exclude_genuine_operator_wait(self):
        d = _make_poll_daemon()
        d._grader.grade.return_value = {"awaiting_operator": True, "worker_id": "d1267", "question": "approve the migration?"}
        d.brain.get_pending_responses.return_value = ["holding for your approval on the migration"]
        d.poll_brain_responses()
        assert "d1267" in d._operator_waits
        d._grader.grade.assert_called_once()


class TestDeployWorkerHooks:
    """Startup hook auto-deploy: mirrors `make deploy-hooks`. Stable dir is
    mandatory (fail-hard); latest plugin-cache hooks dir is best-effort."""

    @pytest.fixture
    def layout(self, tmp_path):
        """Fake repo layout + destinations. Returns (repo_root, hooks_src,
        stable_dir, cache_base)."""
        repo_root = tmp_path / "repo" / "commander"
        hooks_src = tmp_path / "repo" / "worker" / "hooks"
        hooks_src.mkdir(parents=True)
        (hooks_src / "guard.sh").write_text("#!/bin/bash\necho guard\n")
        exec_hook = hooks_src / "gbtw.sh"
        exec_hook.write_text("#!/bin/bash\necho gbtw\n")
        exec_hook.chmod(0o755)
        (hooks_src / "README.md").write_text("not a hook")
        repo_root.mkdir(parents=True)
        stable_dir = tmp_path / "stable-hooks"
        cache_base = tmp_path / "plugin-cache"
        return repo_root, hooks_src, stable_dir, cache_base

    def test_copies_sh_files_to_stable_dir_preserving_exec_bit(self, layout):
        import os
        from ironclaude.main import _deploy_worker_hooks
        repo_root, _, stable_dir, cache_base = layout
        _deploy_worker_hooks(str(repo_root), str(stable_dir), str(cache_base))
        assert (stable_dir / "guard.sh").exists()
        assert (stable_dir / "gbtw.sh").exists()
        assert os.access(stable_dir / "gbtw.sh", os.X_OK)

    def test_non_sh_files_not_copied(self, layout):
        from ironclaude.main import _deploy_worker_hooks
        repo_root, _, stable_dir, cache_base = layout
        _deploy_worker_hooks(str(repo_root), str(stable_dir), str(cache_base))
        assert not (stable_dir / "README.md").exists()

    def test_latest_plugin_cache_version_selected_numerically(self, layout):
        """1.0.16 must beat 1.0.9 — lexicographic sort would invert this."""
        from ironclaude.main import _deploy_worker_hooks
        repo_root, _, stable_dir, cache_base = layout
        (cache_base / "1.0.9" / "hooks").mkdir(parents=True)
        (cache_base / "1.0.16" / "hooks").mkdir(parents=True)
        _deploy_worker_hooks(str(repo_root), str(stable_dir), str(cache_base))
        assert (cache_base / "1.0.16" / "hooks" / "guard.sh").exists()
        assert not (cache_base / "1.0.9" / "hooks" / "guard.sh").exists()

    def test_non_version_dirs_in_cache_base_ignored(self, layout):
        from ironclaude.main import _deploy_worker_hooks
        repo_root, _, stable_dir, cache_base = layout
        (cache_base / "1.0.9" / "hooks").mkdir(parents=True)
        (cache_base / "not-a-version" / "hooks").mkdir(parents=True)
        _deploy_worker_hooks(str(repo_root), str(stable_dir), str(cache_base))
        assert (cache_base / "1.0.9" / "hooks" / "guard.sh").exists()
        assert not (cache_base / "not-a-version" / "hooks" / "guard.sh").exists()

    def test_cache_absent_warns_and_still_deploys_stable(self, layout):
        from ironclaude.main import _deploy_worker_hooks
        repo_root, _, stable_dir, cache_base = layout
        # cache_base never created
        _deploy_worker_hooks(str(repo_root), str(stable_dir), str(cache_base))
        assert (stable_dir / "guard.sh").exists()

    def test_missing_source_dir_exits(self, tmp_path):
        from ironclaude.main import _deploy_worker_hooks
        bare_repo_root = tmp_path / "empty" / "commander"
        bare_repo_root.mkdir(parents=True)
        with pytest.raises(SystemExit):
            _deploy_worker_hooks(
                str(bare_repo_root),
                str(tmp_path / "stable"),
                str(tmp_path / "cache"),
            )


def test_validate_truncates_long_message_before_grading():
    daemon = _make_daemon()
    daemon._grader.grade.return_value = {"valid": True}
    huge = "d1 status " + ("X" * 50000)
    daemon._validate_brain_message(huge)
    forwarded = " ".join(str(a) for a in daemon._grader.grade.call_args[0])
    assert ("X" * 50000) not in forwarded    # full body NOT forwarded (truncated)
    assert ("X" * 100) in forwarded           # a chunk survives (truncated, not dropped)


def test_operator_wait_infra_error_returns_false():
    # design's 3rd per-site default: infra-error must NOT capture the wait
    # (returning truthy would suppress the message from posting)
    daemon = _make_daemon()
    daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "down"}
    assert not daemon._maybe_capture_operator_wait("holding for your reply on d1")


# --- /login relay wiring (Task 4) ---

class _FakeRelay:
    def __init__(self, start_state="started", tick_events=()):
        self._start = {"state": start_state}
        self._ticks = list(tick_events)
        self.submitted = []
    def start(self):
        return self._start
    def submit_code(self, code):
        self.submitted.append(code); return "sent"
    def tick(self):
        return self._ticks.pop(0) if self._ticks else None


def _login_daemon(relay, command=None):
    d = _make_poll_daemon()
    d._db = None
    d._auth_relay = relay
    d.socket_handler = MagicMock()
    items = [] if command is None else [{"parsed": command, "original_text": "/x"}]
    d.socket_handler.drain.return_value = items
    return d


def test_login_dispatch_relays_start():
    d = _login_daemon(_FakeRelay(start_state="started"), command={"type": "login"})
    d.poll_slack_commands()
    assert "Starting sign-in" in _posts(d)


def test_login_dispatch_busy():
    d = _login_daemon(_FakeRelay(start_state="busy"), command={"type": "login"})
    d.poll_slack_commands()
    assert "already in progress" in _posts(d)


def test_login_code_dispatch():
    relay = _FakeRelay()
    d = _login_daemon(relay, command={"type": "login_code", "code": "Z9"})
    d.poll_slack_commands()
    assert relay.submitted == ["Z9"]
    assert "Code submitted" in _posts(d)


def test_login_tick_url_posts():
    d = _login_daemon(_FakeRelay(tick_events=[{"state": "url", "url": "https://claude.ai/x"}]))
    d.poll_slack_commands()
    assert "https://claude.ai/x" in _posts(d)


def test_login_tick_success_restarts(monkeypatch):
    import signal
    killed = []
    monkeypatch.setattr("ironclaude.main.os.kill", lambda pid, sig: killed.append(sig))
    d = _login_daemon(_FakeRelay(tick_events=[{"state": "success", "account": "a@b"}]))
    d.poll_slack_commands()
    assert "Signed in as a@b" in _posts(d)
    assert killed == [signal.SIGHUP]


def test_login_tick_verify_failed_no_restart(monkeypatch):
    killed = []
    monkeypatch.setattr("ironclaude.main.os.kill", lambda pid, sig: killed.append(sig))
    d = _login_daemon(_FakeRelay(tick_events=[{"state": "verify_failed"}]))
    d.poll_slack_commands()
    assert "not restarting" in _posts(d).lower()
    assert killed == []


def test_login_tick_timeout_no_restart(monkeypatch):
    killed = []
    monkeypatch.setattr("ironclaude.main.os.kill", lambda pid, sig: killed.append(sig))
    d = _login_daemon(_FakeRelay(tick_events=[{"state": "timeout"}]))
    d.poll_slack_commands()
    assert "timed out" in _posts(d).lower()
    assert killed == []


def test_login_tick_error_no_restart(monkeypatch):
    killed = []
    monkeypatch.setattr("ironclaude.main.os.kill", lambda pid, sig: killed.append(sig))
    d = _login_daemon(_FakeRelay(tick_events=[{"state": "error", "detail": "boom"}]))
    d.poll_slack_commands()
    assert "didn't complete" in _posts(d).lower()
    assert killed == []


def test_login_tick_already_logged_in_no_restart(monkeypatch):
    killed = []
    monkeypatch.setattr("ironclaude.main.os.kill", lambda pid, sig: killed.append(sig))
    d = _login_daemon(_FakeRelay(tick_events=[{"state": "already_logged_in", "account": "me@x"}]))
    d.poll_slack_commands()
    assert "Already signed in" in _posts(d)
    assert killed == []


# --- limit-surfacing detector (Task 5) ---

def test_detect_account_limit_hit():
    from ironclaude.main import detect_account_limit
    assert detect_account_limit("You've hit your limit · resets 4:10am (America/Chicago)") == "resets 4:10am (America/Chicago)"


def test_detect_worker_session_limit():
    from ironclaude.main import detect_account_limit
    assert detect_account_limit("d1393 session limit hit — resets 4:10am CT") is not None
    assert detect_account_limit("worker stuck at rate-limit menu") is not None


def test_detect_no_limit():
    from ironclaude.main import detect_account_limit
    assert detect_account_limit("Both workers alive. No action needed.") is None


def test_detect_negated_worker_limit_is_not_a_hit():
    # review M3: "no session limit hit" must NOT trigger a spurious alert.
    from ironclaude.main import detect_account_limit
    assert detect_account_limit("no session limit hit — all clear") is None


def test_limit_alert_posts_once_then_throttled(monkeypatch):
    from ironclaude.main import _LIMIT_COOLDOWN_S
    d = _make_poll_daemon()
    d._db = None
    d._limit_alerted = {}
    d._maybe_capture_operator_wait = lambda t: True   # continue right after the alert
    clock = {"v": 1000.0}
    monkeypatch.setattr("ironclaude.main.time.time", lambda: clock["v"])
    msg = "You've hit your limit · resets 4:10am (America/Chicago)"
    d.brain.get_pending_responses.return_value = [msg]
    d.poll_brain_responses()
    assert d.slack.post_message.call_count == 1          # alerted
    d.brain.get_pending_responses.return_value = [msg]
    d.poll_brain_responses()
    assert d.slack.post_message.call_count == 1          # same window -> throttled
    clock["v"] += _LIMIT_COOLDOWN_S + 1
    d.brain.get_pending_responses.return_value = [msg]
    d.poll_brain_responses()
    assert d.slack.post_message.call_count == 2          # cooldown elapsed -> re-alert


def test_limit_alert_fires_even_when_waiting(monkeypatch):
    d = _make_poll_daemon()
    d._db = None
    d._limit_alerted = {}
    monkeypatch.setattr("ironclaude.main.time.time", lambda: 1000.0)
    d._maybe_capture_operator_wait = lambda t: True      # would continue immediately
    d.brain.get_pending_responses.return_value = ["You've hit your limit · resets 4:10am (America/Chicago)"]
    d.poll_brain_responses()
    assert d.slack.post_message.called                   # alert fired BEFORE the continue
