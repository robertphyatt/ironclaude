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


def test_mark_uses_24h_ttl():
    fa.mark_fable_unavailable("r")
    payload = json.loads(fa._STATE_PATH.read_text())
    assert payload["unavailable_until"] - time.time() > 86000


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
