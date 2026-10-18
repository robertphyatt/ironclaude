"""Local box -> Haiku-class fast model -> fail open, across the Commander's local-grader spots."""
import json
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from ironclaude import fast_model
from ironclaude.grader import LocalGrader
from ironclaude.ollama_client import OllamaConnectionError, _BREAKERS
from ironclaude.shadow_grader import ShadowGrader

SCHEMA = {"type": "object", "properties": {"valid": {"type": "boolean"}}, "required": ["valid"]}


def _fast_answer(monkeypatch, obj, record=None):
    def fake(argv, *, input_text, timeout, env):
        if record is not None:
            record.append({"argv": argv, "timeout": timeout})
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"type": "result", "structured_output": obj}]), "")
    monkeypatch.setattr(fast_model, "_run_child", fake)


def _write(tmp_path, cfg):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps(cfg))
    return str(path)


def test_local_connection_error_falls_back_to_full_fast_object(monkeypatch):
    calls = []
    _fast_answer(monkeypatch, {"valid": True, "reason": "kept", "kind": "x"}, calls)
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.side_effect = OllamaConnectionError("refused")
    assert grader.grade("sys", "user", SCHEMA) == {"valid": True, "reason": "kept", "kind": "x"}
    assert calls[0]["timeout"] == fast_model.FAST_MODEL_TIMEOUT_DEFAULT


def test_open_breaker_no_longer_skips_the_box(monkeypatch, tmp_path):
    _fast_answer(monkeypatch, {"valid": True})
    url = "http://box.invalid:11434"
    cfg = _write(tmp_path, {"backend": "ollama", "ollama": {"url": url, "model": "m", "local_backoff_minutes": 0}})
    for _ in range(3):
        _BREAKERS.record_failure(url)
    with patch("requests.post") as post:
        post.return_value.json.return_value = {"response": '{"valid": false}'}
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": False}
        assert post.call_count == 1


def test_local_non_json_falls_back(monkeypatch):
    _fast_answer(monkeypatch, {"valid": False})
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.return_value = "not json"
    assert grader.grade("sys", "user", SCHEMA) == {"valid": False}


def test_haiku_backend_never_builds_a_local_client(monkeypatch, tmp_path):
    cfg = _write(tmp_path, {"backend": "haiku", "fast_model_timeout_seconds": 9})
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    with patch("ironclaude.grader.OllamaClient") as ollama:
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}
    ollama.assert_not_called()
    assert calls[0]["timeout"] == 9


def test_local_answer_is_used_without_fast_model(monkeypatch):
    called = []
    _fast_answer(monkeypatch, {"valid": False}, called)
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.return_value = '{"valid": true}'
    assert grader.grade("sys", "user", SCHEMA) == {"valid": True}
    assert called == []


def test_both_failing_keeps_local_detail_and_names_fast_model():
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_generate.side_effect = OllamaConnectionError("refused")
    result = grader.grade("sys", "user", SCHEMA)
    assert result["infrastructure_error"] is True
    assert "refused" in result["error_detail"]
    assert "fast model unavailable" in result["error_detail"]


def test_client_selection_failure_falls_back_to_fast_model(monkeypatch, tmp_path):
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    cfg = _write(tmp_path, {"backend": "openai"})  # no openai.base_url: OpenAiClient raises
    with patch("requests.post", side_effect=AssertionError("no local request without a client")):
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}
    assert len(calls) == 1


def test_client_selection_failure_with_fast_model_down_returns_infrastructure_error(monkeypatch, tmp_path):
    monkeypatch.setattr(fast_model, "_run_child",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""))
    cfg = _write(tmp_path, {"backend": "openai"})
    result = LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA, read_timeout=2)
    assert result["infrastructure_error"] is True
    assert "base_url" in result["error_detail"]
    assert "fast model unavailable" in result["error_detail"]


def test_profile_error_does_not_reach_the_fast_model(monkeypatch):
    from ironclaude.communication_profiles import CommunicationProfileError
    called = []
    _fast_answer(monkeypatch, {"valid": True}, called)
    grader = LocalGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    with patch("ironclaude.grader.apply_communication_profile", side_effect=CommunicationProfileError("missing")):
        result = grader.grade("sys", "user", SCHEMA)
    assert result == {"infrastructure_error": True, "error_detail": "missing"}
    assert called == []


