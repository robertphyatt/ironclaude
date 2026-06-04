# src/ic/brain_client.py
"""Brain client using Claude Agent SDK for structured I/O.

Replaces the tmux-based BrainMonitor with a persistent subprocess that
provides clean JSON communication instead of ANSI terminal output.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import signal
import subprocess
import sys
import queue
import re
import threading
import time
from glob import glob
from pathlib import Path
from ironclaude.signal_forensics import _logged_kill

logger = logging.getLogger("ironclaude.brain")


def _backoff_seconds(attempt: int, max_seconds: float = 300.0) -> float:
    """Exponential backoff: min(2^attempt, max_seconds)."""
    return min(2 ** attempt, max_seconds)


class BrainClient:
    """Manages the brain as a Claude Agent SDK subprocess with structured I/O."""

    ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash"]
    MAX_BUFFER_SIZE = 50 * 1024 * 1024  # 50MB — prevent SDK transport buffer overflow
    SESSION_LOG_DIR = str(Path.home() / ".ironclaude" / "brain-sessions")
    SESSION_LOG_KEEP = 10

    # Orchestrator action tools that require episodic memory search first
    GATED_TOOLS = {
        "mcp__orchestrator__spawn_worker",
        "mcp__orchestrator__spawn_workers",
        "mcp__orchestrator__approve_plan",
        "mcp__orchestrator__reject_plan",
        "mcp__orchestrator__send_to_worker",
        "mcp__orchestrator__kill_worker",
        "mcp__ollama__pull_model",
        "mcp__ollama__remove_model",
        "mcp__ollama__create_model",
    }

    # Git subcommands allowed via Bash (read + staging/commit operations)
    GIT_ALLOWED_COMMANDS = {"log", "diff", "show", "status", "ls-files", "blame", "branch", "add", "commit"}

    MAX_PERMISSION_CORRECTIONS = 3
    PERMISSION_CORRECTION_WINDOW = 600  # seconds (10 minutes)
    BRAIN_PID_FILE = "/tmp/ic/brain.pid"
    CORRECTION_MESSAGE = (
        "Continue without asking for permission. Do not ask "
        "'shall I', 'should I', 'would you like me to', or "
        "similar questions — just proceed with the implied task."
    )
    _PERMISSION_SEEKING_RE = re.compile(
        r'\bshall I\b'
        r'|\bshould I\b'
        r'|\bwould you like me to\b'
        r'|\bdo you want\b'
        r'|\bwant me to\b'
        r'|\blet me know if\b'
        r'|\bwould you like\b'
        r'|\bshall we\b'
        r'|\bshould we\b',
        re.IGNORECASE,
    )

    @staticmethod
    def discover_episodic_memory_path(
        plugin_base: str | None = None,
    ) -> str:
        """Discover the episodic memory MCP server path from the plugin cache.

        Globs for mcp-server-wrapper.js across plugin versions and returns
        the path from the latest version.

        Args:
            plugin_base: Override base path for testing. Defaults to
                ~/.claude/plugins/cache/ironclaude/ironclaude

        Raises:
            FileNotFoundError: If no plugin installation found.
        """
        if plugin_base is None:
            plugin_base = str(
                Path.home() / ".claude" / "plugins" / "cache" / "ironclaude" / "ironclaude"
            )
        pattern = f"{plugin_base}/*/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js"
        matches = sorted(glob(pattern))
        if not matches:
            raise FileNotFoundError(
                f"Episodic memory MCP server not found. "
                f"Pattern: {pattern}. "
                f"Install the ironclaude plugin."
            )
        return matches[-1]  # sorted() puts latest version last

    def __init__(self, timeout_seconds: int = 600, operator_name: str = "Operator", model: str = "opus", effort_level: str = "high"):
        self.timeout_seconds = timeout_seconds
        self._operator_name = operator_name
        self._model = model
        self._effort_level = effort_level
        self.restart_count = 0
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._message_queue: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._session_id: str | None = None
        self._resume_session_id: str | None = None
        self._compacting = False
        self._compaction_complete = False
        self._restart_reason: str = ""
        self._last_response_time = 0.0
        self._last_message_time: float = 0.0
        self._last_restart_time = 0.0
        self._memory_armed: bool = False
        self._wiki_queried: bool = False
        self._system_prompt: str = ""
        self._cwd: str | None = None
        self._episodic_memory_path: str | None = None
        self._orchestrator_path: str | None = None
        self._db_path: str | None = None
        self._research_mcp_path: str | None = None
        self._ollama_mcp_path: str | None = None
        self.max_restarts = 3
        self.restart_window_seconds = 600  # 10 minutes
        self._restart_timestamps: list[float] = []
        self._permission_correction_timestamps: list[float] = []
        self._brain_pid: int | None = None
        self._expected_kill: bool = False
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_usd: float = 0.0
        self._session_log_path: str | None = None
        self._session_log_lock = threading.Lock()
        self._previous_session_context: str | None = None

    def start(self, system_prompt: str, cwd: str | None = None) -> None:
        """Start the brain SDK client in a background thread."""
        self._expected_kill = True
        self._kill_brain_subprocess()   # singleton guard — kill any pre-existing brain
        self._expected_kill = False
        self._system_prompt = system_prompt
        self._cwd = cwd
        os.environ["IC_ROLE"] = "brain"
        # Resolve episodic memory MCP server path (fail hard if not found)
        self._episodic_memory_path = self.discover_episodic_memory_path()
        # Resolve orchestrator MCP server path (optional — may not be installed yet)
        orchestrator_candidate = Path(__file__).parent / "orchestrator_mcp.py"
        if orchestrator_candidate.exists():
            self._orchestrator_path = str(orchestrator_candidate)
            self._db_path = str(Path(__file__).parents[2] / "data" / "db" / "ironclaude.db")
        # Resolve research MCP server path
        research_candidate = Path(__file__).parent / "research_mcp.py"
        if research_candidate.exists():
            self._research_mcp_path = str(research_candidate)
        # Resolve ollama MCP server path
        ollama_candidate = Path(__file__).parent / "ollama_mcp.py"
        if ollama_candidate.exists():
            self._ollama_mcp_path = str(ollama_candidate)
        logger.info(f"MCP servers discovered: orchestrator={self._orchestrator_path}, research={self._research_mcp_path}, ollama={self._ollama_mcp_path}")
        self._init_session_log()
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_event_loop,
            args=(system_prompt, cwd),
            daemon=True,
        )
        self._thread.start()
        # Wait for event loop to be ready
        deadline = time.time() + 10
        while self._loop is None and self._running and time.time() < deadline:
            time.sleep(0.1)

    def _tool_guard_logic(self, tool_name: str, tool_input: dict, context=None) -> tuple[bool, str | None]:
        """Evaluate whether a tool call should be allowed.

        Returns (True, None) to allow, (False, message) to deny.
        Separated from SDK types so tests can call this synchronously.
        """
        # Mutation tools — first-position hard block; brain must delegate to workers
        if tool_name in ("Edit", "Write", "NotebookEdit"):
            return (False, "Brain cannot use mutation tools — route through workers")

        # Research tools — always ungated
        if tool_name.startswith("mcp__research__"):
            return (True, None)

        # Ollama query tools — always ungated (not in GATED_TOOLS)
        if tool_name.startswith("mcp__ollama__") and tool_name not in self.GATED_TOOLS:
            return (True, None)

        # Episodic memory search arms the toggle
        if tool_name.startswith("mcp__episodic-memory__"):
            self._memory_armed = True
            return (True, None)

        # Wiki query arms the wiki toggle
        if tool_name == "mcp__orchestrator__wiki_query":
            self._wiki_queried = True
            return (True, None)

        # Gated orchestrator action tools require BOTH memory search and wiki query
        if tool_name in self.GATED_TOOLS:
            if not self._memory_armed or not self._wiki_queried:
                missing = []
                if not self._memory_armed:
                    missing.append("episodic memory search")
                if not self._wiki_queried:
                    missing.append("wiki query")
                return (False, f"Required before acting: {', '.join(missing)}. What would {self._operator_name} do?")
            stale_age = self._ledger_stale()
            if stale_age is not None:
                return (False, f"Ledger stale ({stale_age}m without update). Call update_ledger to sync current state.")
            self._memory_armed = False
            self._wiki_queried = False
            return (True, None)

        # Bash: only git read-only commands allowed
        if tool_name == "Bash":
            cmd = tool_input.get("command", "").strip()
            _SHELL_METACHARACTERS = (";", "|", "&", "&&", "||", "$(", "`", "\n", "\r", ">", "<", "$")
            if any(c in cmd for c in _SHELL_METACHARACTERS):
                return (False, "Shell metacharacters are not allowed in git commands.")
            if "-c " in cmd:
                return (False, "git -c flag is not allowed in git commands.")
            if not cmd.startswith("git "):
                return (False, "Brain can only run allowed git commands via Bash.")
            # Extract git subcommand (e.g., "git log ..." → "log")
            parts = cmd.split()
            subcommand = parts[1] if len(parts) > 1 else ""
            if subcommand not in self.GIT_ALLOWED_COMMANDS:
                return (False, f"Only allowed git commands permitted: {', '.join(sorted(self.GIT_ALLOWED_COMMANDS))}")
            # Block git commit --amend (prevents rewriting previous commits)
            if subcommand == "commit" and "--amend" in cmd:
                return (False, "git commit --amend is not allowed. Create new commits only.")
            return (True, None)

        # Read-only investigation tools — allow
        if tool_name in ("Read", "Grep", "Glob"):
            return (True, None)

        # Non-gated MCP tools — only known allowlisted prefixes; game tools explicitly denied
        _MCP_ALLOWED_PREFIXES = (
            "mcp__orchestrator__",
            "mcp__episodic-memory__",
            "mcp__ollama__",
            "mcp__research__",
        )
        if tool_name.startswith("mcp__"):
            if tool_name.startswith("mcp__orchestrator__game_"):
                return (False, "Game tools cannot be used directly by the brain. Use spawn_worker for game operations.")
            if any(tool_name.startswith(p) for p in _MCP_ALLOWED_PREFIXES):
                return (True, None)
            return (False, f"MCP tool prefix not in allowlist: {tool_name}")

        # Default deny — brain is read-only, must delegate mutations to workers
        return (False,
            f"Brain cannot use {tool_name} directly. "
            f"To make changes, spawn a worker via spawn_worker."
        )

    def _ledger_stale(self) -> int | None:
        """Return age in minutes if stale, else None.

        Two checks:
        1. File mtime > ledger_staleness_threshold_minutes (stale snapshot)
        2. Any in_progress task has status_set_at > task_staleness_threshold_hours (stuck task)
        Returns None when: cwd unset, file absent, no in_progress tasks, all checks pass.
        """
        if not self._cwd:
            return None
        tasks_md = os.path.join(self._cwd, "wiki", "tasks.md")
        if not os.path.exists(tasks_md):
            return None
        try:
            with open(tasks_md) as f:
                content = f.read()
        except OSError:
            return None
        if '"status": "in_progress"' not in content:
            return None
        threshold_minutes = 30
        task_threshold_hours = 4
        config_path = os.path.expanduser("~/.claude/ironclaude-hooks-config.json")
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            threshold_minutes = int(cfg.get("ledger_staleness_threshold_minutes", 30))
            task_threshold_hours = int(cfg.get("task_staleness_threshold_hours", 4))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
        # Check 1: file mtime (stale snapshot failure mode)
        try:
            mtime = os.path.getmtime(tasks_md)
        except OSError:
            return None
        age_minutes = int((time.time() - mtime) / 60)
        if age_minutes > threshold_minutes:
            return age_minutes
        # Check 2: per-task status_set_at (stuck task failure mode)
        try:
            parts = content.split("## Data")
            if len(parts) >= 2:
                fence = parts[1].find("```")
                if fence != -1:
                    after = parts[1][fence + 3:]
                    nl = after.find("\n")
                    if nl != -1:
                        json_str = after[nl + 1:]
                        end = json_str.find("```")
                        if end != -1:
                            data = json.loads(json_str[:end].strip())
                            now = time.time()
                            for task in data.get("tasks", []):
                                if task.get("status") != "in_progress":
                                    continue
                                ssa = task.get("status_set_at")
                                if not ssa:
                                    continue
                                dt = datetime.fromisoformat(ssa.replace("Z", "+00:00"))
                                task_age_hours = (now - dt.timestamp()) / 3600
                                if task_age_hours > task_threshold_hours:
                                    return int(task_age_hours * 60)
        except Exception:
            pass
        return None

    def _init_session_log(self) -> None:
        """Create a new session log file and run retention cleanup."""
        try:
            log_dir = Path(self.SESSION_LOG_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted(log_dir.glob("*.log"))
            to_delete = existing[:max(0, len(existing) - (self.SESSION_LOG_KEEP - 1))]
            for old in to_delete:
                try:
                    old.unlink()
                except OSError:
                    pass
            filename = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".log"
            self._session_log_path = str(log_dir / filename)
            self._session_log_write(f"SESSION_START restart_count={self.restart_count}")
        except Exception as e:
            logger.error(f"Failed to initialize session log: {e}")
            self._session_log_path = None

    def _session_log_write(self, entry: str) -> None:
        """Append an entry to the session log. Thread-safe. Never raises."""
        if self._session_log_path is None:
            return
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
            with self._session_log_lock:
                with open(self._session_log_path, "a") as f:
                    f.write(f"{ts} {entry}\n")
        except Exception:
            pass

    def _close_session_log(self, reason: str) -> None:
        """Write SESSION_END and clear the session log path."""
        self._session_log_write(f"SESSION_END reason={reason}")
        self._session_log_path = None

    def _read_previous_session_tail(self, log_path: str) -> str:
        """Read the last 20 lines of a session log file. Returns '' on any error."""
        try:
            with open(log_path) as f:
                lines = f.read().splitlines()
            return "\n".join(lines[-20:])
        except Exception:
            return ""

    def _run_event_loop(self, system_prompt: str, cwd: str | None) -> None:
        """Run the async event loop in a background thread with retry."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._message_queue = asyncio.Queue()
        attempt = 0
        try:
            while not self._stop_event.is_set():
                try:
                    loop.run_until_complete(
                        self._brain_session(system_prompt, cwd, resume_session_id=self._resume_session_id)
                    )
                    if self._compacting:
                        self._compacting = False
                        self._compaction_complete = True
                    if self._stop_event.is_set():
                        break  # Intentional shutdown
                    # Session ended cleanly (likely context limit) — try to resume with compaction
                    if self._session_id:
                        self._compacting = True
                        self._resume_session_id = self._session_id
                        self._session_id = None  # Clear for next capture
                        attempt = 0  # Reset backoff for resume
                        logger.info(f"Brain session ended, resuming with compaction (session: {self._resume_session_id})")
                        continue  # Re-enter loop, _brain_session will use resume params
                    # No session_id captured — fall back to fresh restart behavior
                    break
                except Exception as e:
                    self._session_log_write(f"ERROR: {e}")
                    self._compacting = False  # Clear on error to allow restart
                    if self._stop_event.is_set():
                        break
                    # Forensic snapshot: brain died from SIGTERM — log all .venv/bin/python processes
                    err_str = str(e)
                    if "-15" in err_str or "SIGTERM" in err_str or "signal 15" in err_str.lower():
                        try:
                            result = subprocess.run(
                                ["ps", "aux"],
                                capture_output=True, text=True, timeout=5,
                            )
                            venv_procs = [
                                line for line in result.stdout.splitlines()
                                if ".venv/bin/python" in line
                            ]
                            logger.warning(
                                f"Brain exit-15 forensics: {len(venv_procs)} "
                                f".venv/bin/python process(es):\n"
                                + "\n".join(venv_procs)
                            )
                        except Exception as fe:
                            logger.warning(f"Brain exit-15 forensics ps snapshot failed: {fe}")
                    delay = _backoff_seconds(attempt)
                    logger.error(
                        f"Brain session error (attempt {attempt + 1}): {e}. "
                        f"Retrying in {delay:.0f}s..."
                    )
                    attempt += 1
                    self._stop_event.wait(timeout=delay)
        finally:
            self._running = False
            reason = "stop" if self._stop_event.is_set() else "error"
            self._close_session_log(reason)
            loop.close()

    def _check_permission_seeking(self, text: str) -> str | None:
        """Detect permission-seeking language in the final sentence of a brain response.

        Extracts the final sentence (split on sentence-ending punctuation + whitespace),
        matches against known permission-seeking patterns, enforces a 3-per-10-minute
        throttle, logs all outcomes, and returns the correction string if action should
        be taken or None otherwise.
        """
        parts = re.split(r'[.!?]\s+', text.rstrip())
        final_sentence = parts[-1] if parts else text

        if not self._PERMISSION_SEEKING_RE.search(final_sentence):
            return None

        now = time.time()
        cutoff = now - self.PERMISSION_CORRECTION_WINDOW
        self._permission_correction_timestamps = [
            t for t in self._permission_correction_timestamps if t >= cutoff
        ]
        if len(self._permission_correction_timestamps) >= self.MAX_PERMISSION_CORRECTIONS:
            logger.info("Permission-seeking detected but correction throttled (limit reached)")
            return None

        self._permission_correction_timestamps.append(now)
        logger.info("Permission-seeking detected in brain response, sending correction")
        return self.CORRECTION_MESSAGE

    @staticmethod
    def _log_brain_pid_diagnostics(pid: int) -> None:
        """Log brain subprocess process identity for SIGTERM diagnostics."""
        try:
            pgid = os.getpgid(pid)
            # Use ps to get the brain subprocess's actual ppid (not daemon's)
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            )
            ppid = result.stdout.strip() if result.returncode == 0 else "unknown"
            logger.warning(
                f"Brain subprocess diagnostics: pid={pid} ppid={ppid} pgid={pgid}"
            )
        except Exception as e:
            logger.debug(f"Could not log brain process diagnostics: {e}")

    async def _brain_session(self, system_prompt: str, cwd: str | None, resume_session_id: str | None = None) -> None:
        """Run the brain session with streaming I/O."""
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage
        from claude_agent_sdk.types import TextBlock, ResultMessage

        async def _tool_guard(tool_name, tool_input, context):
            from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
            allowed, msg = self._tool_guard_logic(tool_name, tool_input, context)
            self._session_log_write(
                f"TOOL_INVOKE name={tool_name} input={json.dumps(tool_input)[:200]}"
            )
            if allowed:
                return PermissionResultAllow()
            return PermissionResultDeny(message=msg)

        mcp_servers = {
            "episodic-memory": {
                "command": "node",
                "args": [self._episodic_memory_path],
            },
        }
        if self._orchestrator_path and self._db_path:
            mcp_servers["orchestrator"] = {
                "command": sys.executable,
                "args": [self._orchestrator_path, self._db_path],
                "env": {
                    "SUPABASE_URL": os.environ.get("SUPABASE_URL", ""),
                    "SUPABASE_ANON_KEY": os.environ.get("SUPABASE_ANON_KEY", ""),
                    "IC_BRAIN_CWD": self._cwd or "",
                    "IC_MACHINES_CONFIG": str(Path(__file__).parents[3] / "config" / "machines.yaml"),
                },
            }
        if self._research_mcp_path:
            mcp_servers["research"] = {
                "command": sys.executable,
                "args": [self._research_mcp_path],
            }
        if self._ollama_mcp_path:
            mcp_servers["ollama"] = {
                "command": sys.executable,
                "args": [self._ollama_mcp_path],
            }
        logger.info(f"MCP servers configured for brain session: {list(mcp_servers.keys())}")

        if resume_session_id:
            options = ClaudeAgentOptions(
                system_prompt=system_prompt,
                permission_mode="bypassPermissions",
                include_partial_messages=False,
                allowed_tools=self.ALLOWED_TOOLS,
                can_use_tool=_tool_guard,
                cwd=cwd,
                max_buffer_size=self.MAX_BUFFER_SIZE,
                mcp_servers=mcp_servers,
                resume=resume_session_id,
                fork_session=True,
                effort=self._effort_level,
                model=f"{self._model}[1m]",
                betas=["context-1m-2025-08-07"],
                setting_sources=["project", "local"],
            )
        else:
            options = ClaudeAgentOptions(
                system_prompt=system_prompt,
                permission_mode="bypassPermissions",
                include_partial_messages=False,
                allowed_tools=self.ALLOWED_TOOLS,
                can_use_tool=_tool_guard,
                cwd=cwd,
                max_buffer_size=self.MAX_BUFFER_SIZE,
                mcp_servers=mcp_servers,
                effort=self._effort_level,
                model=f"{self._model}[1m]",
                betas=["context-1m-2025-08-07"],
                setting_sources=["project", "local"],
            )

        logger.info("Brain session started")

        async def message_generator():
            while self._running:
                try:
                    msg = await asyncio.wait_for(
                        self._message_queue.get(), timeout=1.0
                    )
                    logger.info(f"Brain received message ({len(msg)} chars)")
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": msg},
                    }
                    self._session_log_write(f"MSG_RECV chars={len(msg)} preview={msg[:100]!r}")
                except asyncio.TimeoutError:
                    continue

        async def _discover_and_write_pid() -> None:
            await asyncio.sleep(2.0)
            try:
                result = subprocess.run(
                    ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
                    capture_output=True, text=True, timeout=5,
                )
                pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
                if pids:
                    self._brain_pid = pids[0]
                    pid_dir = Path(self.BRAIN_PID_FILE).parent
                    pid_dir.mkdir(parents=True, exist_ok=True)
                    Path(self.BRAIN_PID_FILE).write_text(str(pids[0]))
                    logger.info(f"Brain subprocess PID {pids[0]} written to {self.BRAIN_PID_FILE}")
                    BrainClient._log_brain_pid_diagnostics(pids[0])
            except Exception as e:
                logger.warning(f"Failed to discover brain subprocess PID: {e}")

        pid_task = asyncio.create_task(_discover_and_write_pid())
        try:
            async for message in query(prompt=message_generator(), options=options):
                if isinstance(message, ResultMessage):
                    self._session_id = message.session_id
                    if message.usage:
                        self._total_input_tokens += message.usage.get("input_tokens", 0) or 0
                        self._total_output_tokens += message.usage.get("output_tokens", 0) or 0
                    if message.total_cost_usd is not None:
                        self._total_cost_usd += message.total_cost_usd
                if isinstance(message, AssistantMessage):
                    text_parts = []
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                    if text_parts:
                        full_text = "\n\n".join(text_parts)
                        self._response_queue.put(full_text)
                        self._last_response_time = time.time()
                        self._session_log_write(f"MSG_SEND chars={len(full_text)} preview={full_text[:100]!r}")
                        logger.info(f"Brain response received ({len(full_text)} chars)")
                        correction = self._check_permission_seeking(full_text)
                        if correction is not None:
                            await self._message_queue.put(correction)
        finally:
            pid_task.cancel()
            try:
                await pid_task
            except (asyncio.CancelledError, Exception):
                pass

    def send_message(self, text: str) -> bool:
        """Send a message to the brain. Thread-safe."""
        if not self._running or self._loop is None or self._message_queue is None:
            return False
        self._last_message_time = time.time()
        asyncio.run_coroutine_threadsafe(
            self._message_queue.put(text), self._loop
        )
        return True

    def get_pending_responses(self) -> list[str]:
        """Drain all pending brain responses. Thread-safe."""
        responses = []
        while True:
            try:
                responses.append(self._response_queue.get_nowait())
            except queue.Empty:
                break
        return responses

    def is_alive(self) -> bool:
        """Check if the brain subprocess is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def needs_restart(self) -> bool:
        """Check if brain needs restart (dead or unresponsive)."""
        if self._compacting:
            return False  # Don't interfere with in-progress compaction
        if not self.is_alive():
            self._restart_reason = "dead (thread not alive)"
            return True
        # Timeout: message sent but no response within timeout_seconds
        if (
            self._last_message_time > self._last_response_time
            and time.time() - self._last_message_time > self.timeout_seconds
        ):
            elapsed = time.time() - self._last_message_time
            self._restart_reason = f"timeout (no response in {elapsed:.0f}s)"
            logger.warning(
                f"Brain timeout: no response in {elapsed:.0f}s "
                f"after message sent at {self._last_message_time:.0f}"
            )
            return True
        return False

    def was_compacted(self) -> bool:
        """Check if the last restart was a compaction resume (not a fresh start)."""
        return self._resume_session_id is not None

    def check_compaction_complete(self) -> bool:
        """Check and clear the compaction-complete flag. Returns True once after compaction."""
        if self._compaction_complete:
            self._compaction_complete = False
            return True
        return False

    @property
    def restart_reason(self) -> str:
        """The reason for the most recent needs_restart() == True."""
        return self._restart_reason

    def circuit_breaker_tripped(self) -> bool:
        """Check if too many restarts have occurred in the time window."""
        now = time.time()
        self._restart_timestamps = [
            t for t in self._restart_timestamps
            if now - t < self.restart_window_seconds
        ]
        return len(self._restart_timestamps) >= self.max_restarts

    def restart(self, system_prompt: str, cwd: str | None = None) -> bool:
        """Restart the brain session."""
        prev_log = self._session_log_path
        context = self._read_previous_session_tail(prev_log) if prev_log else ""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost_usd = 0.0
        self._resume_session_id = None
        self._session_id = None
        self.shutdown()
        self._last_message_time = 0.0
        self._last_response_time = 0.0
        self.start(system_prompt, cwd)
        if self.is_alive():
            self.restart_count += 1
            self._last_restart_time = time.time()
            self._restart_timestamps.append(time.time())
            logger.info(f"Brain restarted (count: {self.restart_count})")
            if context:
                self.send_message(f"[DIAGNOSTIC] Previous session log (last 20 entries):\n{context}")
            return True
        logger.error("Failed to restart brain session")
        return False

    def get_token_usage(self) -> dict:
        """Return accumulated token usage since last restart."""
        total = self._total_input_tokens + self._total_output_tokens
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total_tokens": total,
            "cost_usd": self._total_cost_usd,
        }

    def _kill_brain_subprocess(self) -> None:
        """Kill any running brain subprocess. Three-tier lookup: instance var → PID file → pgrep.

        Unconditionally clears _brain_pid and removes BRAIN_PID_FILE when done.
        """
        import traceback
        try:
            caller = "".join(traceback.format_stack()[-3:-1]).strip()
            logger.info(f"_kill_brain_subprocess called from:\n{caller}")
        except Exception as _tb_err:
            logger.warning(f"_kill_brain_subprocess traceback capture failed: {_tb_err}")
        pid = self._brain_pid

        # Fall back to PID file
        if pid is None:
            pid_file = Path(self.BRAIN_PID_FILE)
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                except (ValueError, OSError):
                    pid = None

        # Fall back to pgrep scan — collect ALL matches
        if pid is None:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", "claude.*stream-json.*Orchestrator"],
                    capture_output=True, text=True, timeout=5,
                )
                kill_pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
                if kill_pids:
                    logger.info(f"Found {len(kill_pids)} orphan brain subprocess(es) via pgrep: {kill_pids}")
            except Exception as e:
                logger.warning(f"pgrep scan for brain subprocess failed: {e}")
                kill_pids = []
        else:
            kill_pids = [pid]

        for kpid in kill_pids:
            try:
                _logged_kill(kpid, signal.SIGTERM, f"kill_brain_subprocess SIGTERM pid={kpid}")
                logger.info(f"Sent SIGTERM to brain subprocess PID {kpid}")
                for _ in range(30):
                    time.sleep(0.1)
                    try:
                        os.kill(kpid, 0)
                    except ProcessLookupError:
                        break
                else:
                    try:
                        _logged_kill(kpid, signal.SIGKILL, f"kill_brain_subprocess SIGKILL escalation pid={kpid}")
                        logger.info(f"Sent SIGKILL to brain subprocess PID {kpid}")
                    except ProcessLookupError:
                        pass
            except ProcessLookupError:
                logger.debug(f"Brain subprocess PID {kpid} already dead")
            except PermissionError:
                logger.warning(f"No permission to kill brain subprocess PID {kpid}")

        # Unconditional cleanup
        self._brain_pid = None
        try:
            Path(self.BRAIN_PID_FILE).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"Failed to remove brain PID file: {e}")

    def shutdown(self) -> None:
        """Shut down the brain client."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._thread = None
        self._expected_kill = True
        self._kill_brain_subprocess()   # force-kill subprocess after thread join
        self._expected_kill = False
        self._loop = None
        self._message_queue = None
