"""Unit tests for the Haiku-class fast-model helper (subprocess stubbed at _run_child)."""
import json
import os
import subprocess

import pytest

from ironclaude import fast_model

SCHEMA = {"type": "object", "properties": {"kind": {"type": "string"}}, "required": ["kind"]}


def _proc(stdout, rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


def _claude_array(obj):
    return json.dumps([{"type": "system"}, {"type": "result", "structured_output": obj}])


def _codex_jsonl(obj):
    return "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t"}),
        "not json",
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"kind": "early"})}}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(obj)}}),
    ])


def _cfg(preferred="claude", clients=("claude",), codex_enabled=False):
    return {"providers": {
        "clients": {
            "claude": {"enabled": True, "path": "/bin/claude-x", "models": {"haiku": "haiku"}},
            "codex": {"enabled": codex_enabled, "path": "/bin/codex-x", "models": {"haiku": "gpt-5.6-luna"}},
        },
        "roles": {"grader": {"preferred": preferred, "clients": list(clients)}},
    }}


def test_claude_argv_is_lean_and_session_free():
    assert fast_model.claude_fast_argv("claude", "haiku", SCHEMA) == [
        "claude", "-p", "--model", "haiku", "--restricted", "--effort", "low",
        "--tools", "", "--no-session-persistence",
        "--strict-mcp-config", "--settings", '{"disableAllHooks":true}',
        "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
    ]


def test_codex_argv_is_ephemeral_low_effort():
    assert fast_model.codex_fast_argv("codex", "gpt-6-luna", "/tmp/s.json") == [
        "codex", "exec", "-c", 'model_reasoning_effort="low"',
        "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
        "-c", "features.view_image=false", "-c", "features.apps=false",
        "-c", "features.browser_use=false", "-c", "features.computer_use=false",
        "-c", "features.multi_agent=false", "-c", "features.image_generation=false",
        "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false",
        "-c", "features.goals=false", "-c", "features.tool_suggest=false",
        "-c", "agents.enabled=false", "-c", 'web_search="disabled"',
        "--json", "--ephemeral",
        "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "--output-schema", "/tmp/s.json", "-s", "read-only", "-m", "gpt-6-luna", "-",
    ]


def test_codex_argv_disables_every_tool_feature():
    argv = fast_model.codex_fast_argv("codex", "m", "/tmp/s.json")
    for name in ("shell_tool", "unified_exec", "view_image", "apps",
                 "browser_use", "computer_use", "multi_agent", "image_generation",
                 "memories", "plugins", "hooks"):
        flag = f"features.{name}=false"
        assert flag in argv, flag
        assert argv[argv.index(flag) - 1] == "-c", flag


def test_parse_claude_returns_full_object_from_array_or_object():
    full = {"kind": "menu", "question": "A or B?", "worker_id": "w1", "valid": True}
    assert fast_model.parse_claude_output(_claude_array(full)) == full
    assert fast_model.parse_claude_output(json.dumps({"type": "result", "structured_output": full})) == full


@pytest.mark.parametrize("stdout", [
    "not json", json.dumps([{"type": "result"}]), json.dumps([{"type": "result", "structured_output": [1]}]),
    json.dumps({"type": "assistant"}), "",
])
def test_parse_claude_rejects_unusable_output(stdout):
    assert fast_model.parse_claude_output(stdout) is None


def test_parse_codex_takes_last_agent_message_and_skips_noise():
    assert fast_model.parse_codex_output(_codex_jsonl({"kind": "none"})) == {"kind": "none"}
    assert fast_model.parse_codex_output("not json\n") is None
    assert fast_model.parse_codex_output(json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "prose"}})) is None


