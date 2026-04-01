# src/ic/orchestrator_mcp.py
"""MCP server for brain-daemon orchestration.

Replaces the file-based decision protocol with structured MCP tools.
Shares the daemon's SQLite database (WAL mode) for state coordination.

The OrchestratorTools class implements business logic separately from
the MCP transport layer. Tests call OrchestratorTools methods directly;
the FastMCP server wraps them for the brain's Claude Agent SDK session.
"""

from __future__ import annotations

import collections
import fcntl
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import shlex

from ironclaude.tmux_manager import _strip_ansi

logger = logging.getLogger("ironclaude.orchestrator_mcp")


def log_worker_event(event_type: str, **fields) -> None:
    payload = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **fields}
    logger.info(json.dumps(payload))


WORKER_COMMANDS = {
    "claude-opus": "export CLAUDE_CODE_EFFORT_LEVEL=high; exec claude --model 'opus' --dangerously-skip-permissions",
    "claude-sonnet": "export CLAUDE_CODE_EFFORT_LEVEL=high; exec claude --model 'sonnet' --dangerously-skip-permissions",
}

VALID_DIRECTIVE_STATUSES = frozenset({
    "pending_confirmation", "confirmed", "rejected", "in_progress", "completed",
})

VALID_SUPABASE_TABLES = frozenset({"players", "sessions", "events", "feedback", "errors"})
VALID_ORDER_BY_COLUMNS = frozenset({"id", "created_at", "updated_at", "severity"})
RESERVED_SUPABASE_PARAMS = frozenset({"select", "limit", "order", "offset", "count", "and", "or", "not"})
_SAFE_COLUMN_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*$')

KEY_MAP: dict[str, str] = {
    "Return": "return",
    "space": "space",
    "Tab": "tab",
    "Escape": "escape",
    "BackSpace": "delete",
    "Up": "arrow-up",
    "Down": "arrow-down",
    "Left": "arrow-left",
    "Right": "arrow-right",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Control_L": "ctrl",
    "Control_R": "ctrl",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "Meta_L": "cmd",
    "Meta_R": "cmd",
}


def _load_avatar_skill() -> str:
    """Load the avatar decision skill from src/brain/avatar_skill.md."""
    skill_path = Path(__file__).parents[1] / "brain" / "avatar_skill.md"
    return skill_path.read_text()


