# tests/test_slack_commands.py
import pytest
from ironclaude.slack_commands import SlackSocketHandler, format_help_text


class TestSlackSocketHandler:
    def test_drain_empty(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        assert handler.drain() == []

    def test_drain_returns_items(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        handler._queue.put({"parsed": {"type": "status"}, "original_text": "/status"})
        handler._queue.put({"parsed": {"type": "help"}, "original_text": "/help"})
        items = handler.drain()
        assert len(items) == 2
        assert items[0]["parsed"]["type"] == "status"

    def test_drain_clears_queue(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        handler._queue.put({"parsed": {"type": "status"}})
        handler.drain()
        assert handler.drain() == []

    def test_initial_state(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        assert handler.is_connected is False
        assert handler.seconds_since_disconnect is None

    def test_stop_without_start(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        handler.stop()  # Should not raise


class TestReactionAddedEventHandler:
    """Direct tests for _handle_reaction_added_event class method."""

    def test_message_reaction_queued(self):
        """Normal message reaction is queued with correct format."""
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        event = {
            "reaction": "thumbsup",
            "item": {"type": "message", "channel": "C123", "ts": "123.456"},
            "user": "U_OP123",
        }
        handler._handle_reaction_added_event(event)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["type"] == "reaction"
        assert items[0]["emoji"] == "thumbsup"
        assert items[0]["message_ts"] == "123.456"
        assert items[0]["user"] == "U_OP123"

    def test_non_message_item_ignored(self):
        """Reactions on files or other non-message items are not queued."""
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        event = {
            "reaction": "thumbsup",
            "item": {"type": "file", "file": "F123"},
            "user": "U_OP123",
        }
        handler._handle_reaction_added_event(event)
        assert handler.drain() == []

    def test_missing_reaction_key_queues_empty_emoji(self):
        """Missing reaction key results in empty emoji string, no crash."""
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        event = {
            "item": {"type": "message", "channel": "C123", "ts": "123.456"},
            "user": "U_OP123",
        }
        handler._handle_reaction_added_event(event)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["emoji"] == ""

    def test_missing_ts_key_queues_empty_message_ts(self):
        """Missing item.ts results in empty message_ts string, no crash."""
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        event = {
            "reaction": "thumbsup",
            "item": {"type": "message", "channel": "C123"},
            "user": "U_OP123",
        }
        handler._handle_reaction_added_event(event)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["message_ts"] == ""


def test_message_event_includes_ts():
    """Verify message events include ts field from Slack event."""
    handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
    handler._queue.put({
        "parsed": {"type": "message", "text": "hello"},
        "respond": None,
        "original_text": "hello",
        "ts": "123.456",
    })
    items = handler.drain()
    assert len(items) == 1
    assert items[0]["ts"] == "123.456"


class TestOperatorRestriction:
    """Tests for operator_user_id filtering in handle_message."""

    def test_operator_user_id_stored(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="U_OP123")
        assert handler._operator_user_id == "U_OP123"

    def test_operator_user_id_defaults_empty(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test")
        assert handler._operator_user_id == ""

    def test_message_from_operator_is_queued(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="U_OP123")
        event = {"text": "hello", "user": "U_OP123", "ts": "100.1"}
        handler._handle_message_event(event, say=None)
        items = handler.drain()
        assert len(items) == 1
        assert items[0]["original_text"] == "hello"

    def test_message_from_non_operator_is_ignored(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="U_OP123")
        event = {"text": "hello", "user": "U_STRANGER", "ts": "100.2"}
        handler._handle_message_event(event, say=None)
        assert handler.drain() == []

    def test_message_when_no_operator_set_is_forwarded(self):
        """Backward compat: empty operator_user_id allows all users."""
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="")
        event = {"text": "hello", "user": "U_ANYONE", "ts": "100.3"}
        handler._handle_message_event(event, say=None)
        items = handler.drain()
        assert len(items) == 1

    def test_bot_message_always_ignored(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="U_OP123")
        event = {"text": "bot says hi", "user": "U_OP123", "bot_id": "B_BOT", "ts": "100.4"}
        handler._handle_message_event(event, say=None)
        assert handler.drain() == []

    def test_empty_text_always_ignored(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="U_OP123")
        event = {"text": "", "user": "U_OP123", "ts": "100.5"}
        handler._handle_message_event(event, say=None)
        assert handler.drain() == []

    def test_message_ts_preserved(self):
        handler = SlackSocketHandler(app_token="xapp-test", bot_token="xoxb-test", operator_user_id="U_OP123")
        event = {"text": "ping", "user": "U_OP123", "ts": "999.888"}
        handler._handle_message_event(event, say=None)
        items = handler.drain()
        assert items[0]["ts"] == "999.888"


class TestFormatHelpText:
    def test_contains_all_commands(self):
        text = format_help_text()
        assert "/ironclaude status" in text
        assert "/ironclaude stop" in text
        assert "/ironclaude approve" in text
        assert "/ironclaude help" in text
