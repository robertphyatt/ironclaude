# tests/test_plugins.py
"""Tests for plugin registry and discovery."""

import os
import pytest
from ironclaude.plugins import PluginRegistry, discover_plugins


class TestPluginRegistry:
    def test_register_command(self):
        reg = PluginRegistry()
        reg.register_command("foo", "Do foo", lambda text: None, lambda daemon, parsed: None)
        assert "foo" in reg.get_slash_commands()
        assert reg.get_slash_commands()["foo"] == "Do foo"

    def test_parse_command_returns_first_match(self):
        reg = PluginRegistry()
        reg.register_command("greet", "Say hi", lambda text: {"type": "greet"} if text.upper() == "GREET" else None, lambda d, p: None)
        result = reg.parse_command("greet")
        assert result == {"type": "greet"}

    def test_parse_command_returns_none_when_no_match(self):
        reg = PluginRegistry()
        reg.register_command("greet", "Say hi", lambda text: None, lambda d, p: None)
        assert reg.parse_command("unknown stuff") is None

    def test_handle_command_dispatches(self):
        reg = PluginRegistry()
        calls = []
        reg.register_command("foo", "Do foo", lambda text: None, lambda daemon, parsed: calls.append(("foo", parsed)))
        handled = reg.handle_command(None, "foo", {"type": "foo"})
        assert handled is True
        assert calls == [("foo", {"type": "foo"})]

    def test_handle_command_returns_false_for_unknown(self):
        reg = PluginRegistry()
        assert reg.handle_command(None, "unknown", {}) is False

    def test_register_event_type(self):
        reg = PluginRegistry()
        calls = []
        reg.register_event_type("my_event", lambda daemon, item: calls.append(item))
        handled = reg.handle_event(None, {"type": "my_event", "data": 1})
        assert handled is True
        assert calls == [{"type": "my_event", "data": 1}]

    def test_handle_event_returns_false_for_unknown(self):
        reg = PluginRegistry()
        assert reg.handle_event(None, {"type": "no_handler"}) is False

    def test_register_preprocessor(self):
        reg = PluginRegistry()
        reg.register_preprocessor(lambda event, say, daemon: {"type": "intercepted"} if event.get("special") else None)
        result = reg.preprocess_event({"special": True}, None, None)
        assert result == {"type": "intercepted"}

    def test_preprocess_event_returns_none_when_no_match(self):
        reg = PluginRegistry()
        reg.register_preprocessor(lambda event, say, daemon: None)
        assert reg.preprocess_event({"text": "hello"}, None, None) is None

    def test_preprocess_event_first_match_wins(self):
        reg = PluginRegistry()
        reg.register_preprocessor(lambda event, say, daemon: {"type": "first"})
        reg.register_preprocessor(lambda event, say, daemon: {"type": "second"})
        result = reg.preprocess_event({}, None, None)
        assert result == {"type": "first"}

    def test_lifecycle_hooks_called(self):
        reg = PluginRegistry()
        calls = []
        reg.register_lifecycle_hook("init", lambda daemon: calls.append("init"))
        reg.register_lifecycle_hook("shutdown", lambda daemon: calls.append("shutdown"))
        reg.run_lifecycle("init", None)
        reg.run_lifecycle("shutdown", None)
        assert calls == ["init", "shutdown"]

    def test_multiple_lifecycle_hooks_same_phase(self):
        reg = PluginRegistry()
        calls = []
        reg.register_lifecycle_hook("init", lambda daemon: calls.append("a"))
        reg.register_lifecycle_hook("init", lambda daemon: calls.append("b"))
        reg.run_lifecycle("init", None)
        assert calls == ["a", "b"]

    def test_get_slash_commands_empty(self):
        reg = PluginRegistry()
        assert reg.get_slash_commands() == {}

    def test_multiple_commands(self):
        reg = PluginRegistry()
        reg.register_command("a", "Help A", lambda t: None, lambda d, p: None)
        reg.register_command("b", "Help B", lambda t: None, lambda d, p: None)
        cmds = reg.get_slash_commands()
        assert len(cmds) == 2
        assert cmds["a"] == "Help A"
        assert cmds["b"] == "Help B"


