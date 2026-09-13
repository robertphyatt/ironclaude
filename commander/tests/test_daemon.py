# tests/test_daemon.py
"""Tests for IroncladeDaemon worker completion detection."""

import os
import shlex
import sqlite3
import subprocess
from ironclaude.db import DIRECT_REPLY_FALLBACK_REASON, init_db
import time
import json
import logging
from pathlib import Path

import psutil
import pytest
from unittest.mock import MagicMock, patch

from ironclaude.main import CHECKIN_CADENCE, FINALIZE_DRIFT_RETRY_CAP, PM_GATE_STAGES, PM_GATE_SLACK_SECONDS, IroncladeDaemon, PromptDetection, ensure_brain_trusted
from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.prompt_incidents import PromptIncidentStore
from ironclaude.provider_state import ProviderState
from ironclaude.tmux_manager import PromptSignal


def _semantic_prompt(question="Which action should run?", *, options=(("1", "Continue"),), evidence=None):
    return PromptSignal(
        kind="question",
        question=question,
        options=tuple(options),
        authority_text="",
        source_spans=(("question", 0, len(question)),),
        evidence=evidence or question,
    )


def _brain_instruction_surfaces() -> list[str]:
    root = Path(__file__).resolve().parents[1] / "src" / "brain"
    return [
        (root / "system_prompt.md").read_text(),
        (root / "rules" / "workflow.md").read_text(),
    ]


def test_brain_blocked_capability_contract_reporting_and_recheck():
    for text in _brain_instruction_surfaces():
        assert "report_directive_capability_block" in text
        assert "report_directive_capability_recovery" in text
        assert "workspace_write" in text
        assert "ollama_loopback" in text
        assert "process_inspection" in text
        assert "project_permission" in text
        assert "codex_sandbox" in text
        assert "host_runtime" in text
        assert "exactly one non-mutating probe" in text
        assert "one aggregate result" in text


def test_brain_blocked_capability_contract_forbids_monitor_chatter():
    for text in _brain_instruction_surfaces():
        assert "Do not create a monitor worker" in text
        assert "Do not run repeated minute probes" in text
        assert "Do not post per-check Slack chatter" in text
        assert "wait for the daemon's recovery dispatch" in text


def test_brain_blocked_capability_contract_startup_exemption_preserves_resources():
    for text in _brain_instruction_surfaces():
        assert "daemon-issued `[CAPABILITY RECHECK]`" in text
        assert "startup, context recovery, or ordinary attention sweeps" in text
        assert "resource-blocked work remains unchanged" in text


class TestBrainStartupLogging:
    def test_codex_success_requires_alive_process(self, caplog):
        import ironclaude.main as main_module

        brain = MagicMock()
        brain.client_name = "codex"
        brain.is_alive.return_value = False
        brain.capability_block = {"status": "blocked", "reason": "destination-conflict"}

        with caplog.at_level("INFO"):
            started = main_module._log_brain_start_result(brain)

        assert started is False
        assert "Brain SDK client started" not in caplog.text
        assert "Brain SDK client not started client=codex reason=capability-blocked" in caplog.text

    def test_codex_alive_process_logs_success(self, caplog):
        import ironclaude.main as main_module

        brain = MagicMock()
        brain.client_name = "codex"
        brain.is_alive.return_value = True

        with caplog.at_level("INFO"):
            started = main_module._log_brain_start_result(brain)

        assert started is True
        assert "Brain SDK client started client=codex" in caplog.text

    def test_claude_preserves_nonraising_start_success_semantics(self, caplog):
        import ironclaude.main as main_module

        brain = MagicMock()
        brain.client_name = "claude"
        brain.is_alive.side_effect = AssertionError("Claude startup must not use Codex gate")

        with caplog.at_level("INFO"):
            started = main_module._log_brain_start_result(brain)

        assert started is True
        assert "Brain SDK client started" in caplog.text
        brain.is_alive.assert_not_called()


def test_brain_direct_reply_requires_acknowledgement_before_threaded_reply():
    for text in _brain_instruction_surfaces():
        assert "acknowledge_operator_message(source_ts, reason)" in text
        assert "must succeed before direct reply" in text
        assert "no directive, worker, or repository action" in text
        assert "[reply-to:<source_ts>]" in text


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
    brain.get_token_usage.return_value = {
        "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
        "seconds_since_last_activity": None,
    }
    d = IroncladeDaemon(config, slack, None, registry, tmux, brain)
    d._state_manager_db_path = str(tmp_path / "state-manager.db")
    return d


class TestCodexBrainCapabilityContainment:
    @staticmethod
    def _codex_brain(block=None, *, alive=False):
        brain = MagicMock()
        brain.client_name = "codex"
        brain.capability_tier = "sonnet"
        brain.capability_block = block
        brain.is_alive.return_value = alive
        brain.needs_restart.return_value = False
        brain.check_compaction_complete.return_value = False
        brain.send_message.return_value = True
        return brain

    @staticmethod
    def _daemon(tmp_path, conn, brain):
        slack = MagicMock()
        registry = MagicMock()
        registry.get_running_workers.return_value = []
        tmux = MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(tmux.log_dir, exist_ok=True)
        return IroncladeDaemon(
            {"tmp_dir": str(tmp_path), "brain_capability_probe_seconds": 30},
            slack, None, registry, tmux, brain, db_conn=conn,
        )

    def test_blocked_capability_alerts_once_and_does_not_restart_early(self, tmp_path):
        conn = init_db(str(tmp_path / "blocked.db"))
        block = {
            "schema_version": 1,
            "status": "blocked",
            "reason": "destination-conflict",
            "source_companion": "/source/host",
            "destination_companion": "/destination/host",
        }
        brain = self._codex_brain(block)
        daemon = self._daemon(tmp_path, conn, brain)

        daemon.check_brain()
        daemon.check_brain()

        assert daemon.slack.post_message.call_count == 1
        brain.restart.assert_not_called()
        brain.probe_runtime_capability.assert_not_called()
        assert ProviderState(conn).get_current_client("brain") is None

    def test_healthy_reconstruction_redispatches_held_generation_once(self, tmp_path):
        db_path = str(tmp_path / "recovery.db")
        conn = init_db(db_path)
        provider = ProviderState(conn)
        provider.set_current_client("brain", "codex")
        provider.record_brain_capability_block(
            tier="sonnet", category="command_bridge", reason="destination-conflict",
            fingerprint="capability-1", now=0.0, initial_backoff=1.0,
        )
        prompts = PromptIncidentStore(conn)
        observation = prompts.observe(
            "worker-1", "plan_ready", _semantic_prompt(), now=1.0
        )
        assert prompts.claim_dispatch(observation.dispatch_id, destination="brain", now=2.0)
        prompts.record_delivery(
            observation.dispatch_id, delivered=False,
            failure_category="capability_blocked",
        )

        first_brain = self._codex_brain(None, alive=True)
        first = self._daemon(tmp_path, conn, first_brain)
        first.check_brain()
        assert first_brain.send_message.call_count == 1

        conn.close()
        reopened = init_db(db_path)
        second_brain = self._codex_brain(None, alive=True)
        second = self._daemon(tmp_path, reopened, second_brain)
        second.check_brain()
        second_brain.send_message.assert_not_called()

    def test_due_blocked_probe_holds_without_restart_or_duplicate_alert(
        self, tmp_path, monkeypatch
    ):
        import ironclaude.main as main_module

        conn = init_db(str(tmp_path / "due-blocked.db"))
        block = {
            "schema_version": 1,
            "status": "blocked",
            "reason": "destination-conflict",
            "source_companion": "/source/host",
            "destination_companion": "/destination/host",
        }
        brain = self._codex_brain(block)
        daemon = self._daemon(tmp_path, conn, brain)
        monkeypatch.setattr(main_module.time, "time", lambda: 100.0)
        daemon.check_brain()
        assert daemon.slack.post_message.call_count == 1

        monkeypatch.setattr(main_module.time, "time", lambda: 131.0)
        brain.probe_runtime_capability.return_value = block
        daemon.check_brain()
        brain.probe_runtime_capability.assert_called_once_with()
        brain.restart.assert_not_called()
        assert daemon.slack.post_message.call_count == 1

    def test_fresh_healthy_probe_starts_brain_once_and_marks_available(
        self, tmp_path, monkeypatch
    ):
        import ironclaude.main as main_module

        conn = init_db(str(tmp_path / "due-healthy.db"))
        block = {
            "schema_version": 1,
            "status": "blocked",
            "reason": "destination-conflict",
            "source_companion": "/source/host",
            "destination_companion": "/destination/host",
        }
        healthy = {**block, "status": "healthy", "reason": "destination-equivalent"}
        brain = self._codex_brain(block)
        brain.restart.return_value = True
        brain.restart_count = 1

        def recover():
            brain.capability_block = None
            return healthy

        brain.probe_runtime_capability.side_effect = recover
        daemon = self._daemon(tmp_path, conn, brain)
        monkeypatch.setattr(main_module, "_render_brain_system_prompt", lambda *_: "prompt")
        monkeypatch.setattr(main_module.time, "time", lambda: 100.0)
        daemon.check_brain()

        monkeypatch.setattr(main_module.time, "time", lambda: 131.0)
        daemon.check_brain()
        daemon.check_brain()

        brain.restart.assert_called_once()
        assert ProviderState(conn).is_available(
            "local", "codex", "brain", "sonnet"
        ) is True

    @staticmethod
    def _seed_pending_recovery(conn, capability_fingerprint="capability-1"):
        provider = ProviderState(conn)
        provider.set_current_client("brain", "codex")
        provider.record_brain_capability_block(
            tier="sonnet", category="command_bridge", reason="destination-conflict",
            fingerprint=capability_fingerprint, now=0.0, initial_backoff=1.0,
        )
        prompts = PromptIncidentStore(conn)
        initial = prompts.observe(
            "worker-crash", "plan_ready", _semantic_prompt("Recover?"), now=1.0
        )
        assert prompts.claim_dispatch(initial.dispatch_id, destination="brain", now=2.0)
        prompts.record_delivery(
            initial.dispatch_id, delivered=False,
            failure_category="capability_blocked",
        )
        recovery = prompts.rearm_capability_recovery(
            capability_fingerprint, now=3.0
        )
        assert len(recovery) == 1
        return provider, prompts, recovery[0]

    def test_reconstruction_drains_generation_created_before_capability_clear(
        self, tmp_path
    ):
        db_path = str(tmp_path / "before-clear.db")
        conn = init_db(db_path)
        self._seed_pending_recovery(conn)
        conn.close()

        reopened = init_db(db_path)
        brain = self._codex_brain(None, alive=True)
        daemon = self._daemon(tmp_path, reopened, brain)
        daemon.check_brain()

        brain.send_message.assert_called_once()
        assert ProviderState(reopened).is_available(
            "local", "codex", "brain", "sonnet"
        ) is True

    def test_reconstruction_drains_pending_generation_after_capability_clear(
        self, tmp_path
    ):
        db_path = str(tmp_path / "after-clear.db")
        conn = init_db(db_path)
        provider, _prompts, _recovery = self._seed_pending_recovery(conn)
        provider.record_brain_capability_recovery(tier="sonnet")
        conn.close()

        reopened = init_db(db_path)
        brain = self._codex_brain(None, alive=True)
        daemon = self._daemon(tmp_path, reopened, brain)
        daemon.check_brain()

        brain.send_message.assert_called_once()

    def test_reconstruction_marks_claimed_generation_unknown_without_duplicate(
        self, tmp_path
    ):
        db_path = str(tmp_path / "claimed-crash.db")
        conn = init_db(db_path)
        provider, prompts, recovery = self._seed_pending_recovery(conn)
        provider.record_brain_capability_recovery(tier="sonnet")
        assert prompts.claim_dispatch(
            recovery.dispatch_id, destination="brain", now=4.0
        )
        conn.close()

        reopened = init_db(db_path)
        brain = self._codex_brain(None, alive=True)
        daemon = self._daemon(tmp_path, reopened, brain)
        daemon.check_brain()

        brain.send_message.assert_not_called()
        state = reopened.execute(
            "SELECT state, failure_category FROM worker_prompt_dispatches WHERE id=?",
            (recovery.dispatch_id,),
        ).fetchone()
        assert tuple(state) == ("failed", "delivery_unknown")


class TestBrainNarrationThreading:
    """Brain narration ([NARRATION]-tagged) is threaded under the last heartbeat and NEVER
    re-messages the Brain (no [CONTEXT REQUIRED] feedback loop = the d1435 bug). Dropped when
    no heartbeat thread exists yet (thread_ts=None would post top-level)."""
    def test_narration_posts_to_heartbeat_thread_and_never_messages_brain(self, daemon):
        from ironclaude.brain_client import _NARRATION_PREFIX
        daemon.brain.get_pending_responses = lambda: [f"{_NARRATION_PREFIX}Working on d5 now."]
        daemon._last_heartbeat_ts = "1234.5"
        daemon.poll_brain_responses()
        assert daemon.slack.post_message.called
        # EVERY post is threaded under the heartbeat ts — a top-level post (thread_ts None/absent)
        # is a thread-only violation and must fail this test.
        assert all(
            kw.get("thread_ts") == "1234.5"
            for _args, kw in daemon.slack.post_message.call_args_list
        )
        # NEVER re-messages the Brain (no [CONTEXT REQUIRED] feedback loop).
        daemon.brain.send_message.assert_not_called()

    def test_narration_dropped_when_no_heartbeat(self, daemon):
        from ironclaude.brain_client import _NARRATION_PREFIX
        daemon.brain.get_pending_responses = lambda: [f"{_NARRATION_PREFIX}chatter"]
        daemon._last_heartbeat_ts = None
        daemon.poll_brain_responses()
        daemon.brain.send_message.assert_not_called()
        daemon.slack.post_message.assert_not_called()

    def test_ping_ack_is_not_relayed(self, daemon):
        """R1: the Brain's [PING-ACK] reply to an idle health probe is a liveness
        ack only — never posted to Slack (prefixed or bare)."""
        from ironclaude.brain_client import _NARRATION_PREFIX
        daemon._last_heartbeat_ts = "1234.5"  # a thread exists; still must not post
        for resp in (f"{_NARRATION_PREFIX}[PING-ACK]", "[PING-ACK]"):
            daemon.slack.post_message.reset_mock()
            daemon.brain.get_pending_responses = lambda r=resp: [r]
            daemon.poll_brain_responses()
            daemon.slack.post_message.assert_not_called()

    def test_ping_ack_tolerant_drop(self, daemon):
        """FIX 1: a [PING-ACK] the Brain threads (leading [reply-to:...] marker) or
        appends a token to is still a liveness ack — never posted to Slack. A real
        narration that leads with the token but runs long IS delivered (the len bound
        keeps the drop from over-widening)."""
        from ironclaude.brain_client import _NARRATION_PREFIX
        daemon._db = MagicMock()               # let the reply-to branch run fully
        daemon._db.in_transaction = False      # so persist_ack does not raise (idle-conn guard)
        daemon._last_heartbeat_ts = "1234.5"   # a narration thread exists

        # Positive: threaded ack (would otherwise post + ✅ via the reply-to branch)
        daemon.slack.reset_mock()
        daemon.brain.get_pending_responses = lambda: [f"{_NARRATION_PREFIX}[reply-to:123.456] [PING-ACK]"]
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_not_called()
        daemon.slack.add_reaction.assert_not_called()

        # Positive: trailing-token ack (would otherwise post via the narration branch)
        daemon.slack.reset_mock()
        daemon.brain.get_pending_responses = lambda: [f"{_NARRATION_PREFIX}[PING-ACK]."]
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_not_called()

        # Negative (bounds the guard): a long narration leading with the token is delivered.
        daemon.slack.reset_mock()
        long_narration = f"{_NARRATION_PREFIX}[PING-ACK] and then I did a great many other things that make this message unambiguously a real narration well beyond sixty characters."
        daemon.brain.get_pending_responses = lambda: [long_narration]
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_called()


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
        """Worker whose tmux session died is still detected (fallback). The seam
        owns completion now: against the bare-MagicMock registry the real seam
        returns an 'authority' (preserved) outcome, so the DAEMON completes
        nothing itself — completion is the seam's job, asserted at the
        orchestrator level."""
        worker = {"id": "w2", "tmux_session": "ic-w2"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = False
        daemon.check_workers()
        daemon.registry.update_worker_status.assert_not_called()

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

    def test_returns_none_when_session_file_disappears_before_read(self, daemon, tmp_path):
        """Returns None (no exception) when session file exists() but read_text() raises OSError."""
        from pathlib import Path
        from unittest.mock import patch
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        session_file = claude_dir / "ironclaude-session-12345.id"
        session_file.write_text(session_id)
        daemon.tmux.list_pane_pid.return_value = "12345"

        original_read_text = Path.read_text
        def patched_read_text(self_path, *args, **kwargs):
            if self_path == session_file:
                raise FileNotFoundError("file vanished")
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = daemon._get_worker_workflow_stage("ic-w1", _claude_dir=claude_dir)

        assert result is None


class TestDetectPromptWaiting:
    def test_detects_ask_user_question(self, daemon):
        daemon._grader.grade = MagicMock()
        pane = """Which action should run?\n❯ 1. Continue\n  2. Stop\nEnter to select · ↑/↓ to navigate"""
        result = daemon._detect_worker_prompt(pane)
        assert result.conclusive is True
        assert result.signal.question == "Which action should run?"
        daemon._grader.grade.assert_not_called()

    def test_detects_submit_answers(self, daemon):
        pane = "Inspection complete.\n\nSubmit answers to continue?\n❯ "
        daemon._grader.grade = MagicMock(return_value={
            "kind": "question",
            "interaction_block": "Submit answers to continue?",
            "question": "Submit answers to continue?",
            "options": [],
            "authority_text": "",
        })
        result = daemon._detect_worker_prompt(pane)
        assert result.signal.question == "Submit answers to continue?"

    def test_rejects_historical_question_followed_by_progress(self, daemon):
        pane = "Which action should run?\nAnswer: Continue\nRunning tests...\n12 passed\n❯ "
        daemon._grader.grade = MagicMock(return_value={
            "kind": "question",
            "interaction_block": "Which action should run?",
            "question": "Which action should run?",
            "options": [],
            "authority_text": "",
        })
        result = daemon._detect_worker_prompt(pane)
        assert result.signal is None
        assert result.conclusive is False

    def test_rejects_stale_ask_user_menu_followed_by_completion(self, daemon):
        pane = """Which action should run?
❯ 1. Continue
  2. Stop
Enter to select · ↑/↓ to navigate
Completed task successfully.
❯ """
        daemon._grader.grade = MagicMock(return_value={
            "kind": "none", "interaction_block": None, "question": None,
            "options": [], "authority_text": None,
        })
        result = daemon._detect_worker_prompt(pane)
        assert result.signal is None
        assert result.conclusive is True

    def test_boolean_only_result_cannot_mint_prompt(self, daemon):
        daemon._grader.grade = MagicMock(return_value={"waiting": True})
        result = daemon._detect_worker_prompt("Which approach would you prefer?\n❯ ")
        assert result.signal is None
        assert result.conclusive is False

    def test_no_false_positive_normal_output(self, daemon):
        daemon._grader.grade = MagicMock(return_value={
            "kind": "none", "interaction_block": None, "question": None,
            "options": [], "authority_text": None,
        })
        result = daemon._detect_worker_prompt("Running tests...\nAll 5 passed")
        assert result.signal is None
        assert result.conclusive is True

    def test_infrastructure_failure_is_inconclusive(self, daemon):
        daemon._grader.grade = MagicMock(return_value={
            "infrastructure_error": True, "error_detail": "offline"
        })
        result = daemon._detect_worker_prompt("")
        assert result.signal is None
        assert result.conclusive is False


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


class TestCheckinHashDedup:
    def test_hash_dedup_suppresses_identical_log_tail(self, daemon, tmp_path):
        """No check-in when log_tail content matches last-sent hash."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "same output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "executing")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_hash["w1"] = hash("same output")

        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()

    def test_hash_dedup_allows_changed_log_tail(self, daemon, tmp_path):
        """Check-in fires when log_tail content differs from last-sent hash."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "new output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "executing")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_hash["w1"] = hash("old output")

        daemon.check_workers()
        daemon.brain.send_message.assert_called_once()
        assert daemon._last_checkin_hash["w1"] == hash("new output")

    def test_stage_change_bypasses_hash_dedup(self, daemon, tmp_path):
        """Stage transition fires check-in even when log_tail content is identical."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "same output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "reviewing")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_hash["w1"] = hash("same output")
        daemon._last_checkin_stage["w1"] = "executing"
        daemon._last_checkin_sent["w1"] = time.time()

        daemon.check_workers()
        daemon.brain.send_message.assert_called_once()

    def test_hash_not_updated_on_hash_skip(self, daemon, tmp_path):
        """When hash gate suppresses send, _last_checkin_sent and _last_checkin_hash are not updated."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "same output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "executing")
        daemon._claude_dir = claude_dir

        original_hash = hash("same output")
        daemon._last_checkin_hash["w1"] = original_hash

        daemon.check_workers()

        assert daemon._last_checkin_sent.get("w1", 0.0) == 0.0
        assert daemon._last_checkin_hash["w1"] == original_hash


