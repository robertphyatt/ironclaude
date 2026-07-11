"""Tests for grader log capping and remote-path resolution in kill_worker."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ironclaude.db import init_db
from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.tmux_manager import TmuxManager
from ironclaude.worker_registry import WorkerRegistry


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    return init_db(db_path)


@pytest.fixture
def registry(db_conn):
    return WorkerRegistry(db_conn)


@pytest.fixture
def mock_tmux():
    tmux = MagicMock()
    tmux.has_session.return_value = True
    tmux.send_keys.return_value = True
    tmux.capture_pane.return_value = ""
    tmux.get_log_path.return_value = "/tmp/ic-logs/ic-test.log"
    tmux.read_log_tail.return_value = "log tail\n"
    tmux.list_pane_pid.return_value = None
    return tmux


@pytest.fixture
def tools(registry, mock_tmux, tmp_path, db_conn, monkeypatch):
    ledger_path = str(tmp_path / "task-ledger.json")
    empty_cfg = tmp_path / "empty_ollama.json"
    empty_cfg.write_text("{}")
    monkeypatch.setenv("IC_OLLAMA_CONFIG_PATH", str(empty_cfg))
    t = OrchestratorTools(registry, mock_tmux, ledger_path, db_conn=db_conn)
    t._get_ollama_vram = MagicMock(return_value=(0.0, []))
    return t


def test_read_log_tail_caps_to_500_lines(tmp_path):
    """Characterization test: TmuxManager.read_log_tail already caps correctly."""
    log_path = tmp_path / "ic-big.log"
    log_path.write_text("".join(f"line{i}\n" for i in range(1, 10001)))

    tmux = TmuxManager(log_dir=str(tmp_path))
    tmux.get_log_path = MagicMock(return_value=str(log_path))

    result = tmux.read_log_tail("ic-big", lines=500)
    result_lines = result.splitlines()

    assert len(result_lines) == 500
    assert result_lines[-1] == "line10000"
    assert "line1" not in result_lines


def test_kill_worker_resolves_ssh_and_log_dir_before_grading(tools, registry):
    """kill_worker must resolve ssh_host/remote_log_dir and fetch a capped log
    tail BEFORE calling the grader, including for remote (SSH machine) workers."""
    registry.register_worker("w1", "claude-sonnet", "ic-w1", machine="mac-mini", repo="/repo")

    mock_ssh = MagicMock()
    machine_cfg = MagicMock()
    machine_cfg.host = "mac-mini.local"
    machine_cfg.log_dir = "/remote/logs"
    mock_ssh.get_machine.return_value = machine_cfg
    tools._ssh_manager = mock_ssh

    call_order = []

    def _fake_read_log_tail(*args, **kwargs):
        call_order.append(("read_log_tail", kwargs))
        return "capped tail"

    tools.tmux.read_log_tail.side_effect = _fake_read_log_tail

    grader_prompts = {}

    def _fake_call_grader(system_prompt, user_prompt, *args, **kwargs):
        call_order.append(("grader",))
        grader_prompts["user"] = user_prompt
        return {"grade": "A", "approved": True, "feedback": "ok"}

    tools._call_grader = MagicMock(side_effect=_fake_call_grader)
    tools._fire_shadow_thread = MagicMock()

    tools.kill_worker("w1", original_objective="do the thing", evidence="done")

    assert call_order[0][0] == "read_log_tail"
    assert call_order[1] == ("grader",)
    _, kwargs = call_order[0]
    assert kwargs["ssh_host"] == "mac-mini.local"
    assert kwargs["remote_log_dir"] == "/remote/logs"
    # The log tail must be CAPPED at GRADER_LOG_MAX_LINES and the capped
    # excerpt must actually reach the grader's prompt. Deleting either the
    # `lines=` cap or the `{log_tail}` injection must fail this test — that is
    # the whole point of the fix (the grader must not re-read the full log).
    assert kwargs["lines"] == tools.GRADER_LOG_MAX_LINES
    assert "capped tail" in grader_prompts["user"]


def test_int_env_returns_default_on_missing_or_non_integer(monkeypatch):
    from ironclaude.orchestrator_mcp import _int_env

    monkeypatch.delenv("IC_TEST_INT_ENV", raising=False)
    assert _int_env("IC_TEST_INT_ENV", 500) == 500          # missing -> default
    monkeypatch.setenv("IC_TEST_INT_ENV", "not-an-int")
    assert _int_env("IC_TEST_INT_ENV", 500) == 500          # non-integer -> default
    monkeypatch.setenv("IC_TEST_INT_ENV", "42")
    assert _int_env("IC_TEST_INT_ENV", 500) == 42           # valid -> parsed
    monkeypatch.setenv("IC_TEST_INT_ENV", "-5")
    # Negatives pass through _int_env unchanged — the caller must floor them
    # (GRADER_LOG_MAX_LINES wraps this in max(1, ...) so a negative override
    # can't reach deque(maxlen=<negative>)).
    assert _int_env("IC_TEST_INT_ENV", 500) == -5


def test_positive_int_env_floors_at_one(monkeypatch):
    from ironclaude.orchestrator_mcp import _positive_int_env

    monkeypatch.setenv("IC_TEST_POS_ENV", "-5")
    assert _positive_int_env("IC_TEST_POS_ENV", 500) == 1     # negative -> floored to 1
    monkeypatch.setenv("IC_TEST_POS_ENV", "0")
    assert _positive_int_env("IC_TEST_POS_ENV", 500) == 1     # zero -> floored to 1
    monkeypatch.setenv("IC_TEST_POS_ENV", "42")
    assert _positive_int_env("IC_TEST_POS_ENV", 500) == 42    # positive -> passes through
    monkeypatch.delenv("IC_TEST_POS_ENV", raising=False)
    assert _positive_int_env("IC_TEST_POS_ENV", 500) == 500   # missing -> default


def test_kill_worker_grades_when_log_read_fails(tools, registry):
    """A failure reading the worker log (e.g. PermissionError from open()) must
    NOT abort the kill — grading proceeds with a placeholder excerpt. Without
    the try/except guard at the read_log_tail call site, this raises."""
    registry.register_worker("w2", "claude-sonnet", "ic-w2", repo="/repo")

    tools.tmux.read_log_tail.side_effect = PermissionError("denied")

    grader_prompts = {}

    def _fake_call_grader(system_prompt, user_prompt, *args, **kwargs):
        grader_prompts["user"] = user_prompt
        return {"grade": "A", "approved": True, "feedback": "ok"}

    tools._call_grader = MagicMock(side_effect=_fake_call_grader)
    tools._fire_shadow_thread = MagicMock()

    # Must not raise despite the unreadable log.
    tools.kill_worker("w2", original_objective="do the thing", evidence="done")

    tools._call_grader.assert_called_once()
    assert "log unavailable" in grader_prompts["user"]
