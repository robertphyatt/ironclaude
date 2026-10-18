"""Native quota evidence must survive incremental reads without inventing recovery."""
import json

import pytest

from ironclaude import worker_quota as quota_module
from ironclaude.worker_quota import read_native_quota

T0 = "2026-10-05T02:40:00.124Z"
T1 = "2026-10-05T02:41:00.124Z"
T2 = "2026-10-05T02:42:00.124Z"
T3 = "2026-10-05T02:43:00.124Z"
T4 = "2026-10-05T02:44:00.124Z"
T5 = "2026-10-05T02:45:00.124Z"
T6 = "2026-10-05T02:46:00.124Z"


def _write(path, *items):
    path.write_bytes(b"".join(json.dumps(item).encode() + b"\n" for item in items))


def _append(path, *items):
    with path.open("ab") as stream:
        for item in items:
            stream.write(json.dumps(item).encode() + b"\n")


def _claude(**overrides):
    record = {
        "type": "assistant", "uuid": "event-1", "timestamp": T0,
        "session_id": "s1", "sessionId": "s1", "cwd": "/worktree",
        "message": {"model": "<synthetic>", "role": "assistant",
                    "content": [{"type": "text", "text": "sanitized"}]},
        "error": "rate_limit", "isApiErrorMessage": True, "apiErrorStatus": 429,
        "quotaLimits": {
            "status": "rejected", "resetsAt": 1791277200,
            "rateLimitType": "seven_day",
            "unifiedRateLimitFallbackAvailable": False,
            "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled",
            "isUsingOverage": False,
        },
    }
    record.update(overrides)
    return record


def _claude_success(**overrides):
    record = {
        "type": "assistant", "uuid": "event-2", "timestamp": T1,
        "session_id": "s1", "sessionId": "s1", "cwd": "/worktree",
        "message": {"model": "claude-opus-4-1", "role": "assistant",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 15, "output_tokens": 8},
                    "content": [{"type": "text", "text": "done"}]},
    }
    record.update(overrides)
    return record


def _codex(kind, *, timestamp=T0, **payload):
    return {"type": "event_msg", "timestamp": timestamp,
            "payload": {"type": kind, **payload}}


def _codex_meta():
    return {"type": "session_meta", "payload": {"id": "s1", "cwd": "/worktree"}}


def _codex_count(reached, output, *, timestamp=T1, limit_id="codex"):
    return _codex(
        "token_count", timestamp=timestamp,
        info={"total_token_usage": {
            "input_tokens": 100, "cached_input_tokens": 0,
            "output_tokens": output, "reasoning_output_tokens": 0,
            "total_tokens": 100 + output}},
        rate_limits={"limit_id": limit_id, "rate_limit_reached_type": reached},
    )


def _read(path, client="claude", **kwargs):
    return read_native_quota(
        path, client=client, native_session_id="s1",
        workspace_path="/worktree", generation="g1", **kwargs)


def _resume(path, prior, client="claude", **kwargs):
    return _read(path, client=client, cursor=prior["cursor"],
                 reader_state=prior["reader_state"], **kwargs)


