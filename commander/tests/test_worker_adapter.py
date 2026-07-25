from unittest.mock import MagicMock
import ironclaude.orchestrator_mcp as omcp
from ironclaude.db import init_db

CLAUDE_MODELS = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}
CODEX_MODELS = {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}


def _cfg(worker_pref="claude", worker_clients=("claude",), codex_enabled=False):
    return {
        "grader_model": "opus", "brain_model": "sonnet", "default_opus_model": "opus",
        "advisor": {"advisor_model": "opus", "advisor_models": {}},
        "providers": {
            "clients": {
                "claude": {"enabled": True, "path": "claude", "models": CLAUDE_MODELS},
                "codex": {"enabled": codex_enabled, "path": "codex", "models": CODEX_MODELS},
            },
            "roles": {
                "brain": {"preferred": "claude", "clients": ["claude"]},
                "worker": {"preferred": worker_pref, "clients": list(worker_clients)},
                "grader": {"preferred": "claude", "clients": ["claude"]},
                "advisor": {"preferred": "claude", "clients": ["claude"]},
            },
        },
    }


def _legacy_cfg():
    return {"grader_model": "opus", "default_opus_model": "opus", "brain_model": "sonnet",
            "advisor": {"advisor_model": "opus", "advisor_models": {}}}


def _tools(tmp_path, cfg, dbname="c.db"):
    conn = init_db(str(tmp_path / dbname))
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=conn, config=cfg)


def _codex_avail(monkeypatch):
    from ironclaude.provider_capabilities import CapabilityProbe, ClientCapability
    monkeypatch.setattr(CapabilityProbe, "probe_local", lambda self, c, cl, r, t: ClientCapability(
        host="local", client=cl, role=r, tier=t, configured=True, supported=True,
        installed=True, authenticated=True, available=True, reason=None))


def test_routed_claude_equals_legacy_per_tier(tmp_path):
    """R2 byte-identical: routed-through-claude == legacy claude, per tier (the PRODUCTION path)."""
    routed = _tools(tmp_path, _cfg(), "r.db")
    legacy = _tools(tmp_path, _legacy_cfg(), "l.db")
    for wt in ("claude-opus", "claude-sonnet", "claude-fable"):
        assert routed._get_worker_command(wt, "") == legacy._get_worker_command(wt, "")


def test_codex_worker_command(tmp_path, monkeypatch):
    tools = _tools(tmp_path, _cfg(worker_pref="codex", worker_clients=("codex",), codex_enabled=True))
    _codex_avail(monkeypatch)
    cmd = tools._get_worker_command("claude-opus", "")
    assert "codex" in cmd and "gpt-5.6-sol" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "[1m]" not in cmd and "ANTHROPIC" not in cmd


def test_codex_fable_degrades_to_opus(tmp_path, monkeypatch):
    """fable-tier codex must degrade to codex opus (M1), not fall back to claude."""
    tools = _tools(tmp_path, _cfg(worker_pref="codex", worker_clients=("codex",), codex_enabled=True))
    _codex_avail(monkeypatch)
    monkeypatch.setattr(omcp, "_resolve_fable_worker_type", lambda wt: wt)  # keep claude-fable
    cmd = tools._get_worker_command("claude-fable", "")
    assert "codex" in cmd and "gpt-5.6-sol" in cmd


def test_invalid_worker_type_still_raises_under_codex_cfg(tmp_path, monkeypatch):
    import pytest
    tools = _tools(tmp_path, _cfg(worker_pref="codex", worker_clients=("codex",), codex_enabled=True))
    _codex_avail(monkeypatch)
    with pytest.raises(ValueError):
        tools._get_worker_command("bogus-type", "")


def test_legacy_config_worker_is_claude(tmp_path):
    tools = _tools(tmp_path, _legacy_cfg())
    cmd = tools._get_worker_command("claude-opus", "")
    assert "exec claude" in cmd and "codex" not in cmd


class _FakeTmux:
    """Minimal tmux double: read_log_tail returns scripted outputs in sequence."""
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.sent = []
    def read_log_tail(self, name, lines=50, ssh_host=None, remote_log_dir=None):
        return self._outputs.pop(0) if self._outputs else ""
    def send_keys(self, name, text, ssh_host=None):
        self.sent.append(text)
        return True


def _tools_with_tmux(tmp_path, tmux):
    conn = init_db(str(tmp_path / "c.db"))
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=tmux, db_conn=conn, config=_legacy_cfg())


def test_wait_for_ready_codex_trust_and_marker(tmp_path):
    tmux = _FakeTmux([
        "  Do you trust the contents of this directory?\n  1. Yes, continue",
        ">_ OpenAI Codex (v0.145.0)\n  model: gpt-5.6-sol high",
    ])
    tools = _tools_with_tmux(tmp_path, tmux)
    assert tools._wait_for_ready("ic-w", timeout=5, client="codex") is True
    assert tmux.sent == [""]   # dismissed trust exactly ONCE (Enter)


def test_wait_for_ready_claude_unchanged(tmp_path):
    tmux = _FakeTmux([
        "trust this folder?",
        "ironclaude v1.0.25 ready",
    ])
    tools = _tools_with_tmux(tmp_path, tmux)
    assert tools._wait_for_ready("ic-w", timeout=5, client="claude") is True
    assert tmux.sent == [""]   # claude trust dismissal unchanged


import sqlite3 as _sqlite3
from unittest.mock import patch

