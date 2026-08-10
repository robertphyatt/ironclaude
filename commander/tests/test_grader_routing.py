import json
from unittest.mock import MagicMock, patch
import ironclaude.orchestrator_mcp as omcp
from ironclaude.db import init_db


def _tools(tmp_path):
    conn = init_db(str(tmp_path / "commander.db"))
    cfg = {
        "grader_model": "opus", "brain_model": "sonnet", "default_opus_model": "opus",
        "advisor": {"advisor_model": "opus", "advisor_models": {}},
        "providers": {
            "clients": {
                "claude": {"enabled": True, "path": "claude",
                           "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "claude-opus-4-8", "fable": "fable"}},
                "codex": {"enabled": False, "path": "codex",
                          "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}},
            },
            "roles": {r: {"preferred": "claude", "clients": ["claude"]}
                      for r in ("brain", "worker", "grader", "advisor")},
        },
    }
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=conn, config=cfg)


def _fake_result(verdict):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps([{"type": "result", "structured_output": verdict}])
    proc.stderr = ""
    return proc


EXPECTED_CLAUDE_ARGV_HEAD = ["claude", "-p", "--system-prompt-file"]


def test_claude_grader_argv_is_stable(tmp_path):
    tools = _tools(tmp_path)
    argv = tools._claude_grader_argv(tools._GRADER_VERDICT_SCHEMA, "/tmp/sys.txt", "opus")
    assert argv[:3] == EXPECTED_CLAUDE_ARGV_HEAD
    assert argv[3] == "/tmp/sys.txt"
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert "--json-schema" in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "opus[1m]"  # opus needs 1m beta
    assert "--dangerously-skip-permissions" in argv
    assert "--strict-mcp-config" in argv
    assert "--disallowedTools" in argv


def test_call_grader_claude_returns_verdict(tmp_path):
    tools = _tools(tmp_path)
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "A", "approved": True, "feedback": "ok"})) as run:
        out = tools._call_grader("sys", "user")
    assert out == {"grade": "A", "approved": True, "feedback": "ok"}
    called_argv = run.call_args.args[0]
    assert called_argv[0] == "claude" and "--model" in called_argv
    assert called_argv[called_argv.index("--model") + 1] == "opus[1m]"


def _codex_enabled_cfg(tmp_path, clients=("codex",)):
    conn = init_db(str(tmp_path / "commander.db"))
    cfg = {
        "grader_model": "opus", "brain_model": "sonnet", "default_opus_model": "opus",
        "advisor": {"advisor_model": "opus", "advisor_models": {}},
        "providers": {
            "clients": {
                "claude": {"enabled": True, "path": "claude",
                           "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "claude-opus-4-8", "fable": "fable"}},
                "codex": {"enabled": True, "path": "codex",
                          "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}},
            },
            "roles": {
                "brain": {"preferred": "claude", "clients": ["claude"]},
                "worker": {"preferred": "claude", "clients": ["claude"]},
                "grader": {"preferred": "codex", "clients": list(clients)},
                "advisor": {"preferred": "claude", "clients": ["claude"]},
            },
        },
    }
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=conn, config=cfg)


def test_legacy_config_without_providers_uses_claude(tmp_path):
    """A minimal config with NO providers block -> byte-identical legacy claude path
    (never raises ProviderConfigError out of _call_grader)."""
    conn = init_db(str(tmp_path / "commander.db"))
    tools = omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(),
                                   db_conn=conn, config={"grader_model": "opus"})
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "C", "approved": False, "feedback": "meh"})) as run:
        out = tools._call_grader("sys", "user")
    assert out["grade"] == "C"
    argv = run.call_args.args[0]
    assert argv[0] == "claude"
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8[1m]"


def test_routed_claude_argv_still_opus(tmp_path):
    """Default (claude) config: routed path still builds the opus[1m] claude grader."""
    tools = _tools(tmp_path)
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "B", "approved": True, "feedback": "ok"})) as run:
        out = tools._call_grader("sys", "user")
    assert out["grade"] == "B"
    argv = run.call_args.args[0]
    assert argv[0] == "claude"
    assert argv[argv.index("--model") + 1] == "opus[1m]"


def test_no_capability_returns_F(tmp_path, monkeypatch):
    """grader.clients=[codex] but codex probes unavailable, no claude fallback -> grade F, no crash.

    subprocess.run is patched to a NON-F verdict so this test is isolated (no real claude) AND
    genuinely depends on the routing preamble: without routing, _call_grader would run the (patched)
    claude path and return 'A' -> the grade==F assertion fails (RED). With routing, resolve() raises
    NoCapabilityAvailable and returns F before any subprocess -> GREEN.
    """
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    from ironclaude.provider_capabilities import CapabilityProbe, ClientCapability
    monkeypatch.setattr(CapabilityProbe, "probe_local", lambda self, cfg, client, role, tier: ClientCapability(
        host="local", client=client, role=role, tier=tier, configured=True, supported=True,
        installed=True, authenticated=False, available=False, reason="not_authenticated"))
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "A", "approved": True, "feedback": "ok"})):
        out = tools._call_grader("sys", "user")
    assert out["grade"] == "F" and out["approved"] is False