def test_fast_lane_uses_fast_lane_bound_and_signals(monkeypatch, tmp_path):
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    cfg = _write(tmp_path, {"backend": "haiku"})
    signalled = []
    out = LocalGrader(config_path=cfg).grade("s", "u", SCHEMA, read_timeout=2,
                                             on_fast_model=lambda: signalled.append(True))
    assert out == {"valid": True}
    assert signalled == [True]
    assert calls[0]["timeout"] == fast_model.FAST_LANE_FAST_MODEL_SECONDS


def test_grader_role_order_reaches_codex(monkeypatch, tmp_path):
    calls = []
    def fake(argv, *, input_text, timeout, env):
        calls.append(argv[0])
        text = json.dumps({"valid": True})
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
        return subprocess.CompletedProcess(argv, 0, line, "")
    monkeypatch.setattr(fast_model, "_run_child", fake)
    commander_cfg = {"providers": {
        "clients": {"claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku"}},
                    "codex": {"enabled": True, "path": "codex", "models": {"haiku": "gpt-5.6-luna"}}},
        "roles": {"grader": {"preferred": "codex", "clients": ["codex"]}}}}
    cfg = _write(tmp_path, {"backend": "haiku"})
    assert LocalGrader(config_path=cfg, commander_config=commander_cfg).grade("s", "u", SCHEMA) == {"valid": True}
    assert calls == ["codex"]


# ── _grade_bounded: the bound grows only while the fast model answers ──

def _daemon(timeout):
    from ironclaude.main import IroncladeDaemon
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon._grader = MagicMock()
    daemon._grade_state_lock = threading.Lock()
    daemon._grade_in_flight = False
    daemon.config = {"fast_lane_grade_timeout_seconds": timeout}
    return daemon


def test_grade_bounded_extends_bound_for_fast_model(monkeypatch):
    monkeypatch.setattr(fast_model, "FAST_LANE_FAST_MODEL_SECONDS", 2.0)
    daemon = _daemon(0.2)

    def slow_fast(system, user, schema, read_timeout=None, on_fast_model=None):
        on_fast_model()
        time.sleep(0.6)
        return {"waiting_on": "operator"}

    daemon._grader.grade.side_effect = slow_fast
    assert daemon._grade_bounded("s", "u", {}) == {"waiting_on": "operator"}


def test_grade_bounded_keeps_bound_for_local(monkeypatch):
    monkeypatch.setattr(fast_model, "FAST_LANE_FAST_MODEL_SECONDS", 2.0)
    daemon = _daemon(0.2)

    def slow_local(system, user, schema, read_timeout=None, on_fast_model=None):
        time.sleep(0.6)
        return {"waiting_on": "operator"}

    daemon._grader.grade.side_effect = slow_local
    assert daemon._grade_bounded("s", "u", {}) is None


# ── shadow grader skips quietly ──

def test_shadow_skips_under_haiku_without_building_a_client(tmp_path):
    cfg = _write(tmp_path, {"backend": "haiku"})
    with patch("ironclaude.shadow_grader.OllamaClient") as ollama:
        result = ShadowGrader(config_path=cfg).grade_with_tools("s", "u", repo_path=str(tmp_path))
    ollama.assert_not_called()
    assert result["skipped"] is True and result["infrastructure_error"] is True


def test_shadow_marks_unreachable_box():
    grader = ShadowGrader(config_path="/nonexistent/config.json")
    grader._client = MagicMock()
    grader._client.post_chat.side_effect = OllamaConnectionError("refused")
    assert grader.grade_with_tools("s", "u", repo_path="/tmp")["unreachable"] is True


def _tools(tmp_path, hooks_cfg):
    import ironclaude.orchestrator_mcp as omcp
    from ironclaude.db import init_db
    tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                                   db_conn=init_db(str(tmp_path / "c.db")), config={})
    tools._ollama_config_path = _write(tmp_path, hooks_cfg)
    return tools


@pytest.mark.parametrize("shadow", [
    {"infrastructure_error": True, "skipped": True, "error_detail": "haiku", "tool_calls": []},
    {"infrastructure_error": True, "unreachable": True, "error_detail": "refused", "tool_calls": []},
])
def test_shadow_report_posts_nothing_when_skipped_or_unreachable(tmp_path, shadow):
    tools = _tools(tmp_path, {"backend": "ollama"})
    tools._slack = MagicMock()
    tools._shadow_grader = MagicMock()
    tools._shadow_grader.grade_with_tools.return_value = shadow
    tools._run_shadow_and_report("ctx", "w1", "/tmp", {"grade": "A", "approved": True, "feedback": ""}, [], "s", "u")
    tools._slack.post_message.assert_not_called()


