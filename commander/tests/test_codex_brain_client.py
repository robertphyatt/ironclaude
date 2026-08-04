import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from unittest.mock import MagicMock
from ironclaude.codex_brain_client import CodexBrainClient

DAEMON_TOUCHED = [
    "start", "send_message", "get_pending_responses", "is_alive", "needs_restart",
    "check_compaction_complete", "circuit_breaker_tripped", "restart",
    "get_token_usage", "shutdown", "was_compacted",
    "restart_reason", "restart_count", "max_restarts", "restart_window_seconds",
    "_stop_event", "_running", "_brain_pid", "_restart_timestamps",
]

class TestCodexBrainClientContract:
    def test_every_daemon_touched_member_exists(self):
        client = CodexBrainClient()
        missing = [m for m in DAEMON_TOUCHED if not hasattr(client, m)]
        assert missing == [], f"missing members main.py depends on: {missing}"

    def test_constructor_accepts_brainclient_kwargs(self):
        client = CodexBrainClient(timeout_seconds=600, operator_name="Operator",
                                  model="gpt-5.6-sol", effort_level="high")
        assert client.is_alive() is False

class TestThreadSource:
    def test_thread_start_params_include_threadsource_user(self):
        client = CodexBrainClient()
        params = client._thread_start_params(cwd="/tmp/x")
        assert params["threadSource"] == "user", (
            "threadSource must be exactly 'user'; without it every MCP call the "
            "codex Brain makes fails with 'Missing or invalid Codex thread_source'"
        )

class TestSpawnHardening:
    """The app-server MUST be launched read-only + on-request so every Brain mutation
    escalates to the approval channel (directive #175). Pinning the argv stops a future
    edit from silently dropping the flags."""
    def test_app_server_argv_forces_readonly_and_on_request(self):
        c = CodexBrainClient()
        assert c._app_server_argv() == [
            "codex", "app-server",
            "-c", 'sandbox_mode="read-only"',
            "-c", 'approval_policy="on-request"',
            "-c", 'model="gpt-5.6-terra"',
            "-c", 'model_reasoning_effort="high"',
            *c._orchestrator_mcp_overrides(),
            *c._optional_mcp_overrides(),
            "--stdio",
        ]

class TestErrorSurfacing:
    def test_turn_failed_is_surfaced_and_counted(self):
        client = CodexBrainClient()
        client._handle_event({"method": "turn/failed",
                              "params": {"threadId": "t1", "error": {"message": "boom"}}})
        responses = client.get_pending_responses()
        assert any("boom" in r for r in responses), (
            "turn/failed must reach the operator via get_pending_responses, not be swallowed")
        assert len(client._turn_failures) == 1, "turn failures must be counted toward the restart path"

    def test_agent_message_is_surfaced(self):
        client = CodexBrainClient()
        client._handle_event({"method": "item/completed",
                              "params": {"item": {"type": "agentMessage", "text": "hello"}}})
        assert "hello" in client.get_pending_responses()


class TestTokenUsageEventName:
    def test_real_token_usage_event_populates_usage(self):
        # `thread/tokenUsage/updated` is the event codex app-server actually emits.
        # Matching a name codex never sends makes get_token_usage() return None
        # forever, and main.py:2582 tolerates None — so it fails SILENTLY.
        client = CodexBrainClient()
        client._handle_event({
            "method": "thread/tokenUsage/updated",
            "params": {"usage": {"input_tokens": 11, "output_tokens": 7}},
        })
        usage = client.get_token_usage()
        assert usage is not None, (
            "thread/tokenUsage/updated must populate token usage; a wrong event "
            "name degrades silently and no other test can catch it"
        )
        assert usage["input_tokens"] == 11
        assert usage["output_tokens"] == 7

    def test_real_token_usage_event_populates_cumulative_usage(self):
        client = CodexBrainClient()
        client._handle_event({
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "tokenUsage": {
                    "last": {
                        "inputTokens": 11,
                        "cachedInputTokens": 0,
                        "outputTokens": 7,
                        "reasoningOutputTokens": 0,
                        "totalTokens": 18,
                    },
                    "total": {
                        "inputTokens": 110,
                        "cachedInputTokens": 0,
                        "outputTokens": 70,
                        "reasoningOutputTokens": 0,
                        "totalTokens": 191,
                    },
                },
                "modelContextWindow": 258400,
            },
        })
        usage = client.get_token_usage()
        assert usage is not None
        assert usage["input_tokens"] == 110
        assert usage["output_tokens"] == 70
        assert usage["total_tokens"] == 191
        assert usage["cost_usd"] == 0.0
        assert usage["seconds_since_last_activity"] is not None


class TestFailClosedApprovals:
    # Confirmed from the generated schema (2026-07-23): ServerRequest.json method
    # enums + each *Response.json body.
    ELICITATION_METHOD = "mcpServer/elicitation/request"
    # Every non-elicitation approval method the classifier must decline.
    APPROVAL_METHODS = [
        "execCommandApproval",
        "applyPatchApproval",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    ]

    def test_approval_method_list_is_populated(self):
        assert self.APPROVAL_METHODS, "APPROVAL_METHODS must supply real method names"

    def test_mcp_elicitation_is_accepted(self):
        c = CodexBrainClient()
        assert c._classify_server_request({"method": self.ELICITATION_METHOD, "id": 1}) == "accept"

    @pytest.mark.parametrize("method", APPROVAL_METHODS)
    def test_approval_requests_are_declined(self, method):
        c = CodexBrainClient()
        assert c._classify_server_request({"method": method, "id": 1}) == "decline"

    def test_unknown_method_is_declined(self):
        c = CodexBrainClient()
        assert c._classify_server_request({"method": "some/methodInventedTomorrow", "id": 1}) == "decline"


