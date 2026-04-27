# src/ic/slack_interface.py
"""Slack bidirectional interface for IronClaude daemon."""

from __future__ import annotations

import math
import re
import time
import logging
import requests
from datetime import datetime, timedelta
from slack_sdk import WebClient

logger = logging.getLogger("ironclaude.slack")


def _format_message(m: dict) -> dict:
    """Extract standard fields from a Slack message, including file metadata if present."""
    result = {"text": m["text"], "ts": m["ts"], "user": m.get("user", "")}
    if m.get("files"):
        result["files"] = [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "mimetype": f.get("mimetype", ""),
                "url_private_download": f.get("url_private_download", ""),
            }
            for f in m["files"]
        ]
    return result


DIRECTIVE_STATUS_EMOJI = {
    "pending_confirmation": "hourglass_flowing_sand",
    "confirmed": "thumbsup",
    "in_progress": "hammer",
    "completed": "white_check_mark",
    "rejected": "x",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")



class SlackBot:
    def __init__(self, token: str, channel_id: str, user_token: str = "", operator_user_id: str = ""):
        self._client = WebClient(token=token)
        self._channel_id = channel_id
        self._prefix = "[IRONCLAUDE] "
        self._notification_queue: list[str] = []
        self._user_client = WebClient(token=user_token) if user_token else None
        self._operator_user_id = operator_user_id

    def post_message(self, text: str, thread_ts: str | None = None) -> str | None:
        """Post message to channel. Returns ts on success, None on failure."""
        prefixed = f"{self._prefix}{text}"
        try:
            response = self._client.chat_postMessage(
                channel=self._channel_id, text=prefixed, thread_ts=thread_ts,
            )
            return response["ts"]
        except Exception as e:
            logger.warning(f"Slack post_message failed: {e}")
            self._notification_queue.append(prefixed)
            return None

    def upload_file(self, file_path: str, title: str = "", comment: str = "", thread_ts: str | None = None) -> str:
        """Upload a file to the Slack channel. Returns file ID. Raises on failure."""
        import os
        response = self._client.files_upload_v2(
            channel=self._channel_id,
            file=file_path,
            title=title or os.path.basename(file_path),
            initial_comment=f"{self._prefix}{comment}" if comment else "",
            thread_ts=thread_ts,
        )
        return response.get("file", {}).get("id", "")

    def download_file(self, url: str, save_path: str) -> None:
        """Download a private Slack file to save_path using bot token auth. Raises on failure."""
        import os
        dir_part = os.path.dirname(save_path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        resp = requests.get(url, headers={"Authorization": f"Bearer {self._client.token}"}, timeout=30)
        resp.raise_for_status()
        with open(save_path, "wb") as fh:
            fh.write(resp.content)

    def flush_queue(self) -> None:
        """Retry sending queued messages."""
        remaining = []
        for msg in self._notification_queue:
            try:
                self._client.chat_postMessage(channel=self._channel_id, text=msg)
            except Exception as e:
                logger.warning(f"Slack flush retry failed: {e}")
                remaining.append(msg)
        self._notification_queue = remaining

    def get_recent_messages(self, limit: int = 10, oldest: str = "0") -> list[dict]:
        """Fetch recent non-bot messages from channel."""
        result = self._client.conversations_history(
            channel=self._channel_id, limit=limit, oldest=oldest
        )
        return [
            _format_message(m)
            for m in result.get("messages", [])
            if m.get("user") and not m.get("bot_id")
        ]

    def search_operator_messages(
        self,
        limit: int = 20,
        hours_back: int = 24,
        start_date: str | None = None,
        end_date: str | None = None,
        only_operator: bool = True,
    ) -> list[dict]:
        """Search for operator's messages using search.messages API."""
        if not self._user_client or not self._operator_user_id:
            raise RuntimeError("search_operator_messages requires user_token and operator_user_id")

        if start_date is not None and not _DATE_RE.match(start_date):
            raise ValueError(f"start_date must be in YYYY-MM-DD format, got: {start_date!r}")
        if end_date is not None and not _DATE_RE.match(end_date):
            raise ValueError(f"end_date must be in YYYY-MM-DD format, got: {end_date!r}")

        if start_date is not None:
            after_date = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            after_date = (datetime.now() - timedelta(days=math.ceil(hours_back / 24))).strftime("%Y-%m-%d")

        if only_operator:
            query = f"from:<@{self._operator_user_id}> in:<#{self._channel_id}> after:{after_date}"
        else:
            query = f"in:<#{self._channel_id}> after:{after_date}"
        if end_date is not None:
            before_date = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            query += f" before:{before_date}"

        count = min(limit, 100)
        all_matches: list[dict] = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            result = self._user_client.search_messages(query=query, count=count, page=page)
            paging = result.get("messages", {}).get("paging", {})
            if page == 1:
                total_pages = paging.get("pages", 1)
            all_matches.extend(result.get("messages", {}).get("matches", []))
            if len(all_matches) >= limit:
                break
            page += 1

        if start_date is not None:
            cutoff_start = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
        else:
            cutoff_start = time.time() - (hours_back * 3600)

        cutoff_end = (
            datetime.strptime(end_date, "%Y-%m-%d").timestamp() + 86400
            if end_date is not None
            else None
        )

        return [
            _format_message(m)
            for m in all_matches
            if float(m["ts"]) >= cutoff_start
            and (cutoff_end is None or float(m["ts"]) < cutoff_end)
        ]

    def get_messages_by_ts_range(
        self,
        oldest_ts: str,
        latest_ts: str,
        only_operator: bool = True,
    ) -> list[dict]:
        """Fetch messages in exact timestamp range using conversations.history.

        Uses bot token (no user token required). Returns messages with file metadata.
        """
        result = self._client.conversations_history(
            channel=self._channel_id,
            oldest=oldest_ts,
            latest=latest_ts,
            inclusive=True,
            limit=100,
        )
        messages = result.get("messages", [])
        if only_operator:
            messages = [m for m in messages if m.get("user") and not m.get("bot_id")]
        return [_format_message(m) for m in messages]

    def is_reachable(self) -> bool:
        """Check if Slack API is reachable."""
        try:
            self._client.auth_test()
            return True
        except Exception as e:
            logger.warning(f"Slack reachability check failed: {e}")
            return False

    def add_reaction(self, name: str, timestamp: str) -> bool:
        """Add an emoji reaction to a message. Returns True on success."""
        try:
            self._client.reactions_add(
                channel=self._channel_id, name=name, timestamp=timestamp,
            )
            return True
        except Exception as e:
            if hasattr(e, "response") and e.response.data.get("error") == "already_reacted":
                return True
            logger.warning(f"Slack add_reaction failed: {e}")
            return False

    def remove_reaction(self, name: str, timestamp: str) -> bool:
        """Remove an emoji reaction from a message. Returns True on success."""
        try:
            self._client.reactions_remove(
                channel=self._channel_id, name=name, timestamp=timestamp,
            )
            return True
        except Exception as e:
            if hasattr(e, "response") and e.response.data.get("error") == "no_reaction":
                return True
            logger.warning(f"Slack remove_reaction failed: {e}")
            return False

    def get_reactions(self, timestamp: str) -> list[dict]:
        """Get reactions on a message. Returns list of reaction dicts."""
        try:
            result = self._client.reactions_get(
                channel=self._channel_id, timestamp=timestamp,
            )
            return result.get("message", {}).get("reactions", [])
        except Exception as e:
            logger.warning(f"Slack get_reactions failed: {e}")
            return []

    def get_message(self, timestamp: str) -> str | None:
        """Fetch a single message's text by its timestamp. Returns None on failure."""
        try:
            result = self._client.conversations_history(
                channel=self._channel_id,
                latest=timestamp,
                limit=1,
                inclusive=True,
            )
            messages = result.get("messages", [])
            if messages:
                return messages[0].get("text", "")
            return None
        except Exception as e:
            logger.warning(f"Slack get_message failed: {e}")
            return None


# --- Slash command definitions ---

SLASH_COMMANDS = {
    "status": "Current state: brain, workers, objective, progress",
    "detail": "Worker details: /ironclaude detail <worker-id>",
    "approve": "Approve a worker's plan: /ironclaude approve <worker-id>",
    "reject": "Reject a worker's plan: /ironclaude reject <worker-id>",
    "stop": "Kill all workers and pause",
    "pause": "Stop spawning new work, let current finish",
    "resume": "Resume from pause",
    "log": "Worker logs: /ironclaude log <worker-id> [lines]",
    "objective": "Set objective: /ironclaude objective <text>",
    "help": "Show available commands",
    "summary": "Directive status report: in-progress, blocked, completed",
    "audit": "Reconcile Slack messages vs directives (last 72h)",
}


def format_help_text() -> str:
    """Format slash commands into help message."""
    lines = ["*Available Commands:*\n"]
    for cmd, desc in SLASH_COMMANDS.items():
        lines.append(f"`/ironclaude {cmd}` — {desc}")
    lines.append("\nAll commands also work as plain text.")
    return "\n".join(lines)


def parse_inbound_command(text: str, registry=None) -> dict:
    """Parse a user message into a command dict."""
    text = text.strip()
    if text.startswith("/"):
        text = text[1:]
    upper = text.upper()

    if upper == "STOP":
        return {"type": "stop"}

    if upper == "STATUS":
        return {"type": "status"}

    if upper == "HELP":
        return {"type": "help"}

    if upper == "PAUSE":
        return {"type": "pause"}

    if upper == "RESUME":
        return {"type": "resume"}

    approve_match = re.match(r"^APPROVE\s+(.+)$", text, re.IGNORECASE)
    if approve_match:
        return {"type": "approve", "target": approve_match.group(1).strip()}

    reject_match = re.match(r"^REJECT\s+(.+)$", text, re.IGNORECASE)
    if reject_match:
        return {"type": "reject", "target": reject_match.group(1).strip()}

    detail_match = re.match(r"^DETAIL\s+(.+)$", text, re.IGNORECASE)
    if detail_match:
        return {"type": "detail", "target": detail_match.group(1).strip()}

    log_match = re.match(r"^LOG\s+(\S+)(?:\s+(\d+))?$", text, re.IGNORECASE)
    if log_match:
        return {
            "type": "log",
            "target": log_match.group(1),
            "lines": int(log_match.group(2)) if log_match.group(2) else 20,
        }

    obj_match = re.match(r"^OBJECTIVE\s+(.+)$", text, re.IGNORECASE)
    if obj_match:
        return {"type": "objective", "text": obj_match.group(1).strip()}

    if upper == "SUMMARY":
        return {"type": "summary"}

    if upper == "AUDIT":
        return {"type": "audit"}

    if registry is not None:
        plugin_result = registry.parse_command(text)
        if plugin_result is not None:
            return plugin_result

    return {"type": "message", "text": text}