def test_codex_unavailable_falls_back_to_claude(tmp_path, monkeypatch):
    """grader.clients=[codex,claude], codex unavailable -> router resolves claude."""
    tools = _codex_enabled_cfg(tmp_path, clients=("codex", "claude"))
    from ironclaude.provider_capabilities import CapabilityProbe, ClientCapability
    def fake_probe(self, cfg, client, role, tier):
        avail = client == "claude"
        return ClientCapability(host="local", client=client, role=role, tier=tier,
                                configured=True, supported=True, installed=True,
                                authenticated=(True if client == "claude" else False),
                                available=avail, reason=None if avail else "not_authenticated")
    monkeypatch.setattr(CapabilityProbe, "probe_local", fake_probe)
    with patch.object(omcp.subprocess, "run", return_value=_fake_result(
            {"grade": "A", "approved": True, "feedback": "ok"})) as run:
        out = tools._call_grader("sys", "user")
    assert out["grade"] == "A"
    assert run.call_args.args[0][0] == "claude"     # fell back to claude


import pathlib
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _codex_avail(monkeypatch):
    from ironclaude.provider_capabilities import CapabilityProbe, ClientCapability
    monkeypatch.setattr(CapabilityProbe, "probe_local", lambda self, cfg, client, role, tier: ClientCapability(
        host="local", client=client, role=role, tier=tier, configured=True, supported=True,
        installed=True, authenticated=True, available=True, reason=None))


def _codex_proc(jsonl_name):
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = (FIXTURES / jsonl_name).read_text()
    proc.stderr = ""
    return proc


def test_parse_codex_grader_output(tmp_path):
    tools = _codex_enabled_cfg(tmp_path)
    out = tools._parse_codex_grader_output(_codex_proc("codex_grader_verdict.jsonl"), batch=False)
    assert out == {"grade": "B", "approved": True, "feedback": "looks fine"}


def test_parse_codex_grader_malformed_is_F(tmp_path):
    tools = _codex_enabled_cfg(tmp_path)
    proc = MagicMock(); proc.returncode = 0; proc.stderr = ""
    proc.stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"not json"}}\n'
    out = tools._parse_codex_grader_output(proc, batch=False)
    assert out["grade"] == "F"


def test_codex_argv_no_1m_and_model(tmp_path):
    tools = _codex_enabled_cfg(tmp_path)
    argv = tools._codex_grader_argv("/tmp/schema.json", "gpt-5.6-sol")
    assert argv[0] == "codex" and "exec" in argv
    assert "gpt-5.6-sol" in argv and "gpt-5.6-sol[1m]" not in argv
    assert "--output-schema" in argv and argv[argv.index("--output-schema") + 1] == "/tmp/schema.json"
    assert "--skip-git-repo-check" in argv


def test_codex_env_keeps_chatgpt_auth(tmp_path):
    """codex grader must NOT strip ANTHROPIC_*/use claude env — it runs its own ChatGPT auth."""
    import os
    tools = _codex_enabled_cfg(tmp_path)
    env = tools._codex_grader_env()
    # env is the process env (not the claude _GRADER_ENV_STRIP contract): PATH preserved,
    # ANTHROPIC_* NOT stripped by this builder.
    assert env.get("PATH") == os.environ.get("PATH")
    assert "ANTHROPIC_BASE_URL" not in tools._codex_grader_env() or \
        env.get("ANTHROPIC_BASE_URL") == os.environ.get("ANTHROPIC_BASE_URL")


def test_call_grader_codex_end_to_end(tmp_path, monkeypatch):
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    with patch.object(omcp.subprocess, "run",
                      return_value=_codex_proc("codex_grader_verdict.jsonl")) as run:
        out = tools._call_grader("sys", "user")
    assert out == {"grade": "B", "approved": True, "feedback": "looks fine"}
    assert run.call_args.args[0][0] == "codex"


def _codex_error_proc(returncode, stdout, stderr):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_call_grader_codex_nonzero_exit_long_stream_survives_truncation(tmp_path, monkeypatch):
    """Real error text past 300 chars of boilerplate must survive into error_detail.

    Traced against current source: old code's diagnostic = stdout.strip() (unbounded),
    then _grader_failure collapses whitespace and truncates the WHOLE combined message
    to 300 chars from the head -> only early thread.started/reasoning text survives.
    Expected: FAIL against current code (RED); PASS after the fix (GREEN).
    """
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    stdout = (FIXTURES / "codex_grader_error_long_stream.jsonl").read_text()
    with patch.object(omcp.subprocess, "run",
                      return_value=_codex_error_proc(1, stdout, "")):
        out = tools._call_grader("sys", "user")
    assert out["infrastructure_error"] is True
    assert out["grade"] == "F"
    assert "thread.started" not in out["error_detail"]
    assert "schema validation failed: missing required field 'grade'" in out["error_detail"]