class TestDispatchPathIsFailClosed:
    """Exercises _handle_server_request — the real dispatch seam — and asserts the
    EXACT full response envelope per type. Per-method decline bodies are non-uniform
    (verified from the schema): ReviewDecision uses {"decision":"denied"},
    Command/FileChange use {"decision":"decline"}, Permissions uses {"permissions":{}}.
    """
    ELICITATION_ACCEPT_RESULT = {"action": "accept"}
    # Canonical default decline body, also used for unknown methods.
    EXEC_DECLINE_BODY = {"decision": "denied"}
    # (method, exact schema-correct decline body) for every enumerated approval method.
    DECLINE_CASES = [
        ("execCommandApproval", {"decision": "denied"}),
        ("applyPatchApproval", {"decision": "denied"}),
        ("item/commandExecution/requestApproval", {"decision": "decline"}),
        ("item/fileChange/requestApproval", {"decision": "decline"}),
        ("item/permissions/requestApproval", {"permissions": {}}),
    ]

    def _capture(self, client):
        sent = []
        client._write = lambda payload: sent.append(payload) or True
        return sent

    def test_decline_cases_cover_every_approval_method(self):
        covered = {m for m, _ in self.DECLINE_CASES}
        missing = [m for m in TestFailClosedApprovals.APPROVAL_METHODS if m not in covered]
        assert not missing, f"DECLINE_CASES must pin a body for every approval method; missing {missing}"

    @pytest.mark.parametrize("method,expected", DECLINE_CASES)
    def test_dispatch_declines_with_the_schema_correct_body(self, method, expected):
        c = CodexBrainClient()
        sent = self._capture(c)
        c._handle_server_request({"method": method, "id": 7})
        assert sent == [{"jsonrpc": "2.0", "id": 7, "result": expected}], (
            f"{method} decline must send exactly the schema body; sent {sent}"
        )

    def test_dispatch_accepts_elicitation_with_the_schema_correct_body(self):
        c = CodexBrainClient()
        sent = self._capture(c)
        c._handle_server_request({"method": TestFailClosedApprovals.ELICITATION_METHOD, "id": 8})
        assert sent == [{"jsonrpc": "2.0", "id": 8, "result": self.ELICITATION_ACCEPT_RESULT}]

    def test_dispatch_surfaces_the_decline(self):
        method = self.DECLINE_CASES[0][0]
        c = CodexBrainClient()
        self._capture(c)
        c._handle_server_request({"method": method, "id": 9})
        assert any(method in r for r in c.get_pending_responses()), (
            "a blocked Brain must look blocked to the operator, not merely idle"
        )

    def test_dispatch_decline_is_not_a_turn_failure(self):
        method = self.DECLINE_CASES[0][0]
        c = CodexBrainClient()
        self._capture(c)
        c._handle_server_request({"method": method, "id": 10})
        assert c._turn_failures == []

    def test_unknown_method_dispatch_declines_with_the_canonical_body(self):
        c = CodexBrainClient()
        sent = self._capture(c)
        c._handle_server_request({"method": "codex/methodInventedTomorrow", "id": 11})
        assert sent == [{"jsonrpc": "2.0", "id": 11, "result": self.EXEC_DECLINE_BODY}]

    def test_gate_drops_messages_missing_id_or_method(self):
        c = CodexBrainClient()
        sent = self._capture(c)
        c._handle_server_request({"method": "item/completed", "id": None})
        c._handle_server_request({"method": "item/completed"})
        c._handle_server_request({"id": 42, "result": {"ok": True}})
        assert sent == [], "a notification or a response must never be answered as a server request"


class TestGuardPortApprovesGitEscalations:
    """Ported git-allowlist on the two exec-approval methods. Approve ONLY git-allowlist
    commands (byte-for-byte with brain_client._tool_guard_logic); decline everything else.
    Fail-closed: null/unparseable/non-git -> decline. Approve bodies differ per method
    (v2 accept, v1 approved) - verified from the schema."""
    def _capture(self, client):
        sent = []
        client._write = lambda payload: sent.append(payload) or True
        return sent

    # v2: item/commandExecution/requestApproval - command is a STRING, approve = accept
    def test_v2_git_command_approved_with_accept(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 1,
                                  "params": {"command": "git status"}})
        assert sent == [{"jsonrpc": "2.0", "id": 1, "result": {"decision": "accept"}}]

    def test_v2_non_git_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 2,
                                  "params": {"command": "rm -rf /"}})
        assert sent == [{"jsonrpc": "2.0", "id": 2, "result": {"decision": "decline"}}]

    def test_v2_metachar_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 3,
                                  "params": {"command": "git log | sh"}})
        assert sent == [{"jsonrpc": "2.0", "id": 3, "result": {"decision": "decline"}}]

    def test_v2_null_command_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 4,
                                  "params": {"command": None}})
        assert sent == [{"jsonrpc": "2.0", "id": 4, "result": {"decision": "decline"}}]

    def test_v2_git_dash_c_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 5,
                                  "params": {"command": "git -c core.pager=sh status"}})
        assert sent == [{"jsonrpc": "2.0", "id": 5, "result": {"decision": "decline"}}]

    def test_v2_non_allowlisted_subcommand_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 6,
                                  "params": {"command": "git push origin main"}})
        assert sent == [{"jsonrpc": "2.0", "id": 6, "result": {"decision": "decline"}}]

    def test_v2_commit_amend_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "item/commandExecution/requestApproval", "id": 7,
                                  "params": {"command": "git commit --amend -m x"}})
        assert sent == [{"jsonrpc": "2.0", "id": 7, "result": {"decision": "decline"}}]

    # v1: execCommandApproval - command is an ARRAY, approve = approved
    def test_v1_git_command_approved_with_approved(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "execCommandApproval", "id": 8,
                                  "params": {"command": ["git", "add", "."]}})
        assert sent == [{"jsonrpc": "2.0", "id": 8, "result": {"decision": "approved"}}]

    def test_v1_non_git_declined_with_denied(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "execCommandApproval", "id": 9,
                                  "params": {"command": ["rm", "-rf", "/"]}})
        assert sent == [{"jsonrpc": "2.0", "id": 9, "result": {"decision": "denied"}}]

    def test_v1_non_list_command_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "execCommandApproval", "id": 10,
                                  "params": {"command": "git status"}})
        assert sent == [{"jsonrpc": "2.0", "id": 10, "result": {"decision": "denied"}}]

    def test_v1_metachar_in_joined_args_declined(self):
        c = CodexBrainClient(); sent = self._capture(c)
        c._handle_server_request({"method": "execCommandApproval", "id": 11,
                                  "params": {"command": ["git", "log", "|", "sh"]}})
        assert sent == [{"jsonrpc": "2.0", "id": 11, "result": {"decision": "denied"}}]