def test_clients_follow_grader_role_preference():
    assert fast_model.fast_model_clients(None) == [("claude", "claude", "haiku")]
    assert fast_model.fast_model_clients({"providers": "bad"}) == [("claude", "claude", "haiku")]
    assert fast_model.fast_model_clients(_cfg()) == [("claude", "/bin/claude-x", "haiku")]
    assert fast_model.fast_model_clients(
        _cfg(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    ) == [("codex", "/bin/codex-x", "gpt-5.6-luna"), ("claude", "/bin/claude-x", "haiku")]


@pytest.mark.parametrize("cfg,expected", [
    ({}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
    ({"fast_model_timeout_seconds": 7}, 7),
    ({"fast_model_timeout_seconds": "x"}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
    ({"fast_model_timeout_seconds": True}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
    ({"fast_model_timeout_seconds": 0}, fast_model.FAST_MODEL_TIMEOUT_DEFAULT),
])
def test_fast_model_timeout(cfg, expected):
    assert fast_model.fast_model_timeout(cfg) == expected


def test_call_returns_full_object_with_lean_child(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-remove")
    seen = {}

    def fake(argv, *, input_text, timeout, env):
        seen.update(argv=argv, input=input_text, timeout=timeout, env=env)
        return _proc(_claude_array({"kind": "menu", "question": "Q?"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model("SYS", "USER", SCHEMA, timeout=11, commander_config=None)
    assert out == {"kind": "menu", "question": "Q?"}
    assert seen["argv"][:4] == ["claude", "-p", "--model", "haiku"]
    assert seen["input"] == "SYS\n\nUSER"
    assert seen["timeout"] == 11
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_timeout_moves_to_next_client_then_codex_answers(monkeypatch):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv[0])
        if argv[0] == "/bin/claude-x":
            raise subprocess.TimeoutExpired(argv, timeout)
        schema_file = argv[argv.index("--output-schema") + 1]
        with open(schema_file) as f:
            assert json.load(f) == {**SCHEMA, "additionalProperties": False}
        return _proc(_codex_jsonl({"kind": "none"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model(
        "S", "U", SCHEMA, timeout=5,
        commander_config=_cfg(preferred="claude", clients=("claude", "codex"), codex_enabled=True))
    assert out == {"kind": "none"}
    assert calls == ["/bin/claude-x", "/bin/codex-x"]


def test_codex_schema_file_is_removed(monkeypatch):
    seen = {}

    def fake(argv, *, input_text, timeout, env):
        seen["file"] = argv[argv.index("--output-schema") + 1]
        return _proc(_codex_jsonl({"kind": "none"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    fast_model.call_fast_model("S", "U", SCHEMA, timeout=5,
                               commander_config=_cfg(preferred="codex", clients=("codex",), codex_enabled=True))
    assert not os.path.exists(seen["file"])


def test_all_clients_failing_is_infrastructure_error(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc("", rc=1))
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None)
    assert out["infrastructure_error"] is True
    assert out["error_detail"].startswith("fast model unavailable:")
    assert "haiku exited 1" in out["error_detail"]


def test_missing_required_field_is_infrastructure_error(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_claude_array({"other": 1})))
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None)
    assert out["infrastructure_error"] is True
    assert "kind" in out["error_detail"]


# ── Model class (tier) per call ──

def _cfg_classes(preferred="claude", clients=("claude",), codex_enabled=False):
    return {"providers": {
        "clients": {
            "claude": {"enabled": True, "path": "/bin/claude-x",
                       "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}},
            "codex": {"enabled": codex_enabled, "path": "/bin/codex-x",
                      "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol"}},
        },
        "roles": {"grader": {"preferred": preferred, "clients": list(clients)}},
    }}


def test_clients_map_the_requested_class():
    assert fast_model.fast_model_clients(None, "sonnet") == [("claude", "claude", "sonnet")]
    assert fast_model.fast_model_clients({"providers": "bad"}, "sonnet") == [("claude", "claude", "sonnet")]
    assert fast_model.fast_model_clients(_cfg_classes(), "sonnet") == [("claude", "/bin/claude-x", "sonnet")]
    assert fast_model.fast_model_clients(
        _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True), "sonnet"
    ) == [("codex", "/bin/codex-x", "gpt-5.6-terra"), ("claude", "/bin/claude-x", "sonnet")]
    # Positive control: the default class is still haiku.
    assert fast_model.fast_model_clients(_cfg_classes()) == [("claude", "/bin/claude-x", "haiku")]


@pytest.mark.parametrize("cfg,expected", [
    ({}, None),
    ({"sonnet_model_timeout_seconds": 7}, 7),
    ({"sonnet_model_timeout_seconds": "x"}, None),
    ({"sonnet_model_timeout_seconds": True}, None),
    ({"fast_model_timeout_seconds": 7}, None),
])
def test_sonnet_model_timeout(cfg, expected):
    expected = fast_model.SONNET_MODEL_TIMEOUT_DEFAULT if expected is None else expected
    assert fast_model.sonnet_model_timeout(cfg) == expected


def test_sonnet_cap_default_is_the_measured_gate_value():
    assert fast_model.SONNET_MODEL_TIMEOUT_DEFAULT == 30


def test_call_at_sonnet_class_uses_the_sonnet_argv(monkeypatch):
    seen = []

    def fake(argv, *, input_text, timeout, env):
        seen.append({"argv": argv, "timeout": timeout})
        return _proc(_claude_array({"kind": "x"}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=9, commander_config=None, tier="sonnet")
    assert out == {"kind": "x"}
    assert seen == [{"argv": fast_model.claude_fast_argv("claude", "sonnet", SCHEMA), "timeout": 9}]


def test_sonnet_failure_names_the_class(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc("", rc=1))
    out = fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None, tier="sonnet")
    assert out["infrastructure_error"] is True
    assert "sonnet exited 1" in out["error_detail"]


def test_codex_sonnet_class_is_terra(monkeypatch):
    calls = []

    def fake(argv, *, input_text, timeout, env):
        calls.append(argv)
        return _proc("", rc=1)

    monkeypatch.setattr(fast_model, "_run_child", fake)
    out = fast_model.call_fast_model(
        "S", "U", SCHEMA, timeout=5, tier="sonnet",
        commander_config=_cfg_classes(preferred="codex", clients=("codex",), codex_enabled=True))
    assert calls[0][calls[0].index("-m") + 1] == "gpt-5.6-terra"
    assert "terra:gpt-5.6-terra exited 1" in out["error_detail"]


def test_default_class_is_haiku_positive_control(monkeypatch):
    seen = []
    monkeypatch.setattr(fast_model, "_run_child",
                        lambda argv, **kw: seen.append(argv) or _proc(_claude_array({"kind": "x"})))
    assert fast_model.call_fast_model("S", "U", SCHEMA, timeout=5, commander_config=None) == {"kind": "x"}
    assert seen[0][:4] == ["claude", "-p", "--model", "haiku"]


# ── Codex strict structured output (design 2026-10-17 R1-R4) ──

STRICT_IN = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 5},
        "who": {"type": ["string", "null"]},
        "mode": {"enum": ["a", "b"]},
        "pick": {"type": "string", "enum": ["x", "y"]},
        "opts": {"type": "array", "items": {
            "type": "object", "properties": {"v": {"type": "string"}, "note": {"type": "string"}},
            "required": ["v"]}},
    },
    "required": ["valid", "opts"],
}

STRICT_OUT = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "reason": {"type": ["string", "null"], "maxLength": 5},
        "who": {"type": ["string", "null"]},
        "mode": {"enum": ["a", "b", None]},
        "pick": {"type": ["string", "null"], "enum": ["x", "y", None]},
        "opts": {"type": "array", "items": {
            "type": "object",
            "properties": {"v": {"type": "string"}, "note": {"type": ["string", "null"]}},
            "required": ["v", "note"], "additionalProperties": False}},
    },
    "required": ["valid", "reason", "who", "mode", "pick", "opts"],
    "additionalProperties": False,
}


def test_strict_codex_schema_closes_every_object_and_nullables_optional():
    import copy
    original = copy.deepcopy(STRICT_IN)
    assert fast_model.strict_codex_schema(STRICT_IN) == STRICT_OUT
    assert STRICT_IN == original


def test_strict_codex_schema_keeps_a_propertyless_object_closed():
    assert fast_model.strict_codex_schema({"type": "object"}) == {"type": "object", "additionalProperties": False}


def test_strict_codex_schema_covers_the_commander_schemas():
    from ironclaude import main
    for schema in (main._BRAIN_MSG_SCHEMA, main._AWAITING_OP_SCHEMA, main._PROMPT_WAITING_SCHEMA):
        strict = fast_model.strict_codex_schema(schema)
        assert strict["required"] == list(schema["properties"])
        assert strict["additionalProperties"] is False
    items = fast_model.strict_codex_schema(main._PROMPT_WAITING_SCHEMA)["properties"]["options"]["items"]
    assert items["additionalProperties"] is False


def test_codex_child_gets_strict_schema_and_claude_child_the_original(monkeypatch):
    seen = {}

    def fake(argv, *, input_text, timeout, env):
        if argv[0] == "/bin/claude-x":
            seen["claude"] = json.loads(argv[argv.index("--json-schema") + 1])
            raise subprocess.TimeoutExpired(argv, timeout)
        with open(argv[argv.index("--output-schema") + 1]) as f:
            seen["codex"] = json.load(f)
        return _proc(_codex_jsonl({"valid": True, "reason": "ok", "who": None, "mode": None,
                                   "pick": None, "opts": []}))

    monkeypatch.setattr(fast_model, "_run_child", fake)
    fast_model.call_fast_model("S", "U", STRICT_IN, timeout=5,
                               commander_config=_cfg(preferred="claude", clients=("claude", "codex"),
                                                     codex_enabled=True))
    assert seen["claude"] == STRICT_IN
    assert seen["codex"] == STRICT_OUT


def test_codex_answer_drops_null_optional_keys_only(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_codex_jsonl(
        {"valid": False, "reason": None, "who": "w", "mode": None, "pick": None, "opts": []})))
    out = fast_model.call_fast_model("S", "U", STRICT_IN, timeout=5,
                                     commander_config=_cfg(preferred="codex", clients=("codex",),
                                                           codex_enabled=True))
    assert out == {"valid": False, "who": "w", "opts": []}


def test_codex_answer_keeps_null_for_a_required_key(monkeypatch):
    schema = {"type": "object", "properties": {"q": {"type": ["string", "null"]}}, "required": ["q"]}
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_codex_jsonl({"q": None})))
    out = fast_model.call_fast_model("S", "U", schema, timeout=5,
                                     commander_config=_cfg(preferred="codex", clients=("codex",),
                                                           codex_enabled=True))
    assert out == {"q": None}


def test_claude_answer_is_returned_unchanged_positive_control(monkeypatch):
    monkeypatch.setattr(fast_model, "_run_child", lambda argv, **kw: _proc(_claude_array(
        {"valid": False, "reason": None, "opts": []})))
    out = fast_model.call_fast_model("S", "U", STRICT_IN, timeout=5, commander_config=None)
    assert out == {"valid": False, "reason": None, "opts": []}


_NO_TOOLS = [
    "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
    "-c", "features.view_image=false", "-c", "features.apps=false",
    "-c", "features.browser_use=false", "-c", "features.computer_use=false",
    "-c", "features.multi_agent=false", "-c", "features.image_generation=false",
    "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false",
    "-c", "features.goals=false", "-c", "features.tool_suggest=false",
    "-c", "agents.enabled=false", "-c", 'web_search="disabled"',
]


def _hook_text():
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[2] / "worker" / "hooks" / "plan-validator.sh").read_text()


