# src/ic/main.py
"""Main entry point and loop for the IronClaude daemon."""

from __future__ import annotations

import ctypes
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import time

import psutil
from datetime import datetime, timezone
from pathlib import Path

from ironclaude.config import load_config, load_machines_config, DEFAULTS, make_opus_command
from ironclaude.slack_interface import SlackBot, DIRECTIVE_STATUS_EMOJI
from ironclaude.slack_commands import SlackSocketHandler, format_help_text
from ironclaude.db import init_db
from ironclaude.tmux_manager import TmuxManager, _strip_ansi
from ironclaude.brain_client import BrainClient
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.protocol import read_pending_decisions, read_task_ledger, write_decision
from ironclaude.notifications import (
    format_worker_spawned, format_worker_completed, format_worker_failed,
    format_worker_idle, format_worker_checkin,
    format_heartbeat, format_brain_restarted, format_brain_compacted, format_brain_circuit_breaker,
    format_objective_received,
    format_task_progress, format_plan_ready, format_blocked,
)
from ironclaude.grader import LocalGrader
from ironclaude.orchestrator_mcp import ensure_worker_trusted, WORKER_COMMANDS
from ironclaude.signal_forensics import _logged_kill
from ironclaude.plugins import PluginRegistry, discover_plugins

logger = logging.getLogger("ironclaude")

_BRAIN_MSG_VALIDATION_SYSTEM = (
    "You validate Brain messages before they are posted to Slack.\n\n"
    "A valid Brain message must have BOTH of the following:\n"
    "1. A directive reference — any of: #N, dN, or 'directive N' (e.g. #1083, d1076, directive 42)\n"
    "2. A reason clause — text explaining what the Brain is reporting (status, update, result, error, etc.)\n\n"
    'Respond ONLY with valid JSON: {"valid": true} or {"valid": false, "reason": "..."}'
)
_BRAIN_MSG_SCHEMA = {
    "type": "object",
    "properties": {"valid": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["valid"],
}
_DIRECTIVE_REF_RE = re.compile(r'(?:#\d+|d\d+|directive\s+\d+)', re.IGNORECASE)
_BLOCKED_NO_DIRECTIVE = "no_directive_ref"

_PROMPT_WAITING_SYSTEM = (
    "You detect whether a worker process is waiting for user input based on its log tail.\n\n"
    "Signs of waiting: AskUserQuestion UI, numbered option lists, 'Which approach', 'How would you like', etc.\n"
    "Signs of working: editing files, running tests, reading code, thinking.\n\n"
    'Respond ONLY with valid JSON: {"waiting": true} or {"waiting": false}'
)
_PROMPT_WAITING_SCHEMA = {
    "type": "object",
    "properties": {"waiting": {"type": "boolean"}},
    "required": ["waiting"],
}

PROMPT_WAITING_CACHE_TTL = 120


def log_worker_event(event_type: str, **fields) -> None:
    payload = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    logger.info(json.dumps(payload))


CHECKIN_CADENCE = {
    "idle": 60,
    "undecided": 60,
    "brainstorming": 120,
    "debugging": 120,
    "design_ready": 120,
    "design_marked_for_use": 120,
    "writing_plans": 300,
    "plan_ready": 300,
    "final_plan_prep": 300,
    "executing": 600,
    "reviewing": 600,
    "execution_complete": 900,
}
DEFAULT_CADENCE = 300

OSCILLATION_WINDOW = 900.0
OSCILLATION_THRESHOLD = 3
OSCILLATING_STAGES = frozenset({"executing", "reviewing"})
OSCILLATION_CADENCE = 900

STALENESS_ALERT_SECONDS = 1800
STALENESS_KILL_SECONDS = 3600
STALENESS_PROMPT_ALERT = 900
STALENESS_PROMPT_KILL = 1800
STALENESS_CHECK_INTERVAL = 60
STALENESS_LIVENESS_EXTENSION = 900

PM_GATE_STAGES = frozenset({"plan_ready", "design_ready"})
PM_GATE_SLACK_SECONDS = 1800
MAX_LIVENESS_DEFERRALS = 2

STAGE_STALENESS_MULTIPLIER = {
    "executing": 1.5,
    "reviewing": 1.5,
    "brainstorming": 0.75,
    "debugging": 0.75,
}

_daemon = None
_pid_lock_fd: int | None = None
_clean_shutdown = False
_sigterm_trusted: bool = True  # set False by _sigaction_cb for untrusted SIGTERM senders
_sigaction_callback = None  # GC anchor for ctypes CFUNCTYPE; must outlive sigaction syscall

_PID_FILE = "/tmp/ic-daemon.pid"


def _substitute_prompt(text: str, config: dict) -> str:
    """Replace template placeholders in brain prompt text with config values."""
    text = text.replace("{OPERATOR_NAME}", config.get("operator_name", "Operator"))
    text = text.replace("{AUTONOMY_LEVEL}", str(config.get("autonomy_level", "3")))
    return text


def _load_dotenv(dotenv_path: str = ".env") -> None:
    """Load .env file into os.environ without overriding existing vars.

    Shell environment always takes precedence over .env file values.
    Silently ignores missing file.
    """
    try:
        with open(dotenv_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


def _acquire_singleton_lock() -> None:
    """Acquire an exclusive flock on the PID file to enforce daemon singleton."""
    global _pid_lock_fd
    fd = os.open(_PID_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.fcntl(fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)  # prevent fd inheritance across exec
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            existing = os.read(fd, 32).decode().strip()
            pid_info = f" (PID {existing})" if existing else ""
        except OSError:
            pid_info = ""
        os.close(fd)
        logger.error(f"Another ironclaude daemon is already running{pid_info}. Exiting.")
        logger.info("To stop the existing process: make stop  |  To follow its output: make follow-run")
        sys.exit(1)

    # Lock acquired — check for a live PID from an older (non-locking) daemon
    try:
        existing = os.read(fd, 32).decode().strip()
        if existing:
            try:
                old_pid = int(existing)
                os.kill(old_pid, 0)
                os.close(fd)
                logger.error(f"Another ironclaude daemon is already running (PID {old_pid}). Exiting.")
                logger.info("To stop the existing process: make stop  |  To follow its output: make follow-run")
                sys.exit(1)
            except PermissionError:
                os.close(fd)
                logger.error(f"Another ironclaude daemon is already running (PID {existing}). Exiting.")
                logger.info("To stop the existing process: make stop  |  To follow its output: make follow-run")
                sys.exit(1)
            except ProcessLookupError:
                logger.warning(f"Removing stale PID file from dead process {existing}.")
            except ValueError:
                pass  # Corrupted content — overwrite silently
    except OSError:
        pass

    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{os.getpid()}\n".encode())
    _pid_lock_fd = fd  # Keep open to hold the lock for the process lifetime


def _handle_shutdown(signum, frame):
    import traceback
    global _clean_shutdown, _sigterm_trusted
    logger.warning(
        f"Received signal {signum} — pid={os.getpid()} ppid={os.getppid()} pgid={os.getpgid(0)}"
    )
    logger.warning(f"Shutdown caller stack:\n{''.join(traceback.format_stack(frame))}")
    _clean_shutdown = _sigterm_trusted
    _sigterm_trusted = True  # reset for next signal
    if _daemon:
        if _daemon.plugin_registry:
            _daemon.plugin_registry.run_lifecycle("shutdown", _daemon)
        _daemon.shutdown()
        _daemon.brain._stop_event.set()


def _install_sigaction_handler() -> None:
    """Install SA_SIGINFO signal handler for SIGTERM and SIGINT on macOS arm64.

    Captures sender PID/UID/comm from siginfo_t. Falls back to signal.signal() on any error.
    SA_SIGINFO = 0x0040; struct layouts verified for macOS arm64.
    """
    SA_SIGINFO = 0x0040

    class SigInfo(ctypes.Structure):
        _fields_ = [
            ("si_signo", ctypes.c_int),
            ("si_errno", ctypes.c_int),
            ("si_code",  ctypes.c_int),
            ("si_pid",   ctypes.c_int),   # pid_t, offset 12
            ("si_uid",   ctypes.c_uint),  # uid_t, offset 16
        ]

    class SigAction(ctypes.Structure):
        _fields_ = [
            ("sa_sigaction", ctypes.c_void_p),  # function pointer union, 8 bytes on arm64
            ("sa_mask",      ctypes.c_uint32),   # sigset_t = uint32 on macOS
            ("sa_flags",     ctypes.c_int),
        ]

    SIGACTION_CB = ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.POINTER(SigInfo), ctypes.c_void_p
    )

    def _sigaction_cb(signum, siginfo_ptr, _ctx):
        try:
            if siginfo_ptr:
                sender_pid = siginfo_ptr.contents.si_pid
                sender_uid = siginfo_ptr.contents.si_uid
            else:
                sender_pid = 0
                sender_uid = 0
            try:
                result = subprocess.run(
                    ["ps", "-p", str(sender_pid), "-o", "comm="],
                    capture_output=True, text=True, timeout=2,
                )
                sender_comm = result.stdout.strip() or "<unknown>"
            except Exception:
                sender_comm = "<unknown>"
            logger.warning(
                f"Received signal {signum} FROM pid={sender_pid} uid={sender_uid} "
                f"(comm={sender_comm}) — our pid={os.getpid()} ppid={os.getppid()} "
                f"pgid={os.getpgid(0)}"
            )
            # Trust check: rogue senders leave _clean_shutdown=False so respawner fires
            our_pid = os.getpid()
            our_ppid = os.getppid()
            global _sigterm_trusted
            _sigterm_trusted = sender_pid in (0, 1, our_pid, our_ppid)
            if not _sigterm_trusted:
                logger.warning(
                    f"Rogue SIGTERM: sender_pid={sender_pid} ({sender_comm}) not in trusted set "
                    f"{{0, 1, {our_pid}, {our_ppid}}} — respawner will fire"
                )
        except Exception as e:
            logger.warning(f"Received signal {signum} (siginfo parse error: {e})")
        _handle_shutdown(signum, None)

    global _sigaction_callback
    # Store callback before sigaction syscall — prevents GC between assignment and use
    _sigaction_callback = SIGACTION_CB(_sigaction_cb)

    try:
        libc = ctypes.CDLL(None)
        libc.sigaction.restype = ctypes.c_int
        libc.sigaction.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(SigAction),
            ctypes.POINTER(SigAction),
        ]
        for sig in (signal.SIGTERM, signal.SIGINT):
            sa = SigAction()
            sa.sa_sigaction = ctypes.cast(_sigaction_callback, ctypes.c_void_p)
            sa.sa_mask = 0
            sa.sa_flags = SA_SIGINFO
            ret = libc.sigaction(sig, ctypes.byref(sa), None)
            if ret != 0:
                raise OSError(f"sigaction returned {ret} for signal {sig}")
    except Exception as e:
        logger.warning(f"sigaction setup failed ({e}), falling back to signal.signal")
        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)