class TestThreadIdExtraction:
    """start() must read the thread id from the real thread/start response shape
    (result['thread']['id'], verified live); the old result.get('threadId') left it
    None and every turn/start failed -32600."""
    def test_extracts_nested_thread_id(self):
        c = CodexBrainClient()
        assert c._extract_thread_id({"thread": {"id": "abc123"}}) == "abc123"

    def test_legacy_threadId_fallback(self):
        c = CodexBrainClient()
        assert c._extract_thread_id({"threadId": "leg1"}) == "leg1"

    def test_legacy_thread_id_fallback(self):
        c = CodexBrainClient()
        assert c._extract_thread_id({"thread_id": "leg2"}) == "leg2"

    def test_missing_returns_none(self):
        c = CodexBrainClient()
        assert c._extract_thread_id({}) is None
        assert c._extract_thread_id({"thread": None}) is None
        assert c._extract_thread_id({"thread": {}}) is None


class TestNeedsRestartHangDetection:
    """needs_restart must restart a live-but-wedged Brain (turn sent, no response past
    timeout_seconds) and must not trip when responded or idle."""
    def _force_alive(self, client):
        client.is_alive = lambda: True  # bypass the process check to exercise the timeout tier

    def test_hang_trips_restart_when_no_response_past_timeout(self):
        c = CodexBrainClient(timeout_seconds=1)
        self._force_alive(c)
        c._last_message_time = time.time() - 10  # sent 10s ago
        c._last_response_time = 0.0              # never responded
        assert c.needs_restart() is True
        assert "no response" in (c.restart_reason or "").lower()

    def test_no_hang_when_response_newer_than_message(self):
        c = CodexBrainClient(timeout_seconds=1)
        self._force_alive(c)
        c._last_message_time = time.time() - 10
        c._last_response_time = time.time()      # responded just now
        assert c.needs_restart() is False

    def test_no_hang_when_idle(self):
        c = CodexBrainClient(timeout_seconds=1)
        self._force_alive(c)
        c._last_message_time = 0.0
        c._last_response_time = 0.0
        assert c.needs_restart() is False


class TestTurnFailuresConsecutive:
    """A completed turn clears _turn_failures so needs_restart's count is consecutive."""
    def test_turn_completed_clears_turn_failures(self):
        c = CodexBrainClient()
        c._handle_event({"method": "turn/failed", "params": {"error": {"message": "e1"}}})
        c._handle_event({"method": "turn/failed", "params": {"error": {"message": "e2"}}})
        assert len(c._turn_failures) == 2
        c._handle_event({"method": "turn/completed",
                         "params": {"threadId": "t", "turn": {"status": "completed"}}})
        assert c._turn_failures == []


class TestShutdownJoinsThreads:
    """#5: shutdown must join the reader/stderr threads so a stale reader's _running=False
    cannot clobber a fresh start."""
    def test_shutdown_joins_reader_and_stderr_threads(self):
        c = CodexBrainClient()
        reader = MagicMock(); reader.is_alive.return_value = True
        stderr = MagicMock(); stderr.is_alive.return_value = True
        c._reader_thread = reader
        c._stderr_thread = stderr
        c.shutdown()
        reader.join.assert_called()
        stderr.join.assert_called()


class TestMessageBufferBounded:
    """#6: _record_message bounds _messages to a rolling window and keeps _cursor valid."""
    def test_record_message_bounds_buffer_and_retains_recent(self):
        from ironclaude.codex_brain_client import _MESSAGE_BUFFER_CAP
        c = CodexBrainClient()
        for i in range(_MESSAGE_BUFFER_CAP + 50):
            c._record_message({"id": i})
        assert len(c._messages) == _MESSAGE_BUFFER_CAP
        assert c._messages[-1] == {"id": _MESSAGE_BUFFER_CAP + 49}
        assert 0 <= c._cursor <= len(c._messages)

    def test_record_message_shifts_cursor_on_overflow(self):
        from ironclaude.codex_brain_client import _MESSAGE_BUFFER_CAP
        c = CodexBrainClient()
        for i in range(_MESSAGE_BUFFER_CAP):
            c._messages.append({"id": i})
        c._cursor = _MESSAGE_BUFFER_CAP          # fully consumed
        c._record_message({"id": "new"})         # overflow=1 -> drop 1, cursor -1
        assert len(c._messages) == _MESSAGE_BUFFER_CAP
        assert c._cursor == _MESSAGE_BUFFER_CAP - 1