_SESSIONS_SCHEMA = (
    "CREATE TABLE sessions (terminal_session TEXT PRIMARY KEY, "
    "professional_mode TEXT NOT NULL DEFAULT 'undecided', "
    "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
)
_AUDIT_SCHEMA = (
    "CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT, "
    "actor TEXT, action TEXT, old_value TEXT, new_value TEXT, context TEXT, "
    "created_at TEXT DEFAULT (datetime('now')))"
)
_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # 36 chars


def _claude_dir_with_db(tmp_path, id_pid):
    d = tmp_path / ".claude"
    d.mkdir(exist_ok=True)
    (d / f"ironclaude-session-{id_pid}.id").write_text(_UUID)
    conn = _sqlite3.connect(str(d / "ironclaude.db"))
    conn.execute(_SESSIONS_SCHEMA)
    conn.execute(_AUDIT_SCHEMA)
    conn.commit(); conn.close()
    return d


def _pm(tmp_path):
    conn = init_db(str(tmp_path / "c.db"))
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=conn, config=_legacy_cfg())


def test_set_pm_codex_subtree_walk(tmp_path, monkeypatch):
    """codex: id file keyed to a DESCENDANT pid (not pane_pid) is found via subtree walk."""
    pane_pid, child_pid = "5000", "5042"
    claude_dir = _claude_dir_with_db(tmp_path, child_pid)   # id file at CHILD pid
    tools = _pm(tmp_path)
    monkeypatch.setattr(tools, "_descendant_pids", lambda p: [int(pane_pid), int(child_pid)])
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=f"{pane_pid}\n")):
        result = tools._set_pm_via_sqlite("ic-w", "on", timeout=2, client="codex", _claude_dir=claude_dir)
    assert result is None
    row = _sqlite3.connect(str(claude_dir / "ironclaude.db")).execute(
        "SELECT professional_mode FROM sessions WHERE terminal_session=?", (_UUID,)).fetchone()
    assert row[0] == "on"


def test_set_pm_claude_polls_pane_pid(tmp_path):
    """claude branch unchanged: id file keyed to pane_pid."""
    pane_pid = "6000"
    claude_dir = _claude_dir_with_db(tmp_path, pane_pid)    # id file at PANE pid
    tools = _pm(tmp_path)
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=f"{pane_pid}\n")):
        result = tools._set_pm_via_sqlite("ic-w", "on", timeout=2, client="claude", _claude_dir=claude_dir)
    assert result is None
    row = _sqlite3.connect(str(claude_dir / "ironclaude.db")).execute(
        "SELECT professional_mode FROM sessions WHERE terminal_session=?", (_UUID,)).fetchone()
    assert row[0] == "on"


def _spawn_tools(tmp_path, cfg, advisor_enabled=False, monkeypatch=None, ready_output=">_ OpenAI Codex ready"):
    conn = init_db(str(tmp_path / "c.db"))
    mock_tmux = MagicMock()
    mock_tmux.spawn_session.return_value = True
    mock_tmux.has_session.return_value = True
    mock_tmux.read_log_tail.return_value = ready_output
    registry = MagicMock()
    registry.get_running_workers_by_type.return_value = []
    advisor_cfg = {"enabled": True, "advisor_model": "opus"} if advisor_enabled else {}
    t = omcp.OrchestratorTools(registry, mock_tmux, config=cfg, db_conn=conn, advisor_cfg=advisor_cfg)
    t._get_ollama_vram = MagicMock(return_value=(0.0, []))
    t._check_spawn_preconditions = MagicMock(return_value=None)
    t.ensure_worker_trusted = MagicMock()
    t._ensure_claude_md = MagicMock()
    t._activate_pm_via_sqlite = MagicMock(return_value=None)
    t._wait_for_ready = MagicMock(return_value=True)
    t._call_grader = MagicMock(return_value={"grade": "A", "approved": True, "feedback": ""})
    t._call_local_grader = MagicMock(return_value={"grade": "A", "approved": True, "feedback": ""})
    return t, mock_tmux, registry


def test_spawn_codex_threads_client_and_gates_slash(tmp_path, monkeypatch):
    _codex_avail(monkeypatch)
    tools, tmux, registry = _spawn_tools(
        tmp_path, _cfg(worker_pref="codex", worker_clients=("codex",), codex_enabled=True),
        advisor_enabled=True)
    tools.spawn_worker(worker_id="w1", worker_type="claude-opus", repo=str(tmp_path), objective="do X")
    # codex command spawned
    spawn_cmd = tmux.spawn_session.call_args.args[1]
    assert "codex" in spawn_cmd and "gpt-5.6-sol" in spawn_cmd
    # client threaded into wait + activate
    assert tools._wait_for_ready.call_args.kwargs.get("client") == "codex"
    assert tools._activate_pm_via_sqlite.call_args.kwargs.get("client") == "codex"
    # claude slash commands gated OFF for codex
    keys = [c.args[1] for c in tmux.send_keys.call_args_list]
    assert not any(k.startswith("/advisor") for k in keys)
    assert not any(k.startswith("/goal") for k in keys)
    # client+model persisted
    registry.set_worker_provider.assert_called_once_with("w1", "codex", "gpt-5.6-sol")


def test_spawn_claude_still_sends_advisor(tmp_path, monkeypatch):
    tools, tmux, registry = _spawn_tools(
        tmp_path, _cfg(), advisor_enabled=True, ready_output="ironclaude v1.0 ready")
    tools.spawn_worker(worker_id="w2", worker_type="claude-sonnet", repo=str(tmp_path), objective="do Y")
    assert tools._wait_for_ready.call_args.kwargs.get("client") == "claude"
    keys = [c.args[1] for c in tmux.send_keys.call_args_list]
    assert any(k.startswith("/advisor") for k in keys)   # advisor still sent for claude