def _kill_duplicate_daemons() -> None:
    """Kill any ironclaude.main processes other than the current process before restart."""
    our_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ironclaude.main"],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().splitlines():
            try:
                pid = int(pid_str.strip())
            except ValueError:
                continue
            if pid == our_pid:
                continue
            try:
                _logged_kill(pid, signal.SIGTERM, f"kill_duplicate_daemon pid={pid}")
                logger.info(f"Killed duplicate daemon PID {pid} before restart")
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"Could not kill duplicate daemon PID {pid}: {e}")
    except Exception as e:
        logger.warning(f"Failed to scan for duplicate daemons: {e}")
    time.sleep(2)


def _spawn_respawner() -> None:
    """Fork a detached watchdog that restarts the daemon after 5 seconds."""
    logger.info("Abnormal exit detected — forking crash respawner (5s delay)")
    pid = os.fork()
    if pid == 0:
        # Child: detach from parent's process group
        os.setsid()
        time.sleep(5)
        subprocess.Popen(
            [sys.executable, '-m', 'ironclaude.main', '--no-respawn'],
            start_new_session=True,
        )
        os._exit(0)
    # Parent: continues to exit normally


_BRAIN_SESSION = "ic-brain"


def _kill_orphan_workers(tmux: TmuxManager, registry: WorkerRegistry) -> None:
    """Kill ic-* tmux sessions not in the worker registry (orphans from prior daemon lifecycle)."""
    try:
        sessions = tmux.list_sessions(prefix="ic-")
        registered = {w["tmux_session"] for w in registry.get_running_workers()}
        for name in sessions:
            if name == _BRAIN_SESSION:
                continue
            if name in registered:
                continue
            tmux.kill_session(name)
            logger.info(f"Killed orphan worker session: {name}")
    except Exception as e:
        logger.error(f"Failed to kill orphan worker sessions: {e}")