class TestSendMessageDetectsError:
    """#3: send_message must inspect the turn/start response and return False on a JSON-RPC
    error, surfacing it; success/timeout return True; not-running returns False."""
    def _ready(self, c, monkeypatch):
        c._running = True
        c._thread_id = "t"
        monkeypatch.setattr(c, "_write", lambda payload: True)

    def test_error_response_returns_false_and_surfaces(self, monkeypatch):
        c = CodexBrainClient(); self._ready(c, monkeypatch)
        monkeypatch.setattr(c, "_await_response", lambda rid, timeout: {"id": rid, "error": {"message": "boom"}})
        assert c.send_message("hi") is False
        assert any("turn/start rejected" in r for r in c.get_pending_responses())

    def test_success_response_returns_true(self, monkeypatch):
        c = CodexBrainClient(); self._ready(c, monkeypatch)
        monkeypatch.setattr(c, "_await_response", lambda rid, timeout: {"id": rid, "result": {}})
        assert c.send_message("hi") is True

    def test_timeout_returns_true(self, monkeypatch):
        c = CodexBrainClient(); self._ready(c, monkeypatch)
        monkeypatch.setattr(c, "_await_response", lambda rid, timeout: None)
        assert c.send_message("hi") is True

    def test_not_running_returns_false(self):
        c = CodexBrainClient()
        assert c.send_message("hi") is False


class TestRestartBreakerSuccessOnly:
    """#7: restart increments the breaker only on a successful (alive) restart."""
    def test_restart_counts_only_on_success(self, monkeypatch):
        c = CodexBrainClient()
        monkeypatch.setattr(c, "shutdown", lambda: None)
        monkeypatch.setattr(c, "start", lambda *a, **k: None)
        monkeypatch.setattr(c, "is_alive", lambda: True)
        assert c.restart("p") is True
        assert c.restart_count == 1
        assert len(c._restart_timestamps) == 1
        monkeypatch.setattr(c, "is_alive", lambda: False)
        assert c.restart("p") is False
        assert c.restart_count == 1                # unchanged on failure
        assert len(c._restart_timestamps) == 1     # unchanged on failure


class TestModelWiring:
    """The codex Brain must launch on the operator-ruled model. self._model is
    resolved through the codex tier->model map (tier name -> codex model; a value
    already in codex-model form passes through), then emitted as `-c model="..."`
    without dropping the read-only sandbox + on-request approval flags."""

    def test_default_model_is_terra(self):
        argv = CodexBrainClient()._app_server_argv()
        assert '-c' in argv and 'model="gpt-5.6-terra"' in argv

    def test_sonnet_tier_resolves_to_terra(self):
        c = CodexBrainClient(model="sonnet")
        assert c._resolve_model() == "gpt-5.6-terra"
        assert 'model="gpt-5.6-terra"' in c._app_server_argv()

    def test_opus_tier_resolves_to_sol(self):
        assert CodexBrainClient(model="opus")._resolve_model() == "gpt-5.6-sol"

    def test_haiku_tier_resolves_to_luna(self):
        assert CodexBrainClient(model="haiku")._resolve_model() == "gpt-5.6-luna"

    def test_codex_model_string_passes_through(self):
        assert CodexBrainClient(model="gpt-5.6-terra")._resolve_model() == "gpt-5.6-terra"
        assert CodexBrainClient(model="gpt-5.6-sol")._resolve_model() == "gpt-5.6-sol"

    def test_model_wiring_preserves_sandbox_and_approval(self):
        argv = CodexBrainClient(model="opus")._app_server_argv()
        assert 'sandbox_mode="read-only"' in argv
        assert 'approval_policy="on-request"' in argv
        assert 'model="gpt-5.6-sol"' in argv
        assert argv[-1] == "--stdio"


class TestAwaitResponseConcurrencySafety:
    """_await_response must not consume a SHARED cursor: two waiters would then race,
    one skipping past the other's response, and send_message would report a rejected
    turn/start as success."""

    def _client_with(self, *messages):
        c = CodexBrainClient()
        c._messages = list(messages)
        c._cursor = 0
        return c

    def test_out_of_order_waiters_each_get_their_own_response(self):
        """THE deterministic RED. Pre-fix, awaiting id 2 walks the shared cursor past BOTH
        entries, so the later await for id 1 finds nothing."""
        c = self._client_with(
            {"id": 1, "result": {"a": 1}},
            {"id": 2, "result": {"b": 2}},
        )
        second = c._await_response(2, timeout=0.2)
        assert second is not None and second["id"] == 2
        first = c._await_response(1, timeout=0.2)
        assert first is not None, (
            "awaiting out of order must still find the earlier response; a shared "
            "advancing cursor skips past it and returns None"
        )
        assert first["id"] == 1

    def test_ascending_order_waiters_still_work(self):
        """Regression guard, NOT a RED: today's code returns on the same iteration it
        advances the cursor, so ascending-order awaits already succeed."""
        c = self._client_with(
            {"id": 1, "result": {"a": 1}},
            {"id": 2, "result": {"b": 2}},
        )
        first = c._await_response(1, timeout=0.2)
        second = c._await_response(2, timeout=0.2)
        assert first is not None and first["id"] == 1
        assert second is not None and second["id"] == 2

    def test_does_not_advance_the_shared_cursor(self):
        c = self._client_with({"id": 1, "result": {}})
        c._await_response(1, timeout=0.2)
        assert c._cursor == 0, "_await_response must not write the shared cursor"

    def test_notification_with_same_id_is_not_matched(self):
        c = self._client_with({"id": 1, "method": "turn/completed", "params": {}})
        assert c._await_response(1, timeout=0.2) is None

    def test_timeout_returns_none(self):
        c = self._client_with({"id": 9, "result": {}})
        assert c._await_response(1, timeout=0.2) is None

    def test_dead_process_returns_promptly(self):
        c = self._client_with()

        class _Dead:
            def poll(self):
                return 1

        c._proc = _Dead()
        start = time.time()
        assert c._await_response(1, timeout=5.0) is None
        assert time.time() - start < 2.0, "a dead process must short-circuit the wait"