def test_claude_current_quota_survives_prompt_eof_and_restart(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    first = _read(path)
    assert first["state"] == "exhausted"
    assert first["limit_type"] == "seven_day"
    assert first["event_key"] == "claude:event-1:0"
    _append(path, {"type": "user", "timestamp": T1, "session_id": "s1",
                   "sessionId": "s1", "cwd": "/worktree", "message": {"role": "user"}})
    second = _resume(path, json.loads(json.dumps(first)))
    assert second["state"] == "exhausted"
    assert _resume(path, second)["state"] == "exhausted"
    _append(path, _claude_success())
    assert _resume(path, second)["state"] == "available"


def test_claude_recovery_requires_native_success_not_bare_assistant(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude(), _claude_success(message={"model": "claude", "role": "assistant"}))
    assert _read(path)["state"] == "exhausted"
    _write(path, _claude(), _claude_success(timestamp="x"))
    assert _read(path)["state"] == "unknown"
    _write(path, _claude(), _claude_success(timestamp=T0))
    assert _read(path)["state"] == "unknown"
    _write(path, _claude(), _claude_success(message={
        "model": "", "role": "assistant", "usage": {"output_tokens": -1}}))
    assert _read(path)["state"] == "exhausted"


@pytest.mark.parametrize("stop_reason", ["tool_use", "stop_sequence"])
def test_claude_terminal_tool_use_with_positive_usage_proves_availability(tmp_path, stop_reason):
    path = tmp_path / "claude.jsonl"
    tool_response = _claude_success(message={**_claude_success()["message"],
                                             "stop_reason": stop_reason})
    _write(path, _claude(), tool_response)
    result = _read(path)
    assert result["state"] == "available"
    assert result["event_timestamp"] == "2026-10-05T02:41:00.124000+00:00"


@pytest.mark.parametrize("stop_reason", [None, "", "not_complete", []])
def test_claude_partial_positive_usage_never_clears_old_quota(tmp_path, stop_reason):
    path = tmp_path / "claude.jsonl"
    partial = _claude_success(message={**_claude_success()["message"],
                                       "stop_reason": stop_reason})
    _write(path, _claude())
    first = _read(path)
    _append(path, partial)
    result = _resume(path, first)
    assert result["state"] == "unknown"
    assert result["exhausted"] is False
    assert result["event_timestamp"] == first["event_timestamp"]
    assert _resume(path, json.loads(json.dumps(result)))["state"] == "unknown"


def test_claude_new_native_api_error_invalidates_prior_success(tmp_path):
    path = tmp_path / "claude.jsonl"
    native_error = _claude(uuid="error-2", timestamp=T2, error="server_overloaded")
    _write(path, _claude_success(), native_error)
    result = _read(path)
    assert result["state"] == "unknown"
    assert result["event_timestamp"] == "2026-10-05T02:41:00.124000+00:00"
    assert _resume(path, json.loads(json.dumps(result)))["state"] == "unknown"


@pytest.mark.parametrize("mutation", ["wrong_session", "stale_time"])
def test_claude_partial_output_cannot_cross_identity_or_time(tmp_path, mutation):
    path = tmp_path / "claude.jsonl"
    partial = _claude_success(message={**_claude_success()["message"], "stop_reason": None})
    if mutation == "wrong_session":
        partial["sessionId"] = "foreign"
    else:
        partial["timestamp"] = T0
    _write(path, _claude(), partial)
    result = _read(path)
    assert result["state"] == "unknown"
    assert result["exhausted"] is False


@pytest.mark.parametrize("record", [
    _claude(sessionId="other"),
    _claude(timestamp="x"),
    _claude(timestamp=None),
    _claude(quotaLimits={**_claude()["quotaLimits"], "rateLimitType": "future"}),
    _claude(quotaLimits={**_claude()["quotaLimits"], "resetsAt": "soon"}),
    _claude(quotaLimits={**_claude()["quotaLimits"], "unifiedRateLimitFallbackAvailable": True}),
    _claude(isApiErrorMessage=False),
])
def test_claude_ambiguous_quota_is_unknown(tmp_path, record):
    path = tmp_path / "claude.jsonl"
    _write(path, record)
    assert _read(path)["state"] == "unknown"


@pytest.mark.parametrize("malformed", [["seven_day"], {"type": "seven_day"}])
def test_claude_malformed_rate_limit_type_invalidates_prior_exhaustion(tmp_path, malformed):
    path = tmp_path / "claude.jsonl"
    _write(
        path,
        _claude(),
        _claude(uuid="malformed-latest", timestamp=T1,
                quotaLimits={**_claude()["quotaLimits"], "rateLimitType": malformed}),
    )
    result = _read(path)
    assert result["state"] == "unknown"
    assert result["exhausted"] is False


@pytest.mark.parametrize("malformed", [["rate_limit_reached"], {"type": "rate_limit_reached"}])
def test_codex_malformed_reached_type_invalidates_prior_exhaustion(tmp_path, malformed):
    path = tmp_path / "codex.jsonl"
    _write(
        path, _codex_meta(), _codex("task_started", turn_id="quota-turn"),
        _codex_count("rate_limit_reached", 20),
        _codex_count(malformed, 20, timestamp=T2),
    )
    result = _read(path, "codex")
    assert result["state"] == "unknown"
    assert result["exhausted"] is False


def test_quoted_or_generic_claude_error_never_exhausts(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, {"type": "assistant", "message": {"model": "claude",
           "content": [{"text": json.dumps(_claude())}]}},
           _claude(error="server_overloaded"),
           _claude(error="network"),
           _claude(error="auth"))
    assert _read(path)["state"] == "unknown"


def test_codex_cursor_restores_metadata_turn_and_exhaustion(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("workspace_owner_credits_depleted", 20))
    first = _read(path, "codex")
    assert first["state"] == "exhausted"
    assert first["reader_state"]["metadata_ok"] is True
    assert first["reader_state"]["turn_id"] == "turn-1"
    _append(path, _codex("task_complete", timestamp=T2, turn_id="turn-1",
                         last_agent_message=""))
    second = _resume(path, json.loads(json.dumps(first)), "codex")
    assert second["state"] == "exhausted"
    assert _resume(path, second, "codex")["state"] == "exhausted"


def test_codex_recovery_needs_new_applicable_output_not_null_heartbeat(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20),
           _codex_count(None, 20, timestamp=T2))
    assert _read(path, "codex")["state"] == "exhausted"
    _append(path, _codex("task_started", timestamp=T2, turn_id="turn-2"),
            _codex_count(None, 21, timestamp=T2))
    assert _read(path, "codex")["state"] == "unknown"
    _append(path, _codex("task_complete", timestamp=T3, turn_id="turn-2"))
    assert _read(path, "codex")["state"] == "available"


@pytest.mark.parametrize("error_record", [
    _codex("error", timestamp=T1, message="quota",
           codex_error_info="usage_limit_exceeded"),
    _codex("task_complete", timestamp=T1, turn_id="turn-1",
           error={"message": "quota", "codex_error_info": "usage_limit_exceeded"}),
])
def test_codex_native_usage_error_exhausts_only_bound_turn(tmp_path, error_record):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           error_record)
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["limit_type"] == "usage_limit_exceeded"
    _write(path, _codex_meta(), error_record)
    assert _read(path, "codex")["state"] == "unknown"


def test_codex_generic_error_does_not_exhaust(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="overloaded",
                  codex_error_info="server_overloaded"))
    assert _read(path, "codex")["state"] == "unknown"


@pytest.mark.parametrize("bound", [False, True])
def test_codex_late_generic_error_invalidates_available_but_allows_new_proof(tmp_path, bound):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), *_new_codex_output_turn())
    prior = _read(path, "codex")
    assert prior["state"] == "available"
    error = _codex("error", timestamp=T4, message="server failure",
                   codex_error_info="server_overloaded",
                   **({"turn_id": "turn-2"} if bound else {}))
    _append(path, error)
    uncertain = _resume(path, prior, "codex")
    assert uncertain["state"] == "unknown"
    assert uncertain["event_timestamp"] == prior["event_timestamp"]
    assert _resume(path, json.loads(json.dumps(uncertain)), "codex")["state"] == "unknown"
    _append(path, *_new_codex_output_turn(turn_id="turn-3", started=T5,
                                          context_timestamp=T5, output_timestamp=T5,
                                          completed=T5))
    recovered = _resume(path, uncertain, "codex")
    assert recovered["state"] == "available"
    assert recovered["event_timestamp"] == "2026-10-05T02:45:00.124000+00:00"


def test_codex_foreign_turn_usage_error_is_unknown_not_active_quota(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex("error", timestamp=T2, turn_id="turn-1", message="quota",
                  codex_error_info="usage_limit_exceeded"))
    foreign = _read(path, "codex")
    assert foreign["state"] == "unknown"
    assert foreign["exhausted"] is False
    assert foreign["event_timestamp"] is None
    _append(path, _codex("task_complete", timestamp=T3, turn_id="turn-2"),
            *_new_codex_output_turn(turn_id="turn-3", started=T4,
                                    context_timestamp=T4, output_timestamp=T4,
                                    completed=T4))
    assert _resume(path, json.loads(json.dumps(foreign)), "codex")["state"] == "available"


