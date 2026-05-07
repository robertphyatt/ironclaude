# src/ic/tmux_manager.py
"""tmux session management for IronClaude workers and brain."""

from __future__ import annotations

import collections
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from ironclaude.protocol import validate_safe_id

logger = logging.getLogger("ironclaude.tmux")

_ANSI_RE = re.compile(r'\x1b\[[?!>]*[0-9;]*[a-zA-Z~]|\x1b\].*?\x07|\r')
_SPINNER_RE = re.compile(r'^\s*[^\w\s]{0,3}\w[\w\s]{0,28}\u2026\s*$', re.MULTILINE)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes, spinner frames, carriage returns, and collapse blank lines."""
    cleaned = _ANSI_RE.sub('', text)
    cleaned = _SPINNER_RE.sub('', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned


class TmuxManager:
    def __init__(self, log_dir: str = "/tmp/ic-logs"):
        self.log_dir = log_dir
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    def cleanup_old_logs(self, max_age_days: int = 7) -> int:
        """Delete log files, .done markers, and .brain_contact files older than max_age_days.

        Returns the number of files removed.
        """
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        try:
            for entry in os.scandir(self.log_dir):
                if not entry.is_file():
                    continue
                if entry.name.endswith((".log", ".done", ".brain_contact")):
                    try:
                        if entry.stat().st_mtime < cutoff:
                            os.remove(entry.path)
                            removed += 1
                    except OSError:
                        pass
        except FileNotFoundError:
            pass
        if removed:
            logger.info(f"Cleaned up {removed} old files from {self.log_dir}")
        return removed

    def has_session(self, name: str) -> bool:
        """Check if a tmux session exists."""
        result = subprocess.run(
            ["tmux", "has-session", "-t", name], capture_output=True
        )
        return result.returncode == 0

    def spawn_session(self, name: str, command: str, cwd: str | None = None) -> bool:
        """Create a detached tmux session running command with log capture."""
        validate_safe_id(name)
        args = ["tmux", "new-session", "-d", "-s", name]
        if cwd:
            args.extend(["-c", cwd])
        args.append(command)

        result = subprocess.run(args, capture_output=True)
        if result.returncode != 0:
            logger.error(f"Failed to spawn tmux session {name}: {result.stderr.decode()}")
            return False

        # Enable log capture
        log_path = os.path.join(self.log_dir, f"{name}.log")
        subprocess.run(
            ["tmux", "pipe-pane", "-t", name, f"cat >> {shlex.quote(log_path)}"],
            capture_output=True,
        )
        logger.info(f"Spawned tmux session: {name}, logging to {log_path}")
        return True

    def kill_session(self, name: str) -> bool:
        """Kill a tmux session."""
        result = subprocess.run(
            ["tmux", "kill-session", "-t", name], capture_output=True
        )
        if result.returncode != 0:
            logger.warning(f"Failed to kill tmux session {name}: {result.stderr.decode()}")
            return False
        logger.info(f"Killed tmux session: {name}")
        return True

    def send_keys(self, name: str, text: str) -> bool:
        """Send text + Enter to a tmux session."""
        result = subprocess.run(
            ["tmux", "send-keys", "-t", name, text], capture_output=True
        )
        if result.returncode != 0:
            logger.error(f"Failed to send keys to {name}: {result.stderr.decode()}")
            return False
        time.sleep(0.2)
        subprocess.run(
            ["tmux", "send-keys", "-t", name, "Enter"], capture_output=True
        )
        return True

    def send_raw_keys(self, name: str, keys: list[str]) -> bool:
        """Send raw key sequences to a tmux session. No auto-Enter appended."""
        result = subprocess.run(
            ["tmux", "send-keys", "-t", name] + keys,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.error(f"Failed to send raw keys to {name}: {result.stderr.decode()}")
            return False
        return True

    def get_log_path(self, name: str) -> str:
        """Get the log file path for a session."""
        return os.path.join(self.log_dir, f"{name}.log")

    def capture_pane(self, name: str, lines: int = 50) -> str:
        """Capture rendered terminal output via tmux capture-pane."""
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", name, "-S", f"-{lines}"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout

    def read_log_tail(self, name: str, lines: int = 20) -> str:
        """Read the last N lines of a session's log."""
        log_path = self.get_log_path(name)
        try:
            with open(log_path) as f:
                tail = collections.deque(f, maxlen=lines)
            return _strip_ansi("".join(tail))
        except FileNotFoundError:
            return f"No log file found for {name}"

    def list_pane_pid(self, session_name: str) -> str | None:
        """Get the pane PID for a tmux session. Returns None on failure."""
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return None
            pid = result.stdout.strip()
            return pid if pid.isdigit() else None
        except Exception:
            return None

    def get_log_mtime(self, name: str) -> float | None:
        """Get modification time of session log. None if no log."""
        log_path = self.get_log_path(name)
        try:
            return os.path.getmtime(log_path)
        except FileNotFoundError:
            return None
