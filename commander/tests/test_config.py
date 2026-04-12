# tests/test_config.py
import json
import os
import pytest
from ironclaude.config import load_config


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

    def test_operator_name_default(self, tmp_path):
        config_file = tmp_path / "ironclaude.json"
        config_file.write_text("{}")
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
        monkeypatch.setenv("SLACK_ROBERT_USER_ID", "U456")
        cfg = load_config(str(config_file))
        assert cfg["slack_operator_user_id"] == "U789"

    def test_advisor_default(self):
        cfg = load_config("/nonexistent/path.json")
        assert cfg["advisor"]["enabled"] is True
        assert cfg["advisor"]["executor_model"] == "sonnet"
        assert cfg["advisor"]["advisor_model"] == "opus"