@pytest.mark.parametrize("bound", [False, True])
def test_codex_current_turn_usage_error_remains_authoritative(tmp_path, bound):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded",
                  **({"turn_id": "turn-1"} if bound else {})))
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["event_timestamp"] == "2026-10-05T02:41:00.124000+00:00"


@pytest.mark.parametrize("failure", ["abort", "failed_complete"])
def test_codex_failed_output_cannot_be_next_turn_numeric_baseline(tmp_path, failure):
    path = tmp_path / "codex.jsonl"
    failed_tail = (_codex("turn_aborted", timestamp=T3, turn_id="turn-2") if failure == "abort"
                   else _codex("task_complete", timestamp=T3, turn_id="turn-2",
                               error={"message": "failed"}))
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex_count(None, 21, timestamp=T2), failed_tail)
    failed = _read(path, "codex")
    assert failed["state"] == "unknown"
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count(None, 21, timestamp=T4),
            _codex("task_complete", timestamp=T5, turn_id="turn-3"))
    heartbeat = _resume(path, json.loads(json.dumps(failed)), "codex")
    assert heartbeat["state"] == "unknown"
    assert heartbeat["event_timestamp"] == failed["event_timestamp"]
    _append(path, _codex("task_started", timestamp=T5, turn_id="turn-4"),
            _codex_count(None, 22, timestamp=T5),
            _codex("task_complete", timestamp=T6, turn_id="turn-4"))
    genuine = _resume(path, heartbeat, "codex")
    assert genuine["state"] == "available"
    assert genuine["event_timestamp"] == "2026-10-05T02:46:00.124000+00:00"


@pytest.mark.parametrize("reached", ["rate_limit_reached", None, "null_scope"])
def test_codex_foreign_token_count_cannot_reach_or_recover_active_turn(tmp_path, reached):
    path = tmp_path / "codex.jsonl"
    foreign = _codex_count(None if reached == "null_scope" else reached, 21, timestamp=T2)
    if reached == "null_scope":
        foreign["payload"]["rate_limits"] = None
    foreign["payload"]["turn_id"] = "turn-1"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"), foreign)
    result = _read(path, "codex")
    assert result["state"] == "unknown"
    assert result["event_timestamp"] == "2026-10-05T02:41:00.124000+00:00"
    assert result["reader_state"]["last_output_tokens"] == 20
    assert result["reader_state"]["candidate_numeric_ok"] is False
    _append(path, _codex("task_complete", timestamp=T3, turn_id="turn-2"),
            _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count(None, 21, timestamp=T4),
            _codex("task_complete", timestamp=T5, turn_id="turn-3"))
    assert _resume(path, json.loads(json.dumps(result)), "codex")["state"] == "available"


def test_codex_legacy_candidate_without_turn_baseline_cannot_invent_numeric_proof(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"))
    started = _read(path, "codex")
    legacy = json.loads(json.dumps(started["reader_state"]))
    legacy.pop("candidate_output_baseline")
    _append(path, _codex_count(None, 21, timestamp=T2),
            _codex("task_complete", timestamp=T3, turn_id="turn-2"))
    ambiguous = _read(path, "codex", cursor=started["cursor"], reader_state=legacy)
    assert ambiguous["state"] == "unknown"
    assert _resume(path, json.loads(json.dumps(ambiguous)), "codex")["state"] == "unknown"
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count(None, 22, timestamp=T4),
            _codex("task_complete", timestamp=T5, turn_id="turn-3"))
    assert _resume(path, ambiguous, "codex")["state"] == "available"


def test_codex_other_bucket_count_never_becomes_current_turn_baseline(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex_count(None, 21, timestamp=T2, limit_id="other"))
    foreign_bucket = _read(path, "codex")
    assert foreign_bucket["reader_state"]["last_output_tokens"] == 20
    _append(path, _codex("turn_aborted", timestamp=T3, turn_id="turn-2"))
    prior = _resume(path, foreign_bucket, "codex")
    assert prior["reader_state"]["last_output_tokens"] is None
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count(None, 21, timestamp=T4),
            _codex("task_complete", timestamp=T5, turn_id="turn-3"))
    uncertain = _resume(path, prior, "codex")
    assert uncertain["state"] == "unknown"
    _append(path, _codex("task_started", timestamp=T5, turn_id="turn-4"),
            _codex_count(None, 22, timestamp=T5),
            _codex("task_complete", timestamp=T6, turn_id="turn-4"))
    assert _resume(path, uncertain, "codex")["state"] == "available"


@pytest.mark.parametrize("failure", ["abort", "failed_complete", "generic_error"])
def test_codex_null_scope_output_is_observed_but_never_proves_recovery(tmp_path, failure):
    path = tmp_path / "codex.jsonl"
    null_scope = _codex_count(None, 21, timestamp=T2)
    null_scope["payload"]["rate_limits"] = None
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"), null_scope)
    partial = _read(path, "codex")
    assert partial["state"] == "unknown"
    assert partial["reader_state"]["last_output_tokens"] == 21
    assert not partial["reader_state"]["candidate_scope_ok"]
    assert not partial["reader_state"]["candidate_numeric_ok"]
    tail = ([_codex("turn_aborted", timestamp=T3, turn_id="turn-2")]
            if failure == "abort" else
            [_codex("task_complete", timestamp=T3, turn_id="turn-2", error={"message": "failed"})]
            if failure == "failed_complete" else
            [_codex("error", timestamp=T3, message="failed", codex_error_info="server_overloaded"),
             _codex("task_complete", timestamp=T3, turn_id="turn-2")])
    _append(path, *tail)
    failed = _resume(path, json.loads(json.dumps(partial)), "codex")
    assert failed["state"] == "unknown"
    assert failed["reader_state"]["last_output_tokens"] is None
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count(None, 21, timestamp=T4),
            _codex("task_complete", timestamp=T5, turn_id="turn-3"))
    heartbeat = _resume(path, json.loads(json.dumps(failed)), "codex")
    assert heartbeat["state"] == "unknown"
    _append(path, _codex("task_started", timestamp=T5, turn_id="turn-4"),
            _codex_count(None, 22, timestamp=T5),
            _codex("task_complete", timestamp=T6, turn_id="turn-4"))
    assert _resume(path, heartbeat, "codex")["state"] == "available"


