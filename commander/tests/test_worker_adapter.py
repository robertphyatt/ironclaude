from unittest.mock import MagicMock
import ironclaude.orchestrator_mcp as omcp
from ironclaude.db import init_db
from ironclaude.tmux_manager import _strip_ansi

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
    def __init__(self, outputs, sanitize=False):
        self._outputs = list(outputs)
        self._sanitize = sanitize
        self.read_count = 0
        self.sent = []
        self.raw_sent = []
    def read_log_tail(self, name, lines=50, ssh_host=None, remote_log_dir=None):
        self.read_count += 1
        output = self._outputs.pop(0) if self._outputs else ""
        return _strip_ansi(output) if self._sanitize else output
    def send_keys(self, name, text, ssh_host=None):
        self.sent.append(text)
        return True
    def send_raw_keys(self, name, keys, ssh_host=None):
        self.raw_sent.append(keys)
        return True


def _tools_with_tmux(tmp_path, tmux):
    conn = init_db(str(tmp_path / "c.db"))
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=tmux, db_conn=conn, config=_legacy_cfg())


def test_wait_for_ready_codex_trust_hooks_and_marker(tmp_path):
    trust_screen = (
        "\x1b[2J\x1b[1;1HDo\x1b[1;4Hyou\x1b[1;8Htrust"
        "\x1b[1;14Hthe\x1b[1;18Hcontents\x1b[1;27Hof\x1b[1;30Hthis"
        "\x1b[1;35Hdirectory?\n\x1b[2;1H1.\x1b[2;4HYes,\x1b[2;9Hcontinue"
    )
    hooks_screen = (
        trust_screen
        + "\n\x1b[2;3HHooks\x1b[2;9Hneed\x1b[2;14Hreview"
        "\n\x1b[6;1H1.\x1b[6;4HReview\x1b[6;11Hhooks"
        "\n\x1b[7;1H2.\x1b[7;4HTrust\x1b[7;10Hall\x1b[7;14Hand"
        "\x1b[7;18Hcontinue"
        "\n\x1b[8;1H3.\x1b[8;4HContinue\x1b[8;13Hwithout"
        "\x1b[8;21Htrusting"
    )
    welcome_marker = (
        "\n\x1b[20;1H>_\x1b[20;4HOpenAI\x1b[20;11HCodex"
        "\x1b[20;17H(v0.146.0)\n\x1b[21;1Hmodel:\x1b[21;8Hgpt-5.6-sol"
    )
    reopened_hooks_screen = (
        hooks_screen
        + welcome_marker
        + "\n\x1b[22;1HHooks\x1b[22;7H7\x1b[22;9Hhooks\x1b[22;15Hneed"
        "\x1b[22;20Hreview\n\x1b[23;1HPress\x1b[23;7Ht\x1b[23;9Hto"
        "\x1b[23;12Htrust\x1b[23;18Hall"
    )
    fresh_ready_screen = reopened_hooks_screen + welcome_marker
    hooks_with_stale_welcome = hooks_screen + welcome_marker
    tmux = _FakeTmux(
        [
            trust_screen,
            trust_screen,
            hooks_with_stale_welcome,
            reopened_hooks_screen,
            fresh_ready_screen,
        ],
        sanitize=True,
    )
    tools = _tools_with_tmux(tmp_path, tmux)
    assert tools._wait_for_ready("ic-w", timeout=5, client="codex") is True
    assert tmux.sent == [""]
    assert tmux.raw_sent == [["2"]]
    assert tmux.read_count == 5
    assert tmux._outputs == []


def test_wait_for_ready_codex_does_not_match_unrelated_hooks_fragments(tmp_path):
    trust_screen = "Do you trust the contents of this directory?"
    unrelated_hooks = (
        "Hooks need review"
        + ("x" * 501)
        + "2. Trust all and continue"
    )
    tmux = _FakeTmux(
        [trust_screen, unrelated_hooks, ">_ OpenAI Codex"],
        sanitize=True,
    )
    tools = _tools_with_tmux(tmp_path, tmux)

    assert tools._wait_for_ready("ic-w", timeout=5, client="codex") is True
    assert tmux.sent == [""]
    assert tmux.raw_sent == []


def test_wait_for_ready_codex_requires_directory_trust_before_hooks(tmp_path):
    hooks_screen = "Hooks need review\n2. Trust all and continue"
    tmux = _FakeTmux([hooks_screen, ">_ OpenAI Codex"], sanitize=True)
    tools = _tools_with_tmux(tmp_path, tmux)

    assert tools._wait_for_ready("ic-w", timeout=5, client="codex") is True
    assert tmux.sent == []
    assert tmux.raw_sent == []


