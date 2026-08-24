"""Task 3 integration coverage for communication-profile routing."""
import inspect
from unittest.mock import MagicMock

import pytest

from ironclaude import main as main_module
from ironclaude import orchestrator_mcp as orchestrator_module
from ironclaude.communication_profiles import (
    CommunicationProfileError,
    PROFILE_READY_MARKER,
)
from ironclaude.main import IroncladeDaemon
from ironclaude.main import _render_brain_system_prompt
from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.tmux_manager import TmuxManager


def _tools(tmp_path, tmux):
    return OrchestratorTools(
        registry=MagicMock(), tmux=tmux, db_conn=None, config={},
    )


def test_brain_restart_profiles_substituted_prompt_without_marker(tmp_path, monkeypatch):
    prompt = tmp_path / "brain.md"
    prompt.write_text("Brain for {OPERATOR_NAME}")
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._brain_paused = False
    daemon.config = {"brain_prompt_path": str(prompt), "operator_name": "Ada"}
    daemon.brain = MagicMock()
    daemon.brain.check_compaction_complete.return_value = False
    daemon.brain.needs_restart.return_value = True
    daemon.brain.circuit_breaker_tripped.return_value = False
    daemon.brain.restart_count = 1
    daemon.brain.restart_reason = "timeout"
    daemon.slack = MagicMock()
    monkeypatch.setattr(
        main_module, "apply_communication_profile",
        lambda construction, text: f"profile:{construction}\n{text}",
    )

    daemon.check_brain()

    rendered = daemon.brain.restart.call_args.args[0]
    assert rendered == "profile:commander_brain\nBrain for Ada"
    assert PROFILE_READY_MARKER not in rendered


def test_brain_restart_fails_closed_when_mixed_profile_is_unavailable(tmp_path, monkeypatch, caplog):
    prompt = tmp_path / "brain.md"
    prompt.write_text("Brain")
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._brain_paused = False
    daemon.config = {"brain_prompt_path": str(prompt)}
    daemon.brain = MagicMock()
    daemon.brain.check_compaction_complete.return_value = False
    daemon.brain.needs_restart.return_value = True
    daemon.brain.circuit_breaker_tripped.return_value = False
    monkeypatch.setattr(
        main_module, "apply_communication_profile",
        MagicMock(side_effect=CommunicationProfileError("missing mixed skill")),
    )

    daemon.check_brain()

    daemon.brain.restart.assert_not_called()
    assert "communication-profile infrastructure error" in caplog.text


def test_brain_startup_and_restart_share_profiled_prompt_loader(tmp_path, monkeypatch):
    prompt = tmp_path / "brain.md"
    prompt.write_text("Brain for {OPERATOR_NAME}")
    profile = MagicMock(return_value="mixed\nBrain for Ada")
    monkeypatch.setattr(main_module, "apply_communication_profile", profile)

    rendered = _render_brain_system_prompt(
        str(prompt), {"operator_name": "Ada"},
    )

    assert rendered == "mixed\nBrain for Ada"
    profile.assert_called_once_with("commander_brain", "Brain for Ada")
    assert "_render_brain_system_prompt(prompt_path, self.config)" in inspect.getsource(
        IroncladeDaemon.check_brain
    )
    assert "_render_brain_system_prompt(prompt_path, config)" in inspect.getsource(
        main_module.main
    )


def test_brain_prompt_loader_propagates_profile_infrastructure_error(tmp_path, monkeypatch):
    prompt = tmp_path / "brain.md"
    prompt.write_text("Brain")
    monkeypatch.setattr(
        main_module, "apply_communication_profile",
        MagicMock(side_effect=CommunicationProfileError("missing mixed skill")),
    )
    with pytest.raises(CommunicationProfileError, match="missing mixed skill"):
        _render_brain_system_prompt(str(prompt), {})


@pytest.mark.parametrize("client, invocation", [
    ("claude", "/write-lossless-ai-messages"),
    ("codex", "$ironclaude:write-lossless-ai-messages"),
])
def test_worker_profile_uses_only_marker_bytes_appended_after_dispatch(
    tmp_path, monkeypatch, client, invocation,
):
    tmux = MagicMock()
    tmux.get_log_size.return_value = 17
    tmux.send_keys.return_value = True
    tmux.read_log_since.return_value = f"new output\n{PROFILE_READY_MARKER}\n"
    tools = _tools(tmp_path, tmux)
    clock = iter((0, 1))
    monkeypatch.setattr(orchestrator_module.time, "time", lambda: next(clock))
    monkeypatch.setattr(orchestrator_module.time, "sleep", lambda _: None)

    assert tools._dispatch_worker_communication_profile("ic-w", client) is None
    tmux.send_keys.assert_called_once_with("ic-w", invocation, ssh_host=None)
    tmux.read_log_since.assert_called_once_with(
        "ic-w", 17, ssh_host=None, remote_log_dir=None,
    )


def test_worker_profile_rejects_stale_marker_from_before_dispatch(tmp_path, monkeypatch):
    tmux = MagicMock()
    tmux.get_log_size.return_value = 42
    tmux.send_keys.return_value = True
    # The old log contains the marker, but post-offset bytes do not.
    tmux.read_log_tail.return_value = f"old\n{PROFILE_READY_MARKER}\n"
    tmux.read_log_since.return_value = "new output without marker"
    tools = _tools(tmp_path, tmux)
    clock = iter((0, 31))
    monkeypatch.setattr(orchestrator_module.time, "time", lambda: next(clock))
    monkeypatch.setattr(orchestrator_module.time, "sleep", lambda _: None)

    assert "fresh readiness marker" in tools._dispatch_worker_communication_profile(
        "ic-w", "claude",
    )
    tmux.read_log_tail.assert_not_called()


