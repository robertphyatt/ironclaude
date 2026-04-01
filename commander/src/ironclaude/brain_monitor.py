# src/ic/brain_monitor.py
"""Brain session monitoring and restart logic."""

from __future__ import annotations

import logging
import time

from ironclaude.tmux_manager import TmuxManager

logger = logging.getLogger("ironclaude.brain")

BRAIN_SESSION_NAME = "ic-brain"


class BrainMonitor:
    def __init__(
        self,
        tmux_manager: TmuxManager,
        timeout_seconds: int = 300,
        grace_period_seconds: int = 30,
    ):
        self._tmux = tmux_manager
        self.timeout_seconds = timeout_seconds
        self.grace_period_seconds = grace_period_seconds
        self.restart_count = 0
        self._last_restart_time = 0.0

    def is_alive(self) -> bool:
        """Check if brain log was recently modified."""
        mtime = self._tmux.get_log_mtime(BRAIN_SESSION_NAME)
        if mtime is None:
            return False
        age = time.time() - mtime
        return age < self.timeout_seconds

    def needs_restart(self) -> bool:
        """Check if brain session is gone or log stale."""
        if not self._tmux.has_session(BRAIN_SESSION_NAME):
            return True
        if not self.is_alive():
            # Grace period: don't kill freshly spawned sessions
            if self._last_restart_time > 0:
                elapsed = time.time() - self._last_restart_time
                if elapsed < self.grace_period_seconds:
                    return False
            return True
        return False

    def restart(self, command: str, cwd: str | None = None) -> bool:
        """Kill existing brain session (if any) and start a new one."""
        if self._tmux.has_session(BRAIN_SESSION_NAME):
            self._tmux.kill_session(BRAIN_SESSION_NAME)

        success = self._tmux.spawn_session(BRAIN_SESSION_NAME, command, cwd=cwd)
        if success:
            self.restart_count += 1
            self._last_restart_time = time.time()
            logger.info(f"Brain restarted (count: {self.restart_count})")
        else:
            logger.error("Failed to restart brain session")
        return success

    def send_message(self, text: str) -> bool:
        """Send a message to the brain session."""
        return self._tmux.send_keys(BRAIN_SESSION_NAME, text)
