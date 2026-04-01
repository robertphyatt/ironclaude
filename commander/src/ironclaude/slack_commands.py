# src/ic/slack_commands.py
"""Slack Socket Mode handler for IronClaude slash commands.

Daemon thread bridges Slack WebSocket to sequential main loop.
Thread only queues parsed commands. All logic runs in main thread.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time

from ironclaude.slack_interface import parse_inbound_command, SLASH_COMMANDS, format_help_text

logger = logging.getLogger("ironclaude.slack_commands")


class SlackSocketHandler:
    """Socket Mode handler for Slack slash commands."""

    def __init__(self, app_token: str, bot_token: str, operator_user_id: str = ""):
        self._app_token = app_token
        self._bot_token = bot_token
        self._operator_user_id = operator_user_id
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False
        self._connected = False
        self._last_disconnect_time: float | None = None

    def _handle_message_event(self, event: dict, say) -> None:
        """Handle an incoming channel message event."""
        text = event.get("text", "")
        if not text or event.get("bot_id"):
            return
        if self._operator_user_id and event.get("user") != self._operator_user_id:
            logger.warning("Ignoring message from non-operator user: %s", event.get("user"))
            return
        parsed = parse_inbound_command(text)
        self._queue.put({"parsed": parsed, "respond": say, "original_text": text, "ts": event.get("ts", "")})

    def _handle_reaction_added_event(self, event: dict) -> None:
        """Handle an incoming reaction_added event from Slack."""
        item = event.get("item", {})
        logger.debug(
            "reaction_added: emoji=%r item_type=%r ts=%r user=%r",
            event.get("reaction"), item.get("type"), item.get("ts"), event.get("user"),
        )
        if item.get("type") != "message":
            return
        self._queue.put({
            "type": "reaction",
            "emoji": event.get("reaction", ""),
            "message_ts": item.get("ts", ""),
            "user": event.get("user", ""),
        })

    def start(self) -> None:
        """Start Socket Mode in a daemon thread."""
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        app = App(token=self._bot_token)

        @app.command(re.compile(r".*"))
        def handle_command(ack, command, respond):
            ack()
            cmd_name = command.get("command", "").lstrip("/").removeprefix("ironclaude")
            cmd_text = command.get("text", "")
            full_text = f"{cmd_name} {cmd_text}" if cmd_text else cmd_name
            parsed = parse_inbound_command(full_text)
            slash_text = f"/{cmd_name}" + (f" {cmd_text}" if cmd_text else "")
            self._queue.put({"parsed": parsed, "respond": respond, "original_text": slash_text})

        @app.event("message")
        def handle_message(event, say):
            self._handle_message_event(event, say)

        @app.event("reaction_added")
        def handle_reaction_added(event):
            self._handle_reaction_added_event(event)

        self._running = True

        def run_with_reconnect():
            backoff = 1
            while self._running:
                try:
                    handler = SocketModeHandler(app, self._app_token)
                    self._connected = True
                    backoff = 1
                    handler.start()
                except Exception as e:
                    self._connected = False
                    self._last_disconnect_time = time.time()
                    logger.warning(f"Socket Mode disconnected: {e}")
                    if not self._running:
                        break
                    logger.info(f"Socket Mode reconnecting in {backoff}s...")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        self._thread = threading.Thread(target=run_with_reconnect, daemon=True)
        self._thread.start()
        logger.info("Slack Socket Mode started")

    def drain(self) -> list[dict]:
        """Drain all queued commands. Thread-safe."""
        items = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def seconds_since_disconnect(self) -> float | None:
        if self._last_disconnect_time is None:
            return None
        return time.time() - self._last_disconnect_time

    def stop(self) -> None:
        self._running = False
        self._connected = False
        if self._thread is not None:
            self._thread.join(timeout=5)
