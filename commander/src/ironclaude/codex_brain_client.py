"""Codex Brain client — a persistent `codex app-server --stdio` JSON-RPC client.

Why app-server and NOT one-shot `codex exec`
--------------------------------------------
The daemon's Brain contract is multi-turn: `main.py` starts one Brain, then feeds
it messages for the lifetime of the process (`send_message`), drains its output
(`get_pending_responses`), monitors liveness (`is_alive` / `needs_restart`), and
restarts it in place (`restart`). A one-shot `codex exec` invocation exits after a
single turn and carries no thread state forward, so it cannot satisfy that
contract without re-establishing context on every message. `codex app-server`
keeps a long-lived process with a persistent thread, which maps one-to-one onto
the `BrainClient` surface this class is a drop-in replacement for.

Why `threadSource` MUST be `"user"`
-----------------------------------
`thread/start` takes a `threadSource` parameter. If it is missing (or is any value
other than `"user"`), the codex Brain still starts, but EVERY MCP tool call it
makes fails with `Missing or invalid Codex thread_source`. Since the IronClaude
Brain is essentially an MCP-driven orchestrator (state-manager, episodic-memory,
research, ollama), a Brain without `threadSource="user"` is dead on arrival while
looking superficially healthy. It is a hard constant (`THREAD_SOURCE`), never a
caller-supplied option, and a failing `thread/start` is never retried without it.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from ironclaude.config import DEFAULTS

logger = logging.getLogger(__name__)

# Codex tier->model map — single source of truth is config.DEFAULTS. A tier name
# (haiku/sonnet/opus) resolves to its codex model; a value already in codex-model
# form passes through, so BRAIN_MODEL may be a tier OR a literal codex model.
_CODEX_TIER_MODELS = DEFAULTS["providers"]["clients"]["codex"]["models"]

# The GATED_TOOLS hook distinguishes the Brain from workers by IC_ROLE. The Claude
# Brain sets this in-process (brain_client.py); the codex Brain is a subprocess, so the
# marker is scoped to its spawn — mutating the daemon's own environment would leak
# IC_ROLE=brain into later worker spawns, which must be IC_ROLE=worker.
_BRAIN_ROLE_ENV = {"IC_ROLE": "brain"}
_BRAIN_GATE_ROOT = Path("/tmp/ic/codex-brain-gate")

# Optional Brain MCP servers. Claude registers these when their files are present
# (brain_client.py:279-286); codex reaches them only through -c overrides.
_OPTIONAL_MCP_SERVERS = ("research", "ollama")

# NOTE: `BrainClient.discover_episodic_memory_path` is deliberately NOT ported.
# It resolves a path inside the Claude Code plugin cache
# (~/.claude/plugins/.../episodic-memory) to build Claude-specific MCP server
# config. It is Claude-plugin-cache specific, has no Codex equivalent, and
# `main.py` never calls it on the instance — it is only used internally by
# `BrainClient.start`. Adding a Codex stub for it would be surface without
# meaning.

# Repeated turn errors mean the thread is wedged even though the process is
# alive; past this many the daemon's restart path must take over.
_TURN_FAILURE_RESTART_THRESHOLD = 3

# Rolling cap on the message buffer; it only holds handshake/await responses, so this is
# far above steady-state need and prevents unbounded growth over a long-lived Brain.
_MESSAGE_BUFFER_CAP = 1000

# Bounded wait for the turn/start response so a rejected turn is detected without materially
# blocking a working Brain (a JSON-RPC error arrives in ms; a success ack returns fast).
_TURN_START_ACK_TIMEOUT = 1.0

_ORCHESTRATOR_REQUIRED_TOOLS = frozenset({
    "wiki_query",
    "get_operator_messages",
    "update_ledger",
    "spawn_worker",
    "spawn_workers",
    "approve_plan",
    "reject_plan",
    "send_to_worker",
    "kill_worker",
})
_ORCHESTRATOR_STARTUP_TIMEOUT = 120.0


# Ported byte-for-byte from brain_client._tool_guard_logic (:357-374) + GIT_ALLOWED_COMMANDS
# (:154). Approved codex commands run UNSANDBOXED, so the metachar/`-c `/amend blocks are
# load-bearing. Returns True ONLY for an allowlisted git command with no shell metacharacters.
_SHELL_METACHARACTERS = (";", "|", "&", "&&", "||", "$(", "`", "\n", "\r", ">", "<", "$")
_GIT_ALLOWED_COMMANDS = frozenset(
    {"log", "diff", "show", "status", "ls-files", "blame", "branch", "add", "commit"}
)


def _git_command_allowed(cmd: str) -> bool:
    if not cmd:
        return False
    cmd = cmd.strip()
    if any(c in cmd for c in _SHELL_METACHARACTERS):
        return False
    if "-c " in cmd:
        return False
    if not cmd.startswith("git "):
        return False
    parts = cmd.split()
    subcommand = parts[1] if len(parts) > 1 else ""
    if subcommand not in _GIT_ALLOWED_COMMANDS:
        return False
    if subcommand == "commit" and "--amend" in cmd:
        return False
    return True


class CodexBrainClient:
    """Drop-in `BrainClient` replacement backed by `codex app-server --stdio`."""

    THREAD_SOURCE = "user"

    def __init__(
        self,
        timeout_seconds: int = 600,
        operator_name: str = "Operator",
        model: str = "gpt-5.6-terra",
        effort_level: str = "high",
        on_fable_unavailable_transition=None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._operator_name = operator_name
        self._model = model
        self._effort_level = effort_level
        self._on_fable_unavailable_transition = on_fable_unavailable_transition

        # Restart bookkeeping — read directly by main.py's restart supervisor.
        self.max_restarts = 3
        self.restart_window_seconds = 600  # 10 minutes
        self.restart_count = 0
        self._restart_timestamps: list[float] = []
        self._turn_failures: list[dict] = []
        self._restart_reason = ""

        # Lifecycle state.
        self._stop_event = threading.Event()
        self._running = False
        self._brain_pid: int | None = None
        self._responses: queue.Queue[str] = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._thread_id: str | None = None
        self._token_usage: dict | None = None

        # Protocol plumbing. Nothing here spawns a process — construction is
        # side-effect free; all subprocess work happens in `start()`.
        self._system_prompt: str | None = None
        self._cwd: str | None = None
        self._brain_gate_session = uuid.uuid4().hex
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._messages: list[dict] = []
        # Rolling-window bookkeeping for `_messages` only. `_await_response` deliberately
        # does NOT advance this — it scans a snapshot, so concurrent waiters can't consume
        # one another's responses.
        self._cursor = 0
        self._cursor_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._compaction_complete = False
        self._last_activity = 0.0
        # Hang detection: a turn is "in flight" when _last_message_time > _last_response_time.
        self._last_message_time = 0.0
        self._last_response_time = 0.0
        self._mcp_status_condition = threading.Condition()
        self._mcp_startup_status: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Protocol seams
    # ------------------------------------------------------------------
    def _thread_start_params(self, cwd: str | None = None) -> dict:
        """Params for `thread/start`. `threadSource` is mandatory — see module docstring."""
        params: dict = {"threadSource": self.THREAD_SOURCE}
        if cwd:
            params["cwd"] = cwd
        return params

    def _resolve_model(self) -> str:
        """Map a tier name (haiku/sonnet/opus) to its codex model; pass a value
        already in codex-model form through unchanged."""
        return _CODEX_TIER_MODELS.get(self._model, self._model)

    def _app_server_argv(self) -> list[str]:
        """Argv for the app-server. Forces read-only sandbox + on-request approval so
        EVERY model mutation escalates to the approval channel (directive #175), and
        pins the model so the codex Brain runs the operator-ruled tier rather than
        whatever ~/.codex/config.toml defaults to. `-c` is an app-server option
        (codex app-server --help); values are parsed as TOML strings."""
        return [
            "codex", "app-server",
            "-c", 'sandbox_mode="read-only"',
            "-c", 'approval_policy="on-request"',
            "-c", f'model="{self._resolve_model()}"',
            "-c", f'model_reasoning_effort="{self._effort_level}"',
            *self._orchestrator_mcp_overrides(),
            *self._optional_mcp_overrides(),
            "--stdio",
        ]

    def _orchestrator_source_path(self) -> Path:
        return Path(__file__).parent / "orchestrator_mcp.py"

    def _commander_root(self) -> Path:
        return Path(__file__).parents[2]

    def _orchestrator_mcp_overrides(self) -> list[str]:
        source = self._orchestrator_source_path()
        commander_root = self._commander_root()
        database = commander_root / "data" / "db" / "ironclaude.db"
        machines = Path(__file__).parents[3] / "config" / "machines.yaml"
        values = [
            f"mcp_servers.orchestrator.command={json.dumps(sys.executable)}",
            "mcp_servers.orchestrator.args="
            + json.dumps([str(source), str(database)], separators=(",", ":")),
            f"mcp_servers.orchestrator.cwd={json.dumps(str(commander_root))}",
            "mcp_servers.orchestrator.enabled=true",
            'mcp_servers.orchestrator.default_tools_approval_mode="approve"',
            "mcp_servers.orchestrator.startup_timeout_sec=120",
            'mcp_servers.orchestrator.env_vars=["SUPABASE_URL","SUPABASE_ANON_KEY"]',
            "mcp_servers.orchestrator.env.IC_BRAIN_CWD="
            + json.dumps(self._cwd or ""),
            "mcp_servers.orchestrator.env.IC_MACHINES_CONFIG="
            + json.dumps(str(machines)),
        ]
        return [part for value in values for part in ("-c", value)]

    def _optional_mcp_source_path(self, name: str) -> Path:
        return Path(__file__).parent / f"{name}_mcp.py"

    def _optional_mcp_overrides(self) -> list[str]:
        """``-c`` pairs for the optional Brain MCP servers, mirroring the Claude path.

        Existence-guarded like brain_client.py:280-286, so a trimmed install degrades the
        same way on both clients. Deliberately NO default_tools_approval_mode: cloning the
        orchestrator arm would auto-approve ollama's pull/remove/create_model, which are the
        tools codex-brain-gated-actions.sh:63-65 exists to gate.
        """
        values: list[str] = []
        for name in _OPTIONAL_MCP_SERVERS:
            source = self._optional_mcp_source_path(name)
            if not source.exists():
                continue
            values.extend([
                f"mcp_servers.{name}.command={json.dumps(sys.executable)}",
                f"mcp_servers.{name}.args="
                + json.dumps([str(source)], separators=(",", ":")),
                f"mcp_servers.{name}.enabled=true",
            ])
        return [part for value in values for part in ("-c", value)]

    def _fail_start(self, reason: str) -> None:
        self._running = False
        self._restart_reason = reason
        logger.error(reason)
        self._responses.put(f"[CODEX BRAIN ERROR] {reason}")
        self.shutdown()

    def _preflight_orchestrator(self) -> str | None:
        source = self._orchestrator_source_path()
        if not source.is_file():
            return f"orchestrator MCP source missing: {source}"
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import ironclaude.orchestrator_mcp"],
                cwd=str(self._commander_root()),
                env=self._spawn_env(),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"orchestrator MCP import preflight failed: {exc}"
        if result.returncode != 0:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"exit {result.returncode}"
            )
            return f"orchestrator MCP import preflight failed: {detail}"
        return None

    def _extract_thread_id(self, result: dict) -> str | None:
        """The thread id from a thread/start response. The real shape nests it under
        result['thread']['id'] (verified live); the legacy top-level keys are kept as
        fallbacks. Returns None if no id is present (⇒ start() must fail loud)."""
        if not isinstance(result, dict):
            return None
        thread = result.get("thread")
        if isinstance(thread, dict) and thread.get("id"):
            return thread["id"]
        return result.get("threadId") or result.get("thread_id")

    def _handle_event(self, msg: dict) -> None:
        """Single event-dispatch seam for every server->client notification.

        Unknown methods and malformed payloads are ignored rather than raised —
        the reader thread must never die on an unexpected event.
        """
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if method == "mcpServer/startupStatus/updated":
            name = params.get("name")
            status = params.get("status")
            if isinstance(name, str) and isinstance(status, str):
                with self._mcp_status_condition:
                    self._mcp_startup_status[name] = {
                        "threadId": params.get("threadId"),
                        "status": status,
                        "error": params.get("error"),
                        "failureReason": params.get("failureReason"),
                    }
                    self._mcp_status_condition.notify_all()
            return

        if method == "item/completed":
            self._last_response_time = time.time()
            item = params.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text")
                if text:
                    # Enqueue the RAW text — main.py relays this verbatim.
                    self._responses.put(text)
            return

        if method == "turn/failed":
            self._last_response_time = time.time()
            self._turn_failures.append(msg)
            error = params.get("error") or {}
            detail = ""
            if isinstance(error, dict):
                detail = error.get("message") or ""
            elif isinstance(error, str):
                detail = error
            if not detail:
                detail = "unknown error"
            # Must reach the operator: a swallowed turn/failed looks like a Brain
            # that simply went quiet.
            self._responses.put(f"[CODEX BRAIN ERROR] turn failed: {detail}")
            return

        # `thread/tokenUsage/updated` is the event codex app-server actually emits
        # (verified against the generated protocol schema and a live turn). The
        # remaining names are defensive only — matching a name codex never sends
        # leaves get_token_usage() returning None forever, and main.py:2582
        # tolerates None, so the failure is SILENT.
        if method in (
            "thread/tokenUsage/updated",
            "turn/completed",
            "thread/tokenCount",
            "turn/tokenCount",
        ):
            usage = params.get("usage") or params.get("tokenUsage") or {}
            if isinstance(usage, dict) and usage:
                self._token_usage = usage
            self._last_activity = time.time()
            self._last_response_time = time.time()
            if method == "turn/completed":
                # A completed turn breaks the consecutive-failure streak (#8).
                self._turn_failures = []
            return

        # Any other event: safely ignored.

    # ------------------------------------------------------------------
    # Server->client request handling (FAIL-CLOSED)
    # ------------------------------------------------------------------
    # Conformance with standing operator directive #175: the Brain must never
    # mutate state directly, and the only unbypassable enforcement is refusing at
    # the request boundary. codex app-server asks the client to approve command
    # execution / patch application / permission grants over this same channel that
    # also carries MCP server-trust elicitation. We ACCEPT only elicitation (needed
    # for the Brain to reach state-manager) and DECLINE every approval — and every
    # unrecognised method (default-deny: a future codex version may add approval
    # types we have not seen). A read-and-reason Brain is safe; an
    # execute-and-patch Brain via auto-approve is exactly the hole #175 closed.

    # The one accept-class method (MCP server-trust elicitation).
    _ELICITATION_METHOD = "mcpServer/elicitation/request"

    # Per-method decline response bodies, verified from the generated schema
    # (ServerRequest.json method enums + each *Response.json). The shapes are
    # NOT uniform: ReviewDecision (exec/patch) uses {"decision":"denied"};
    # Command/FileChange approval decisions use {"decision":"decline"};
    # permission grants use {"permissions":{}} (grant nothing). A mis-shaped
    # decline is silently rejected by codex and would stall the turn, so each is
    # pinned exactly.
    _DECLINE_BODIES = {
        "execCommandApproval": {"decision": "denied"},
        "applyPatchApproval": {"decision": "denied"},
        "item/commandExecution/requestApproval": {"decision": "decline"},
        "item/fileChange/requestApproval": {"decision": "decline"},
        "item/permissions/requestApproval": {"permissions": {}},
        "item/tool/requestUserInput": {"answers": {}},
    }
    # Fallback decline body for any recognised-but-unmapped or unknown method.
    # No universal decline shape exists; this is the most common (ReviewDecision)
    # deny. If codex rejects it for an unmapped type, the decline is still surfaced
    # to the operator, so the failure is visible rather than a silent auto-approve.
    _DEFAULT_DECLINE_BODY = {"decision": "denied"}

    # The two exec-approval methods that carry a shell command → allowlist-gated approve.
    # Approve bodies differ per method (verified from schema): v2 accept, v1 approved.
    _EXEC_APPROVAL_APPROVE = {
        "item/commandExecution/requestApproval": {"decision": "accept"},
        "execCommandApproval": {"decision": "approved"},
    }

    def _extract_exec_command(self, method: str, params: dict) -> str | None:
        """Command string for the git predicate, or None (⇒ fail-closed decline).
        v2 command is a STRING; v1 command is an ARRAY of strings."""
        cmd = params.get("command")
        if method == "item/commandExecution/requestApproval":
            return cmd if isinstance(cmd, str) else None
        if method == "execCommandApproval":
            if isinstance(cmd, list) and all(isinstance(x, str) for x in cmd):
                return " ".join(cmd)
            return None
        return None

    def _classify_server_request(self, msg: dict) -> str:
        """'accept' ONLY for MCP elicitation; 'decline' for everything else.

        Default-deny is intentional: an approval type we have never seen must never
        be auto-approved.
        """
        if msg.get("method") == self._ELICITATION_METHOD:
            return "accept"
        return "decline"

    def _note_declined_request(self, msg: dict) -> None:
        """Surface a declined request to the operator. A blocked Brain must look
        blocked, not silently starved. Never counts as a turn failure."""
        method = msg.get("method")
        self._responses.put(
            f"[CODEX BRAIN BLOCKED] declined {method} — the Brain may not execute "
            f"commands or apply patches"
        )

    def _handle_server_request(self, msg: dict) -> None:
        """Answer a server->client REQUEST fail-closed. Owns the gate: a message
        without a non-null id or without a method is a notification or a JSON-RPC
        response, not a server request — return without answering it. The two
        exec-approval methods are allowlist-gated (git only); everything else is
        declined/accepted exactly as before (defense-in-depth). Sandbox-agnostic:
        never approves on the assumption the sandbox already vetted a command."""
        if not isinstance(msg, dict):
            return
        if msg.get("id") is None or msg.get("method") is None:
            return
        method = msg["method"]
        if self._classify_server_request(msg) == "accept":
            result = {"action": "accept"}
        elif method in self._EXEC_APPROVAL_APPROVE:
            params = msg.get("params")
            params = params if isinstance(params, dict) else {}
            cmd = self._extract_exec_command(method, params)
            if cmd is not None and _git_command_allowed(cmd):
                result = self._EXEC_APPROVAL_APPROVE[method]
            else:
                result = self._DECLINE_BODIES.get(method, self._DEFAULT_DECLINE_BODY)
                self._note_declined_request(msg)
        else:
            result = self._DECLINE_BODIES.get(method, self._DEFAULT_DECLINE_BODY)
            self._note_declined_request(msg)
        self._write({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------
    def _alloc_id(self) -> int:
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        return request_id

    def _write(self, payload: dict) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        line = json.dumps(payload) + "\n"
        try:
            with self._write_lock:
                proc.stdin.write(line)
                proc.stdin.flush()
            return True
        except (BrokenPipeError, ValueError, OSError) as exc:
            logger.error(f"codex app-server write failed: {exc}")
            self._restart_reason = f"stdin write failed: {exc}"
            return False

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if self._stop_event.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            self._record_message(msg)
            # Server->client requests are answered fail-closed; the gate lives in
            # _handle_server_request, which drops non-requests (notifications /
            # responses) without answering them.
            self._handle_server_request(msg)
            self._handle_event(msg)
        self._running = False

    def _stderr_loop(self) -> None:
        """Drain stderr continuously.

        NEVER call `.read()` on a live process's stderr — it blocks until EOF and,
        if the pipe buffer fills first, the child deadlocks writing to it.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            if self._stop_event.is_set():
                break
            line = line.rstrip()
            if line:
                logger.debug(f"codex app-server stderr: {line}")

    def _record_message(self, msg: dict) -> None:
        """Append a message and bound the buffer to a rolling window, shifting the cursor
        down by the dropped count so it keeps pointing at the same logical entry.
        `_await_response` no longer uses the cursor (it scans a snapshot under this same
        lock), so a drop cannot make an in-flight await mis-index."""
        with self._cursor_lock:
            self._messages.append(msg)
            overflow = len(self._messages) - _MESSAGE_BUFFER_CAP
            if overflow > 0:
                del self._messages[:overflow]
                self._cursor = max(0, self._cursor - overflow)

    def _await_response(self, request_id: int, timeout: float) -> dict | None:
        """Wait for the response to `request_id` without consuming shared state.

        Scans a snapshot of the buffer under the lock instead of advancing a shared
        cursor, so CONCURRENT (or out-of-order) waiters cannot skip past one another's
        responses — a race that made `send_message` report a rejected turn/start as
        success.

        A response matches only its own request: notifications and server->client
        requests are excluded by `method is None`, request ids are monotonic
        (`_alloc_id` never resets), and `start()` clears `_messages`, so no stale
        message can re-match.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._cursor_lock:
                for msg in self._messages:
                    if msg.get("id") == request_id and msg.get("method") is None:
                        return msg
            if self._proc is not None and self._proc.poll() is not None:
                return None
            time.sleep(0.02)
        return None

    def _await_orchestrator_ready(self, timeout: float) -> str | None:
        thread_id = self._thread_id
        if not thread_id:
            return "orchestrator MCP startup missing thread id"
        deadline = time.time() + timeout
        with self._mcp_status_condition:
            while True:
                state = self._mcp_startup_status.get("orchestrator")
                if state is not None and state.get("threadId") == thread_id:
                    status = state.get("status")
                    if status == "ready":
                        return None
                    if status in ("failed", "cancelled"):
                        detail = (
                            state.get("error")
                            or state.get("failureReason")
                            or "no detail"
                        )
                        return f"orchestrator MCP startup {status}: {detail}"
                if self._proc is not None and self._proc.poll() is not None:
                    return "orchestrator MCP startup failed: app-server exited"
                remaining = deadline - time.time()
                if remaining <= 0:
                    return "orchestrator MCP startup timed out waiting for ready"
                self._mcp_status_condition.wait(timeout=min(remaining, 0.1))

    def _list_mcp_server_inventory(
        self,
    ) -> tuple[dict[str, dict] | None, str | None]:
        thread_id = self._thread_id
        if not thread_id:
            return None, "orchestrator MCP inventory missing thread id"
        inventory: dict[str, dict] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            request_id = self._alloc_id()
            params: dict = {"detail": "full", "threadId": thread_id}
            if cursor is not None:
                params["cursor"] = cursor
            if not self._write({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "mcpServerStatus/list",
                "params": params,
            }):
                return None, "orchestrator MCP inventory write failed"

            response = self._await_response(
                request_id, timeout=_ORCHESTRATOR_STARTUP_TIMEOUT
            )
            if response is None:
                return None, "orchestrator MCP inventory timed out"
            error = response.get("error")
            if error:
                if isinstance(error, dict):
                    detail = error.get("message") or json.dumps(error, sort_keys=True)
                else:
                    detail = str(error)
                return None, f"orchestrator MCP inventory failed: {detail}"

            result = response.get("result")
            if not isinstance(result, dict):
                return None, "orchestrator MCP inventory response malformed: result"
            data = result.get("data")
            if not isinstance(data, list):
                return None, "orchestrator MCP inventory response malformed: data"

            for server in data:
                if not isinstance(server, dict):
                    return None, "orchestrator MCP inventory response malformed: server"
                name = server.get("name")
                tools = server.get("tools")
                if not isinstance(name, str) or not isinstance(tools, dict):
                    return None, "orchestrator MCP inventory response malformed: server fields"
                if name in inventory:
                    return None, f"orchestrator MCP inventory duplicated server: {name}"
                inventory[name] = server

            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return inventory, None
            if not isinstance(next_cursor, str) or not next_cursor:
                return None, "orchestrator MCP inventory response malformed: nextCursor"
            if next_cursor in seen_cursors:
                return None, "orchestrator MCP inventory repeated cursor"
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def _verify_orchestrator_mcp(self) -> str | None:
        ready_error = self._await_orchestrator_ready(
            timeout=_ORCHESTRATOR_STARTUP_TIMEOUT
        )
        if ready_error is not None:
            return ready_error

        inventory, inventory_error = self._list_mcp_server_inventory()
        if inventory_error is not None:
            return inventory_error
        if inventory is None or "orchestrator" not in inventory:
            return "orchestrator MCP inventory missing server"
        tools = inventory["orchestrator"].get("tools")
        if not isinstance(tools, dict):
            return "orchestrator MCP inventory malformed tools"
        missing = sorted(_ORCHESTRATOR_REQUIRED_TOOLS - set(tools))
        if missing:
            return f"orchestrator MCP inventory missing tools: {', '.join(missing)}"
        # Parity with Claude Brain, which refuses to start when episodic-memory cannot be
        # found (brain_client.discover_episodic_memory_path raises FileNotFoundError).
        # Claude's mandate is EXISTENCE ONLY — it never inspects which tools the server
        # exposes and never awaits a readiness notification — so this must not either, or
        # Codex becomes stricter than Claude, which is not parity. Codex loads
        # installed-plugin MCP servers implicitly (probe-verified 2026-07-28), so presence
        # in the inventory is the equivalent evidence to Claude's path glob.
        if "episodic-memory" not in inventory:
            return "episodic-memory MCP inventory missing server"
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _spawn_env(self) -> dict:
        """Environment for the app-server: the daemon's, plus the Brain role marker.

        `env=` REPLACES inheritance, so the ambient environment is copied forward
        explicitly; without the copy the app-server would lose auth/PATH/config.
        """
        return {
            **os.environ,
            **_BRAIN_ROLE_ENV,
            "IRONCLAUDE_CLIENT": "codex",
            "IRONCLAUDE_BRAIN_GATE_SESSION": self._brain_gate_session,
        }

    def _reset_brain_gate_startup_state(self) -> None:
        """Reset startup-only gates while retaining same-client memory/wiki arms."""
        state_dir = _BRAIN_GATE_ROOT / self._brain_gate_session
        for marker in ("lookback-slack", "lookback-ledger"):
            try:
                (state_dir / marker).unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Failed to reset Codex Brain gate marker %s: %s", marker, exc)

    def start(self, system_prompt: str, cwd: str | None = None) -> None:
        """Spawn the app-server and open a thread. Never raises — records a reason."""
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._stop_event.clear()
        self._restart_reason = ""
        self._reset_brain_gate_startup_state()
        with self._mcp_status_condition:
            self._mcp_startup_status.clear()

        preflight_error = self._preflight_orchestrator()
        if preflight_error is not None:
            self._fail_start(preflight_error)
            return

        try:
            self._proc = subprocess.Popen(
                self._app_server_argv(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=cwd,
                env=self._spawn_env(),
            )
        except (OSError, ValueError) as exc:
            self._proc = None
            self._fail_start(f"spawn failed: {exc}")
            return

        self._brain_pid = self._proc.pid
        self._messages = []
        self._cursor = 0

        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="codex-brain-reader", daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name="codex-brain-stderr", daemon=True
        )
        self._stderr_thread.start()

        init_id = self._alloc_id()
        self._write({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {"clientInfo": {"name": "ironclaude", "title": "IronClaude", "version": "1.0"}},
        })
        init_response = self._await_response(init_id, timeout=30.0)
        if init_response is None or init_response.get("error"):
            detail = (init_response or {}).get("error") if init_response else "timeout"
            self._fail_start(f"initialize failed: {detail}")
            return

        self._write({"jsonrpc": "2.0", "method": "initialized", "params": {}})

        thread_id_req = self._alloc_id()
        self._write({
            "jsonrpc": "2.0",
            "id": thread_id_req,
            "method": "thread/start",
            "params": self._thread_start_params(cwd),
        })
        thread_response = self._await_response(thread_id_req, timeout=60.0)
        if thread_response is None or thread_response.get("error"):
            detail = (thread_response or {}).get("error") if thread_response else "timeout"
            # Loud on purpose: the #1 cause is a missing/incorrect threadSource,
            # and retrying WITHOUT it produces a Brain whose every MCP call fails.
            self._fail_start(
                f"thread/start failed with threadSource={self.THREAD_SOURCE!r}: {detail}. "
                "NOT retrying without threadSource — omitting it makes every MCP call "
                "fail with 'Missing or invalid Codex thread_source'."
            )
            return

        result = thread_response.get("result") or {}
        self._thread_id = self._extract_thread_id(result)
        if not self._thread_id:
            self._fail_start(
                "thread/start returned no thread id (expected result['thread']['id']); "
                "without it every turn/start fails with -32600."
            )
            return

        orchestrator_error = self._verify_orchestrator_mcp()
        if orchestrator_error is not None:
            self._fail_start(orchestrator_error)
            return

        self._running = True
        self._last_activity = time.time()

        if system_prompt:
            self.send_message(system_prompt)

    def send_message(self, text: str) -> bool:
        if not self._running:
            return False
        request_id = self._alloc_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "turn/start",
            "params": {
                "threadId": self._thread_id,
                # `input` is an ARRAY of typed content blocks, never a bare string.
                "input": [{"type": "text", "text": text}],
            },
        }
        if not self._write(payload):
            return False
        self._last_activity = time.time()
        self._last_message_time = time.time()
        # Inspect the turn/start response so a rejected turn (JSON-RPC error) is not swallowed
        # as a success — callers act on the return value and would otherwise skip retry (#3).
        # `_await_response` scans without consuming shared state, so concurrent sends are safe.
        response = self._await_response(request_id, timeout=_TURN_START_ACK_TIMEOUT)
        if response is not None and response.get("error"):
            self._responses.put(
                f"[CODEX BRAIN ERROR] turn/start rejected: {response['error']}"
            )
            return False
        return True

    def get_pending_responses(self) -> list[str]:
        responses: list[str] = []
        while True:
            try:
                responses.append(self._responses.get_nowait())
            except queue.Empty:
                break
        return responses

    def is_alive(self) -> bool:
        return bool(self._running and self._proc is not None and self._proc.poll() is None)

    def needs_restart(self) -> bool:
        if not self.is_alive():
            if not self._restart_reason:
                self._restart_reason = "dead (codex app-server not running)"
            return True
        if len(self._turn_failures) >= _TURN_FAILURE_RESTART_THRESHOLD:
            self._restart_reason = f"{len(self._turn_failures)} consecutive turn failures"
            return True
        # Wall-clock hang: a turn was sent but no response has arrived within timeout_seconds.
        if (
            self._last_message_time > self._last_response_time
            and time.time() - self._last_message_time > self.timeout_seconds
        ):
            elapsed = time.time() - self._last_message_time
            self._restart_reason = f"timeout (no response in {elapsed:.0f}s)"
            return True
        return False

    def circuit_breaker_tripped(self) -> bool:
        cutoff = time.time() - self.restart_window_seconds
        self._restart_timestamps = [t for t in self._restart_timestamps if t > cutoff]
        return len(self._restart_timestamps) >= self.max_restarts

    def check_compaction_complete(self) -> bool:
        """One-shot read of the compaction-complete edge."""
        if self._compaction_complete:
            self._compaction_complete = False
            return True
        return False

    def restart(self, system_prompt: str, cwd: str | None = None) -> bool:
        self.shutdown()
        self._turn_failures = []
        self._token_usage = None
        self.start(system_prompt, cwd=cwd)
        if self.is_alive():
            # Count the restart against the circuit breaker only on SUCCESS (#7) — a failed
            # restart must not consume a breaker slot. Mirrors brain_client.py.
            self.restart_count += 1
            self._restart_timestamps.append(time.time())
            return True
        logger.error(f"Failed to restart codex brain: {self._restart_reason}")
        return False

    @property
    def restart_reason(self) -> str:
        return self._restart_reason

    def get_token_usage(self) -> dict | None:
        usage = self._token_usage
        if not usage:
            return None
        input_tokens = usage.get("inputTokens", usage.get("input_tokens", 0)) or 0
        output_tokens = usage.get("outputTokens", usage.get("output_tokens", 0)) or 0
        total = usage.get("totalTokens", usage.get("total_tokens")) or (input_tokens + output_tokens)
        age = time.time() - self._last_activity if self._last_activity > 0 else None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
            # Codex usage is subscription-billed; no per-turn dollar figure is reported.
            "cost_usd": usage.get("costUsd", usage.get("cost_usd", 0.0)) or 0.0,
            "seconds_since_last_activity": age,
        }

    def shutdown(self) -> None:
        """Idempotent — safe to call before start, twice, or after a failed start."""
        self._stop_event.set()
        self._running = False
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                logger.warning(f"codex app-server shutdown issue: {exc}")
        self._proc = None
        self._brain_pid = None
        self._thread_id = None
        # Join the reader/stderr threads (proc is terminated → stdout EOF → they exit) so a
        # stale reader's `self._running = False` cannot land after a new start()'s True (#5).
        reader = self._reader_thread
        if reader is not None and reader.is_alive():
            reader.join(timeout=5)
        stderr = self._stderr_thread
        if stderr is not None and stderr.is_alive():
            stderr.join(timeout=5)

    def was_compacted(self) -> bool:
        # Documented constant, NOT a hidden stub: this method has zero production
        # callers (main.py never invokes it; it exists only for BrainClient surface
        # parity). Codex app-server performs no Claude-style /compact handoff, so
        # False is the correct and permanent answer, not a TODO.
        return False
