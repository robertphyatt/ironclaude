"""Tests for the Fable availability flag module.

Hermetic: every test redirects fa._STATE_PATH into tmp_path so no test
touches the real ~/.ironclaude/state/fable_unavailable.json.
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from ironclaude import fable_availability as fa


@pytest.fixture(autouse=True)
def _isolate_state_path(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "_STATE_PATH", tmp_path / "state.json")
    return tmp_path


def test_absent_returns_false():
    assert fa.is_fable_unavailable() is False


def test_future_returns_true():
    fa._STATE_PATH.write_text(json.dumps({"unavailable_until": time.time() + 3600}))
    assert fa.is_fable_unavailable() is True


def test_past_returns_false():
    fa._STATE_PATH.write_text(json.dumps({"unavailable_until": time.time() - 3600}))
    assert fa.is_fable_unavailable() is False


def test_corrupt_json_returns_false():
    fa._STATE_PATH.write_text("not json")
    assert fa.is_fable_unavailable() is False


def test_missing_key_returns_false():
    fa._STATE_PATH.write_text(json.dumps({"foo": "bar"}))
    assert fa.is_fable_unavailable() is False


def test_mark_transition_semantics():
    assert fa.mark_fable_unavailable("r") == "transition"
    assert fa.mark_fable_unavailable("r") == "already_flagged"


def test_mark_model_unavailable_uses_24h_ttl():
    # Repointed: the blanket 24h window is now scoped to model_unavailable only
    # (usage/unknown get shorter re-probe windows). A model-outage reason keeps 24h.
    fa.mark_fable_unavailable("Claude Fable 5 is currently unavailable")
    payload = json.loads(fa._STATE_PATH.read_text())
    assert payload["unavailable_until"] - time.time() > 86000
    assert payload["category"] == "model_unavailable"


def test_mark_creates_parent_dir(tmp_path, monkeypatch):
    nested = tmp_path / "sub" / "dir" / "state.json"
    monkeypatch.setattr(fa, "_STATE_PATH", nested)
    assert fa.mark_fable_unavailable("r") == "transition"
    assert nested.exists()


def test_clear_returns_true_when_present():
    fa.mark_fable_unavailable("r")
    assert fa.clear_fable_unavailable() == "removed"
    assert fa.is_fable_unavailable() is False


def test_clear_returns_false_when_absent():
    assert fa.clear_fable_unavailable() == "not_present"


def test_resolve_worker_type_redirects_when_flag_active():
    fa.mark_fable_unavailable("r")
    assert fa.resolve_worker_type("claude-fable") == "claude-opus"


def test_resolve_worker_type_passthrough_when_flag_inactive():
    assert fa.resolve_worker_type("claude-fable") == "claude-fable"
    assert fa.resolve_worker_type("claude-sonnet") == "claude-sonnet"


def test_resolve_advisor_model_redirects_when_flag_active():
    fa.mark_fable_unavailable("r")
    assert fa.resolve_advisor_model("fable") == "opus"


def test_resolve_advisor_model_passthrough():
    assert fa.resolve_advisor_model("fable") == "fable"
    assert fa.resolve_advisor_model(None) is None
    assert fa.resolve_advisor_model("opus") == "opus"


def test_atomic_write_no_partial_file_on_success():
    fa.mark_fable_unavailable("r")
    json.loads(fa._STATE_PATH.read_text())  # well-formed JSON
    tmp_sibling = fa._STATE_PATH.with_suffix(fa._STATE_PATH.suffix + ".tmp")
    assert not tmp_sibling.exists()


def test_mark_returns_transition_first_call():
    assert fa.mark_fable_unavailable("test") == "transition"


def test_mark_returns_already_flagged_second_call():
    fa.mark_fable_unavailable("test")
    assert fa.mark_fable_unavailable("test") == "already_flagged"


def test_mark_returns_write_failed_when_disk_error(monkeypatch):
    monkeypatch.setattr(fa.os, "replace", MagicMock(side_effect=OSError("disk full")))
    assert fa.mark_fable_unavailable("test") == "write_failed"


def test_tmp_file_cleaned_up_on_write_failure(monkeypatch):
    monkeypatch.setattr(fa.os, "replace", MagicMock(side_effect=OSError("disk full")))
    assert fa.mark_fable_unavailable("test") == "write_failed"
    tmp_sibling = fa._STATE_PATH.with_suffix(fa._STATE_PATH.suffix + ".tmp")
    assert not tmp_sibling.exists()


def test_clear_returns_removed_when_present():
    fa.mark_fable_unavailable("test")
    assert fa.clear_fable_unavailable() == "removed"


def test_clear_returns_not_present_when_absent():
    assert fa.clear_fable_unavailable() == "not_present"


def test_clear_returns_remove_failed_on_error(monkeypatch):
    fa.mark_fable_unavailable("test")
    monkeypatch.setattr(fa.Path, "unlink", MagicMock(side_effect=OSError("permission denied")))
    assert fa.clear_fable_unavailable() == "remove_failed"


def test_concurrent_marks_serialized():
    """Two threads racing to mark_fable_unavailable against the same
    _STATE_PATH: flock serializes the read-check-write so exactly one call
    observes "not yet flagged" and transitions; the other must see the
    already-written flag and return already_flagged. Without the lock, both
    could read the pre-write state and both return "transition" (FA-03)."""
    results = []
    barrier = threading.Barrier(2)

    def _call():
        barrier.wait()
        results.append(fa.mark_fable_unavailable("concurrent"))

    threads = [threading.Thread(target=_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["already_flagged", "transition"]


class TestClassifyReason:
    @pytest.mark.parametrize("reason", [
        "5-hour limit reached - resets 3pm", "Usage limit exceeded", "rate limit",
        "HTTP 429 Too Many Requests", "quota exceeded",
    ])
    def test_usage_limit(self, reason):
        assert fa.classify_reason(reason) == "usage_limit"

    @pytest.mark.parametrize("reason", [
        "Claude Fable 5 is currently unavailable", "access denied", "HTTP 403 forbidden",
        # the REAL message-shaped outage text the call site now forwards (brain_client.py):
        "There's an issue with the selected model (fable[1m]). It may not exist or you may not have access to it.",
    ])
    def test_model_unavailable(self, reason):
        assert fa.classify_reason(reason) == "model_unavailable"

    @pytest.mark.parametrize("reason", [
        "brain-detected-exception", "brain-detected-message", "spawn-died",
        "unexpected capacity constraints, unable to respond", "", "weird",
    ])
    def test_unknown(self, reason):
        assert fa.classify_reason(reason) == "unknown"

    def test_bare_number_not_substring_matched(self):
        # a request id containing 429/403 as a substring must NOT trigger a match
        assert fa.classify_reason("request req_4290x11 failed") == "unknown"
        assert fa.classify_reason("trace 14039 done") == "unknown"

    def test_model_anchor_wins_over_incidental_usage_words(self):
        # adversarial review M1: a real outage that also mentions a usage word must
        # still classify model_unavailable (the anchor wins).
        assert fa.classify_reason(
            "There's an issue with the selected model (fable[1m]); rate limit / quota noise"
        ) == "model_unavailable"
        assert fa.classify_reason("selected model may not exist — too many requests") == "model_unavailable"


class TestParseResetTime:
    def test_resets_pm(self):
        import datetime
        now = datetime.datetime(2026, 7, 14, 13, 0, 0).timestamp()   # 1pm
        got = fa.parse_reset_time("5-hour limit reached - resets 3pm", now)
        assert got == datetime.datetime(2026, 7, 14, 15, 0, 0).timestamp()

    def test_resets_next_day_when_past(self):
        import datetime
        now = datetime.datetime(2026, 7, 14, 23, 0, 0).timestamp()   # 11pm
        got = fa.parse_reset_time("5-hour limit resets 12:30am - continuing with usage credits", now)
        assert got == datetime.datetime(2026, 7, 15, 0, 30, 0).timestamp()

    def test_no_reset_returns_none(self):
        assert fa.parse_reset_time("some message without a reset time", 1000.0) is None
        assert fa.parse_reset_time("", 1000.0) is None


class TestMarkWindows:
    def _read(self):
        return json.loads(fa._STATE_PATH.read_text())

    def test_usage_limit_uses_message_reset(self, monkeypatch):
        import datetime
        now = datetime.datetime(2026, 7, 14, 13, 0, 0).timestamp()   # 1pm
        monkeypatch.setattr(fa, "_now", lambda: now)
        assert fa.mark_fable_unavailable("5-hour limit reached - resets 3pm") == "transition"
        p = self._read()
        assert p["category"] == "usage_limit"
        assert p["unavailable_until"] == datetime.datetime(2026, 7, 14, 15, 0, 0).timestamp()

    def test_usage_limit_reset_at_wins(self, monkeypatch):
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        fa.mark_fable_unavailable("usage limit", reset_at=1000.0 + 4000)
        assert self._read()["unavailable_until"] == 5000.0

    def test_usage_limit_retry_after(self, monkeypatch):
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        fa.mark_fable_unavailable("rate limit", retry_after_seconds=120)
        assert self._read()["unavailable_until"] == 1120.0

    def test_usage_limit_fallback_reprobe(self, monkeypatch):
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        fa.mark_fable_unavailable("usage limit")
        assert self._read()["unavailable_until"] == 1000.0 + fa._USAGE_REPROBE

    def test_usage_limit_clamped_to_max(self, monkeypatch):
        # a reset_at 20h out (bad parse / skew) is clamped to now + _USAGE_MAX
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        fa.mark_fable_unavailable("usage limit", reset_at=1000.0 + 20 * 3600)
        assert self._read()["unavailable_until"] == 1000.0 + fa._USAGE_MAX

    def test_model_unavailable_24h(self, monkeypatch):
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        fa.mark_fable_unavailable("Claude Fable 5 is currently unavailable")
        p = self._read()
        assert p["category"] == "model_unavailable"
        assert p["unavailable_until"] == 1000.0 + fa._MODEL_TTL

    def test_unknown_reprobe(self, monkeypatch):
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        fa.mark_fable_unavailable("brain-detected-exception")
        p = self._read()
        assert p["category"] == "unknown"
        assert p["unavailable_until"] == 1000.0 + fa._UNKNOWN_REPROBE

    def test_usage_limit_escalates_to_model_unavailable(self, monkeypatch):
        # adversarial review I3: a genuine outage during a keep-Fable usage_limit window
        # must be able to flip the category so resolve_* stops keeping Fable.
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        assert fa.mark_fable_unavailable("usage limit") == "transition"
        assert self._read()["category"] == "usage_limit"
        assert fa.mark_fable_unavailable("issue with the selected model (fable[1m])") == "transition"
        p = self._read()
        assert p["category"] == "model_unavailable"
        assert p["unavailable_until"] == 1000.0 + fa._MODEL_TTL

    def test_usage_limit_not_re_marked_by_another_usage_limit(self, monkeypatch):
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        assert fa.mark_fable_unavailable("usage limit") == "transition"
        assert fa.mark_fable_unavailable("rate limit") == "already_flagged"   # same category -> dedup

    def test_model_unavailable_not_downgraded_to_usage(self, monkeypatch):
        # escalation is one-way: a model_unavailable window is NOT flipped to usage_limit.
        monkeypatch.setattr(fa, "_now", lambda: 1000.0)
        assert fa.mark_fable_unavailable("currently unavailable") == "transition"
        assert fa.mark_fable_unavailable("usage limit") == "already_flagged"


class TestResolveGating:
    def _flag(self, category):
        fa._STATE_PATH.write_text(json.dumps(
            {"unavailable_until": time.time() + 9999, "category": category,
             "reason": "x", "marked_at": time.time()}))

    def test_usage_limit_keeps_fable(self):
        self._flag("usage_limit")
        assert fa.resolve_worker_type("claude-fable") == "claude-fable"
        assert fa.resolve_advisor_model("fable") == "fable"

    def test_model_unavailable_downgrades(self):
        self._flag("model_unavailable")
        assert fa.resolve_worker_type("claude-fable") == "claude-opus"
        assert fa.resolve_advisor_model("fable") == "opus"

    def test_unknown_downgrades(self):
        self._flag("unknown")
        assert fa.resolve_worker_type("claude-fable") == "claude-opus"

    def test_legacy_flag_no_category_downgrades(self):
        # active flag missing 'category' (legacy / unreadable-category race) -> downgrade (safe)
        fa._STATE_PATH.write_text(json.dumps({"unavailable_until": time.time() + 9999}))
        assert fa.resolve_worker_type("claude-fable") == "claude-opus"
        assert fa.fable_block_category() == "unknown"

    def test_available_passthrough(self):
        assert fa.resolve_worker_type("claude-fable") == "claude-fable"
        assert fa.fable_block_category() is None