class TestBrainRoleDiscriminator:
    """The GATED_TOOLS hook must tell the codex Brain from codex workers. The Brain
    process carries IC_ROLE=brain — scoped to its own spawn, never leaked into the
    daemon environment (which would mislabel later worker spawns)."""

    def test_spawn_env_marks_the_brain(self):
        assert CodexBrainClient()._spawn_env()["IC_ROLE"] == "brain"

    def test_spawn_env_preserves_ambient_environment(self, monkeypatch):
        # env= REPLACES inheritance, so the daemon env must be copied forward.
        monkeypatch.setenv("IC_TEST_AMBIENT", "kept")
        assert CodexBrainClient()._spawn_env()["IC_TEST_AMBIENT"] == "kept"

    def test_spawn_env_does_not_mutate_process_environment(self, monkeypatch):
        monkeypatch.delenv("IC_ROLE", raising=False)
        CodexBrainClient()._spawn_env()
        assert "IC_ROLE" not in os.environ, (
            "IC_ROLE must be scoped to the Brain spawn; leaking it into the daemon "
            "environment would mislabel subsequently spawned workers"
        )

    def test_start_passes_brain_role_to_popen(self, monkeypatch):
        # The fake reports an ALREADY-EXITED process (poll() -> 0). That makes
        # _await_response return immediately instead of spinning its 30s timeout, and
        # makes shutdown() skip the terminate() branch — so start() completes fast and
        # the assertions below actually run. env is recorded at the Popen call itself,
        # before any of that, so an early bail-out does not hide the result.
        import ironclaude.codex_brain_client as m
        monkeypatch.setenv("IC_TEST_AMBIENT", "kept")
        recorded = {}

        class _FakeProc:
            pid = 4321
            stdin = MagicMock()
            stdout = MagicMock()
            stderr = MagicMock()

            def poll(self):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 0

        def fake_popen(argv, **kwargs):
            recorded["argv"] = argv
            recorded["env"] = kwargs.get("env")
            return _FakeProc()

        monkeypatch.setattr(m.subprocess, "Popen", fake_popen)
        c = CodexBrainClient()
        monkeypatch.setattr(c, "_preflight_orchestrator", lambda: None)
        monkeypatch.setattr(c, "_reader_loop", lambda: None)
        monkeypatch.setattr(c, "_stderr_loop", lambda: None)
        c.start("system prompt", cwd="/tmp")
        assert recorded["env"] is not None, "start() must pass env= so IC_ROLE reaches the app-server"
        assert recorded["env"]["IC_ROLE"] == "brain"
        # env= REPLACES inheritance: pin the MERGED env at the Popen boundary so an
        # implementation passing a bare {"IC_ROLE": "brain"} (stripping auth/PATH) fails.
        assert recorded["env"].get("IC_TEST_AMBIENT") == "kept"


class TestBrainGateEnvironment:
    def test_spawn_env_marks_codex_and_stable_gate_session(self):
        client = CodexBrainClient()
        first = client._spawn_env()
        second = client._spawn_env()
        assert first["IRONCLAUDE_CLIENT"] == "codex"
        assert first["IRONCLAUDE_BRAIN_GATE_SESSION"]
        assert second["IRONCLAUDE_BRAIN_GATE_SESSION"] == first["IRONCLAUDE_BRAIN_GATE_SESSION"]

    def test_gate_session_is_unique_per_client(self):
        first = CodexBrainClient()._spawn_env()["IRONCLAUDE_BRAIN_GATE_SESSION"]
        second = CodexBrainClient()._spawn_env()["IRONCLAUDE_BRAIN_GATE_SESSION"]
        assert first != second

    def test_gate_markers_do_not_leak_into_daemon_environment(self, monkeypatch):
        monkeypatch.delenv("IC_ROLE", raising=False)
        monkeypatch.delenv("IRONCLAUDE_CLIENT", raising=False)
        monkeypatch.delenv("IRONCLAUDE_BRAIN_GATE_SESSION", raising=False)
        CodexBrainClient()._spawn_env()
        assert "IC_ROLE" not in os.environ
        assert "IRONCLAUDE_CLIENT" not in os.environ
        assert "IRONCLAUDE_BRAIN_GATE_SESSION" not in os.environ

    def test_startup_reset_clears_lookback_only(self, tmp_path, monkeypatch):
        import ironclaude.codex_brain_client as module

        monkeypatch.setattr(module, "_BRAIN_GATE_ROOT", tmp_path)
        client = CodexBrainClient()
        state_dir = tmp_path / client._brain_gate_session
        state_dir.mkdir()
        for marker in ("lookback-slack", "lookback-ledger", "memory-armed", "wiki-queried"):
            (state_dir / marker).touch()

        client._reset_brain_gate_startup_state()

        assert not (state_dir / "lookback-slack").exists()
        assert not (state_dir / "lookback-ledger").exists()
        assert (state_dir / "memory-armed").exists()
        assert (state_dir / "wiki-queried").exists()

    def test_restart_keeps_same_gate_session(self, monkeypatch):
        client = CodexBrainClient()
        gate_session = client._brain_gate_session
        monkeypatch.setattr(client, "shutdown", lambda: None)

        def fake_start(*_args, **_kwargs):
            client._running = True
            client._proc = MagicMock()
            client._proc.poll.return_value = None

        monkeypatch.setattr(client, "start", fake_start)
        assert client.restart("prompt") is True
        assert client._brain_gate_session == gate_session