def test_codex_no_tools_args_are_the_shared_set():
    """Design 2026-10-17 tool-audit-fixes R1: the 11 feature-offs, goals, tool_suggest, agents, web search."""
    assert list(fast_model.CODEX_NO_TOOLS_ARGS) == _NO_TOOLS


def test_codex_no_tools_args_match_hook():
    """One IronClaude: the hook's Codex child passes the Commander's no-tools pairs, in order."""
    import re
    import shlex
    body = re.search(r"^IC_CODEX_NO_TOOLS_ARGS=\((.*?)^\)", _hook_text(), re.S | re.M)
    assert body is not None, "plan-validator.sh defines no IC_CODEX_NO_TOOLS_ARGS array"
    assert shlex.split(body.group(1)) == list(fast_model.CODEX_NO_TOOLS_ARGS)
    assert '"${IC_CODEX_NO_TOOLS_ARGS[@]}"' in _hook_text()


def test_codex_no_tools_args_match_broker():
    """One IronClaude: the Codex advisor broker passes the same no-tools pairs, in order."""
    import pathlib
    import re
    ts = (pathlib.Path(__file__).resolve().parents[2] / "worker" / "mcp-servers" / "state-manager"
          / "src" / "tools" / "advisor-review.ts").read_text()
    body = re.search(r"export const CODEX_NO_TOOLS_ARGS = \[(.*?)\] as const;", ts, re.S)
    assert body is not None, "advisor-review.ts defines no CODEX_NO_TOOLS_ARGS"
    assert re.findall(r"'([^']*)'", body.group(1)) == list(fast_model.CODEX_NO_TOOLS_ARGS)
    assert "...CODEX_NO_TOOLS_ARGS," in ts