class TestDiscoverPlugins:
    def test_discovers_plugin_in_directory(self, tmp_path):
        plugin_dir = tmp_path / "myplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text(
            "def register(registry):\n"
            "    registry.register_command('test-cmd', 'A test', lambda t: None, lambda d, p: None)\n"
        )
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=[str(plugin_dir)])
        assert "myplugin" in loaded
        assert "test-cmd" in reg.get_slash_commands()

    def test_skips_dir_without_plugin_py(self, tmp_path):
        plugin_dir = tmp_path / "empty"
        plugin_dir.mkdir()
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=[str(plugin_dir)])
        assert loaded == []

    def test_skips_dir_missing_register_function(self, tmp_path):
        plugin_dir = tmp_path / "bad"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("x = 1\n")
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=[str(plugin_dir)])
        assert loaded == []

    def test_failed_plugin_load_does_not_crash(self, tmp_path):
        plugin_dir = tmp_path / "broken"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("raise RuntimeError('boom')\n")
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=[str(plugin_dir)])
        assert loaded == []

    def test_syntax_error_does_not_crash(self, tmp_path):
        plugin_dir = tmp_path / "syntax"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("def register(:\n")
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=[str(plugin_dir)])
        assert loaded == []

    def test_nonexistent_dir_returns_empty(self):
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=["/tmp/no-such-dir-ironclaude-test"])
        assert loaded == []

    def test_multiple_plugins(self, tmp_path):
        for name in ["alpha", "beta"]:
            d = tmp_path / name
            d.mkdir()
            (d / "plugin.py").write_text(
                f"def register(registry):\n"
                f"    registry.register_command('{name}', '{name} help', lambda t: None, lambda d, p: None)\n"
            )
        reg = PluginRegistry()
        loaded = discover_plugins(reg, plugin_dirs=[str(tmp_path / "alpha"), str(tmp_path / "beta")])
        assert len(loaded) == 2
        assert "alpha" in reg.get_slash_commands()
        assert "beta" in reg.get_slash_commands()

    def test_parent_dir_remains_on_syspath_for_deferred_handler_imports(self, tmp_path):
        """Handler closures must be able to import sibling modules at call time.

        Regression test for the bug where discover_plugins() stripped parent_dir
        from sys.path in a finally block, breaking runtime imports in handlers.
        """
        import sys

        plugin_dir = tmp_path / "myplugin"
        plugin_dir.mkdir()
        # Sibling module the handler will import at call time (not load time)
        (tmp_path / "helpers.py").write_text("VALUE = 42\n")
        (plugin_dir / "plugin.py").write_text(
            "def register(registry):\n"
            "    def handler(daemon, parsed):\n"
            "        import helpers  # deferred import — needs parent_dir on sys.path\n"
            "        return helpers.VALUE\n"
            "    registry.register_command(\n"
            "        'test', 'Test',\n"
            "        lambda t: {'type': 'test'} if t == 'test' else None,\n"
            "        handler\n"
            "    )\n"
        )
        reg = PluginRegistry()
        discover_plugins(reg, plugin_dirs=[str(plugin_dir)])
        # Parent dir must still be on sys.path so the handler can import helpers
        assert str(tmp_path) in sys.path
        # Calling the handler must not raise ImportError
        reg.handle_command(None, "test", {"type": "test"})


class TestParseInboundCommandRegistryFallthrough:
    def test_unknown_command_tries_registry(self):
        from ironclaude.slack_interface import parse_inbound_command
        reg = PluginRegistry()
        reg.register_command("custom", "Custom cmd",
            lambda text: {"type": "custom", "val": text} if text.upper().startswith("CUSTOM") else None,
            lambda d, p: None)
        result = parse_inbound_command("custom hello", registry=reg)
        assert result["type"] == "custom"

    def test_builtin_commands_still_work_with_registry(self):
        from ironclaude.slack_interface import parse_inbound_command
        reg = PluginRegistry()
        assert parse_inbound_command("STATUS", registry=reg)["type"] == "status"
        assert parse_inbound_command("HELP", registry=reg)["type"] == "help"

    def test_no_registry_falls_through_to_message(self):
        from ironclaude.slack_interface import parse_inbound_command
        result = parse_inbound_command("totally unknown text")
        assert result["type"] == "message"

    def test_registry_no_match_falls_through_to_message(self):
        from ironclaude.slack_interface import parse_inbound_command
        reg = PluginRegistry()
        reg.register_command("greet", "Greet", lambda t: None, lambda d, p: None)
        result = parse_inbound_command("something else", registry=reg)
        assert result["type"] == "message"