def test_codex_failed_turn_without_count_invalidates_next_numeric_baseline(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex("token_count", timestamp=T2, info=None, rate_limits=None),
           _codex("turn_aborted", timestamp=T3, turn_id="turn-2"))
    failed = _read(path, "codex")
    assert failed["state"] == "unknown"
    assert failed["reader_state"]["quota_output_tokens"] == 20
    assert failed["reader_state"]["last_output_tokens"] is None
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count(None, 21, timestamp=T4),
            _codex("task_complete", timestamp=T5, turn_id="turn-3"))
    assert _resume(path, failed, "codex")["state"] == "unknown"


def test_codex_late_usage_error_without_active_turn_is_unknown(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), *_new_codex_output_turn())
    first = _read(path, "codex")
    _append(path, _codex("error", timestamp=T4, message="quota",
                         codex_error_info="usage_limit_exceeded"))
    late = _resume(path, first, "codex")
    assert late["state"] == "unknown"
    assert late["event_timestamp"] == first["event_timestamp"]
    assert _resume(path, json.loads(json.dumps(late)), "codex")["state"] == "unknown"


@pytest.mark.parametrize("terminal", ["turn_aborted", "failed_complete"])
def test_codex_new_failed_turn_without_output_invalidates_old_available(tmp_path, terminal):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), *_new_codex_output_turn())
    prior = _read(path, "codex")
    assert prior["state"] == "available"
    failed = (_codex("turn_aborted", timestamp=T5, turn_id="turn-3")
              if terminal == "turn_aborted" else
              _codex("task_complete", timestamp=T5, turn_id="turn-3", error={"message": "failed"}))
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"), failed)
    uncertain = _resume(path, json.loads(json.dumps(prior)), "codex")
    assert uncertain["state"] == "unknown"
    assert uncertain["event_timestamp"] == prior["event_timestamp"]
    assert _resume(path, json.loads(json.dumps(uncertain)), "codex")["state"] == "unknown"
    _append(path, *_new_codex_output_turn(turn_id="turn-4", started=T6,
                                          context_timestamp=T6, output_timestamp=T6,
                                          completed=T6))
    assert _resume(path, uncertain, "codex")["state"] == "available"


@pytest.mark.parametrize("terminal", ["turn_aborted", "failed_complete"])
def test_codex_same_turn_quota_error_survives_failed_terminal(tmp_path, terminal):
    path = tmp_path / "codex.jsonl"
    failed = (_codex("turn_aborted", timestamp=T2, turn_id="turn-1")
              if terminal == "turn_aborted" else
              _codex("task_complete", timestamp=T2, turn_id="turn-1", error={"message": "failed"}))
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           failed)
    assert _read(path, "codex")["state"] == "exhausted"


def test_codex_same_turn_scoped_quota_preserves_numeric_baseline_on_abort(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("turn_aborted", timestamp=T2, turn_id="turn-1"))
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["reader_state"]["quota_output_tokens"] == 20
    assert result["reader_state"]["last_output_tokens"] == 20


def test_codex_stray_assistant_item_without_turn_does_not_corrupt_reader(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), {
        "type": "response_item", "timestamp": T1,
        "payload": {"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "stray"}]},
    })
    result = _read(path, "codex")
    assert result["state"] == "ordinary"
    assert _resume(path, json.loads(json.dumps(result)), "codex")["state"] == "ordinary"