class TestOrchestratorMcpWiring:
    def test_app_server_argv_registers_production_orchestrator(self):
        client = CodexBrainClient()
        client._cwd = "/tmp/brain"
        argv = client._app_server_argv()
        joined = "\n".join(argv)
        assert f"mcp_servers.orchestrator.command={json.dumps(sys.executable)}" in joined
        assert "orchestrator_mcp.py" in joined
        assert "commander/data/db/ironclaude.db" in joined
        assert "mcp_servers.orchestrator.enabled=true" in joined
        assert 'mcp_servers.orchestrator.default_tools_approval_mode="approve"' in joined
        assert "mcp_servers.orchestrator.startup_timeout_sec=120" in joined
        assert "mcp_servers.orchestrator.cwd=" in joined
        assert (
            'mcp_servers.orchestrator.env_vars=["SUPABASE_URL","SUPABASE_ANON_KEY",'
            '"SLACK_BOT_TOKEN","SLACK_CHANNEL_ID","SLACK_USER_TOKEN",'
            '"SLACK_OPERATOR_USER_ID","OPERATOR_NAME"]'
            in joined
        )
        assert (
            "mcp_servers.orchestrator.env.IC_BRAIN_CWD="
            + json.dumps("/tmp/brain")
        ) in joined
        assert "mcp_servers.orchestrator.env.IC_MACHINES_CONFIG=" in joined
        assert argv[-1] == "--stdio"

    def test_secret_stays_in_spawn_env_and_out_of_argv(self, monkeypatch):
        sentinels = {
            "SUPABASE_ANON_KEY": "supabase-secret-sentinel",
            "SLACK_BOT_TOKEN": "slack-bot-secret-sentinel",
            "SLACK_USER_TOKEN": "slack-user-secret-sentinel",
        }
        for name, value in sentinels.items():
            monkeypatch.setenv(name, value)

        client = CodexBrainClient()
        spawn_env = client._spawn_env()
        argv_text = "\n".join(client._app_server_argv())

        for name, value in sentinels.items():
            assert spawn_env[name] == value
            assert value not in argv_text

    def test_preflight_uses_same_interpreter_cwd_and_environment(self, monkeypatch):
        import ironclaude.codex_brain_client as module

        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setenv("SUPABASE_ANON_KEY", "secret-sentinel")
        monkeypatch.setattr(module.subprocess, "run", fake_run)
        client = CodexBrainClient()
        assert client._preflight_orchestrator() is None
        assert captured["argv"] == [
            sys.executable,
            "-c",
            "import ironclaude.orchestrator_mcp",
        ]
        assert captured["cwd"] == str(client._commander_root())
        assert captured["env"]["SUPABASE_ANON_KEY"] == "secret-sentinel"
        assert captured["timeout"] == 30

    def test_import_preflight_reports_nonzero_exit(self, monkeypatch):
        import ironclaude.codex_brain_client as module

        monkeypatch.setattr(
            module.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "boom"),
        )
        error = CodexBrainClient()._preflight_orchestrator()
        assert error is not None
        assert "import preflight failed" in error
        assert "boom" in error

    def test_import_preflight_reports_timeout(self, monkeypatch):
        import ironclaude.codex_brain_client as module

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 30)

        monkeypatch.setattr(module.subprocess, "run", timeout)
        error = CodexBrainClient()._preflight_orchestrator()
        assert error is not None
        assert "import preflight failed" in error
        assert "timed out" in error

    def test_missing_orchestrator_fails_before_spawn_and_is_visible(
        self, monkeypatch, tmp_path
    ):
        import ironclaude.codex_brain_client as module

        client = CodexBrainClient()
        monkeypatch.setattr(
            client, "_orchestrator_source_path", lambda: tmp_path / "missing.py"
        )
        popen = MagicMock()
        monkeypatch.setattr(module.subprocess, "Popen", popen)
        client.start("", cwd=str(tmp_path))
        assert not popen.called
        assert client.is_alive() is False
        assert "orchestrator" in client.restart_reason.lower()
        assert any(
            "[CODEX BRAIN ERROR]" in item
            for item in client.get_pending_responses()
        )