def test_call_grader_codex_nonzero_exit_both_streams_present(tmp_path, monkeypatch):
    """Neither stream may be silently dropped when both carry signal.

    Traced against current source: old diagnostic = stderr.strip() or stdout.strip() —
    the `or` short-circuit discards stdout entirely whenever stderr is non-empty.
    Expected: FAIL against current code (RED, stdout marker absent); PASS after fix (GREEN).
    """
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"stdout-signal-marker"}}\n'
    stderr = "stderr-signal-marker"
    with patch.object(omcp.subprocess, "run",
                      return_value=_codex_error_proc(1, stdout, stderr)):
        out = tools._call_grader("sys", "user")
    assert "stdout-signal-marker" in out["error_detail"]
    assert "stderr-signal-marker" in out["error_detail"]


def test_call_grader_codex_nonzero_exit_stderr_only(tmp_path, monkeypatch):
    """stderr-only diagnostics must be legible as coming from stderr in Slack/logs.

    Traced against current source: old diagnostic is the bare stderr text with no
    channel label at all.
    Expected: FAIL against current code (RED, no "stderr: " label); PASS after fix (GREEN).
    """
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    with patch.object(omcp.subprocess, "run",
                      return_value=_codex_error_proc(1, "", "codex: authentication expired")):
        out = tools._call_grader("sys", "user")
    assert "stderr: codex: authentication expired" in out["error_detail"]


def test_call_grader_codex_nonzero_exit_malformed_stdout(tmp_path, monkeypatch):
    """Non-JSON stdout on a nonzero exit must degrade to bounded raw text, never raise.

    Traced against current source: old code's stdout.strip() fallback already carries
    plain text through untouched (whitespace-collapsed, well under the 300-char cap).
    Expected: PASS against current code already (characterization); must keep passing
    after the fix, proving the new tail-scan's malformed-JSON fallback path is safe.
    """
    tools = _codex_enabled_cfg(tmp_path, clients=("codex",))
    _codex_avail(monkeypatch)
    stdout = (
        "panic: runtime error: index out of range [3] with length 3\n"
        "goroutine 1 [running]:\n"
        "main.main()\n"
        "\t/build/src/main.go:42 +0x1a5"
    )
    with patch.object(omcp.subprocess, "run",
                      return_value=_codex_error_proc(1, stdout, "")):
        out = tools._call_grader("sys", "user")
    assert out["infrastructure_error"] is True
    assert "no diagnostic output" not in out["error_detail"]
    assert "main.go:42" in out["error_detail"]


def _codex_cfg_with_effort(tmp_path, effort):
    """Same config as _codex_enabled_cfg, but with a NON-DEFAULT effort level.

    effort_level is an OrchestratorTools constructor kwarg, not a config key.
    Asserting "high" would prove nothing — it is the default, so a hardcoded
    value would pass. Only a non-default value proves _effort_level is read.
    """
    conn = init_db(str(tmp_path / "commander.db"))
    cfg = {
        "grader_model": "opus", "brain_model": "sonnet", "default_opus_model": "opus",
        "advisor": {"advisor_model": "opus", "advisor_models": {}},
        "providers": {
            "clients": {
                "claude": {"enabled": True, "path": "claude",
                           "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "claude-opus-4-8", "fable": "fable"}},
                "codex": {"enabled": True, "path": "codex",
                          "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}},
            },
            "roles": {
                "brain": {"preferred": "claude", "clients": ["claude"]},
                "worker": {"preferred": "claude", "clients": ["claude"]},
                "grader": {"preferred": "codex", "clients": ["codex"]},
                "advisor": {"preferred": "claude", "clients": ["claude"]},
            },
        },
    }
    return omcp.OrchestratorTools(registry=MagicMock(), tmux=MagicMock(), db_conn=conn,
                                  config=cfg, effort_level=effort)


def test_codex_grader_argv_pins_configured_reasoning_effort(tmp_path):
    """IronClaude's effort_level must govern codex, not ~/.codex/config.toml."""
    tools = _codex_cfg_with_effort(tmp_path, "low")
    argv = tools._codex_grader_argv("/tmp/schema.json", "gpt-5.6-sol")
    assert "-c" in argv
    assert 'model_reasoning_effort="low"' in argv


def test_codex_grader_env_adds_nothing(tmp_path):
    """The codex grader env builder must be a pure pass-through.

    Asserting `"CLAUDE_CODE_EFFORT_LEVEL" not in env` would test the ambient shell,
    not the builder — and this shell exports that variable, while conftest.py scrubs
    only IC_*. Equality against os.environ is environment-independent and still fails
    the moment the builder re-adds any key.
    """
    import os
    tools = _codex_cfg_with_effort(tmp_path, "low")
    assert tools._codex_grader_env() == dict(os.environ)