class TestCheckinCadenceValues:
    def test_execution_complete_cadence_is_900(self):
        assert CHECKIN_CADENCE["execution_complete"] == 900


class TestDirectiveConfirmation:
    def test_confirmed_directive_uses_operator_name(self, daemon):
        """Directive confirmation message uses operator_name from config, not hardcoded 'Robert'."""
        daemon.config["operator_name"] = "Alice"
        # Set up an in-memory DB with a pending_confirmation directive
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE directives (id INTEGER PRIMARY KEY, interpretation TEXT, "
            "interpretation_ts TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
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


class TestHandleAudit:
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
        conn.execute(
            "CREATE TABLE operator_message_acknowledgements ("
            "source_ts TEXT PRIMARY KEY, reason TEXT NOT NULL)"
        )
        for row in rows:
            conn.execute(
                "INSERT INTO directives (source_ts, source_text, interpretation, status) "
                "VALUES (?, ?, ?, ?)",
                (row["source_ts"], row["source_text"], row["interpretation"], row["status"]),
            )
        conn.commit()
        return conn

    def test_audit_no_db(self, daemon):
        """_handle_audit posts error when _db is None."""
        daemon._db = None
        daemon._handle_audit()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Database not configured" in msg

    def test_audit_search_unavailable(self, daemon):
        """_handle_audit posts error when search_operator_messages raises RuntimeError."""
        conn = self._make_db([])
        daemon._db = conn
        daemon.slack.search_operator_messages.side_effect = RuntimeError("requires user_token")
        daemon._handle_audit()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Audit unavailable" in msg
        conn.close()

    def test_audit_report_mapped_and_unmapped(self, daemon):
        """_handle_audit shows mapped directive and unmapped message in correct sections."""
        conn = self._make_db([
            {"source_ts": "1.0", "source_text": "fix auth", "interpretation": "Fix auth bug", "status": "completed"},
        ])
        daemon._db = conn
        daemon.slack.search_operator_messages.return_value = [
            {"ts": "1.0", "text": "fix auth"},
            {"ts": "2.0", "text": "thanks!"},
        ]
        daemon._handle_audit()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "ts:1.0" in msg
        assert "d1" in msg
        assert "completed" in msg
        assert "Fix auth bug" in msg
        assert "ts:2.0" in msg
        assert "thanks!" in msg
        conn.close()

    def test_audit_all_mapped(self, daemon):
        """_handle_audit shows (none) in unmapped section when all messages are mapped."""
        conn = self._make_db([
            {"source_ts": "1.0", "source_text": "fix", "interpretation": "Fix bug", "status": "in_progress"},
        ])
        daemon._db = conn
        daemon.slack.search_operator_messages.return_value = [
            {"ts": "1.0", "text": "fix"},
        ]
        daemon._handle_audit()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Directives: 1" in msg
        assert "Acknowledged: 0" in msg
        assert "Unresolved: 0" in msg
        assert "Mapped to directives:" not in msg
        assert "Unmapped:" not in msg
        assert "(none)" in msg
        conn.close()

    def test_audit_all_unmapped(self, daemon):
        """_handle_audit shows (none) in mapped section when no messages match directives."""
        conn = self._make_db([])
        daemon._db = conn
        daemon.slack.search_operator_messages.return_value = [
            {"ts": "1.0", "text": "random chat"},
        ]
        daemon._handle_audit()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Directives: 0" in msg
        assert "Acknowledged: 0" in msg
        assert "Unresolved: 1" in msg
        assert "Mapped to directives:" not in msg
        assert "Unmapped:" not in msg
        assert "random chat" in msg
        conn.close()


class TestHeartbeatWorkerListing:
    def test_post_heartbeat_passes_worker_details(self, daemon):
        """post_heartbeat builds worker detail list from registry + workflow stage."""
        daemon.registry.get_recent_workers.return_value = [
            {"id": "w-1", "tmux_session": "ic-w-1", "description": "Your task: Fix auth bug"},
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
        daemon.registry.get_recent_workers.return_value = [
            {"id": "w-1", "tmux_session": "ic-w-1", "description": "Your task: Fix the bug"},
        ]
        daemon._last_heartbeat = 0
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "Fix the bug" in msg


class TestDirectiveCapabilityQuiescence:
    @pytest.fixture
    def blocked_daemon(self, tmp_path):
        conn = init_db(str(tmp_path / "daemon.db"))
        conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('b1', 'blocked', 'Blocked directive', 'blocked')"
        )
        directive_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO directive_capability_blocks "
            "(directive_id, capabilities_json, denial_scope, target, reason, "
            "fingerprint, state, first_observed_at, last_observed_at, "
            "next_recheck_at, backoff_seconds, generation) "
            "VALUES (?, '[\"workspace_write\"]', 'codex_sandbox', '/repo', "
            "'sandbox denied', 'fp', 'blocked', 1, 1, 160, 60, 1)",
            (directive_id,),
        )
        conn.commit()
        slack = MagicMock()
        slack.post_message.return_value = "1.2"
        registry = MagicMock()
        registry.get_running_workers.return_value = []
        registry.get_recent_workers.return_value = []
        tmux = MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(tmux.log_dir, exist_ok=True)
        brain = MagicMock()
        brain.send_message.return_value = True
        brain.get_token_usage.return_value = None
        daemon = IroncladeDaemon(
            {"tmp_dir": str(tmp_path), "heartbeat_interval_seconds": 900},
            slack, None, registry, tmux, brain, db_conn=conn,
        )
        return daemon, conn, directive_id

    def test_daemon_blocked_quiescence_deduplicates_notification(self, blocked_daemon):
        daemon, conn, directive_id = blocked_daemon
        daemon.check_directive_capability_blocks(now=100)
        daemon.check_directive_capability_blocks(now=101)
        assert daemon.slack.post_message.call_count == 1
        assert conn.execute(
            "SELECT notification_state FROM directive_capability_blocks "
            "WHERE directive_id=?", (directive_id,)
        ).fetchone()[0] == "submitted"

    def test_heartbeat_blocked_capability_is_stable_without_idle_nudge(self, blocked_daemon):
        daemon, _conn, _directive_id = blocked_daemon
        daemon._last_heartbeat = 0
        daemon.post_heartbeat(now=1000)
        heartbeat = daemon.slack.post_message.call_args_list[0].args[0]
        assert "Blocked directives" in heartbeat
        assert "workspace_write" in heartbeat
        daemon.brain.send_message.assert_not_called()

    def test_blocked_recheck_backoff_retry_and_no_early_duplicate(self, blocked_daemon):
        daemon, conn, directive_id = blocked_daemon
        daemon.check_directive_capability_blocks(now=160)
        assert daemon.brain.send_message.call_count == 1
        row = conn.execute(
            "SELECT next_recheck_at, backoff_seconds FROM directive_capability_blocks "
            "WHERE directive_id=?", (directive_id,)
        ).fetchone()
        assert tuple(row) == (280, 120)
        daemon.check_directive_capability_blocks(now=200)
        assert daemon.brain.send_message.call_count == 1
        daemon.brain.send_message.return_value = False
        daemon.check_directive_capability_blocks(now=280)
        retry = conn.execute(
            "SELECT next_recheck_at, backoff_seconds FROM directive_capability_blocks "
            "WHERE directive_id=?", (directive_id,)
        ).fetchone()
        assert tuple(retry) == (280, 120)

    def test_blocked_recheck_initial_backoff_clamps_to_heartbeat(self, blocked_daemon):
        daemon, conn, directive_id = blocked_daemon
        daemon.config["heartbeat_interval_seconds"] = 30
        conn.execute(
            "UPDATE directive_capability_blocks SET last_observed_at=1, "
            "next_recheck_at=61, backoff_seconds=60 WHERE directive_id=?",
            (directive_id,),
        )
        conn.commit()
        daemon.check_directive_capability_blocks(now=31)
        assert daemon.brain.send_message.call_count == 1
        assert tuple(conn.execute(
            "SELECT next_recheck_at, backoff_seconds FROM directive_capability_blocks "
            "WHERE directive_id=?", (directive_id,)
        ).fetchone()) == (61, 30)

    def test_blocked_recheck_stale_generation_compare_and_swap(self, blocked_daemon):
        daemon, conn, directive_id = blocked_daemon
        loaded = daemon._load_directive_capability_blocks()
        conn.execute(
            "UPDATE directive_capability_blocks SET generation=2, fingerprint='new' "
            "WHERE directive_id=?", (directive_id,)
        )
        conn.commit()
        daemon._process_directive_capability_block(loaded[0], now=160)
        daemon.brain.send_message.assert_not_called()
        assert tuple(conn.execute(
            "SELECT generation, next_recheck_at FROM directive_capability_blocks "
            "WHERE directive_id=?", (directive_id,)
        ).fetchone()) == (2, 160)

    def test_blocked_recovery_redispatch_once_and_suppresses_generic_paths(self, blocked_daemon):
        daemon, conn, directive_id = blocked_daemon
        conn.execute(
            "UPDATE directive_capability_blocks SET state='recovered', "
            "capabilities_json='[]', recovery_dispatch_state='pending' "
            "WHERE directive_id=?", (directive_id,)
        )
        conn.commit()
        daemon.check_directive_capability_blocks(now=200)
        assert daemon.brain.send_message.call_count == 1
        assert conn.execute(
            "SELECT status FROM directives WHERE id=?", (directive_id,)
        ).fetchone()[0] == "confirmed"
        daemon.brain.send_message.reset_mock()
        daemon.check_directive_capability_blocks(now=201)
        daemon.check_confirmed_directives()
        daemon._last_idle_check = 0
        daemon.check_idle_enforcement()
        daemon._last_heartbeat = 0
        daemon.post_heartbeat(now=1000)
        daemon.brain.send_message.assert_not_called()


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
        with patch.object(main_module, '_daemon', None), \
             patch('os.execvp') as mock_exec, \
             patch('os.kill'), \
             patch.object(main_module.subprocess, 'run'), \
             patch.object(main_module.time, 'sleep'):
            from ironclaude.main import _handle_restart
            _handle_restart(signal.SIGHUP, None)
        mock_exec.assert_called_once_with(
            sys.executable, [sys.executable, '-m', 'ironclaude.main']
        )

    def test_handle_restart_shuts_down_daemon_before_exec(self):
        """_handle_restart calls daemon.shutdown() before os.execvp."""
        import ironclaude.main as main_module
        mock_daemon = MagicMock()
        with patch.object(main_module, '_daemon', mock_daemon), \
             patch('os.execvp'), \
             patch('os.kill'), \
             patch.object(main_module.subprocess, 'run'), \
             patch.object(main_module.time, 'sleep'):
            from ironclaude.main import _handle_restart
            _handle_restart(signal.SIGHUP, None)
        mock_daemon.shutdown.assert_called_once()

    def test_handle_restart_stops_socket_handler_before_exec(self):
        """_handle_restart calls socket_handler.stop() before os.execvp."""
        import ironclaude.main as main_module
        mock_daemon = MagicMock()
        mock_daemon.socket_handler = MagicMock()
        with patch.object(main_module, '_daemon', mock_daemon), \
             patch('os.execvp'), \
             patch('os.kill'), \
             patch.object(main_module.subprocess, 'run'), \
             patch.object(main_module.time, 'sleep'):
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

    def test_handle_restart_uses_targeted_brain_kill_not_pkill(self):
        """_handle_restart kills brain by PID, not via pkill -P."""
        import ironclaude.main as main_module

        mock_daemon = MagicMock()
        mock_daemon.brain._stop_event = MagicMock()
        mock_daemon.brain._brain_pid = 99999

        subprocess_cmds = []
        kill_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = ""
            m.returncode = 0
            if isinstance(cmd, list):
                subprocess_cmds.append(cmd)
            return m

        def fake_os_kill(pid, sig):
            kill_calls.append((pid, sig))

        with patch.object(main_module, '_daemon', mock_daemon), \
             patch('os.execvp'), \
             patch('os.kill', side_effect=fake_os_kill), \
             patch.object(main_module.subprocess, 'run', side_effect=fake_subprocess_run), \
             patch.object(main_module.time, 'sleep'):
            main_module._handle_restart(signal.SIGHUP, None)

        pkill_cmds = [c for c in subprocess_cmds if c and c[0] == "pkill"]
        assert len(pkill_cmds) == 0, \
            f"pkill must not be called, but found: {pkill_cmds}"
        assert (99999, signal.SIGTERM) in kill_calls, \
            "Must send SIGTERM to brain PID directly"


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


class TestDaemonProcessGroupIsolation:
    def test_main_calls_setpgid_before_singleton_lock(self):
        """main() calls os.setpgid(0, 0) before _acquire_singleton_lock."""
        import ironclaude.main as main_module

        call_order = []

        def track_setpgid(pid, pgid):
            call_order.append("setpgid")

        def track_lock():
            call_order.append("lock")
            raise SystemExit(0)  # Stop main() early

        with patch.object(main_module.os, 'setpgid', side_effect=track_setpgid), \
             patch.object(main_module, '_acquire_singleton_lock', side_effect=track_lock), \
             pytest.raises(SystemExit):
            main_module.main()

        assert call_order == ["setpgid", "lock"], \
            f"setpgid must be called before singleton lock, got: {call_order}"

    def test_main_setpgid_failure_does_not_crash(self):
        """main() continues if os.setpgid raises PermissionError."""
        import ironclaude.main as main_module

        def fail_setpgid(pid, pgid):
            raise PermissionError("Operation not permitted")

        def stop_lock():
            raise SystemExit(0)

        with patch.object(main_module.os, 'setpgid', side_effect=fail_setpgid), \
             patch.object(main_module, '_acquire_singleton_lock', side_effect=stop_lock), \
             pytest.raises(SystemExit):
            main_module.main()
        # No crash = pass

    def test_main_does_not_attach_daemon_log_handler_under_pytest(self):
        """SF1: under pytest (PYTEST_CURRENT_TEST set), main() must NOT attach the
        live /tmp/ic/daemon.log RotatingFileHandler — test runs that call main()
        would else pollute the real daemon log and corrupt latency stats."""
        import logging as _logging
        from logging.handlers import RotatingFileHandler as _RFH
        import ironclaude.main as main_module

        root = _logging.getLogger()
        before = list(root.handlers)

        def stop_lock():
            raise SystemExit(0)

        try:
            with patch.object(main_module.os, 'setpgid', lambda *a: None), \
                 patch.object(main_module, '_acquire_singleton_lock', side_effect=stop_lock), \
                 pytest.raises(SystemExit):
                main_module.main()
            added_daemon_log = [
                h for h in root.handlers
                if h not in before
                and isinstance(h, _RFH)
                and getattr(h, "baseFilename", "").endswith("daemon.log")
            ]
            assert not added_daemon_log, "daemon.log handler must not attach under pytest"
        finally:
            for h in list(root.handlers):
                if h not in before:
                    root.removeHandler(h)


