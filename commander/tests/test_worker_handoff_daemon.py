"""Native quota detection only starts handoffs; only an unpaused handoff row suppresses lifecycle."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock
import uuid

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.worker_handoff import HandoffHold, WorkerHandoffController
from ironclaude.worker_quota import read_native_quota


def _daemon(tmp_path, client="claude"):
    registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    native_id = str(uuid.uuid4())
    registry.register_worker("w1", "claude-opus", "ic-w1", repo=str(tmp_path),
                             description="unfinished", client=client,
                             model="opus" if client == "claude" else "gpt-5.6-sol",
                             native_session_id=native_id)
    registry.set_worker_workspace("w1", {
        "workspace_guid": str(uuid.uuid4()), "repository_identity": "repository",
        "worktree_path": str(tmp_path), "branch": "work", "base_commit": "base",
        "integration_target": "main",
    })
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon.registry = registry
    daemon._native_log_root = tmp_path / "home"
    daemon.slack = MagicMock()
    daemon.brain = MagicMock()
    daemon.tmux = MagicMock()
    daemon.tmux.log_dir = str(tmp_path / "logs")
    daemon.config = {}
    daemon._get_orchestrator = MagicMock()
    daemon._prompt_store = MagicMock(return_value=None)
    daemon._worker_idle_since = {}
    daemon._resolve_worker_ssh = MagicMock(return_value=(None, None))
    return daemon, native_id


def _claude_quota(native_id, cwd, *, event_uuid="quota", timestamp="2026-10-05T12:00:00Z"):
    return {"session_id": native_id, "sessionId": native_id, "cwd": str(cwd),
            "timestamp": timestamp, "type": "assistant", "uuid": event_uuid,
            "error": "rate_limit", "isApiErrorMessage": True, "apiErrorStatus": 429,
            "quotaLimits": {"status": "rejected", "rateLimitType": "seven_day",
                            "resetsAt": 1, "unifiedRateLimitFallbackAvailable": False,
                            "overageStatus": "rejected", "isUsingOverage": False},
            "message": {"role": "assistant", "model": "<synthetic>",
                        "usage": {"output_tokens": 1}}}


def test_missing_native_log_does_not_suppress_dead_session_handling(tmp_path):
    daemon, _ = _daemon(tmp_path)
    _dead_worker_seams(daemon)
    daemon.check_workers()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry.get_native_quota_evidence("w1") is None
    assert not any("native quota" in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)


def test_path_less_worker_is_excluded_without_notification(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    daemon.registry._conn.execute("UPDATE workers SET workspace_path=NULL WHERE id='w1'")
    daemon.registry._conn.commit()
    _dead_worker_seams(daemon)
    daemon.check_workers()
    assert daemon.registry.get_native_quota_evidence("w1") is None
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    assert not any("native quota" in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)


def test_remote_worker_is_excluded_before_any_read_without_notification(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    daemon.registry._conn.execute("UPDATE workers SET machine='remote-host' WHERE id='w1'")
    daemon.registry._conn.commit()
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1") is None
    daemon._get_orchestrator.assert_not_called()
    daemon.slack.post_message.assert_not_called()
    daemon.brain.send_message.assert_not_called()
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False


def test_pending_handoff_reconciles_outside_running_enumeration_once(tmp_path):
    daemon, _ = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    daemon.registry.claim_worker_handoff(worker, generation=1)
    facade = daemon._get_orchestrator.return_value
    facade.reconcile_worker_handoff.return_value = {
        "status": "pending", "phase": "claimed", "reason": "awaiting_readback",
    }
    daemon.check_workers()
    daemon.check_workers()
    assert facade.reconcile_worker_handoff.call_count == 2
    assert daemon.slack.post_message.call_count == 1
    assert daemon.registry.get_worker("w1")["status"] == "handoff_pending"
    daemon.tmux.has_session.assert_not_called()


def test_busy_reconciler_does_not_consume_later_hold_notification(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    facade = daemon._get_orchestrator.return_value
    facade.reconcile_worker_handoff.side_effect = [
        {"status": "pending", "phase": "claimed", "reason": "handoff_reconcile_owned"},
        {"status": "held", "phase": "claimed", "reason": "opposite_capability_unavailable"},
    ]
    daemon.check_workers()
    daemon.slack.post_message.assert_not_called()
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    assert "opposite_capability_unavailable" in daemon.slack.post_message.call_args.args[0]


def test_held_worker_satisfies_confirmed_directive_without_replacement_prompt(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry._conn.execute(
        "INSERT INTO directives (source_ts, source_text, interpretation, status) "
        "VALUES ('1', 'work', 'work', 'confirmed')"
    )
    daemon.registry._conn.execute("UPDATE workers SET description='#1 unfinished' WHERE id='w1'")
    daemon.registry._conn.commit()
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    daemon._db = daemon.registry._conn
    daemon._directive_reminder_sent = {}
    daemon.check_confirmed_directives()
    daemon.brain.send_message.assert_not_called()


def test_stale_running_snapshot_never_reads_or_mutates_successor(tmp_path):
    daemon, _ = _daemon(tmp_path)
    old = daemon.registry.get_worker("w1")
    daemon.registry.get_running_workers = lambda: [old]
    daemon.registry.set_worker_provider("w1", "codex", "gpt-5.6-sol")
    daemon.check_workers()
    daemon.tmux.has_session.assert_not_called()
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry.get_worker("w1")["client"] == "codex"


def test_ordinary_unmanaged_worker_reaches_old_completion_branch(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.set_worker_provider("w1", "ollama", "local")
    daemon.tmux.has_session.return_value = False
    daemon._finalize_and_release_worker = MagicMock(return_value=None)
    daemon._drive_finalization_recovery = MagicMock(return_value="held")
    daemon._session_died_notified = set()
    daemon.registry.get_events_for_worker = MagicMock(return_value=[])
    daemon._finalize_marker_seen = {}
    daemon._finalize_drift_retry = {}
    daemon._finalize_failure_count = {}
    daemon._finalize_recovery_alerted = set()
    daemon._finalize_failure_alerted = set()
    daemon._commit_failure_alerted = set()
    daemon._resolve_worker_prompt = MagicMock()
    daemon.check_workers()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once()


def test_unknown_evidence_has_no_incident_recorder(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert not hasattr(WorkerRegistry, "record_native_quota_unknown_observation")
    assert "unknown_observation" not in daemon.registry.get_native_quota_evidence("w1")["verdict"]


def test_handoff_suppression_requires_unpaused_unfinished_row(tmp_path):
    daemon, _ = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    assert daemon._handoff_suppresses(worker) is False
    daemon.registry.claim_worker_handoff(worker, generation=1)
    assert daemon._handoff_suppresses(worker) is True
    daemon.registry.pause_worker_handoff("w1", 1, "opposite_capability_unavailable")
    assert daemon._handoff_suppresses(worker) is False


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("reason", ["operator_paused_pm", "workspace_handoff_protocol_unavailable"])
def test_confirmed_native_quota_starts_handoff_without_suppressing_lifecycle(tmp_path, client, reason):
    daemon, native_id = _daemon(tmp_path, client)
    root = daemon._native_log_root
    if client == "claude":
        path = root / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [_claude_quota(native_id, tmp_path, event_uuid="turn")]
    else:
        path = root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "task_started", "turn_id": "t"}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": "rate_limit_reached"},
                     "info": {"total_token_usage": {"output_tokens": 7}}}}]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if reason != "operator_paused_pm":
        packet = read_native_quota(
            path, client=client, native_session_id=native_id,
            workspace_path=str(tmp_path), generation="1",
        )
        daemon.registry.record_native_quota_verdict(
            "w1", 1, packet, snapshot=daemon.registry.get_worker("w1"),
        )
    daemon._get_orchestrator.return_value.handoff_worker.return_value = {
        "status": "rejected", "reason": reason,
    }
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    packet = daemon._get_orchestrator.return_value.handoff_worker.call_args.args[1]
    assert packet["state"] == "exhausted"
    assert packet["native_session_id"] == native_id
    assert daemon.registry.get_worker("w1")["status"] == "running"
    assert daemon.registry.get_worker_handoff("w1") is None
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False
    if reason == "operator_paused_pm":
        daemon.slack.post_message.assert_not_called()
    else:
        assert daemon.slack.post_message.call_count == 1
        assert "Workspace assignment hold is unproved" in daemon.slack.post_message.call_args.args[0]


def test_rejected_exhaustion_does_not_suppress_dead_session_handling(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    daemon._get_orchestrator.return_value.handoff_worker.return_value = {
        "status": "rejected", "reason": "operator_paused_pm",
    }
    _dead_worker_seams(daemon)
    daemon.check_workers()
    daemon._get_orchestrator.return_value.handoff_worker.assert_called_once()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )


def test_new_native_success_clears_only_matching_usage_incident(tmp_path):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    quota = _claude_quota(native_id, tmp_path)
    path.write_text(json.dumps(quota) + "\n")
    first = read_native_quota(path, client="claude", native_session_id=native_id,
                              workspace_path=str(tmp_path), generation="1")
    daemon.registry.record_native_quota_verdict("w1", 1, first,
                                                 snapshot=daemon.registry.get_worker("w1"))
    state = ProviderState(daemon.registry._conn)
    key = f"{native_id}:{first['file_identity']}:1"
    state.mark_unavailable("local", "claude", "worker", "opus", "usage", "native_usage_exhausted",
                           usage_generation=key, usage_cursor=first["cursor"],
                           usage_event_key=first["event_key"])
    state.mark_unavailable("local", "claude", "worker", "sonnet", "usage", "other_worker",
                           usage_generation="other", usage_cursor=1, usage_event_key="other")
    success = {"session_id": native_id, "sessionId": native_id, "cwd": str(tmp_path),
               "timestamp": "2026-10-05T12:00:01Z", "type": "assistant", "uuid": "success",
               "message": {"role": "assistant", "model": "claude-opus-4-5", "stop_reason": "end_turn",
                           "usage": {"output_tokens": 2}}}
    with path.open("a") as stream:
        stream.write(json.dumps(success) + "\n")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert state.unavailable_reason("local", "claude", "worker", "opus") is None
    assert state.unavailable_reason("local", "claude", "worker", "sonnet")["reason"] == "other_worker"
    daemon._get_orchestrator.assert_not_called()


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize("fresh", [True, False])
def test_successful_other_native_session_recovers_only_newer_provider_quota(tmp_path, client, fresh):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path, client)
    state = ProviderState(daemon.registry._conn)
    predecessor = str(uuid.uuid4())
    key = ("local", client, "worker", state.worker_usage_scope_tier("worker", "opus"))
    # The exhausted predecessor has stopped. This is independent genuine native
    # success from another managed worker using the same scoped provider.
    state.mark_unavailable(*key, "usage", "stopped predecessor quota",
                           usage_generation=f"{predecessor}:old-file:1", usage_cursor=900,
                           usage_event_key="old-quota", usage_event_timestamp="2026-10-05T12:00:00Z")
    state.mark_unavailable("local", client, "worker", "sonnet", "usage", "other tier")
    state.set_current_client("worker", "codex")
    minute = "01" if fresh else "00"
    when = f"2026-10-05T12:{minute}:00Z"
    if client == "claude":
        path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [{"session_id": native_id, "sessionId": native_id, "cwd": str(tmp_path),
                 "timestamp": when, "type": "assistant", "uuid": "fresh-success",
                 "message": {"role": "assistant", "model": "claude-opus-4-5", "stop_reason": "end_turn",
                             "usage": {"output_tokens": 2}}}]
    else:
        path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
                {"type": "event_msg", "timestamp": when,
                 "payload": {"type": "task_started", "turn_id": "success-turn"}},
                {"type": "turn_context", "timestamp": when,
                 "payload": {"turn_id": "success-turn", "model": "gpt-5.6-sol"}},
                {"type": "response_item", "timestamp": when,
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "completed work"}]}},
                {"type": "event_msg", "timestamp": when,
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": None},
                     "info": {"total_token_usage": {"output_tokens": 2}}}},
                {"type": "event_msg", "timestamp": when,
                 "payload": {"type": "task_complete", "turn_id": "success-turn"}}]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert state.is_available(*key) is fresh
    assert not state.is_available("local", client, "worker", "sonnet")
    assert state.get_current_client("worker") == "codex"
    daemon._get_orchestrator.assert_not_called()
    daemon.slack.post_message.assert_not_called()


@pytest.mark.parametrize("client,native_model", [
    ("claude", "claude-sonnet-4-5"), ("claude", None),
    ("codex", "gpt-5.6-terra"), ("codex", None),
])
def test_native_success_cannot_clear_other_model_quarantine(tmp_path, client, native_model):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path, client)
    state = ProviderState(daemon.registry._conn)
    key = ("local", client, "worker", state.worker_usage_scope_tier("worker", "opus"))
    state.mark_unavailable(*key, "usage", "prior worker quota",
        usage_generation=f"{uuid.uuid4()}:old:1", usage_cursor=100,
        usage_event_key="old-quota", usage_event_timestamp="2026-10-05T12:00:00Z")
    when = "2026-10-05T12:01:00Z"
    if client == "claude":
        path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
        message = {"role": "assistant", "stop_reason": "end_turn", "usage": {"output_tokens": 2}}
        if native_model is not None:
            message["model"] = native_model
        rows = [{"session_id": native_id, "sessionId": native_id, "cwd": str(tmp_path),
                 "timestamp": when, "type": "assistant", "uuid": "success", "message": message}]
    else:
        path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        context = {"turn_id": "success"}
        if native_model is not None:
            context["model"] = native_model
        rows = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}}]
        if native_model is None:
            rows.extend([
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "task_started", "turn_id": "quota"}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": "rate_limit_reached"},
                     "info": {"total_token_usage": {"output_tokens": 7}}}},
                {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
                 "payload": {"type": "task_complete", "turn_id": "quota"}},
            ])
        rows.extend([
            {"type": "event_msg", "timestamp": when,
             "payload": {"type": "task_started", "turn_id": "success"}},
            {"type": "turn_context", "timestamp": when, "payload": context},
            {"type": "response_item", "timestamp": when,
             "payload": {"type": "message", "role": "assistant", "content": [
                 {"type": "output_text", "text": "completed work"}]}},
            {"type": "event_msg", "timestamp": when,
             "payload": {"type": "token_count", "rate_limits": {
                 "limit_id": "codex", "rate_limit_reached_type": None},
                 "info": {"total_token_usage": {"output_tokens": 9}}}},
            {"type": "event_msg", "timestamp": when,
             "payload": {"type": "task_complete", "turn_id": "success"}},
        ])
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    parser = read_native_quota(path, client=client, native_session_id=native_id,
                               workspace_path=str(tmp_path), generation="1")
    assert parser["state"] == ("ordinary" if client == "claude" and native_model is None
                               else "available")
    assert parser["observed_model"] == native_model
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert not state.is_available(*key)
    daemon._get_orchestrator.assert_not_called()


def test_later_codex_success_reopens_capacity_after_other_session_quota(tmp_path):
    from ironclaude.provider_state import ProviderState

    daemon, native_id = _daemon(tmp_path, "codex")
    path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": native_id, "cwd": str(tmp_path)}}) + "\n")

    def append_success(turn, timestamp):
        rows = [{"type": "event_msg", "timestamp": timestamp,
                 "payload": {"type": "task_started", "turn_id": turn}},
                {"type": "turn_context", "timestamp": timestamp,
                 "payload": {"turn_id": turn, "model": "gpt-5.6-sol"}},
                {"type": "response_item", "timestamp": timestamp,
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "native success"}]}},
                {"type": "event_msg", "timestamp": timestamp,
                 "payload": {"type": "token_count", "rate_limits": {
                     "limit_id": "codex", "rate_limit_reached_type": None}, "info": None}},
                {"type": "event_msg", "timestamp": timestamp,
                 "payload": {"type": "task_complete", "turn_id": turn}}]
        with path.open("a") as stream:
            stream.write("".join(json.dumps(row) + "\n" for row in rows))

    append_success("first", "2026-10-05T12:01:00Z")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    first = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    state = ProviderState(daemon.registry._conn)
    key = ("local", "codex", "worker", state.worker_usage_scope_tier("worker", "opus"))
    state.mark_unavailable(*key, "usage", "another worker exhausted at T2",
                           usage_generation=f"{uuid.uuid4()}:other-file:1", usage_cursor=50,
                           usage_event_key="other-quota", usage_event_timestamp="2026-10-05T12:02:00Z")
    # Re-observing the earlier successful EOF cannot erase the newer incident.
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert not state.is_available(*key)
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "event_msg", "timestamp": "2026-10-05T12:02:30Z",
            "payload": {"type": "error", "message": "native request failed"}}) + "\n")
    assert read_native_quota(path, client="codex", native_session_id=native_id,
        workspace_path=str(tmp_path), generation="1")["state"] == "unknown"
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1")["verdict"]["state"] == "unknown"
    assert not state.is_available(*key)
    daemon.slack.post_message.assert_not_called()
    append_success("later", "2026-10-05T12:03:00Z")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    last = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert last["event_timestamp"] > first["event_timestamp"]
    assert last["event_key"] != first["event_key"]
    assert state.is_available(*key)
    daemon._get_orchestrator.assert_not_called()


def _ordinary_native_log(daemon, native_id, client):
    if client == "claude":
        path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [{"type": "user", "session_id": native_id, "sessionId": native_id,
                 "cwd": daemon.registry.get_worker("w1")["workspace_path"],
                 "timestamp": "2026-10-05T12:00:00Z", "message": {"role": "user"}}]
    else:
        path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type": "session_meta", "payload": {
            "id": native_id, "cwd": daemon.registry.get_worker("w1")["workspace_path"]}},
            {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
             "payload": {"type": "task_started", "turn_id": "ordinary"}}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _dead_worker_seams(daemon):
    daemon.tmux.has_session.return_value = False
    daemon._finalize_and_release_worker = MagicMock(return_value=None)
    daemon._drive_finalization_recovery = MagicMock(return_value="held")
    daemon._session_died_notified = set()
    daemon.registry.get_events_for_worker = MagicMock(return_value=[])
    daemon._finalize_marker_seen = {}
    daemon._finalize_drift_retry = {}
    daemon._finalize_failure_count = {}
    daemon._finalize_recovery_alerted = set()
    daemon._finalize_failure_alerted = set()
    daemon._commit_failure_alerted = set()
    daemon._resolve_worker_prompt = MagicMock()


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_managed_ordinary_native_evidence_reaches_terminal_branch(tmp_path, client):
    daemon, native_id = _daemon(tmp_path, client)
    _ordinary_native_log(daemon, native_id, client)
    _dead_worker_seams(daemon)
    daemon.check_workers()
    daemon.tmux.has_session.assert_called_once()
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry.get_native_quota_evidence("w1")["verdict"]["state"] == "ordinary"


def test_native_reader_state_resumes_after_new_daemon_instance(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["cursor"] > 0 and saved["reader_state"]["cursor"] == saved["cursor"]
    with path.open("a") as stream:
        stream.write(json.dumps(_claude_quota(native_id, tmp_path,
                                              timestamp="2026-10-05T12:00:01Z")) + "\n")
    restarted = IroncladeDaemon.__new__(IroncladeDaemon)
    restarted.registry = daemon.registry
    restarted._native_log_root = daemon._native_log_root
    restarted.slack = MagicMock(); restarted.brain = MagicMock()
    restarted._get_orchestrator = MagicMock()
    restarted._get_orchestrator.return_value.handoff_worker.return_value = {
        "status": "rejected", "reason": "operator_paused_pm",
    }
    restarted._maybe_start_handoff(restarted.registry.get_worker("w1"))
    packet = restarted._get_orchestrator.return_value.handoff_worker.call_args.args[1]
    assert packet["state"] == "exhausted" and packet["cursor"] > saved["cursor"]
    assert packet["file_identity"] == saved["file_identity"]


@pytest.mark.parametrize("change", ["truncated", "replaced"])
def test_changed_native_log_never_rewinds_trusted_cursor_or_notifies(tmp_path, change):
    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    trusted = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert trusted["cursor"] > 0
    if change == "truncated":
        path.write_text("")
    else:
        path.unlink()
        path.write_text("{}\n")
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["cursor"] == trusted["cursor"]
    assert saved["reader_state"] == trusted["reader_state"]
    assert saved["state"] == "ordinary"
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False
    daemon.slack.post_message.assert_not_called()
    daemon.brain.send_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_malformed_native_line_is_skipped_and_cursor_advances(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    trusted = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    with path.open("a") as stream:
        stream.write("{bad}\n")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["state"] == "ordinary"
    assert saved["cursor"] == path.stat().st_size > trusted["cursor"]
    assert saved["reader_state"]["skipped_records"] == 1
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_bounded_scan_persists_forward_cursor_until_current_eof(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    import ironclaude.main as daemon_module

    daemon, native_id = _daemon(tmp_path)
    path = _ordinary_native_log(daemon, native_id, "claude")
    row = {"type": "user", "session_id": native_id, "sessionId": native_id,
           "cwd": str(tmp_path), "timestamp": "2026-10-05T12:00:01Z",
           "message": {"role": "user"}}
    with path.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
        stream.write(json.dumps(row) + "\n")
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: next(clock))
    cursors = []
    for _ in range(3):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
        saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
        assert saved["state"] == "unknown"
        assert saved["reason"] == "native_quota_log_scan_budget_exceeded"
        cursors.append(saved["cursor"])
    assert cursors == sorted(set(cursors))
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    final = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert final["state"] == "ordinary" and final["cursor"] == cursors[-1]
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_blocked_finalization_cannot_claim_handoff_or_mutate_successor(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    _dead_worker_seams(daemon)
    source = daemon.registry.get_worker("w1")
    observed = []
    def blocked_grade(_worker_id, *, reason, terminal):
        with pytest.raises(RuntimeError, match="operation"):
            daemon.registry.claim_worker_handoff(source, generation=1)
        observed.append((reason, terminal))
        return None
    daemon._finalize_and_release_worker.side_effect = blocked_grade
    daemon.check_workers()
    assert observed == [("session ended", True)]
    assert daemon.registry.get_worker_handoff("w1") is None
    assert daemon.registry.get_worker("w1")["native_session_id"] == native_id


@pytest.mark.parametrize("appearance", ["ordinary", "quota"])
def test_missing_native_log_records_nothing_until_file_appears(tmp_path, appearance):
    daemon, native_id = _daemon(tmp_path)
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1") is None
    path = _ordinary_native_log(daemon, native_id, "claude")
    if appearance == "quota":
        with path.open("a") as stream:
            stream.write(json.dumps(_claude_quota(native_id, tmp_path,
                                                  timestamp="2026-10-05T12:00:01Z")) + "\n")
        daemon._get_orchestrator.return_value.handoff_worker.return_value = {
            "status": "rejected", "reason": "operator_paused_pm",
        }
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    daemon.slack.post_message.assert_not_called()
    if appearance == "ordinary":
        current = daemon.registry.get_native_quota_evidence("w1")["verdict"]
        assert current["state"] == "ordinary" and current["file_identity"]
        daemon._get_orchestrator.assert_not_called()
    else:
        packet = daemon._get_orchestrator.return_value.handoff_worker.call_args.args[1]
        assert packet["state"] == "exhausted" and packet["file_identity"]


def test_missing_log_then_large_first_file_resumes_across_restart(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    import ironclaude.main as daemon_module

    daemon, native_id = _daemon(tmp_path)
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    assert daemon.registry.get_native_quota_evidence("w1") is None
    path = _ordinary_native_log(daemon, native_id, "claude")
    row = {"type": "user", "session_id": native_id, "sessionId": native_id,
           "cwd": str(tmp_path), "timestamp": "2026-10-05T12:00:01Z",
           "message": {"role": "user"}}
    with path.open("a") as stream:
        stream.write("".join(json.dumps(row) + "\n" for _ in range(3)))
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: next(clock))
    cursors = []
    for attempt in range(4):
        if attempt == 1:
            restarted = IroncladeDaemon.__new__(IroncladeDaemon)
            restarted.registry = daemon.registry
            restarted._native_log_root = daemon._native_log_root
            restarted.slack = daemon.slack
            restarted.brain = daemon.brain
            restarted._get_orchestrator = daemon._get_orchestrator
            daemon = restarted
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
        saved = daemon.registry.get_native_quota_evidence("w1")["verdict"]
        assert saved["state"] == "unknown"
        assert saved["reason"] == "native_quota_log_scan_budget_exceeded"
        assert saved["file_identity"] and saved["reader_state"]["cursor"] == saved["cursor"]
        cursors.append(saved["cursor"])
    assert cursors == sorted(set(cursors))
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    final = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert final["state"] == "ordinary" and final["cursor"] == cursors[-1]
    path.unlink()
    path.write_text("{}\n")
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    replaced = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert replaced["cursor"] == final["cursor"]
    assert replaced["reader_state"] == final["reader_state"]
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


def test_unproved_completed_successor_tier_records_unknown_without_suppressing(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    worker = daemon.registry.get_worker("w1")
    daemon.registry._conn.execute(
        "INSERT INTO worker_handoffs(worker_id,generation,phase,snapshot_json,evidence_json,completed_at) "
        "VALUES (? ,1,'completed',?,?,datetime('now'))",
        ("w1", json.dumps(daemon.registry._snapshot(worker)), json.dumps({
            "assignment_finished": True, "target_tier": "invalid",
            "target_runtime": {key: worker[key] for key in (
                "client", "model", "native_session_id", "tmux_session", "machine")},
        })),
    )
    daemon.registry._conn.commit()
    for _ in range(2):
        daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    verdict = daemon.registry.get_native_quota_evidence("w1")["verdict"]
    assert (verdict["state"], verdict["status"], verdict["exhausted"]) == ("unknown", "unknown", False)
    assert verdict["reason"] == "worker_effective_tier_unproved"
    assert daemon._handoff_suppresses(daemon.registry.get_worker("w1")) is False
    daemon.slack.post_message.assert_not_called()
    daemon._get_orchestrator.assert_not_called()


@pytest.mark.parametrize("evidence_kind", ["error", "token_count"])
def test_stale_turn_native_evidence_cannot_claim_or_stop_current_codex_worker(tmp_path, evidence_kind):
    daemon, native_id = _daemon(tmp_path, "codex")
    path = daemon._native_log_root / ".codex/sessions" / f"rollout-{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:00:00Z",
         "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:00:00.500Z",
         "payload": {"type": "error", "turn_id": "t1", "message": "usage exhausted",
                     "codex_error_info": "usage_limit_exceeded"}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:00:01Z",
         "payload": {"type": "task_complete", "turn_id": "t1"}},
        {"type": "event_msg", "timestamp": "2026-10-05T12:01:00Z",
         "payload": {"type": "task_started", "turn_id": "t2"}},
    ]
    if evidence_kind == "error":
        rows.append({"type": "event_msg", "timestamp": "2026-10-05T12:02:00Z",
                     "payload": {"type": "error", "turn_id": "t1", "message": "usage exhausted",
                                 "codex_error_info": "usage_limit_exceeded"}})
    else:
        rows.append({"type": "event_msg", "timestamp": "2026-10-05T12:02:00Z",
                     "payload": {"type": "token_count", "turn_id": "t1",
                                 "rate_limits": {"limit_id": "codex",
                                                 "rate_limit_reached_type": "rate_limit_reached"},
                                 "info": {"total_token_usage": {"output_tokens": 7}}}})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    worker = daemon.registry.get_worker("w1")
    actual = read_native_quota(path, client="codex", native_session_id=native_id,
                               workspace_path=str(tmp_path), generation="1")
    assert actual["state"] == "unknown" and actual["event_key"]
    assert actual["file_identity"] and actual["cursor"] > 0
    daemon._maybe_start_handoff(worker)
    assert daemon._handoff_suppresses(worker) is False
    assert daemon.registry.get_native_quota_evidence("w1")["verdict"]["state"] == "unknown"
    daemon._get_orchestrator.assert_not_called()
    process, runtime = MagicMock(), MagicMock()
    tools = SimpleNamespace(registry=daemon.registry, tmux=daemon.tmux,
                            _workspace_client=MagicMock(), _ssh_manager=None)
    controller = WorkerHandoffController(tools, process=process, runtime=runtime,
                                         native_log_root=daemon._native_log_root)
    forged = {**actual, "state": "exhausted", "status": "rejected", "exhausted": True}
    with pytest.raises(HandoffHold, match="native_quota_evidence_changed"):
        controller._validate_quota(worker, 1, forged)
    assert daemon.registry.get_worker_handoff("w1") is None
    assert daemon.registry.get_worker("w1")["status"] == "running"
    process.stop_and_prove.assert_not_called()
    runtime.bootstrap.assert_not_called()


def test_dead_started_operation_is_reaped_and_reaches_ordinary_handling(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    _ordinary_native_log(daemon, native_id, "claude")
    worker = daemon.registry.get_worker("w1")
    lease = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry.mark_worker_operation_effects_started(lease, worker)
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    _dead_worker_seams(daemon)
    daemon.check_workers()
    reaped = daemon.registry._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped' AND worker_id='w1'"
    ).fetchall()
    assert len(reaped) == 1 and json.loads(reaped[0]["details"])["nonce"] == lease["nonce"]
    assert not any("effects_readback_required" in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    assert daemon.registry._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None


def test_startup_sweep_reaps_dead_owner_rows_and_keeps_live_ones(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.register_worker("w2", "claude-opus", "ic-w2", repo=str(tmp_path), client="claude",
                                    model="opus", native_session_id=str(uuid.uuid4()))
    dead_lease = daemon.registry.acquire_worker_operation(
        daemon.registry.get_worker("w1"), operation="worker_mutation")
    live_owner = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    live_owner.acquire_worker_operation(live_owner.get_worker("w2"), operation="worker_mutation")
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    workspace = daemon._get_orchestrator.return_value._workspace_client
    workspace.discover_installed_plugin_root.return_value = "/installed/plugin"
    workspace.reap_stale_mutations.return_value = {"protocol_version": 1, "reaped": []}
    daemon._sweep_dead_owner_leases()
    rows = [row["worker_id"] for row in daemon.registry._conn.execute(
        "SELECT worker_id FROM worker_operations ORDER BY worker_id").fetchall()]
    assert rows == ["w2"]
    details = daemon.registry._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped'").fetchone()["details"]
    assert json.loads(details)["nonce"] == dead_lease["nonce"]
    workspace.reap_stale_mutations.assert_called_once_with({}, plugin_root="/installed/plugin")


def test_startup_sweep_tolerates_installed_workspace_without_command(tmp_path):
    daemon, _ = _daemon(tmp_path)
    workspace = daemon._get_orchestrator.return_value._workspace_client
    workspace.discover_installed_plugin_root.return_value = "/installed/plugin"
    workspace.reap_stale_mutations.side_effect = RuntimeError(
        "Unknown internal workspace command: reap-stale-mutations")
    daemon._sweep_dead_owner_leases()
    assert workspace.reap_stale_mutations.call_count == 2


def test_run_sweeps_dead_owner_leases_before_first_cycle(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon._running = False
    daemon._sweep_dead_owner_leases = MagicMock()
    daemon.run()
    daemon._sweep_dead_owner_leases.assert_called_once_with()


def test_post_stop_release_routes_through_dead_session_preserve_path_once(tmp_path):
    daemon, _ = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    daemon.registry.claim_worker_handoff(worker, generation=1)
    daemon.registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    daemon.registry.pause_worker_handoff("w1", 1, "pm_readback_failed")
    _dead_worker_seams(daemon)
    facade = daemon._get_orchestrator.return_value

    def release(worker_id):
        daemon.registry.release_worker_handoff(worker_id, 1, "pm_readback_failed", after_stop=True)
        return {"status": "released", "worker_id": worker_id, "generation": 1, "phase": "released",
                "reason": "pm_readback_failed", "preserve": True}

    facade.retry_worker_handoff_preflight.side_effect = release
    daemon.check_workers()
    daemon._finalize_and_release_worker.assert_called_once_with("w1", reason="session ended", terminal=True)
    daemon._drive_finalization_recovery.assert_called_once()
    assert daemon.slack.post_message.call_count == 1
    assert "pm_readback_failed" in daemon.slack.post_message.call_args.args[0]
    assert daemon.registry.get_worker("w1")["status"] == "running"
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    assert daemon.brain.send_message.call_count == 1


def test_prestop_release_result_does_not_preserve_or_notify(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    daemon.registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    _dead_worker_seams(daemon)
    facade = daemon._get_orchestrator.return_value

    def release(worker_id):
        daemon.registry.release_worker_handoff(worker_id, 1, "native_quota_evidence_changed")
        return {"status": "released", "worker_id": worker_id, "generation": 1, "phase": "released",
                "reason": "native_quota_evidence_changed"}

    facade.retry_worker_handoff_preflight.side_effect = release
    daemon.check_workers()
    daemon._finalize_and_release_worker.assert_not_called()
    daemon.slack.post_message.assert_not_called()
    assert daemon.registry.get_worker("w1")["status"] == "running"


def test_uncertain_effect_pause_stays_with_one_notification(tmp_path):
    daemon, _ = _daemon(tmp_path)
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1)
    daemon.registry.pause_worker_handoff("w1", 1, "uncertain_external_effect")
    facade = daemon._get_orchestrator.return_value
    facade.retry_worker_handoff_preflight.return_value = {
        "status": "held", "phase": "claimed", "reason": "uncertain_external_effect"}
    daemon.check_workers()
    daemon.check_workers()
    assert daemon.slack.post_message.call_count == 1
    assert "uncertain_external_effect" in daemon.slack.post_message.call_args.args[0]
    assert daemon.registry.get_worker("w1")["status"] == "handoff_pending"


def test_released_handoff_event_is_not_reclaimed_until_fresh_exhaustion(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    verdict = read_native_quota(path, client="claude", native_session_id=native_id,
                                workspace_path=str(tmp_path), generation="1")
    daemon.registry.claim_worker_handoff(daemon.registry.get_worker("w1"), generation=1,
                                         initial_evidence={"quota_verdict": verdict})
    daemon.registry.pause_worker_handoff("w1", 1, "source_process_changed_before_stop")
    daemon.registry.release_worker_handoff("w1", 1, "source_process_changed_before_stop")
    facade = daemon._get_orchestrator.return_value
    facade.handoff_worker.return_value = {"status": "rejected", "reason": "operator_paused_pm"}
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    facade.handoff_worker.assert_not_called()
    with path.open("a") as stream:
        stream.write(json.dumps(_claude_quota(native_id, tmp_path, event_uuid="fresh",
                                              timestamp="2026-10-05T12:10:00Z")) + "\n")
    daemon._maybe_start_handoff(daemon.registry.get_worker("w1"))
    packet = facade.handoff_worker.call_args.args[1]
    assert packet["event_key"] != verdict["event_key"]
    assert packet["generation"] == "2"


def test_confirmed_quota_recovers_dead_claimed_operation_before_handoff(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    path = daemon._native_log_root / ".claude/projects/source" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_claude_quota(native_id, tmp_path)) + "\n")
    worker = daemon.registry.get_worker("w1")
    old = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    old_registry = daemon.registry
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))

    def handoff(worker_id, verdict):
        assert verdict["state"] == "exhausted"
        daemon.registry.claim_worker_handoff(daemon.registry.get_worker(worker_id), generation=1)
        return {"status": "pending", "phase": "claimed", "reason": "target_preflight_pending"}

    daemon._get_orchestrator.return_value.handoff_worker.side_effect = handoff
    daemon.check_workers()
    assert daemon.registry.get_worker("w1")["status"] == "handoff_pending"
    assert daemon.registry.get_worker_handoff("w1")["phase"] == "claimed"
    assert daemon.registry._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None
    with pytest.raises(RuntimeError, match="missing"):
        old_registry.release_worker_operation("w1", old["nonce"], worker)
    daemon.tmux.has_session.assert_not_called()


def test_dead_started_operation_is_reaped_even_when_native_log_missing(tmp_path):
    daemon, native_id = _daemon(tmp_path)
    worker = daemon.registry.get_worker("w1")
    lease = daemon.registry.acquire_worker_operation(worker, operation="worker_mutation")
    daemon.registry.mark_worker_operation_effects_started(lease, worker)
    daemon.registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    daemon.registry._conn.commit()
    daemon.registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    _dead_worker_seams(daemon)
    daemon.check_workers()
    assert not any(lease["nonce"] in call.args[0]
                   for call in daemon.slack.post_message.call_args_list)
    daemon._finalize_and_release_worker.assert_called_once_with(
        "w1", reason="session ended", terminal=True,
    )
    daemon._get_orchestrator.assert_not_called()
    assert daemon.registry._conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='worker_operation_reaped'").fetchone()[0] == 1