def test_codex_usage_error_recovers_only_after_new_native_output(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count(None, 20),
           _codex("error", timestamp=T1, message="quota",
                  codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex_count(None, 20, timestamp=T2))
    assert _read(path, "codex")["state"] == "exhausted"
    _append(path, _codex_count(None, 21, timestamp=T2))
    assert _read(path, "codex")["state"] == "unknown"
    _append(path, _codex("task_complete", timestamp=T3, turn_id="turn-2"))
    assert _read(path, "codex")["state"] == "available"


def _new_codex_output_turn(*, scope="codex", role="assistant", context_turn="turn-2",
                           completion="task_complete", error=None, include_scope=True,
                           include_context=True, include_output=True, include_complete=True,
                           output_turn=None, output_timestamp=T2, context_timestamp=T2,
                           turn_id="turn-2", started=T2, completed=T3):
    records = [_codex("task_started", timestamp=started, turn_id=turn_id)]
    if include_context:
        records.append({"type": "turn_context", "timestamp": context_timestamp,
                        "payload": {"turn_id": context_turn if turn_id == "turn-2" else turn_id,
                                    "model": "gpt-6-sol"}})
    if include_scope:
        records.append(_codex("token_count", timestamp=started, info=None,
                              rate_limits={"limit_id": scope, "rate_limit_reached_type": None}))
    if include_output:
        records.append({"type": "response_item", "timestamp": output_timestamp,
                        "payload": {"type": "message", "role": role,
                                    **({"turn_id": output_turn} if output_turn else {}),
                                    "content": [{"type": "output_text", "text": "completed work"}]}})
    if include_complete:
        records.append(_codex(completion, timestamp=completed, turn_id=turn_id, error=error))
    return records


def test_codex_no_numeric_baseline_recovers_from_new_completed_scoped_output_after_restart(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"))
    exhausted = _read(path, "codex")
    assert exhausted["state"] == "exhausted"
    assert exhausted["reader_state"]["quota_output_tokens"] is None
    _append(path, *_new_codex_output_turn())

    recovered = _resume(path, json.loads(json.dumps(exhausted)), "codex")

    assert recovered["state"] == "available"
    assert _resume(path, recovered, "codex")["state"] == "available"


def test_codex_no_baseline_output_proof_survives_chunk_boundary(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"))
    first = _read(path, "codex")
    records = _new_codex_output_turn()
    _append(path, *records[:-1])
    pending = _resume(path, first, "codex")
    assert pending["state"] == "unknown"
    assert pending["exhausted"] is False  # Controller must not stop source on old quota.
    assert pending["reader_state"]["candidate_output"] is True
    _append(path, records[-1])
    assert _resume(path, json.loads(json.dumps(pending)), "codex")["state"] == "available"


def test_codex_numeric_baseline_new_output_is_unknown_until_proved_across_restart(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"))
    first = _read(path, "codex")
    assert first["state"] == "exhausted"
    assert first["reader_state"]["quota_output_tokens"] == 20
    records = _new_codex_output_turn()
    _append(path, *records[:-1])
    partial = _resume(path, json.loads(json.dumps(first)), "codex")
    assert partial["state"] == "unknown"
    assert partial["exhausted"] is False
    assert _resume(path, json.loads(json.dumps(partial)), "codex")["state"] == "unknown"
    _append(path, records[-1])
    assert _resume(path, partial, "codex")["state"] == "available"


@pytest.mark.parametrize("change", [
    {"role": "user"}, {"output_turn": "turn-1"}, {"output_timestamp": T0},
])
def test_codex_numeric_baseline_unbound_or_stale_output_keeps_exhaustion(tmp_path, change):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           *_new_codex_output_turn(include_complete=False, **change))
    result = _read(path, "codex")
    assert result["state"] == "exhausted"


def test_codex_numeric_baseline_new_quota_overrides_partial_output(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           *_new_codex_output_turn(include_complete=False),
           _codex("error", timestamp=T3, message="new quota", codex_error_info="usage_limit_exceeded"))
    assert _read(path, "codex")["state"] == "exhausted"


def test_codex_numeric_delta_still_proves_availability_after_partial_output(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           *_new_codex_output_turn(include_complete=False))
    partial = _read(path, "codex")
    assert partial["state"] == "unknown"
    _append(path, _codex_count(None, 21, timestamp=T3))
    numeric = _resume(path, partial, "codex")
    assert numeric["state"] == "unknown"
    _append(path, _codex("task_complete", timestamp=T4, turn_id="turn-2"))
    recovered = _resume(path, numeric, "codex")
    assert recovered["state"] == "available"
    assert recovered["event_timestamp"] == "2026-10-05T02:44:00.124000+00:00"
    assert _resume(path, json.loads(json.dumps(recovered)), "codex")["state"] == "available"


def test_codex_baseline_output_before_new_count_is_unknown_at_eof(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"))
    exhausted = _read(path, "codex")
    new_turn = _new_codex_output_turn(include_scope=False, include_complete=False)
    _append(path, *new_turn)
    partial = _resume(path, exhausted, "codex")
    assert partial["state"] == "unknown"
    assert partial["exhausted"] is False
    assert _resume(path, json.loads(json.dumps(partial)), "codex")["state"] == "unknown"
    _append(path, _codex_count(None, 21, timestamp=T3))
    numeric = _resume(path, partial, "codex")
    assert numeric["state"] == "unknown"
    _append(path, _codex("task_complete", timestamp=T4, turn_id="turn-2"))
    recovered = _resume(path, numeric, "codex")
    assert recovered["state"] == "available"
    assert _resume(path, json.loads(json.dumps(recovered)), "codex")["state"] == "available"


def test_codex_authoritative_event_timestamp_ignores_newer_heartbeat(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex_count(None, 20, timestamp=T2))
    exhausted = _read(path, "codex")
    assert exhausted["state"] == "exhausted"
    assert exhausted["event_timestamp"] == exhausted["reader_state"]["event_timestamp"] == "2026-10-05T02:41:00.124000+00:00"
    assert _resume(path, json.loads(json.dumps(exhausted)), "codex")["event_timestamp"] == exhausted["event_timestamp"]


def test_codex_second_clean_success_refreshes_authoritative_timestamp(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), *_new_codex_output_turn())
    first = _read(path, "codex")
    assert first["state"] == "available"
    assert first["event_timestamp"] == "2026-10-05T02:43:00.124000+00:00"
    _append(path, *_new_codex_output_turn(turn_id="turn-3", started=T4,
                                          context_timestamp=T4, output_timestamp=T4,
                                          completed=T4))
    second = _resume(path, json.loads(json.dumps(first)), "codex")
    assert second["state"] == "available"
    assert second["event_timestamp"] == "2026-10-05T02:44:00.124000+00:00"
    assert _resume(path, second, "codex")["event_timestamp"] == second["event_timestamp"]
    _append(path, _codex_count(None, 99, timestamp=T5))
    heartbeat = _resume(path, second, "codex")
    assert heartbeat["state"] == "available"
    assert heartbeat["event_timestamp"] == second["event_timestamp"]


@pytest.mark.parametrize("tail", [
    [_codex("turn_aborted", timestamp=T3, turn_id="turn-2")],
    [_codex("task_complete", timestamp=T3, turn_id="turn-2", error={"message": "failed"})],
    [_codex("error", timestamp=T3, message="failed", codex_error_info="server_overloaded"),
     _codex("task_complete", timestamp=T3, turn_id="turn-2")],
])
def test_codex_numeric_delta_requires_clean_completion(tmp_path, tail):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex_count(None, 21, timestamp=T2))
    partial = _read(path, "codex")
    assert partial["state"] == "unknown"
    assert partial["event_timestamp"] == "2026-10-05T02:41:00.124000+00:00"
    assert partial["reader_state"]["candidate_numeric_ok"] is True
    assert _resume(path, json.loads(json.dumps(partial)), "codex")["state"] == "unknown"
    _append(path, *tail)
    result = _resume(path, partial, "codex")
    assert result["state"] == "unknown"
    assert result["event_timestamp"] == partial["event_timestamp"]


def test_codex_numeric_delta_clean_completion_needs_no_response_context(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20, timestamp=T1),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           _codex("task_started", timestamp=T2, turn_id="turn-2"),
           _codex_count(None, 21, timestamp=T2))
    partial = _read(path, "codex")
    assert partial["state"] == "unknown"
    _append(path, _codex("task_complete", timestamp=T3, turn_id="turn-2"))
    recovered = _resume(path, partial, "codex")
    assert recovered["state"] == "available"
    assert recovered["event_timestamp"] == "2026-10-05T02:43:00.124000+00:00"


def test_codex_ordinary_complete_scoped_output_is_authoritative_available(tmp_path):
    path = tmp_path / "codex.jsonl"
    records = _new_codex_output_turn()
    _write(path, _codex_meta(), *records[:-1])
    partial = _read(path, "codex")
    assert partial["state"] == "unknown"
    assert partial["event_timestamp"] is None
    _append(path, records[-1])
    available = _resume(path, json.loads(json.dumps(partial)), "codex")
    assert available["state"] == "available"
    assert available["event_timestamp"] == available["reader_state"]["event_timestamp"] == "2026-10-05T02:43:00.124000+00:00"


@pytest.mark.parametrize("change", [
    {"include_complete": False}, {"include_scope": False}, {"role": "user"},
    {"completion": "turn_aborted"}, {"error": {"message": "failed"}},
    {"output_timestamp": T0},
])
def test_codex_ordinary_unproved_output_cannot_emit_available_timestamp(tmp_path, change):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), *_new_codex_output_turn(**change))
    result = _read(path, "codex")
    assert result["state"] != "available"
    assert result["event_timestamp"] is None


