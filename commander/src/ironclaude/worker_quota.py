"""Bounded, resumable reading of provider-native quota evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS = 10_000
CODEX_REACHED_TYPES = frozenset({
    "rate_limit_reached", "workspace_owner_credits_depleted",
    "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached",
    "workspace_member_usage_limit_reached",
})
CLAUDE_RATE_LIMIT_TYPES = frozenset({"five_hour", "seven_day"})


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _result(state, reason, reader):
    if reader["latest_state"] != "available":
        reader["observed_model"] = None
    return {
        "state": state, "status": "rejected" if state == "exhausted" else state,
        "exhausted": state == "exhausted", "client": reader["client"],
        "native_session_id": reader["native_session_id"],
        "generation": reader["generation"], "cursor": reader["cursor"],
        "file_identity": reader["file_identity"], "reason": reason,
        "event_key": reader["event_key"], "limit_type": reader["limit_type"],
        "event_timestamp": reader["event_timestamp"],
        "reset_at": reader["reset_at"] if state == "exhausted" else None,
        "observed_model": reader["observed_model"] if state == "available" else None,
        "reader_state": reader.copy(),
    }


def _skip_record(reader, stream, start, end):
    """Advance past one complete but unusable record (H2).

    The boundary proof keeps at most the trailing MAX_RECORD_BYTES of the record,
    so a resumed read can re-hash exactly those bytes before the cursor.
    """
    size = min(end - start, MAX_RECORD_BYTES)
    stream.seek(end - size)
    reader["last_line_hash"] = hashlib.sha256(stream.read(size)).hexdigest()
    reader["last_line_size"] = size
    reader["cursor"] = end
    reader["skipped_records"] += 1


def _codex_reset_at(limits):
    """Latest positive integer native reset time across Codex windows, if present (H9)."""
    resets = [window.get("resets_at") for window in (limits.get("primary"), limits.get("secondary"))
              if isinstance(window, dict)]
    valid = [value for value in resets if type(value) is int and value > 0]
    return max(valid) if valid else None


def _claude_identity(record, session, cwd):
    return (record.get("session_id") == session and record.get("sessionId") == session
            and (cwd is None or record.get("cwd") == cwd))


def _claude_quota(record):
    message = record.get("message")
    if not (record.get("type") == "assistant" and isinstance(message, dict)
            and message.get("model") == "<synthetic>" and record.get("error") == "rate_limit"):
        return False
    quota = record.get("quotaLimits")
    if not (record.get("isApiErrorMessage") is True and record.get("apiErrorStatus") == 429
            and isinstance(quota, dict)):
        return None
    rate_limit_type = quota.get("rateLimitType")
    if (quota.get("status") != "rejected" or not isinstance(rate_limit_type, str)
            or rate_limit_type not in CLAUDE_RATE_LIMIT_TYPES
            or type(quota.get("resetsAt")) is not int or quota["resetsAt"] <= 0
            or quota.get("unifiedRateLimitFallbackAvailable") is not False
            or quota.get("overageStatus") != "rejected" or quota.get("isUsingOverage") is not False):
        return None
    return True


def _claude_positive_output(record):
    message = record.get("message")
    return (record.get("type") == "assistant" and isinstance(message, dict)
            and isinstance(record.get("uuid"), str) and bool(record["uuid"])
            and not record.get("error") and record.get("isApiErrorMessage") is not True
            and message.get("role") == "assistant"
            and isinstance(message.get("model"), str) and bool(message["model"])
            and message["model"] != "<synthetic>"
            and isinstance(message.get("usage"), dict)
            and type(message["usage"].get("output_tokens")) is int
            and message["usage"]["output_tokens"] > 0)


def _claude_success(record):
    if not _claude_positive_output(record):
        return False
    reason = record["message"].get("stop_reason")
    return isinstance(reason, str) and reason in {"end_turn", "stop_sequence", "tool_use"}


def _claude_native_api_error(record):
    return (record.get("type") == "assistant"
            and record.get("isApiErrorMessage") is True
            and isinstance(record.get("message"), dict)
            and isinstance(record.get("error"), str) and bool(record["error"]))


def _codex_output_count(info):
    if not isinstance(info, dict):
        return None
    total = info.get("total_token_usage")
    if not isinstance(total, dict):
        return None
    count = total.get("output_tokens")
    return count if type(count) is int and count >= 0 else None


def _fresh_candidate_time(reader, record):
    when = _timestamp(record.get("timestamp"))
    started = _timestamp(reader["candidate_started_at"])
    prior = _timestamp(reader["candidate_last_timestamp"])
    global_prior = _timestamp(reader["last_timestamp"])
    if (when is None or started is None or when < started
            or (prior is not None and when < prior)
            or (global_prior is not None and when < global_prior)
            or (reader["quota_timestamp"] is not None
                and when <= _timestamp(reader["quota_timestamp"]))):
        return None
    reader["candidate_last_timestamp"] = when.isoformat()
    reader["last_timestamp"] = when.isoformat()
    return when


def _clear_candidate(reader):
    reader.update({
        "candidate_turn_id": None, "candidate_started_at": None,
        "candidate_last_timestamp": None, "candidate_context_ok": False,
        "candidate_output": False, "candidate_scope_ok": False,
        "candidate_error": False, "candidate_numeric_ok": False,
        "candidate_output_baseline": None, "candidate_model": None,
    })


def _valid_reader_state(state):
    if (state.get("latest_state") not in {"ordinary", "available", "exhausted", "unknown"}
            or type(state.get("metadata_ok")) is not bool
            or (state.get("turn_id") is not None and not isinstance(state["turn_id"], str))
            or (state.get("last_timestamp") is not None and _timestamp(state["last_timestamp"]) is None)
            or (state.get("quota_output_tokens") is not None
                and (type(state["quota_output_tokens"]) is not int or state["quota_output_tokens"] < 0))
            or (state.get("last_output_tokens") is not None
                and (type(state["last_output_tokens"]) is not int or state["last_output_tokens"] < 0))
            or (state.get("candidate_output_baseline") is not None
                and (type(state["candidate_output_baseline"]) is not int
                     or state["candidate_output_baseline"] < 0))
            or (state.get("quota_timestamp") is not None and _timestamp(state["quota_timestamp"]) is None)
            or (state.get("event_timestamp") is not None and _timestamp(state["event_timestamp"]) is None)
            or (state.get("event_key") is not None and not isinstance(state["event_key"], str))
            or (state.get("observed_model") is not None
                and (not isinstance(state["observed_model"], str) or not state["observed_model"]))
            or (state.get("candidate_model") is not None
                and (not isinstance(state["candidate_model"], str) or not state["candidate_model"]))
            or (state.get("last_line_size") is not None
                and (type(state["last_line_size"]) is not int
                     or not 0 < state["last_line_size"] <= MAX_RECORD_BYTES))
            or (state.get("last_line_hash") is not None and not isinstance(state["last_line_hash"], str))
            or (state.get("skipped_records") is not None
                and (type(state["skipped_records"]) is not int or state["skipped_records"] < 0))
            or (state.get("reset_at") is not None
                and (type(state["reset_at"]) is not int or state["reset_at"] <= 0))):
        return False
    for key in ("quota_turn_id", "candidate_turn_id"):
        if state.get(key) is not None and (not isinstance(state[key], str) or not state[key]):
            return False
    for key in ("candidate_started_at", "candidate_last_timestamp"):
        if state.get(key) is not None and _timestamp(state[key]) is None:
            return False
    if any(type(state.get(key, False)) is not bool for key in (
        "candidate_context_ok", "candidate_output", "candidate_scope_ok", "candidate_error",
        "candidate_numeric_ok")):
        return False
    if (any(state.get(key, False) for key in (
            "candidate_context_ok", "candidate_output", "candidate_scope_ok", "candidate_error",
            "candidate_numeric_ok"))
            and (not state.get("candidate_turn_id") or not state.get("candidate_started_at"))):
        return False
    if state.get("candidate_turn_id") is not None:
        started = _timestamp(state.get("candidate_started_at"))
        last = _timestamp(state.get("candidate_last_timestamp"))
        quota = _timestamp(state.get("quota_timestamp"))
        if (state["candidate_turn_id"] != state.get("turn_id")
                or state.get("latest_state") not in {"ordinary", "available", "exhausted", "unknown"}
                or started is None or (quota is not None and started <= quota)
                or last is None or last < started):
            return False
    if state.get("candidate_numeric_ok") and state.get("candidate_output_baseline") is None:
        return False
    if state.get("candidate_model") is not None and not state.get("candidate_context_ok"):
        return False
    return state["latest_state"] != "exhausted" or bool(state.get("event_key"))


def read_native_quota(
    path: str | Path, *, client: str, native_session_id: str,
    workspace_path: str | None = None, generation: str | None = None,
    cursor: int = 0, applicable_limit_id: str = "codex",
    expected_file_identity: str | None = None, reader_state: dict | None = None,
) -> dict:
    """Return a verdict and reader_state for the next call, including ordinary EOF."""
    reader = {
        "client": client, "native_session_id": native_session_id,
        "workspace_path": workspace_path, "generation": generation,
        "applicable_limit_id": applicable_limit_id, "file_identity": "",
        "cursor": cursor, "metadata_ok": client == "claude", "turn_id": None,
        "last_timestamp": None, "latest_state": "ordinary", "event_key": None,
        "limit_type": None, "quota_output_tokens": None,
        "last_output_tokens": None, "quota_timestamp": None,
        "event_timestamp": None, "observed_model": None,
        "quota_turn_id": None, "candidate_turn_id": None,
        "candidate_started_at": None, "candidate_last_timestamp": None,
        "candidate_context_ok": False, "candidate_output": False,
        "candidate_scope_ok": False, "candidate_error": False,
        "candidate_numeric_ok": False, "candidate_output_baseline": None,
        "candidate_model": None,
        "last_line_size": None, "last_line_hash": None,
        "skipped_records": 0, "reset_at": None,
    }
    if client not in {"claude", "codex"}:
        return _result("unknown", "unsupported_native_client", reader)
    if type(cursor) is not int or cursor < 0:
        reader["cursor"] = 0
        return _result("unknown", "invalid_native_cursor", reader)
    if (expected_file_identity is not None and isinstance(reader_state, dict) and cursor > 0
            and _valid_reader_state(reader_state)
            and all(reader_state.get(key) == value for key, value in (
                ("client", client), ("native_session_id", native_session_id),
                ("workspace_path", workspace_path), ("generation", generation),
                ("applicable_limit_id", applicable_limit_id),
                ("file_identity", expected_file_identity), ("cursor", cursor)))):
        try:
            current = os.stat(path)
        except OSError:
            current = None
        if (current is not None and f"{current.st_dev}:{current.st_ino}" == expected_file_identity
                and current.st_size == cursor):
            # H3: an unchanged log (same identity and size as the cursor) is not re-read.
            reader.update(reader_state)
            return _result(reader["latest_state"], "native_log_unchanged", reader)
    try:
        with open(path, "rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = f"{stat.st_dev}:{stat.st_ino}"
            reader["file_identity"] = identity
            if expected_file_identity is not None and expected_file_identity != identity:
                reader["cursor"] = 0
                return _result("unknown", "native_file_identity_changed", reader)
            if cursor > stat.st_size:
                reader["cursor"] = 0
                return _result("unknown", "invalid_native_cursor", reader)
            if reader_state is not None:
                keys = ("client", "native_session_id", "workspace_path", "generation",
                        "applicable_limit_id", "file_identity", "cursor")
                if (not isinstance(reader_state, dict) or any(reader_state.get(k) != reader[k] for k in keys)
                        or not _valid_reader_state(reader_state)):
                    reader["cursor"] = 0
                    return _result("unknown", "native_reader_state_mismatch", reader)
                reader.update(reader_state)
                # Older persisted cursors tracked candidate time separately.
                # Preserve their stronger watermark before reading another event.
                candidate_time = _timestamp(reader["candidate_last_timestamp"])
                global_time = _timestamp(reader["last_timestamp"])
                if (reader["candidate_turn_id"] is not None and candidate_time is not None
                        and (global_time is None or candidate_time > global_time)):
                    reader["last_timestamp"] = candidate_time.isoformat()
                last_size = reader["last_line_size"]
                if cursor and (last_size is None or last_size > cursor or reader["last_line_hash"] is None):
                    reader["cursor"] = 0
                    return _result("unknown", "native_reader_state_mismatch", reader)
                if cursor:
                    stream.seek(cursor - last_size)
                    if hashlib.sha256(stream.read(last_size)).hexdigest() != reader["last_line_hash"]:
                        reader["cursor"] = 0
                        return _result("unknown", "native_log_content_changed", reader)
            elif cursor:
                reader["cursor"] = 0
                return _result("unknown", "native_reader_state_required", reader)
            stream.seek(cursor)
            for _ in range(MAX_RECORDS):
                start = stream.tell()
                line = stream.readline(MAX_RECORD_BYTES + 1)
                if not line:
                    return _result(reader["latest_state"], "native_log_eof", reader)
                end = stream.tell()
                if len(line) > MAX_RECORD_BYTES:
                    # Consume the rest of the oversize record in bounded chunks. A record
                    # without its newline yet is torn: stay unknown, do not advance.
                    while not line.endswith(b"\n"):
                        line = stream.readline(MAX_RECORD_BYTES + 1)
                        if not line:
                            return _result("unknown", "incomplete_native_record", reader)
                    _skip_record(reader, stream, start, stream.tell())
                    continue
                if not line.endswith(b"\n"):
                    return _result("unknown", "incomplete_native_record", reader)
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    _skip_record(reader, stream, start, end)
                    continue
                if not isinstance(record, dict):
                    _skip_record(reader, stream, start, end)
                    continue

                kind = record.get("type")
                if client == "codex" and kind == "session_meta":
                    payload = record.get("payload")
                    if not (isinstance(payload, dict) and payload.get("id") == native_session_id
                            and (workspace_path is None or payload.get("cwd") == workspace_path)):
                        return _result("unknown", "native_metadata_mismatch", reader)
                    reader["metadata_ok"] = True
                elif client == "codex" and kind == "turn_context":
                    payload = record.get("payload")
                    if (reader["candidate_turn_id"] is not None
                            and reader["candidate_turn_id"] == reader["turn_id"]
                            and isinstance(payload, dict)
                            and payload.get("turn_id") == reader["candidate_turn_id"]
                            and isinstance(payload.get("model"), str) and payload["model"]
                            and _fresh_candidate_time(reader, record)):
                        reader["candidate_context_ok"] = True
                        reader["candidate_model"] = payload["model"]
                elif client == "codex" and kind == "response_item":
                    payload = record.get("payload")
                    if (reader["candidate_turn_id"] is not None
                            and reader["candidate_turn_id"] == reader["turn_id"]
                            and isinstance(payload, dict) and payload.get("type") == "message"
                            and payload.get("role") == "assistant"
                            and payload.get("turn_id", reader["turn_id"]) == reader["turn_id"]
                            and isinstance(payload.get("content"), list)
                            and any(isinstance(part, dict) and part.get("type") == "output_text"
                                    and isinstance(part.get("text"), str) and part["text"].strip()
                                    for part in payload["content"])):
                        if _fresh_candidate_time(reader, record):
                            reader["candidate_output"] = True
                            # New native assistant output supersedes the old
                            # quota until completion and scope are proved.
                            reader["latest_state"] = "unknown"
                        else:
                            reader["candidate_error"] = True
                elif client == "codex" and kind == "event_msg":
                    if not reader["metadata_ok"]:
                        return _result("unknown", "native_metadata_missing", reader)
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        return _result("unknown", "malformed_codex_event", reader)
                    event_type = payload.get("type")
                    if event_type in {"task_started", "task_complete", "turn_aborted", "token_count", "error"}:
                        when = _timestamp(record.get("timestamp"))
                        if when is None or (reader["last_timestamp"] and when < _timestamp(reader["last_timestamp"])):
                            return _result("unknown", "invalid_native_timestamp", reader)
                        reader["last_timestamp"] = when.isoformat()
                    if event_type == "task_started":
                        turn_id = payload.get("turn_id")
                        if not isinstance(turn_id, str) or not turn_id:
                            return _result("unknown", "invalid_native_turn", reader)
                        reader["turn_id"] = turn_id
                        fresh = (reader["latest_state"] in {"ordinary", "available", "exhausted", "unknown"}
                                 and (reader["quota_turn_id"] is None
                                      or turn_id != reader["quota_turn_id"])
                                 and (reader["quota_timestamp"] is None
                                      or when > _timestamp(reader["quota_timestamp"])))
                        reader.update({
                            "candidate_turn_id": turn_id if fresh else None,
                            "candidate_started_at": when.isoformat() if fresh else None,
                            "candidate_last_timestamp": when.isoformat() if fresh else None,
                            "candidate_context_ok": False, "candidate_output": False,
                            "candidate_scope_ok": False,
                            "candidate_numeric_ok": False,
                            "candidate_model": None,
                            "candidate_output_baseline": reader["last_output_tokens"] if fresh else None,
                            "candidate_error": (fresh and reader["quota_timestamp"] is not None
                                                and reader["quota_turn_id"] is None),
                        })
                    elif event_type in {"task_complete", "turn_aborted"}:
                        if not reader["turn_id"] or payload.get("turn_id") != reader["turn_id"]:
                            return _result("unknown", "native_turn_mismatch", reader)
                        error = payload.get("error")
                        candidate_failed = reader["candidate_error"]
                        if (event_type == "task_complete" and isinstance(error, dict)
                                and error.get("codex_error_info") == "usage_limit_exceeded"):
                            if not isinstance(error.get("message"), str):
                                return _result("unknown", "unsupported_codex_quota_schema", reader)
                            reader["latest_state"] = "exhausted"
                            reader["event_key"] = f"codex:{start}"
                            reader["limit_type"] = "usage_limit_exceeded"
                            reader["reset_at"] = None
                            reader["quota_output_tokens"] = reader["last_output_tokens"]
                            reader["quota_timestamp"] = when.isoformat()
                            reader["quota_turn_id"] = reader["turn_id"]
                            reader["event_timestamp"] = when.isoformat()
                        elif (reader["candidate_turn_id"] is not None
                              and reader["candidate_turn_id"] == reader["turn_id"]
                              and (reader["candidate_output"] or reader["candidate_numeric_ok"])
                              and event_type == "task_complete" and error is None
                              and reader["latest_state"] in {"ordinary", "available", "exhausted", "unknown"}):
                            complete_time = _fresh_candidate_time(reader, record)
                            if (complete_time and reader["candidate_scope_ok"]
                                    and not reader["candidate_error"]
                                    and (reader["candidate_numeric_ok"]
                                         or reader["candidate_output"] and reader["candidate_context_ok"])):
                                reader["latest_state"] = "available"
                                reader["event_key"] = f"codex:{start}"
                                reader["limit_type"] = None
                                reader["reset_at"] = None
                                reader["quota_output_tokens"] = None
                                reader["quota_timestamp"] = None
                                reader["quota_turn_id"] = None
                                reader["event_timestamp"] = when.isoformat()
                                reader["observed_model"] = (reader["candidate_model"]
                                                             if reader["candidate_context_ok"] else None)
                            else:
                                reader["latest_state"] = "unknown"
                        elif event_type == "turn_aborted" or error is not None:
                            if not (reader["latest_state"] == "exhausted"
                                    and reader["quota_turn_id"] == reader["turn_id"]):
                                reader["latest_state"] = "unknown"
                        if ((event_type == "turn_aborted" or error is not None or candidate_failed)
                                and not (reader["latest_state"] == "exhausted"
                                         and reader["quota_turn_id"] == reader["turn_id"])):
                            # A failed turn may have emitted unreported output. Its
                            # last observed cumulative count cannot seed the next turn.
                            reader["last_output_tokens"] = None
                        _clear_candidate(reader)
                        reader["turn_id"] = None
                    elif event_type == "error":
                        bound_turn = payload.get("turn_id")
                        foreign_turn = bound_turn is not None and bound_turn != reader["turn_id"]
                        candidate_active = (reader["candidate_turn_id"] is not None
                                            and reader["candidate_turn_id"] == reader["turn_id"])
                        if (payload.get("codex_error_info") == "usage_limit_exceeded"
                                and isinstance(payload.get("message"), str)
                                and reader["turn_id"] and not foreign_turn):
                            reader["latest_state"] = "exhausted"
                            reader["event_key"] = f"codex:{start}"
                            reader["limit_type"] = "usage_limit_exceeded"
                            reader["reset_at"] = None
                            reader["quota_output_tokens"] = reader["last_output_tokens"]
                            reader["quota_timestamp"] = when.isoformat()
                            reader["quota_turn_id"] = reader["turn_id"]
                            reader["event_timestamp"] = when.isoformat()
                            _clear_candidate(reader)
                        else:
                            reader["latest_state"] = "unknown"
                            if foreign_turn:
                                _clear_candidate(reader)
                            elif candidate_active:
                                reader["candidate_error"] = True
                    elif event_type == "token_count":
                        bound_turn = payload.get("turn_id")
                        if bound_turn is not None and bound_turn != reader["turn_id"]:
                            reader["latest_state"] = "unknown"
                            _clear_candidate(reader)
                            reader["cursor"] = end
                            reader["last_line_size"] = len(line)
                            reader["last_line_hash"] = hashlib.sha256(line).hexdigest()
                            continue
                        limits = payload.get("rate_limits")
                        if limits is None:
                            output = _codex_output_count(payload.get("info"))
                            prior = reader["last_output_tokens"]
                            if output is not None:
                                if (reader["candidate_turn_id"] == reader["turn_id"]
                                        and reader["turn_id"] is not None
                                        and prior is not None and output > prior
                                        and _fresh_candidate_time(reader, record)):
                                    # Native cumulative growth supersedes an older
                                    # quota, but absent bucket metadata proves no scope.
                                    reader["latest_state"] = "unknown"
                                reader["last_output_tokens"] = output
                            reader["cursor"] = end
                            reader["last_line_size"] = len(line)
                            reader["last_line_hash"] = hashlib.sha256(line).hexdigest()
                            continue
                        if not isinstance(limits, dict):
                            return _result("unknown", "unsupported_codex_quota_schema", reader)
                        reached = limits.get("rate_limit_reached_type")
                        limit_id = limits.get("limit_id")
                        output = _codex_output_count(payload.get("info"))
                        if reached is not None and (not isinstance(reached, str)
                                                    or reached not in CODEX_REACHED_TYPES
                                                    or not isinstance(limit_id, str)):
                            return _result("unknown", "unsupported_codex_quota_schema", reader)
                        if reached is not None and limit_id != applicable_limit_id:
                            return _result("unknown", "unrelated_codex_quota_scope", reader)
                        if limit_id == applicable_limit_id and reached is not None:
                            if not reader["turn_id"]:
                                return _result("unknown", "native_turn_missing", reader)
                            reader["latest_state"] = "exhausted"
                            reader["event_key"] = f"codex:{start}"
                            reader["limit_type"] = reached
                            reader["reset_at"] = _codex_reset_at(limits)
                            reader["quota_output_tokens"] = output if output is not None else reader["last_output_tokens"]
                            reader["quota_timestamp"] = when.isoformat()
                            reader["quota_turn_id"] = reader["turn_id"]
                            reader["event_timestamp"] = when.isoformat()
                            _clear_candidate(reader)
                        elif (limit_id == applicable_limit_id and reached is None
                              and reader["latest_state"] in {"ordinary", "available", "exhausted", "unknown"}
                              and reader["turn_id"]):
                            if reader["candidate_turn_id"] == reader["turn_id"]:
                                if _fresh_candidate_time(reader, record):
                                    reader["candidate_scope_ok"] = True
                                else:
                                    reader["candidate_error"] = True
                            prior = reader["quota_output_tokens"]
                            turn_baseline = reader["candidate_output_baseline"]
                            if (reader["candidate_turn_id"] == reader["turn_id"]
                                    and reader["candidate_scope_ok"] and not reader["candidate_error"]
                                    and output is not None and prior is not None and output > prior
                                    and reader["quota_timestamp"] and when > _timestamp(reader["quota_timestamp"])):
                                if turn_baseline is not None and output > turn_baseline:
                                    reader["candidate_numeric_ok"] = True
                                reader["latest_state"] = "unknown"
                        if limit_id == applicable_limit_id and output is not None:
                            reader["last_output_tokens"] = output
                elif client == "claude":
                    quota = _claude_quota(record)
                    success = _claude_success(record)
                    partial = _claude_positive_output(record) and not success
                    api_error = _claude_native_api_error(record) and not quota
                    if quota or success or partial or api_error:
                        when = _timestamp(record.get("timestamp"))
                        if not _claude_identity(record, native_session_id, workspace_path):
                            return _result("unknown", "native_identity_mismatch", reader)
                        if (when is None or (reader["last_timestamp"] and when < _timestamp(reader["last_timestamp"]))
                                or not isinstance(record.get("uuid"), str) or not record["uuid"]):
                            return _result("unknown", "invalid_native_event", reader)
                        if ((success or partial) and reader["latest_state"] == "exhausted"
                                and reader["quota_timestamp"] and when <= _timestamp(reader["quota_timestamp"])):
                            return _result("unknown", "stale_native_recovery", reader)
                        reader["last_timestamp"] = when.isoformat()
                        if quota or success:
                            reader["latest_state"] = "exhausted" if quota else "available"
                            reader["event_key"] = f"claude:{record['uuid']}:{start}"
                            reader["limit_type"] = record["quotaLimits"]["rateLimitType"] if quota else None
                            reader["reset_at"] = record["quotaLimits"]["resetsAt"] if quota else None
                            reader["quota_timestamp"] = when.isoformat() if quota else None
                            reader["event_timestamp"] = when.isoformat()
                            reader["observed_model"] = record["message"]["model"] if success else None
                        else:
                            reader["latest_state"] = "unknown"
                    elif quota is None:
                        return _result("unknown", "unsupported_claude_quota_schema", reader)
                reader["cursor"] = end
                reader["last_line_size"] = len(line)
                reader["last_line_hash"] = hashlib.sha256(line).hexdigest()
            return _result("unknown", "native_log_requires_cursor_continuation", reader)
    except OSError as exc:
        return _result("unknown", f"unreadable_native_log:{exc.__class__.__name__}", reader)