def test_profile_load_failure_returns_before_objective_delivery(tmp_path, monkeypatch):
    tmux = MagicMock()
    tools = _tools(tmp_path, tmux)
    monkeypatch.setattr(
        orchestrator_module, "skill_invocation",
        MagicMock(side_effect=CommunicationProfileError("missing skill")),
    )

    error = tools._dispatch_worker_communication_profile("ic-w", "claude")

    assert error == "Communication-profile infrastructure error: missing skill"
    tmux.send_keys.assert_not_called()


def test_adopt_missing_skill_preflight_is_non_mutating(tmp_path, monkeypatch):
    tmux = MagicMock()
    tmux.has_session.side_effect = lambda name, **_: name == "manual"
    tools = _tools(tmp_path, tmux)
    tools.registry.get_worker.return_value = None
    monkeypatch.setattr(
        orchestrator_module, "skill_invocation",
        MagicMock(side_effect=CommunicationProfileError("missing skill")),
    )

    result = tools.adopt_session("manual", "w", repo="/r")

    assert result == {"error": "Communication-profile infrastructure error: missing skill"}
    tmux.rename_session.assert_not_called()
    tmux.setup_log_capture.assert_not_called()
    tmux.send_keys.assert_not_called()
    tools.registry.register_worker.assert_not_called()


def test_resume_missing_skill_kills_before_registration(tmp_path, monkeypatch):
    session_id = "11111111-1111-4111-8111-111111111111"
    tmux = MagicMock()
    tmux.has_session.return_value = False
    tmux.spawn_session.return_value = True
    tmux.read_log_tail.return_value = "ironclaude v1.1.6"
    tools = _tools(tmp_path, tmux)
    tools.registry.get_worker.return_value = None
    tools._ensure_worker_instructions = MagicMock(return_value=None)
    tools.ensure_worker_trusted = MagicMock()
    tools._activate_pm_via_sqlite = MagicMock(return_value=None)
    tools._read_pm_state_via_sqlite = MagicMock(return_value={
        "professional_mode": "on", "session_uuid": session_id,
    })
    clock = iter((0, 1, 1))
    monkeypatch.setattr(orchestrator_module.time, "time", lambda: next(clock))
    monkeypatch.setattr(
        orchestrator_module, "skill_invocation",
        MagicMock(side_effect=CommunicationProfileError("missing skill")),
    )

    result = tools.resume_session(session_id, "claude", "w", repo="/r")

    assert result == {
        "error": "Communication-profile infrastructure error: missing skill",
    }
    tmux.kill_session.assert_called_once_with("ic-w")
    tmux.send_keys.assert_not_called()
    tools.registry.register_worker.assert_not_called()


@pytest.mark.parametrize("surface", ["adopt", "resume"])
def test_stale_marker_cannot_activate_adopt_or_resume(tmp_path, monkeypatch, surface):
    tmux = MagicMock()
    tmux.get_log_size.return_value = 42
    tmux.send_keys.return_value = True
    tmux.read_log_since.return_value = "new output"
    tmux.read_log_tail.return_value = f"old {PROFILE_READY_MARKER}"
    tools = _tools(tmp_path, tmux)
    tools.registry.get_worker.return_value = None
    clock_values = (
        (0, 0, 31)
        if surface == "adopt"
        else (0, 1, 1, 2, 2, 33)
    )
    clock = iter(clock_values)
    monkeypatch.setattr(orchestrator_module.time, "time", lambda: next(clock))
    monkeypatch.setattr(orchestrator_module.time, "sleep", lambda _: None)

    if surface == "adopt":
        tmux.has_session.side_effect = lambda name, **_: name == "manual"
        tmux.rename_session.return_value = True
        result = tools.adopt_session("manual", "w", repo="/r")
        assert "fresh readiness marker" in result["error"]
        tools.registry.register_worker.assert_not_called()
        tmux.rename_session.assert_called_once_with("manual", "ic-w")
    else:
        session_id = "11111111-1111-4111-8111-111111111111"
        tmux.has_session.return_value = False
        tmux.spawn_session.return_value = True
        tmux.read_log_tail.return_value = (
            f"ironclaude v1.1.6\nold {PROFILE_READY_MARKER}"
        )
        tools._ensure_worker_instructions = MagicMock(return_value=None)
        tools.ensure_worker_trusted = MagicMock()
        tools._activate_pm_via_sqlite = MagicMock(return_value=None)
        tools._read_pm_state_via_sqlite = MagicMock(return_value={
            "professional_mode": "on", "session_uuid": session_id,
        })
        result = tools.resume_session(session_id, "claude", "w", repo="/r")
        assert "fresh readiness marker" in result["error"]
        tmux.kill_session.assert_called_once_with("ic-w")
        tools.registry.register_worker.assert_not_called()
    tmux.read_log_since.assert_called()


def test_log_offset_helpers_bound_local_reads(tmp_path):
    log = tmp_path / "ic-w.log"
    log.write_bytes(b"before-marker\n" + b"x" * 20 + b"after-marker\n")
    tmux = TmuxManager(log_dir=str(tmp_path))

    assert tmux.get_log_size("ic-w") == log.stat().st_size
    assert tmux.read_log_since("ic-w", len(b"before-marker\n"), max_bytes=5) == "xxxxx"