def test_legacy_no_baseline_reader_state_treats_new_output_as_unknown(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"))
    first = _read(path, "codex")
    legacy = json.loads(json.dumps(first["reader_state"]))
    for key in ("quota_turn_id", "candidate_turn_id", "candidate_started_at",
                "candidate_last_timestamp", "candidate_context_ok", "candidate_output",
                "candidate_scope_ok", "candidate_error"):
        legacy.pop(key)
    _append(path, *_new_codex_output_turn())
    result = _read(path, "codex", cursor=first["cursor"], reader_state=legacy)
    assert result["state"] == "unknown"
    assert _resume(path, result, "codex")["state"] == "unknown"


def test_codex_valid_later_turn_recovers_after_ambiguous_completed_output(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"),
           *_new_codex_output_turn(include_scope=False))
    ambiguous = _read(path, "codex")
    assert ambiguous["state"] == "unknown"
    _append(path, *_new_codex_output_turn(turn_id="turn-3", started=T4,
                                          context_timestamp=T4, output_timestamp=T4,
                                          completed=T4))
    assert _resume(path, ambiguous, "codex")["state"] == "available"


def test_codex_new_quota_error_invalidates_earlier_candidate_output(tmp_path):
    path = tmp_path / "codex.jsonl"
    records = _new_codex_output_turn(include_complete=False)
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"), *records,
           _codex("error", timestamp=T3, message="quota again", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T3, turn_id="turn-2"))
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["reader_state"]["quota_turn_id"] == "turn-2"


@pytest.mark.parametrize("change,expected", [
    ({"include_complete": False}, "unknown"),
    ({"include_output": False}, "exhausted"),
    ({"role": "user"}, "exhausted"),
    ({"completion": "turn_aborted"}, "unknown"),
    ({"error": {"message": "failure"}}, "unknown"),
    ({"include_scope": False}, "unknown"),
    ({"scope": "other"}, "unknown"),
    ({"include_context": False}, "unknown"),
    ({"context_turn": "turn-1"}, "unknown"),
    ({"output_turn": "turn-1"}, "exhausted"),
    ({"output_timestamp": T0}, "exhausted"),
    ({"context_timestamp": T0}, "unknown"),
])
def test_codex_no_baseline_output_does_not_falsely_recover(tmp_path, change, expected):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("error", timestamp=T1, message="quota", codex_error_info="usage_limit_exceeded"),
           _codex("task_complete", timestamp=T1, turn_id="turn-1"))
    first = _read(path, "codex")
    _append(path, *_new_codex_output_turn(**change))
    assert _resume(path, first, "codex")["state"] == expected


def test_codex_null_rate_limits_do_not_hide_later_native_quota(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("token_count", timestamp=T1, info=None, rate_limits=None),
           _codex_count("rate_limit_reached", 20, timestamp=T2))
    assert _read(path, "codex")["state"] == "exhausted"


@pytest.mark.parametrize("evidence", ["context", "output"])
@pytest.mark.parametrize("quota", ["count", "error"])
def test_codex_stale_quota_after_fresh_candidate_evidence_cannot_exhaust(tmp_path, evidence, quota):
    path = tmp_path / "codex.jsonl"
    fresh = ({"type": "turn_context", "timestamp": T4,
              "payload": {"turn_id": "turn-2", "model": "gpt-6-sol"}}
             if evidence == "context" else
             {"type": "response_item", "timestamp": T4,
              "payload": {"type": "message", "role": "assistant", "turn_id": "turn-2",
                          "content": [{"type": "output_text", "text": "fresh response"}]}})
    stale = (_codex_count("rate_limit_reached", 21, timestamp=T3)
             if quota == "count" else
             _codex("error", timestamp=T3, message="quota",
                    codex_error_info="usage_limit_exceeded"))
    _write(path, _codex_meta(), _codex("task_started", timestamp=T2, turn_id="turn-2"), fresh)
    first = _read(path, "codex")
    assert first["reader_state"]["last_timestamp"] == "2026-10-05T02:44:00.124000+00:00"
    _append(path, stale)
    rejected = _resume(path, json.loads(json.dumps(first)), "codex")
    assert rejected["state"] == "unknown"
    assert rejected["exhausted"] is False
    assert rejected["reason"] == "invalid_native_timestamp"
    assert _resume(path, json.loads(json.dumps(rejected)), "codex")["state"] == "unknown"


@pytest.mark.parametrize("quota", ["count", "error"])
def test_codex_newer_matching_quota_after_fresh_output_remains_authoritative(tmp_path, quota):
    path = tmp_path / "codex.jsonl"
    native_quota = (_codex_count("rate_limit_reached", 21, timestamp=T5)
                    if quota == "count" else
                    _codex("error", timestamp=T5, message="quota",
                           codex_error_info="usage_limit_exceeded"))
    _write(path, _codex_meta(), _codex("task_started", timestamp=T2, turn_id="turn-2"),
           {"type": "response_item", "timestamp": T4,
            "payload": {"type": "message", "role": "assistant", "turn_id": "turn-2",
                        "content": [{"type": "output_text", "text": "fresh response"}]}},
           native_quota)
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["event_timestamp"] == "2026-10-05T02:45:00.124000+00:00"


