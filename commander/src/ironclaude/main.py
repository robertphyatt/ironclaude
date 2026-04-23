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
from datetime import datetime, timezone
from pathlib import Path

from ironclaude.config import load_config, DEFAULTS, make_opus_command
from ironclaude.slack_interface import SlackBot, DIRECTIVE_STATUS_EMOJI
from ironclaude.slack_commands import SlackSocketHandler, format_help_text
from ironclaude.db import init_db
from ironclaude.tmux_manager import TmuxManager
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
from ironclaude.orchestrator_mcp import ensure_worker_trusted
from ironclaude.signal_forensics import _logged_kill
from ironclaude.plugins import PluginRegistry, discover_plugins

logger = logging.getLogger("ironclaude")


def log_worker_event(event_type: str, **fields) -> None:
    payload = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    logger.info(json.dumps(payload))


WORKER_COMMANDS = {
    "claude-sonnet": "export CLAUDE_CODE_EFFORT_LEVEL=high; exec claude --model 'sonnet' --dangerously-skip-permissions",
}

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
    "execution_complete": 120,
}
DEFAULT_CADENCE = 300

PROMPT_PATTERNS = [
    "AskUserQuestion",
    "Submit answers",
    "options:",
    "Which approach",
    "How would you like",
    "question:",
    re.compile(r"^\s*[1-4]\.", re.MULTILINE),
]

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
                sys.exit(1)
            except PermissionError:
                os.close(fd)
                logger.error(f"Another ironclaude daemon is already running (PID {existing}). Exiting.")
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