class TestSlackSocketHandlerPreprocessor:
    def test_preprocessor_intercepts_event(self):
        from ironclaude.slack_commands import SlackSocketHandler
        reg = PluginRegistry()
        reg.register_preprocessor(lambda event, say, daemon: {"type": "intercepted"} if event.get("special") else None)
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", registry=reg)
        handler._handle_message_event({"special": True, "user": "U1", "ts": "1.0"}, say=None)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["type"] == "intercepted"

    def test_preprocessor_none_falls_through_to_text_parse(self):
        from ironclaude.slack_commands import SlackSocketHandler
        reg = PluginRegistry()
        reg.register_preprocessor(lambda event, say, daemon: None)
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", registry=reg)
        handler._handle_message_event({"text": "STATUS", "user": "U1", "ts": "1.0"}, say=None)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["parsed"]["type"] == "status"

    def test_no_registry_still_works(self):
        from ironclaude.slack_commands import SlackSocketHandler
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        handler._handle_message_event({"text": "HELP", "user": "U1", "ts": "1.0"}, say=None)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["parsed"]["type"] == "help"

    def test_preprocessor_error_falls_through(self):
        from ironclaude.slack_commands import SlackSocketHandler
        def bad_preprocessor(event, say, daemon):
            raise RuntimeError("boom")
        reg = PluginRegistry()
        reg.register_preprocessor(bad_preprocessor)
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", registry=reg)
        handler._handle_message_event({"text": "HELP", "user": "U1", "ts": "1.0"}, say=None)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["parsed"]["type"] == "help"


class TestDaemonPluginDispatch:
    @pytest.fixture
    def daemon_with_plugin(self, tmp_path):
        from ironclaude.main import IroncladeDaemon
        from unittest.mock import MagicMock
        reg = PluginRegistry()
        calls = []
        reg.register_command("ping", "Ping test",
            lambda text: {"type": "ping"} if text.upper() == "PING" else None,
            lambda daemon, parsed: calls.append("pinged"))
        reg.register_event_type("custom_event",
            lambda daemon, item: calls.append(("event", item["data"])))
        config = {"tmp_dir": str(tmp_path)}
        slack = MagicMock()
        socket_handler = MagicMock()
        worker_registry = MagicMock()
        tmux = MagicMock()
        tmux.log_dir = str(tmp_path / "logs")
        brain = MagicMock()
        d = IroncladeDaemon(config, slack, socket_handler, worker_registry, tmux, brain, plugin_registry=reg)
        return d, calls

    def test_plugin_command_dispatched(self, daemon_with_plugin):
        daemon, calls = daemon_with_plugin
        daemon.socket_handler.drain.return_value = [
            {"parsed": {"type": "ping"}, "respond": None, "original_text": "PING", "ts": "1.0"}
        ]
        daemon.poll_slack_commands()
        assert calls == ["pinged"]

    def test_plugin_event_dispatched(self, daemon_with_plugin):
        daemon, calls = daemon_with_plugin
        daemon.socket_handler.drain.return_value = [
            {"type": "custom_event", "data": 42, "respond": None, "ts": "1.0"}
        ]
        daemon.poll_slack_commands()
        assert calls == [("event", 42)]

    def test_unknown_command_posts_message(self, daemon_with_plugin):
        daemon, calls = daemon_with_plugin
        daemon.socket_handler.drain.return_value = [
            {"parsed": {"type": "nonexistent"}, "respond": None, "original_text": "nonexistent", "ts": "1.0"}
        ]
        daemon.poll_slack_commands()
        daemon.slack.post_message.assert_called()