class TestOrchestratorStartupReadiness:
    REQUIRED = {
        "wiki_query",
        "get_operator_messages",
        "update_ledger",
        "spawn_worker",
        "spawn_workers",
        "approve_plan",
        "reject_plan",
        "send_to_worker",
        "kill_worker",
        "acknowledge_operator_message",
    }

    @staticmethod
    def _inventory(tools=None, next_cursor=None):
        names = tools if tools is not None else TestOrchestratorStartupReadiness.REQUIRED
        return {
            "result": {
                "data": [{
                    "name": "orchestrator",
                    "tools": {
                        name: {"name": name, "inputSchema": {}}
                        for name in names
                    },
                }],
                "nextCursor": next_cursor,
            },
        }

    def test_early_ready_notification_is_latched(self):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        client._handle_event({
            "method": "mcpServer/startupStatus/updated",
            "params": {
                "threadId": "thread-1",
                "name": "orchestrator",
                "status": "ready",
            },
        })
        assert client._mcp_startup_status["orchestrator"]["status"] == "ready"
        assert client._mcp_startup_status["orchestrator"]["threadId"] == "thread-1"
        assert client._await_orchestrator_ready(timeout=0.01) is None

    @pytest.mark.parametrize("status", ["failed", "cancelled"])
    def test_terminal_startup_failure_is_visible(self, status):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        client._handle_event({
            "method": "mcpServer/startupStatus/updated",
            "params": {
                "threadId": "thread-1",
                "name": "orchestrator",
                "status": status,
                "error": "boom",
            },
        })
        error = client._await_orchestrator_ready(timeout=0.01)
        assert error is not None
        assert status in error
        assert "boom" in error

    def test_ready_wait_detects_dead_process(self):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        client._proc = MagicMock()
        client._proc.poll.return_value = 1
        assert "exited" in client._await_orchestrator_ready(timeout=0.01)

    def test_ready_wait_times_out(self):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        client._proc = MagicMock()
        client._proc.poll.return_value = None
        assert "timed out" in client._await_orchestrator_ready(timeout=0.01)

    def test_ready_for_another_thread_does_not_satisfy_wait(self):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        client._proc = MagicMock()
        client._proc.poll.return_value = None
        client._handle_event({
            "method": "mcpServer/startupStatus/updated",
            "params": {
                "threadId": "thread-2",
                "name": "orchestrator",
                "status": "ready",
            },
        })
        assert "timed out" in client._await_orchestrator_ready(timeout=0.01)

    def test_inventory_paginates_with_full_detail(self, monkeypatch):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        sent = []
        responses = [
            {"result": {"data": [], "nextCursor": "opaque-2"}},
            self._inventory(),
        ]
        monkeypatch.setattr(client, "_write", lambda payload: sent.append(payload) or True)
        monkeypatch.setattr(
            client,
            "_await_response",
            lambda request_id, timeout: responses.pop(0),
        )
        inventory, error = client._list_mcp_server_inventory()
        assert error is None
        assert inventory is not None
        assert self.REQUIRED <= set(inventory["orchestrator"]["tools"])
        calls = [item for item in sent if item.get("method") == "mcpServerStatus/list"]
        assert calls[0]["params"] == {
            "detail": "full",
            "threadId": "thread-1",
        }
        assert calls[1]["params"] == {
            "detail": "full",
            "threadId": "thread-1",
            "cursor": "opaque-2",
        }

    def test_inventory_requires_thread_id_before_write(self, monkeypatch):
        client = CodexBrainClient()
        write = MagicMock()
        monkeypatch.setattr(client, "_write", write)
        monkeypatch.setattr(
            client,
            "_await_response",
            lambda request_id, timeout: self._inventory(),
        )
        inventory, error = client._list_mcp_server_inventory()
        assert inventory is None
        assert "thread id" in error
        write.assert_not_called()

    @pytest.mark.parametrize(
        ("response", "fragment"),
        [
            (None, "timed out"),
            ({"error": {"message": "boom"}}, "boom"),
            ({"result": []}, "malformed"),
            ({"result": {"data": "bad", "nextCursor": None}}, "malformed"),
            ({"result": {"data": [], "nextCursor": 7}}, "malformed"),
        ],
    )
    def test_inventory_reports_response_failures(
        self, monkeypatch, response, fragment
    ):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        monkeypatch.setattr(client, "_write", lambda payload: True)
        monkeypatch.setattr(
            client, "_await_response", lambda request_id, timeout: response
        )
        inventory, error = client._list_mcp_server_inventory()
        assert inventory is None
        assert fragment in error

    def test_inventory_rejects_repeated_cursor(self, monkeypatch):
        client = CodexBrainClient()
        client._thread_id = "thread-1"
        monkeypatch.setattr(client, "_write", lambda payload: True)
        monkeypatch.setattr(
            client,
            "_await_response",
            lambda request_id, timeout: {
                "result": {"data": [], "nextCursor": "repeat"}
            },
        )
        inventory, error = client._list_mcp_server_inventory()
        assert inventory is None
        assert "repeated cursor" in error

    @pytest.mark.parametrize(
        ("inventory", "fragment"),
        [
            ({}, "missing server"),
            (
                {"orchestrator": {"tools": {"wiki_query": {"name": "wiki_query"}}}},
                "missing tools",
            ),
        ],
    )
    def test_verify_requires_server_and_all_tools(
        self, monkeypatch, inventory, fragment
    ):
        client = CodexBrainClient()
        monkeypatch.setattr(client, "_await_orchestrator_ready", lambda timeout: None)
        monkeypatch.setattr(
            client,
            "_list_mcp_server_inventory",
            lambda: (inventory, None),
        )
        assert fragment in client._verify_orchestrator_mcp()

    def test_verify_rejects_stale_acknowledgement_inventory(self, monkeypatch):
        client = CodexBrainClient()
        stale_tools = self.REQUIRED - {"acknowledge_operator_message"}
        inventory = {
            "orchestrator": {
                "tools": {name: {"name": name} for name in stale_tools},
            },
            "episodic-memory": {"tools": {}},
        }
        monkeypatch.setattr(client, "_await_orchestrator_ready", lambda timeout: None)
        monkeypatch.setattr(client, "_list_mcp_server_inventory", lambda: (inventory, None))

        assert client._verify_orchestrator_mcp() == (
            "orchestrator MCP inventory missing tools: acknowledge_operator_message"
        )

    def test_verify_requires_ready_before_inventory(self, monkeypatch):
        client = CodexBrainClient()
        inventory = MagicMock()
        monkeypatch.setattr(
            client,
            "_await_orchestrator_ready",
            lambda timeout: "not ready",
        )
        monkeypatch.setattr(client, "_list_mcp_server_inventory", inventory)
        assert client._verify_orchestrator_mcp() == "not ready"
        inventory.assert_not_called()

    def test_verify_requires_episodic_memory_present(self, monkeypatch):
        """Parity with Claude Brain, which refuses to start without episodic-memory."""
        client = CodexBrainClient()
        orchestrator = {name: {"name": name} for name in self.REQUIRED}
        monkeypatch.setattr(client, "_await_orchestrator_ready", lambda timeout: None)
        monkeypatch.setattr(
            client,
            "_list_mcp_server_inventory",
            lambda: ({"orchestrator": {"tools": orchestrator}}, None),
        )

        reason = client._verify_orchestrator_mcp()

        assert reason is not None
        assert "episodic-memory" in reason

    def test_verify_passes_when_episodic_memory_present(self, monkeypatch):
        """Over-tightening guard: a check that always fails would still pass the case above.

        The EMPTY tools dict is deliberate. Claude Brain's mandate is existence-only
        (brain_client.discover_episodic_memory_path globs a path and raises
        FileNotFoundError); it never inspects which tools episodic-memory exposes. This
        pins that semantics, so a later change that starts requiring named tools breaks
        here rather than silently making Codex stricter than Claude.
        """
        client = CodexBrainClient()
        orchestrator = {name: {"name": name} for name in self.REQUIRED}
        monkeypatch.setattr(client, "_await_orchestrator_ready", lambda timeout: None)
        monkeypatch.setattr(
            client,
            "_list_mcp_server_inventory",
            lambda: (
                {
                    "orchestrator": {"tools": orchestrator},
                    "episodic-memory": {"tools": {}},
                },
                None,
            ),
        )

        assert client._verify_orchestrator_mcp() is None

    def test_start_failure_occurs_after_thread_start_before_first_turn(self, monkeypatch):
        import ironclaude.codex_brain_client as module

        class FakeProc:
            pid = 4321
            stdin = MagicMock()
            stdout = MagicMock()
            stderr = MagicMock()

            def poll(self):
                return None

            def terminate(self):
                pass

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 0

        client = CodexBrainClient()
        sent = []
        responses = [
            {"result": {}},
            {"result": {"thread": {"id": "thread-1"}}},
        ]
        monkeypatch.setattr(client, "_preflight_orchestrator", lambda: None)
        monkeypatch.setattr(client, "_reader_loop", lambda: None)
        monkeypatch.setattr(client, "_stderr_loop", lambda: None)
        monkeypatch.setattr(client, "_write", lambda payload: sent.append(payload) or True)
        monkeypatch.setattr(
            client,
            "_await_response",
            lambda request_id, timeout: responses.pop(0),
        )

        def fail_verification():
            assert client._thread_id == "thread-1"
            assert client._running is False
            assert client.send_message("forbidden") is False
            return "orchestrator not ready"

        monkeypatch.setattr(client, "_verify_orchestrator_mcp", fail_verification)
        monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
        client.start("", cwd="/tmp")
        assert sum(item.get("method") == "thread/start" for item in sent) == 1
        assert not any(item.get("method") == "turn/start" for item in sent)
        assert "orchestrator not ready" in client.restart_reason
        assert any(
            "[CODEX BRAIN ERROR]" in item
            for item in client.get_pending_responses()
        )