class IroncladeDaemon:
    def __init__(self, config: dict, slack: SlackBot, socket_handler: SlackSocketHandler | None,
                 registry: WorkerRegistry, tmux_manager: TmuxManager, brain: BrainClient,
                 db_conn=None, plugin_registry=None):
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
        self._claude_dir: Path | None = None
        self._last_maintenance = 0.0
        self._state_manager_db_path = os.path.expanduser("~/.claude/ironclaude.db")

    def shutdown(self):
        self._running = False

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
                self._handle_directive_reaction(item["emoji"], item["message_ts"])
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
            "SELECT id, interpretation FROM directives WHERE status='pending_confirmation' "
            "ORDER BY created_at DESC LIMIT 1"
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
        if emoji in ("thumbsup", "+1", "thumbs_up"):
            self._db.execute(
                "UPDATE directives SET status='confirmed', updated_at=datetime('now') WHERE id=?",
                (directive_id,),
            )
            self._db.commit()
            self.slack.post_message(f"Directive #{directive_id} confirmed: {interpretation}")
            operator = self.config.get("operator_name", "Operator")
            self.brain.send_message(f"Directive #{directive_id} confirmed by {operator}: {interpretation}")
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
        new_status = "confirmed" if emoji in ("thumbsup", "+1", "thumbs_up") else "rejected"
        logger.info("Directive #%d %s via reaction %r", directive_id, new_status, emoji)
        return True

    def poll_brain_responses(self):
        """Drain brain responses and post to Slack."""
        for text in self.brain.get_pending_responses():
            logger.info(f"Brain response: {text[:100]}...")
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

            cmd = f"ollama launch claude --model {shlex.quote(model_name)} -- --dangerously-skip-permissions"
        elif worker_type == "claude-opus":
            cmd = make_opus_command(self.config.get("default_opus_model", DEFAULTS["brain_model"]))
        elif worker_type in WORKER_COMMANDS:
            cmd = WORKER_COMMANDS[worker_type]
        else:
            self.slack.post_message(
                f"Unknown worker type `{worker_type}`. "
                f"Supported: ollama, claude-opus, {', '.join(WORKER_COMMANDS.keys())}"
            )
            return

        # Inject worker ID for stop hook completion detection
        cmd = f"IC_WORKER_ID={shlex.quote(worker_id)} {cmd}"

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

    def _get_worker_workflow_stage(
        self, session_name: str, _claude_dir: Path | None = None
    ) -> str | None:
        """Read worker's workflow stage from ironclaude.db.

        Follows the pane_pid → session_id file → DB query pattern.
        Returns the workflow_stage string, or None if any step fails.
        """
        claude_dir = _claude_dir if _claude_dir is not None else Path("~/.claude").expanduser()

        # Step 1: Get pane PID
        pane_pid = self.tmux.list_pane_pid(session_name)
        if not pane_pid:
            return None

        # Step 2: Read session ID file
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        if not session_id_file.exists():
            return None
        session_id = session_id_file.read_text().strip()
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

    def _detect_prompt_waiting(self, log_tail: str) -> bool:
        """Heuristic: scan log tail for patterns indicating worker awaits input."""
        for pattern in PROMPT_PATTERNS:
            if isinstance(pattern, re.Pattern):
                if pattern.search(log_tail):
                    return True
            elif pattern in log_tail:
                return True
        return False

    def check_workers(self):
        """Check running workers for completion signals."""
        for worker in self.registry.get_running_workers():
            worker_id = worker["id"]
            session_name = worker["tmux_session"]

            # Primary: check for .done marker from stop hook
            marker_path = os.path.join(self.tmux.log_dir, f"{session_name}.done")
            if os.path.exists(marker_path):
                # Do NOT kill session or update registry — brain controls lifecycle
                log_worker_event("WORKER_IDLE", worker_id=worker_id)
                self.slack.post_message(format_worker_idle(worker_id))
                delivered = self.brain.send_message(
                    f"Worker {worker_id} idle."
                )
                if delivered:
                    try:
                        os.remove(marker_path)
                    except FileNotFoundError:
                        pass  # Already cleaned up
                    logger.info(f"Idle notification delivered for {worker_id}, marker removed")
                else:
                    logger.warning(f"Brain unreachable, keeping marker for {worker_id} (will retry)")
                continue

            # Fallback: session died (crash, OOM, etc.)
            if not self.tmux.has_session(session_name):
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
            stage = self._get_worker_workflow_stage(session_name, _claude_dir=claude_dir)

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
                log_tail = self.tmux.capture_pane(session_name, lines=5)
            except Exception:
                log_tail = "(could not capture output)"

            prompt_waiting = self._detect_prompt_waiting(log_tail)

            spawned_at = worker.get("spawned_at", "")
            try:
                from datetime import datetime
                spawn_time = datetime.fromisoformat(spawned_at)
                elapsed = int((datetime.utcnow() - spawn_time).total_seconds() / 60)
            except (ValueError, TypeError):
                elapsed = 0

            message = format_worker_checkin(worker_id, elapsed, stage or "unknown", log_tail, prompt_waiting)
            self.brain.send_message(message)
            self._last_checkin_sent[worker_id] = time.time()
            self._last_checkin_stage[worker_id] = stage

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

        running = self.registry.get_running_workers()
        worker_details = []
        for w in running:
            stage = self._get_worker_workflow_stage(w["tmux_session"])
            worker_details.append({
                "id": w["id"],
                "description": w.get("description"),
                "workflow_stage": stage,
            })

        self.slack.post_message(format_heartbeat(worker_details))

        # Grader enforcement: if no workers but directives exist, nudge the Brain
        if not running and self._db is not None:
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
            if not self._paused:
                self.check_brain()
                self.process_brain_decisions()
                self.check_workers()
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
    tmux = TmuxManager(log_dir=config.get("log_dir", "/tmp/ic-logs"))
    registry = WorkerRegistry(conn)
    brain = BrainClient(
        timeout_seconds=config.get("brain_timeout_seconds", 600),
        operator_name=config.get("operator_name", "Operator"),
        model=config.get("brain_model", "claude-opus-4-5-20251101"),
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
    if socket_handler:
        socket_handler.stop()
    slack.post_message("IronClaude Commander daemon stopped.")
    logger.info("IronClaude Commander daemon stopped.")

    if not _clean_shutdown and not no_respawn:
        _spawn_respawner()


if __name__ == "__main__":
    main()