def ensure_worker_trusted(repo: str) -> None:
    """Ensure a worker's repo directory is trusted in ~/.claude.json.

    Reads ~/.claude.json, checks if repo has hasTrustDialogAccepted=true,
    adds a minimal project entry if missing, and writes back with file locking.
    Requires .git directory (workers are git repos).
    Defensive: logs warnings and continues on any failure.
    """
    claude_json_path = os.path.expanduser("~/.claude.json")
    abs_cwd = os.path.abspath(repo)
    real_cwd = os.path.realpath(abs_cwd)

    if not os.path.exists(os.path.join(real_cwd, ".git")):
        logger.warning(f"Refusing to trust {real_cwd!r}: no .git directory found")
        return

    try:
        with open(claude_json_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse {claude_json_path}")
                return

            projects = data.get("projects", {})
            project = projects.get(real_cwd, {})

            if project.get("hasTrustDialogAccepted") is True:
                logger.info(f"Worker directory already trusted: {real_cwd}")
                return

            project["hasTrustDialogAccepted"] = True
            project.setdefault("allowedTools", [])
            projects[real_cwd] = project
            data["projects"] = projects

            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
            logger.info(f"Added trust entry for worker directory: {real_cwd}")
    except FileNotFoundError:
        logger.warning(f"{claude_json_path} not found — cannot pre-trust worker directory")
    except OSError as e:
        logger.warning(f"Could not update {claude_json_path}: {e}")


class OrchestratorTools:
    """Business logic for orchestrator MCP tools.

    Holds references to the worker registry, tmux manager, and ledger path.
    All methods are synchronous and raise exceptions on error.
    """

    def __init__(self, registry, tmux, ledger_path: str, grader_home: str = "~/.ironclaude/grader", slack_bot=None, db_conn=None, operator_name: str = "Operator", supabase_url: str = "", supabase_anon_key: str = ""):
        self.registry = registry
        self.tmux = tmux
        self.ledger_path = ledger_path
        self._grader_session = "ic-grader"
        self._grader_ready = False
        self._grader_home = os.path.expanduser(grader_home)
        self._slack = slack_bot
        self._db = db_conn
        self._operator_name = operator_name
        self._supabase_url = supabase_url
        self._supabase_anon_key = supabase_anon_key
        self._failed_worker_bases: set[str] = set()
        self._game_pid: int | None = None

    def _write_brain_contact(self, worker_id: str) -> None:
        """Write brain contact timestamp for daemon to read."""
        worker = self.registry.get_worker(worker_id)
        if not worker:
            return
        contact_path = os.path.join(
            self.tmux.log_dir, f"{worker['tmux_session']}.brain_contact"
        )
        try:
            with open(contact_path, "w") as f:
                f.write(str(time.time()))
        except OSError:
            logger.warning(f"Could not write brain contact file for {worker_id}")

    def _is_grader_alive(self) -> bool:
        """Check if grader tmux session has a live process."""
        if not self.tmux.has_session(self._grader_session):
            return False
        result = subprocess.run(
            ["tmux", "list-panes", "-t", self._grader_session, "-F", "#{pane_pid}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False
        pane_pid = result.stdout.strip()
        if not pane_pid:
            return False
        result = subprocess.run(
            ["pgrep", "-P", pane_pid],
            capture_output=True,
        )
        return result.returncode == 0

    def _ensure_grader(self) -> bool:
        """Ensure the persistent grader tmux session is running.

        Lazily spawns a Claude Opus session on first call.
        Detects zombie sessions (tmux exists but process dead) and re-spawns.
        Truncates log before spawn to prevent stale readiness matches.
        Returns True if grader is ready, False on failure.
        """
        # Fast path: grader known-ready and process alive
        if self._grader_ready and self._is_grader_alive():
            return True

        # Kill zombie session if it exists but process is dead
        if self.tmux.has_session(self._grader_session):
            logger.warning("Grader session exists but process is dead — killing zombie")
            self.tmux.kill_session(self._grader_session)
        self._grader_ready = False

        # Truncate stale log before spawning fresh grader
        log_path = self.tmux.get_log_path(self._grader_session)
        open(log_path, "w").close()

        # Inject trust entry immediately before spawn (lazy import avoids circular dep)
        from ironclaude.main import ensure_brain_trusted
        ensure_brain_trusted(self._grader_home)

        # Spawn fresh session with grader's own working directory
        success = self.tmux.spawn_session(
            self._grader_session,
            "claude --model 'opus' --dangerously-skip-permissions",
            cwd=self._grader_home,
        )
        if not success:
            logger.warning("Failed to spawn grader tmux session")
            return False

        # Wait for Claude CLI to be ready (look for prompt indicator)
        deadline = time.time() + 60
        while time.time() < deadline:
            output = self.tmux.read_log_tail(self._grader_session, lines=50)
            if output and ("\u2771" in output or ">" in output or "\u276f" in output or "waiting for your" in output.lower()):
                # Deactivate professional mode so hooks don't interfere with grading
                if self._deactivate_pm_via_sqlite(self._grader_session, timeout=120) is not None:
                    logger.warning("Failed to deactivate PM on grader — killing session")
                    self.tmux.kill_session(self._grader_session)
                    return False
                self._grader_ready = True
                return True
            time.sleep(2)

        logger.warning("Grader session timed out waiting for ready")
        return False

    def _wait_for_grader_clear(self, timeout: int = 10) -> bool:
        """Wait for grader to finish processing /clear command.

        Polls log for prompt indicator confirming context was reset.
        Returns True when detected, False on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            output = self.tmux.read_log_tail(self._grader_session, lines=50)
            if output and ("\u2771" in output or "\u276f" in output or ">" in output or "waiting for your" in output.lower()):
                return True
            time.sleep(0.5)
        logger.warning("Grader /clear timed out after %ds", timeout)
        return False

    def _call_grader(self, system_prompt: str, user_prompt: str) -> dict:
        """Send grading prompt to persistent grader worker, read response.

        Sends the prompt to the ic-grader tmux session, polls the log
        for a JSON response, then sends /clear to reset context.

        Returns {"grade": str, "approved": bool, "feedback": str}.
        Falls back to grade F on any error or timeout.
        """
        import re

        if not self._ensure_grader():
            return {"grade": "F", "approved": False, "feedback": "Grader session failed to start"}

        # Capture baseline log output before sending prompt
        baseline = self.tmux.read_log_tail(self._grader_session, lines=200)

        # Send the combined prompt with nonce delimiter to prevent echo injection
        nonce = secrets.token_hex(8)
        combined = (
            f"{system_prompt}\n\n---\n\n{user_prompt}\n\n"
            f"Respond with ONLY valid JSON, no markdown fences: "
            f'{{\"grade\": \"A|B|C|D|F\", \"approved\": true|false, \"feedback\": \"...\", \"recommended_model\": \"claude-sonnet|claude-opus\"}}\n\n'
            f"Begin your JSON response after the delimiter: GRADER_RESPONSE_{nonce}"
        )
        self.tmux.send_keys(self._grader_session, combined)

        # Poll for JSON response in new log output (120s timeout)
        deadline = time.time() + 120
        delimiter = f"GRADER_RESPONSE_{nonce}"
        while time.time() < deadline:
            current = self.tmux.read_log_tail(self._grader_session, lines=200)

            # Compute delta: new output since baseline
            if current.startswith(baseline):
                delta = current[len(baseline):]
            else:
                # Log was truncated or rotated — search full current output
                delta = current

            # Only search for JSON after the nonce delimiter to prevent echo injection
            delimiter_pos = delta.find(delimiter)
            if delimiter_pos == -1:
                time.sleep(2)
                continue
            post_delimiter = delta[delimiter_pos + len(delimiter):]

            # Look for JSON with grade/approved/feedback fields
            json_match = re.search(
                r'\{[^{}]*"grade"\s*:\s*"[ABCDF]"[^{}]*"approved"\s*:\s*(?:true|false)[^{}]*"feedback"\s*:\s*"[^"]*"[^{}]*\}',
                post_delimiter,
            )
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    # Send /clear to reset context for next use
                    self.tmux.send_keys(self._grader_session, "/clear")
                    self._wait_for_grader_clear()
                    return {
                        "grade": result.get("grade", "F"),
                        "approved": result.get("approved", False),
                        "feedback": result.get("feedback", ""),
                    }
                except json.JSONDecodeError:
                    # Grader emitted invalid JSON (e.g. unescaped quotes in feedback)
                    # Extract fields individually via targeted regex
                    raw = json_match.group()
                    grade_m = re.search(r'"grade"\s*:\s*"([ABCDF])"', raw)
                    approved_m = re.search(r'"approved"\s*:\s*(true|false)', raw)
                    feedback_m = re.search(r'"feedback"\s*:\s*"(.*)"', raw, re.DOTALL)
                    if grade_m and approved_m:
                        self.tmux.send_keys(self._grader_session, "/clear")
                        self._wait_for_grader_clear()
                        return {
                            "grade": grade_m.group(1),
                            "approved": approved_m.group(1) == "true",
                            "feedback": feedback_m.group(1) if feedback_m else "",
                        }

            time.sleep(2)

        # Timeout — send /clear anyway to avoid stale context
        self.tmux.send_keys(self._grader_session, "/clear")
        self._wait_for_grader_clear()
        logger.warning("Grader timed out after 120s")
        return {"grade": "F", "approved": False, "feedback": "Grader timed out after 120s"}

    def get_operator_messages(
        self,
        limit: int = 20,
        hours_back: float = 24,
        start_date: str | None = None,
        end_date: str | None = None,
        only_operator: bool = True,
    ) -> list[dict]:
        """Read recent Slack messages from the operator (non-bot messages).

        Returns a list of message dicts with keys: text, ts, user.
        Returns [] if Slack is unavailable or not configured.
        Set only_operator=False to include bot messages.
        """
        if self._slack is None:
            return []
        return self._slack.search_operator_messages(
            limit=limit, hours_back=hours_back, start_date=start_date, end_date=end_date,
            only_operator=only_operator,
        )


    def submit_directive(self, source_ts: str, source_text: str, interpretation: str) -> dict:
        """Submit a new directive for the operator's confirmation.

        Inserts a row into the directives table and posts to Slack for confirmation.
        Returns dict with id and status.
        """
        if self._db is None:
            raise RuntimeError("Database connection required for directive operations")
        cursor = self._db.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, ?, ?)",
            (source_ts, source_text, interpretation),
        )
        self._db.commit()
        directive_id = cursor.lastrowid
        if self._slack is not None:
            interpretation_ts = self._slack.post_message(
                f"Directive #{directive_id} detected: '{interpretation}'. "
                f"From your message: '{source_text}'. "
                f"React 👍 to confirm or 👎 to reject."
            )
            if interpretation_ts:
                self._db.execute(
                    "UPDATE directives SET interpretation_ts=? WHERE id=?",
                    (interpretation_ts, directive_id),
                )
                self._db.commit()
                logger.info(
                    "Directive #%d: interpretation_ts=%r stored (source_ts=%r)",
                    directive_id, interpretation_ts, source_ts,
                )
            else:
                logger.warning(
                    "Directive #%d: post_message returned None — interpretation_ts will be NULL. "
                    "Reaction matching will rely on source_ts=%r or content fallback.",
                    directive_id, source_ts,
                )
            self._slack.remove_reaction("eyes", source_ts)
            self._slack.add_reaction("hourglass_flowing_sand", source_ts)
        return {"id": directive_id, "status": "pending_confirmation"}

    def get_directives(self, status: str | None = None) -> list[dict]:
        """Retrieve directives, optionally filtered by status.

        Returns list of directive dicts ordered by created_at descending.
        """
        if self._db is None:
            raise RuntimeError("Database connection required for directive operations")
        if status:
            rows = self._db.execute(
                "SELECT * FROM directives WHERE status=? ORDER BY created_at DESC", (status,),
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM directives ORDER BY created_at DESC").fetchall()
        directives = [dict(row) for row in rows]
        # Reconcile emoji on recent directives
        if self._slack is not None:
            import time
            from ironclaude.slack_interface import DIRECTIVE_STATUS_EMOJI
            cutoff = time.time() - (48 * 3600)
            for d in directives:
                source_ts = d.get("source_ts")
                try:
                    ts_val = float(source_ts)
                except (ValueError, TypeError):
                    continue
                if not source_ts or ts_val < cutoff:
                    continue
                expected_emoji = DIRECTIVE_STATUS_EMOJI.get(d["status"])
                if not expected_emoji:
                    continue
                reactions = self._slack.get_reactions(source_ts)
                reaction_names = {r["name"] for r in reactions}
                # Remove wrong status emoji
                for emoji_name in DIRECTIVE_STATUS_EMOJI.values():
                    if emoji_name in reaction_names and emoji_name != expected_emoji:
                        self._slack.remove_reaction(emoji_name, source_ts)
                # Add correct emoji if missing
                if expected_emoji not in reaction_names:
                    self._slack.add_reaction(expected_emoji, source_ts)
        return directives

    def get_status_summary(self) -> dict:
        """Return directives grouped by status plus active workers.

        Returns dict with keys:
        - in_progress: directives currently being worked on
        - needs_input: directives awaiting operator confirmation
        - recently_completed: last 5 completed directives
        - active_workers: currently running worker sessions
        """
        if self._db is None:
            raise RuntimeError("Database connection required for directive operations")
        in_progress = [
            dict(r) for r in self._db.execute(
                "SELECT * FROM directives WHERE status='in_progress' ORDER BY created_at DESC"
            ).fetchall()
        ]
        needs_input = [
            dict(r) for r in self._db.execute(
                "SELECT * FROM directives WHERE status='pending_confirmation' ORDER BY created_at DESC"
            ).fetchall()
        ]
        recently_completed = [
            dict(r) for r in self._db.execute(
                "SELECT * FROM directives WHERE status='completed' ORDER BY updated_at DESC LIMIT 5"
            ).fetchall()
        ]
        active_workers = self.registry.get_running_workers()
        return {
            "in_progress": in_progress,
            "needs_input": needs_input,
            "recently_completed": recently_completed,
            "active_workers": active_workers,
        }

    def update_directive_status(self, directive_id: int, status: str) -> dict:
        """Update a directive's status.

        Validates the status value and that the directive exists.
        Returns the updated directive dict.
        """
        if self._db is None:
            raise RuntimeError("Database connection required for directive operations")
        if status not in VALID_DIRECTIVE_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_DIRECTIVE_STATUSES))}")
        row = self._db.execute("SELECT * FROM directives WHERE id=?", (directive_id,)).fetchone()
        if row is None:
            raise ValueError(f"Directive {directive_id} not found")
        old_status = dict(row)["status"]
        self._db.execute(
            "UPDATE directives SET status=?, updated_at=datetime('now') WHERE id=?", (status, directive_id),
        )
        self._db.commit()
        # Swap emoji reaction on operator's original message
        source_ts = dict(row).get("source_ts")
        if self._slack is not None and source_ts:
            from ironclaude.slack_interface import DIRECTIVE_STATUS_EMOJI
            old_emoji = DIRECTIVE_STATUS_EMOJI.get(old_status)
            new_emoji = DIRECTIVE_STATUS_EMOJI.get(status)
            if old_emoji:
                self._slack.remove_reaction(old_emoji, source_ts)
            if new_emoji:
                self._slack.add_reaction(new_emoji, source_ts)
        updated = self._db.execute("SELECT * FROM directives WHERE id=?", (directive_id,)).fetchone()
        return dict(updated)

    def debug_slack_connection(self) -> dict:
        """Diagnose Slack connectivity issues.

        Returns diagnostic dict with reachability, message counts, and sample data.
        """
        if self._slack is None:
            return {"reachable": False, "error": "Slack not configured"}
        reachable = self._slack.is_reachable()
        if not reachable:
            return {"reachable": False, "error": "Slack API unreachable"}
        try:
            result = self._slack._client.conversations_history(
                channel=self._slack._channel_id, limit=20
            )
            messages = result.get("messages", [])
            user_msgs = [m for m in messages if m.get("user") and not m.get("bot_id")]
            bot_msgs = [m for m in messages if m.get("bot_id")]
            diag = {
                "reachable": True,
                "total_messages": len(messages),
                "user_messages": len(user_msgs),
                "bot_messages": len(bot_msgs),
                "sample_keys": list(messages[0].keys()) if messages else [],
            }
            # Search API diagnostics
            if getattr(self._slack, "_user_client", None):
                diag["search_api_available"] = True
                diag["search_operator_user_id"] = getattr(self._slack, "_operator_user_id", "")
                try:
                    search_result = self._slack._user_client.search_messages(
                        query=f"in:<#{self._slack._channel_id}>", count=5
                    )
                    diag["search_messages_count"] = len(
                        search_result.get("messages", {}).get("matches", [])
                    )
                except Exception as e:
                    diag["search_messages_count"] = 0
                    diag["search_error"] = str(e)
            else:
                diag["search_api_available"] = False
            return diag
        except Exception as e:
            return {"reachable": True, "error": f"API call failed: {e}"}

    def ensure_worker_trusted(self, repo: str) -> None:
        """Delegate to module-level ensure_worker_trusted."""
        ensure_worker_trusted(repo)

    def _ensure_claude_md(self, repo: str) -> None:
        """Ensure repo has a CLAUDE.md file for clean PM activation.

        If repo lacks CLAUDE.md, writes the standard boilerplate template.
        If repo already has one, does nothing. Failures are logged but
        never block the spawn pipeline.
        """
        claude_md_path = os.path.join(repo, "CLAUDE.md")
        if os.path.exists(claude_md_path):
            logger.info(f"CLAUDE.md already exists in {repo}")
            return

        template_path = Path(__file__).parent / "templates" / "worker_claude_md.md"
        try:
            content = template_path.read_text()
        except FileNotFoundError:
            logger.warning(f"Worker CLAUDE.md template not found at {template_path}")
            return

        try:
            with open(claude_md_path, "w") as f:
                f.write(content)
            logger.info(f"Injected CLAUDE.md into {repo}")
        except OSError as e:
            logger.warning(f"Failed to write CLAUDE.md to {repo}: {e}")

    def _wait_for_ready(self, session_name: str, timeout: int = 30) -> bool:
        """Poll tmux log until the worker is ready or timeout exceeded.

        Returns True if a ready indicator ("ironclaude v") is found,
        False if the timeout is exceeded without seeing one.
        Dismisses trust dialogs by sending Enter if detected.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            output = self.tmux.read_log_tail(session_name, lines=50)
            if output:
                lower = output.lower()
                if "trust this folder" in lower:
                    self.tmux.send_keys(session_name, "")
                if "ironclaude v" in output:
                    return True
            time.sleep(1)
        return False

    def _activate_pm_via_sqlite(
        self, session_name: str, timeout: int = 30, _claude_dir: Path | None = None
    ) -> str | None:
        """Activate professional mode by writing directly to ironclaude.db.

        Gets the tmux pane PID, reads the session ID file written by the Claude
        CLI during init, and writes professional_mode='on' to the sessions table.

        Args:
            session_name: tmux session name (e.g. 'ic-w1')
            timeout: seconds to wait for session ID file to appear
            _claude_dir: override ~/.claude path (for testing)

        Returns None on success, or a failure reason string on any failure.
        """
        claude_dir = _claude_dir if _claude_dir is not None else Path("~/.claude").expanduser()

        # Step 1: Get pane PID via tmux
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                reason = f"tmux list-panes failed: {result.stderr.strip()}"
                logger.warning(f"{reason} for {session_name}")
                return reason
            pane_pid = result.stdout.strip()
            if not pane_pid.isdigit():
                reason = f"invalid pane PID: {pane_pid}"
                logger.warning(f"{reason} for {session_name}")
                return reason
        except Exception as e:
            reason = f"pane PID error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason

        # Step 2: Poll for session ID file
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        deadline = time.time() + timeout
        session_uuid = None
        while time.time() < deadline:
            if session_id_file.exists():
                candidate = session_id_file.read_text().strip()
                if len(candidate) == 36:
                    session_uuid = candidate
                    break
            time.sleep(1)

        if session_uuid is None:
            reason = f"session ID file timeout after {timeout}s"
            logger.warning(f"{reason} for {session_name}")
            return reason

        # Step 3: Write professional_mode='on' to DB
        db_path = claude_dir / "ironclaude.db"
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                " VALUES (?, 'on')",
                (session_uuid,),
            )
            conn.execute(
                "UPDATE sessions SET professional_mode='on', updated_at=datetime('now')"
                " WHERE terminal_session=?",
                (session_uuid,),
            )
            conn.commit()
            conn.close()
            logger.info(
                f"PM activated via SQLite for {session_name} "
                f"(session {session_uuid[:8]}...)"
            )
            return None
        except sqlite3.Error as e:
            reason = f"sqlite error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason

    def _deactivate_pm_via_sqlite(
        self, session_name: str, timeout: int = 30, _claude_dir: Path | None = None
    ) -> str | None:
        """Deactivate professional mode by writing 'off' to ironclaude.db.

        Mirror of _activate_pm_via_sqlite but writes professional_mode='off'.
        Used for the grader session which must not have hooks active.

        Returns None on success, or a failure reason string on any failure.
        """
        claude_dir = _claude_dir if _claude_dir is not None else Path("~/.claude").expanduser()

        # Step 1: Get pane PID via tmux
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                reason = f"tmux list-panes failed: {result.stderr.strip()}"
                logger.warning(f"{reason} for {session_name}")
                return reason
            pane_pid = result.stdout.strip()
            if not pane_pid.isdigit():
                reason = f"invalid pane PID: {pane_pid}"
                logger.warning(f"{reason} for {session_name}")
                return reason
        except Exception as e:
            reason = f"pane PID error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason

        # Step 2: Poll for session ID file
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        deadline = time.time() + timeout
        session_uuid = None
        while time.time() < deadline:
            if session_id_file.exists():
                candidate = session_id_file.read_text().strip()
                if len(candidate) == 36:
                    session_uuid = candidate
                    break
            time.sleep(1)

        if session_uuid is None:
            reason = f"session ID file timeout after {timeout}s"
            logger.warning(f"{reason} for {session_name}")
            return reason

        # Step 3: Write professional_mode='off' to DB
        db_path = claude_dir / "ironclaude.db"
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                " VALUES (?, 'off')",
                (session_uuid,),
            )
            conn.execute(
                "UPDATE sessions SET professional_mode='off', updated_at=datetime('now')"
                " WHERE terminal_session=?",
                (session_uuid,),
            )
            conn.commit()
            conn.close()
            logger.info(
                f"PM deactivated via SQLite for {session_name} "
                f"(session {session_uuid[:8]}...)"
            )
            return None
        except sqlite3.Error as e:
            reason = f"sqlite error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason

    def spawn_worker(
        self,
        worker_id: str,
        worker_type: str,
        repo: str,
        objective: str,
        allowed_paths: list[str] | None = None,
        model_name: str = "",
    ) -> str | dict:
        """Spawn a new worker with the given objective."""
        # Retry escalation: auto-upgrade to opus if previous attempt with same base ID failed
        base_id = re.sub(r'[-_]?\d*[a-z]?$', '', worker_id)
        if base_id and base_id in self._failed_worker_bases and worker_type == "claude-sonnet":
            logger.info(f"Escalating {worker_id} to claude-opus (previous failure on {base_id})")
            worker_type = "claude-opus"

        # Inline grader enforcement — MCP grades automatically
        avatar_skill = _load_avatar_skill().replace("{OPERATOR_NAME}", self._operator_name)
        system_prompt = f"""{avatar_skill}

You are grading a spawn_worker decision. Respond with valid JSON only — no markdown, no explanation:
{{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "specific feedback"}}

Grading criteria:
- A: Specific objective (file paths, success criteria, constraints), correct worker type. approved=true
- B: Minor issues but fundamentally sound. approved=true
- C: Missing constraints, weak objective, vague success criteria. approved=false
- D: Significant problems — wrong approach, scaffolding thinking. approved=false
- F: Fundamentally wrong — violates {self._operator_name}'s principles. approved=false

Automatic F-grade triggers (objective contains PM deactivation language):
- Any objective instructing the worker to deactivate, disable, or turn off professional mode
- Pattern detection for: "deactivate professional mode", "disable professional mode", "turn off professional mode", "PM off", "/deactivate-professional-mode", "skip professional mode", "without professional mode"
- These are ALWAYS grade F, approved=false — no exceptions

Model recommendation (include "recommended_model" in your JSON response):
- claude-sonnet: single-file changes, config updates, bug fixes with clear root cause,
  adding tests, documentation, straightforward features
- claude-opus: multi-file refactors (5+ files), architectural changes requiring broad
  codebase understanding, complex debugging with unclear root cause, greenfield design"""

        # Hard-enforce brain-notes: append repo constraints before grader sees objective
        notes_path = os.path.join(repo, ".ironclaude", "brain-notes.md")
        if os.path.exists(notes_path):
            try:
                with open(notes_path) as f:
                    notes_content = f.read()
                objective += "\n\n--- REPO CONSTRAINTS (from .ironclaude/brain-notes.md) ---\n" + notes_content
                logger.info(f"Appended brain-notes from {notes_path} to objective for '{worker_id}'")
            except OSError as e:
                logger.warning(f"Could not read brain-notes at {notes_path}: {e}")

        user_prompt = f"""Evaluate this spawn decision:

worker_id: {worker_id}
worker_type: {worker_type}
repo: {repo}
objective: {objective}

Does this objective meet {self._operator_name}'s standards? Is the worker type appropriate?"""

        grade_result = self._call_grader(system_prompt, user_prompt)
        if not grade_result["approved"]:
            return {
                "error": f"Spawn rejected by grader (grade {grade_result['grade']}). {grade_result['feedback']}",
                "action": "revise objective and try again",
            }
        logger.info(f"Grader approved spawn for '{worker_id}' (grade {grade_result['grade']})")

        # Handle ollama dynamic command construction
        if worker_type == "ollama":
            if not model_name:
                return {"error": "Cannot spawn ollama worker — model_name is required"}

            # Enforce ollama singleton (hard enforcement)
            running_ollama = self.registry.get_running_workers_by_type("ollama")
            if running_ollama:
                raise ValueError(
                    f"Ollama worker slot occupied by '{running_ollama[0]['id']}'. "
                    f"Wait for completion or use claude-opus/claude-sonnet."
                )

            cmd = f"ollama launch claude --model {shlex.quote(model_name)} -- --dangerously-skip-permissions"
        elif worker_type in WORKER_COMMANDS:
            cmd = WORKER_COMMANDS[worker_type]
        else:
            raise ValueError(
                f"Invalid worker type '{worker_type}'. "
                f"Supported: ollama, {', '.join(WORKER_COMMANDS.keys())}"
            )

        # Inject worker ID for stop hook completion detection
        cmd = f"export IC_WORKER_ID={shlex.quote(worker_id)}; {cmd}"

        session_name = f"ic-{worker_id}"

        # Stage 0: ensure CLAUDE.md exists for clean PM activation
        self._ensure_claude_md(repo)

        # Stage 1: ensure trust
        self.ensure_worker_trusted(repo)

        # Stage 2: spawn tmux session
        success = self.tmux.spawn_session(session_name, cmd, cwd=repo)
        if not success:
            raise RuntimeError(
                f"Failed to spawn tmux session for worker '{worker_id}'"
            )

        # Stage 3: wait for ready
        self._wait_for_ready(session_name, timeout=30)

        # Stages 4-5: activate professional mode via direct SQLite write
        # timeout raised from 120→300 because session-init.sh hook can be slow
        # when ~/.claude/session-env/ has many files (find without -maxdepth)
        pm_failure = self._activate_pm_via_sqlite(session_name, timeout=300)
        if pm_failure is not None:
            self.tmux.kill_session(session_name)
            return {"error": f"PM activation failed for worker '{worker_id}': {pm_failure}"}

        # Stage 6: send objective
        self.registry.register_worker(worker_id, worker_type, session_name, repo=repo, description=objective)
        self.tmux.send_keys(session_name, objective)
        self.registry.log_event(
            "worker_spawned",
            worker_id=worker_id,
            details={
                "type": worker_type,
                "repo": repo,
                "objective": objective,
                "allowed_paths": allowed_paths,
                "model_name": model_name,
            },
        )

        recommended = grade_result.get('recommended_model', worker_type)
        return f"Worker '{worker_id}' spawned ({worker_type}) in {repo}. Model recommendation: {recommended}"

    def spawn_workers(self, requests: list[dict]) -> list[dict]:
        """Spawn multiple workers with batch grading and parallel PM activation.

        Each request: {worker_id, worker_type, repo, objective, allowed_paths?, model_name?}
        Returns list of results (one per request): success string or error dict.
        """
        if not requests:
            return []

        # Apply retry escalation to each request
        for req in requests:
            base_id = re.sub(r'[-_]?\d*[a-z]?$', '', req["worker_id"])
            if base_id and base_id in self._failed_worker_bases and req.get("worker_type") == "claude-sonnet":
                logger.info(f"Escalating {req['worker_id']} to claude-opus (previous failure on {base_id})")
                req["worker_type"] = "claude-opus"

        # Hard-enforce brain-notes for each request
        for req in requests:
            notes_path = os.path.join(req["repo"], ".ironclaude", "brain-notes.md")
            if os.path.exists(notes_path):
                try:
                    with open(notes_path) as f:
                        req["objective"] += "\n\n--- REPO CONSTRAINTS (from .ironclaude/brain-notes.md) ---\n" + f.read()
                except OSError:
                    pass

        # Batch grading: single grader call for all objectives
        avatar_skill = _load_avatar_skill().replace("{OPERATOR_NAME}", self._operator_name)
        system_prompt = f"""{avatar_skill}

You are grading {len(requests)} spawn_worker decisions. Respond with a valid JSON ARRAY — one object per decision:
[{{"worker_id": "...", "grade": "A|B|C|D|F", "approved": true|false, "feedback": "...", "recommended_model": "claude-sonnet|claude-opus"}}, ...]

Grading criteria (apply to EACH decision independently):
- A: Specific objective (file paths, success criteria, constraints), correct worker type. approved=true
- B: Minor issues but fundamentally sound. approved=true
- C: Missing constraints, weak objective, vague success criteria. approved=false
- D: Significant problems — wrong approach, scaffolding thinking. approved=false
- F: Fundamentally wrong — violates {self._operator_name}'s principles. approved=false

Model recommendation (include "recommended_model" for each):
- claude-sonnet: single-file changes, config updates, bug fixes with clear root cause
- claude-opus: multi-file refactors (5+ files), architectural changes, complex debugging"""

        decisions_text = ""
        for i, req in enumerate(requests, 1):
            decisions_text += f"\n\n--- Decision {i} ---\n"
            decisions_text += f"worker_id: {req['worker_id']}\n"
            decisions_text += f"worker_type: {req['worker_type']}\n"
            decisions_text += f"repo: {req['repo']}\n"
            decisions_text += f"objective: {req['objective']}"

        user_prompt = f"Evaluate these {len(requests)} spawn decisions:{decisions_text}"

        # Try batch grading
        grade_results = self._call_grader(system_prompt, user_prompt)

        # Validate batch response — must be a list with correct length
        if not isinstance(grade_results, list) or len(grade_results) != len(requests):
            # Fallback: grade each individually
            logger.warning("Batch grading returned invalid response — falling back to individual grading")
            grade_results = []
            for req in requests:
                individual_result = self._call_grader(system_prompt, f"Evaluate this spawn decision:\n\n"
                    f"worker_id: {req['worker_id']}\nworker_type: {req['worker_type']}\n"
                    f"repo: {req['repo']}\nobjective: {req['objective']}")
                if isinstance(individual_result, dict):
                    grade_results.append(individual_result)
                else:
                    grade_results.append({"grade": "F", "approved": False, "feedback": "Grader returned invalid response"})

        # Separate approved and rejected
        results = []
        approved = []
        for req, grade in zip(requests, grade_results):
            if not isinstance(grade, dict) or not grade.get("approved"):
                feedback = grade.get("feedback", "Unknown") if isinstance(grade, dict) else "Invalid grade"
                g = grade.get("grade", "F") if isinstance(grade, dict) else "F"
                results.append({"worker_id": req["worker_id"], "error": f"Rejected (grade {g}): {feedback}"})
            else:
                approved.append((req, grade))
                results.append(None)  # placeholder — will be filled after spawn

        # Spawn all approved tmux sessions
        spawned = []
        for req, grade in approved:
            worker_type = req["worker_type"]
            worker_id = req["worker_id"]
            repo = req["repo"]
            model_name = req.get("model_name", "")

            self._ensure_claude_md(repo)
            self.ensure_worker_trusted(repo)

            if worker_type in WORKER_COMMANDS:
                cmd = WORKER_COMMANDS[worker_type]
            elif worker_type == "ollama" and model_name:
                cmd = f"export CLAUDE_CODE_EFFORT_LEVEL=high; exec claude --model {shlex.quote(model_name)} --dangerously-skip-permissions"
            else:
                idx = next(i for i, r in enumerate(results) if r is None)
                results[idx] = {"worker_id": worker_id, "error": f"Unknown worker type: {worker_type}"}
                continue

            cmd = f"export IC_WORKER_ID={shlex.quote(worker_id)}; {cmd}"
            session_name = f"ic-{worker_id}"

            success = self.tmux.spawn_session(session_name, cmd, cwd=repo)
            if not success:
                idx = next(i for i, r in enumerate(results) if r is None)
                results[idx] = {"worker_id": worker_id, "error": "Failed to spawn tmux session"}
                continue

            spawned.append((req, grade, session_name))

        # Parallel PM activation: poll all PPID files in a single loop
        # timeout raised from 120→300 because session-init.sh hook can be slow
        # when ~/.claude/session-env/ has many files (find without -maxdepth)
        claude_dir = Path("~/.claude").expanduser()
        deadline = time.time() + 300
        pending = {}
        for req, grade, session_name in spawned:
            try:
                result = subprocess.run(
                    ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
                    capture_output=True, text=True,
                )
                pane_pid = result.stdout.strip()
                if pane_pid.isdigit():
                    pending[session_name] = (req, grade, pane_pid)
            except Exception:
                pass

        activated = {}
        while time.time() < deadline and pending:
            for session_name, (req, grade, pane_pid) in list(pending.items()):
                session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
                if session_id_file.exists():
                    candidate = session_id_file.read_text().strip()
                    if len(candidate) == 36:
                        # Write PM=on
                        db_path = claude_dir / "ironclaude.db"
                        try:
                            conn = sqlite3.connect(str(db_path), timeout=5)
                            conn.execute("PRAGMA journal_mode=WAL")
                            conn.execute(
                                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                                " VALUES (?, 'on')", (candidate,))
                            conn.execute(
                                "UPDATE sessions SET professional_mode='on', updated_at=datetime('now')"
                                " WHERE terminal_session=?", (candidate,))
                            conn.commit()
                            conn.close()
                            activated[session_name] = (req, grade)
                            del pending[session_name]
                            logger.info(f"PM activated for {session_name} (batch)")
                        except sqlite3.Error as e:
                            logger.warning(f"SQLite error activating PM for {session_name}: {e}")
            time.sleep(2)

        # Send objectives to activated workers, clean up timed-out ones
        for req, grade, session_name in spawned:
            worker_id = req["worker_id"]
            idx = next(i for i, r in enumerate(results) if r is None)
            if session_name in activated:
                self.registry.register_worker(
                    worker_id, req["worker_type"], session_name,
                    repo=req["repo"], description=req["objective"])
                self.tmux.send_keys(session_name, req["objective"])
                recommended = grade.get("recommended_model", req["worker_type"])
                results[idx] = {
                    "worker_id": worker_id,
                    "status": "spawned",
                    "grade": grade.get("grade", "?"),
                    "recommended_model": recommended,
                }
            else:
                self.tmux.kill_session(session_name)
                results[idx] = {"worker_id": worker_id, "error": "PM activation timed out (batch)"}

        return results

    def approve_plan(self, worker_id: str, rationale: str) -> str:
        """Approve a worker's plan with documented rationale."""
        worker = self.registry.get_worker(worker_id)
        if not worker:
            raise ValueError(f"Worker '{worker_id}' not found")

        session_name = f"ic-{worker_id}"
        if not self.tmux.has_session(session_name):
            self.registry.update_worker_status(worker_id, "failed")
            raise RuntimeError(f"Worker '{worker_id}' tmux session is dead")

        self.tmux.send_keys(session_name, "yes")
        self.registry.log_event(
            "plan_approved",
            worker_id=worker_id,
            details={"rationale": rationale},
        )

        return f"Plan approved for '{worker_id}'. Rationale: {rationale}"

    def reject_plan(self, worker_id: str, reason: str) -> str:
        """Reject a worker's plan with documented reason."""
        worker = self.registry.get_worker(worker_id)
        if not worker:
            raise ValueError(f"Worker '{worker_id}' not found")

        session_name = f"ic-{worker_id}"
        if not self.tmux.has_session(session_name):
            self.registry.update_worker_status(worker_id, "failed")
            raise RuntimeError(f"Worker '{worker_id}' tmux session is dead")

        self.tmux.send_keys(session_name, f"no: {reason}")
        self.registry.log_event(
            "plan_rejected",
            worker_id=worker_id,
            details={"reason": reason},
        )

        return f"Plan rejected for '{worker_id}'. Reason: {reason}"

    def send_to_worker(self, worker_id: str, message: str) -> str | dict:
        """Send a message to a running worker."""
        worker = self.registry.get_worker(worker_id)
        if not worker:
            raise ValueError(f"Worker '{worker_id}' not found")

        session_name = f"ic-{worker_id}"
        if not self.tmux.has_session(session_name):
            self.registry.update_worker_status(worker_id, "failed")
            raise RuntimeError(f"Worker '{worker_id}' tmux session is dead")

        # Inline grader enforcement — MCP grades automatically
        avatar_skill = _load_avatar_skill()
        system_prompt = f"""{avatar_skill}

You are grading a send_to_worker message. Respond with valid JSON only — no markdown, no explanation:
{{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "specific feedback"}}

Grading criteria:
- A: Message answers the worker's question appropriately within the ironclaude workflow. approved=true
- B: Minor issues but does not contradict the workflow. approved=true
- C: Vague or unhelpful guidance but not harmful. approved=false
- D: Micromanages implementation (provides code) instead of answering at the right workflow level. approved=false
- F: Contradicts the ironclaude workflow — tells worker to skip stages, bypass professional mode, or avoid design/planning. approved=false

Automatic F-grade triggers (in addition to avatar_skill banned terms):
- Telling a worker to skip brainstorming, design, or planning
- Telling a worker a design doc is not needed
- Telling a worker to "just make the change" or edit files directly
- Providing implementation code instead of answering design/planning questions
- Any instruction that would cause the worker to bypass professional mode hooks
- Any message instructing the worker to deactivate, disable, or turn off its own professional mode
- Pattern detection for: 'deactivate professional mode', 'disable professional mode', 'turn off professional mode', 'PM off', '/deactivate-professional-mode', 'skip professional mode', 'without professional mode'"""

        user_prompt = f"""Evaluate this message being sent to worker '{worker_id}':

{message}

Does this message respect the ironclaude workflow? Would it block or misdirect the worker?"""

        grade_result = self._call_grader(system_prompt, user_prompt)
        if not grade_result["approved"]:
            return {
                "error": f"Message rejected by grader (grade {grade_result['grade']}). {grade_result['feedback']}",
                "action": "revise message and try again",
            }
        logger.info(f"Grader approved message to '{worker_id}' (grade {grade_result['grade']})")

        self.tmux.send_keys(session_name, message)
        self.registry.log_event(
            "message_sent",
            worker_id=worker_id,
            details={"message": message},
        )
        self._write_brain_contact(worker_id)

        return f"Message sent to '{worker_id}'"

    def update_ledger(self, objective: str, tasks: list[dict]) -> str:
        """Update the task ledger with current objective and tasks."""
        ledger = {"objective": objective, "tasks": tasks}
        Path(self.ledger_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "w") as f:
            json.dump(ledger, f, indent=2)
        return f"Ledger updated: {len(tasks)} tasks"

    def get_task_ledger(self) -> dict:
        """Read the current task ledger."""
        try:
            with open(self.ledger_path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {"objective": None, "tasks": []}

    def get_worker_status(self, worker_id: str | None = None) -> dict | list[dict]:
        """Get status of a specific worker or all running workers."""
        if worker_id:
            worker = self.registry.get_worker(worker_id)
            if not worker:
                raise ValueError(f"Worker '{worker_id}' not found")
            self._write_brain_contact(worker_id)
            return dict(worker)

        workers = self.registry.get_running_workers()
        result = []
        for w in workers:
            w_dict = dict(w)
            w_dict["tmux_alive"] = self.tmux.has_session(w["tmux_session"])
            result.append(w_dict)
        return result

    def get_worker_log(self, worker_id: str, lines: int = 50) -> str:
        """Read the last N lines of a worker's log."""
        session_name = f"ic-{worker_id}"
        try:
            result = self.tmux.capture_pane(session_name, lines=lines)
            self._write_brain_contact(worker_id)
            return result
        except subprocess.CalledProcessError:
            pass
        log_path = self.tmux.get_log_path(session_name)
        try:
            with open(log_path) as f:
                tail = collections.deque(f, maxlen=lines)
            self._write_brain_contact(worker_id)
            return _strip_ansi("".join(tail))
        except FileNotFoundError:
            raise ValueError(f"No log file found for worker '{worker_id}'")

    def kill_worker(self, worker_id: str, original_objective: str = "", evidence: str = "") -> str | dict:
        """Kill a worker's tmux session and mark it completed."""
        # Inline grader enforcement — MCP grades automatically
        if original_objective and evidence:
            avatar_skill = _load_avatar_skill()
            system_prompt = f"""{avatar_skill}

You are grading a kill_worker decision. Respond with valid JSON only — no markdown, no explanation:
{{"grade": "A|B|C|D|F", "approved": true|false, "feedback": "specific feedback"}}

Grading criteria:
- A: All success criteria verified with concrete evidence (diffs, timestamps, test results). approved=true
- B: Most criteria verified, minor items can be deferred. approved=true
- C: Some criteria unverified — trusted self-assessment instead of checking. approved=false
- D: Worker claimed done but evidence shows incomplete. approved=false
- F: Work clearly not done. approved=false"""

            user_prompt = f"""Evaluate this kill_worker decision:

worker_id: {worker_id}
original_objective: {original_objective}
evidence provided: {evidence}

Has the worker genuinely completed its objective based on the evidence?"""

            grade_result = self._call_grader(system_prompt, user_prompt)
            if not grade_result["approved"]:
                # Track failure for retry escalation
                fail_base = re.sub(r'[-_]?\d*[a-z]?$', '', worker_id)
                if fail_base:
                    self._failed_worker_bases.add(fail_base)
                    logger.info(f"Tracked failure base '{fail_base}' for retry escalation")
                return {
                    "error": f"Kill rejected by grader (grade {grade_result['grade']}). {grade_result['feedback']}",
                    "action": "send worker back to finish, then try again with updated evidence",
                }
            logger.info(f"Grader approved kill for '{worker_id}' (grade {grade_result['grade']})")
        else:
            logger.warning(f"kill_worker called without objective/evidence for '{worker_id}' — skipping grader")

        session_name = f"ic-{worker_id}"
        self.tmux.kill_session(session_name)
        self.registry.update_worker_status(worker_id, "completed")
        _wr = self.registry.get_worker(worker_id)
        _runtime = None
        if _wr:
            try:
                _created = datetime.fromisoformat(_wr["spawned_at"])
                if _created.tzinfo is None:
                    _created = _created.replace(tzinfo=timezone.utc)
                _runtime = round(time.time() - _created.timestamp(), 1)
            except (ValueError, TypeError):
                pass
        log_worker_event(
            "WORKER_KILLED",
            worker_id=worker_id,
            had_evidence=bool(original_objective and evidence),
            kill_reason=evidence[:200] if evidence else None,
            runtime_seconds=_runtime,
        )
        self.registry.log_event("worker_finished", worker_id=worker_id)
        return f"Worker {worker_id} killed and marked completed."

    def restart_daemon(self) -> str:
        """Send SIGHUP to the IronClaude daemon to trigger a graceful self-restart."""
        import signal as _signal
        pid_file = Path("/tmp/ic-daemon.pid")
        if not pid_file.exists():
            return "ERROR: PID file /tmp/ic-daemon.pid not found — is the daemon running?"
        try:
            daemon_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError) as e:
            return f"ERROR: Could not read PID file: {e}"
        try:
            os.kill(daemon_pid, _signal.SIGHUP)
        except ProcessLookupError:
            return f"ERROR: No process with PID {daemon_pid} — stale PID file?"
        except PermissionError:
            return f"ERROR: No permission to signal PID {daemon_pid}"
        return f"Daemon restart requested (SIGHUP sent to PID {daemon_pid})"

    def game_launch(self, resolution: str = "1280x720") -> str:
        """Launch GodotSteam with Artificial Adventures in windowed mode."""
        godot_bin = os.environ.get("QE_LAUNCH_BIN", "")
        game_path = os.environ.get("QE_LAUNCH_PATH", "")
        if not godot_bin or not game_path:
            return json.dumps({"error": "QE_LAUNCH_BIN and QE_LAUNCH_PATH environment variables are required"})
        proc = subprocess.Popen(
            [godot_bin, "--path", game_path, "--windowed", "--resolution", resolution],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._game_pid = proc.pid
        for _ in range(30):
            check = subprocess.run(["pgrep", "-f", "Godot"], capture_output=True)
            if check.returncode == 0:
                return json.dumps({"pid": self._game_pid, "status": "running"})
            time.sleep(1)
        return json.dumps({"error": "Godot failed to start within 30s"})

    def game_screenshot(self) -> str:
        """Take a screenshot of the game window. Returns file path to PNG."""
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to tell process "Godot" to set frontmost to true'],
            capture_output=True, timeout=5,
        )
        time.sleep(0.3)
        timestamp = int(time.time() * 1000)
        path = f"/tmp/game-screenshot-{timestamp}.png"
        result = subprocess.run(
            ["screencapture", "-x", path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"screencapture failed: {result.stderr}")
        subprocess.run(
            ["sips", "--resampleWidth", "1280", "--resampleHeight", "720", path],
            capture_output=True, timeout=10,
        )
        return json.dumps({"path": path})

    def game_click(self, x: int, y: int) -> str:
        """Click at screen coordinates (x, y) via cliclick."""
        result = subprocess.run(["cliclick", f"c:{x},{y}"])
        return json.dumps({"action": "click", "x": x, "y": y, "success": result.returncode == 0})

    def game_type(self, text: str) -> str:
        """Type text at current cursor position via cliclick."""
        result = subprocess.run(["cliclick", f"t:{text}"])
        return json.dumps({"action": "type", "text": text, "success": result.returncode == 0})

    def game_key(self, key: str) -> str:
        """Press a key or key combination via cliclick. Examples: 'Return', 'Escape', 'space'."""
        mapped = KEY_MAP.get(key, key.lower())
        result = subprocess.run(["cliclick", f"kp:{mapped}"])
        return json.dumps({"action": "key", "key": key, "success": result.returncode == 0})

    def game_kill(self) -> str:
        """Kill the running Godot process."""
        subprocess.run(["pkill", "-f", "Godot"], capture_output=True)
        self._game_pid = None
        return json.dumps({"status": "killed"})

    def restart_mcp(self) -> None:
        """Close DB and exec a fresh instance of this MCP server.

        Preserves stdin/stdout so Claude Code's stdio pipe survives the restart.
        Does not return — os.execvp replaces the process image.
        """
        logger.info("restart_mcp: closing DB and exec'ing fresh instance (argv=%s)", sys.argv)
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvp(sys.executable, [sys.executable] + sys.argv)

    def query_supabase(
        self,
        table: str,
        filters: dict | None = None,
        limit: int = 50,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> list[dict] | dict:
        """Query a Supabase telemetry table via the PostgREST REST API.

        All queries are read-only SELECT operations.

        Args:
            table: Table name. Must be one of: players, sessions, events, feedback, errors.
            filters: Optional equality filters as {column: value} pairs.
            limit: Maximum rows to return (default 50).
            order_by: Column to sort by (default created_at).
            ascending: Sort ascending if True, descending if False (default).

        Returns:
            List of row dicts, or {error: "message"} dict on failure.
        """
        if filters is None:
            filters = {}
        if not self._supabase_url or not self._supabase_anon_key:
            return {"error": "Supabase not configured (missing SUPABASE_URL or SUPABASE_ANON_KEY)"}
        if table not in VALID_SUPABASE_TABLES:
            return {"error": f"Invalid table '{table}'. Must be one of: {', '.join(sorted(VALID_SUPABASE_TABLES))}"}
        if order_by not in VALID_ORDER_BY_COLUMNS:
            return {"error": f"Invalid order_by column '{order_by}'. Must be one of: {', '.join(sorted(VALID_ORDER_BY_COLUMNS))}"}
        if not (1 <= limit <= 1000):
            return {"error": f"Limit must be between 1 and 1000, got {limit}"}
        for key in filters:
            if not _SAFE_COLUMN_RE.match(key):
                return {"error": f"Filter key '{key}' contains invalid characters (only letters, digits, underscores; must start with a letter)"}
        for key in filters:
            if key in RESERVED_SUPABASE_PARAMS:
                return {"error": f"Filter key '{key}' is reserved and cannot be used as a filter"}
        url = f"{self._supabase_url}/rest/v1/{table}"
        headers = {
            "apikey": self._supabase_anon_key,
            "Authorization": f"Bearer {self._supabase_anon_key}",
        }
        direction = "asc" if ascending else "desc"
        params: dict = {"select": "*", "limit": limit, "order": f"{order_by}.{direction}"}
        for col, val in filters.items():
            params[col] = f"eq.{val}"
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Supabase query failed for table '{table}': {e}")
            return {"error": str(e)}


def _create_mcp_server(tools: OrchestratorTools):
    """Create and configure the FastMCP server wrapping OrchestratorTools."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("orchestrator")

    @mcp.tool()
    def spawn_worker(
        worker_id: str,
        worker_type: str,
        repo: str,
        objective: str,
        allowed_paths: list[str] | None = None,
        model_name: str = "",
    ) -> str:
        """Spawn a new worker with the given objective."""
        result = tools.spawn_worker(
            worker_id, worker_type, repo, objective,
            allowed_paths, model_name,
        )
        if isinstance(result, dict):
            return json.dumps(result)
        return result

    @mcp.tool()
    def spawn_workers(requests: list[dict]) -> str:
        """Spawn multiple workers with batch grading and parallel startup.

        Each request: {worker_id, worker_type, repo, objective, allowed_paths?, model_name?}
        """
        results = tools.spawn_workers(requests)
        return json.dumps(results)

    @mcp.tool()
    def approve_plan(worker_id: str, rationale: str) -> str:
        """Approve a worker's plan with documented rationale."""
        return tools.approve_plan(worker_id, rationale)

    @mcp.tool()
    def reject_plan(worker_id: str, reason: str) -> str:
        """Reject a worker's plan with documented reason."""
        return tools.reject_plan(worker_id, reason)

    @mcp.tool()
    def send_to_worker(worker_id: str, message: str) -> str | dict:
        """Send a message to a running worker."""
        return tools.send_to_worker(worker_id, message)

    @mcp.tool()
    def update_ledger(objective: str, tasks: list[dict]) -> str:
        """Update the task ledger with current objective and tasks."""
        return tools.update_ledger(objective, tasks)

    @mcp.tool()
    def get_task_ledger() -> str:
        """Read the current task ledger."""
        return json.dumps(tools.get_task_ledger(), indent=2)

    @mcp.tool()
    def get_worker_status(worker_id: str | None = None) -> str:
        """Get status of a specific worker or all running workers."""
        result = tools.get_worker_status(worker_id)
        return json.dumps(result, indent=2, default=str)

    @mcp.tool()
    def get_worker_log(worker_id: str, lines: int = 50) -> str:
        """Read the last N lines of a worker's log."""
        return tools.get_worker_log(worker_id, lines)

    @mcp.tool()
    def kill_worker(worker_id: str, original_objective: str = "", evidence: str = "") -> str:
        """Kill a worker's tmux session and mark it completed. Use after reviewing worker log."""
        result = tools.kill_worker(worker_id, original_objective, evidence)
        if isinstance(result, dict):
            return json.dumps(result)
        return result

    @mcp.tool()
    def restart_daemon() -> str:
        """Send SIGHUP to the IronClaude daemon, triggering a graceful self-restart."""
        return tools.restart_daemon()

    @mcp.tool()
    def restart_mcp() -> str:
        """Restart the MCP server by exec'ing a fresh instance.
        Picks up code changes to orchestrator_mcp.py without restarting the brain session.
        The stdio pipe to Claude Code survives — os.execvp preserves open file descriptors.
        """
        tools.restart_mcp()
        return "restarting"  # never reached

    @mcp.tool()
    def game_launch(resolution: str = "1280x720") -> str:
        """Launch GodotSteam with Artificial Adventures in windowed mode."""
        return tools.game_launch(resolution)

    @mcp.tool()
    def game_screenshot() -> str:
        """Take a screenshot of the game window. Returns JSON with file path to PNG."""
        return tools.game_screenshot()

    @mcp.tool()
    def game_click(x: int, y: int) -> str:
        """Click at screen coordinates (x, y) via cliclick."""
        return tools.game_click(x, y)

    @mcp.tool()
    def game_type(text: str) -> str:
        """Type text at current cursor position via cliclick."""
        return tools.game_type(text)

    @mcp.tool()
    def game_key(key: str) -> str:
        """Press a key or key combination via cliclick. Examples: 'Return', 'Escape', 'space', 'Up'."""
        return tools.game_key(key)

    @mcp.tool()
    def game_kill() -> str:
        """Kill the running Godot process."""
        return tools.game_kill()

    @mcp.tool()
    def get_operator_messages(
        limit: int = 20,
        hours_back: float = 24,
        start_date: str | None = None,
        end_date: str | None = None,
        only_operator: bool = True,
    ) -> str:
        """Read recent Slack messages from the operator (non-bot messages from the channel).

        Args:
            limit: Maximum number of messages to return (default 20).
            hours_back: How far back to look in hours (default 24).
            start_date: Optional ISO date (YYYY-MM-DD) for lower bound. Overrides hours_back lower bound.
            end_date: Optional ISO date (YYYY-MM-DD) for upper bound (inclusive through end of day).
            only_operator: If True (default), return only operator messages. Set False to include bot messages.

        Returns JSON array of messages with keys: text, ts, user.
        Returns empty array if Slack is unavailable or not configured.
        """
        return json.dumps(
            tools.get_operator_messages(
                limit, hours_back, start_date=start_date, end_date=end_date, only_operator=only_operator
            ),
            indent=2,
        )

    @mcp.tool()
    def submit_directive(source_ts: str, source_text: str, interpretation: str) -> str:
        """Submit a new directive for the operator's confirmation.

        Args:
            source_ts: Slack message timestamp of the original message.
            source_text: The operator's original message text.
            interpretation: Your interpretation of what the operator wants done.

        Returns JSON with directive id and status.
        """
        return json.dumps(tools.submit_directive(source_ts, source_text, interpretation))

    @mcp.tool()
    def get_directives(status: str | None = None) -> str:
        """Retrieve directives, optionally filtered by status.

        Args:
            status: Filter by status (pending_confirmation, confirmed, rejected, in_progress, completed).
                    If None, returns all directives.

        Returns JSON array of directive dicts.
        """
        return json.dumps(tools.get_directives(status), indent=2)

    @mcp.tool()
    def get_status_summary() -> str:
        """Get a status summary of all directives grouped by status.

        Returns JSON with four keys:
        - in_progress: directives currently being worked on
        - needs_input: directives awaiting operator confirmation (pending_confirmation)
        - recently_completed: last 5 completed directives
        - active_workers: currently running worker sessions

        Use this for a quick overview of system state without fetching all directives.
        """
        return json.dumps(tools.get_status_summary(), indent=2)

    @mcp.tool()
    def update_directive_status(directive_id: int, status: str) -> str:
        """Update a directive's status.

        Args:
            directive_id: ID of the directive to update.
            status: New status (pending_confirmation, confirmed, rejected, in_progress, completed).

        Returns JSON of the updated directive.
        """
        return json.dumps(tools.update_directive_status(directive_id, status))

    @mcp.tool()
    def debug_slack_connection() -> str:
        """Diagnose Slack connectivity issues.

        Returns JSON with reachability status, message counts, and sample data.
        Use when get_operator_messages returns empty to investigate why.
        """
        return json.dumps(tools.debug_slack_connection(), indent=2)

    @mcp.tool()
    def query_supabase(
        table: str,
        filters: dict = {},
        limit: int = 50,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> str:
        """Query a Supabase telemetry table (read-only SELECT).

        Args:
            table: One of: players, sessions, events, feedback, errors.
            filters: Optional equality filters as {column: value} pairs.
                     Example: {"severity": "error"} filters rows where severity = 'error'.
            limit: Max rows to return (default 50).
            order_by: Column to sort by (default created_at).
            ascending: Sort ascending if True, descending if False (default).

        Returns JSON array of row dicts, or {"error": "message"} on failure.
        """
        return json.dumps(
            tools.query_supabase(table, filters, limit, order_by, ascending),
            indent=2,
        )

    return mcp


def main():
    """Entry point when run as MCP server subprocess."""
    from ironclaude.db import init_db
    from ironclaude.worker_registry import WorkerRegistry
    from ironclaude.tmux_manager import TmuxManager

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/db/ic.db"
    log_dir = os.environ.get("IC_LOG_DIR", "/tmp/ic-logs")
    ledger_path = os.environ.get("IC_LEDGER_PATH", "/tmp/ic/task-ledger.json")

    conn = init_db(db_path)
    registry = WorkerRegistry(conn)
    tmux = TmuxManager(log_dir=log_dir)

    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    slack_channel = os.environ.get("SLACK_CHANNEL_ID", "")
    slack_bot = None
    if slack_token and slack_channel:
        from ironclaude.slack_interface import SlackBot
        user_token = os.environ.get("SLACK_USER_TOKEN", "")
        operator_user_id = os.environ.get("SLACK_OPERATOR_USER_ID", "")
        slack_bot = SlackBot(slack_token, slack_channel, user_token=user_token, operator_user_id=operator_user_id)

    operator_name = os.environ.get("OPERATOR_NAME", "Operator")
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    tools = OrchestratorTools(
        registry, tmux, ledger_path,
        slack_bot=slack_bot, db_conn=conn, operator_name=operator_name,
        supabase_url=supabase_url, supabase_anon_key=supabase_anon_key,
    )

    mcp = _create_mcp_server(tools)
    mcp.run()


if __name__ == "__main__":
    main()