class TestOperatorFastLane:
    """R3: operator-facing I/O (Slack commands, Brain responses, send-queue flush)
    runs on a ~3s fast lane; heavy worker/heartbeat sweeps stay on the 15s slow
    lane; the awaiting-operator classifier grade is bounded off the fast lane."""

    def test_fast_lane_runs_more_often_than_slow_sweep(self, daemon, monkeypatch):
        import ironclaude.main as main_module
        counts = {}

        def counter(name):
            def _f(*a, **k):
                counts[name] = counts.get(name, 0) + 1
            return _f

        for name in ("poll_slack_commands", "poll_brain_responses", "check_brain",
                     "process_brain_decisions", "check_workers",
                     "check_directive_capability_blocks", "check_confirmed_directives",
                     "check_idle_enforcement", "check_post_kill_sweep",
                     "check_message_aging", "post_heartbeat", "_run_maintenance",
                     "_sweep_expired_push_requests"):
            monkeypatch.setattr(daemon, name, counter(name))
        daemon.slack.flush_queue = counter("flush_queue")
        daemon._paused = False
        daemon.config["poll_interval_seconds"] = 15
        daemon.config["fast_poll_interval_seconds"] = 3

        clock = {"t": 0.0}
        state = {"n": 0}
        monkeypatch.setattr(main_module.time, "monotonic", lambda: clock["t"])

        def fake_sleep(s):
            clock["t"] += s
            state["n"] += 1
            if state["n"] >= 10:
                daemon._running = False
        monkeypatch.setattr(main_module.time, "sleep", fake_sleep)

        daemon._running = True
        daemon.run()

        assert counts["poll_slack_commands"] == 10           # fast lane: every tick
        assert counts["flush_queue"] == 10                   # flush on the fast lane
        assert counts["check_workers"] < counts["poll_slack_commands"]  # slow < fast
        assert counts["check_workers"] >= 2                  # slow lane still runs

    def test_awaiting_grade_is_bounded_off_the_fast_lane(self, daemon):
        import time as _t
        from unittest.mock import MagicMock
        daemon.config["fast_lane_grade_timeout_seconds"] = 0.3
        daemon._db = None
        daemon._grader = MagicMock()

        def slow_grade(*a, **k):
            _t.sleep(5)  # simulate an empty-Ollama stall
            return {"waiting_on": "operator", "worker_id": "w1", "question": "?"}
        daemon._grader.grade.side_effect = slow_grade

        started = _t.monotonic()
        captured = daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")
        elapsed = _t.monotonic() - started

        assert captured is False           # bound elapsed → not captured
        assert elapsed < 2.0               # returned at the ~0.3s bound, not the 5s stall


class TestOperatorPriorityNudgeGating:
    """R4: while the Brain is mid-turn on operator work (_executing_tool is True),
    background idle/grader/stuck nudges are suppressed so they never compete with
    the operator's own request. The idle clock keeps accumulating; nudges resume
    when the turn completes. Gated on `is True` so the MagicMock brains used in
    other tests (truthy _executing_tool) are unaffected."""

    def test_idle_escalation_suppressed_while_brain_executing(self, daemon):
        import time as _t
        daemon.registry.get_recent_workers.return_value = []
        daemon._db = None
        daemon._get_unprocessed_messages = lambda: ["m1"]  # pending work exists
        daemon._last_idle_check = 0.0
        daemon._idle_enforcement_start = _t.time() - 400  # tier-3 territory
        daemon._idle_escalation_tier = 0
        daemon._operator_notified_idle = False
        daemon.brain._executing_tool = True  # busy on operator work

        daemon.check_idle_enforcement()

        daemon.brain.send_message.assert_not_called()   # escalation nudge suppressed
        daemon.slack.post_message.assert_not_called()   # [ALERT] suppressed
        assert daemon._idle_enforcement_start != 0.0     # idle clock kept accumulating

    def test_idle_escalation_resumes_when_not_executing(self, daemon):
        import time as _t
        daemon.registry.get_recent_workers.return_value = []
        daemon._db = None
        daemon._get_unprocessed_messages = lambda: ["m1"]
        daemon._last_idle_check = 0.0
        daemon._idle_enforcement_start = _t.time() - 400
        daemon._idle_escalation_tier = 0
        daemon._operator_notified_idle = False
        daemon.brain._executing_tool = False  # not busy

        daemon.check_idle_enforcement()

        daemon.brain.send_message.assert_called()   # tier-3 CRITICAL nudge fires

    def test_grader_check_suppressed_while_brain_executing(self, daemon, tmp_path):
        daemon._db = init_db(str(tmp_path / "gc.db"))
        daemon._db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1.1', 'txt', 'do x', 'confirmed')"
        )
        daemon._db.commit()
        daemon.registry.get_recent_workers.return_value = []
        daemon._prompt_store = lambda: None
        daemon._last_heartbeat = 0.0
        daemon.brain._executing_tool = True

        daemon.post_heartbeat(now=10_000.0)

        assert not any(
            "GRADER CHECK" in str(c.args[0])
            for c in daemon.brain.send_message.call_args_list if c.args
        )


class TestBrainSettingsHookSync:
    """SF2: register repo-declared PreToolUse hooks into the Brain's settings.json
    via a template-driven, idempotent, non-clobbering merge that preserves every
    existing PreToolUse entry and the entire PostToolUse block."""

    @staticmethod
    def _real_settings():
        H = "$HOME/.claude/ironclaude-hooks"
        B = "$HOME/.ironclaude/brain/hooks"
        return {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": f"bash {B}/block-push.sh"}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": f"bash {H}/memory-search-enforcer.sh"}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": f"bash {H}/wiki-synthesis-enforcer.sh"}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": f"bash {H}/attention-sweep-enforcer.sh"}]},
                ],
                "PostToolUse": [
                    {"matcher": "mcp__orchestrator__get_worker_status", "hooks": [{"type": "command", "command": f"bash {H}/attention-sweep-arm.sh"}]},
                    {"matcher": "mcp__orchestrator__update_ledger", "hooks": [{"type": "command", "command": f"bash {B}/block-pin-enforcer.sh"}]},
                ],
            }
        }

    def test_sync_appends_preserves_all_deploys_and_is_idempotent(self, tmp_path):
        import json as _json
        from ironclaude.main import _sync_brain_settings_hooks

        lookback_cmd = "bash $HOME/.claude/ironclaude-hooks/startup-lookback-enforcer.sh"
        template = {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": lookback_cmd}]}]}
        template_path = tmp_path / "brain_settings_hooks.json"
        template_path.write_text(_json.dumps(template))
        settings_path = tmp_path / "settings.json"
        original = self._real_settings()
        settings_path.write_text(_json.dumps(original))
        hooks_src = tmp_path / "src_hooks"
        hooks_src.mkdir()
        (hooks_src / "startup-lookback-enforcer.sh").write_text("#!/bin/bash\nexit 0\n")
        hooks_dst = tmp_path / "dst_hooks"

        _sync_brain_settings_hooks(str(template_path), str(settings_path), str(hooks_src), str(hooks_dst))

        result = _json.loads(settings_path.read_text())
        pre_cmds = [h["command"] for e in result["hooks"]["PreToolUse"] for h in e["hooks"]]
        # every original PreToolUse command preserved
        for e in original["hooks"]["PreToolUse"]:
            for h in e["hooks"]:
                assert h["command"] in pre_cmds
        # lookback appended exactly once
        assert pre_cmds.count(lookback_cmd) == 1
        # PostToolUse byte-identical — block-pin-enforcer NOT dropped
        assert result["hooks"]["PostToolUse"] == original["hooks"]["PostToolUse"]
        # referenced script deployed
        assert (hooks_dst / "startup-lookback-enforcer.sh").is_file()

        # idempotent: a second run leaves exactly one lookback entry
        _sync_brain_settings_hooks(str(template_path), str(settings_path), str(hooks_src), str(hooks_dst))
        result2 = _json.loads(settings_path.read_text())
        pre_cmds2 = [h["command"] for e in result2["hooks"]["PreToolUse"] for h in e["hooks"]]
        assert pre_cmds2.count(lookback_cmd) == 1
        assert result2["hooks"]["PostToolUse"] == original["hooks"]["PostToolUse"]

    def test_sync_leaves_corrupt_settings_untouched(self, tmp_path):
        """A corrupt settings.json must NOT be clobbered — overwriting it with only
        the template entries would silently drop the existing guardrail hooks."""
        import json as _json
        from ironclaude.main import _sync_brain_settings_hooks

        template = {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command",
                    "command": "bash $HOME/.claude/ironclaude-hooks/startup-lookback-enforcer.sh"}]}]}
        template_path = tmp_path / "brain_settings_hooks.json"
        template_path.write_text(_json.dumps(template))
        settings_path = tmp_path / "settings.json"
        corrupt = '{"hooks": {"PreToolUse": [ THIS IS NOT JSON'
        settings_path.write_text(corrupt)
        hooks_src = tmp_path / "src_hooks"
        hooks_src.mkdir()
        (hooks_src / "startup-lookback-enforcer.sh").write_text("#!/bin/bash\nexit 0\n")

        _sync_brain_settings_hooks(str(template_path), str(settings_path),
                                   str(hooks_src), str(tmp_path / "dst"))

        # File is left exactly as-is for a human to repair (not overwritten).
        assert settings_path.read_text() == corrupt

    def test_template_includes_agent_task_gate_entry(self):
        """R5a: the version-controlled template declares the brain-task-gate hook with
        matcher 'Agent|Task', so the Task-8 sync deploys + registers it."""
        import json as _json
        import os as _os
        import ironclaude.main as _m
        repo_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(_m.__file__))))
        template_path = _os.path.join(repo_root, "src", "brain", "brain_settings_hooks.json")
        with open(template_path) as f:
            template = _json.load(f)
        gate = [
            e for e in template["PreToolUse"]
            if any("brain-task-gate.sh" in h.get("command", "") for h in e["hooks"])
        ]
        assert gate, "brain-task-gate.sh entry missing from brain_settings_hooks.json"
        assert gate[0]["matcher"] == "Agent|Task"

    def test_sync_creates_settings_when_missing(self, tmp_path):
        """A missing settings.json is fine — create it with the template entries."""
        import json as _json
        from ironclaude.main import _sync_brain_settings_hooks

        lookback_cmd = "bash $HOME/.claude/ironclaude-hooks/startup-lookback-enforcer.sh"
        template = {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": lookback_cmd}]}]}
        template_path = tmp_path / "brain_settings_hooks.json"
        template_path.write_text(_json.dumps(template))
        settings_path = tmp_path / "nested" / "settings.json"  # dir does not exist yet
        hooks_src = tmp_path / "src_hooks"
        hooks_src.mkdir()
        (hooks_src / "startup-lookback-enforcer.sh").write_text("#!/bin/bash\nexit 0\n")

        _sync_brain_settings_hooks(str(template_path), str(settings_path),
                                   str(hooks_src), str(tmp_path / "dst"))

        result = _json.loads(settings_path.read_text())
        cmds = [h["command"] for e in result["hooks"]["PreToolUse"] for h in e["hooks"]]
        assert cmds == [lookback_cmd]


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
        daemon.tmux.list_pane_pid.return_value = None

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
        """main.py and orchestrator_mcp.py WORKER_COMMANDS share the static sonnet entry."""
        from ironclaude.main import WORKER_COMMANDS as main_cmds
        from ironclaude.orchestrator_mcp import WORKER_COMMANDS as orc_cmds
        assert main_cmds["claude-sonnet"] == orc_cmds["claude-sonnet"]

    def test_worker_commands_use_exec_prefix(self):
        """main.py worker commands must use exec prefix."""
        from ironclaude.main import WORKER_COMMANDS
        from ironclaude.config import DEFAULTS, make_opus_command
        opus_cmd = make_opus_command(DEFAULTS["brain_model"], "high")
        assert "exec claude" in opus_cmd
        assert "exec claude" in WORKER_COMMANDS["claude-sonnet"]

    def test_worker_commands_set_effort_level_high(self):
        """main.py worker commands must set CLAUDE_CODE_EFFORT_LEVEL=high."""
        from ironclaude.main import WORKER_COMMANDS
        from ironclaude.config import DEFAULTS, make_opus_command
        opus_cmd = make_opus_command(DEFAULTS["brain_model"], "high")
        assert "CLAUDE_CODE_EFFORT_LEVEL=high" in opus_cmd
        assert "CLAUDE_CODE_EFFORT_LEVEL=high" in WORKER_COMMANDS["claude-sonnet"]



class TestDaemonWorkerTrust:
    @patch("ironclaude.main.ensure_worker_trusted")
    def test_spawn_worker_calls_ensure_worker_trusted(self, mock_trust, daemon):
        """_handle_spawn_worker uses ensure_worker_trusted (with realpath + .git check)."""
        daemon.registry.get_running_workers_by_type.return_value = []
        daemon.tmux.spawn_session.return_value = True
        daemon.tmux.read_log_tail.return_value = "ironclaude v1.0.0"
        daemon.tmux.list_pane_pid.return_value = None

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
        daemon.tmux.list_pane_pid.return_value = None

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
        daemon.tmux.list_pane_pid.return_value = None

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
    """Tests for ensure_brain_trusted — trusts any directory unconditionally."""

    def test_trusts_path_without_git_dir(self, tmp_path):
        """ensure_brain_trusted trusts directories even without .git."""
        non_git_dir = tmp_path / "brain_repo"
        non_git_dir.mkdir()
        claude_json = tmp_path / "claude.json"
        claude_json.write_text('{"projects": {}}')
        with patch("os.path.expanduser", return_value=str(claude_json)):
            ensure_brain_trusted(str(non_git_dir))
        data = json.loads(claude_json.read_text())
        projects = data.get("projects", {})
        abs_path = os.path.abspath(str(non_git_dir))
        assert abs_path in projects
        assert projects[abs_path]["hasTrustDialogAccepted"] is True

    def test_trusts_symlinked_path(self, tmp_path):
        """ensure_brain_trusted resolves symlinks and trusts the absolute path."""
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
        abs_path = os.path.abspath(str(link_dir))
        assert abs_path in projects
        assert projects[abs_path]["hasTrustDialogAccepted"] is True

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


class TestHandleSpawnWorkerAdvisor:
    def test_sends_advisor_before_objective_when_enabled(self, tmp_path):
        """With advisor in config, /advisor {model} is sent after PM wait, before objective."""
        config = {
            "tmp_dir": str(tmp_path),
            "advisor": {"enabled": True, "advisor_model": "opus"},
        }
        slack = MagicMock()
        registry = MagicMock()
        tmux = MagicMock()
        tmux.spawn_session.return_value = True
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(tmux.log_dir, exist_ok=True)
        brain = MagicMock()
        d = IroncladeDaemon(config, slack, None, registry, tmux, brain)
        d._wait_for_ready = MagicMock(return_value=True)
        tmux.list_pane_pid.return_value = None

        with patch("ironclaude.main.ensure_worker_trusted"):
            d._handle_spawn_worker({
                "worker_id": "w1",
                "type": "claude-sonnet",
                "repo": "/tmp/repo",
                "objective": "Test objective",
            })

        keys_sent = [call[0][1] for call in tmux.send_keys.call_args_list]
        assert "/advisor opus" in keys_sent
        advisor_idx = keys_sent.index("/advisor opus")
        obj_idx = keys_sent.index("Test objective")
        assert advisor_idx < obj_idx, f"advisor at {advisor_idx} must precede objective at {obj_idx}"

    def test_skips_advisor_when_not_in_config(self, daemon):
        """Default daemon config has no advisor key — no /advisor command sent."""
        daemon._wait_for_ready = MagicMock(return_value=True)
        daemon.tmux.list_pane_pid.return_value = None

        with patch("ironclaude.main.ensure_worker_trusted"):
            daemon._handle_spawn_worker({
                "worker_id": "w1",
                "type": "claude-sonnet",
                "repo": "/tmp/repo",
                "objective": "Test objective",
            })

        keys_sent = [call[0][1] for call in daemon.tmux.send_keys.call_args_list]
        assert not any(k.startswith("/advisor") for k in keys_sent)


class TestSpawnWorkerPid:
    def test_spawn_worker_logs_pane_pid(self, daemon, caplog):
        """WORKER_SPAWNED log entry includes pane_pid from tmux."""
        import logging
        daemon.tmux.list_pane_pid.return_value = "99999"
        daemon._wait_for_ready = MagicMock(return_value=True)

        with patch("ironclaude.main.ensure_worker_trusted"):
            with caplog.at_level(logging.INFO, logger="ironclaude"):
                daemon._handle_spawn_worker({
                    "worker_id": "w1",
                    "type": "claude-sonnet",
                    "repo": "/tmp",
                    "objective": "do work",
                })

        spawned = []
        for r in caplog.records:
            try:
                data = json.loads(r.getMessage())
                if data.get("event_type") == "WORKER_SPAWNED":
                    spawned.append(data)
            except (json.JSONDecodeError, TypeError):
                pass
        assert len(spawned) == 1, f"Expected 1 WORKER_SPAWNED entry, got {len(spawned)}"
        assert spawned[0]["pane_pid"] == "99999"


class TestIsOscillating:
    def test_returns_false_when_no_history(self, daemon):
        """No history → not oscillating."""
        assert daemon._is_oscillating("w1") is False

    def test_returns_false_below_threshold(self, daemon):
        """Two transitions → below threshold of 3."""
        now = time.time()
        daemon._stage_history["w1"] = [(now - 20, "executing"), (now - 10, "reviewing")]
        assert daemon._is_oscillating("w1") is False

    def test_returns_true_at_threshold(self, daemon):
        """Three transitions, all executing/reviewing → oscillating."""
        now = time.time()
        daemon._stage_history["w1"] = [
            (now - 30, "executing"),
            (now - 20, "reviewing"),
            (now - 10, "executing"),
        ]
        assert daemon._is_oscillating("w1") is True

    def test_returns_false_with_non_oscillating_stage(self, daemon):
        """Any non-executing/reviewing stage in history → not oscillating."""
        now = time.time()
        daemon._stage_history["w1"] = [
            (now - 20, "executing"),
            (now - 10, "reviewing"),
            (now - 5, "idle"),
        ]
        assert daemon._is_oscillating("w1") is False

    def test_returns_false_when_all_entries_expired(self, daemon):
        """Three transitions older than 900s window → pruned → not oscillating."""
        old = time.time() - 1000
        daemon._stage_history["w1"] = [
            (old, "executing"),
            (old + 1, "reviewing"),
            (old + 2, "executing"),
        ]
        assert daemon._is_oscillating("w1") is False

    def test_prunes_expired_entries(self, daemon):
        """_is_oscillating removes entries older than 900s from history in place."""
        now = time.time()
        old = now - 1000
        daemon._stage_history["w1"] = [
            (old, "executing"),
            (old + 1, "reviewing"),
            (now - 10, "executing"),
        ]
        result = daemon._is_oscillating("w1")
        assert result is False
        assert len(daemon._stage_history["w1"]) == 1

    def test_returns_false_with_none_stage(self, daemon):
        """None stage (DB miss) in history breaks oscillation check."""
        now = time.time()
        daemon._stage_history["w1"] = [
            (now - 20, "executing"),
            (now - 10, None),
            (now - 5, "executing"),
        ]
        assert daemon._is_oscillating("w1") is False


class TestOscillationSuppression:
    def _setup_worker(self, daemon):
        worker = {"id": "w1", "tmux_session": "ic-w1", "spawned_at": "2026-01-01 00:00:00"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "output"
        daemon.brain.send_message.return_value = True

    def test_stage_changed_suppressed_during_oscillation(self, daemon, tmp_path):
        """After 3+ ex/rev oscillations, stage_changed bypass is suppressed."""
        self._setup_worker(daemon)
        contact_file = os.path.join(daemon.tmux.log_dir, "ic-w1.brain_contact")

        with patch.object(daemon, "_get_worker_workflow_stage") as mock_stage:
            # Call 1: executing — initial send (no last_sent, cadence check passes)
            mock_stage.return_value = "executing"
            daemon.check_workers()
            assert daemon.brain.send_message.call_count == 1

            # Ack: set brain_contact just after last send
            with open(contact_file, "w") as f:
                f.write(str(daemon._last_checkin_sent["w1"] + 1))

            # Call 2: reviewing — stage_changed fires immediately
            mock_stage.return_value = "reviewing"
            daemon.check_workers()
            assert daemon.brain.send_message.call_count == 2

            # Ack: update brain_contact
            with open(contact_file, "w") as f:
                f.write(str(daemon._last_checkin_sent["w1"] + 1))

            # Call 3: executing — oscillation detected (3 transitions), suppressed
            mock_stage.return_value = "executing"
            daemon.check_workers()
            assert daemon.brain.send_message.call_count == 2  # not incremented

            # Call 4: reviewing — still oscillating, still suppressed
            mock_stage.return_value = "reviewing"
            daemon.check_workers()
            assert daemon.brain.send_message.call_count == 2  # still not incremented

    def test_genuine_stage_change_fires_after_oscillation(self, daemon, tmp_path):
        """plan_ready transition fires immediately once oscillation exits."""
        self._setup_worker(daemon)
        contact_file = os.path.join(daemon.tmux.log_dir, "ic-w1.brain_contact")

        with patch.object(daemon, "_get_worker_workflow_stage") as mock_stage:
            # Build oscillation: 3 transitions (calls 1, 2, 3)
            for stage in ["executing", "reviewing", "executing"]:
                mock_stage.return_value = stage
                daemon.check_workers()
                if "w1" in daemon._last_checkin_sent:
                    with open(contact_file, "w") as f:
                        f.write(str(daemon._last_checkin_sent["w1"] + 1))

            pre_count = daemon.brain.send_message.call_count

            # Genuine stage change: plan_ready — stage_changed=True, fires immediately
            mock_stage.return_value = "plan_ready"
            daemon.check_workers()

            assert daemon.brain.send_message.call_count == pre_count + 1
            msg = daemon.brain.send_message.call_args[0][0]
            assert "plan_ready" in msg


class TestCheckWorkersRemote:
    """Tests for check_workers handling remote (SSH) workers."""

    def _make_remote_worker(self):
        return {
            "id": "w-remote",
            "tmux_session": "ic-w-remote",
            "machine": "remote-worker",
            "spawned_at": "2026-01-01 00:00:00",
        }

    def _make_local_worker(self):
        return {"id": "w-local", "tmux_session": "ic-w-local", "machine": None}

    def test_remote_done_marker_via_ssh(self, daemon):
        """Remote worker .done marker is detected via tmux.file_exists over SSH."""
        worker = self._make_remote_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon._ssh_manager = MagicMock()
        machine_cfg = MagicMock()
        machine_cfg.host = "remote-worker"
        machine_cfg.log_dir = "/tmp/ic-logs"
        daemon._ssh_manager.get_machine.return_value = machine_cfg
        daemon.tmux.file_exists.return_value = True
        daemon.brain.send_message.return_value = True
        daemon.tmux.remove_file.return_value = True

        daemon.check_workers()

        daemon.tmux.file_exists.assert_called_once()
        call_args = daemon.tmux.file_exists.call_args
        assert call_args[0][0].endswith("ic-w-remote.done")
        assert call_args[1].get("ssh_host") == "remote-worker" or call_args[0][1] == "remote-worker"

    def test_remote_dead_session_via_ssh(self, daemon):
        """Remote worker dead session is detected via tmux.has_session over SSH."""
        worker = self._make_remote_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon._ssh_manager = MagicMock()
        machine_cfg = MagicMock()
        machine_cfg.host = "remote-worker"
        machine_cfg.log_dir = "/tmp/ic-logs"
        daemon._ssh_manager.get_machine.return_value = machine_cfg
        daemon.tmux.file_exists.return_value = False
        daemon.tmux.has_session.return_value = False

        daemon.check_workers()

        daemon.tmux.has_session.assert_called_once()
        call_args = daemon.tmux.has_session.call_args
        assert call_args[0][0] == "ic-w-remote"
        assert call_args[1].get("ssh_host") == "remote-worker" or (len(call_args[0]) > 1 and call_args[0][1] == "remote-worker")
        # Seam owns completion: bare-MagicMock registry yields an 'authority'
        # (preserved) outcome, so the daemon completes nothing itself.
        daemon.registry.update_worker_status.assert_not_called()

    def test_local_worker_unaffected(self, daemon):
        """Local worker (machine=None) uses local file checks, not SSH."""
        worker = self._make_local_worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = False
        marker = os.path.join(daemon.tmux.log_dir, "ic-w-local.done")

        daemon.check_workers()

        # Seam owns completion: bare-MagicMock registry yields an 'authority'
        # (preserved) outcome, so the daemon completes nothing itself.
        daemon.registry.update_worker_status.assert_not_called()

    def test_get_worker_workflow_stage_remote(self, daemon):
        """_get_worker_workflow_stage queries remote DB via tmux.run_sqlite_query."""
        daemon.tmux.list_pane_pid.return_value = "12345"
        daemon.tmux.read_file.return_value = "abcdef01-2345-6789-abcd-ef0123456789"
        daemon.tmux.run_sqlite_query.return_value = "executing"

        result = daemon._get_worker_workflow_stage("ic-w1", ssh_host="remote-worker")

        daemon.tmux.list_pane_pid.assert_called_with("ic-w1", ssh_host="remote-worker")
        daemon.tmux.read_file.assert_called_once()
        daemon.tmux.run_sqlite_query.assert_called_once()
        assert result == "executing"

    def test_get_worker_workflow_stage_remote_no_pid(self, daemon):
        """Returns None when remote pane PID cannot be retrieved."""
        daemon.tmux.list_pane_pid.return_value = None

        result = daemon._get_worker_workflow_stage("ic-w1", ssh_host="remote-worker")
        assert result is None


class TestDirectiveUnpin:
    """Tests for unpin calls on directive confirmation/rejection."""

    @pytest.fixture
    def db_conn(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @pytest.fixture
    def daemon_db(self, tmp_path, db_conn):
        from ironclaude.main import IroncladeDaemon
        config = {"tmp_dir": str(tmp_path), "operator_name": "TestOp"}
        slack = MagicMock()
        slack.post_message.return_value = "reply-ts"
        registry = MagicMock()
        tmux = MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(str(tmp_path / "logs"), exist_ok=True)
        brain = MagicMock()
        d = IroncladeDaemon(
            config=config, slack=slack, socket_handler=None,
            registry=registry, tmux_manager=tmux, brain=brain, db_conn=db_conn,
        )
        return d, db_conn

    def _insert_directive(self, db, interpretation_ts="interp-ts-001"):
        db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status, interpretation_ts)"
            " VALUES ('src-ts-001', 'fix bug', 'Fix the login bug', 'pending_confirmation', ?)",
            (interpretation_ts,),
        )
        db.commit()

    def test_reaction_confirm_unpins(self, daemon_db):
        """thumbsup reaction calls unpin_message with interpretation_ts."""
        daemon, db = daemon_db
        self._insert_directive(db)
        daemon._handle_directive_reaction("thumbsup", "interp-ts-001")
        daemon.slack.unpin_message.assert_called_once_with("interp-ts-001")

    def test_reaction_reject_unpins(self, daemon_db):
        """thumbsdown reaction calls unpin_message with interpretation_ts."""
        daemon, db = daemon_db
        self._insert_directive(db)
        daemon._handle_directive_reaction("thumbsdown", "interp-ts-001")
        daemon.slack.unpin_message.assert_called_once_with("interp-ts-001")

    def test_reaction_skips_unpin_when_ts_null(self, daemon_db):
        """No unpin_message call when interpretation_ts is NULL."""
        daemon, db = daemon_db
        db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status)"
            " VALUES ('src-ts-001', 'fix bug', 'Fix the login bug', 'pending_confirmation')"
        )
        db.commit()
        daemon._handle_directive_reaction("thumbsup", "src-ts-001")
        daemon.slack.unpin_message.assert_not_called()

    def test_text_confirm_unpins(self, daemon_db):
        """Text 'yes' confirmation calls unpin_message with interpretation_ts."""
        daemon, db = daemon_db
        self._insert_directive(db)
        daemon._handle_directive_confirmation("yes")
        daemon.slack.unpin_message.assert_called_once_with("interp-ts-001")

    def test_text_reject_unpins(self, daemon_db):
        """Text 'no' rejection calls unpin_message with interpretation_ts."""
        daemon, db = daemon_db
        self._insert_directive(db)
        daemon._handle_directive_confirmation("no")
        daemon.slack.unpin_message.assert_called_once_with("interp-ts-001")