def test_hook_claude_child_unsets_the_commander_strip_list():
    """One IronClaude: the hook's Claude child runs without the routing variables _claude_env strips."""
    import re
    assert re.findall(r"-u ([A-Z_]+)", _hook_text()) == list(fast_model._ENV_STRIP)


def test_claude_env_drops_inherited_effort(monkeypatch):
    """Design R5: an inherited CLAUDE_CODE_EFFORT_LEVEL beats --effort low (measured), so the fast child drops it."""
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "high")
    monkeypatch.setenv("IC_KEEP_PROBE", "kept")
    env = fast_model._claude_env()
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in env
    assert env["IC_KEEP_PROBE"] == "kept"  # positive control: other variables still pass through


def test_codex_fast_clients_resolve_family_words_and_skip_an_unavailable_family(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps(
        {"models": [{"slug": "gpt-6-luna"}, {"slug": "gpt-5.10-terra"}, {"slug": "gpt-5.9-terra"}]}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    cfg = _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    cfg["providers"]["clients"]["codex"]["models"] = {"haiku": "luna", "sonnet": "terra", "opus": "sol"}
    assert fast_model.fast_model_clients(cfg) == [
        ("codex", "/bin/codex-x", "gpt-6-luna"), ("claude", "/bin/claude-x", "haiku")]
    assert fast_model.fast_model_clients(cfg, "sonnet") == [
        ("codex", "/bin/codex-x", "gpt-5.10-terra"), ("claude", "/bin/claude-x", "sonnet")]
    # No Sol installed: Codex is unavailable for the call and Claude answers.
    assert fast_model.fast_model_clients(cfg, "opus") == [("claude", "/bin/claude-x", "opus")]
    # An explicit slug wins over a newer installed model.
    slugs = _cfg_classes(preferred="codex", clients=("codex", "claude"), codex_enabled=True)
    assert fast_model.fast_model_clients(slugs)[0] == ("codex", "/bin/codex-x", "gpt-5.6-luna")


def test_hook_strict_schema_matches_python():
    """One IronClaude: the hook's Codex schema transform equals fast_model.strict_codex_schema."""
    import pathlib
    hook = pathlib.Path(__file__).resolve().parents[2] / "worker" / "hooks" / "plan-validator.sh"
    proc = subprocess.run(["bash", "-c", 'source "$0" && _strict_codex_schema', str(hook)],
                          input=json.dumps(STRICT_IN), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == fast_model.strict_codex_schema(STRICT_IN) == STRICT_OUT


def _bundle_no_tools(bundle_text):
    """The CODEX_NO_TOOLS_ARGS list esbuild wrote into the state-manager runtime bundle, or None."""
    import ast
    import re
    body = re.search(r"^var CODEX_NO_TOOLS_ARGS = \[(.*?)^\];", bundle_text, re.S | re.M)
    return None if body is None else list(ast.literal_eval("[" + body.group(1) + "]"))


def test_codex_no_tools_args_match_bundle():
    """Design 2026-10-17 v120-completeness R2: the bundle the broker actually runs
    (state-manager dist/index.js) carries the same no-tools pairs, in order, and spreads them."""
    import pathlib
    bundle = (pathlib.Path(__file__).resolve().parents[2] / "worker" / "mcp-servers" / "state-manager"
              / "dist" / "index.js").read_text()
    assert _bundle_no_tools(bundle) == list(fast_model.CODEX_NO_TOOLS_ARGS)
    assert "...CODEX_NO_TOOLS_ARGS," in bundle
