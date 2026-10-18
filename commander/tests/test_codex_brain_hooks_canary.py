"""R8: the Codex Brain may run only after its UserPromptSubmit canary proves the
Brain restriction hooks are trusted and running."""
from unittest.mock import MagicMock

from ironclaude import paths

from ironclaude.codex_brain_client import CodexBrainClient


class _FakeProc:
    pid = 4321
    stdin = MagicMock()
    stdout = MagicMock()
    stderr = MagicMock()

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def _client(monkeypatch, tmp_path, write_canary):
    import ironclaude.codex_brain_client as module
    root = paths.brain_gate_root()
    monkeypatch.setattr(module, "_HOOKS_CANARY_TIMEOUT", 0.3)
    client = CodexBrainClient()
    monkeypatch.setattr(client, "_runtime_preflight", lambda: {
        "schema_version": 1, "status": "healthy", "reason": "destination-equivalent",
        "source_companion": "/s", "destination_companion": "/d",
    })
    monkeypatch.setattr(client, "_preflight_orchestrator", lambda: None)
    monkeypatch.setattr(client, "_reader_loop", lambda: None)
    monkeypatch.setattr(client, "_stderr_loop", lambda: None)
    monkeypatch.setattr(client, "_verify_orchestrator_mcp", lambda: None)
    responses = [{"result": {}}, {"result": {"thread": {"id": "thread-1"}}}]
    monkeypatch.setattr(client, "_await_response",
                        lambda request_id, timeout: responses.pop(0) if responses else None)

    def write(payload):
        if payload.get("method") == "turn/start" and write_canary:
            marker = root / client._brain_gate_session / "hooks-live"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        return True

    monkeypatch.setattr(client, "_write", write)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: _FakeProc())
    return client


def test_canary_present_keeps_brain_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, write_canary=True)
    client.start("system prompt", cwd="/tmp")
    assert client._running is True
    assert client.restart_reason == ""


def test_canary_absent_stops_brain_visibly(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, write_canary=False)
    client.start("system prompt", cwd="/tmp")
    assert client._running is False
    assert "hooks are not running" in client.restart_reason
    assert any("[CODEX BRAIN ERROR]" in r for r in client.get_pending_responses())


def test_stale_canary_from_earlier_start_does_not_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, write_canary=False)
    stale = paths.brain_gate_root() / client._brain_gate_session / "hooks-live"
    stale.parent.mkdir(parents=True)
    stale.touch()
    client.start("system prompt", cwd="/tmp")
    assert client._running is False