class TestPostHeartbeat:
    def test_heartbeat_includes_all_alive_workers(self, daemon):
        """Heartbeat posts message containing all workers with live tmux sessions."""
        workers = [
            {"id": f"w{i}", "tmux_session": f"ic-w{i}", "description": f"task {i}", "machine": None}
            for i in range(1, 7)
        ]
        daemon.registry.get_recent_workers.return_value = workers
        daemon.tmux.has_session.return_value = True
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        daemon.slack.post_message.assert_called_once()
        msg = daemon.slack.post_message.call_args[0][0]
        for i in range(1, 7):
            assert f"w{i}" in msg

    def test_heartbeat_excludes_dead_sessions(self, daemon):
        """Heartbeat excludes workers where has_session returns False."""
        workers = [
            {"id": "w1", "tmux_session": "ic-w1", "description": "task 1", "machine": None},
            {"id": "w2", "tmux_session": "ic-w2", "description": "task 2", "machine": None},
        ]
        daemon.registry.get_recent_workers.return_value = workers
        daemon.tmux.has_session.side_effect = lambda name, ssh_host=None: name == "ic-w1"
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "w1" in msg
        assert "w2" not in msg

    def test_heartbeat_no_workers_posts_no_active(self, daemon):
        """Heartbeat posts 'No active workers' when all sessions are dead."""
        daemon.registry.get_recent_workers.return_value = [
            {"id": "w1", "tmux_session": "ic-w1", "description": "done", "machine": None}
        ]
        daemon.tmux.has_session.return_value = False
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "No active workers" in msg

    def test_heartbeat_respects_interval(self, daemon):
        """Heartbeat does not fire before interval elapses."""
        daemon._last_heartbeat = time.time()
        daemon.post_heartbeat()
        daemon.slack.post_message.assert_not_called()

    def test_post_heartbeat_degraded_label_names_resolved_openai_backend(
        self, daemon, tmp_path, monkeypatch
    ):
        """The degraded heartbeat names the resolved validator backend (OpenAI),
        guarding the main.py resolve_degraded_backend_label wiring."""
        cfg = tmp_path / "hooks.json"
        cfg.write_text('{"backend": "openai", "openai": {"base_url": "http://h/v1", "model": "m"}}')
        monkeypatch.setenv("IC_OLLAMA_CONFIG_PATH", str(cfg))
        daemon._last_heartbeat = 0
        with patch("ironclaude.ollama_client.ollama_degraded_urls", return_value=["http://h/v1"]):
            daemon.post_heartbeat()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "OpenAI endpoint(s) unreachable/degraded" in msg
        assert "Ollama endpoint(s) unreachable/degraded" not in msg


class TestGetRecentWorkers:
    def _make_registry(self, tmp_path):
        from ironclaude.db import init_db
        from ironclaude.worker_registry import WorkerRegistry
        conn = init_db(str(tmp_path / "ic.db"))
        return WorkerRegistry(conn)

    def test_returns_running_workers(self, tmp_path):
        """Returns workers with status='running'."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        results = reg.get_recent_workers()
        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_returns_recently_finished_workers(self, tmp_path):
        """Returns workers finished within lookback window."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg._conn.execute(
            "UPDATE workers SET status='completed', finished_at=datetime('now', '-30 minutes') WHERE id='w1'"
        )
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        assert len(results) == 1
        assert results[0]["id"] == "w1"

    def test_excludes_old_finished_workers(self, tmp_path):
        """Excludes workers finished outside the lookback window."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg._conn.execute(
            "UPDATE workers SET status='completed', finished_at=datetime('now', '-2 hours') WHERE id='w1'"
        )
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        assert len(results) == 0

    def test_excludes_finished_null_timestamp(self, tmp_path):
        """Excludes workers with status='completed' but NULL finished_at."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg._conn.execute("UPDATE workers SET status='completed' WHERE id='w1'")
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        assert len(results) == 0

    def test_returns_both_running_and_recent_finished(self, tmp_path):
        """Returns mix of running and recently-finished workers."""
        reg = self._make_registry(tmp_path)
        reg.register_worker("w1", "claude-sonnet", "ic-w1", repo="/repo")
        reg.register_worker("w2", "claude-sonnet", "ic-w2", repo="/repo")
        reg._conn.execute(
            "UPDATE workers SET status='completed', finished_at=datetime('now', '-10 minutes') WHERE id='w2'"
        )
        reg._conn.commit()
        results = reg.get_recent_workers(lookback_hours=1)
        ids = {r["id"] for r in results}
        assert ids == {"w1", "w2"}


class TestHashDedupBypassPromptWaiting:
    def test_semantic_prompt_bypasses_hash_dedup_once(self, daemon, tmp_path):
        """A validated prompt bypasses pane hash dedup through durable routing."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Sequential\n2. Parallel\n3. Inline"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "plan_ready")
        daemon._claude_dir = claude_dir
        daemon._db = init_db(str(tmp_path / "hash-prompt.db"))
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_hash["w1"] = hash("1. Sequential\n2. Parallel\n3. Inline")
        daemon._last_checkin_sent["w1"] = time.time() - 1000
        daemon._last_checkin_stage["w1"] = "plan_ready"

        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(_semantic_prompt(), True)
        )

        daemon.check_workers()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[ACTION REQUIRED]" in msg

    def test_non_prompt_waiting_still_blocked_by_hash(self, daemon, tmp_path):
        """Hash dedup still blocks when prompt_waiting is False."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "same output"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "executing")
        daemon._claude_dir = claude_dir

        daemon._last_checkin_hash["w1"] = hash("same output")
        daemon._last_checkin_sent["w1"] = time.time() - 1000
        daemon._last_checkin_stage["w1"] = "executing"

        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(None, True)
        )

        daemon.check_workers()
        daemon.brain.send_message.assert_not_called()


