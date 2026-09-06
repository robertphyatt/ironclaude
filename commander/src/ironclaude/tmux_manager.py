# src/ic/tmux_manager.py
"""tmux session management for IronClaude workers and brain."""

from __future__ import annotations

import collections
from dataclasses import dataclass
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
_MENU_HEADER_RE = re.compile(r'^\s*[☐☑]\s+')
_DIVIDER_RE = re.compile(r'^\s*[─━═_-]{3,}\s*$', re.MULTILINE)
_PROMPT_CHROME_RE = re.compile(
    r'^(?:\s*|\s*[❯>$]\s*.*|\s*[─━═_-]{3,}\s*|'
    r'\s*(?:Claude Code|ironclaude)\b.*|\s*/goal\b.*|'
    r'\s*(?:context\s+left\b.*|context\s*[:|].*|'
    r'professional mode\s*[:|].*|status\s*[:|].*)|'
    r'\s*(?:esc to interrupt|shift\+tab to cycle mode|⏵⏵).*)$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptSignal:
    """Validated semantic identity for one current unresolved interaction."""

    kind: str
    question: str
    options: tuple[tuple[str, str], ...]
    authority_text: str
    source_spans: tuple[tuple[str, int, int], ...]
    evidence: str


def _no_menu_result() -> dict:
    return {
        "detected": False,
        "options": [],
        "free_text_option": None,
        "current_selection": None,
        "question": None,
        "source_spans": [],
        "signal": None,
    }


def detect_ask_user_menu(pane_text: str) -> dict:
    """Detect an AskUserQuestion menu in capture_pane output."""
    cleaned = _strip_ansi(pane_text)
    footers = list(_MENU_FOOTER_RE.finditer(cleaned))
    if not footers:
        return _no_menu_result()
    footer = footers[-1]
    footer_line_start = cleaned.rfind("\n", 0, footer.start()) + 1
    footer_line_end = cleaned.find("\n", footer.end())
    if footer_line_end < 0:
        footer_line_end = len(cleaned)
    if not _suffix_is_prompt_chrome(cleaned[footer_line_end:]):
        return _no_menu_result()
    preceding_dividers = list(_DIVIDER_RE.finditer(cleaned[:footer.start()]))
    prior_footer_lines = [item for item in footers[:-1] if item.end() <= footer_line_start]
    block_start = preceding_dividers[-1].start() if preceding_dividers else (
        prior_footer_lines[-1].end() if prior_footer_lines else 0
    )
    menu_block = cleaned[block_start:footer_line_end]

    options = []
    current_selection = None
    free_text_option = None

    matches = list(_MENU_OPTION_RE.finditer(menu_block))
    for match in matches:
        cursor_char, num_str, label = match.group(1), match.group(2), match.group(3).strip()
        num = int(num_str)
        options.append((num, label))
        if '\u276f' in cursor_char:
            current_selection = num
        if _FREE_TEXT_RE.match(label):
            free_text_option = num

    if not options:
        return _no_menu_result()

    option_start = matches[0].start()
    question = None
    question_span = None
    cursor = 0
    for line in menu_block[:option_start].splitlines(keepends=True):
        text = line.strip()
        if text and not _DIVIDER_RE.match(text) and not _MENU_HEADER_RE.match(text):
            start = block_start + cursor + line.index(text)
            question = text
            question_span = ("question", start, start + len(text))
        cursor += len(line)
    if not question or question_span is None:
        return _no_menu_result()

    spans = [question_span]
    semantic_options = []
    for match, (number, label) in zip(matches, options):
        value_start = block_start + match.start(2)
        label_start = block_start + match.start(3)
        spans.append((f"option-value:{number}", value_start, value_start + len(str(number))))
        spans.append((f"option-label:{number}", label_start, label_start + len(label)))
        semantic_options.append((str(number), label))

    signal = PromptSignal(
        kind="menu",
        question=question,
        options=tuple(semantic_options),
        authority_text="",
        source_spans=tuple(spans),
        evidence=cleaned.strip()[-8192:],
    )

    return {
        "detected": True,
        "options": options,
        "free_text_option": free_text_option,
        "current_selection": current_selection,
        "question": question,
        "source_spans": spans,
        "signal": signal,
    }


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes, spinner frames, carriage returns, and collapse blank lines."""
    cleaned = _ANSI_RE.sub('', text)
    cleaned = _SPINNER_RE.sub('', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned


def _parse_candidate_options(raw_options) -> tuple[tuple[str, str], ...] | None:
    if raw_options in (None, []):
        return ()
    if not isinstance(raw_options, list) or len(raw_options) > 16:
        return None
    parsed = []
    for item in raw_options:
        if not isinstance(item, dict):
            return None
        value = item.get("value")
        label = item.get("label")
        if not isinstance(value, str) or not isinstance(label, str):
            return None
        value = value.strip()
        label = label.strip()
        if not value or not label or len(value) > 128 or len(label) > 512:
            return None
        parsed.append((value, label))
    return tuple(parsed)


def _suffix_is_prompt_chrome(suffix: str) -> bool:
    return all(_PROMPT_CHROME_RE.fullmatch(line) for line in suffix.splitlines())


def validate_prompt_candidate(
    pane_text: str,
    candidate: dict,
    *,
    capture_truncated: bool = False,
) -> PromptSignal | None:
    """Validate a grader candidate against the current final interaction block.

    Exact occurrence establishes provenance. Requiring one final block with only
    prompt chrome after it establishes currentness; pane history cannot qualify.
    """
    if not isinstance(candidate, dict):
        return None
    kind = candidate.get("kind")
    question = candidate.get("question")
    interaction_block = candidate.get("interaction_block")
    authority_text = candidate.get("authority_text", "")
    if kind not in {"question", "approval", "authority"}:
        return None
    if not isinstance(question, str) or not isinstance(interaction_block, str):
        return None
    if not isinstance(authority_text, str):
        return None
    question = question.strip()
    interaction_block = interaction_block.strip()
    authority_text = authority_text.strip()
    if (
        not question
        or not interaction_block
        or len(question) > 2048
        or len(interaction_block) > 4096
        or len(authority_text) > 2048
    ):
        return None
    options = _parse_candidate_options(candidate.get("options", []))
    if options is None:
        return None

    cleaned = _strip_ansi(pane_text)
    starts = [match.start() for match in re.finditer(re.escape(interaction_block), cleaned)]
    if len(starts) != 1:
        return None
    block_start = starts[0]
    block_end = block_start + len(interaction_block)
    if capture_truncated and block_start == 0:
        return None
    line_start = cleaned.rfind("\n", 0, block_start) + 1
    if cleaned[line_start:block_start].strip():
        return None
    if not _suffix_is_prompt_chrome(cleaned[block_end:]):
        return None

    fields = [("question", question)]
    if authority_text:
        fields.append(("authority", authority_text))
    spans = []
    for name, value in fields:
        relative = [
            match.start()
            for match in re.finditer(re.escape(value), interaction_block)
        ]
        if len(relative) != 1:
            return None
        start = block_start + relative[0]
        spans.append((name, start, start + len(value)))

    last_option_span = -1
    for value, label in options:
        pair_re = re.compile(
            rf"(?P<value>{re.escape(value)})[\s.)\]:=\-]{{1,16}}"
            rf"(?P<label>{re.escape(label)})"
        )
        pair_matches = list(pair_re.finditer(interaction_block))
        if len(pair_matches) != 1 or pair_matches[0].start() <= last_option_span:
            return None
        pair = pair_matches[0]
        value_start = block_start + pair.start("value")
        label_start = block_start + pair.start("label")
        spans.append((f"option-value:{value}", value_start, value_start + len(value)))
        spans.append((f"option-label:{value}", label_start, label_start + len(label)))
        last_option_span = pair.start()

    return PromptSignal(
        kind=kind,
        question=question,
        options=options,
        authority_text=authority_text,
        source_spans=tuple(spans),
        evidence=cleaned.strip()[-8192:],
    )


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
            ["tmux", "pipe-pane", "-t", name, f"cat > {shlex.quote(log_path)}"],
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
            ["tmux", "send-keys", "-t", name, "--", text], ssh_host=ssh_host, capture_output=True
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
            ["tmux", "send-keys", "-t", name, "--"] + keys,
            ssh_host=ssh_host, capture_output=True,
        )
        if result.returncode != 0:
            logger.error(f"Failed to send raw keys to {name}: {result.stderr.decode()}")
            return False
        return True

    def get_log_path(self, name: str) -> str:
        """Get the log file path for a session."""
        return os.path.join(self.log_dir, f"{name}.log")

    def get_log_size(self, name: str, ssh_host: str | None = None,
                     remote_log_dir: str | None = None) -> int:
        """Return current byte size of a session log, or zero when unavailable."""
        if ssh_host:
            log_path = os.path.join(remote_log_dir or self.log_dir, f"{name}.log")
            result = self._run(
                ["wc", "-c", log_path], ssh_host=ssh_host,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                return 0
            try:
                return max(0, int(result.stdout.split()[0]))
            except (IndexError, ValueError):
                return 0
        try:
            return os.path.getsize(self.get_log_path(name))
        except OSError:
            return 0

    def read_log_since(self, name: str, offset: int, *, max_bytes: int = 65536,
                       ssh_host: str | None = None,
                       remote_log_dir: str | None = None) -> str:
        """Read at most ``max_bytes`` appended after a previously observed offset."""
        offset = max(0, offset)
        if ssh_host:
            log_path = os.path.join(remote_log_dir or self.log_dir, f"{name}.log")
            result = self._run(
                ["dd", f"if={log_path}", "bs=1", f"skip={offset}",
                 f"count={max_bytes}", "status=none"],
                ssh_host=ssh_host, capture_output=True, text=True,
            )
            return _strip_ansi(result.stdout) if result.returncode == 0 else ""
        try:
            with open(self.get_log_path(name), encoding="utf-8", errors="replace") as stream:
                stream.seek(offset)
                return _strip_ansi(stream.read(max_bytes))
        except OSError:
            return ""

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

    def rename_session(self, old_name: str, new_name: str, ssh_host: str | None = None) -> bool:
        """Rename a tmux session. Preserves the pane and its PID."""
        validate_safe_id(new_name)
        result = self._run(
            ["tmux", "rename-session", "-t", old_name, new_name],
            ssh_host=ssh_host, capture_output=True,
        )
        if result.returncode != 0:
            logger.error(f"Failed to rename tmux session {old_name} -> {new_name}: {result.stderr.decode()}")
            return False
        logger.info(f"Renamed tmux session: {old_name} -> {new_name}")
        return True

    def pane_current_command(self, name: str, ssh_host: str | None = None) -> str | None:
        """Return the pane's current foreground command, or None on failure."""
        result = self._run(
            ["tmux", "list-panes", "-t", name, "-F", "#{pane_current_command}"],
            ssh_host=ssh_host, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        return lines[0] if lines else None

    def setup_log_capture(self, name: str, ssh_host: str | None = None,
                          remote_log_dir: str | None = None) -> str:
        """Enable tmux pipe-pane logging for an already-running session. Returns log path."""
        if remote_log_dir:
            log_path = os.path.join(remote_log_dir, f"{name}.log")
        else:
            log_path = os.path.join(self.log_dir, f"{name}.log")
        self._run(
            ["tmux", "pipe-pane", "-t", name, f"cat >> {shlex.quote(log_path)}"],
            ssh_host=ssh_host, capture_output=True,
        )
        logger.info(f"Enabled log capture for {name} -> {log_path}")
        return log_path

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
