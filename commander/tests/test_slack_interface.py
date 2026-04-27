# tests/test_slack_interface.py
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ironclaude.slack_interface import SlackBot, parse_inbound_command


class TestSlackBot:
    @patch("ironclaude.slack_interface.WebClient")
    def test_post_message_returns_ts(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "123.456"}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.post_message("Hello")
        assert result == "123.456"
        mock_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="[IRONCLAUDE] Hello", thread_ts=None,
        )

    @patch("ironclaude.slack_interface.WebClient")
    def test_post_message_queues_on_failure(self, mock_client_cls, caplog):
        mock_client = MagicMock()
        mock_client.chat_postMessage.side_effect = Exception("connection refused")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with caplog.at_level(logging.WARNING, logger="ironclaude.slack"):
            result = bot.post_message("Hello")
        assert result is None
        assert len(bot._notification_queue) == 1

    @patch("ironclaude.slack_interface.WebClient")
    def test_flush_queue_retries(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        bot._notification_queue = ["[IRONCLAUDE] queued"]
        bot.flush_queue()
        mock_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="[IRONCLAUDE] queued"
        )
        assert len(bot._notification_queue) == 0

    @patch("ironclaude.slack_interface.WebClient")
    def test_is_reachable_true(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        assert bot.is_reachable() is True

    @patch("ironclaude.slack_interface.WebClient")
    def test_is_reachable_false(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.auth_test.side_effect = Exception("auth failed")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        assert bot.is_reachable() is False

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_recent_messages(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [
                {"text": "hello", "ts": "1.0", "user": "U123"},
                {"text": "bot msg", "ts": "2.0", "bot_id": "B123"},
            ]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        msgs = bot.get_recent_messages()
        assert len(msgs) == 1
        assert msgs[0]["text"] == "hello"

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_recent_messages_raises_on_api_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.side_effect = Exception("api error")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with pytest.raises(Exception, match="api error"):
            bot.get_recent_messages()

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_recent_messages_includes_file_metadata(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [
                {
                    "text": "screenshot attached",
                    "ts": "1.0",
                    "user": "U123",
                    "files": [
                        {
                            "id": "F999",
                            "name": "screenshot.png",
                            "mimetype": "image/png",
                            "url_private_download": "https://files.slack.com/F999/screenshot.png",
                        }
                    ],
                }
            ]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        msgs = bot.get_recent_messages()
        assert len(msgs) == 1
        assert "files" in msgs[0]
        assert msgs[0]["files"][0]["id"] == "F999"
        assert msgs[0]["files"][0]["name"] == "screenshot.png"
        assert msgs[0]["files"][0]["mimetype"] == "image/png"
        assert msgs[0]["files"][0]["url_private_download"] == "https://files.slack.com/F999/screenshot.png"

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_recent_messages_no_files_no_key(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [{"text": "hello", "ts": "1.0", "user": "U123"}]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        msgs = bot.get_recent_messages()
        assert len(msgs) == 1
        assert "files" not in msgs[0]

    @patch("ironclaude.slack_interface.WebClient")
    def test_search_operator_messages_start_date_subtracts_one_day(self, mock_client_cls):
        mock_bot = MagicMock()
        mock_user = MagicMock()
        mock_user.search_messages.return_value = {"messages": {"matches": [], "paging": {"pages": 1}}}
        mock_client_cls.side_effect = [mock_bot, mock_user]
        bot = SlackBot(token="xoxb", channel_id="C123", user_token="xoxp", operator_user_id="U456")
        bot.search_operator_messages(start_date="2026-04-19")
        query = mock_user.search_messages.call_args[1]["query"]
        assert "after:2026-04-18" in query

    @patch("ironclaude.slack_interface.WebClient")
    def test_search_operator_messages_end_date_adds_one_day_in_query(self, mock_client_cls):
        mock_bot = MagicMock()
        mock_user = MagicMock()
        mock_user.search_messages.return_value = {"messages": {"matches": [], "paging": {"pages": 1}}}
        mock_client_cls.side_effect = [mock_bot, mock_user]
        bot = SlackBot(token="xoxb", channel_id="C123", user_token="xoxp", operator_user_id="U456")
        bot.search_operator_messages(start_date="2026-04-19", end_date="2026-04-20")
        query = mock_user.search_messages.call_args[1]["query"]
        assert "before:2026-04-21" in query

    @patch("ironclaude.slack_interface.WebClient")
    def test_search_operator_messages_client_filter_exact_cutoff(self, mock_client_cls):
        from datetime import datetime
        mock_bot = MagicMock()
        mock_user = MagicMock()
        cutoff_start = datetime(2026, 4, 19).timestamp()
        in_ts = str(cutoff_start)
        out_ts = str(cutoff_start - 1)
        mock_user.search_messages.return_value = {
            "messages": {
                "matches": [
                    {"text": "in range", "ts": in_ts, "user": "U456"},
                    {"text": "too early", "ts": out_ts, "user": "U456"},
                ],
                "paging": {"pages": 1},
            }
        }
        mock_client_cls.side_effect = [mock_bot, mock_user]
        bot = SlackBot(token="xoxb", channel_id="C123", user_token="xoxp", operator_user_id="U456")
        result = bot.search_operator_messages(start_date="2026-04-19")
        assert len(result) == 1
        assert result[0]["text"] == "in range"


class TestGetMessagesByTsRange:
    @patch("ironclaude.slack_interface.WebClient")
    def test_get_messages_by_ts_range_calls_conversations_history(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb", channel_id="C123")
        bot.get_messages_by_ts_range("1776657033.774459", "1776657985.900139")
        mock_client.conversations_history.assert_called_once_with(
            channel="C123",
            oldest="1776657033.774459",
            latest="1776657985.900139",
            inclusive=True,
            limit=100,
        )

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_messages_by_ts_range_filters_bots(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [
                {"text": "human", "ts": "1.0", "user": "U123"},
                {"text": "bot", "ts": "2.0", "bot_id": "B456"},
            ]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb", channel_id="C123")
        result = bot.get_messages_by_ts_range("1.0", "2.0", only_operator=True)
        assert len(result) == 1
        assert result[0]["text"] == "human"

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_messages_by_ts_range_includes_all_when_not_operator_only(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [
                {"text": "human", "ts": "1.0", "user": "U123"},
                {"text": "bot", "ts": "2.0", "bot_id": "B456"},
            ]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb", channel_id="C123")
        result = bot.get_messages_by_ts_range("1.0", "2.0", only_operator=False)
        assert len(result) == 2

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_messages_by_ts_range_returns_file_metadata(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [
                {
                    "text": "here is the image",
                    "ts": "1.0",
                    "user": "U123",
                    "files": [
                        {
                            "id": "F001",
                            "name": "card.png",
                            "mimetype": "image/png",
                            "url_private_download": "https://files.slack.com/files-pri/T0/F001/card.png",
                        }
                    ],
                }
            ]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb", channel_id="C123")
        result = bot.get_messages_by_ts_range("1.0", "2.0")
        assert len(result) == 1
        assert "files" in result[0]
        assert result[0]["files"][0]["id"] == "F001"
        assert result[0]["files"][0]["mimetype"] == "image/png"


class TestParseInboundCommand:
    def test_status(self):
        assert parse_inbound_command("STATUS")["type"] == "status"

    def test_help(self):
        assert parse_inbound_command("HELP")["type"] == "help"

    def test_stop(self):
        assert parse_inbound_command("STOP")["type"] == "stop"

    def test_pause(self):
        assert parse_inbound_command("PAUSE")["type"] == "pause"

    def test_resume(self):
        assert parse_inbound_command("RESUME")["type"] == "resume"

    def test_approve_worker(self):
        cmd = parse_inbound_command("APPROVE worker-1")
        assert cmd["type"] == "approve"
        assert cmd["target"] == "worker-1"

    def test_reject_worker(self):
        cmd = parse_inbound_command("REJECT worker-1")
        assert cmd["type"] == "reject"
        assert cmd["target"] == "worker-1"

    def test_detail_worker(self):
        cmd = parse_inbound_command("DETAIL worker-1")
        assert cmd["type"] == "detail"
        assert cmd["target"] == "worker-1"

    def test_log_worker(self):
        cmd = parse_inbound_command("LOG worker-1 50")
        assert cmd["type"] == "log"
        assert cmd["target"] == "worker-1"
        assert cmd["lines"] == 50

    def test_log_default_lines(self):
        cmd = parse_inbound_command("LOG worker-1")
        assert cmd["type"] == "log"
        assert cmd["lines"] == 20

    def test_objective(self):
        cmd = parse_inbound_command("OBJECTIVE Process the D&amp;D chapters")
        assert cmd["type"] == "objective"
        assert cmd["text"] == "Process the D&amp;D chapters"

    def test_free_text_is_message(self):
        cmd = parse_inbound_command("How are the workers doing?")
        assert cmd["type"] == "message"
        assert "workers" in cmd["text"]

    def test_case_insensitive(self):
        assert parse_inbound_command("status")["type"] == "status"
        assert parse_inbound_command("Help")["type"] == "help"

    def test_slash_prefix_stripped(self):
        assert parse_inbound_command("/status")["type"] == "status"
        assert parse_inbound_command("/help")["type"] == "help"

    def test_empty_string(self):
        cmd = parse_inbound_command("")
        assert cmd["type"] == "message"


class TestSlackBotReactions:
    @patch("ironclaude.slack_interface.WebClient")
    def test_add_reaction_calls_api(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.add_reaction("eyes", "123.456")
        assert result is True
        mock_client.reactions_add.assert_called_once_with(
            channel="C123", name="eyes", timestamp="123.456",
        )

    @patch("ironclaude.slack_interface.WebClient")
    def test_add_reaction_returns_false_on_failure(self, mock_client_cls, caplog):
        mock_client = MagicMock()
        mock_client.reactions_add.side_effect = Exception("api error")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with caplog.at_level(logging.WARNING, logger="ironclaude.slack"):
            result = bot.add_reaction("eyes", "123.456")
        assert result is False

    @patch("ironclaude.slack_interface.WebClient")
    def test_add_reaction_ignores_already_reacted(self, mock_client_cls):
        from slack_sdk.errors import SlackApiError
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = {"error": "already_reacted"}
        mock_client.reactions_add.side_effect = SlackApiError(
            message="already_reacted", response=mock_response,
        )
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.add_reaction("eyes", "123.456")
        assert result is True

    @patch("ironclaude.slack_interface.WebClient")
    def test_remove_reaction_calls_api(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.remove_reaction("eyes", "123.456")
        assert result is True
        mock_client.reactions_remove.assert_called_once_with(
            channel="C123", name="eyes", timestamp="123.456",
        )

    @patch("ironclaude.slack_interface.WebClient")
    def test_remove_reaction_ignores_no_reaction(self, mock_client_cls):
        from slack_sdk.errors import SlackApiError
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = {"error": "no_reaction"}
        mock_client.reactions_remove.side_effect = SlackApiError(
            message="no_reaction", response=mock_response,
        )
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.remove_reaction("eyes", "123.456")
        assert result is True

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_reactions_returns_list(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.reactions_get.return_value = {
            "message": {
                "reactions": [
                    {"name": "eyes", "count": 1, "users": ["U123"]},
                ]
            }
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.get_reactions("123.456")
        assert len(result) == 1
        assert result[0]["name"] == "eyes"


class TestSlackBotGetMessage:
    @patch("ironclaude.slack_interface.WebClient")
    def test_get_message_returns_text(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {
            "messages": [{"text": "Directive detected: 'Build feature X'. React to confirm."}]
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.get_message("111.222")
        assert result == "Directive detected: 'Build feature X'. React to confirm."
        mock_client.conversations_history.assert_called_once_with(
            channel="C123", latest="111.222", limit=1, inclusive=True,
        )

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_message_returns_none_on_empty(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.return_value = {"messages": []}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        assert bot.get_message("111.222") is None

    @patch("ironclaude.slack_interface.WebClient")
    def test_get_message_returns_none_on_api_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.conversations_history.side_effect = Exception("api error")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        assert bot.get_message("111.222") is None


class TestSlackBotUploadFile:
    @patch("ironclaude.slack_interface.WebClient")
    def test_upload_file_calls_api_with_correct_params(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.files_upload_v2.return_value = {"file": {"id": "F123ABC"}}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        result = bot.upload_file("/tmp/report.txt", title="Report", comment="Here it is", thread_ts="111.222")
        assert result == "F123ABC"
        mock_client.files_upload_v2.assert_called_once_with(
            channel="C123",
            file="/tmp/report.txt",
            title="Report",
            initial_comment="[IRONCLAUDE] Here it is",
            thread_ts="111.222",
        )

    @patch("ironclaude.slack_interface.WebClient")
    def test_upload_file_uses_basename_when_no_title(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.files_upload_v2.return_value = {"file": {"id": "F456"}}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        bot.upload_file("/tmp/data.csv")
        call_kwargs = mock_client.files_upload_v2.call_args[1]
        assert call_kwargs["title"] == "data.csv"

    @patch("ironclaude.slack_interface.WebClient")
    def test_upload_file_returns_file_id(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.files_upload_v2.return_value = {"file": {"id": "FXYZ"}}
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        assert bot.upload_file("/tmp/x.txt") == "FXYZ"

    @patch("ironclaude.slack_interface.WebClient")
    def test_upload_file_raises_on_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.files_upload_v2.side_effect = Exception("upload failed")
        mock_client_cls.return_value = mock_client
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with pytest.raises(Exception, match="upload failed"):
            bot.upload_file("/tmp/x.txt")


def test_directive_status_emoji_confirmed_is_thumbsup():
    """Verify confirmed maps to thumbsup not eyes."""
    from ironclaude.slack_interface import DIRECTIVE_STATUS_EMOJI
    assert DIRECTIVE_STATUS_EMOJI["confirmed"] == "thumbsup"


class TestAuditCommand:
    def test_audit_command_uppercase(self):
        cmd = parse_inbound_command("AUDIT")
        assert cmd == {"type": "audit"}

    def test_audit_command_lowercase(self):
        cmd = parse_inbound_command("audit")
        assert cmd == {"type": "audit"}

    def test_audit_command_with_slash(self):
        cmd = parse_inbound_command("/audit")
        assert cmd == {"type": "audit"}

    def test_audit_in_slash_commands(self):
        from ironclaude.slack_interface import SLASH_COMMANDS
        assert "audit" in SLASH_COMMANDS


class TestSummaryCommand:
    def test_summary_in_slash_commands(self):
        from ironclaude.slack_interface import SLASH_COMMANDS
        assert "summary" in SLASH_COMMANDS

    def test_summary_command_lowercase(self):
        cmd = parse_inbound_command("summary")
        assert cmd == {"type": "summary"}

    def test_summary_command_uppercase(self):
        cmd = parse_inbound_command("SUMMARY")
        assert cmd == {"type": "summary"}

    def test_summary_command_with_slash(self):
        cmd = parse_inbound_command("/summary")
        assert cmd == {"type": "summary"}

    def test_summary_mixed_case_with_slash(self):
        cmd = parse_inbound_command("/Summary")
        assert cmd == {"type": "summary"}


class TestSearchOperatorMessagesDateValidation:
    @patch("ironclaude.slack_interface.WebClient")
    def test_valid_start_date_accepted(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.search_messages.return_value = {
            "messages": {"paging": {"pages": 1}, "matches": []}
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        bot.search_operator_messages(start_date="2024-01-15")  # must not raise

    @patch("ironclaude.slack_interface.WebClient")
    def test_valid_end_date_accepted(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.search_messages.return_value = {
            "messages": {"paging": {"pages": 1}, "matches": []}
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        bot.search_operator_messages(end_date="2024-12-31")  # must not raise

    @patch("ironclaude.slack_interface.WebClient")
    def test_invalid_start_date_raises(self, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        with pytest.raises(ValueError, match="start_date"):
            bot.search_operator_messages(start_date="2024/01/15")

    @patch("ironclaude.slack_interface.WebClient")
    def test_invalid_end_date_raises(self, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        with pytest.raises(ValueError, match="end_date"):
            bot.search_operator_messages(end_date="Jan 15 2024")

    @patch("ironclaude.slack_interface.WebClient")
    def test_injected_start_date_raises(self, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        with pytest.raises(ValueError, match="start_date"):
            bot.search_operator_messages(start_date="2024-01-01 OR from:*")

    @patch("ironclaude.slack_interface.WebClient")
    def test_injected_end_date_raises(self, mock_client_cls):
        mock_client_cls.return_value = MagicMock()
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        with pytest.raises(ValueError, match="end_date"):
            bot.search_operator_messages(end_date="2024-12-31 before:*")

    @patch("ironclaude.slack_interface.WebClient")
    def test_none_dates_accepted(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.search_messages.return_value = {
            "messages": {"paging": {"pages": 1}, "matches": []}
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        bot.search_operator_messages(start_date=None, end_date=None)  # must not raise


class TestSearchOperatorMessagesFileMetadata:
    @patch("ironclaude.slack_interface.WebClient")
    def test_search_operator_messages_includes_file_metadata(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.search_messages.return_value = {
            "messages": {
                "paging": {"pages": 1},
                "matches": [
                    {
                        "text": "diagram attached",
                        "ts": "9999999999.0",
                        "user": "U123",
                        "files": [
                            {
                                "id": "F777",
                                "name": "diagram.png",
                                "mimetype": "image/png",
                                "url_private_download": "https://files.slack.com/F777/diagram.png",
                            }
                        ],
                    }
                ],
            }
        }
        mock_client_cls.return_value = mock_client
        bot = SlackBot(
            token="xoxb-test", channel_id="C123",
            user_token="xoxp-test", operator_user_id="U123",
        )
        msgs = bot.search_operator_messages(hours_back=24)
        assert len(msgs) == 1
        assert "files" in msgs[0]
        assert msgs[0]["files"][0]["id"] == "F777"
        assert msgs[0]["files"][0]["mimetype"] == "image/png"


class TestSlackBotDownloadFile:
    @patch("ironclaude.slack_interface.requests")
    @patch("ironclaude.slack_interface.WebClient")
    def test_download_file_calls_requests_with_bearer_token(self, mock_client_cls, mock_requests, tmp_path):
        mock_client = MagicMock()
        mock_client.token = "xoxb-test"
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = b"fake image bytes"
        mock_requests.get.return_value = mock_resp
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        save_path = str(tmp_path / "F999_screenshot.png")
        bot.download_file("https://files.slack.com/F999/screenshot.png", save_path)
        mock_requests.get.assert_called_once_with(
            "https://files.slack.com/F999/screenshot.png",
            headers={"Authorization": "Bearer xoxb-test"},
            timeout=30,
        )
        mock_resp.raise_for_status.assert_called_once()
        assert Path(save_path).read_bytes() == b"fake image bytes"

    @patch("ironclaude.slack_interface.requests")
    @patch("ironclaude.slack_interface.WebClient")
    def test_download_file_raises_on_http_error(self, mock_client_cls, mock_requests, tmp_path):
        from requests.exceptions import HTTPError
        mock_client = MagicMock()
        mock_client.token = "xoxb-test"
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = HTTPError("403 Forbidden")
        mock_requests.get.return_value = mock_resp
        bot = SlackBot(token="xoxb-test", channel_id="C123")
        with pytest.raises(HTTPError):
            bot.download_file("https://files.slack.com/F999/screenshot.png", str(tmp_path / "out.png"))