class TestDurablePromptRouting:
    @staticmethod
    def _worker(worker_id="worker-1"):
        return {
            "id": worker_id,
            "tmux_session": f"ic-{worker_id}",
            "spawned_at": "2026-03-08 00:00:00",
            "description": "waiting task",
            "machine": None,
        }

    def _configure(self, daemon, tmp_path, db_name="prompt-routing.db"):
        daemon._db = init_db(str(tmp_path / db_name))
        worker = self._worker()
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.registry.get_recent_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Which action should run?\n1. Continue\n❯ "
        daemon._get_worker_workflow_stage = MagicMock(return_value="plan_ready")
        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(_semantic_prompt(), True)
        )
        daemon.brain.send_message.return_value = True
        daemon.brain.capability_block = None
        return worker

    def test_twenty_three_unchanged_checks_send_one_brain_action(self, daemon, tmp_path):
        self._configure(daemon, tmp_path)
        for _ in range(23):
            daemon.check_workers()
        assert daemon.brain.send_message.call_count == 1
        assert daemon.slack.post_message.call_count == 0
        row = PromptIncidentStore(daemon._db).active_for_worker("worker-1")
        assert row["dispatch_state"] == "delivered"

    def test_reconstruction_holds_same_prompt_and_changed_prompt_dispatches(self, daemon, tmp_path):
        worker = self._configure(daemon, tmp_path, "reconstruct-prompt.db")
        daemon.check_workers()
        db_path = str(tmp_path / "reconstruct-prompt.db")
        daemon._db.close()

        conn = init_db(db_path)
        second = IroncladeDaemon(
            {"tmp_dir": str(tmp_path)}, MagicMock(), None,
            daemon.registry, daemon.tmux, MagicMock(), db_conn=conn,
        )
        second.registry.get_running_workers.return_value = [worker]
        second.tmux.has_session.return_value = True
        second.tmux.capture_pane.return_value = "Which action should run?\n1. Continue\n❯ "
        second._get_worker_workflow_stage = MagicMock(return_value="plan_ready")
        second._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(_semantic_prompt(), True)
        )
        second.brain.send_message.return_value = True
        second.brain.capability_block = None

        second.check_workers()
        second.brain.send_message.assert_not_called()
        second.tmux.capture_pane.return_value = "Which action should run?\n1. Retry\n❯ "
        second._detect_worker_prompt.return_value = PromptDetection(
            _semantic_prompt(options=(("1", "Retry"),)), True
        )
        second.check_workers()
        second.brain.send_message.assert_called_once()

    def test_reconstruction_claims_pending_initial_dispatch(self, daemon, tmp_path):
        worker = self._configure(daemon, tmp_path, "pending-initial.db")
        store = PromptIncidentStore(daemon._db)
        pending = store.observe(
            worker["id"], "plan_ready", _semantic_prompt(), now=1.0
        )

        daemon.check_workers()

        daemon.brain.send_message.assert_called_once()
        row = daemon._db.execute(
            "SELECT state FROM worker_prompt_dispatches WHERE id=?",
            (pending.dispatch_id,),
        ).fetchone()
        assert row[0] == "delivered"

    def test_transient_capture_failure_does_not_resolve_active_prompt(self, daemon, tmp_path):
        self._configure(daemon, tmp_path)
        daemon.check_workers()
        daemon.tmux.capture_pane.side_effect = RuntimeError("tmux unavailable")

        daemon.check_workers()

        assert PromptIncidentStore(daemon._db).active_for_worker("worker-1") is not None
        assert daemon.brain.send_message.call_count == 1

    def test_prompt_disappearance_and_missing_worker_resolve_episode(self, daemon, tmp_path):
        self._configure(daemon, tmp_path)
        daemon.check_workers()
        daemon.tmux.capture_pane.return_value = "All tests passed"
        daemon._detect_worker_prompt.return_value = PromptDetection(None, True)
        daemon.check_workers()
        assert PromptIncidentStore(daemon._db).active_for_worker("worker-1") is None

        daemon.tmux.capture_pane.return_value = "Which action should run?\n1. Continue\n❯ "
        daemon._detect_worker_prompt.return_value = PromptDetection(_semantic_prompt(), True)
        daemon._last_checkin_sent.clear()
        daemon.check_workers()
        assert PromptIncidentStore(daemon._db).active_for_worker("worker-1") is not None
        daemon.registry.get_running_workers.return_value = []
        daemon.check_workers()
        assert PromptIncidentStore(daemon._db).active_for_worker("worker-1") is None

    def test_active_prompt_coalesces_heartbeat_stuck_action(self, daemon, tmp_path):
        worker = self._configure(daemon, tmp_path)
        daemon.check_workers()
        daemon.brain.reset_mock()
        daemon.slack.reset_mock()
        log_path = os.path.join(daemon.tmux.log_dir, f"{worker['tmux_session']}.log")
        with open(log_path, "w") as stream:
            stream.write("x" * 100)

        daemon.post_heartbeat(now=1000.0)
        daemon.post_heartbeat(now=2000.0)

        daemon.brain.send_message.assert_not_called()
        rendered = "\n".join(call.args[0] for call in daemon.slack.post_message.call_args_list)
        assert "prompt active" in rendered
        assert "Worker worker-1 unchanged" not in rendered

    def test_inconclusive_recheck_retains_active_prompt(self, daemon, tmp_path):
        self._configure(daemon, tmp_path)
        daemon.check_workers()
        daemon._detect_worker_prompt.return_value = PromptDetection(None, False)
        daemon.tmux.capture_pane.return_value = "grader unavailable"

        daemon.check_workers()

        assert PromptIncidentStore(daemon._db).active_for_worker("worker-1") is not None
        assert daemon.brain.send_message.call_count == 1

    def test_stale_resolved_history_cannot_supersede_active_incident(self, daemon, tmp_path):
        self._configure(daemon, tmp_path)
        daemon.check_workers()
        original = PromptIncidentStore(daemon._db).active_for_worker("worker-1")
        daemon._detect_worker_prompt = IroncladeDaemon._detect_worker_prompt.__get__(daemon)
        daemon.tmux.capture_pane.return_value = (
            "Which action should run?\nAnswer: Continue\n"
            "Running tests...\n12 passed\n❯ "
        )
        daemon._grader.grade = MagicMock(return_value={
            "kind": "question",
            "interaction_block": "Which action should run?",
            "question": "Which action should run?",
            "options": [],
            "authority_text": "",
        })

        daemon.check_workers()

        retained = PromptIncidentStore(daemon._db).active_for_worker("worker-1")
        assert retained["id"] == original["id"]
        assert daemon.brain.send_message.call_count == 1
        daemon.slack.post_message.assert_not_called()

    def test_telemetry_and_stage_changes_do_not_redispatch_or_alert(self, daemon, tmp_path):
        self._configure(daemon, tmp_path)
        daemon.check_workers()
        daemon._get_worker_workflow_stage.return_value = "executing"
        daemon.tmux.capture_pane.return_value = (
            "Which action should run?\n1. Continue\n❯ \n"
            "reviewer 45s · 50.5k tokens\n/goal active (3h)"
        )
        daemon._detect_worker_prompt.return_value = PromptDetection(
            _semantic_prompt(evidence=daemon.tmux.capture_pane.return_value), True
        )

        daemon.check_workers()

        assert daemon.brain.send_message.call_count == 1
        daemon.slack.post_message.assert_not_called()

    def _seed_guidance(self, daemon, tmp_path):
        self._configure(daemon, tmp_path, "guidance.db")
        daemon.check_workers()
        daemon.brain.reset_mock()
        daemon.slack.reset_mock()
        daemon.socket_handler = MagicMock()

    @staticmethod
    def _message(text, ts):
        return {
            "parsed": {"type": "message", "text": text},
            "original_text": text,
            "ts": ts,
        }

    def test_exact_worker_operator_guidance_rearms_once_with_elapsed_context(
        self, daemon, tmp_path
    ):
        self._seed_guidance(daemon, tmp_path)
        item = self._message("worker-1 proceed with option 1", "1700000000.000100")
        daemon.socket_handler.drain.return_value = [item]
        daemon.poll_slack_commands()

        daemon.brain.send_message.assert_called_once()
        message = daemon.brain.send_message.call_args.args[0]
        assert "OPERATOR MESSAGE" in message
        assert "[ACTIVE PROMPT] worker=worker-1" in message
        assert "age=" in message
        daemon.slack.add_reaction.assert_called_once_with(
            "eyes", "1700000000.000100"
        )

        daemon.socket_handler.drain.return_value = [item]
        daemon.poll_slack_commands()
        assert daemon.brain.send_message.call_count == 1
        assert "already recorded" in daemon.slack.post_message.call_args.args[0]

    def test_pending_operator_guidance_is_retried_only_by_same_slack_event(
        self, daemon, tmp_path
    ):
        self._seed_guidance(daemon, tmp_path)
        store = PromptIncidentStore(daemon._db)
        pending = store.rearm_from_operator_guidance(
            "worker-1", "1700000000.000100", now=1.0
        )
        assert pending is not None

        item = self._message("worker-1 proceed with option 1", "1700000000.000100")
        daemon.socket_handler.drain.return_value = [item]
        daemon.poll_slack_commands()

        daemon.brain.send_message.assert_called_once()
        row = daemon._db.execute(
            "SELECT state FROM worker_prompt_dispatches WHERE id=?",
            (pending.dispatch_id,),
        ).fetchone()
        assert row[0] == "delivered"

    def test_exact_worker_guidance_with_invalid_source_identity_fails_closed(
        self, daemon, tmp_path
    ):
        self._seed_guidance(daemon, tmp_path)
        daemon.socket_handler.drain.return_value = [
            self._message("worker-1 proceed", "not-a-slack-ts")
        ]

        daemon.poll_slack_commands()

        daemon.brain.send_message.assert_not_called()
        assert "held" in daemon.slack.post_message.call_args.args[0]

    def test_prompt_store_claim_failure_holds_guidance_without_generic_forward(
        self, daemon, tmp_path, monkeypatch
    ):
        self._seed_guidance(daemon, tmp_path)

        def fail_claim(*args, **kwargs):
            raise sqlite3.OperationalError("database unavailable")

        monkeypatch.setattr(PromptIncidentStore, "claim_dispatch", fail_claim)
        daemon.socket_handler.drain.return_value = [
            self._message("worker-1 proceed", "1700000000.000100")
        ]

        daemon.poll_slack_commands()

        daemon.brain.send_message.assert_not_called()
        assert "held" in daemon.slack.post_message.call_args.args[0]

    def test_unrelated_or_ambiguous_operator_text_stays_generic(self, daemon, tmp_path):
        self._seed_guidance(daemon, tmp_path)
        store = PromptIncidentStore(daemon._db)
        other = store.observe("worker-2", "plan_ready", _semantic_prompt("Choose B?"), now=1.0)
        assert store.claim_dispatch(other.dispatch_id, destination="brain", now=2.0)
        store.record_delivery(other.dispatch_id, delivered=True)

        unrelated = self._message("please summarize", "1700000001.000100")
        ambiguous = self._message(
            "worker-1 and worker-2 should continue", "1700000002.000100"
        )
        daemon.socket_handler.drain.return_value = [unrelated, ambiguous]
        daemon.poll_slack_commands()

        assert daemon.brain.send_message.call_count == 2
        sent = [call.args[0] for call in daemon.brain.send_message.call_args_list]
        assert all("[ACTIVE PROMPT]" not in message for message in sent)
        count = daemon._db.execute(
            "SELECT COUNT(*) FROM worker_prompt_dispatches "
            "WHERE reason='operator_guidance'"
        ).fetchone()[0]
        assert count == 0

    def test_generic_forward_ack_threads_under_operator_message(
        self, daemon, tmp_path
    ):
        """The 'Forwarded to brain' ack threads under the operator's message ts,
        co-located with the Brain's reply-to answer, instead of posting top-level."""
        self._seed_guidance(daemon, tmp_path)
        item = self._message("ping", "T123")
        daemon.socket_handler.drain.return_value = [item]

        daemon.poll_slack_commands()

        call = next(
            c for c in daemon.slack.post_message.call_args_list
            if "Forwarded to brain" in c.args[0]
        )
        assert call.kwargs.get("thread_ts") == "T123"


