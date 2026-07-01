# tests/test_config.py
"""Tests for config loading — ironclaude.json and machines.yaml."""
import json
import os
import pytest
from ironclaude.config import load_config, load_machines_config


class TestLoadConfig:
    def test_loads_json_config(self, tmp_path):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text(json.dumps({
            "poll_interval_seconds": 10,
            "machines": [{"name": "test", "url": "http://localhost:11434"}],
        }))
        cfg = load_config(str(config_file))
        assert cfg["poll_interval_seconds"] == 10
        assert cfg["machines"][0]["name"] == "test"

    def test_env_overrides_json(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text(json.dumps({"poll_interval_seconds": 10}))
        monkeypatch.setenv("POLL_INTERVAL_SECONDS", "5")
        cfg = load_config(str(config_file))
        assert cfg["poll_interval_seconds"] == 5

    def test_missing_config_uses_defaults(self):
        cfg = load_config("/nonexistent/path.json")
        assert "poll_interval_seconds" in cfg
        assert "tmp_dir" in cfg

    def test_db_path_default(self):
        cfg = load_config("/nonexistent/path.json")
        assert cfg["db_path"] == "data/db/ironclaude.db"

    def test_required_env_vars(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
        monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")
        monkeypatch.setenv("SLACK_OPERATOR_USER_ID", "U456")
        cfg = load_config(str(config_file))
        assert cfg["slack_bot_token"] == "xoxb-test"
        assert cfg["slack_app_token"] == "xapp-test"
        assert cfg["slack_channel_id"] == "C123"
        assert cfg["slack_user_token"] == "xoxp-test"
        assert cfg["slack_operator_user_id"] == "U456"

    def test_operator_name_from_env(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("OPERATOR_NAME", "Alice")
        cfg = load_config(str(config_file))
        assert cfg["operator_name"] == "Alice"

    def test_operator_name_default(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.delenv("OPERATOR_NAME", raising=False)
        cfg = load_config(str(config_file))
        assert cfg["operator_name"] == "Operator"

    def test_autonomy_level_from_env(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("AUTONOMY_LEVEL", "5")
        cfg = load_config(str(config_file))
        assert cfg["autonomy_level"] == "5"

    def test_autonomy_level_default(self, tmp_path):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        cfg = load_config(str(config_file))
        assert cfg["autonomy_level"] == "3"

    def test_slack_operator_user_id_from_env(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("SLACK_OPERATOR_USER_ID", "U789")
        cfg = load_config(str(config_file))
        assert cfg["slack_operator_user_id"] == "U789"

    def test_slack_operator_user_id_new_name_takes_precedence(self, tmp_path, monkeypatch):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("SLACK_OPERATOR_USER_ID", "U789")
        cfg = load_config(str(config_file))
        assert cfg["slack_operator_user_id"] == "U789"

    def test_advisor_default(self):
        cfg = load_config("/nonexistent/path.json")
        assert cfg["advisor"]["enabled"] is True
        assert cfg["advisor"]["executor_model"] == "sonnet"
        assert cfg["advisor"]["advisor_model"] == "opus"

    def test_defaults_include_brain_model(self):
        """brain_model defaults to fable."""
        from ironclaude.config import DEFAULTS
        assert DEFAULTS["brain_model"] == "fable"

    def test_no_env_brain_model_is_fable(self, monkeypatch):
        """With no relevant env vars, brain_model resolves to fable."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        monkeypatch.delenv("BRAIN_MODEL", raising=False)
        monkeypatch.delenv("GRADER_MODEL", raising=False)
        cfg = load_config("/nonexistent/path.json")
        assert cfg["brain_model"] == "fable"

    def test_defaults_include_grader_model(self):
        """grader_model defaults to opus."""
        from ironclaude.config import DEFAULTS
        assert DEFAULTS["grader_model"] == "opus"

    def test_env_override_brain_model(self, tmp_path, monkeypatch):
        """BRAIN_MODEL env var overrides config."""
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("BRAIN_MODEL", "claude-sonnet-4-5-20241022")
        cfg = load_config(str(config_file))
        assert cfg["brain_model"] == "claude-sonnet-4-5-20241022"

    def test_env_override_grader_model(self, tmp_path, monkeypatch):
        """GRADER_MODEL env var overrides config."""
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("GRADER_MODEL", "claude-sonnet-4-5-20241022")
        cfg = load_config(str(config_file))
        assert cfg["grader_model"] == "claude-sonnet-4-5-20241022"

    def test_default_opus_model_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-7")
        cfg = load_config()
        assert cfg["default_opus_model"] == "claude-opus-4-7"

    def test_default_opus_model_overrides_brain_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-7")
        cfg = load_config()
        assert cfg["brain_model"] == "claude-opus-4-7"

    def test_default_opus_model_overrides_grader_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-7")
        cfg = load_config()
        assert cfg["grader_model"] == "claude-opus-4-7"

    def test_brain_model_env_takes_precedence_over_default_opus(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("BRAIN_MODEL", "claude-sonnet-4-5")
        cfg = load_config()
        assert cfg["brain_model"] == "claude-sonnet-4-5"
        assert cfg["default_opus_model"] == "claude-opus-4-7"

    def test_default_opus_model_decoupled_from_brain_model(self, monkeypatch):
        """With no relevant env vars, default_opus_model is 'opus' and is
        decoupled from brain_model (must NOT inherit brain_model's value)."""
        monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
        monkeypatch.delenv("BRAIN_MODEL", raising=False)
        monkeypatch.delenv("GRADER_MODEL", raising=False)
        cfg = load_config("/nonexistent/path.json")
        assert cfg["default_opus_model"] == "opus"
        assert cfg["default_opus_model"] != cfg["brain_model"]

    def test_defaults_include_default_opus_model(self):
        """default_opus_model defaults to opus."""
        from ironclaude.config import DEFAULTS
        assert DEFAULTS["default_opus_model"] == "opus"

    def test_defaults_include_effort_level(self):
        """effort_level defaults to 'high'."""
        from ironclaude.config import DEFAULTS
        assert DEFAULTS["effort_level"] == "high"

    def test_env_override_effort_level(self, tmp_path, monkeypatch):
        """EFFORT_LEVEL env var overrides config."""
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
        monkeypatch.setenv("EFFORT_LEVEL", "medium")
        cfg = load_config(str(config_file))
        assert cfg["effort_level"] == "medium"

    def test_make_opus_command_uses_effort_param(self):
        """make_opus_command uses provided effort level."""
        from ironclaude.config import make_opus_command
        cmd = make_opus_command("claude-opus-4-5", "medium")
        assert "CLAUDE_CODE_EFFORT_LEVEL=medium" in cmd
        assert "claude-opus-4-5" in cmd
        assert "[1m]" not in cmd

    def test_make_opus_command_high_effort(self):
        """make_opus_command with high effort."""
        from ironclaude.config import make_opus_command
        cmd = make_opus_command("claude-opus-4-5", "high")
        assert "CLAUDE_CODE_EFFORT_LEVEL=high" in cmd
        assert "[1m]" not in cmd

    def test_partial_nested_override_retains_sibling_defaults(self, tmp_path):
        """A partial nested override must deep-merge, not replace the whole dict.

        Overriding only advisor.enabled must keep executor_model and
        advisor_model from DEFAULTS instead of dropping them.
        """
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text(json.dumps({"advisor": {"enabled": False}}))
        cfg = load_config(str(config_file))
        assert cfg["advisor"]["enabled"] is False
        assert cfg["advisor"]["executor_model"] == "sonnet"
        assert cfg["advisor"]["advisor_model"] == "opus"

    def test_effort_level_out_of_allowlist_normalizes_to_high(self, tmp_path, monkeypatch):
        """An out-of-allowlist effort_level falls back to 'high'."""
        monkeypatch.delenv("EFFORT_LEVEL", raising=False)
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text(json.dumps({"effort_level": "ludicrous; rm -rf /"}))
        cfg = load_config(str(config_file))
        assert cfg["effort_level"] == "high"

    def test_effort_level_valid_values_preserved(self, tmp_path, monkeypatch):
        """In-allowlist effort_level values are preserved."""
        monkeypatch.delenv("EFFORT_LEVEL", raising=False)
        for value in ("low", "medium", "high"):
            config_file = tmp_path / "ironclaude.json"
            config_file.write_text(json.dumps({"effort_level": value}))
            cfg = load_config(str(config_file))
            assert cfg["effort_level"] == value


# ── machines.yaml tests ──────────────────────────────────────────────


@pytest.fixture
def machines_yaml(tmp_path):
    path = tmp_path / "machines.yaml"
    path.write_text("""
machines:
  - name: server1
    host: server1
    purpose: "Test server"
    claude_path: /usr/bin/claude
    repos:
      - /home/user/repo1
      - /home/user/repo2
    max_workers: 2
    env:
      API_KEY: "test-key"
  - name: server2
    host: user@server2.example.com
    claude_path: ~/.claude/local/claude
    repos:
      - /opt/project
""")
    return str(path)


class TestLoadMachinesConfig:
    def test_load_valid_config(self, machines_yaml):
        machines = load_machines_config(machines_yaml)
        assert len(machines) == 2
        assert machines[0]["name"] == "server1"
        assert machines[0]["repos"] == ["/home/user/repo1", "/home/user/repo2"]
        assert machines[0]["max_workers"] == 2

    def test_defaults_for_optional_fields(self, machines_yaml):
        machines = load_machines_config(machines_yaml)
        s2 = machines[1]
        assert s2.get("purpose", "") == ""
        assert s2.get("log_dir", "/tmp/ic-logs") == "/tmp/ic-logs"

    def test_missing_file_returns_empty(self, tmp_path):
        machines = load_machines_config(str(tmp_path / "nonexistent.yaml"))
        assert machines == []

    def test_env_var_interpolation(self, machines_yaml, monkeypatch):
        path = os.path.dirname(machines_yaml)
        p = os.path.join(path, "interp.yaml")
        with open(p, "w") as f:
            f.write("""
machines:
  - name: s
    host: s
    claude_path: /c
    repos: [/r]
    env:
      KEY: "${MY_TEST_VAR}"
""")
        monkeypatch.setenv("MY_TEST_VAR", "secret123")
        machines = load_machines_config(p)
        assert machines[0]["env"]["KEY"] == "secret123"

    def test_duplicate_names_raises(self, tmp_path):
        p = tmp_path / "dup.yaml"
        p.write_text("""
machines:
  - name: same
    host: a
    claude_path: /c
    repos: [/r]
  - name: same
    host: b
    claude_path: /c
    repos: [/r]
""")
        with pytest.raises(ValueError, match="Duplicate"):
            load_machines_config(str(p))

    def test_missing_required_field_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("""
machines:
  - name: nohost
    claude_path: /c
    repos: [/r]
""")
        with pytest.raises(ValueError, match="host"):
            load_machines_config(str(p))
