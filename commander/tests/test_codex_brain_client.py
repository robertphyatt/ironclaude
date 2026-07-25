import os
import time
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
        monkeypatch.setattr(c, "_reader_loop", lambda: None)
        monkeypatch.setattr(c, "_stderr_loop", lambda: None)
        c.start("system prompt", cwd="/tmp")
        assert recorded["env"] is not None, "start() must pass env= so IC_ROLE reaches the app-server"
        assert recorded["env"]["IC_ROLE"] == "brain"
        # env= REPLACES inheritance: pin the MERGED env at the Popen boundary so an
        # implementation passing a bare {"IC_ROLE": "brain"} (stripping auth/PATH) fails.
        assert recorded["env"].get("IC_TEST_AMBIENT") == "kept"