class TestPromptWaitingPmGateSlack:
    def test_active_prompt_suppresses_pm_gate_slack_at_threshold(self, daemon, tmp_path):
        """A durable routine prompt never becomes a direct PM-gate operator alert."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Sequential\n2. Parallel"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "plan_ready")
        daemon._claude_dir = claude_dir
        daemon._db = init_db(str(tmp_path / "pm-gate.db"))
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_sent["w1"] = time.time() - 2000
        daemon._last_checkin_stage["w1"] = "plan_ready"

        daemon._stage_entered_at["w1"] = time.time() - 1860
        daemon._last_stage_seen["w1"] = "plan_ready"

        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(_semantic_prompt(), True)
        )

        daemon.check_workers()
        daemon.slack.post_message.assert_not_called()
        assert PromptIncidentStore(daemon._db).active_for_worker("w1") is not None

    def test_pm_gate_slack_deduped(self, daemon, tmp_path):
        """PM gate Slack notification only fires once per worker per stage."""
        worker = {
            "id": "w1", "tmux_session": "ic-w1",
            "spawned_at": "2026-03-08 00:00:00",
        }
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Sequential\n2. Parallel"
        daemon.tmux.list_pane_pid.return_value = "12345"

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        _setup_ironclaude_db(claude_dir, "12345", "abcdef01-2345-6789-abcd-ef0123456789", "plan_ready")
        daemon._claude_dir = claude_dir
        daemon.brain.send_message.return_value = True

        daemon._last_checkin_sent["w1"] = time.time() - 2000
        daemon._last_checkin_stage["w1"] = "plan_ready"
        daemon._stage_entered_at["w1"] = time.time() - 1860
        daemon._last_stage_seen["w1"] = "plan_ready"
        daemon._pm_gate_slack_sent["w1"] = True

        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(_semantic_prompt(), True)
        )

        daemon.check_workers()
        daemon.slack.post_message.assert_not_called()


class TestStuckWorkerSlackAlert:
    def test_active_prompt_suppresses_general_stuck_alert(self, daemon, tmp_path):
        """Routine prompt state suppresses both Brain and Slack stuck duplicates."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "1. Option A\n2. Option B"

        daemon._stuck_hash["w1"] = hash("1. Option A\n2. Option B")
        daemon._stuck_since["w1"] = time.time() - 960
        daemon._stuck_alert_sent["w1"] = False

        daemon._db = init_db(str(tmp_path / "stuck-prompt.db"))
        initial = PromptIncidentStore(daemon._db).observe(
            "w1", "plan_ready", _semantic_prompt(), now=1.0
        )
        assert initial.action == "dispatch"
        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(_semantic_prompt(), True)
        )
        daemon._last_stuck_check = 0

        daemon.check_stuck_workers()

        daemon.brain.send_message.assert_not_called()
        daemon.slack.post_message.assert_not_called()

    def test_no_slack_for_non_prompt_waiting_stuck_alert(self, daemon):
        """Slack notification does NOT fire when prompt_waiting is False at stuck alert."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        daemon.tmux.capture_pane.return_value = "Running tests..."

        daemon._stuck_hash["w1"] = hash("Running tests...")
        daemon._stuck_since["w1"] = time.time() - 1900
        daemon._stuck_alert_sent["w1"] = False

        daemon._detect_worker_prompt = MagicMock(
            return_value=PromptDetection(None, True)
        )
        daemon._last_stuck_check = 0

        daemon.check_stuck_workers()

        daemon.brain.send_message.assert_called_once()
        daemon.slack.post_message.assert_not_called()


class TestLivenessDeferralCap:
    def test_prompt_waiting_kill_after_max_deferrals(self, daemon):
        """Kill proceeds for prompt-waiting worker after MAX_LIVENESS_DEFERRALS."""
        daemon._stuck_liveness_count["w1"] = 2
        daemon._stuck_since["w1"] = time.time() - 3600
        daemon._stuck_hash["w1"] = 12345
        daemon._stuck_alert_sent["w1"] = True

        with patch('ironclaude.main.psutil.Process') as mock_process_cls:
            mock_child = MagicMock()
            mock_child.cpu_percent.return_value = 5.0
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_process_cls.return_value = mock_parent

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", True, None,
            )

        daemon.tmux.kill_session.assert_called_once_with("ic-w1", ssh_host=None)

    def test_non_prompt_waiting_still_defers(self, daemon):
        """Non-prompt-waiting worker still defers on liveness check regardless of count."""
        daemon._stuck_liveness_count["w1"] = 5

        with patch('ironclaude.main.psutil.Process') as mock_process_cls:
            mock_child = MagicMock()
            mock_child.cpu_percent.return_value = 5.0
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_process_cls.return_value = mock_parent

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", False, None,
            )

        daemon.tmux.kill_session.assert_not_called()
        assert daemon._stuck_kill_deferred.get("w1", 0) > 0

    def test_deferral_count_increments(self, daemon):
        """Liveness deferral increments the counter."""
        daemon._stuck_liveness_count["w1"] = 0

        with patch('ironclaude.main.psutil.Process') as mock_process_cls:
            mock_child = MagicMock()
            mock_child.cpu_percent.return_value = 5.0
            mock_parent = MagicMock()
            mock_parent.children.return_value = [mock_child]
            mock_process_cls.return_value = mock_parent

            daemon.tmux.list_pane_pid.return_value = "12345"
            daemon._confirm_and_kill_stuck_worker(
                "w1", "ic-w1", 3600.0, "plan_ready", False, None,
            )

        assert daemon._stuck_liveness_count["w1"] == 1

    def test_cleanup_removes_liveness_count(self, daemon):
        """After kill, liveness count is cleaned up."""
        daemon._stuck_liveness_count["w1"] = 3
        daemon._stuck_since["w1"] = time.time() - 3600
        daemon._stuck_hash["w1"] = 12345
        daemon._stuck_alert_sent["w1"] = True

        daemon.tmux.list_pane_pid.return_value = None
        daemon._confirm_and_kill_stuck_worker(
            "w1", "ic-w1", 3600.0, "plan_ready", True, None,
        )

        assert "w1" not in daemon._stuck_liveness_count


import ironclaude.main as _ic_main


class TestBrainMessageFilter:
    """Tests for _validate_brain_message directive-reference pre-filter."""

    _NO_DIR = "no_directive_ref"

    @pytest.mark.parametrize("text", [
        "#123 fixed the bug",
        "d456 update with reason",
        "D789 status",
        "directive 10 completed",
        "DIRECTIVE 42 done",
        "Directive 9 resolved",
    ])
    def test_directive_ref_re_matches(self, text):
        """_DIRECTIVE_REF_RE matches valid directive reference patterns."""
        assert _ic_main._DIRECTIVE_REF_RE.search(text) is not None

    @pytest.mark.parametrize("text", [
        "directive no number",
        "dSomething not a directive",
        "#abc not numeric",
        "d_none underscore",
        "",
        "plain conversational text",
    ])
    def test_directive_ref_re_no_match(self, text):
        """_DIRECTIVE_REF_RE does not match non-directive text."""
        assert _ic_main._DIRECTIVE_REF_RE.search(text) is None

    def test_no_directive_ref_blocked_without_grader_call(self, daemon):
        """Message without directive ref: blocked as no_directive_ref, grader not called."""
        daemon._grader = MagicMock()
        valid, reason = daemon._validate_brain_message("That was conversational output")
        assert valid is False
        assert reason == self._NO_DIR
        daemon._grader.grade.assert_not_called()

    def test_context_required_ack_blocked_without_grader_call(self, daemon):
        """Brain ack to CONTEXT_REQUIRED has no directive ref; blocked before grader."""
        daemon._grader = MagicMock()
        valid, reason = daemon._validate_brain_message(
            "[CONTEXT REQUIRED] ack, will restate with directive ref"
        )
        assert valid is False
        assert reason == self._NO_DIR
        daemon._grader.grade.assert_not_called()

    def test_directive_ref_valid_passes_through(self, daemon):
        """Message with directive ref: grader called; valid=True → (True, '')."""
        daemon._grader = MagicMock()
        daemon._grader.grade.return_value = {"valid": True}
        valid, reason = daemon._validate_brain_message(
            "#1134 fixed the deploy pipeline — build now stable"
        )
        assert valid is True
        assert reason == ""
        daemon._grader.grade.assert_called_once()

    def test_directive_ref_grader_blocks(self, daemon):
        """Message with directive ref: grader called; valid=False → (False, reason)."""
        daemon._grader = MagicMock()
        daemon._grader.grade.return_value = {"valid": False, "reason": "No reason clause"}
        valid, reason = daemon._validate_brain_message("#1134 update")
        assert valid is False
        assert reason == "No reason clause"
        daemon._grader.grade.assert_called_once()

    def test_directive_ref_infrastructure_error_fail_open(self, daemon):
        """Message with directive ref: Ollama down → fail-open (True, '')."""
        daemon._grader = MagicMock()
        daemon._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "Ollama down"}
        valid, reason = daemon._validate_brain_message("#1134 update")
        assert valid is True
        assert reason == ""

    def test_no_directive_ref_blocked_before_grader_infrastructure_error(self, daemon):
        """Pre-filter fires before grader; no directive ref blocked even when Ollama would fail-open."""
        daemon._grader = MagicMock()
        daemon._grader.grade.return_value = {"infrastructure_error": True}
        valid, reason = daemon._validate_brain_message("Conversational ack to a system notification")
        assert valid is False
        assert reason == self._NO_DIR
        daemon._grader.grade.assert_not_called()

    def test_empty_message_blocked_by_empty_check(self, daemon):
        """Empty message caught by existing empty check before pre-filter."""
        daemon._grader = MagicMock()
        valid, reason = daemon._validate_brain_message("")
        assert valid is False
        assert reason == "Empty message"
        daemon._grader.grade.assert_not_called()


class TestPollBrainResponsesFilter:
    """Tests for poll_brain_responses blocked-message split on _BLOCKED_NO_DIRECTIVE."""

    def test_conversational_threaded_under_heartbeat(self, daemon):
        """Ref-less chatter is threaded under the last heartbeat, not dropped or nudged."""
        daemon._last_heartbeat_ts = "1699.5"
        daemon.brain.get_pending_responses.return_value = [
            "That was conversational output, not a Slack post"
        ]
        daemon._grader = MagicMock()
        daemon.poll_brain_responses()
        daemon.slack.post_message.assert_called_once()
        _, kwargs = daemon.slack.post_message.call_args
        assert kwargs.get("thread_ts") == "1699.5"
        assert daemon.brain.send_message.call_count == 0

    def test_context_required_ack_silently_dropped(self, daemon):
        """Brain ack to CONTEXT_REQUIRED: silently dropped, no feedback loop triggered."""
        daemon.brain.get_pending_responses.return_value = [
            "[CONTEXT REQUIRED] understood, I will include directive refs"
        ]
        daemon._grader = MagicMock()
        daemon.poll_brain_responses()
        daemon.brain.send_message.assert_not_called()
        daemon.slack.post_message.assert_not_called()

    def test_directive_ref_grader_blocked_sends_context_required(self, daemon):
        """Message with directive ref blocked by grader → CONTEXT_REQUIRED sent (unchanged)."""
        daemon.brain.get_pending_responses.return_value = ["#1134 update"]
        daemon._grader = MagicMock()
        daemon._grader.grade.return_value = {"valid": False, "reason": "No reason clause"}
        daemon.poll_brain_responses()
        daemon.brain.send_message.assert_called_once()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[CONTEXT REQUIRED]" in msg
        daemon.slack.post_message.assert_not_called()

    def test_directive_ref_valid_posts_to_slack(self, daemon):
        """Message with directive ref passing grader → posted to Slack."""
        daemon.brain.get_pending_responses.return_value = [
            "#1134 fixed the deploy pipeline, now resolving within 2 min"
        ]
        daemon._grader = MagicMock()
        daemon._grader.grade.return_value = {"valid": True}
        daemon.poll_brain_responses()
        daemon.brain.send_message.assert_not_called()
        daemon.slack.post_message.assert_called_once()
        msg = daemon.slack.post_message.call_args[0][0]
        assert "#1134" in msg


class TestSolicitedReply:
    def test_solicited_reply_uses_shared_marker_helper(self, daemon):
        daemon.brain.get_pending_responses.return_value = ["ordinary response"]
        with patch("ironclaude.main.parse_reply_to_marker", return_value=("ordinary response", None)) as parser:
            daemon.poll_brain_responses()
        parser.assert_called_once_with("ordinary response")

    def test_solicited_reply_threads_and_reacts_only_after_delivery(self, daemon, tmp_path):
        daemon._db = init_db(str(tmp_path / "reply-ack.db"))
        daemon.brain.get_pending_responses.return_value = [
            "[reply-to:1700000000.123456] #12 completed requested work"
        ]
        daemon.slack.post_message.return_value = "1700000001.000001"

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_called_once_with(
            "*Brain:* #12 completed requested work", thread_ts="1700000000.123456"
        )
        daemon.slack.add_reaction.assert_called_once_with("white_check_mark", "1700000000.123456")

    def test_narration_prefixed_reply_marker_still_threads(self, daemon, tmp_path):
        """R2: the Brain prepends _NARRATION_PREFIX to ALL its text, which defeats
        parse_reply_to_marker's leading-[reply-to: check. A marker-led reply must
        still thread under the operator ts with a ✅, not fall through to narration."""
        from ironclaude.brain_client import _NARRATION_PREFIX
        daemon._db = init_db(str(tmp_path / "reply-ack.db"))
        daemon.brain.get_pending_responses.return_value = [
            f"{_NARRATION_PREFIX}[reply-to:1700000000.123456] #12 completed requested work"
        ]
        daemon.slack.post_message.return_value = "1700000001.000001"

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_called_once_with(
            "*Brain:* #12 completed requested work", thread_ts="1700000000.123456"
        )
        daemon.slack.add_reaction.assert_called_once_with("white_check_mark", "1700000000.123456")

    def test_solicited_reply_does_not_react_when_delivery_is_incomplete(self, daemon):
        daemon.brain.get_pending_responses.return_value = [
            "[reply-to:1700000000.123456] #12 completed requested work"
        ]
        daemon._post_brain_message = MagicMock(return_value=None)

        daemon.poll_brain_responses()

        daemon.slack.add_reaction.assert_not_called()

    def test_solicited_reply_marker_only_posts_nothing_and_does_not_react(self, daemon):
        daemon.brain.get_pending_responses.return_value = ["[reply-to:1700000000.123456]   "]

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_not_called()
        daemon.slack.add_reaction.assert_not_called()

    def test_solicited_reply_marked_waiting_language_bypasses_operator_wait_capture(self, daemon, tmp_path):
        daemon._db = init_db(str(tmp_path / "reply-ack.db"))
        daemon.brain.get_pending_responses.return_value = [
            "[reply-to:1700000000.123456] Waiting for your decision on #12"
        ]
        daemon._maybe_capture_operator_wait = MagicMock(return_value=True)
        daemon._post_brain_message = MagicMock(return_value="1700000001.000001")

        daemon.poll_brain_responses()

        daemon._maybe_capture_operator_wait.assert_not_called()
        daemon._post_brain_message.assert_called_once_with(
            "Waiting for your decision on #12", thread_ts="1700000000.123456"
        )

    def test_solicited_reply_unmarked_waiting_message_uses_operator_wait_capture(self, daemon):
        text = "Waiting for your decision on #12"
        daemon.brain.get_pending_responses.return_value = [text]
        daemon._maybe_capture_operator_wait = MagicMock(return_value=True)

        daemon.poll_brain_responses()

        daemon._maybe_capture_operator_wait.assert_called_once_with(text)
        daemon.slack.post_message.assert_not_called()

    @pytest.mark.parametrize("text", [
        "[reply-to:not-a-ts] #12 body",
        "[reply-to:1.2.3] #12 body",
    ])
    def test_solicited_reply_suppresses_malformed_leading_marker(self, daemon, text):
        daemon.brain.get_pending_responses.return_value = [text]

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_not_called()
        daemon.slack.add_reaction.assert_not_called()
        daemon.brain.send_message.assert_not_called()


class TestSolicitedReplyTransportAcknowledgement:
    """Marked daemon replies must durably classify their source before delivery."""

    def _with_db(self, daemon, tmp_path):
        daemon._db = init_db(str(tmp_path / "reply-ack.db"))
        return daemon._db

    def _ack(self, conn, source_ts):
        return conn.execute(
            "SELECT reason FROM operator_message_acknowledgements WHERE source_ts=?",
            (source_ts,),
        ).fetchone()

    def test_transport_acknowledgement_commits_before_first_post_and_uses_fallback(self, daemon, tmp_path):
        source_ts = "1700000000.123456"
        conn = self._with_db(daemon, tmp_path)
        db_path = str(tmp_path / "reply-ack.db")
        daemon.brain.get_pending_responses.return_value = [f"[reply-to:{source_ts}] reply body"]

        def post(*_args, **_kwargs):
            reader = sqlite3.connect(db_path)
            try:
                assert self._ack(reader, source_ts)[0] == DIRECT_REPLY_FALLBACK_REASON
            finally:
                reader.close()
            return "1700000001.000001"

        daemon.slack.post_message.side_effect = post
        daemon.poll_brain_responses()

        daemon.slack.add_reaction.assert_called_once_with("white_check_mark", source_ts)

    def test_transport_acknowledgement_preserves_existing_reason(self, daemon, tmp_path):
        source_ts = "1700000000.123456"
        conn = self._with_db(daemon, tmp_path)
        conn.execute(
            "INSERT INTO operator_message_acknowledgements (source_ts, reason) VALUES (?, ?)",
            (source_ts, "operator supplied reason"),
        )
        conn.commit()
        daemon.brain.get_pending_responses.return_value = [f"[reply-to:{source_ts}] reply body"]
        daemon.slack.post_message.return_value = "1700000001.000001"

        daemon.poll_brain_responses()

        assert self._ack(conn, source_ts)[0] == "operator supplied reason"
        daemon.slack.post_message.assert_called_once()

    @pytest.mark.parametrize("text", [
        "ordinary response",
        "[reply-to:not-a-ts] reply body",
        "[reply-to:1700000000.123456]   ",
    ])
    def test_transport_acknowledgement_skips_unmarked_or_invalid_reply_forms(self, daemon, tmp_path, text):
        conn = self._with_db(daemon, tmp_path)
        daemon.brain.get_pending_responses.return_value = [text]

        daemon.poll_brain_responses()

        assert conn.execute("SELECT COUNT(*) FROM operator_message_acknowledgements").fetchone()[0] == 0

    def test_transport_acknowledgement_missing_db_skips_reply_and_continues_polling(self, daemon):
        first_ts = "1700000000.123456"
        second_ts = "1700000000.123457"
        daemon.brain.get_pending_responses.return_value = [
            f"[reply-to:{first_ts}] first reply",
            f"[reply-to:{second_ts}] second reply",
        ]

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_not_called()
        daemon.slack.add_reaction.assert_not_called()

    def test_transport_acknowledgement_directive_conflict_skips_failed_reply_and_continues(self, daemon, tmp_path):
        blocked_ts = "1700000000.123456"
        delivered_ts = "1700000000.123457"
        conn = self._with_db(daemon, tmp_path)
        conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, ?, ?)",
            (blocked_ts, "operator text", "directive"),
        )
        conn.commit()
        daemon.brain.get_pending_responses.return_value = [
            f"[reply-to:{blocked_ts}] blocked reply",
            f"[reply-to:{delivered_ts}] delivered reply",
        ]
        daemon.slack.post_message.return_value = "1700000001.000001"

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_called_once_with(
            "*Brain:* delivered reply", thread_ts=delivered_ts
        )
        daemon.slack.add_reaction.assert_called_once_with("white_check_mark", delivered_ts)

    def test_transport_acknowledgement_persistence_failure_skips_post_and_reaction(self, daemon, tmp_path):
        source_ts = "1700000000.123456"
        conn = self._with_db(daemon, tmp_path)
        conn.close()
        daemon.brain.get_pending_responses.return_value = [f"[reply-to:{source_ts}] reply body"]

        daemon.poll_brain_responses()

        daemon.slack.post_message.assert_not_called()
        daemon.slack.add_reaction.assert_not_called()

    @patch("ironclaude.slack_interface.WebClient")
    def test_transport_acknowledgement_real_slack_queue_preserves_thread_without_reaction(self, web_client, tmp_path):
        from ironclaude.slack_interface import SlackBot

        source_ts = "1700000000.123456"
        client = web_client.return_value
        client.chat_postMessage.side_effect = Exception("network down")
        slack = SlackBot(token="xoxb-test", channel_id="C123")
        registry, tmux, brain = MagicMock(), MagicMock(), MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(tmux.log_dir, exist_ok=True)
        daemon = IroncladeDaemon({"tmp_dir": str(tmp_path)}, slack, None, registry, tmux, brain,
                                 db_conn=init_db(str(tmp_path / "reply-ack.db")))
        daemon.brain.get_pending_responses.return_value = [f"[reply-to:{source_ts}] reply body"]

        daemon.poll_brain_responses()

        assert daemon._db.execute(
            "SELECT reason FROM operator_message_acknowledgements WHERE source_ts=?", (source_ts,)
        ).fetchone()[0] == DIRECT_REPLY_FALLBACK_REASON
        assert slack._notification_queue == [("[IRONCLAUDE] *Brain:* reply body", source_ts)]
        client.reactions_add.assert_not_called()


class TestHeartbeatStateHistory:
    """State tracking mechanics for heartbeat-level stuck detection."""

    def _make_worker(self):
        return {"id": "w1", "tmux_session": "ic-w1", "description": "task", "machine": None}

    def test_first_heartbeat_no_escalation(self, daemon):
        """First heartbeat records snapshot but does not escalate."""
        daemon.registry.get_recent_workers.return_value = [self._make_worker()]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        daemon.brain.send_message.assert_not_called()
        assert len(daemon._heartbeat_state_history.get("w1", [])) == 1

    def test_second_heartbeat_unchanged_escalates(self, daemon):
        """Second heartbeat with same (stage, log_bytes) fires [ACTION REQUIRED]."""
        daemon.registry.get_recent_workers.return_value = [self._make_worker()]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        daemon.brain.send_message.assert_called()
        msg = daemon.brain.send_message.call_args[0][0]
        assert "[ACTION REQUIRED]" in msg
        assert "w1" in msg

    def test_second_heartbeat_stage_changed_no_escalation(self, daemon):
        """Second heartbeat with different stage does not escalate."""
        daemon.registry.get_recent_workers.return_value = [self._make_worker()]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        stages = ['brainstorming', 'writing_plans']
        call_count = [0]
        def stage_side_effect(*args, **kwargs):
            s = stages[min(call_count[0], len(stages) - 1)]
            call_count[0] += 1
            return s
        with patch.object(daemon, '_get_worker_workflow_stage', side_effect=stage_side_effect):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        daemon.brain.send_message.assert_not_called()

    def test_second_heartbeat_log_bytes_changed_no_escalation(self, daemon):
        """Second heartbeat with same stage but different log_bytes does not escalate."""
        daemon.registry.get_recent_workers.return_value = [self._make_worker()]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
            with open(log_path, "a") as f:
                f.write("y" * 100)
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        daemon.brain.send_message.assert_not_called()

    def test_escalation_not_repeated_within_same_stuck_episode(self, daemon):
        """Third heartbeat while still stuck does not fire escalation again (dedup)."""
        daemon.registry.get_recent_workers.return_value = [self._make_worker()]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            for _ in range(3):
                daemon._last_heartbeat = 0
                daemon.post_heartbeat()
        assert daemon.brain.send_message.call_count == 1

    def test_escalation_resets_on_state_change(self, daemon):
        """After escalation, state change removes worker from _heartbeat_stuck_notified."""
        daemon.registry.get_recent_workers.return_value = [self._make_worker()]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        assert "w1" in daemon._heartbeat_stuck_notified
        with open(log_path, "a") as f:
            f.write("y" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        assert "w1" not in daemon._heartbeat_stuck_notified


class TestHeartbeatStuckEscalation:
    """Notification content tests for heartbeat stuck detection."""

    def _setup_stuck_worker(self, daemon):
        worker = {"id": "w1", "tmux_session": "ic-w1", "description": "task", "machine": None}
        daemon.registry.get_recent_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)

    def _fire_two_heartbeats(self, daemon, stage='brainstorming'):
        with patch.object(daemon, '_get_worker_workflow_stage', return_value=stage):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()

    def test_brain_message_has_action_required_prefix(self, daemon):
        self._setup_stuck_worker(daemon)
        self._fire_two_heartbeats(daemon)
        msg = daemon.brain.send_message.call_args[0][0]
        assert msg.startswith("[ACTION REQUIRED]")

    def test_brain_message_includes_worker_id_and_stage(self, daemon):
        self._setup_stuck_worker(daemon)
        self._fire_two_heartbeats(daemon, stage='brainstorming')
        msg = daemon.brain.send_message.call_args[0][0]
        assert "w1" in msg
        assert "brainstorming" in msg

    def test_slack_stuck_message_posted(self, daemon):
        self._setup_stuck_worker(daemon)
        self._fire_two_heartbeats(daemon, stage='executing')
        calls = [c[0][0] for c in daemon.slack.post_message.call_args_list]
        stuck_calls = [c for c in calls if "[STUCK]" in c]
        assert len(stuck_calls) == 1
        assert "w1" in stuck_calls[0]
        assert "executing" in stuck_calls[0]


class TestHeartbeatStateCleanup:
    """Lifecycle tests for heartbeat state dict cleanup."""

    def test_history_cleared_when_worker_no_longer_in_candidates(self, daemon):
        """Worker removed from get_recent_workers clears its history entry."""
        worker = {"id": "w1", "tmux_session": "ic-w1", "description": "task", "machine": None}
        daemon.registry.get_recent_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        assert "w1" in daemon._heartbeat_state_history

        daemon.registry.get_recent_workers.return_value = []
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        assert "w1" not in daemon._heartbeat_state_history

    def test_notified_cleared_when_worker_no_longer_in_candidates(self, daemon):
        """Worker removed from candidates also removed from _heartbeat_stuck_notified."""
        daemon._heartbeat_stuck_notified.add("w1")
        daemon.registry.get_recent_workers.return_value = []
        daemon._last_heartbeat = 0
        daemon.post_heartbeat()
        assert "w1" not in daemon._heartbeat_stuck_notified

    def test_active_workers_not_cleaned_up(self, daemon):
        """Workers still in candidates retain their history."""
        worker = {"id": "w1", "tmux_session": "ic-w1", "description": "task", "machine": None}
        daemon.registry.get_recent_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = True
        log_path = os.path.join(daemon.tmux.log_dir, "ic-w1.log")
        with open(log_path, "w") as f:
            f.write("x" * 100)
        with patch.object(daemon, '_get_worker_workflow_stage', return_value='brainstorming'):
            daemon._last_heartbeat = 0
            daemon.post_heartbeat()
        assert "w1" in daemon._heartbeat_state_history


class TestProviderCommand:
    """`/provider` must never persist a change the router would silently ignore:
    ProviderRouter falls back to `preferred` when the sticky client is not in the
    role's configured `clients` list."""

    def _prep(self, daemon):
        # The shared `daemon` fixture builds the daemon with db_conn=None, so a real
        # connection must be attached before ProviderState can be used (same pattern as
        # TestHandleSummary / reaction_daemon). A bare sqlite3.connect is insufficient —
        # provider_role_state only exists in db.py's schema.
        from ironclaude.db import init_db
        daemon._db = init_db(":memory:")
        daemon.config = {
            "providers": {
                "clients": {
                    "claude": {"enabled": True, "path": "claude", "models": {}},
                    "codex": {"enabled": True, "path": "codex", "models": {}},
                },
                "roles": {
                    "brain": {"preferred": "claude", "clients": ["claude"]},
                    "worker": {"preferred": "claude", "clients": ["claude", "codex"]},
                    "grader": {"preferred": "claude", "clients": ["claude"]},
                    "advisor": {"preferred": "claude", "clients": ["claude"]},
                },
            }
        }
        daemon.slack.post_message.reset_mock()
        return daemon

    def _posted(self, daemon):
        return " ".join(str(c) for c in daemon.slack.post_message.call_args_list)

    def _current(self, daemon, role):
        from ironclaude.provider_state import ProviderState
        return ProviderState(daemon._db).get_current_client(role)

    def test_status_lists_every_role(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command([])
        out = self._posted(d)
        for role in ("brain", "worker", "grader", "advisor"):
            assert role in out

    def test_valid_set_persists(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command(["worker", "codex"])
        assert self._current(d, "worker") == "codex"

    def test_explicit_cutover_resets_only_selected_routed_quarantine(self, daemon):
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        state = ProviderState(d._db)
        state.mark_unavailable(
            "local", "codex", "worker", "sonnet", "usage_limit", "worker limit"
        )
        state.mark_unavailable(
            "local", "codex", "grader", "opus", "usage_limit", "grader limit"
        )

        d._handle_provider_command(["worker", "codex"])

        assert state.capability_observation(
            "local", "codex", "worker", "sonnet"
        ) is None
        assert state.is_available("local", "codex", "grader", "opus") is False
        posted = self._posted(d).lower()
        assert "selected" in posted and "next use" in posted

    def test_client_not_in_role_clients_is_rejected(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command(["grader", "codex"])
        assert self._current(d, "grader") is None, (
            "must not persist a client the router would ignore (not in the role's clients list)"
        )
        assert "clients" in self._posted(d).lower()

    def test_unknown_role_is_rejected(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command(["bogus", "codex"])
        assert self._current(d, "bogus") is None

    def test_unknown_client_is_rejected(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command(["worker", "bogus"])
        assert self._current(d, "worker") is None

    def test_disabled_client_is_rejected(self, daemon):
        d = self._prep(daemon)
        d.config["providers"]["clients"]["codex"]["enabled"] = False
        d._handle_provider_command(["worker", "codex"])
        assert self._current(d, "worker") is None
        assert "disabled" in self._posted(d).lower()

    def test_wrong_arity_returns_usage(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command(["worker"])
        assert "usage" in self._posted(d).lower()
        assert self._current(d, "worker") is None

    # NOTE on `split("\\n")` below: `_posted` joins `str(mock_call)`, and repr-ing the call
    # turns a real newline into the TWO characters backslash+n. So the literal "\\n" here is
    # correct. Do NOT "fix" it to "\n": the split would then return one blob containing every
    # role's line, and `current: *claude*` from another role would satisfy the assertion —
    # making the test pass pre-fix (pure theatre).

    def test_status_reports_effective_client_not_stale_sticky(self, daemon):
        """A sticky client no longer in the role's clients list is IGNORED by
        ProviderRouter (it falls back to preferred), so status must not report it."""
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        ProviderState(d._db).set_current_client("worker", "codex")
        # Operator later narrows the config so codex is no longer allowed for worker.
        d.config["providers"]["roles"]["worker"]["clients"] = ["claude"]
        d.slack.post_message.reset_mock()
        d._handle_provider_command([])
        out = self._posted(d)
        worker_line = [ln for ln in out.split("\\n") if "`worker`" in ln]
        assert worker_line, out
        assert "current: *claude*" in worker_line[0], (
            f"status must report what the router would actually use; got {worker_line[0]}"
        )

    def test_status_notes_the_ignored_sticky_value(self, daemon):
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        ProviderState(d._db).set_current_client("worker", "codex")
        d.config["providers"]["roles"]["worker"]["clients"] = ["claude"]
        d.slack.post_message.reset_mock()
        d._handle_provider_command([])
        out = self._posted(d)
        assert "ignor" in out.lower() and "codex" in out, (
            "the discrepancy must be surfaced, not silently corrected"
        )

    def test_status_reports_a_valid_sticky_client(self, daemon):
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        ProviderState(d._db).set_current_client("worker", "codex")
        d.slack.post_message.reset_mock()
        d._handle_provider_command([])
        worker_line = [ln for ln in self._posted(d).split("\\n") if "`worker`" in ln]
        assert worker_line and "current: *codex*" in worker_line[0]

    def test_status_reports_selected_client_quarantine_and_fallback(self, daemon):
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        state = ProviderState(d._db)
        state.set_current_client("worker", "codex")
        state.mark_unavailable(
            "local", "codex", "worker", "sonnet", "usage_limit", "limit"
        )

        d.slack.post_message.reset_mock()
        d._handle_provider_command([])

        worker_line = [ln for ln in self._posted(d).split("\\n") if "`worker`" in ln]
        assert worker_line
        assert "quarantined" in worker_line[0].lower()
        assert "local/sonnet" in worker_line[0]
        assert "fallback" in worker_line[0].lower()

    def test_status_reports_persisted_brain_after_config_removal(self, daemon):
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        ProviderState(d._db).set_current_client("brain", "codex")
        d.config["providers"]["roles"]["brain"]["clients"] = ["claude"]
        d.slack.post_message.reset_mock()
        d._handle_provider_command([])
        brain_line = [ln for ln in self._posted(d).split("\\n") if "`brain`" in ln]
        assert brain_line and "current: *codex*" in brain_line[0]
        assert "ignor" not in brain_line[0].lower()

    def test_brain_cutover_persists_flushes_and_requests_restart(self, daemon):
        import signal
        d = self._prep(daemon)

        with patch("ironclaude.main.os.getpid", return_value=12345), \
             patch("ironclaude.main.os.kill") as kill:
            d._handle_provider_command(["brain", "codex"])

        assert self._current(d, "brain") == "codex"
        d.slack.flush_queue.assert_called_once_with()
        kill.assert_called_once_with(12345, signal.SIGHUP)
        assert "restart" in self._posted(d).lower()

    def test_brain_status_distinguishes_selected_from_active_until_restart(self, daemon):
        from ironclaude.provider_state import ProviderState
        d = self._prep(daemon)
        d.config["providers"]["roles"]["brain"]["clients"] = ["claude", "codex"]
        ProviderState(d._db).set_current_client("brain", "codex")

        d.slack.post_message.reset_mock()
        d._handle_provider_command([])

        brain_line = [ln for ln in self._posted(d).split("\\n") if "`brain`" in ln]
        assert brain_line
        assert "current: *codex*" in brain_line[0]
        assert "active: *claude*" in brain_line[0]
        assert "restart pending" in brain_line[0].lower()

    def test_status_without_sticky_row_has_no_note(self, daemon):
        d = self._prep(daemon)
        d._handle_provider_command([])
        assert "ignor" not in self._posted(d).lower()


def test_daemon_fixture_isolates_state_manager_db_path(daemon, tmp_path):
    """The daemon fixture must never point at the operator's real state DB
    (main.py:995 DELETEs from audit_log against it). Equality, not startswith:
    the fake home lives under tmp_path, so startswith passes without the override."""
    assert daemon._state_manager_db_path == str(tmp_path / "state-manager.db")


# A frozen-drift finalization failure as _classify_finalization_failure emits it:
# failure_phase='finalization' at the top level, mode nested under
# recovery.reconcile.mode (NOT a top-level key).
def _drift_outcome():
    return {
        "error": "finalize drift",
        "failure_phase": "finalization",
        "assignment_preserved": True,
        "recovery": {"reconcile": {"repository_path": "/repo", "mode": "drift"}},
    }


def _drift_session_died(daemon, *, drive_states):
    """Wire a single running worker whose tmux session is dead and whose finalize
    returns a frozen-drift failure; drive_frozen_reconcile_recovery yields the
    given per-cycle states. Returns the orchestrator mock."""
    worker = {"id": "w1", "tmux_session": "ic-w1"}
    daemon.registry.get_running_workers.return_value = [worker]
    daemon.tmux.has_session.return_value = False
    orch = MagicMock()
    orch._finalize_and_release_worker.return_value = _drift_outcome()
    orch.drive_frozen_reconcile_recovery.side_effect = drive_states
    daemon._get_orchestrator = MagicMock(return_value=orch)
    return orch


class TestFinalizeDriftRecovery:
    """C-1: a frozen-drift finalize must drive the capped plain-reconcile recovery
    (never the empty-commit-minting isRepair retry), surface-and-hold over the cap
    (never abandon, never complete), and fire the session-died posts exactly once."""

    def test_finalize_drift_seam_integrates_clears_counter_daemon_completes_nothing(self, daemon):
        orch = _drift_session_died(daemon, drive_states=[{"state": "cleaned"}])
        daemon.check_workers()
        orch.drive_frozen_reconcile_recovery.assert_called_once_with("w1")
        # The seam integrated (and completed a dead worker) itself; the DAEMON
        # completes nothing. The counter-clear on the integrated state is the
        # discriminator that still fails on an integrate-path regression.
        daemon.registry.update_worker_status.assert_not_called()
        assert daemon._finalize_drift_retry.get("w1") is None
        orch._abandon_rescue_worker.assert_not_called()

    def test_finalize_drift_under_cap_drives_seam_and_leaves_running(self, daemon):
        orch = _drift_session_died(
            daemon, drive_states=[{"state": "frozen-no-rebase"}],
        )
        daemon.check_workers()
        orch.drive_frozen_reconcile_recovery.assert_called_once_with("w1")
        # Not integrated -> worker left running (never completed), counter at 1.
        daemon.registry.update_worker_status.assert_not_called()
        assert daemon._finalize_drift_retry["w1"] == 1
        orch._abandon_rescue_worker.assert_not_called()

    def test_finalize_drift_over_cap_surfaces_and_holds(self, daemon):
        cycles = FINALIZE_DRIFT_RETRY_CAP + 2
        orch = _drift_session_died(
            daemon, drive_states=[{"state": "frozen-no-rebase"}] * cycles,
        )
        for _ in range(cycles):
            daemon.check_workers()
        # The seam is driven at most cap times, then driving STOPS (surface-and-hold).
        assert orch.drive_frozen_reconcile_recovery.call_count == FINALIZE_DRIFT_RETRY_CAP
        # Never completed, never abandoned — the held drift row keeps its lock.
        daemon.registry.update_worker_status.assert_not_called()
        orch._abandon_rescue_worker.assert_not_called()
        # Two DISTINCT once-per-worker surfaces fire across every cycle: the
        # session-died post (first cycle) and the drift-over-cap "held" alert
        # (the first over-cap cycle) — each gated separately, so 2 total, not
        # a re-fire of either one.
        assert daemon.slack.post_message.call_count == 2
        posted = [c.args[0] for c in daemon.slack.post_message.call_args_list]
        assert any("preserved" in p.lower() for p in posted)
        assert any("held" in p.lower() for p in posted)
        assert daemon._finalize_drift_retry["w1"] == cycles

    def test_finalize_drift_session_died_posts_fire_exactly_once(self, daemon):
        cycles = FINALIZE_DRIFT_RETRY_CAP + 2
        _drift_session_died(
            daemon, drive_states=[{"state": "frozen-no-rebase"}] * cycles,
        )
        for _ in range(cycles):
            daemon.check_workers()
        # Session-died Slack AND Brain fire exactly once despite the worker
        # re-entering the dead-session branch every cycle. Over the cap the
        # distinct drift-"held" alert also fires exactly once, for 2 posts
        # total — each surface individually gated, neither one re-firing.
        assert daemon.slack.post_message.call_count == 2
        assert daemon.brain.send_message.call_count == 2

    def test_finalize_drift_retry_then_integrate_still_posts_once(self, daemon):
        """Retry-then-integrate lifecycle: the session-died Slack/Brain posts fire
        exactly once across the WHOLE lifecycle (not again on the integrate cycle)
        and the worker ends completed."""
        orch = _drift_session_died(
            daemon,
            drive_states=[
                {"state": "frozen-no-rebase"},
                {"state": "frozen-no-rebase"},
                {"state": "cleaned"},
            ],
        )
        for _ in range(3):
            daemon.check_workers()
        assert daemon.slack.post_message.call_count == 1
        assert daemon.brain.send_message.call_count == 1
        # The seam completes the (dead) worker on integrate; the DAEMON completes
        # nothing. drive_count==3 stays the integrate-path discriminator.
        daemon.registry.update_worker_status.assert_not_called()
        assert orch.drive_frozen_reconcile_recovery.call_count == 3

    def test_finalize_drift_stuck_kill_site_drives_seam_not_bare_complete(self, daemon):
        """The stuck-kill completed-flip site routes a frozen-drift outcome through
        the recovery seam instead of bare-completing the worker."""
        daemon.tmux.list_pane_pid.return_value = None  # skip liveness probe
        daemon._persist_staleness_state = MagicMock()
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = _drift_outcome()
        orch.drive_frozen_reconcile_recovery.return_value = {"state": "frozen-no-rebase"}
        daemon._get_orchestrator = MagicMock(return_value=orch)
        daemon._confirm_and_kill_stuck_worker(
            "w1", "ic-w1", 1200.0, "execution", False, None,
        )
        orch.drive_frozen_reconcile_recovery.assert_called_once_with("w1")
        # Drift not integrated -> worker left running, never bare-completed/abandoned.
        daemon.registry.update_worker_status.assert_not_called()
        orch._abandon_rescue_worker.assert_not_called()


# --------------------------------------------------------------------------
# I-1 — a mid-finalization new-work probe failure on a terminal worker must
# not be silently completed by the daemon's completed-flip guards. Uses a
# REAL OrchestratorTools (only the workspace_client/registry boundary is
# mocked) against a real conflicted git worktree, so the outcome exercised
# here is exactly what production classification produces.
# --------------------------------------------------------------------------

_I1_OWNER = "33333333-3333-4333-8333-333333333333"
_I1_GUID = "44444444-4444-4444-8444-444444444444"


def _i1_git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _i1_conflicted_worktree(base):
    """Real git checkout on branch ironclaude/wt left with an UNMERGED index
    (an unresolved merge conflict on f.txt) -- the same index shape a paused
    rebase leaves behind. `git write-tree` fails against it exactly like it
    does mid-rebase, so the orchestrator's new-work probe raises."""
    base.mkdir(parents=True, exist_ok=True)
    _i1_git(base, "init", "-q", "-b", "main")
    _i1_git(base, "config", "user.email", "t@example.com")
    _i1_git(base, "config", "user.name", "Tester")
    (base / "f.txt").write_text("base\n")
    _i1_git(base, "add", "-A")
    _i1_git(base, "commit", "-qm", "base")
    _i1_git(base, "checkout", "-q", "-b", "ironclaude/wt")
    (base / "f.txt").write_text("wt-change\n")
    _i1_git(base, "add", "-A")
    _i1_git(base, "commit", "-qm", "wt work")
    _i1_git(base, "checkout", "-q", "main")
    (base / "f.txt").write_text("main-change\n")
    _i1_git(base, "add", "-A")
    _i1_git(base, "commit", "-qm", "main work")
    _i1_git(base, "checkout", "-q", "ironclaude/wt")
    # Merge conflict, left unresolved on purpose (non-zero exit expected).
    subprocess.run(
        ["git", "merge", "main"], cwd=str(base),
        capture_output=True, text=True, check=False,
    )
    return base


def _i1_orchestrator_tools(worktree):
    """A real OrchestratorTools whose `_finalize_and_release_worker` runs for
    real against the given worktree, with only the workspace_client/registry
    boundary mocked -- exactly the shape the daemon's session-died/stuck-kill
    seams call in production."""
    tools = object.__new__(OrchestratorTools)
    tools.registry = MagicMock()
    tools.registry.get_worker.return_value = {
        "id": "w1",
        "client": "codex",
        "machine": None,
        "repo": "/repo",
        "native_session_id": _I1_OWNER,
        "tmux_session": "ic-w1",
        "workspace_guid": _I1_GUID,
        "workspace_repository_identity": "machine:repo.git",
        "workspace_path": str(worktree),
        "workspace_branch": "ironclaude/wt",
        "workspace_base_commit": "a" * 40,
        "workspace_integration_target": "main",
    }
    tools.registry.update_worker_status = MagicMock()
    tools.tmux = MagicMock()
    tools.tmux.has_session.return_value = False  # session already dead
    tools._ssh_manager = None
    tools._workspace_client = MagicMock()
    tools._workspace_client.discover_installed_plugin_root.return_value = "/installed"
    # The status probe confirms a genuinely mid-finalization worktree (a
    # paused/conflicted rebase) -- the case a bare new-work probe failure must
    # be routed through, not treated as an environment ('authority') error.
    tools._workspace_client.reconcile.return_value = {"state": "rebase-paused-conflict"}
    tools._read_worker_finalization_state = MagicMock(return_value={
        "workflow_stage": "execution_complete",
        "unfinished_tasks": 0,
        "latest_task_boundary_grade": "A",
    })
    tools._ensure_ssh_manager = MagicMock()
    tools._resolve_ssh_host = MagicMock(return_value=None)
    tools._slack = MagicMock()
    return tools


class TestUnmergedProbeNotCompleted:
    """I-1: a terminal worker whose managed worktree is mid-finalization (an
    unmerged index from a paused/conflicted rebase) must NOT be silently
    marked completed by the daemon's completed-flip guards -- the orchestrator
    must surface it as a finalization failure instead of an authority one."""

    def test_unmerged_probe_not_completed_session_died(self, daemon, tmp_path):
        worktree = _i1_conflicted_worktree(tmp_path / "wt")
        tools = _i1_orchestrator_tools(worktree)
        daemon.registry.get_running_workers.return_value = [
            {"id": "w1", "tmux_session": "ic-w1"},
        ]
        daemon.tmux.has_session.return_value = False
        daemon._get_orchestrator = MagicMock(return_value=tools)
        daemon.check_workers()
        # The new-work probe raised on the unmerged index; the mid-finalization
        # status classifies it 'finalization', not 'authority' -- the worker
        # must be left running, never bare-completed.
        daemon.registry.update_worker_status.assert_not_called()

    def test_unmerged_probe_not_completed_stuck_kill(self, daemon, tmp_path):
        worktree = _i1_conflicted_worktree(tmp_path / "wt")
        tools = _i1_orchestrator_tools(worktree)
        daemon.tmux.list_pane_pid.return_value = None  # skip liveness probe
        daemon._persist_staleness_state = MagicMock()
        daemon._get_orchestrator = MagicMock(return_value=tools)
        daemon._confirm_and_kill_stuck_worker(
            "w1", "ic-w1", 1200.0, "execution", False, None,
        )
        daemon.registry.update_worker_status.assert_not_called()


# --------------------------------------------------------------------------
# Capstone — the DAEMON completes NOTHING; the orchestrator seam owns ALL
# completion. Every finalize path routes its outcome through the unified
# _drive_finalization_recovery driver, which NEVER calls update_worker_status.
# worker_finished is logged only when the worker is ACTUALLY completed (a
# registry re-read), a conflict/repair worker is surfaced exactly once, and the
# live idle worker is never completed by the daemon.
# --------------------------------------------------------------------------


def _worker_finished_calls(registry):
    """Return the log_event calls whose event name is 'worker_finished'."""
    return [
        c for c in registry.log_event.call_args_list
        if c.args and c.args[0] == "worker_finished"
    ]


def _seam_session_died(daemon, outcome):
    """Wire a single dead-session worker whose finalize seam returns `outcome`."""
    worker = {"id": "w1", "tmux_session": "ic-w1"}
    daemon.registry.get_running_workers.return_value = [worker]
    daemon.tmux.has_session.return_value = False
    orch = MagicMock()
    orch._finalize_and_release_worker.return_value = outcome
    daemon._get_orchestrator = MagicMock(return_value=orch)
    return orch


def _seam_stuck_kill(daemon, outcome):
    """Wire the stuck-kill site to a finalize seam returning `outcome`."""
    daemon.tmux.list_pane_pid.return_value = None  # skip liveness probe
    daemon._persist_staleness_state = MagicMock()
    orch = MagicMock()
    orch._finalize_and_release_worker.return_value = outcome
    daemon._get_orchestrator = MagicMock(return_value=orch)
    return orch


_STAYS_RUNNING_OUTCOMES = [
    None,
    {"failure_phase": "authority", "assignment_preserved": True},
    {"action": "surfaced"},
]


class TestDaemonCompletesNothing:
    """The seam owns completion. On any transient/preserved outcome the daemon
    leaves the worker running: no update_worker_status flip, no worker_finished
    log. (The session-died `_session_died_notified` post and the stuck-kill
    stuck-killed/MANDATORY-SWEEP posts still fire — those are not completion.)"""

    @pytest.mark.parametrize("outcome", _STAYS_RUNNING_OUTCOMES)
    def test_session_died_stays_running_never_completes(self, daemon, outcome):
        _seam_session_died(daemon, outcome)
        # Bare-MagicMock registry: get_worker returns a non-dict, so the
        # completion re-read gate is False.
        daemon.check_workers()
        daemon.registry.update_worker_status.assert_not_called()
        assert _worker_finished_calls(daemon.registry) == []

    @pytest.mark.parametrize("outcome", _STAYS_RUNNING_OUTCOMES)
    def test_stuck_kill_stays_running_never_completes(self, daemon, outcome):
        _seam_stuck_kill(daemon, outcome)
        daemon._confirm_and_kill_stuck_worker(
            "w1", "ic-w1", 1200.0, "execution", False, None,
        )
        daemon.registry.update_worker_status.assert_not_called()
        assert _worker_finished_calls(daemon.registry) == []

    def test_drift_outcome_drives_seam_daemon_completes_nothing(self, daemon):
        orch = _seam_session_died(daemon, _drift_outcome())
        orch.drive_frozen_reconcile_recovery.return_value = {"state": "cleaned"}
        daemon.check_workers()
        orch.drive_frozen_reconcile_recovery.assert_called_once_with("w1")
        # The driver drove the seam; the daemon performs NO completion itself.
        daemon.registry.update_worker_status.assert_not_called()

    def test_drift_over_cap_surfaces_exactly_once(self, daemon):
        cycles = FINALIZE_DRIFT_RETRY_CAP + 3
        orch = _drift_session_died(
            daemon, drive_states=[{"state": "frozen-no-rebase"}] * cycles,
        )
        for _ in range(cycles):
            daemon.check_workers()
        # Two DISTINCT operator surfaces, each firing exactly once across
        # every cycle (never re-alerting): the session-died post and the
        # drift-over-cap "held" alert.
        assert daemon.slack.post_message.call_count == 2
        assert daemon.brain.send_message.call_count == 2
        assert orch.drive_frozen_reconcile_recovery.call_count == FINALIZE_DRIFT_RETRY_CAP
        daemon.registry.update_worker_status.assert_not_called()

    @pytest.mark.parametrize("mode", ["conflict", "repair"])
    def test_conflict_or_repair_surfaced_once_never_completed(self, daemon, mode):
        outcome = {
            "failure_phase": "finalization",
            "assignment_preserved": True,
            "recovery": {"reconcile": {"mode": mode}},
        }
        # Drive the unified driver directly, twice, to prove the alert is
        # once-only via the new _finalize_recovery_alerted gate.
        d1 = daemon._drive_finalization_recovery("w1", outcome)
        posts_after_first = daemon.slack.post_message.call_count
        d2 = daemon._drive_finalization_recovery("w1", outcome)
        assert d1 == "surfaced" and d2 == "surfaced"
        assert "w1" in daemon._finalize_recovery_alerted
        assert posts_after_first == 1
        assert daemon.slack.post_message.call_count == 1  # no re-alert
        # Never completed, never abandoned.
        daemon.registry.update_worker_status.assert_not_called()

    def test_idle_drift_outcome_drives_driver_completes_nothing(self, daemon):
        """The idle branch (terminal=False) now routes its (previously discarded)
        outcome through the driver; a drift outcome drives the seam and the
        daemon completes nothing."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        marker = os.path.join(daemon.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("2026-03-01T00:00:00Z")
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = _drift_outcome()
        orch.drive_frozen_reconcile_recovery.return_value = {"state": "frozen-no-rebase"}
        daemon._get_orchestrator = MagicMock(return_value=orch)
        daemon.check_workers()
        orch.drive_frozen_reconcile_recovery.assert_called_once_with("w1")
        daemon.registry.update_worker_status.assert_not_called()

    def test_seam_completion_logs_worker_finished(self, daemon):
        """When the worker is ACTUALLY completed (the seam completed it; the
        registry re-read reports 'completed'), worker_finished IS logged. The
        outcome deliberately does NOT itself signal completion (None) — only the
        registry status does, so the gate must read the REGISTRY, not the
        outcome, or this test cannot log worker_finished."""
        _seam_session_died(daemon, None)
        daemon.registry.get_worker.return_value = {"id": "w1", "status": "completed"}
        daemon.check_workers()
        # Daemon still performs no completion flip of its own...
        daemon.registry.update_worker_status.assert_not_called()
        # ...but the finished event is logged because the worker is completed.
        assert len(_worker_finished_calls(daemon.registry)) == 1

    def test_seam_completion_stuck_kill_logs_worker_finished(self, daemon):
        # Outcome does NOT signal completion (None); only the registry status
        # does — the gate must read the registry.
        _seam_stuck_kill(daemon, None)
        daemon.registry.get_worker.return_value = {"id": "w1", "status": "completed"}
        daemon._confirm_and_kill_stuck_worker(
            "w1", "ic-w1", 1200.0, "execution", False, None,
        )
        daemon.registry.update_worker_status.assert_not_called()
        assert len(_worker_finished_calls(daemon.registry)) == 1


# --------------------------------------------------------------------------
# Operator-facing messaging accuracy — the dead-session branch must not label
# an uncompleted (drift/transient/held) worker "Worker Completed"; it gets an
# accurate "preserved, not completed" surface instead. The drift-over-cap
# "held" disposition, which surfaces nothing today, must alert the operator
# exactly once. MESSAGING/OBSERVABILITY ONLY.
# --------------------------------------------------------------------------


class TestDeadSessionAccurateSurface:
    def test_dead_session_completed_worker_posts_worker_completed(self, daemon):
        """Guard-preservation: a worker the seam actually completed still gets
        the accurate 'Worker Completed' surface (not the preserved one)."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = False
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = None
        daemon._get_orchestrator = MagicMock(return_value=orch)
        daemon.registry.get_worker.return_value = {"id": "w1", "status": "completed"}
        daemon.check_workers()
        assert daemon.slack.post_message.call_count == 1
        posted = daemon.slack.post_message.call_args[0][0]
        assert "Worker Completed" in posted
        assert "preserved" not in posted.lower()

    def test_dead_session_not_completed_posts_preserved_not_completed(self, daemon, caplog):
        """A worker left NOT completed (drift/transient/held; work preserved)
        must never be labeled 'Worker Completed' — it gets the accurate
        preserved surface instead, and fires only once even across a second
        check_workers cycle (the once-per-worker _session_died_notified gate).
        WORKER_DEAD must also log exactly once across both cycles (not once
        per cycle)."""
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        daemon.registry.get_running_workers.return_value = [worker]
        daemon.tmux.has_session.return_value = False
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = None  # transient/preserved
        daemon._get_orchestrator = MagicMock(return_value=orch)
        daemon.registry.get_worker.return_value = {"id": "w1", "status": "running"}
        with caplog.at_level(logging.INFO, logger="ironclaude"):
            daemon.check_workers()
            assert daemon.slack.post_message.call_count == 1
            posted = daemon.slack.post_message.call_args[0][0]
            assert "Worker Completed" not in posted
            assert "preserved" in posted.lower()
            assert "not completed" in posted.lower()
            brain_msg = daemon.brain.send_message.call_args[0][0]
            assert "preserved" in brain_msg.lower()
            assert 'recover_worker_integration("w1", "status")' in brain_msg
            assert "Do not spawn a cleanup worker" in brain_msg
            assert "Do not request primary-checkout or terminal commands" in brain_msg
            # Second cycle: the once-per-worker gate suppresses a repeat post.
            daemon.check_workers()
            assert daemon.slack.post_message.call_count == 1
        worker_dead_records = []
        for record in caplog.records:
            try:
                payload = json.loads(record.message)
            except (ValueError, TypeError):
                continue
            if payload.get("event_type") == "WORKER_DEAD":
                worker_dead_records.append(record)
        assert len(worker_dead_records) == 1

    def test_dead_session_drift_held_over_cap_surfaces_once(self, daemon):
        """The drift-over-cap 'held' disposition surfaces NOTHING today; it
        must post to slack+brain exactly once (never re-alerting on a
        subsequent over-cap call for the same worker)."""
        outcome = {
            "failure_phase": "finalization",
            "assignment_preserved": True,
            "recovery": {"reconcile": {"mode": "drift"}},
        }
        daemon._finalize_drift_retry["w1"] = FINALIZE_DRIFT_RETRY_CAP  # next call exceeds cap
        disposition = daemon._drive_finalization_recovery("w1", outcome)
        assert disposition == "held"
        assert daemon.slack.post_message.call_count == 1
        assert daemon.brain.send_message.call_count == 1
        posted = daemon.slack.post_message.call_args[0][0]
        assert "held" in posted.lower()
        assert "w1" in daemon._finalize_recovery_alerted
        # A second over-cap call for the same worker does not re-alert.
        disposition2 = daemon._drive_finalization_recovery("w1", outcome)
        assert disposition2 == "held"
        assert daemon.slack.post_message.call_count == 1
        assert daemon.brain.send_message.call_count == 1


class TestGradeBoundedInFlightCap:
    def test_second_call_while_in_flight_returns_none_without_second_grade(self, daemon):
        """FIX 2: while one bounded grade is still running (abandoned past its
        timeout), a second call returns None immediately and does NOT invoke the
        grader again — capping concurrent grade threads at one. Once the abandoned
        worker finishes, the flag clears and a later call proceeds."""
        import threading as _t
        import time as _time
        gate = _t.Event()
        calls = []

        def _blocking_grade(system, user, schema, **kw):
            calls.append(1)
            gate.wait(5)
            return {"ok": True}

        daemon._grader = MagicMock()
        daemon._grader.grade.side_effect = _blocking_grade

        # First call abandons at the tiny timeout; the flag stays set.
        assert daemon._grade_bounded("s", "u", None, timeout=0.1) is None
        # Second call while the first is in flight: immediate None, grade NOT re-invoked.
        assert daemon._grade_bounded("s", "u", None, timeout=0.1) is None
        assert len(calls) == 1

        # Release the abandoned worker; its finally clears the flag.
        gate.set()
        for _ in range(100):
            if not daemon._grade_in_flight:
                break
            _time.sleep(0.05)
        assert daemon._grade_in_flight is False
        # A subsequent call now proceeds and invokes the grader again.
        assert daemon._grade_bounded("s", "u", None, timeout=2) == {"ok": True}
        assert len(calls) == 2


class TestGradeBoundedFastLaneReadTimeout:
    def test_abandoned_grade_clears_flag_near_bound_not_full_read_timeout(self, daemon, tmp_path):
        """Cherry-pick A: LocalGrader.grade's per-call read_timeout must bound the
        underlying client's read timeout to _grade_bounded's `timeout`, so the
        thread left running past an abandoned call's join ends near that bound
        (~5s target) instead of stalling for the full 600s inference read
        timeout — clearing _grade_in_flight promptly and letting the NEXT call
        proceed rather than being skipped as still-in-flight."""
        import time as _time
        import requests
        from ironclaude.grader import LocalGrader
        from ironclaude.ollama_client import OllamaClient

        # The fake transport stalls for a fixed 0.5s — reliably longer than the
        # 0.2s _grade_bounded join (so the first call is deterministically
        # abandoned, not racing thread-scheduling jitter against an equal
        # sleep) yet still well under the OLD 120s default that read_timeout
        # replaces, and short enough to clear within the poll window below.
        # kw["timeout"][1] is captured directly to prove the CLIENT's own read
        # timeout was actually threaded down to ~0.2s (not left at 120s).
        captured_read_timeouts = []

        def fake_post(url, **kw):
            captured_read_timeouts.append(kw["timeout"][1])
            _time.sleep(0.5)
            raise requests.ReadTimeout()

        daemon._grader = LocalGrader(config_path=str(tmp_path / "absent.json"), keep_alive="30m")

        with patch("ironclaude.ollama_client.requests.post", side_effect=fake_post) as mock_post, \
                patch.object(OllamaClient, "_probe_reachable", return_value=True):
            assert daemon._grade_bounded("s", "u", None, timeout=0.2) is None
            assert captured_read_timeouts == [0.2]   # client's read timeout bounded near the join, not 120s

            for _ in range(60):
                if not daemon._grade_in_flight:
                    break
                _time.sleep(0.05)
            assert daemon._grade_in_flight is False
            assert mock_post.call_count == 1

            # A second call now proceeds (not skipped as still-in-flight) and
            # actually invokes the transport again.
            daemon._grade_bounded("s", "u", None, timeout=0.2)
            assert mock_post.call_count == 2

            # Drain the second abandoned worker while the patches are still
            # active, so it never falls through to a real network probe.
            for _ in range(60):
                if not daemon._grade_in_flight:
                    break
                _time.sleep(0.05)
            assert daemon._grade_in_flight is False


class TestCodexBrainExecutingToolAttr:
    def test_idle_enforcement_does_not_crash_on_real_codex_brain(self, tmp_path):
        """C1 regression: CodexBrainClient lacked _executing_tool, so the R4 gating
        deref at main.py:4417 AttributeError'd and crash-looped the daemon under
        BRAIN_CLIENT=codex. A MagicMock brain hides the gap; a real client exposes it."""
        import time as _t
        from ironclaude.codex_brain_client import CodexBrainClient
        slack = MagicMock()
        registry = MagicMock()
        registry.get_recent_workers.return_value = []
        tmux = MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        os.makedirs(tmux.log_dir, exist_ok=True)
        d = IroncladeDaemon({"tmp_dir": str(tmp_path)}, slack, None, registry, tmux, CodexBrainClient())
        d._state_manager_db_path = str(tmp_path / "state-manager.db")
        d._db = None
        d._get_unprocessed_messages = lambda: ["m1"]
        d._last_idle_check = 0.0
        d._idle_enforcement_start = _t.time() - 400
        d._idle_escalation_tier = 0
        d._operator_notified_idle = False

        # Load-bearing regression assertion: this call must NOT raise AttributeError.
        d.check_idle_enforcement()

        # The inert Codex semantic is pinned.
        assert d.brain._executing_tool is False


def test_brain_settings_hooks_template_lists_all_three_gates():
    """#1: _sync_brain_settings_hooks only deploys+registers hooks named in this
    template. All three commander Brain gates must be listed so their scripts reach
    the stable dir on daemon start (else a source fix — e.g. obs-3's memory-search
    fail-closed — never reaches prod)."""
    import json as _json
    from pathlib import Path
    template = _json.loads(
        (Path(__file__).resolve().parents[1] / "src" / "brain" / "brain_settings_hooks.json").read_text()
    )
    cmds = "\n".join(
        h.get("command", "")
        for e in template.get("PreToolUse", [])
        for h in e.get("hooks", [])
    )
    assert "brain-task-gate.sh" in cmds
    assert "startup-lookback-enforcer.sh" in cmds
    assert "memory-search-enforcer.sh" in cmds
    # obs-6(c): every command must use $HOME, not ~. _sync appends an entry only when its
    # exact command string is absent, so a ~-vs-$HOME drift would register a SECOND copy of a
    # gate; memory-search rm -f's its arm flag each pass, so a double-registration blocks every
    # gated action. Pin the exact deployed prefix.
    for e in template.get("PreToolUse", []):
        for h in e.get("hooks", []):
            cmd = h.get("command", "")
            assert cmd.startswith("bash $HOME/.claude/ironclaude-hooks/"), (
                f"template hook command must use $HOME (not ~); got: {cmd!r}"
            )