# ── session summarization ──

def _session(tools):
    tools.tmux.list_sessions.return_value = ["my-session"]
    tools.tmux.list_pane_pid.return_value = "1"
    tools.tmux.pane_current_command.return_value = "node"
    tools.tmux.capture_pane.return_value = "terminal content"


def test_summarization_haiku_branch(monkeypatch, tmp_path):
    _fast_answer(monkeypatch, {"summary": "S"})
    tools = _tools(tmp_path, {"backend": "haiku"})
    _session(tools)
    tools._get_ollama_client = MagicMock(side_effect=AssertionError("no local box under haiku"))
    assert json.loads(tools.list_claude_sessions())[0]["summary"] == "S"


def test_summarization_haiku_failure_is_explicit(tmp_path):
    tools = _tools(tmp_path, {"backend": "haiku"})
    _session(tools)
    summary = json.loads(tools.list_claude_sessions())[0]["summary"]
    assert summary.startswith("ERROR: Haiku-class summarization failed")


def test_summarization_ollama_unavailable_chains_to_fast_model(monkeypatch, tmp_path):
    from ironclaude.ollama_client import OllamaError
    _fast_answer(monkeypatch, {"summary": "S"})
    tools = _tools(tmp_path, {"backend": "ollama"})
    _session(tools)
    client = MagicMock()
    client.get_ps.side_effect = OllamaError("refused")
    tools._get_ollama_client = MagicMock(return_value=client)
    assert json.loads(tools.list_claude_sessions())[0]["summary"] == "S"
    client.post_generate.assert_not_called()


# ── LocalGrader model class: sonnet never uses the local box ──

def test_sonnet_class_skips_the_local_box(monkeypatch, tmp_path):
    cfg = _write(tmp_path, {"backend": "ollama", "ollama": {"url": "http://box.invalid:11434", "model": "m"},
                            "sonnet_model_timeout_seconds": 13})
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    with patch("ironclaude.grader.OllamaClient") as ollama, patch("ironclaude.grader.make_client") as make, \
            patch("requests.post", side_effect=AssertionError("no local request at the sonnet class")):
        assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA, tier="sonnet") == {"valid": True}
    ollama.assert_not_called()
    make.assert_not_called()
    assert [c["argv"][c["argv"].index("--model") + 1] for c in calls] == ["sonnet"]
    assert calls[0]["timeout"] == 13


def test_sonnet_class_failure_is_infrastructure_error_without_step_up(monkeypatch, tmp_path):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(fast_model, "_run_child", fake)
    cfg = _write(tmp_path, {"backend": "haiku"})
    result = LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA, tier="sonnet")
    assert result["infrastructure_error"] is True
    assert "sonnet exited 1" in result["error_detail"]
    assert [a[a.index("--model") + 1] for a in calls] == ["sonnet"]


def test_sonnet_class_uses_codex_terra_from_grader_role(monkeypatch, tmp_path):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv)
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"valid": True})}})
        return subprocess.CompletedProcess(argv, 0, line, "")

    monkeypatch.setattr(fast_model, "_run_child", fake)
    commander_cfg = {"providers": {
        "clients": {"claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku", "sonnet": "sonnet"}},
                    "codex": {"enabled": True, "path": "codex",
                              "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra"}}},
        "roles": {"grader": {"preferred": "codex", "clients": ["codex"]}}}}
    cfg = _write(tmp_path, {"backend": "ollama"})
    with patch("ironclaude.grader.OllamaClient") as ollama:
        out = LocalGrader(config_path=cfg, commander_config=commander_cfg).grade("s", "u", SCHEMA, tier="sonnet")
    assert out == {"valid": True}
    ollama.assert_not_called()
    assert calls[0][calls[0].index("-m") + 1] == "gpt-5.6-terra"


def test_default_class_keeps_the_haiku_chain_positive_control(monkeypatch, tmp_path):
    cfg = _write(tmp_path, {"backend": "haiku"})
    calls = []
    _fast_answer(monkeypatch, {"valid": True}, calls)
    assert LocalGrader(config_path=cfg).grade("sys", "user", SCHEMA) == {"valid": True}
    assert calls[0]["argv"][calls[0]["argv"].index("--model") + 1] == "haiku"
    assert calls[0]["timeout"] == fast_model.FAST_MODEL_TIMEOUT_DEFAULT