def test_codex_legacy_candidate_timestamp_seeds_global_watermark_on_restart(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", timestamp=T2, turn_id="turn-2"),
           {"type": "turn_context", "timestamp": T4,
            "payload": {"turn_id": "turn-2", "model": "gpt-6-sol"}})
    first = _read(path, "codex")
    legacy = json.loads(json.dumps(first["reader_state"]))
    legacy["last_timestamp"] = "2026-10-05T02:42:00.124000+00:00"
    legacy.pop("observed_model")
    legacy.pop("candidate_model")
    _append(path, _codex_count("rate_limit_reached", 21, timestamp=T3))
    result = _read(path, "codex", cursor=first["cursor"], reader_state=legacy)
    assert result["state"] == "unknown"
    assert result["reason"] == "invalid_native_timestamp"
    assert result["reader_state"]["last_timestamp"] == "2026-10-05T02:44:00.124000+00:00"
    assert result["observed_model"] is None


def test_claude_observed_model_is_native_success_model(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude(), _claude_success(message={**_claude_success()["message"],
                                              "model": "claude-sonnet-4-5"}))
    result = _read(path)
    assert result["observed_model"] == "claude-sonnet-4-5"
    assert result["reader_state"]["observed_model"] == "claude-sonnet-4-5"
    assert _resume(path, json.loads(json.dumps(result)))["observed_model"] == "claude-sonnet-4-5"


def test_codex_observed_model_binds_current_proven_turn_and_does_not_leak(tmp_path):
    path = tmp_path / "codex.jsonl"
    first_turn = _new_codex_output_turn()
    first_turn[1]["payload"]["model"] = "gpt-6-sol"
    first_turn.insert(-1, _codex_count(None, 20, timestamp=T2))
    _write(path, _codex_meta(), *first_turn)
    first = _read(path, "codex")
    assert first["state"] == "available"
    assert first["observed_model"] == "gpt-6-sol"
    _append(path, _codex("task_started", timestamp=T4, turn_id="turn-3"),
            _codex_count("rate_limit_reached", 20, timestamp=T4),
            _codex("task_complete", timestamp=T4, turn_id="turn-3"))
    quota = _resume(path, json.loads(json.dumps(first)), "codex")
    assert quota["state"] == "exhausted"
    assert quota["observed_model"] is None
    _append(path, _codex("task_started", timestamp=T5, turn_id="turn-4"),
            _codex_count(None, 21, timestamp=T5),
            _codex("task_complete", timestamp=T6, turn_id="turn-4"))
    numeric = _resume(path, quota, "codex")
    assert numeric["state"] == "available"
    assert numeric["observed_model"] is None
    assert numeric["reader_state"]["observed_model"] is None
    next_turn = _new_codex_output_turn(turn_id="turn-5", started=T6,
                                       context_timestamp=T6, output_timestamp=T6,
                                       completed=T6)
    next_turn[1]["payload"]["model"] = "gpt-6-astra"
    _append(path, *next_turn)
    recovered = _resume(path, numeric, "codex")
    assert recovered["observed_model"] == "gpt-6-astra"


@pytest.mark.parametrize("client,model", [
    ("claude", "claude-sonnet-4-5"), ("codex", "gpt-6-sol"),
])
def test_observed_model_survives_budget_unknown_chunks_until_eof(tmp_path, monkeypatch,
                                                                 client, model):
    path = tmp_path / f"{client}.jsonl"
    if client == "claude":
        records = [_claude_success(message={**_claude_success()["message"], "model": model})]
        records.extend({"type": "user", "timestamp": T2, "session_id": "s1",
                        "sessionId": "s1", "cwd": "/worktree", "message": {"role": "user"}}
                       for _ in range(3))
    else:
        records = [_codex_meta(), *_new_codex_output_turn()]
        records.extend({"type": "user", "timestamp": T4, "payload": {"role": "user"}}
                       for _ in range(3))
    _write(path, *records)
    monkeypatch.setattr(quota_module, "MAX_RECORDS", 1)
    result = _read(path, client)
    for _ in range(len(records) + 1):
        if result["reason"] == "native_log_eof":
            break
        assert result["state"] == "unknown"
        assert result["observed_model"] is None
        result = _resume(path, json.loads(json.dumps(result)), client)
    assert result["reason"] == "native_log_eof"
    assert result["state"] == "available"
    assert result["observed_model"] == model
    assert result["reader_state"]["observed_model"] == model


@pytest.mark.parametrize("record", [
    _codex_count("workspace_owner_credits_depleted", 20),
    _codex_count("workspace_owner_credits_depleted", 20, timestamp="x"),
    _codex_count("future_reached_type", 20),
    _codex_count("workspace_owner_credits_depleted", 20, limit_id="other"),
])
def test_codex_missing_turn_bad_time_or_unsupported_scope_is_unknown(tmp_path, record):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), record)
    assert _read(path, "codex")["state"] == "unknown"


def test_codex_percentage_only_is_ordinary_and_metadata_required(tmp_path):
    path = tmp_path / "codex.jsonl"
    _write(path, _codex_meta(), _codex("task_started", turn_id="turn-1"),
           _codex("token_count", timestamp=T1, info={"total_token_usage": {"output_tokens": 20}},
                  rate_limits={"limit_id": "codex", "rate_limit_reached_type": None,
                               "primary": {"used_percent": 100}}))
    assert _read(path, "codex")["state"] == "ordinary"
    _write(path, _codex("task_started", turn_id="turn-1"),
           _codex_count("rate_limit_reached", 20))
    assert _read(path, "codex")["state"] == "unknown"


