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
_MENU_FOOTER_RE = re.compile(r'Enter to select|\u2191/\u2193 to navigate', re.MULTILINE)
_MENU_OPTION_RE = re.compile(r'([\u276f\s])\s*(\d+)\.\s+(.+)')
_FREE_TEXT_RE = re.compile(r'(?i)^(other|type\s+something)')


def detect_ask_user_menu(pane_text: str) -> dict:
    """Detect an AskUserQuestion menu in capture_pane output."""
    if not _MENU_FOOTER_RE.search(pane_text):
        return {"detected": False, "options": [], "free_text_option": None, "current_selection": None}

    options = []
    current_selection = None
    free_text_option = None

    for match in _MENU_OPTION_RE.finditer(pane_text):
        cursor_char, num_str, label = match.group(1), match.group(2), match.group(3).strip()
        num = int(num_str)
        options.append((num, label))
        if '\u276f' in cursor_char:
            current_selection = num
        if _FREE_TEXT_RE.match(label):
            free_text_option = num

    if not options:
        return {"detected": False, "options": [], "free_text_option": None, "current_selection": None}

    return {
        "detected": True,
        "options": options,
        "free_text_option": free_text_option,
        "current_selection": current_selection,
    }


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes, spinner frames, carriage returns, and collapse blank lines."""
    cleaned = _ANSI_RE.sub('', text)
    cleaned = _SPINNER_RE.sub('', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned


class TmuxManager:
    def __init__(self, log_dir: str = "/tmp/ic-logs", ssh_manager=None):
        self.log_dir = log_dir
        self._ssh_manager = ssh_manager
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    def _run(self, cmd: list[str], ssh_host: str | None = None, **kwargs) -> subprocess.CompletedProcess:
        """Execute command locally or via SSH."""
        if ssh_host and self._ssh_manager:
            ssh_args = self._ssh_manager.get_ssh_args(ssh_host)
            remote_cmd = " ".join(shlex.quote(c) for c in cmd)
            full_cmd = ssh_args + [remote_cmd]
        else:
            full_cmd = cmd
        return subprocess.run(full_cmd, **kwargs)

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

    def has_session(self, name: str, ssh_host: str | None = None) -> bool:
        """Check if a tmux session exists."""
        result = self._run(
            ["tmux", "has-session", "-t", name], ssh_host=ssh_host, capture_output=True
        )
        return result.returncode == 0

    def list_sessions(self, prefix: str = "ic-") -> list[str]:
        """Return session names matching prefix. Returns [] if tmux server isn't running."""
        result = self._run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []
        return [
            name for name in result.stdout.strip().splitlines()
            if name.startswith(prefix)
        ]

    def spawn_session(self, name: str, command: str, cwd: str | None = None,
                      ssh_host: str | None = None, remote_log_dir: str | None = None) -> bool:
        """Create a detached tmux session running command with log capture."""
        validate_safe_id(name)
        args = ["tmux", "new-session", "-d", "-s", name]
        if cwd:
            args.extend(["-c", cwd])
        args.append(command)

        result = self._run(args, ssh_host=ssh_host, capture_output=True)
        if result.returncode != 0:
            logger.error(f"Failed to spawn tmux session {name}: {result.stderr.decode()}")
            return False

        # Enable log capture
        if remote_log_dir:
            log_path = os.path.join(remote_log_dir, f"{name}.log")
        else:
            log_path = os.path.join(self.log_dir, f"{name}.log")
        self._run(
            ["tmux", "pipe-pane", "-t", name, f"cat >> {shlex.quote(log_path)}"],
            ssh_host=ssh_host, capture_output=True,
        )
        logger.info(f"Spawned tmux session: {name}, logging to {log_path}")
        return True

    def kill_session(self, name: str, ssh_host: str | None = None) -> bool:
        """Kill a tmux session."""
        result = self._run(
            ["tmux", "kill-session", "-t", name], ssh_host=ssh_host, capture_output=True
        )
        if result.returncode != 0:
            logger.warning(f"Failed to kill tmux session {name}: {result.stderr.decode()}")
            return False
        logger.info(f"Killed tmux session: {name}")
        return True

    def send_keys(self, name: str, text: str, ssh_host: str | None = None) -> bool:
        """Send text + Enter to a tmux session."""
        result = self._run(
            ["tmux", "send-keys", "-t", name, text], ssh_host=ssh_host, capture_output=True
        )
        if result.returncode != 0:
            logger.error(f"Failed to send keys to {name}: {result.stderr.decode()}")
            return False
        time.sleep(0.2)
        self._run(
            ["tmux", "send-keys", "-t", name, "Enter"], ssh_host=ssh_host, capture_output=True
        )
        return True

    def send_raw_keys(self, name: str, keys: list[str], ssh_host: str | None = None) -> bool:
        """Send raw key sequences to a tmux session. No auto-Enter appended."""
        result = self._run(
            ["tmux", "send-keys", "-t", name] + keys,
            ssh_host=ssh_host, capture_output=True,
        )
        if result.returncode != 0:
            logger.error(f"Failed to send raw keys to {name}: {result.stderr.decode()}")
            return False
        return True

    def get_log_path(self, name: str) -> str:
        """Get the log file path for a session."""
        return os.path.join(self.log_dir, f"{name}.log")

    def capture_pane(self, name: str, lines: int = 50, ssh_host: str | None = None) -> str:
        """Capture rendered terminal output via tmux capture-pane."""
        kwargs = dict(capture_output=True, text=True)
        if not ssh_host:
            kwargs['check'] = True
        result = self._run(
            ["tmux", "capture-pane", "-p", "-t", name, "-S", f"-{lines}"],
            ssh_host=ssh_host, **kwargs,
        )
        return result.stdout

    def read_log_tail(self, name: str, lines: int = 20, ssh_host: str | None = None,
                      remote_log_dir: str | None = None) -> str:
        """Read the last N lines of a session's log."""
        if ssh_host:
            log_dir = remote_log_dir or self.log_dir
            log_path = os.path.join(log_dir, f"{name}.log")
            result = self._run(
                ["tail", "-n", str(lines), log_path],
                ssh_host=ssh_host, capture_output=True, text=True,
            )
            return _strip_ansi(result.stdout) if result.returncode == 0 else f"No log file found for {name}"
        log_path = self.get_log_path(name)
        try:
            with open(log_path) as f:
                tail = collections.deque(f, maxlen=lines)
            return _strip_ansi("".join(tail))
        except FileNotFoundError:
            return f"No log file found for {name}"

    def list_pane_pid(self, session_name: str, ssh_host: str | None = None) -> str | None:
        """Get the pane PID for a tmux session. Returns None on failure."""
        try:
            result = self._run(
                ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
                ssh_host=ssh_host, capture_output=True, text=True,
            )
            if result.returncode != 0:
                return None
            pid = result.stdout.strip()
            return pid if pid.isdigit() else None
        except Exception:
            return None

    def get_log_mtime(self, name: str, ssh_host: str | None = None,
                      remote_log_dir: str | None = None) -> float | None:
        """Get modification time of session log. None if no log."""
        if ssh_host:
            log_dir = remote_log_dir or self.log_dir
            log_path = os.path.join(log_dir, f"{name}.log")
            result = self._run(
                ["stat", "-c", "%Y", log_path],
                ssh_host=ssh_host, capture_output=True, text=True,
            )
            if result.returncode == 0:
                try:
                    return float(result.stdout.strip())
                except ValueError:
                    return None
            return None
        log_path = self.get_log_path(name)
        try:
            return os.path.getmtime(log_path)
        except FileNotFoundError:
            return None

    # --- Remote file/DB operations ---

    def file_exists(self, path: str, ssh_host: str | None = None) -> bool:
        """Check if a file exists locally or on a remote host."""
        if ssh_host:
            result = self._run(["test", "-f", path], ssh_host=ssh_host, capture_output=True)
            return result.returncode == 0
        return os.path.exists(path)

    def read_file(self, path: str, ssh_host: str | None = None) -> str | None:
        """Read file contents locally or from a remote host."""
        if ssh_host:
            result = self._run(["cat", path], ssh_host=ssh_host, capture_output=True, text=True)
            return result.stdout if result.returncode == 0 else None
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return None

    def write_file(self, path: str, content: str, ssh_host: str | None = None) -> bool:
        """Write content to a file locally or on a remote host."""
        if ssh_host:
            result = self._run(
                ["bash", "-c", f"cat > {shlex.quote(path)}"],
                ssh_host=ssh_host, input=content.encode(), capture_output=True,
            )
            return result.returncode == 0
        try:
            with open(path, "w") as f:
                f.write(content)
            return True
        except OSError:
            return False

    def remove_file(self, path: str, ssh_host: str | None = None) -> bool:
        """Remove a file locally or on a remote host."""
        if ssh_host:
            result = self._run(["rm", "-f", path], ssh_host=ssh_host, capture_output=True)
            return result.returncode == 0
        try:
            os.remove(path)
            return True
        except OSError:
            return False

    def run_sqlite_query(self, db_path: str, query: str,
                         ssh_host: str | None = None) -> str | None:
        """Run a sqlite3 query on a remote host. Returns stdout or None."""
        if ssh_host:
            result = self._run(
                ["sqlite3", db_path, query],
                ssh_host=ssh_host, capture_output=True, text=True,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        return None

    def mkdir_p(self, path: str, ssh_host: str | None = None) -> bool:
        """Create directory (and parents) locally or on a remote host."""
        if ssh_host:
            result = self._run(["mkdir", "-p", path], ssh_host=ssh_host, capture_output=True)
            return result.returncode == 0
        os.makedirs(path, exist_ok=True)
        return True