def _kill_orphan_brains() -> None:
    """Kill any orphaned brain claude subprocesses surviving daemon restart."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
        for pid in pids:
            try:
                _logged_kill(pid, signal.SIGTERM, f"kill_orphan_brain pid={pid}")
                logger.info(f"Killed orphan brain subprocess PID {pid}")
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"Could not kill orphan brain PID {pid}: {e}")
        if pids:
            time.sleep(2)
    except Exception as e:
        logger.warning(f"Failed to kill orphan brains: {e}")


def _handle_restart(signum, frame):
    global _clean_shutdown
    logger.info("Daemon restart requested via SIGHUP")
    _clean_shutdown = True
    if _daemon:
        # Step 1: Poison brain retry loop BEFORE any subprocess kills
        try:
            logger.info("Restart step 1: poison brain retry loop")
            _daemon.brain._stop_event.set()
            _daemon.brain._running = False
        except Exception:
            pass
        logger.info("Restart step 2: daemon.shutdown()")
        _daemon.shutdown()
        # Explicitly stop Slack socket connection (main()'s finally block won't run on execvp)
        try:
            if _daemon.socket_handler:
                logger.info("Restart step 3: socket_handler.stop()")
                _daemon.socket_handler.stop()
        except Exception:
            pass
        # Shut down BrainClient — thread joins quickly since _stop_event already set
        try:
            logger.info("Restart step 4: brain.shutdown()")
            _daemon.brain.shutdown()
        except Exception:
            pass
        # Belt-and-suspenders: kill any brain subprocesses that survived shutdown
        try:
            logger.info("Restart step 5: _kill_orphan_brains()")
            _kill_orphan_brains()
        except Exception:
            pass
        # Kill orphaned worker tmux sessions from previous daemon lifecycle
        try:
            logger.info("Restart step 5b: _kill_orphan_workers()")
            _kill_orphan_workers(_daemon.tmux, _daemon.registry)
        except Exception:
            pass
        # Verify no brain subprocesses remain
        try:
            result = subprocess.run(
                ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
                capture_output=True, text=True, timeout=5,
            )
            survivors = [p for p in result.stdout.strip().split() if p.strip()]
            if survivors:
                logger.warning(f"Brain subprocesses still alive after cleanup: {survivors}")
            else:
                logger.info("Restart step 6: verified no brain subprocesses remain")
        except Exception:
            pass
        # Belt-and-suspenders: targeted kill of brain subprocess if still alive
        try:
            brain_pid = _daemon.brain._brain_pid
            if brain_pid is not None:
                logger.info(f"Restart step 7: targeted kill of brain PID {brain_pid}")
                _logged_kill(brain_pid, signal.SIGTERM, "handle_restart targeted brain kill")
                time.sleep(1)
            else:
                logger.info("Restart step 7: no brain PID to kill")
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            pass
        # Close the DB connection before exec
        try:
            if _daemon._db:
                _daemon._db.close()
        except Exception:
            pass
    # Kill any other ironclaude.main processes before restarting
    logger.info("Restart step 8: _kill_duplicate_daemons()")
    _kill_duplicate_daemons()
    global _pid_lock_fd
    if _pid_lock_fd is not None:
        try:
            logger.info("Restart step 9: releasing lock fd")
            os.ftruncate(_pid_lock_fd, 0)
        except OSError:
            pass
        try:
            os.close(_pid_lock_fd)
        except OSError:
            pass
        _pid_lock_fd = None
    logger.info("Restart step 10: os.execvp() — this is the last log line before restart")
    os.execvp(sys.executable, [sys.executable, '-m', 'ironclaude.main'])


def ensure_brain_trusted(brain_cwd: str) -> None:
    """Ensure the brain's working directory is trusted in ~/.claude.json.

    Reads ~/.claude.json, checks if brain_cwd has hasTrustDialogAccepted=true,
    adds a minimal project entry if missing, and writes back with file locking.
    Defensive: logs warnings and continues on any failure.
    """
    claude_json_path = os.path.expanduser("~/.claude.json")
    abs_cwd = os.path.abspath(brain_cwd)

    try:
        with open(claude_json_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse {claude_json_path}")
                return

            projects = data.get("projects", {})
            project = projects.get(abs_cwd, {})

            if project.get("hasTrustDialogAccepted") is True:
                logger.info(f"Brain directory already trusted: {abs_cwd}")
                return

            project["hasTrustDialogAccepted"] = True
            project.setdefault("allowedTools", [])
            projects[abs_cwd] = project
            data["projects"] = projects

            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
            logger.info(f"Added trust entry for brain directory: {abs_cwd}")
    except FileNotFoundError:
        logger.warning(f"{claude_json_path} not found — cannot pre-trust brain directory")
    except OSError as e:
        logger.warning(f"Could not update {claude_json_path}: {e}")


def _worker_matches_directive(worker: dict, directive_id: int) -> bool:
    description = worker.get("description") or ""
    worker_id = worker.get("id") or ""
    return f"#{directive_id}" in description or worker_id.startswith(f"d{directive_id}-")


class IroncladeDaemon:
    def __init__(self, config: dict, slack: SlackBot, socket_handler: SlackSocketHandler | None,
                 registry: WorkerRegistry, tmux_manager: TmuxManager, brain: BrainClient,
                 db_conn=None, plugin_registry=None, ssh_manager=None):
        self.config = config
        self.slack = slack
        self.socket_handler = socket_handler
        self.registry = registry
        self.tmux = tmux_manager
        self.brain = brain
        self._running = True
        self._paused = False
        self._brain_paused = False
        self.plugin_registry = plugin_registry or PluginRegistry()
        self._last_heartbeat = 0.0
        self._decisions_dir = os.path.join(config.get("tmp_dir", "/tmp/ic"), "brain-decisions")
        self._ledger_path = os.path.join(config.get("tmp_dir", "/tmp/ic"), "task-ledger.json")
        self._db = db_conn
        self._last_checkin_sent: dict[str, float] = {}
        self._last_checkin_stage: dict[str, str | None] = {}
        self._last_checkin_hash: dict[str, int] = {}
        self._last_stage_seen: dict[str, str | None] = {}
        self._stage_history: dict[str, list[tuple[float, str]]] = {}
        self._directive_reminder_sent: dict[int, float] = {}
        self._claude_dir: Path | None = None
        self._last_maintenance = 0.0
        self._state_manager_db_path = os.path.expanduser("~/.claude/ironclaude.db")
        self._ssh_manager = ssh_manager
        # Idle enforcement state
        self._idle_enforcement_start = 0.0
        self._idle_escalation_tier = 0
        self._last_idle_check = 0.0
        self._operator_notified_idle = False
        # Post-kill sweep state
        self._last_kill_sweep_check: float = time.time()
        # Message aging state
        self._last_message_aging_check: float = 0.0
        self._message_aging_alerted: set[str] = set()
        # Stuck worker detection state
        self._stuck_hash: dict[str, int] = {}
        self._stuck_since: dict[str, float] = {}
        self._stuck_alert_sent: dict[str, bool] = {}
        self._stuck_kill_deferred: dict[str, float] = {}
        self._last_stuck_check: float = 0.0
        self._grader = LocalGrader()
        self._prompt_waiting_cache: dict[int, tuple[float, bool]] = {}
        self._stuck_liveness_count: dict[str, int] = {}
        self._pm_gate_slack_sent: dict[str, bool] = {}
        self._stage_entered_at: dict[str, float] = {}
        self._load_staleness_state()

    def shutdown(self):
        self._running = False

    def _get_unprocessed_messages(self, max_age_seconds: int = 1800) -> list[dict]:
        """Find operator messages older than max_age with no matching directive source_ts."""
        operator_user_id = self.config.get("slack_operator_user_id", "")
        if not operator_user_id or self._db is None:
            return []
        try:
            oldest = str(time.time() - 7200)
            messages = self.slack.get_recent_messages(limit=50, oldest=oldest)
        except Exception:
            return []
        now = time.time()
        try:
            directive_ts_rows = self._db.execute("SELECT source_ts FROM directives").fetchall()
            directive_ts_set = {row[0] for row in directive_ts_rows}
        except Exception:
            return []
        result = []
        for msg in messages:
            if msg.get("user") != operator_user_id:
                continue
            msg_age = now - float(msg["ts"])
            if msg_age < max_age_seconds:
                continue
            if msg["ts"] in directive_ts_set:
                continue
            result.append(msg)
        return result

    def _validate_brain_message(self, text: str) -> tuple[bool, str]:
        """Validate Brain message has directive reference and reason clause via LLM."""
        if not text.strip():
            return False, "Empty message"
        if not _DIRECTIVE_REF_RE.search(text):
            return False, _BLOCKED_NO_DIRECTIVE
        result = self._grader.grade(
            _BRAIN_MSG_VALIDATION_SYSTEM,
            f"Validate this Brain message:\n{text}",
            _BRAIN_MSG_SCHEMA,
        )
        if result.get("infrastructure_error"):
            logger.warning(
                "Brain message validator unavailable: %s — allowing through",
                result.get("error_detail"),
            )
            return True, ""
        if result.get("valid", True):
            return True, ""
        return False, result.get("reason", "Message does not meet Brain message requirements")

    def check_post_kill_sweep(self):
        """Send mandatory sweep message to Brain after each kill_worker event."""
        if self._db is None:
            return
        try:
            rows = self._db.execute(
                "SELECT worker_id FROM events "
                "WHERE event_type = 'worker_finished' "
                "AND timestamp > datetime(?, 'unixepoch')",
                (self._last_kill_sweep_check,),
            ).fetchall()
        except Exception:
            return
        self._last_kill_sweep_check = time.time()
        if not rows:
            return
        try:
            directives = self._db.execute(
                "SELECT id, interpretation FROM directives "
                "WHERE status IN ('confirmed', 'in_progress')"
            ).fetchall()
        except Exception:
            return
        if not directives:
            return
        directive_list = ", ".join(f"#{r[0]}: {r[1][:60]}" for r in directives)
        for row in rows:
            worker_id = row[0] or "unknown"
            self.brain.send_message(
                f"[MANDATORY SWEEP] Worker {worker_id} completed. "
                f"{len(directives)} directive(s) remain unworked: {directive_list}. "
                f"Run full attention sweep — spawn workers for all unblocked "
                f"directives before doing anything else."
            )

    def check_message_aging(self):
        """Alert Brain about operator messages >30min old without directives."""
        now = time.time()
        if now - self._last_message_aging_check < 300:
            return
        self._last_message_aging_check = now

        unprocessed = self._get_unprocessed_messages()

        if self._message_aging_alerted and self._db is not None:
            try:
                directive_ts_rows = self._db.execute(
                    "SELECT source_ts FROM directives"
                ).fetchall()
                directive_ts_set = {row[0] for row in directive_ts_rows}
                self._message_aging_alerted -= directive_ts_set
            except Exception:
                pass

        for msg in unprocessed:
            ts = msg["ts"]
            if ts in self._message_aging_alerted:
                continue
            minutes_ago = int((now - float(ts)) / 60)
            self.brain.send_message(
                f"[UNPROCESSED MESSAGE] Operator message from {minutes_ago} minutes ago "
                f"has not been processed into a directive. "
                f'Message: "{msg["text"][:100]}..." '
                f"(ts: {ts}). Read this message and submit_directive() or acknowledge it."
            )
            self._message_aging_alerted.add(ts)

    def _run_maintenance(self):
        """Run periodic maintenance: clean old logs and prune DB tables.

        Runs on first call and then hourly. Each sub-operation is independent —
        one failure does not block others. Episodic memory is NEVER touched.
        """
        now = time.time()
        if now - self._last_maintenance < 3600:
            return
        self._last_maintenance = now

        # 1. Clean old log files (7 days)
        try:
            self.tmux.cleanup_old_logs(7)
        except Exception as e:
            logger.warning(f"Maintenance: log cleanup failed: {e}")

        # 2. Prune daemon events table (30 days)
        try:
            if self._db:
                self._db.execute(
                    "DELETE FROM events WHERE timestamp < datetime('now', '-30 days')"
                )
                self._db.commit()
        except Exception as e:
            logger.warning(f"Maintenance: events pruning failed: {e}")

        # 3. Prune state-manager audit_log (90 days) — separate DB, best-effort
        try:
            conn = sqlite3.connect(self._state_manager_db_path, timeout=10)
            try:
                conn.execute(
                    "DELETE FROM audit_log WHERE created_at < datetime('now', '-90 days')"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Maintenance: audit_log pruning failed: {e}")

    def poll_slack_commands(self):
        """Drain and process Slack commands."""
        if not self.socket_handler:
            return
        for item in self.socket_handler.drain():
            # Check for directive confirmation before command parsing
            raw_text = item.get("original_text", "").strip()
            if self._handle_directive_confirmation(raw_text):
                continue
            # Check for reaction events
            if item.get("type") == "reaction":
                logger.info("routing reaction: emoji=%r ts=%r", item["emoji"], item["message_ts"])
                if not self._handle_directive_reaction(item["emoji"], item["message_ts"]):
                    self._handle_push_reaction(item["emoji"], item["message_ts"])
                continue
            # Check for plugin event types
            if "parsed" not in item and item.get("type"):
                if self.plugin_registry.handle_event(self, item):
                    continue
            parsed = item["parsed"]
            cmd_type = parsed["type"]
            logger.info(f"Slack command: {item.get('original_text', cmd_type)}")

            if cmd_type == "help":
                self.slack.post_message(format_help_text())
            elif cmd_type == "status":
                self._handle_status()
            elif cmd_type == "stop":
                self.slack.post_message("Stopping all work and shutting down.")
                self.shutdown()
            elif cmd_type == "pause":
                self._paused = True
                self.slack.post_message("Paused. No new work will be started.")
            elif cmd_type == "resume":
                self._paused = False
                self.slack.post_message("Resumed.")
            elif cmd_type == "message":
                text = parsed.get("text", "")
                msg_ts = item.get("ts", "")
                if msg_ts:
                    self.slack.add_reaction("eyes", msg_ts)
                self.brain.send_message(
                    f"OPERATOR MESSAGE (ts={msg_ts}): {text}"
                )
                self.slack.post_message(f"Forwarded to brain: {text}")
            elif cmd_type == "detail":
                self._handle_detail(parsed)
            elif cmd_type == "log":
                self._handle_log(parsed)
            elif cmd_type == "objective":
                text = parsed.get("text", "")
                obj_id = self.registry.create_objective(text)
                self.slack.post_message(format_objective_received(text))
                self.brain.send_message(f"NEW OBJECTIVE: {text}")
                self.registry.log_event("objective_received", details={"text": text, "id": obj_id})
            elif cmd_type == "approve":
                worker_id = parsed.get("target", "")
                write_decision(self._decisions_dir, {"action": "approve_plan", "worker_id": worker_id})
                self.slack.post_message(f"Approval queued for `{worker_id}`.")
            elif cmd_type == "reject":
                worker_id = parsed.get("target", "")
                write_decision(self._decisions_dir, {"action": "reject_plan", "worker_id": worker_id, "reason": "User rejected via Slack"})
                self.slack.post_message(f"Rejection queued for `{worker_id}`.")
            elif cmd_type == "summary":
                self._handle_summary()
            elif cmd_type == "audit":
                self._handle_audit()
            elif self.plugin_registry.handle_command(self, cmd_type, parsed):
                pass  # handled by plugin
            else:
                self.slack.post_message(f"Command `{cmd_type}` acknowledged (not yet implemented).")

    def _handle_directive_confirmation(self, text: str) -> bool:
        """Check if text is a yes/no reply to a pending directive confirmation.

        Returns True if handled (caller should skip normal processing).
        """
        if self._db is None:
            return False
        normalized = text.strip().lower()
        if normalized not in ("yes", "no"):
            return False
        row = self._db.execute(
            "SELECT id, interpretation, interpretation_ts FROM directives "
            "WHERE status='pending_confirmation' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        directive_id = row[0]
        interpretation = row[1]
        if normalized == "yes":
            self._db.execute(
                "UPDATE directives SET status='confirmed', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} confirmed: {interpretation}")
            operator = self.config.get("operator_name", "Operator")
            self.brain.send_message(f"Directive #{directive_id} confirmed by {operator}: {interpretation}")
        else:
            self._db.execute(
                "UPDATE directives SET status='rejected', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} rejected.")
        if row[2]:
            self.slack.unpin_message(row[2])
        return True

    def _match_directive_by_content(self, message_text: str) -> tuple | None:
        """Match message text to a pending directive by content.

        Strategies in order:
        1. Regex for 'Directive #N' — look up by ID
        2. Check if text contains a pending directive's interpretation
        3. Check if text matches a pending directive's source_text
        """
        # Strategy 1: Directive ID reference
        match = re.search(r"Directive\s*#(\d+)", message_text, re.IGNORECASE)
        if match:
            directive_id = int(match.group(1))
            row = self._db.execute(
                "SELECT id, interpretation FROM directives "
                "WHERE id=? AND status IN ('pending_confirmation','in_progress') LIMIT 1",
                (directive_id,),
            ).fetchone()
            if row:
                return row

        # Strategy 2: Interpretation text match
        pending = self._db.execute(
            "SELECT id, interpretation FROM directives "
            "WHERE status IN ('pending_confirmation','in_progress')"
        ).fetchall()
        for row in pending:
            if row[1] in message_text:
                return row

        # Strategy 3: Source text match
        pending_sources = self._db.execute(
            "SELECT id, interpretation, source_text FROM directives "
            "WHERE status IN ('pending_confirmation','in_progress')"
        ).fetchall()
        for row in pending_sources:
            if row[2] in message_text:
                return (row[0], row[1])

        return None

    def _handle_directive_reaction(self, emoji: str, message_ts: str) -> bool:
        """Handle a reaction on any directive-related message.

        Tries fast-path timestamp match first, then falls back to content-based
        matching by fetching the reacted-to message text and searching for
        directive references.

        Returns True if handled (matched a pending directive).
        """
        if self._db is None:
            return False
        logger.info("_handle_directive_reaction: emoji=%r ts=%r", emoji, message_ts)
        if emoji not in ("thumbsup", "+1", "thumbs_up", "thumbsdown", "-1", "thumbs_down"):
            logger.debug("reaction emoji %r not in accepted set, ignoring", emoji)
            return False

        # Fast path: match on interpretation_ts (bot's message) or source_ts (operator's message)
        row = self._db.execute(
            "SELECT id, interpretation FROM directives "
            "WHERE (interpretation_ts=? OR source_ts=?) "
            "AND status IN ('pending_confirmation','in_progress') LIMIT 1",
            (message_ts, message_ts),
        ).fetchone()

        if row is not None:
            logger.info("Fast-path matched directive #%d for reaction ts=%r", row[0], message_ts)

        # Fallback: content-based matching
        if row is None and self.slack is not None:
            message_text = self.slack.get_message(message_ts)
            if message_text:
                row = self._match_directive_by_content(message_text)
                if row:
                    logger.info(
                        "Content-based match: directive #%d matched via message text", row[0]
                    )

        if row is None:
            pending = self._db.execute(
                "SELECT id, interpretation_ts, source_ts FROM directives "
                "WHERE status IN ('pending_confirmation','in_progress')"
            ).fetchall()
            pending_info = [(r[0], r[1], r[2]) for r in pending] if pending else []
            logger.warning(
                "No directive matched for reaction ts=%r. Pending directives (id, interpretation_ts, source_ts): %r",
                message_ts, pending_info,
            )
            return False

        directive_id = row[0]
        interpretation = row[1]
        ts_row = self._db.execute(
            "SELECT interpretation_ts FROM directives WHERE id=?", (directive_id,)
        ).fetchone()
        interpretation_ts = ts_row[0] if ts_row else None
        if emoji in ("thumbsup", "+1", "thumbs_up"):
            self._db.execute(
                "UPDATE directives SET status='confirmed', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} confirmed: {interpretation}")
            operator = self.config.get("operator_name", "Operator")
            delivered = self.brain.send_message(
                f"Directive #{directive_id} confirmed by {operator}: {interpretation}"
            )
            if delivered:
                self._directive_reminder_sent[directive_id] = time.time()
            else:
                logger.warning(
                    "Brain unreachable; directive #%d confirmation will retry via check_confirmed_directives",
                    directive_id,
                )
            source_ts = self._db.execute(
                "SELECT source_ts FROM directives WHERE id=?", (directive_id,)
            ).fetchone()[0]
            self.slack.remove_reaction("hourglass_flowing_sand", source_ts)
            self.slack.add_reaction(DIRECTIVE_STATUS_EMOJI.get("confirmed", "thumbsup"), source_ts)
        else:
            self._db.execute(
                "UPDATE directives SET status='rejected', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} rejected.")
            source_ts = self._db.execute(
                "SELECT source_ts FROM directives WHERE id=?", (directive_id,)
            ).fetchone()[0]
            self.slack.remove_reaction("hourglass_flowing_sand", source_ts)
            self.slack.add_reaction(DIRECTIVE_STATUS_EMOJI.get("rejected", "x"), source_ts)
        if interpretation_ts:
            self.slack.unpin_message(interpretation_ts)
        new_status = "confirmed" if emoji in ("thumbsup", "+1", "thumbs_up") else "rejected"
        logger.info("Directive #%d %s via reaction %r", directive_id, new_status, emoji)
        return True

    def _handle_push_reaction(self, emoji: str, message_ts: str) -> bool:
        """Handle ✅/❌ reaction on a push request confirmation message.

        Returns True if the reaction matched a push request (handled), False otherwise.
        """
        if self._db is None:
            return False
        if emoji not in ("white_check_mark", "x"):
            return False

        row = self._db.execute(
            "SELECT id, repo, remote, branch FROM push_requests"
            " WHERE message_ts=? AND status='pending' LIMIT 1",
            (message_ts,),
        ).fetchone()
        if row is None:
            return False

        push_id, repo, remote, branch = row[0], row[1], row[2], row[3]

        expired = self._db.execute(
            "SELECT COUNT(*) FROM push_requests WHERE id=? AND expires_at < datetime('now')",
            (push_id,),
        ).fetchone()[0]
        if expired:
            self._db.execute(
                "UPDATE push_requests SET status='expired' WHERE id=?", (push_id,)
            )
            self._db.commit()
            self.slack.post_message(f"Push request `{push_id[:8]}` expired before confirmation.")
            self.slack.unpin_message(message_ts)
            logger.info("Push %s expired — no push executed", push_id[:8])
            return True

        if emoji == "x":
            self._db.execute(
                "UPDATE push_requests SET status='rejected' WHERE id=?", (push_id,)
            )
            self._db.commit()
            self.slack.post_message(f"Push request `{push_id[:8]}` rejected.")
            self.slack.unpin_message(message_ts)
            self.brain.send_message(f"Push {push_id[:8]} was rejected by the operator.")
            logger.info("Push %s rejected", push_id[:8])
            return True

        result = subprocess.run(
            ["git", "push", remote, branch],
            cwd=repo, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            self._db.execute(
                "UPDATE push_requests SET status='completed' WHERE id=?", (push_id,)
            )
            self._db.commit()
            self.slack.post_message(
                f"Push request `{push_id[:8]}` completed: `{remote}/{branch}` pushed successfully."
            )
            self.brain.send_message(
                f"Push {push_id[:8]} completed: {remote}/{branch} pushed successfully."
            )
            logger.info("Push %s completed: %s/%s", push_id[:8], remote, branch)
        else:
            self._db.execute(
                "UPDATE push_requests SET status='failed' WHERE id=?", (push_id,)
            )
            self._db.commit()
            err = result.stderr.strip()
            self.slack.post_message(f"Push request `{push_id[:8]}` failed: {err}")
            self.brain.send_message(f"Push {push_id[:8]} failed: {err}")
            logger.warning("Push %s failed: %s", push_id[:8], err)
        self.slack.unpin_message(message_ts)
        return True

    def _sweep_expired_push_requests(self):
        """Mark pending push requests past their TTL as expired."""
        if self._db is None:
            return
        try:
            rows = self._db.execute(
                "SELECT message_ts FROM push_requests"
                " WHERE status='pending' AND expires_at < datetime('now')"
            ).fetchall()
            self._db.execute(
                "UPDATE push_requests SET status='expired'"
                " WHERE status='pending' AND expires_at < datetime('now')"
            )
            self._db.commit()
            for row in rows:
                if row[0]:
                    self.slack.unpin_message(row[0])
        except sqlite3.OperationalError as e:
            self._db.rollback()
            logger.warning("push_requests sweep skipped (db locked): %s", e)

    def poll_brain_responses(self):
        """Drain brain responses, validate context, and post to Slack."""
        for text in self.brain.get_pending_responses():
            logger.info(f"Brain response: {text[:100]}...")
            valid, reason = self._validate_brain_message(text)
            if not valid:
                if reason == _BLOCKED_NO_DIRECTIVE:
                    logger.info("Brain response dropped (no directive ref): %s", text[:100])
                    continue
                logger.warning(f"Brain message blocked: {reason} | text={text[:200]}")
                self.brain.send_message(
                    f"[CONTEXT REQUIRED] Your message was blocked from Slack. "
                    f"Reason: {reason}. Restate your message with: "
                    f"(1) a directive reference (#N or dN), and "
                    f"(2) why you are reporting this (status/update/result). "
                    f'Original message: "{text[:200]}..."'
                )
                continue
            if len(text) > 39000:
                chunks = [text[i:i+39000] for i in range(0, len(text), 39000)]
                for chunk in chunks:
                    self.slack.post_message(f"*Brain:* {chunk}")
            else:
                self.slack.post_message(f"*Brain:* {text}")

    def _handle_status(self):
        running = self.registry.get_running_workers()
        obj = self.registry.get_active_objective()
        ledger = read_task_ledger(self._ledger_path)
        progress = f"{ledger['current_task']}/{ledger['total_tasks']}" if ledger else "N/A"
        status_lines = [
            "*IronClaude Status*",
            f"State: {'paused' if self._paused else 'running'}",
            f"Workers: {len(running)} active",
            f"Brain: {'alive' if self.brain.is_alive() else 'dead/stale'}",
            f"Objective: {obj['text'] if obj else 'none'}",
            f"Progress: {progress}",
        ]
        self.slack.post_message("\n".join(status_lines))

    def _handle_summary(self):
        if self._db is None:
            self.slack.post_message("Database not configured.")
            return
        in_progress = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='in_progress' ORDER BY created_at DESC"
        ).fetchall()
        needs_input = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='pending_confirmation' ORDER BY created_at DESC"
        ).fetchall()
        recently_completed = self._db.execute(
            "SELECT id, interpretation FROM directives WHERE status='completed' ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        workers = self.registry.get_running_workers()
        lines = ["*Directive Summary*", ""]
        lines.append(f"*In Progress ({len(in_progress)}):*")
        if in_progress:
            for row in in_progress:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        if workers:
            worker_ids = ", ".join(w["id"] for w in workers)
            lines.append(f"Active workers: {worker_ids}")
        lines.append("")
        lines.append(f"*Blocked / Needs Input ({len(needs_input)}):*")
        if needs_input:
            for row in needs_input:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("*Recently Completed (last 5):*")
        if recently_completed:
            for row in recently_completed:
                lines.append(f"• #{row[0]} — {row[1]}")
        else:
            lines.append("(none)")
        self.slack.post_message("\n".join(lines))

    def _handle_audit(self):
        if self._db is None:
            self.slack.post_message("Database not configured.")
            return
        try:
            messages = self.slack.search_operator_messages(limit=100, hours_back=72)
        except RuntimeError as e:
            self.slack.post_message(f"Audit unavailable: {e}")
            return
        rows = self._db.execute(
            "SELECT id, source_ts, interpretation, status FROM directives ORDER BY created_at DESC"
        ).fetchall()
        directive_by_ts = {row[1]: row for row in rows}
        mapped = []
        unmapped = []
        for msg in messages:
            ts = msg["ts"]
            if ts in directive_by_ts:
                row = directive_by_ts[ts]
                mapped.append((ts, row[0], row[2], row[3]))
            else:
                unmapped.append(msg)
        lines = ["*Slack Audit Report (72h)*", ""]
        lines.append("📊 *Summary*")
        lines.append(f"• Messages scanned: {len(messages)}")
        lines.append(f"• Mapped to directives: {len(mapped)}")
        lines.append(f"• Unmapped: {len(unmapped)}")
        lines.append("")
        lines.append("✅ *Mapped Messages*")
        if mapped:
            for ts, d_id, interpretation, status in mapped:
                lines.append(f"• `ts:{ts}` → d{d_id} ({status}): {interpretation[:60]}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("⚠️ *Unmapped Messages*")
        if unmapped:
            for msg in unmapped:
                snippet = msg["text"][:50]
                lines.append(f'• "{snippet}" (ts:{msg["ts"]})')
        else:
            lines.append("(none)")
        self.slack.post_message("\n".join(lines))

    def check_brain(self):
        """Check brain health, restart if needed."""
        if self._brain_paused:
            return
        # Check if compaction just completed — post notification
        if self.brain.check_compaction_complete():
            self.slack.post_message(format_brain_compacted())
            logger.info("Brain compacted and resumed successfully")
            return
        if not self.brain.needs_restart():
            logger.debug("Brain healthy (alive, no timeout)")
            return
        if self.brain.circuit_breaker_tripped():
            self._brain_paused = True
            self.slack.post_message(format_brain_circuit_breaker(
                self.brain.restart_count,
                self.brain.max_restarts,
                self.brain.restart_window_seconds,
            ))
            logger.error(
                f"Brain circuit breaker tripped: {self.brain.restart_count} restarts. "
                f"Brain paused until manual intervention. "
                f"restart_timestamps={self.brain._restart_timestamps}"
            )
            return
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        brain_cwd = os.path.expanduser(self.config.get("brain_cwd", "~/.ironclaude/brain"))
        os.makedirs(brain_cwd, exist_ok=True)
        prompt_path = self.config.get("brain_prompt_path") or os.path.join(repo_root, "src", "brain", "system_prompt.md")
        try:
            with open(prompt_path) as f:
                system_prompt = _substitute_prompt(f.read(), self.config)
        except FileNotFoundError:
            logger.error(f"Brain system prompt not found: {prompt_path}")
            return
        success = self.brain.restart(system_prompt, cwd=brain_cwd)
        if success:
            self.slack.post_message(format_brain_restarted(self.brain.restart_count, self.brain.restart_reason))
            logger.info(f"Brain restarted fresh ({self.brain.restart_reason})")
            logger.info(f"Brain new pid={self.brain._brain_pid}")

    def process_brain_decisions(self):
        """Read and act on brain decision files."""
        decisions = read_pending_decisions(self._decisions_dir)
        for decision in decisions:
            action = decision.get("action")
            logger.info(f"Brain decision: {action}")
            self.registry.log_event("brain_decision", details=decision)

            if action == "spawn_worker":
                self._handle_spawn_worker(decision)
            elif action == "approve_plan":
                worker_id = decision.get("worker_id", "")
                self.tmux.send_keys(f"ic-{worker_id}", "yes")
                self.slack.post_message(f"Approved plan for `{worker_id}`.")
            elif action == "reject_plan":
                worker_id = decision.get("worker_id", "")
                reason = decision.get("reason", "No reason given")
                self.tmux.send_keys(f"ic-{worker_id}", f"no: {reason}")
                self.slack.post_message(f"Rejected plan for `{worker_id}`: {reason}")
            elif action == "send_to_worker":
                worker_id = decision.get("worker_id", "")
                message = decision.get("message", "")
                self.tmux.send_keys(f"ic-{worker_id}", message)

    def _wait_for_ready(self, session_name: str, timeout: int = 30, marker: str = "ironclaude v") -> bool:
        """Poll tmux log until the worker is ready or timeout exceeded.

        Returns True if marker is found in output, False on timeout.
        Dismisses trust dialogs by sending Enter if detected.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            output = self.tmux.read_log_tail(session_name, lines=20)
            if output:
                lower = output.lower()
                if "trust this folder" in lower:
                    self.tmux.send_keys(session_name, "")
                if marker in output:
                    return True
            time.sleep(1)
        return False

    def _handle_spawn_worker(self, decision: dict):
        """Spawn a worker from a brain decision."""
        worker_id = decision.get("worker_id", "")
        worker_type = decision.get("type", "claude-sonnet")
        repo = decision.get("repo", "")
        objective = decision.get("objective", "")
        model_name = decision.get("model_name", "")
        session_name = f"ic-{worker_id}"

        # Handle ollama dynamic command construction
        if worker_type == "ollama":
            if not model_name:
                self.slack.post_message(
                    f"Cannot spawn ollama worker `{worker_id}` — model_name is required."
                )
                return

            # Enforce ollama singleton
            existing = self.registry.get_running_workers_by_type("ollama")
            if existing:
                self.slack.post_message(
                    f"Local LLM worker slot occupied by `{existing[0]['id']}`. "
                    f"Wait for completion or use claude-opus/claude-sonnet."
                )
                return

            _hooks_cfg_path = Path.home() / ".claude" / "ironclaude-hooks-config.json"
            try:
                with open(_hooks_cfg_path) as _f:
                    _ollama_url = json.load(_f).get("ollama", {}).get("url", "http://localhost:11434")
            except (FileNotFoundError, json.JSONDecodeError):
                _ollama_url = "http://localhost:11434"
            cmd = f"export CLAUDE_CODE_ATTRIBUTION_HEADER=0; export ANTHROPIC_BASE_URL={shlex.quote(_ollama_url)}; export ANTHROPIC_AUTH_TOKEN=ollama; export ANTHROPIC_API_KEY=; exec claude --model {shlex.quote(model_name)} --dangerously-skip-permissions"
        elif worker_type == "claude-opus":
            cmd = make_opus_command(self.config.get("default_opus_model", DEFAULTS["brain_model"]), self.config.get("effort_level", "high"))
        elif worker_type in WORKER_COMMANDS:
            cmd = WORKER_COMMANDS[worker_type]
        else:
            self.slack.post_message(
                f"Unknown worker type `{worker_type}`. "
                f"Supported: ollama, claude-opus, {', '.join(WORKER_COMMANDS.keys())}"
            )
            return

        # Inject worker ID for stop hook completion detection
        cmd = f"export IC_ROLE=worker; export IC_WORKER_ID={shlex.quote(worker_id)}; export ENABLE_STOP_REVIEW=0; {cmd}"

        # Stage 1: ensure trust
        ensure_worker_trusted(repo)

        # Stage 2: spawn tmux session
        success = self.tmux.spawn_session(session_name, cmd, cwd=repo)
        if not success:
            self.slack.post_message(f"Failed to spawn worker `{worker_id}`.")
            return

        # Stage 3: wait for ready
        self._wait_for_ready(session_name, timeout=30)

        # Stage 4: activate professional mode
        self.tmux.send_keys(session_name, "/activate-professional-mode")
        log_worker_event("WORKER_PM_ACTIVATED", worker_id=worker_id)

        # Stage 5: wait for professional mode
        self._wait_for_ready(session_name, timeout=15, marker="Professional Mode: ON")

        # Stage 5.5: enable advisor if configured
        advisor_cfg = self.config.get("advisor", {})
        if advisor_cfg.get("enabled"):
            advisor_model = advisor_cfg.get("advisor_model", "opus")
            self.tmux.send_keys(session_name, f"/advisor {advisor_model}")
            self._wait_for_ready(session_name, timeout=10, marker="advisor")

        # Stage 6: register and send objective
        self.registry.register_worker(worker_id, worker_type, session_name, repo=repo, description=objective)
        self.tmux.send_keys(session_name, objective)
        log_worker_event("WORKER_OBJECTIVE_DELIVERED", worker_id=worker_id, objective=objective[:200])
        self.slack.post_message(format_worker_spawned(worker_id, worker_type, repo, objective))
        self.registry.log_event("worker_spawned", worker_id=worker_id, details=decision)
        pane_pid = self.tmux.list_pane_pid(session_name)
        log_worker_event("WORKER_SPAWNED", worker_id=worker_id, worker_type=worker_type, repo=repo, pane_pid=pane_pid)

    def _resolve_worker_ssh(self, worker: dict) -> tuple[str | None, str | None]:
        """Resolve SSH host and remote log dir for a worker.

        Returns (ssh_host, remote_log_dir) — both None for local workers.
        """
        machine_name = worker.get("machine")
        if not machine_name or not self._ssh_manager:
            return None, None
        machine_cfg = self._ssh_manager.get_machine(machine_name)
        if not machine_cfg:
            return None, None
        return machine_cfg.host, machine_cfg.log_dir

    def _get_worker_workflow_stage(
        self, session_name: str, _claude_dir: Path | None = None,
        ssh_host: str | None = None,
    ) -> str | None:
        """Read worker's workflow stage from ironclaude.db.

        Follows the pane_pid → session_id file → DB query pattern.
        Returns the workflow_stage string, or None if any step fails.
        For remote workers, uses tmux SSH helpers instead of local file access.
        """
        if ssh_host:
            return self._get_worker_workflow_stage_remote(session_name, ssh_host)

        claude_dir = _claude_dir if _claude_dir is not None else Path("~/.claude").expanduser()

        # Step 1: Get pane PID
        pane_pid = self.tmux.list_pane_pid(session_name)
        if not pane_pid:
            return None

        # Step 2: Read session ID file
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        if not session_id_file.exists():
            return None
        try:
            session_id = session_id_file.read_text().strip()
        except OSError:
            return None
        if len(session_id) != 36:
            return None

        # Step 3: Query DB for workflow_stage
        db_path = claude_dir / "ironclaude.db"
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            row = conn.execute(
                "SELECT workflow_stage FROM sessions WHERE terminal_session = ?",
                (session_id,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _get_worker_workflow_stage_remote(
        self, session_name: str, ssh_host: str,
    ) -> str | None:
        """Read remote worker's workflow stage via SSH."""
        pane_pid = self.tmux.list_pane_pid(session_name, ssh_host=ssh_host)
        if not pane_pid:
            return None
        session_id_file = f"~/.claude/ironclaude-session-{pane_pid}.id"
        content = self.tmux.read_file(session_id_file, ssh_host=ssh_host)
        if not content or len(content.strip()) != 36:
            return None
        session_id = content.strip()
        db_path = "~/.claude/ironclaude.db"
        result = self.tmux.run_sqlite_query(
            db_path,
            f"SELECT workflow_stage FROM sessions WHERE terminal_session='{session_id}';",
            ssh_host=ssh_host,
        )
        return result if result else None

    def _detect_prompt_waiting(self, log_tail: str) -> bool:
        """Detect whether a worker is waiting for user input via LLM grading."""
        cache_key = hash(log_tail)
        cached = self._prompt_waiting_cache.get(cache_key)
        if cached is not None:
            ts, result = cached
            if time.time() - ts < PROMPT_WAITING_CACHE_TTL:
                return result
        result_dict = self._grader.grade(
            _PROMPT_WAITING_SYSTEM,
            f"Worker log tail:\n{log_tail[-2000:]}",
            _PROMPT_WAITING_SCHEMA,
        )
        if result_dict.get("infrastructure_error"):
            logger.debug(
                "Prompt-waiting check unavailable: %s — defaulting to False",
                result_dict.get("error_detail"),
            )
            return False
        waiting = bool(result_dict.get("waiting", False))
        self._prompt_waiting_cache[cache_key] = (time.time(), waiting)
        return waiting

    def _load_staleness_state(self):
        """Load stuck worker state from DB for restart persistence."""
        if self._db is None:
            return
        try:
            rows = self._db.execute(
                "SELECT worker_id, hash_value, stale_since, alert_sent "
                "FROM worker_staleness"
            ).fetchall()
            for row in rows:
                wid = row[0]
                self._stuck_hash[wid] = row[1]
                self._stuck_since[wid] = row[2]
                self._stuck_alert_sent[wid] = bool(row[3])
        except sqlite3.OperationalError:
            pass

    def _persist_staleness_state(self, worker_id: str):
        """Persist single worker's staleness state to DB."""
        if self._db is None:
            return
        if worker_id not in self._stuck_since:
            try:
                self._db.execute(
                    "DELETE FROM worker_staleness WHERE worker_id = ?",
                    (worker_id,),
                )
                self._db.commit()
            except sqlite3.Error as e:
                logger.warning(f"Failed to delete staleness state for {worker_id}: {e}")
            return
        try:
            self._db.execute(
                "INSERT OR REPLACE INTO worker_staleness "
                "(worker_id, hash_value, stale_since, alert_sent, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (
                    worker_id,
                    self._stuck_hash.get(worker_id, 0),
                    self._stuck_since[worker_id],
                    int(self._stuck_alert_sent.get(worker_id, False)),
                ),
            )
            self._db.commit()
        except sqlite3.Error as e:
            logger.warning(f"Failed to persist staleness state for {worker_id}: {e}")

    def _is_oscillating(self, worker_id: str) -> bool:
        """Return True if worker is oscillating between executing/reviewing."""
        now = time.time()
        cutoff = now - OSCILLATION_WINDOW
        history = self._stage_history.get(worker_id, [])
        pruned = [(ts, s) for ts, s in history if ts >= cutoff]
        self._stage_history[worker_id] = pruned
        if len(pruned) < OSCILLATION_THRESHOLD:
            return False
        return all(s in OSCILLATING_STAGES for _, s in pruned)

    def check_stuck_workers(self):
        """Detect workers with unchanged output and escalate/kill."""
        now = time.time()
        if now - self._last_stuck_check < STALENESS_CHECK_INTERVAL:
            return
        self._last_stuck_check = now

        running_ids = set()
        for worker in self.registry.get_running_workers():
            worker_id = worker["id"]
            session_name = worker["tmux_session"]
            running_ids.add(worker_id)
            ssh_host, _ = self._resolve_worker_ssh(worker)

            if not self.tmux.has_session(session_name, ssh_host=ssh_host):
                continue

            try:
                raw = self.tmux.capture_pane(session_name, lines=20, ssh_host=ssh_host)
                log_tail = _strip_ansi(raw)
            except Exception:
                continue

            current_hash = hash(log_tail)

            if current_hash != self._stuck_hash.get(worker_id):
                self._stuck_hash[worker_id] = current_hash
                self._stuck_since[worker_id] = now
                self._stuck_alert_sent[worker_id] = False
                self._stuck_kill_deferred.pop(worker_id, None)
                self._persist_staleness_state(worker_id)
                continue

            if worker_id not in self._stuck_since:
                self._stuck_hash[worker_id] = current_hash
                self._stuck_since[worker_id] = now
                self._stuck_alert_sent[worker_id] = False
                self._persist_staleness_state(worker_id)
                continue

            duration = now - self._stuck_since[worker_id]
            stage = self._get_worker_workflow_stage(session_name, ssh_host=ssh_host)
            prompt_waiting = self._detect_prompt_waiting(log_tail)

            if prompt_waiting:
                alert_threshold = STALENESS_PROMPT_ALERT
                kill_threshold = STALENESS_PROMPT_KILL
            else:
                multiplier = STAGE_STALENESS_MULTIPLIER.get(stage, 1.0)
                alert_threshold = STALENESS_ALERT_SECONDS * multiplier
                kill_threshold = STALENESS_KILL_SECONDS * multiplier

            if duration >= kill_threshold:
                deferred_until = self._stuck_kill_deferred.get(worker_id, 0)
                if now < deferred_until:
                    continue
                self._confirm_and_kill_stuck_worker(
                    worker_id, session_name, duration,
                    stage, prompt_waiting, ssh_host,
                )
            elif duration >= alert_threshold and not self._stuck_alert_sent.get(worker_id, False):
                minutes = int(duration / 60)
                self.brain.send_message(
                    f"[STUCK] Worker {worker_id} output unchanged for {minutes}min. "
                    f"{'Prompt waiting — respond or kill.' if prompt_waiting else 'Check worker status.'}"
                )
                if prompt_waiting:
                    from ironclaude.notifications import format_worker_gate_stuck_slack
                    self.slack.post_message(
                        format_worker_gate_stuck_slack(worker_id, minutes, stage or "unknown")
                    )
                self._stuck_alert_sent[worker_id] = True
                self._persist_staleness_state(worker_id)

        for wid in list(self._stuck_since.keys()):
            if wid not in running_ids:
                del self._stuck_since[wid]
                self._stuck_hash.pop(wid, None)
                self._stuck_alert_sent.pop(wid, None)
                self._stuck_kill_deferred.pop(wid, None)
                self._stuck_liveness_count.pop(wid, None)
                self._persist_staleness_state(wid)

    def _confirm_and_kill_stuck_worker(
        self, worker_id: str, session_name: str, duration: float,
        stage: str | None, prompt_waiting: bool, ssh_host: str | None,
    ):
        """Liveness confirmation gate + kill action for stuck worker."""
        if ssh_host is None:
            pane_pid = self.tmux.list_pane_pid(session_name)
            if pane_pid:
                try:
                    parent = psutil.Process(int(pane_pid))
                    children = parent.children(recursive=True)
                    if children:
                        for child in children:
                            try:
                                child.cpu_percent()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        time.sleep(2)
                        for child in children:
                            try:
                                if child.cpu_percent() > 1.0:
                                    deferral_count = self._stuck_liveness_count.get(worker_id, 0) + 1
                                    self._stuck_liveness_count[worker_id] = deferral_count
                                    if prompt_waiting and deferral_count > MAX_LIVENESS_DEFERRALS:
                                        logger.info(
                                            f"Worker {worker_id} liveness deferred {deferral_count} times "
                                            f"but prompt_waiting=True — proceeding with kill"
                                        )
                                        break
                                    self._stuck_kill_deferred[worker_id] = (
                                        time.time() + STALENESS_LIVENESS_EXTENSION
                                    )
                                    logger.info(
                                        f"Worker {worker_id} liveness check passed "
                                        f"(CPU active, deferral {deferral_count}), deferring kill by "
                                        f"{STALENESS_LIVENESS_EXTENSION}s"
                                    )
                                    return
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError) as e:
                    logger.warning(f"Liveness check failed for {worker_id}: {e}")

        self.tmux.kill_session(session_name, ssh_host=ssh_host)
        self.registry.update_worker_status(worker_id, "completed")
        self.registry.log_event("worker_finished", worker_id=worker_id)

        minutes = int(duration / 60)
        log_worker_event(
            "WORKER_STUCK_KILLED", worker_id=worker_id,
            duration_minutes=minutes, stage=stage or "unknown",
            prompt_waiting=prompt_waiting,
        )

        from ironclaude.notifications import format_worker_stuck_killed
        self.slack.post_message(format_worker_stuck_killed(
            worker_id, minutes, stage or "unknown", prompt_waiting,
        ))

        directive_list = ""
        if self._db is not None:
            try:
                rows = self._db.execute(
                    "SELECT id, interpretation FROM directives "
                    "WHERE status IN ('confirmed', 'in_progress')"
                ).fetchall()
                if rows:
                    directive_list = " Remaining: " + ", ".join(
                        f"#{r[0]}: {r[1][:60]}" for r in rows
                    )
            except Exception:
                pass

        self.brain.send_message(
            f"[MANDATORY SWEEP] Worker {worker_id} killed (stuck {minutes}min, "
            f"stage={stage or 'unknown'}, "
            f"prompt={'yes' if prompt_waiting else 'no'})."
            f"{directive_list} Spawn replacement if needed."
        )

        self._stuck_since.pop(worker_id, None)
        self._stuck_hash.pop(worker_id, None)
        self._stuck_alert_sent.pop(worker_id, None)
        self._stuck_kill_deferred.pop(worker_id, None)
        self._stuck_liveness_count.pop(worker_id, None)
        self._persist_staleness_state(worker_id)

    def check_workers(self):
        """Check running workers for completion signals."""
        for worker in self.registry.get_running_workers():
            worker_id = worker["id"]
            session_name = worker["tmux_session"]
            ssh_host, remote_log_dir = self._resolve_worker_ssh(worker)

            # Primary: check for .done marker from stop hook
            if ssh_host:
                log_dir = remote_log_dir or self.tmux.log_dir
                marker_path = os.path.join(log_dir, f"{session_name}.done")
                marker_exists = self.tmux.file_exists(marker_path, ssh_host=ssh_host)
            else:
                marker_path = os.path.join(self.tmux.log_dir, f"{session_name}.done")
                marker_exists = os.path.exists(marker_path)

            if marker_exists:
                log_worker_event("WORKER_IDLE", worker_id=worker_id)
                self.slack.post_message(format_worker_idle(worker_id))
                delivered = self.brain.send_message(
                    f"Worker {worker_id} idle."
                )
                if delivered:
                    if ssh_host:
                        self.tmux.remove_file(marker_path, ssh_host=ssh_host)
                    else:
                        try:
                            os.remove(marker_path)
                        except FileNotFoundError:
                            pass
                    logger.info(f"Idle notification delivered for {worker_id}, marker removed")
                else:
                    logger.warning(f"Brain unreachable, keeping marker for {worker_id} (will retry)")
                continue

            # Fallback: session died (crash, OOM, etc.)
            if not self.tmux.has_session(session_name, ssh_host=ssh_host):
                self.registry.update_worker_status(worker_id, "completed")
                self.registry.log_event("worker_finished", worker_id=worker_id)
                log_worker_event("WORKER_DEAD", worker_id=worker_id)
                self.slack.post_message(format_worker_completed(worker_id, "Session ended"))
                self.brain.send_message(
                    f"Worker {worker_id} session died (tmux gone)."
                )
                continue

            # Proactive check-in: notify brain when cadence elapses
            claude_dir = self._claude_dir if self._claude_dir is not None else Path("~/.claude").expanduser()
            stage = self._get_worker_workflow_stage(session_name, _claude_dir=claude_dir, ssh_host=ssh_host)

            cadence = CHECKIN_CADENCE.get(stage, DEFAULT_CADENCE) if stage else DEFAULT_CADENCE

            # Check brain contact file (written by MCP server)
            contact_path = os.path.join(self.tmux.log_dir, f"{session_name}.brain_contact")
            last_contact = 0.0
            if os.path.exists(contact_path):
                try:
                    last_contact = float(open(contact_path).read().strip())
                except (ValueError, OSError):
                    pass

            # Dedup gate: suppress if brain hasn't acked and heartbeat window hasn't elapsed
            heartbeat_interval = self.config.get("heartbeat_interval_seconds", 900)
            last_sent = self._last_checkin_sent.get(worker_id, 0.0)
            last_stage_sent = self._last_checkin_stage.get(worker_id)
            stage_changed = (last_stage_sent is not None and stage != last_stage_sent)

            # Track transitions independently of notification sends
            last_seen = self._last_stage_seen.get(worker_id)
            if stage != last_seen:
                self._stage_history.setdefault(worker_id, []).append((time.time(), stage))
                self._last_stage_seen[worker_id] = stage
                self._pm_gate_slack_sent.pop(worker_id, None)
                self._stage_entered_at[worker_id] = time.time()
            if self._is_oscillating(worker_id):
                stage_changed = False
                cadence = OSCILLATION_CADENCE

            if not stage_changed:
                if last_sent > 0:
                    brain_acknowledged = last_contact > last_sent
                    heartbeat_elapsed = (time.time() - last_sent) >= heartbeat_interval
                    if not brain_acknowledged and not heartbeat_elapsed:
                        continue
                # Cadence check (applies on first send and after ack)
                most_recent = max(last_contact, last_sent)
                if time.time() - most_recent < cadence:
                    continue
            # stage_changed=True → bypass both gates, fire immediately

            # Cadence expired — send proactive notification
            try:
                log_tail = self.tmux.capture_pane(session_name, lines=5, ssh_host=ssh_host)
            except Exception:
                log_tail = "(could not capture output)"

            prompt_waiting = self._detect_prompt_waiting(log_tail)

            current_hash = hash(log_tail)
            if not stage_changed and current_hash == self._last_checkin_hash.get(worker_id):
                if not prompt_waiting:
                    continue

            spawned_at = worker.get("spawned_at", "")
            try:
                from datetime import datetime
                spawn_time = datetime.fromisoformat(spawned_at)
                elapsed = int((datetime.utcnow() - spawn_time).total_seconds() / 60)
            except (ValueError, TypeError):
                elapsed = 0

            brain_message = format_worker_checkin(worker_id, elapsed, stage or "unknown", log_tail, prompt_waiting)
            self.brain.send_message(brain_message)
            self._last_checkin_sent[worker_id] = time.time()
            self._last_checkin_stage[worker_id] = stage
            self._last_checkin_hash[worker_id] = current_hash

            if prompt_waiting and stage in PM_GATE_STAGES:
                entered_at = self._stage_entered_at.get(worker_id)
                if entered_at:
                    time_at_stage = time.time() - entered_at
                    if time_at_stage >= PM_GATE_SLACK_SECONDS and not self._pm_gate_slack_sent.get(worker_id):
                        from ironclaude.notifications import format_worker_gate_stuck_slack
                        self.slack.post_message(
                            format_worker_gate_stuck_slack(worker_id, int(time_at_stage / 60), stage)
                        )
                        self._pm_gate_slack_sent[worker_id] = True

    def check_confirmed_directives(self):
        """Send reminder to Brain for confirmed directives with no worker spawned within 5 minutes.

        Runs every poll cycle. Queries all confirmed directives. For each with no matching
        running worker, fires immediately if never notified (_directive_reminder_sent absent),
        or fires an ACTION REQUIRED reminder if 10+ minutes have elapsed since last notification.
        """
        if self._db is None:
            return
        REMINDER_INTERVAL = 600  # 10 minutes between reminders per directive
        now = time.time()
        try:
            rows = self._db.execute(
                "SELECT id, interpretation FROM directives WHERE status='confirmed'"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        running_workers = self.registry.get_running_workers()
        for directive_id, interpretation in rows:
            worker_found = any(_worker_matches_directive(w, directive_id) for w in running_workers)
            if worker_found:
                continue
            last_sent = self._directive_reminder_sent.get(directive_id, 0.0)
            if last_sent == 0.0:
                msg = (
                    f"Directive #{directive_id} confirmed — no worker spawned yet. Act on it now."
                )
            elif now - last_sent < REMINDER_INTERVAL:
                continue
            else:
                msg = (
                    f"[ACTION REQUIRED] Directive #{directive_id} confirmed but no worker spawned. "
                    f"Act on it now."
                )
            delivered = self.brain.send_message(msg)
            if delivered:
                self._directive_reminder_sent[directive_id] = now

    def check_idle_enforcement(self):
        """Escalate when Brain is idle with pending work.

        Throttled to 60s. Three tiers: INFO (0-60s), WARNING (60-360s),
        CRITICAL (360s+) with operator notification.
        """
        now = time.time()
        if now - self._last_idle_check < 60:
            return
        self._last_idle_check = now

        alive_workers = []
        for w in self.registry.get_recent_workers():
            ssh_host, _ = self._resolve_worker_ssh(w)
            if self.tmux.has_session(w["tmux_session"], ssh_host=ssh_host):
                alive_workers.append(w)

        if alive_workers:
            self._idle_enforcement_start = 0.0
            self._idle_escalation_tier = 0
            self._operator_notified_idle = False
            return

        unworked_directives = []
        if self._db is not None:
            try:
                rows = self._db.execute(
                    "SELECT id, status, interpretation FROM directives "
                    "WHERE status IN ('confirmed', 'in_progress')"
                ).fetchall()
                unworked_directives = rows
            except Exception:
                pass

        pending_tasks = []
        try:
            ledger = read_task_ledger(self._ledger_path)
            if ledger and isinstance(ledger, dict):
                pending_tasks = [
                    t for t in ledger.get("tasks", [])
                    if isinstance(t, dict) and t.get("status") in ("pending", "in_progress")
                ]
        except Exception:
            pass

        unprocessed_msgs = self._get_unprocessed_messages()

        if not unworked_directives and not pending_tasks and not unprocessed_msgs:
            self._idle_enforcement_start = 0.0
            self._idle_escalation_tier = 0
            self._operator_notified_idle = False
            return

        if self._idle_enforcement_start == 0.0:
            self._idle_enforcement_start = now

        idle_duration = now - self._idle_enforcement_start

        if idle_duration < 60:
            if self._idle_escalation_tier < 1:
                self._idle_escalation_tier = 1
                self.brain.send_message(
                    f"You have {len(unworked_directives)} unworked directive(s), "
                    f"{len(pending_tasks)} pending ledger task(s), and "
                    f"{len(unprocessed_msgs)} unprocessed operator message(s) "
                    f"with no active workers. Spawn workers now."
                )
        elif idle_duration < 360:
            if self._idle_escalation_tier < 2:
                self._idle_escalation_tier = 2
                directive_list = ", ".join(
                    f"#{r[0]} ({r[1]}): {r[2][:60]}" for r in unworked_directives
                )
                task_list = ", ".join(
                    t.get("description", "unknown")[:40] for t in pending_tasks[:5]
                )
                parts = [f"[WARNING] Idle for {int(idle_duration)}s with pending work."]
                if directive_list:
                    parts.append(f"Directives: {directive_list}.")
                if task_list:
                    parts.append(f"Ledger tasks: {task_list}.")
                if unprocessed_msgs:
                    parts.append(f"{len(unprocessed_msgs)} unprocessed message(s).")
                parts.append("Act immediately.")
                self.brain.send_message(" ".join(parts))
        else:
            if self._idle_escalation_tier < 3:
                self._idle_escalation_tier = 3
                self.brain.send_message(
                    f"[CRITICAL] Idle for {int(idle_duration)}s with pending work. "
                    f"This is a hard enforcement escalation. Spawn workers NOW."
                )
            if not self._operator_notified_idle:
                self._operator_notified_idle = True
                total = len(unworked_directives) + len(pending_tasks) + len(unprocessed_msgs)
                self.slack.post_message(
                    f"[ALERT] Brain idle for {int(idle_duration // 60)} minutes "
                    f"with {total} pending item(s). Manual intervention may be needed."
                )

    def _handle_detail(self, parsed: dict):
        """Handle /detail command — show worker status and log."""
        worker_id = parsed.get("target", "")
        w = self.registry.get_worker(worker_id)
        if not w:
            self.slack.post_message(f"Worker `{worker_id}` not found.")
            return
        try:
            log_tail = self.tmux.capture_pane(w["tmux_session"], lines=20)
        except subprocess.CalledProcessError:
            log_tail = self.tmux.read_log_tail(w["tmux_session"], lines=20)
        self.slack.post_message(f"*Worker {worker_id}:* status={w['status']}\n```{log_tail}```")

    def _handle_log(self, parsed: dict):
        """Handle /log command — show worker log."""
        worker_id = parsed.get("target", "")
        lines = parsed.get("lines", 20)
        w = self.registry.get_worker(worker_id)
        if not w:
            self.slack.post_message(f"Worker `{worker_id}` not found.")
            return
        try:
            log_tail = self.tmux.capture_pane(w["tmux_session"], lines=lines)
        except subprocess.CalledProcessError:
            log_tail = self.tmux.read_log_tail(w["tmux_session"], lines=lines)
        self.slack.post_message(f"```{log_tail}```")

    def post_heartbeat(self):
        """Post heartbeat to Slack at configured interval."""
        heartbeat_interval = self.config.get("heartbeat_interval_seconds", 900)
        now = time.time()
        if now - self._last_heartbeat < heartbeat_interval:
            return
        self._last_heartbeat = now

        candidates = self.registry.get_recent_workers()
        worker_details = []
        for w in candidates:
            ssh_host, _ = self._resolve_worker_ssh(w)
            if not self.tmux.has_session(w["tmux_session"], ssh_host=ssh_host):
                continue
            stage = self._get_worker_workflow_stage(w["tmux_session"], ssh_host=ssh_host)
            worker_details.append({
                "id": w["id"],
                "description": w.get("description"),
                "workflow_stage": stage,
            })

        brain_usage = self.brain.get_token_usage() if self.brain is not None else None
        self.slack.post_message(format_heartbeat(worker_details, brain_usage=brain_usage))

        # Grader enforcement: if no alive workers but directives exist, nudge the Brain
        if not worker_details and self._db is not None:
            try:
                unworked = self._db.execute(
                    "SELECT count(*) FROM directives WHERE status IN ('confirmed', 'in_progress')"
                ).fetchone()[0]
                if unworked > 0:
                    self.brain.send_message(
                        f"GRADER CHECK: You have {unworked} confirmed/in_progress directive(s) "
                        f"with no active workers. Follow the Attention Sweep Protocol — "
                        f"spawn workers for unblocked directives immediately."
                    )
                    self.slack.post_message(
                        f"*Idle with {unworked} unworked directive(s)* — nudge sent to Brain."
                    )
            except Exception as e:
                logger.warning(f"Heartbeat directive check failed: {e}")

    def run(self):
        """Main daemon loop."""
        poll_interval = self.config.get("poll_interval_seconds", 15)
        while self._running:
            self.poll_slack_commands()
            self.poll_brain_responses()
            self._sweep_expired_push_requests()
            if not self._paused:
                self.check_brain()
                self.process_brain_decisions()
                self.check_workers()
                self.check_confirmed_directives()
                self.check_idle_enforcement()
                self.check_post_kill_sweep()
                self.check_message_aging()
            self.post_heartbeat()
            self._run_maintenance()
            self.slack.flush_queue()
            time.sleep(poll_interval)


def main():
    _log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    os.makedirs("/tmp/ic", exist_ok=True)
    _file_handler = RotatingFileHandler(
        "/tmp/ic/daemon.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    _file_handler.setFormatter(logging.Formatter(_log_format))
    _stderr_handler = logging.StreamHandler()
    _stderr_handler.setFormatter(logging.Formatter(_log_format))
    _root_logger = logging.getLogger()
    _root_logger.setLevel(logging.INFO)
    _root_logger.addHandler(_file_handler)
    _root_logger.addHandler(_stderr_handler)

    no_respawn = '--no-respawn' in sys.argv

    # Isolate daemon from pipeline process group (e.g. make run | tee)
    # so SIGTERM to the pipeline group doesn't kill the brain subprocess
    try:
        os.setpgid(0, 0)
    except PermissionError:
        logger.warning("Could not create new process group (setpgid failed)")

    _acquire_singleton_lock()
    logger.info(
        "Daemon started — pid=%d executable=%s cwd=%s utc=%s",
        os.getpid(),
        sys.executable,
        os.getcwd(),
        datetime.now(timezone.utc).isoformat(),
    )
    _load_dotenv()

    config = load_config()

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    brain_cwd = os.path.expanduser(config.get("brain_cwd", "~/.ironclaude/brain"))
    os.makedirs(brain_cwd, exist_ok=True)

    wiki_dir = os.path.join(brain_cwd, "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    for wiki_file, wiki_content in [
        ("index.md", "# Wiki Index\n\n*No wiki pages yet.*\n"),
        ("log.md", "# Wiki Log\n\n"),
    ]:
        wiki_path = os.path.join(wiki_dir, wiki_file)
        if not os.path.exists(wiki_path):
            with open(wiki_path, "w") as f:
                f.write(wiki_content)
            logger.info(f"Initialized wiki file: {wiki_path}")

    # Sync orchestrator CLAUDE.md from source control to brain home (with template substitution)
    orchestrator_claude_md_src = os.path.join(repo_root, "src", "brain", "orchestrator_claude.md")
    orchestrator_claude_md_dst = os.path.join(brain_cwd, "CLAUDE.md")
    try:
        with open(orchestrator_claude_md_src) as f:
            content = _substitute_prompt(f.read(), config)
        with open(orchestrator_claude_md_dst, "w") as f:
            f.write(content)
        logger.info(f"Synced orchestrator CLAUDE.md to {orchestrator_claude_md_dst}")
    except FileNotFoundError:
        logger.error(f"Orchestrator CLAUDE.md not found at {orchestrator_claude_md_src}")
        sys.exit(1)

    # Sync orchestrator rules files to brain_cwd/.claude/rules/
    rules_src_dir = os.path.join(repo_root, "src", "brain", "rules")
    rules_dst_dir = os.path.join(brain_cwd, ".claude", "rules")
    if os.path.isdir(rules_src_dir):
        os.makedirs(rules_dst_dir, exist_ok=True)
        for rules_file in sorted(os.listdir(rules_src_dir)):
            if not rules_file.endswith(".md"):
                continue
            src_path = os.path.join(rules_src_dir, rules_file)
            dst_path = os.path.join(rules_dst_dir, rules_file)
            try:
                with open(src_path) as f:
                    content = _substitute_prompt(f.read(), config)
                with open(dst_path, "w") as f:
                    f.write(content)
                logger.info(f"Synced rules file {rules_file} to {dst_path}")
            except FileNotFoundError:
                logger.error(f"Rules file not found at {src_path}")
                sys.exit(1)

    # Sync grader CLAUDE.md from source control to grader home (with template substitution)
    grader_home = os.path.expanduser("~/.ironclaude/grader")
    os.makedirs(grader_home, exist_ok=True)
    grader_claude_md_src = os.path.join(repo_root, "src", "brain", "grader_claude.md")
    grader_claude_md_dst = os.path.join(grader_home, "CLAUDE.md")
    try:
        with open(grader_claude_md_src) as f:
            content = _substitute_prompt(f.read(), config)
        with open(grader_claude_md_dst, "w") as f:
            f.write(content)
        logger.info(f"Synced grader CLAUDE.md to {grader_claude_md_dst}")
    except FileNotFoundError:
        logger.error(f"Grader CLAUDE.md not found at {grader_claude_md_src}")
        sys.exit(1)

    ensure_brain_trusted(grader_home)

    slack_token = config.get("slack_bot_token", "")
    channel_id = config.get("slack_channel_id", "")
    if not slack_token or not channel_id:
        logger.error("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are required.")
        sys.exit(1)

    user_token = config.get("slack_user_token", "")
    operator_user_id = config.get("slack_operator_user_id", "")
    slack = SlackBot(token=slack_token, channel_id=channel_id, user_token=user_token, operator_user_id=operator_user_id)

    # Plugin system
    plugin_registry = PluginRegistry()
    loaded_plugins = discover_plugins(plugin_registry)
    if loaded_plugins:
        logger.info("Loaded plugins: %s", ", ".join(loaded_plugins))
    from ironclaude.slack_interface import SLASH_COMMANDS
    SLASH_COMMANDS.update(plugin_registry.get_slash_commands())

    socket_handler = None
    app_token = config.get("slack_app_token", "")
    if app_token:
        socket_handler = SlackSocketHandler(app_token=app_token, bot_token=slack_token, operator_user_id=operator_user_id, registry=plugin_registry)
        socket_handler.start()
        logger.info("Slack Socket Mode enabled")
    else:
        logger.warning("SLACK_APP_TOKEN not set — slash commands disabled")

    conn = init_db(config.get("db_path", "data/db/ic.db"))

    # SSH remote machines
    from ironclaude.ssh_manager import SSHConnectionManager
    ssh_manager = None
    machines = load_machines_config()
    if machines:
        ssh_manager = SSHConnectionManager()
        ssh_manager.register_machines(machines)
        for name in ssh_manager.list_machine_names():
            health = ssh_manager.health_check(name)
            logger.info(f"Remote machine '{name}': {'healthy' if health.ok else health.details}")

    tmux = TmuxManager(log_dir=config.get("log_dir", "/tmp/ic-logs"), ssh_manager=ssh_manager)
    registry = WorkerRegistry(conn)
    _kill_orphan_workers(tmux, registry)
    brain = BrainClient(
        timeout_seconds=config.get("brain_timeout_seconds", 600),
        operator_name=config.get("operator_name", "Operator"),
        model=config.get("brain_model", "opus"),
        effort_level=config.get("effort_level", "high"),
    )

    # Start brain
    prompt_path = config.get("brain_prompt_path") or os.path.join(repo_root, "src", "brain", "system_prompt.md")
    try:
        with open(prompt_path) as f:
            system_prompt = _substitute_prompt(f.read(), config)
        ensure_brain_trusted(brain_cwd)
        brain.start(system_prompt, cwd=brain_cwd)
        logger.info("Brain SDK client started")
    except FileNotFoundError:
        logger.warning(f"Brain system prompt not found: {prompt_path} — brain will start on first check_brain()")

    daemon = IroncladeDaemon(
        config=config, slack=slack, socket_handler=socket_handler,
        registry=registry, tmux_manager=tmux, brain=brain,
        db_conn=conn, plugin_registry=plugin_registry,
        ssh_manager=ssh_manager,
    )
    plugin_registry.run_lifecycle("init", daemon)

    global _daemon
    _daemon = daemon

    _install_sigaction_handler()
    signal.signal(signal.SIGHUP, _handle_restart)

    logger.info("IronClaude Commander daemon starting.")
    slack.post_message("IronClaude Commander daemon started.")

    try:
        daemon.run()
    finally:
        try:
            Path(_PID_FILE).unlink(missing_ok=True)
        except OSError:
            pass
    brain.shutdown()
    conn.close()
    if ssh_manager:
        ssh_manager.teardown_all()
    if socket_handler:
        socket_handler.stop()
    slack.post_message("IronClaude Commander daemon stopped.")
    logger.info("IronClaude Commander daemon stopped.")

    if not _clean_shutdown and not no_respawn:
        _spawn_respawner()


if __name__ == "__main__":
    main()