def test_reader_state_rejects_wrong_generation_identity_and_cursor(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    first = _read(path)
    assert _read(path, cursor=first["cursor"])["state"] == "unknown"
    assert read_native_quota(path, client="claude", native_session_id="s1",
                             workspace_path="/worktree", generation="g2",
                             cursor=first["cursor"], reader_state=first["reader_state"])["state"] == "unknown"
    assert _read(path, cursor=first["cursor"] - 1,
                 reader_state=first["reader_state"])["state"] == "unknown"
    corrupt = json.loads(json.dumps(first["reader_state"]))
    corrupt["last_line_size"] = 100_000_000
    assert _read(path, cursor=first["cursor"], reader_state=corrupt)["state"] == "unknown"
    old = tmp_path / "old.jsonl"
    path.rename(old)
    _write(path, _claude())
    assert _resume(path, first)["state"] == "unknown"
    assert _read(path, expected_file_identity=first["file_identity"])["state"] == "unknown"
    path.write_bytes(b"")
    assert _resume(path, first)["state"] == "unknown"


def test_same_inode_rewrite_of_cursor_boundary_is_rejected(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    first = _read(path)
    before = path.stat().st_ino
    data = path.read_bytes()
    path.write_bytes(data.replace(b"event-1", b"event-9"))
    assert path.stat().st_ino == before
    assert _resume(path, first)["state"] == "unknown"


def test_bounded_scan_keeps_quota_internal_until_full_eof(tmp_path, monkeypatch):
    path = tmp_path / "claude.jsonl"
    monkeypatch.setattr("ironclaude.worker_quota.MAX_RECORDS", 1)
    _write(path, _claude(), {"type": "user", "timestamp": T1})
    first = _read(path)
    assert first["state"] == "unknown"
    assert first["reader_state"]["latest_state"] == "exhausted"
    assert _resume(path, first)["state"] == "unknown"
    last = _resume(path, _resume(path, first))
    assert last["state"] == "exhausted"


def test_torn_final_line_stays_unknown_then_exhausts_once_complete(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude_success())
    first = _read(path)
    with path.open("ab") as stream:
        stream.write(json.dumps(_claude(timestamp=T2)).encode())
    held = _resume(path, first)
    assert held["state"] == "unknown" and held["reason"] == "incomplete_native_record"
    assert held["cursor"] == first["cursor"]
    with path.open("ab") as stream:
        stream.write(b"\n")
    assert _resume(path, first)["state"] == "exhausted"


def test_oversize_line_is_skipped_and_following_quota_exhausts(tmp_path):
    path = tmp_path / "claude.jsonl"
    oversize = b'{"type":"user","pad":"' + b"x" * (300 * 1024) + b'"}\n'
    path.write_bytes(oversize + json.dumps(_claude()).encode() + b"\n")
    result = _read(path)
    assert result["state"] == "exhausted"
    assert result["cursor"] == path.stat().st_size
    assert result["reader_state"]["skipped_records"] == 1


def test_oversize_skip_cursor_resumes_across_calls(tmp_path):
    path = tmp_path / "claude.jsonl"
    path.write_bytes(b"x" * (256 * 1024 + 1) + b"\n")
    first = _read(path)
    assert first["state"] == "ordinary" and first["reason"] == "native_log_eof"
    assert first["cursor"] == path.stat().st_size
    assert first["reader_state"]["skipped_records"] == 1
    assert first["reader_state"]["last_line_size"] == 256 * 1024
    _append(path, _claude())
    second = _resume(path, json.loads(json.dumps(first)))
    assert second["state"] == "exhausted"
    assert second["cursor"] == path.stat().st_size


def test_torn_oversize_line_waits_without_advancing(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude_success())
    first = _read(path)
    with path.open("ab") as stream:
        stream.write(b"x" * (256 * 1024 + 10))
    held = _resume(path, first)
    assert held["state"] == "unknown" and held["reason"] == "incomplete_native_record"
    assert held["cursor"] == first["cursor"]


@pytest.mark.parametrize("bad", [b"{bad}\n", b"[1, 2]\n", b"\x80\x81\n"])
def test_unparseable_line_is_skipped_and_counted(tmp_path, bad):
    path = tmp_path / "claude.jsonl"
    path.write_bytes(bad + json.dumps(_claude()).encode() + b"\n")
    result = _read(path)
    assert result["state"] == "exhausted"
    assert result["cursor"] == path.stat().st_size
    assert result["reader_state"]["skipped_records"] == 1


def test_unchanged_log_with_known_identity_is_not_reopened(tmp_path, monkeypatch):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    first = _read(path)
    opened = []
    real_open = open

    def counting_open(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(quota_module, "open", counting_open, raising=False)
    again = _read(path, cursor=first["cursor"], reader_state=first["reader_state"],
                  expected_file_identity=first["file_identity"])
    assert opened == []
    assert again["state"] == "exhausted" and again["cursor"] == first["cursor"]
    assert again["event_key"] == first["event_key"]
    _append(path, _claude_success(timestamp=T2))
    grown = _read(path, cursor=first["cursor"], reader_state=first["reader_state"],
                  expected_file_identity=first["file_identity"])
    assert opened == [path]
    assert grown["state"] == "available"


@pytest.mark.parametrize("window", ["five_hour", "seven_day"])
def test_both_claude_limit_windows_exhaust(tmp_path, window):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude(quotaLimits={**_claude()["quotaLimits"], "rateLimitType": window}))
    result = _read(path)
    assert result["state"] == "exhausted"
    assert result["limit_type"] == window


def test_claude_exhaustion_reports_native_reset_time(tmp_path):
    path = tmp_path / "claude.jsonl"
    _write(path, _claude())
    assert _read(path)["reset_at"] == 1791277200
    _append(path, _claude_success(timestamp=T2))
    assert _read(path)["reset_at"] is None


@pytest.mark.parametrize("primary,secondary,expected", [
    ({"used_percent": 100, "resets_at": 1791589397}, None, 1791589397),
    ({"resets_at": 1791500000}, {"resets_at": 1791589397}, 1791589397),
    ({"used_percent": 100}, None, None),
])
def test_codex_exhaustion_reports_latest_window_reset_when_present(tmp_path, primary, secondary, expected):
    path = tmp_path / "codex.jsonl"
    count = _codex_count("rate_limit_reached", 20)
    count["payload"]["rate_limits"].update({"primary": primary, "secondary": secondary})
    _write(path, _codex_meta(), _codex("task_started", turn_id="quota-turn"), count)
    result = _read(path, "codex")
    assert result["state"] == "exhausted"
    assert result["reset_at"] == expected


def test_unknown_client_and_unreadable_file_hold(tmp_path):
    path = tmp_path / "missing.jsonl"
    assert _read(path)["state"] == "unknown"
    _write(path, _claude())
    assert _read(path, client="other")["state"] == "unknown"