def test_app_server_argv_pins_configured_reasoning_effort():
    """A non-default effort proves _effort_level is read rather than hardcoded."""
    c = CodexBrainClient(effort_level="low")
    assert 'model_reasoning_effort="low"' in c._app_server_argv()


def test_optional_mcp_overrides_registers_research_and_ollama():
    """Claude Brain registers research + ollama (brain_client.py:716-725); codex must too."""
    c = CodexBrainClient()
    overrides = c._optional_mcp_overrides()
    joined = " ".join(overrides)
    assert "mcp_servers.research.enabled=true" in overrides
    assert "mcp_servers.ollama.enabled=true" in overrides
    assert "research_mcp.py" in joined
    assert "ollama_mcp.py" in joined


def test_optional_mcp_overrides_skips_a_missing_server(monkeypatch):
    """Mirrors brain_client.py:280-286, which registers a server only if its file exists.

    Without this the existence guard is untested and could be dropped silently.
    """
    c = CodexBrainClient()
    monkeypatch.setattr(
        CodexBrainClient,
        "_optional_mcp_source_path",
        lambda self, name: Path("/nonexistent") / f"{name}_mcp.py",
    )
    assert c._optional_mcp_overrides() == []


def test_optional_mcp_servers_are_not_auto_approved():
    """ollama's pull/remove/create_model are gated by codex-brain-gated-actions.sh:63-65.

    The orchestrator arm sets default_tools_approval_mode="approve"; copying that here
    would auto-approve exactly those destructive tools. This test is what catches a later
    edit that clones the orchestrator arm wholesale.
    """
    c = CodexBrainClient()
    joined = " ".join(c._optional_mcp_overrides())
    assert "mcp_servers.research.default_tools_approval_mode" not in joined
    assert "mcp_servers.ollama.default_tools_approval_mode" not in joined