def test_wait_for_ready_codex_requires_exact_hooks_option_two(tmp_path):
    trust_screen = "Do you trust the contents of this directory?"
    wrong_option = "Hooks need review\n12. Trust all and continue"
    tmux = _FakeTmux(
        [trust_screen, wrong_option, ">_ OpenAI Codex"],
        sanitize=True,
    )
    tools = _tools_with_tmux(tmp_path, tmux)

    assert tools._wait_for_ready("ic-w", timeout=5, client="codex") is True
    assert tmux.sent == [""]
    assert tmux.raw_sent == []


def test_wait_for_ready_codex_does_not_match_unrelated_fragments(tmp_path):
    unrelated = (
        "\x1b[1;1HUndo\x1b[1;6Hyou\x1b[1;10Htrustworthy"
        "\x1b[1;22Hchanges\x1b[1;30Hin\x1b[1;33Hthis\x1b[1;38Hdirectory"
    )
    ready = ">_ OpenAI Codex"
    tmux = _FakeTmux([unrelated, ready], sanitize=True)
    tools = _tools_with_tmux(tmp_path, tmux)

    assert tools._wait_for_ready("ic-w", timeout=5, client="codex") is True
    assert tmux.sent == []


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
    t._read_pm_state_via_sqlite = MagicMock(return_value={
        "professional_mode": "on",
        "workflow_stage": "idle",
        "session_uuid": _UUID,
    })
    t._wait_for_ready = MagicMock(return_value=True)
    t._call_grader = MagicMock(return_value={"grade": "A", "approved": True, "feedback": ""})
    t._call_local_grader = MagicMock(return_value={"grade": "A", "approved": True, "feedback": ""})
    # Without this the spawn path builds a real WorkspaceClient, which reads the
    # OPERATOR'S ~/.claude/plugins/cache and the installed commander version.
    # These tests then pass or fail according to which plugin versions happen to
    # be installed on the machine — they are about worker-adapter threading, not
    # about workspace allocation.
    workspace = MagicMock()
    workspace.discover_installed_plugin_root.return_value = "/installed/ironclaude"

    def _assignment(payload, **_transport):
        guid = payload.get("workspace_guid", "44444444-4444-4444-8444-444444444444")
        return {
            "workspace_guid": guid,
            "repository_identity": "identity:test",
            "worktree_path": str(tmp_path),
            "branch": f"ironclaude/{guid}",
            "base_commit": "a" * 40,
            "current_head": "a" * 40,
            "owner_session_id": None,
            "lifecycle_status": "active",
            "integration_target": payload.get("integration_target", "main"),
        }

    workspace.allocate.side_effect = _assignment
    workspace.bind.side_effect = lambda payload, **transport: {
        **_assignment(payload),
        "owner_session_id": payload["owner_session_id"],
    }
    t._workspace_client = workspace
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
    assert isinstance(tools._activate_pm_via_sqlite.call_args.kwargs["not_before"], float)
    # claude slash commands gated OFF for codex
    keys = [c.args[1] for c in tmux.send_keys.call_args_list]
    assert not any(k.startswith("/advisor") for k in keys)
    assert not any(k.startswith("/goal") for k in keys)
    # native identity + client/model persisted atomically
    tools._read_pm_state_via_sqlite.assert_called_once()
    assert tools._read_pm_state_via_sqlite.call_args.args == ("ic-w1",)
    assert tools._read_pm_state_via_sqlite.call_args.kwargs["client"] == "codex"
    assert isinstance(tools._read_pm_state_via_sqlite.call_args.kwargs["not_before"], float)
    registry.register_worker.assert_called_once()
    register_call = registry.register_worker.call_args
    assert register_call.args[0] == "w1"
    assert register_call.kwargs["client"] == "codex"
    assert register_call.kwargs["model"] == "gpt-5.6-sol"
    assert register_call.kwargs["native_session_id"] == _UUID


def test_spawn_claude_still_sends_advisor(tmp_path, monkeypatch):
    tools, tmux, registry = _spawn_tools(
        tmp_path, _cfg(), advisor_enabled=True, ready_output="ironclaude v1.0 ready")
    tools.spawn_worker(worker_id="w2", worker_type="claude-sonnet", repo=str(tmp_path), objective="do Y")
    assert tools._wait_for_ready.call_args.kwargs.get("client") == "claude"
    keys = [c.args[1] for c in tmux.send_keys.call_args_list]
    assert any(k.startswith("/advisor") for k in keys)   # advisor still sent for claude
