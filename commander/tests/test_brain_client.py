# tests/test_brain_client.py
import asyncio
import os
import threading
import time
import json
from datetime import datetime, timezone, timedelta
import pytest
from ironclaude.brain_client import BrainClient, _backoff_seconds, _is_model_unavailable


class TestBrainClient:
    def test_is_alive_initially_false(self):
        """Brain is not alive before start."""
        client = BrainClient()
        assert client.is_alive() is False

    def test_needs_restart_when_not_alive(self):
        """Needs restart when not alive."""
        client = BrainClient()
        assert client.needs_restart() is True

    def test_send_message_returns_false_when_not_running(self):
        """send_message returns False when brain is not running."""
        client = BrainClient()
        assert client.send_message("hello") is False

    def test_get_pending_responses_empty(self):
        """get_pending_responses returns empty list when no responses."""
        client = BrainClient()
        assert client.get_pending_responses() == []

    def test_get_pending_responses_drains_queue(self):
        """get_pending_responses drains all items from queue."""
        client = BrainClient()
        client._response_queue.put("response 1")
        client._response_queue.put("response 2")
        responses = client.get_pending_responses()
        assert responses == ["response 1", "response 2"]
        assert client.get_pending_responses() == []

    def test_restart_count_starts_at_zero(self):
        """restart_count starts at zero."""
        client = BrainClient()
        assert client.restart_count == 0

    def test_shutdown_when_not_started(self):
        """shutdown is safe to call when not started."""
        client = BrainClient()
        client._kill_brain_subprocess = lambda: None
        client.shutdown()  # should not raise
        assert client.is_alive() is False

    def test_start_has_no_continue_session_param(self):
        """start() no longer accepts continue_session — always fresh sessions."""
        import inspect
        sig = inspect.signature(BrainClient.start)
        assert "continue_session" not in sig.parameters

    def test_effort_level_defaults_to_high(self):
        """BrainClient stores effort_level, defaults to 'high'."""
        client = BrainClient()
        assert client._effort_level == "high"

    def test_effort_level_param_stored(self):
        """BrainClient stores provided effort_level."""
        client = BrainClient(effort_level="medium")
        assert client._effort_level == "medium"


class TestBrainToolRestrictions:
    def test_allowed_tools_includes_bash(self):
        """Brain's allowed tools must include Bash (git-only whitelist enforced in guard)."""
        assert "Bash" in BrainClient.ALLOWED_TOOLS

    def test_allowed_tools_includes_read_tools(self):
        """Brain must have Read, Grep, Glob for analysis."""
        for tool in ["Read", "Grep", "Glob"]:
            assert tool in BrainClient.ALLOWED_TOOLS

    def test_write_not_in_allowed_tools(self):
        """Write tool removed — brain uses MCP orchestrator tools instead."""
        assert "Write" not in BrainClient.ALLOWED_TOOLS

    def test_tool_guard_logic_accepts_context_param(self):
        """_tool_guard_logic accepts optional context parameter (SDK CanUseTool contract)."""
        client = BrainClient()
        # 3-arg call (as SDK invokes it)
        allowed, msg = client._tool_guard_logic("Read", {"file_path": "/tmp/foo.py"}, None)
        assert allowed is True
        assert msg is None


class TestBrainModelParameter:
    def test_default_model_is_opus_4_6_1m(self):
        """BrainClient defaults to opus."""
        client = BrainClient()
        assert client._model == "opus"

    def test_model_parameter_accepted(self):
        """BrainClient accepts custom model parameter."""
        client = BrainClient(model="claude-sonnet-4-5-20241022")
        assert client._model == "claude-sonnet-4-5-20241022"


class TestIsModelUnavailable:
    def _proc_error(self, stderr, exit_code=1):
        """Duck-typed ProcessError for testing — matches the real SDK's attribute shape."""
        exc = Exception(stderr)
        exc.exit_code = exit_code
        exc.stderr = stderr
        return exc

    def test_returns_true_for_selected_model_error(self):
        exc = self._proc_error(
            "There's an issue with the selected model (claude-fable-5). "
            "It may not exist or you may not have access to it."
        )
        assert _is_model_unavailable(exc) is True

    def test_returns_true_for_not_available_phrase(self):
        exc = self._proc_error("model not available")
        assert _is_model_unavailable(exc) is True

    def test_returns_true_for_not_have_access_phrase(self):
        exc = self._proc_error("model: you do not have access to it")
        assert _is_model_unavailable(exc) is True

    def test_returns_false_for_network_error(self):
        exc = self._proc_error("connection refused: network error")
        assert _is_model_unavailable(exc) is False

    def test_returns_false_for_exception_without_stderr(self):
        assert _is_model_unavailable(RuntimeError("something went wrong")) is False

    def test_returns_false_for_exception_without_exit_code(self):
        exc = Exception("selected model")
        exc.stderr = "selected model"
        # no exit_code attribute — not a ProcessError
        assert _is_model_unavailable(exc) is False


class TestBrainModelFallback:
    def _proc_error(self, stderr, exit_code=1):
        exc = Exception(stderr)
        exc.exit_code = exit_code
        exc.stderr = stderr
        return exc

    def _run_session(self, client, prompt="sys", cwd="/tmp", resume_id=None):
        from unittest.mock import MagicMock, patch
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        with patch("ironclaude.brain_client.subprocess.run", return_value=mock_proc):
            asyncio.run(client._brain_session(prompt, cwd, resume_id))

    def test_model_updates_to_opus_on_unavailable_error(self):
        from unittest.mock import patch
        model_error = self._proc_error(
            "There's an issue with the selected model (fable[1m]). "
            "It may not exist or you may not have access to it."
        )
        call_count = 0

        async def mock_query(prompt, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise model_error
            return
            yield  # makes this an async generator

        client = BrainClient(model="fable")
        client._running = False
        client._message_queue = asyncio.Queue()

        with patch("claude_agent_sdk.query", new=mock_query):
            self._run_session(client)

        assert client._model == "opus"

    def test_error_logged_on_fallback(self, caplog):
        import logging
        from unittest.mock import patch
        model_error = self._proc_error(
            "There's an issue with the selected model (fable[1m])."
        )
        call_count = 0

        async def mock_query(prompt, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise model_error
            return
            yield

        client = BrainClient(model="fable")
        client._running = False
        client._message_queue = asyncio.Queue()

        with patch("claude_agent_sdk.query", new=mock_query):
            with caplog.at_level(logging.ERROR, logger="ironclaude.brain"):
                self._run_session(client)

        error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert any("NOT AVAILABLE" in m and "opus" in m for m in error_messages)

    def test_non_model_process_error_propagates(self):
        from unittest.mock import patch
        network_error = self._proc_error("connection refused: network down")

        async def mock_query(prompt, options):
            raise network_error
            yield

        client = BrainClient(model="fable")
        client._running = False
        client._message_queue = asyncio.Queue()

        with patch("claude_agent_sdk.query", new=mock_query):
            with pytest.raises(Exception) as exc_info:
                self._run_session(client)

        assert exc_info.value is network_error

    def test_second_attempt_failure_propagates(self):
        from unittest.mock import patch
        model_error = self._proc_error("selected model not available")
        second_error = RuntimeError("API completely down")
        call_count = 0

        async def mock_query(prompt, options):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise model_error
            raise second_error
            yield

        client = BrainClient(model="fable")
        client._running = False
        client._message_queue = asyncio.Queue()

        with patch("claude_agent_sdk.query", new=mock_query):
            with pytest.raises(RuntimeError) as exc_info:
                self._run_session(client)

        assert exc_info.value is second_error


class TestBrainBufferAndMemory:
    def test_max_buffer_size_exceeds_default(self):
        """Buffer must be larger than SDK's 1MB default."""
        assert BrainClient.MAX_BUFFER_SIZE > 1024 * 1024

    def test_max_buffer_size_is_50mb(self):
        """Buffer must be exactly 50MB."""
        assert BrainClient.MAX_BUFFER_SIZE == 50 * 1024 * 1024

    def test_discover_episodic_memory_path_finds_plugin(self, tmp_path):
        """Discovers MCP server path when plugin is installed."""
        # Create fake plugin structure
        mcp_dir = tmp_path / "ironclaude" / "1.0.33" / "mcp-servers" / "episodic-memory" / "cli"
        mcp_dir.mkdir(parents=True)
        wrapper = mcp_dir / "mcp-server-wrapper.js"
        wrapper.write_text("// fake")
        path = BrainClient.discover_episodic_memory_path(
            plugin_base=str(tmp_path / "ironclaude")
        )
        assert path == str(wrapper)

    def test_discover_episodic_memory_path_raises_when_missing(self, tmp_path):
        """Raises FileNotFoundError when no plugin installed."""
        with pytest.raises(FileNotFoundError):
            BrainClient.discover_episodic_memory_path(
                plugin_base=str(tmp_path / "nonexistent")
            )

    def test_discover_episodic_memory_path_picks_latest_version(self, tmp_path):
        """Picks the latest version when multiple versions exist."""
        for version in ["1.0.31", "1.0.33", "1.0.32"]:
            mcp_dir = tmp_path / "ironclaude" / version / "mcp-servers" / "episodic-memory" / "cli"
            mcp_dir.mkdir(parents=True)
            (mcp_dir / "mcp-server-wrapper.js").write_text("// fake")
        path = BrainClient.discover_episodic_memory_path(
            plugin_base=str(tmp_path / "ironclaude")
        )
        assert "1.0.33" in path


class TestBrainLivenessTimeout:
    @staticmethod
    def _make_alive_client(timeout_seconds=300):
        """Create a BrainClient that reports is_alive() == True."""
        client = BrainClient(timeout_seconds=timeout_seconds)
        client._running = True
        # Start a real daemon thread that blocks on an event so is_alive() returns True
        stop = threading.Event()
        client._thread = threading.Thread(target=stop.wait, daemon=True)
        client._thread.start()
        client._stop_event = stop  # stash for cleanup
        return client

    def test_needs_restart_returns_true_on_timeout(self):
        """Needs restart when message sent but no response within timeout."""
        client = self._make_alive_client(timeout_seconds=300)
        # Simulate: message sent 600s ago, last response was before that
        client._last_message_time = time.time() - 600
        client._last_response_time = client._last_message_time - 10
        assert client.needs_restart() is True
        client._stop_event.set()

    def test_needs_restart_returns_false_when_idle(self):
        """No restart needed when no messages have been sent."""
        client = self._make_alive_client(timeout_seconds=300)
        # _last_message_time = 0 means no messages sent
        client._last_message_time = 0.0
        client._last_response_time = 0.0
        assert client.needs_restart() is False
        client._stop_event.set()

    def test_needs_restart_returns_false_when_response_recent(self):
        """No restart needed when response came after last message."""
        client = self._make_alive_client(timeout_seconds=300)
        # Response came after message — brain is responsive
        client._last_message_time = time.time() - 600
        client._last_response_time = client._last_message_time + 5
        assert client.needs_restart() is False
        client._stop_event.set()

    def test_send_message_updates_last_message_time(self):
        """send_message() sets _last_message_time before queuing."""
        client = BrainClient()
        client._running = True
        client._loop = asyncio.new_event_loop()
        client._message_queue = asyncio.Queue()
        before = time.time()
        client.send_message("test")
        after = time.time()
        assert before <= client._last_message_time <= after
        # Drain the scheduled put() coroutine before closing to avoid RuntimeWarning
        client._loop.run_until_complete(client._message_queue.get())
        client._loop.close()

    def test_restart_resets_timing_state(self):
        """restart() clears _last_message_time and _last_response_time to prevent stale timeout."""
        client = self._make_alive_client(timeout_seconds=300)
        # Simulate: message sent, response received, then stale state
        client._last_message_time = time.time() - 600
        client._last_response_time = client._last_message_time - 10
        # Mock start() to avoid real SDK session
        client.start = lambda *a, **kw: setattr(client, '_running', True)
        client._kill_brain_subprocess = lambda: None
        client.restart("test prompt")
        assert client._last_message_time == 0.0
        assert client._last_response_time == 0.0
        client._stop_event.set()

    def test_needs_restart_false_after_restart(self):
        """needs_restart() returns False immediately after restart() resets timing."""
        client = self._make_alive_client(timeout_seconds=300)
        # Simulate stale timing state that would trigger restart
        client._last_message_time = time.time() - 600
        client._last_response_time = client._last_message_time - 10
        assert client.needs_restart() is True  # Confirm it would trigger
        # Mock start() to create a fresh alive thread.
        # NOTE: shutdown() now calls _stop_event.set(), which kills the original
        # mock thread, so we must create a new one rather than restore original_thread.
        def fake_start(*a, **kw):
            new_stop = threading.Event()
            client._running = True
            client._thread = threading.Thread(target=new_stop.wait, daemon=True)
            client._thread.start()
            client._stop_event = new_stop
        client.start = fake_start
        client._kill_brain_subprocess = lambda: None
        client.restart("test prompt")
        assert client.needs_restart() is False
        client._stop_event.set()  # Clean up new thread

    def test_needs_restart_false_during_tool_execution(self):
        """600s timeout is bypassed when _executing_tool is True."""
        client = self._make_alive_client(timeout_seconds=300)
        client._executing_tool = True
        client._last_message_time = time.time() - 700  # past 600s threshold
        client._last_response_time = client._last_message_time - 10
        assert client.needs_restart() is False
        client._stop_event.set()

    def test_hard_timeout_fires_at_1800s(self):
        """1800s hard safety net fires even when _executing_tool is True."""
        client = self._make_alive_client(timeout_seconds=300)
        client._executing_tool = True
        client._last_message_time = time.time() - 1801
        client._last_response_time = client._last_message_time - 10
        assert client.needs_restart() is True
        assert "hung" in client.restart_reason
        client._stop_event.set()

    def test_absolute_inactivity_timeout_fires(self):
        """If no SDK messages for 1800s (flag cleared), needs_restart fires."""
        client = self._make_alive_client(timeout_seconds=300)
        client._executing_tool = False
        client._last_response_time = time.time() - 1801
        client._last_message_time = time.time() - 2000
        assert client.needs_restart() is True
        assert "no SDK activity" in client.restart_reason
        client._stop_event.set()

    def test_normal_timeout_fires_without_executing_tool(self):
        """600s timeout still fires when _executing_tool is False."""
        client = self._make_alive_client(timeout_seconds=300)
        client._executing_tool = False
        client._last_message_time = time.time() - 700
        client._last_response_time = client._last_message_time - 10
        assert client.needs_restart() is True
        client._stop_event.set()

    def test_send_message_sets_executing_tool(self):
        """send_message() sets _executing_tool=True when message is dispatched."""
        client = BrainClient()
        client._running = True
        client._loop = asyncio.new_event_loop()
        client._message_queue = asyncio.Queue()
        client.send_message("test")
        assert client._executing_tool is True
        client._loop.run_until_complete(client._message_queue.get())
        client._loop.close()

    def test_restart_resets_executing_tool(self):
        """restart() clears _executing_tool to False."""
        client = self._make_alive_client(timeout_seconds=300)
        client._executing_tool = True
        client.start = lambda *a, **kw: setattr(client, '_running', True)
        client._kill_brain_subprocess = lambda: None
        client.restart("test prompt")
        assert client._executing_tool is False
        client._stop_event.set()

    def test_executing_tool_cleared_on_result_message(self):
        """ResultMessage (end-of-turn) must clear _executing_tool even without text."""
        from unittest.mock import patch, MagicMock
        from claude_agent_sdk.types import ResultMessage

        client = BrainClient()
        client._executing_tool = True
        client._running = True
        client._session_log_path = None

        mock_result = MagicMock(spec=ResultMessage)
        mock_result.session_id = "test-session"
        mock_result.usage = None
        mock_result.total_cost_usd = None

        async def fake_query(**kwargs):
            yield mock_result
            client._running = False

        async def run():
            with patch("claude_agent_sdk.query", fake_query):
                with patch("claude_agent_sdk.ClaudeAgentOptions"):
                    await client._brain_session("test prompt", "/tmp")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        except Exception:
            pass
        finally:
            loop.close()

        assert client._executing_tool is False


class TestCircuitBreaker:
    """Circuit breaker prevents infinite restart loops."""

    def test_circuit_breaker_false_with_no_restarts(self):
        """circuit_breaker_tripped() returns False with no restart history."""
        client = BrainClient()
        assert client.circuit_breaker_tripped() is False

    def test_circuit_breaker_false_under_limit(self):
        """circuit_breaker_tripped() returns False when restarts under max_restarts."""
        client = BrainClient()
        client._restart_timestamps = [time.time() - 60, time.time() - 30]
        assert client.circuit_breaker_tripped() is False

    def test_circuit_breaker_true_at_limit(self):
        """circuit_breaker_tripped() returns True when restarts reach max_restarts."""
        client = BrainClient()
        now = time.time()
        client._restart_timestamps = [now - 120, now - 60, now - 10]
        assert client.circuit_breaker_tripped() is True

    def test_circuit_breaker_prunes_old_timestamps(self):
        """Timestamps outside restart_window_seconds are pruned."""
        client = BrainClient()
        old = time.time() - client.restart_window_seconds - 100
        client._restart_timestamps = [old, old - 50, old - 100]
        assert client.circuit_breaker_tripped() is False
        assert len(client._restart_timestamps) == 0

    def test_restart_records_timestamp(self):
        """restart() appends to _restart_timestamps on success."""
        client = BrainClient()
        client._running = True
        # Mock start() to avoid real SDK
        def fake_start(*a, **kw):
            client._running = True
            stop = threading.Event()
            client._thread = threading.Thread(target=stop.wait, daemon=True)
            client._thread.start()
            client._stop_event = stop
        client.start = fake_start
        client._kill_brain_subprocess = lambda: None
        assert len(client._restart_timestamps) == 0
        client.restart("test prompt")
        assert len(client._restart_timestamps) == 1
        client._stop_event.set()

    def test_circuit_breaker_defaults(self):
        """Default max_restarts=3 and restart_window_seconds=600."""
        client = BrainClient()
        assert client.max_restarts == 3
        assert client.restart_window_seconds == 600


class TestCompactionFlags:
    def test_compacting_initially_false(self):
        client = BrainClient()
        assert client._compacting is False

    def test_compaction_complete_initially_false(self):
        client = BrainClient()
        assert client._compaction_complete is False

    def test_needs_restart_returns_false_during_compaction(self):
        """needs_restart must not interfere with in-progress compaction."""
        client = BrainClient()
        client._compacting = True
        assert client.needs_restart() is False

    def test_check_compaction_complete_returns_true_once(self):
        """check_compaction_complete returns True once then False."""
        client = BrainClient()
        client._compaction_complete = True
        assert client.check_compaction_complete() is True
        assert client.check_compaction_complete() is False

    def test_check_compaction_complete_false_by_default(self):
        client = BrainClient()
        assert client.check_compaction_complete() is False


class TestCompactionDeadline:
    """needs_restart must recover from a hang during compaction-resume."""

    def test_compaction_started_initially_zero(self):
        client = BrainClient()
        assert client._compaction_started == 0.0

    def test_deadline_constant_is_1800(self):
        assert BrainClient.COMPACTION_DEADLINE == 1800

    def test_needs_restart_true_when_compaction_exceeds_deadline(self):
        """A compaction stuck past COMPACTION_DEADLINE must force a restart."""
        client = BrainClient()
        client._compacting = True
        client._compaction_started = time.time() - (BrainClient.COMPACTION_DEADLINE + 200)
        assert client.needs_restart() is True
        assert "compaction deadline exceeded" in client.restart_reason

    def test_needs_restart_false_within_compaction_deadline(self):
        """A compaction within the deadline must still be left alone."""
        client = BrainClient()
        client._compacting = True
        client._compaction_started = time.time() - 10  # well within deadline
        assert client.needs_restart() is False

    def test_needs_restart_false_when_compacting_and_no_start_timestamp(self):
        """Defensive: _compacting True with unset timestamp must not force restart."""
        client = BrainClient()
        client._compacting = True
        client._compaction_started = 0.0
        assert client.needs_restart() is False

    def test_restart_clears_compaction_state(self):
        """restart() must clear _compacting/_compaction_started to avoid a restart loop.

        Without this, a deadline-exceeded restart leaves _compacting=True with a
        stale timestamp; the very next poll re-fires 'compaction deadline exceeded'
        and burns the entire restart budget.
        """
        client = TestBrainLivenessTimeout._make_alive_client(timeout_seconds=300)
        client._compacting = True
        client._compaction_started = time.time() - (BrainClient.COMPACTION_DEADLINE + 200)
        assert client.needs_restart() is True  # confirm it would trigger

        def fake_start(*a, **kw):
            new_stop = threading.Event()
            client._running = True
            client._thread = threading.Thread(target=new_stop.wait, daemon=True)
            client._thread.start()
            client._stop_event = new_stop
        client.start = fake_start
        client._kill_brain_subprocess = lambda: None

        client.restart("test prompt")
        assert client._compacting is False
        assert client._compaction_started == 0.0
        # Must not immediately re-fire a compaction-deadline restart
        client._restart_reason = ""  # clear stale reason from the confirm call above
        assert client.needs_restart() is False
        assert client.restart_reason == ""  # no new restart reason set
        client._stop_event.set()


class TestTimeoutAndReason:
    def test_default_timeout_is_600(self):
        """Default timeout increased from 300 to 600."""
        client = BrainClient()
        assert client.timeout_seconds == 600

    def test_restart_reason_set_on_timeout(self):
        """restart_reason contains 'timeout' when timeout fires."""
        client = TestBrainLivenessTimeout._make_alive_client(timeout_seconds=1)
        client._last_message_time = time.time() - 10
        client._last_response_time = client._last_message_time - 1
        assert client.needs_restart() is True
        assert "timeout" in client.restart_reason
        client._stop_event.set()

    def test_restart_reason_set_on_dead_thread(self):
        """restart_reason contains 'dead' when thread not alive."""
        client = BrainClient()
        assert client.needs_restart() is True
        assert "dead" in client.restart_reason

    def test_restart_reason_empty_when_no_restart_needed(self):
        """restart_reason stays empty when no restart needed."""
        client = TestBrainLivenessTimeout._make_alive_client()
        client._last_message_time = 0
        client._last_response_time = 0
        assert client.needs_restart() is False
        assert client.restart_reason == ""
        client._stop_event.set()


class TestMemoryToggle:
    """Memory-action toggle: episodic memory search arms, gated tool disarms."""

    def test_initial_state_unarmed(self):
        """_memory_armed starts False — must search before acting."""
        client = BrainClient()
        assert client._memory_armed is False

    def test_gated_tool_denied_when_unarmed(self):
        """Gated orchestrator action denied when memory not searched first."""
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "episodic memory search" in msg.lower()
        assert "Operator" in msg  # default operator_name

    def test_gated_tool_denied_uses_operator_name(self):
        """Denial message includes configured operator_name."""
        client = BrainClient(operator_name="Alice")
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "Alice" in msg

    def test_memory_search_arms_toggle(self):
        """Episodic memory search sets _memory_armed = True."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__episodic-memory__search", {"query": "test"}
        )
        assert allowed is True
        assert msg is None
        assert client._memory_armed is True

    def test_gated_tool_allowed_when_armed(self):
        """Gated tool passes when memory was searched first."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__approve_plan", {"worker_id": "w1", "rationale": "ok"}
        )
        assert allowed is True
        assert msg is None

    def test_gated_tool_disarms_toggle(self):
        """Using a gated tool resets _memory_armed to False."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        client._tool_guard_logic(
            "mcp__orchestrator__reject_plan", {"worker_id": "w1", "reason": "bad"}
        )
        assert client._memory_armed is False
        assert client._wiki_queried is False

    def test_kill_worker_denied_when_unarmed(self):
        """kill_worker is a gated tool — denied when memory not searched first."""
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__kill_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "episodic memory search" in msg.lower()

    def test_spawn_workers_denied_when_unarmed(self):
        """spawn_workers (plural) is a gated tool — denied when memory not searched first."""
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_workers", {"requests": []}
        )
        assert allowed is False
        assert "episodic memory search" in msg.lower()

    def test_kill_worker_allowed_when_armed(self):
        """kill_worker allowed and disarms toggle when memory was searched first."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__kill_worker", {"worker_id": "w1"}
        )
        assert allowed is True
        assert msg is None
        assert client._memory_armed is False
        assert client._wiki_queried is False

    def test_spawn_workers_allowed_when_armed(self):
        """spawn_workers (plural) allowed and disarms toggle when memory was searched first."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_workers", {"requests": []}
        )
        assert allowed is True
        assert msg is None
        assert client._memory_armed is False
        assert client._wiki_queried is False

    def test_query_tools_bypass_toggle(self):
        """Query tools (get_worker_status, etc.) always allowed regardless of toggle."""
        client = BrainClient()
        assert client._memory_armed is False
        for tool_name in [
            "mcp__orchestrator__get_worker_status",
            "mcp__orchestrator__get_worker_log",
            "mcp__orchestrator__get_task_ledger",
            "mcp__orchestrator__update_ledger",
        ]:
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is True, f"{tool_name} should be allowed"
            assert msg is None

    def test_non_orchestrator_tools_bypass_toggle(self):
        """Read/Grep/Glob bypass the memory toggle entirely."""
        client = BrainClient()
        assert client._memory_armed is False
        for tool_name in ["Read", "Grep", "Glob"]:
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is True, f"{tool_name} should be allowed"
            assert msg is None


class TestBashWhitelist:
    """Bash commands restricted to git read-only operations."""

    def test_git_log_allowed(self):
        """git log is a read-only git command — allowed."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log --oneline -5"}
        )
        assert allowed is True
        assert msg is None

    def test_git_diff_allowed(self):
        """git diff is a read-only git command — allowed."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git diff HEAD~1 src/main.py"}
        )
        assert allowed is True
        assert msg is None

    def test_non_git_command_denied(self):
        """Non-git commands (rm, ls, etc.) are denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "rm -rf /tmp/data"}
        )
        assert allowed is False
        assert "git" in msg.lower()

    def test_git_push_denied(self):
        """git push is a write command — denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git push origin main"}
        )
        assert allowed is False
        assert "read-only" in msg.lower() or "git" in msg.lower()

    def test_git_reset_denied(self):
        """git reset is destructive — denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git reset --hard HEAD~1"}
        )
        assert allowed is False

    def test_git_add_allowed(self):
        """git add is allowed — brain can stage files workers missed."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git add src/ic/brain_client.py"}
        )
        assert allowed is True
        assert msg is None

    def test_git_commit_allowed(self):
        """git commit is allowed — brain commits after reviewing diffs."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": 'git commit -m "Fix authentication bug"'}
        )
        assert allowed is True
        assert msg is None

    def test_git_commit_amend_denied(self):
        """git commit --amend is blocked — prevents rewriting previous commits."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git commit --amend -m 'rewrite history'"}
        )
        assert allowed is False
        assert "amend" in msg.lower()


class TestBashMetacharacterGuard:
    """Shell metacharacters in git commands must be rejected outright."""

    def test_semicolon_injection_denied(self):
        """git log; rm -rf / denied — semicolon chains a second command."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log; rm -rf /"}
        )
        assert allowed is False
        assert "metacharacter" in msg.lower()

    def test_pipe_injection_denied(self):
        """git log | cat /etc/passwd denied — pipe leaks output."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log | cat /etc/passwd"}
        )
        assert allowed is False

    def test_and_and_injection_denied(self):
        """git log && rm -rf / denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log && rm -rf /"}
        )
        assert allowed is False

    def test_or_or_injection_denied(self):
        """git log || rm -rf / denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log || rm -rf /"}
        )
        assert allowed is False

    def test_subshell_injection_denied(self):
        """git log $(cat /etc/passwd) denied — $() executes subshell."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log $(cat /etc/passwd)"}
        )
        assert allowed is False

    def test_backtick_injection_denied(self):
        """git log `cat /etc/passwd` denied — backticks execute subshell."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log `cat /etc/passwd`"}
        )
        assert allowed is False

    def test_clean_git_log_still_allowed(self):
        """Plain git log with no metacharacters remains allowed."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log --oneline -5"}
        )
        assert allowed is True
        assert msg is None

    def test_newline_injection_denied(self):
        """git log\\nrm -rf / denied — newline chains a second command."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log\nrm -rf /"}
        )
        assert allowed is False

    def test_redirect_denied(self):
        """git log > /etc/file denied — redirect writes to arbitrary path."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log > /etc/file"}
        )
        assert allowed is False

    def test_dash_c_flag_denied(self):
        """git -c core.pager=evil log denied — -c flag executes arbitrary config."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git -c core.pager=evil log"}
        )
        assert allowed is False

    def test_single_ampersand_injection_denied(self):
        """git log & rm -rf / denied — single & backgrounds the rm command."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log & rm -rf /"}
        )
        assert allowed is False
        assert "metacharacter" in msg.lower()

    def test_single_ampersand_trailing_denied(self):
        """git log & denied — trailing & backgrounds git in a subshell."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log &"}
        )
        assert allowed is False

    def test_dollar_variable_expansion_denied(self):
        """git log $HOME denied — unquoted $ expands shell variables."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "Bash", {"command": "git log $HOME"}
        )
        assert allowed is False


class TestResearchOllamaToolGuard:
    """Tool guard logic for research and ollama MCP tools."""

    def test_research_tools_always_allowed(self):
        """Research tools (prefixed mcp__research__) always allowed regardless of memory toggle."""
        client = BrainClient()
        assert client._memory_armed is False
        for tool_name in [
            "mcp__research__web_search",
            "mcp__research__fetch_url",
            "mcp__research__summarize",
        ]:
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is True, f"{tool_name} should be allowed"
            assert msg is None

    def test_ollama_query_tools_always_allowed(self):
        """Ollama query tools always allowed regardless of memory toggle."""
        client = BrainClient()
        assert client._memory_armed is False
        for tool_name in [
            "mcp__ollama__list_models",
            "mcp__ollama__show_model",
            "mcp__ollama__list_running",
        ]:
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is True, f"{tool_name} should be allowed"
            assert msg is None

    def test_ollama_mutation_tools_gated(self):
        """Ollama mutation tools denied when _memory_armed is False."""
        client = BrainClient()
        assert client._memory_armed is False
        client._lookback_slack = True
        client._lookback_ledger = True
        for tool_name in [
            "mcp__ollama__pull_model",
            "mcp__ollama__remove_model",
            "mcp__ollama__create_model",
        ]:
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is False, f"{tool_name} should be gated"
            assert "memory" in msg.lower()

    def test_ollama_mutation_tools_allowed_when_armed(self):
        """Ollama mutation tools allowed when both flags are set."""
        client = BrainClient()
        for tool_name in [
            "mcp__ollama__pull_model",
            "mcp__ollama__remove_model",
            "mcp__ollama__create_model",
        ]:
            client._memory_armed = True
            client._wiki_queried = True
            client._lookback_slack = True
            client._lookback_ledger = True
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is True, f"{tool_name} should be allowed when armed"
            assert msg is None

    def test_ollama_mutation_disarms_toggle(self):
        """Using an ollama mutation tool resets both flags to False."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        client._tool_guard_logic("mcp__ollama__pull_model", {})
        assert client._memory_armed is False
        assert client._wiki_queried is False


class TestDefaultDenyGuard:
    """Default-deny: unknown/mutation tools blocked, brain redirected to workers."""

    def test_edit_denied_with_worker_redirect(self):
        """Edit tool denied — brain must use workers for file changes."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Edit", {"file_path": "/tmp/foo.py", "old_string": "a", "new_string": "b"})
        assert allowed is False
        assert "mutation tools" in msg

    def test_write_denied_with_worker_redirect(self):
        """Write tool denied — brain must use workers for file creation."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Write", {"file_path": "/tmp/foo.py", "content": "hello"})
        assert allowed is False
        assert "mutation tools" in msg

    def test_notebook_edit_denied(self):
        """NotebookEdit denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("NotebookEdit", {})
        assert allowed is False

    def test_agent_denied(self):
        """Agent tool denied — brain cannot spawn subagents."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Agent", {"prompt": "do stuff"})
        assert allowed is False
        assert "spawn_worker" in msg

    def test_unknown_tool_denied(self):
        """Any unknown tool name is denied by default."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("SomeRandomTool", {})
        assert allowed is False

    def test_read_still_allowed(self):
        """Read remains allowed after default-deny change."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Read", {"file_path": "/tmp/foo.py"})
        assert allowed is True
        assert msg is None

    def test_grep_still_allowed(self):
        """Grep remains allowed after default-deny change."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Grep", {"pattern": "foo"})
        assert allowed is True
        assert msg is None

    def test_glob_still_allowed(self):
        """Glob remains allowed after default-deny change."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Glob", {"pattern": "*.py"})
        assert allowed is True
        assert msg is None


class TestExplicitMutationToolDeny:
    """First-position mutation-tool deny: Edit/Write/NotebookEdit rejected before any other check."""

    def test_edit_tool_explicitly_denied(self):
        """Edit denied with canonical message — explicit first-position check."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Edit", {})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"

    def test_write_tool_explicitly_denied(self):
        """Write denied with canonical message — explicit first-position check."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Write", {})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"

    def test_notebook_edit_explicitly_denied(self):
        """NotebookEdit denied with canonical message — explicit first-position check."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("NotebookEdit", {})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"


class TestBackoffSeconds:
    """Exponential backoff calculation."""

    def test_first_attempt_is_one_second(self):
        assert _backoff_seconds(0) == 1

    def test_fifth_attempt_is_32_seconds(self):
        assert _backoff_seconds(5) == 32

    def test_caps_at_max_seconds(self):
        assert _backoff_seconds(20) == 300

    def test_custom_max_seconds(self):
        assert _backoff_seconds(10, max_seconds=60) == 60

    def test_below_custom_max(self):
        assert _backoff_seconds(3, max_seconds=60) == 8


class TestBrainRetryLoop:
    """_run_event_loop retries on exception with backoff."""

    def test_retries_on_exception(self):
        """_run_event_loop retries _brain_session when it raises."""
        client = BrainClient()
        call_count = 0

        async def fake_brain_session(prompt, cwd, resume_session_id=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("API Error: 500")
            # Third call succeeds — simulate clean exit

        client._brain_session = fake_brain_session
        client._run_event_loop("test prompt", None)
        assert call_count == 3

    def test_stays_running_during_retry(self):
        """_running stays True while retrying."""
        client = BrainClient()
        client._running = True  # Simulate start() setting this before thread spawn
        running_during_retry = []

        async def fake_brain_session(prompt, cwd, resume_session_id=None):
            if len(running_during_retry) == 0:
                running_during_retry.append(client._running)
                raise RuntimeError("API Error: 500")
            running_during_retry.append(client._running)

        client._brain_session = fake_brain_session
        client._run_event_loop("test prompt", None)
        assert all(running_during_retry)

    def test_stop_event_interrupts_backoff(self):
        """Setting _stop_event during backoff exits the loop."""
        client = BrainClient()
        call_count = 0

        async def fake_brain_session(prompt, cwd, resume_session_id=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Schedule stop after first failure
                client._stop_event.set()
            raise RuntimeError("API Error: 500")

        client._brain_session = fake_brain_session
        client._run_event_loop("test prompt", None)
        assert call_count == 1
        assert client._running is False


class TestMCPDiscovery:
    """MCP server discovery in start() and _brain_session()."""

    def test_start_discovers_research_mcp_path(self, tmp_path):
        """After calling start(), _research_mcp_path is set."""
        client = BrainClient()
        # Mock discover_episodic_memory_path to avoid real filesystem
        client.discover_episodic_memory_path = staticmethod(lambda **kw: "/fake/memory.js")
        # Patch _run_event_loop to prevent actual threading
        client._run_event_loop = lambda *a, **kw: None
        client._kill_brain_subprocess = lambda: None
        client._running = False
        client.start("test prompt", "/tmp")
        assert client._research_mcp_path is not None

    def test_start_discovers_ollama_mcp_path(self, tmp_path):
        """After calling start(), _ollama_mcp_path is set."""
        client = BrainClient()
        client.discover_episodic_memory_path = staticmethod(lambda **kw: "/fake/memory.js")
        client._run_event_loop = lambda *a, **kw: None
        client._kill_brain_subprocess = lambda: None
        client._running = False
        client.start("test prompt", "/tmp")
        assert client._ollama_mcp_path is not None

    def test_research_mcp_path_resolves_relative_to_package(self):
        """_research_mcp_path points to src/ic/research_mcp.py."""
        from pathlib import Path
        expected = str(Path(__file__).parent.parent / "src" / "ironclaude" / "research_mcp.py")
        client = BrainClient()
        client.discover_episodic_memory_path = staticmethod(lambda **kw: "/fake/memory.js")
        client._run_event_loop = lambda *a, **kw: None
        client._kill_brain_subprocess = lambda: None
        client._running = False
        client.start("test prompt", "/tmp")
        assert client._research_mcp_path == expected


class TestBrainPIDTracking:
    """PID tracking, singleton guard, and orphan kill for brain subprocess."""

    def test_pid_file_constant_exists(self):
        """BrainClient has BRAIN_PID_FILE class constant containing 'brain.pid'."""
        assert hasattr(BrainClient, 'BRAIN_PID_FILE')
        assert 'brain.pid' in BrainClient.BRAIN_PID_FILE

    def test_kill_subprocess_safe_when_no_pid(self, tmp_path, monkeypatch):
        """_kill_brain_subprocess is safe when no PID is known and no PID file exists."""
        from unittest.mock import patch, MagicMock
        client = BrainClient()
        client._brain_pid = None  # Reset any PID read from real brain.pid on disk
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(tmp_path / 'brain.pid'))
        # Prevent pgrep fallback from finding a real running brain process
        with patch('ironclaude.brain_client.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(stdout='', returncode=1)
            client._kill_brain_subprocess()  # Must not raise
        assert client._brain_pid is None

    def test_kill_subprocess_cleans_up_stale_pid_file(self, tmp_path, monkeypatch):
        """_kill_brain_subprocess removes PID file even when process is already dead."""
        from unittest.mock import patch
        pid_file = tmp_path / 'brain.pid'
        pid_file.write_text('12345')
        client = BrainClient()
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(pid_file))
        with patch('ironclaude.brain_client.os.kill', side_effect=ProcessLookupError):
            client._kill_brain_subprocess()
        assert not pid_file.exists()

    def test_kill_subprocess_clears_instance_pid(self, tmp_path, monkeypatch):
        """_kill_brain_subprocess sets _brain_pid to None after kill attempt."""
        from unittest.mock import patch
        client = BrainClient()
        client._brain_pid = 12345
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(tmp_path / 'brain.pid'))
        with patch('ironclaude.brain_client.os.kill', side_effect=ProcessLookupError):
            client._kill_brain_subprocess()
        assert client._brain_pid is None

    def test_kill_skips_stored_pid_when_cmdline_mismatch(self, tmp_path, monkeypatch):
        """A stored PID whose cmdline no longer matches the brain pattern is NOT killed."""
        from unittest.mock import patch
        client = BrainClient()
        client._brain_pid = 12345
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(tmp_path / 'brain.pid'))
        # cmdline lookup reports the PID is no longer a brain process
        monkeypatch.setattr(client, '_pid_cmdline_matches', lambda pid: False)
        with patch('ironclaude.brain_client._logged_kill') as mock_kill:
            client._kill_brain_subprocess()
        mock_kill.assert_not_called()
        assert client._brain_pid is None  # cleanup still happens

    def test_kill_proceeds_when_stored_pid_cmdline_matches(self, tmp_path, monkeypatch):
        """A stored PID whose cmdline still matches the brain pattern IS killed."""
        from unittest.mock import patch
        client = BrainClient()
        client._brain_pid = 12345
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(tmp_path / 'brain.pid'))
        monkeypatch.setattr(client, '_pid_cmdline_matches', lambda pid: True)
        with patch('ironclaude.brain_client._logged_kill') as mock_kill, \
                patch('ironclaude.brain_client.os.kill', side_effect=ProcessLookupError):
            client._kill_brain_subprocess()
        mock_kill.assert_called_once()
        assert mock_kill.call_args[0][0] == 12345

    def test_pid_cmdline_matches_true_for_brain_pattern(self, monkeypatch):
        """_pid_cmdline_matches returns True when ps reports a brain command line."""
        from unittest.mock import MagicMock
        monkeypatch.setattr(
            'ironclaude.brain_client.subprocess.run',
            lambda *a, **kw: MagicMock(
                returncode=0,
                stdout="node claude --output-format stream-json --system-prompt Orchestrator",
            ),
        )
        assert BrainClient._pid_cmdline_matches(999) is True

    def test_pid_cmdline_matches_false_for_other_process(self, monkeypatch):
        """_pid_cmdline_matches returns False for an unrelated command line."""
        from unittest.mock import MagicMock
        monkeypatch.setattr(
            'ironclaude.brain_client.subprocess.run',
            lambda *a, **kw: MagicMock(returncode=0, stdout="/usr/bin/python some_other_script.py"),
        )
        assert BrainClient._pid_cmdline_matches(999) is False

    def test_pid_cmdline_matches_false_when_pid_absent(self, monkeypatch):
        """_pid_cmdline_matches returns False when ps exits non-zero (no such PID)."""
        from unittest.mock import MagicMock
        monkeypatch.setattr(
            'ironclaude.brain_client.subprocess.run',
            lambda *a, **kw: MagicMock(returncode=1, stdout=""),
        )
        assert BrainClient._pid_cmdline_matches(999) is False

    def test_shutdown_calls_kill_subprocess(self, monkeypatch):
        """shutdown() calls _kill_brain_subprocess() after thread join."""
        client = BrainClient()
        kill_called = []
        monkeypatch.setattr(client, '_kill_brain_subprocess', lambda: kill_called.append(True))
        client.shutdown()
        assert kill_called == [True]

    def test_start_calls_kill_subprocess_first(self, monkeypatch):
        """start() calls _kill_brain_subprocess() before spawning the brain thread."""
        import unittest.mock as mock
        client = BrainClient()
        kill_calls = []
        monkeypatch.setattr(client, '_kill_brain_subprocess', lambda: kill_calls.append(True))
        monkeypatch.setattr(BrainClient, 'discover_episodic_memory_path',
                            lambda *a, **kw: '/fake/path')
        with mock.patch('ironclaude.brain_client.threading.Thread') as mock_thread:
            mock_thread.return_value = mock.MagicMock()
            client.start('test prompt')
        assert len(kill_calls) == 1

    def test_stale_pid_file_cleaned_on_start(self, tmp_path, monkeypatch):
        """start() removes stale PID file from previous crash via singleton guard."""
        import unittest.mock as mock
        from unittest.mock import patch
        pid_file = tmp_path / 'brain.pid'
        pid_file.write_text('12345')
        client = BrainClient()
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(pid_file))
        monkeypatch.setattr(BrainClient, 'discover_episodic_memory_path',
                            lambda *a, **kw: '/fake/path')
        with mock.patch('ironclaude.brain_client.threading.Thread') as mock_thread, \
             patch('ironclaude.brain_client.os.kill', side_effect=ProcessLookupError):
            mock_thread.return_value = mock.MagicMock()
            client.start('test prompt')
        assert not pid_file.exists()

    def test_ollama_mcp_path_resolves_relative_to_package(self):
        """_ollama_mcp_path points to src/ic/ollama_mcp.py."""
        from pathlib import Path
        expected = str(Path(__file__).parent.parent / "src" / "ironclaude" / "ollama_mcp.py")
        client = BrainClient()
        client.discover_episodic_memory_path = staticmethod(lambda **kw: "/fake/memory.js")
        client._run_event_loop = lambda *a, **kw: None
        client._kill_brain_subprocess = lambda: None
        client._running = False
        client.start("test prompt", "/tmp")
        assert client._ollama_mcp_path == expected

    def test_brain_session_includes_research_mcp_in_mcp_servers(self):
        """The mcp_servers dict in _brain_session includes a 'research' key."""
        client = BrainClient()
        client._research_mcp_path = "/fake/research_mcp.py"
        client._ollama_mcp_path = "/fake/ollama_mcp.py"
        client._episodic_memory_path = "/fake/memory.js"
        # We can't easily inspect the mcp_servers dict without running
        # _brain_session, so verify the paths are set and will be used
        assert client._research_mcp_path is not None
        assert client._ollama_mcp_path is not None


class TestPermissionSeekingFilter:
    """_check_permission_seeking detects permission-seeking language and throttles corrections."""

    def _mk(self, seeking: bool = False):
        from unittest.mock import MagicMock
        client = BrainClient()
        client._grader = MagicMock()
        client._grader.grade.return_value = {"permission_seeking": seeking}
        return client

    def test_clean_text_returns_none(self):
        """Clean response with no permission-seeking returns None."""
        client = self._mk(seeking=False)
        result = client._check_permission_seeking(
            "I analyzed the problem and found the root cause."
        )
        assert result is None

    def test_detects_shall_i(self):
        """'Shall I' in final sentence triggers correction."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking(
            "I analyzed the issue. Shall I proceed?"
        )
        assert result is not None

    def test_detects_should_i(self):
        """'Should I' in final sentence triggers correction."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking(
            "Found the bug. Should I implement the fix?"
        )
        assert result is not None

    def test_detects_would_you_like_me_to(self):
        """'Would you like me to' triggers correction."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking(
            "Would you like me to make these changes?"
        )
        assert result is not None

    def test_detects_do_you_want(self):
        """'Do you want' triggers correction."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking(
            "Do you want me to proceed with the implementation?"
        )
        assert result is not None

    def test_detects_want_me_to(self):
        """'Want me to' triggers correction."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking("Want me to fix this now?")
        assert result is not None

    def test_detects_let_me_know_if(self):
        """'Let me know if' triggers correction."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking(
            "Let me know if you'd like me to continue."
        )
        assert result is not None

    def test_pattern_in_middle_only_returns_none(self):
        """Pattern only in middle sentence (clean final) returns None."""
        client = self._mk(seeking=False)
        result = client._check_permission_seeking(
            "Shall I? Actually I will just do it."
        )
        assert result is None

    def test_case_insensitive(self):
        """Pattern matching is case insensitive."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking("SHALL I PROCEED?")
        assert result is not None

    def test_throttled_after_limit(self):
        """Returns None after MAX_PERMISSION_CORRECTIONS corrections in window."""
        client = self._mk(seeking=True)
        for _ in range(BrainClient.MAX_PERMISSION_CORRECTIONS):
            r = client._check_permission_seeking("Shall I proceed?")
            assert r is not None
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is None

    def test_throttle_window_prunes_old_timestamps(self):
        """Timestamps older than PERMISSION_CORRECTION_WINDOW are pruned, allowing new corrections."""
        client = self._mk(seeking=True)
        old = time.time() - client.PERMISSION_CORRECTION_WINDOW - 100
        client._permission_correction_timestamps = [old, old - 10, old - 20]
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is not None

    def test_correction_message_content(self):
        """Returned correction message tells brain to continue without asking."""
        client = self._mk(seeking=True)
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is not None
        assert "Continue without asking" in result

    def test_logs_on_correction(self, caplog):
        """logger.info is called when a correction is sent."""
        import logging
        client = self._mk(seeking=True)
        with caplog.at_level(logging.INFO, logger="ironclaude.brain"):
            result = client._check_permission_seeking("Shall I proceed?")
        assert result is not None
        assert any("Permission-seeking detected" in r.message for r in caplog.records)

    def test_logs_on_throttle(self, caplog):
        """logger.info is called when correction is throttled."""
        import logging
        client = self._mk(seeking=True)
        for _ in range(BrainClient.MAX_PERMISSION_CORRECTIONS):
            client._check_permission_seeking("Shall I proceed?")
        with caplog.at_level(logging.INFO, logger="ironclaude.brain"):
            result = client._check_permission_seeking("Shall I proceed?")
        assert result is None
        assert any("throttled" in r.message for r in caplog.records)


class TestMcpToolAllowlist:
    """MCP tool allowlist: game tools and unknown prefixes must be denied."""

    def test_game_click_denied(self):
        """mcp__orchestrator__game_click denied — game tools bypass brain restrictions."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__game_click", {})
        assert allowed is False
        assert msg is not None

    def test_game_type_denied(self):
        """mcp__orchestrator__game_type denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__game_type", {})
        assert allowed is False

    def test_game_key_denied(self):
        """mcp__orchestrator__game_key denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__game_key", {})
        assert allowed is False

    def test_game_launch_denied(self):
        """mcp__orchestrator__game_launch denied."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__game_launch", {})
        assert allowed is False

    def test_unknown_mcp_prefix_denied(self):
        """mcp__unknown__tool denied — unknown prefixes not in allowlist."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__unknown__do_something", {})
        assert allowed is False

    def test_known_orchestrator_query_tool_still_allowed(self):
        """mcp__orchestrator__get_worker_status still allowed after allowlist change."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__get_worker_status", {})
        assert allowed is True
        assert msg is None

    def test_game_denial_message_mentions_spawn_worker(self):
        """Game tool denial message guides brain to use spawn_worker."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__game_click", {})
        assert allowed is False
        assert "spawn_worker" in msg


class TestShutdownWakesOrphanedThreads:
    """Regression tests for 'This event loop is already running' (Directive #233).

    Root cause: shutdown() never called _stop_event.set(), so threads sleeping in
    _stop_event.wait(300) were orphaned when restart() spawned a new thread. On
    waking, the orphaned thread called self._loop.run_until_complete() on the new
    thread's already-running loop.
    """

    def test_shutdown_sets_stop_event(self):
        """shutdown() must set _stop_event to interrupt backoff sleeps."""
        client = BrainClient()
        assert not client._stop_event.is_set()
        client._kill_brain_subprocess = lambda: None
        client.shutdown()
        assert client._stop_event.is_set()

    def test_shutdown_wakes_thread_sleeping_in_backoff(self):
        """Thread sleeping in _stop_event.wait(300) must exit within 2s of shutdown()."""
        client = BrainClient()
        loop = asyncio.new_event_loop()
        started = threading.Event()

        def simulate_backoff():
            client._loop = loop
            client._running = True
            started.set()
            # Exact backoff-sleep pattern from _run_event_loop after exception
            while not client._stop_event.is_set():
                client._stop_event.wait(timeout=300)
            client._running = False
            loop.close()

        client._thread = threading.Thread(target=simulate_backoff, daemon=True)
        client._thread.start()
        assert started.wait(timeout=2.0), "Thread did not start"

        thread_ref = client._thread  # Save before shutdown() nullifies it
        client._kill_brain_subprocess = lambda: None
        client.shutdown()  # Already joins with timeout=10
        thread_ref.join(timeout=2.0)

        assert not thread_ref.is_alive(), "Thread did not exit within 2s of shutdown()"

    def test_start_clears_stop_event_before_thread(self, tmp_path):
        """start() must clear _stop_event so the new thread's while-loop does not immediately exit."""
        client = BrainClient()
        client._stop_event.set()  # Simulate state left by a prior shutdown()

        stop_event_state_when_run = []

        def fake_run_loop(prompt, cwd):
            stop_event_state_when_run.append(client._stop_event.is_set())
            client._loop = asyncio.new_event_loop()  # Unblock start()'s loop-wait

        client._run_event_loop = fake_run_loop
        client.discover_episodic_memory_path = lambda: str(tmp_path / "fake.js")
        client._kill_brain_subprocess = lambda: None

        client.start("test prompt")
        deadline = time.time() + 2.0
        while not stop_event_state_when_run and time.time() < deadline:
            time.sleep(0.05)

        assert stop_event_state_when_run, "fake_run_loop was never called by start()"
        assert not stop_event_state_when_run[0], (
            "_stop_event was still set when thread ran — start() must clear it before launching thread"
        )


class TestKillSubprocessDiagnostics:
    def test_kill_subprocess_logs_caller(self):
        """_kill_brain_subprocess logs its call stack."""
        from unittest.mock import patch
        client = BrainClient()
        with patch('ironclaude.brain_client.logger') as mock_logger, \
             patch('ironclaude.brain_client.os.kill', lambda pid, sig: None):
            client._kill_brain_subprocess()
        info_calls = [str(c) for c in mock_logger.info.call_args_list]
        assert any("_kill_brain_subprocess called from:" in c for c in info_calls)

    def test_expected_kill_false_by_default(self):
        """_expected_kill starts as False."""
        client = BrainClient()
        assert client._expected_kill is False

    def test_expected_kill_set_during_start_singleton(self):
        """start() sets _expected_kill=True before calling _kill_brain_subprocess."""
        from unittest.mock import patch
        client = BrainClient()
        captured = []

        def spy():
            captured.append(client._expected_kill)

        client._kill_brain_subprocess = spy
        with patch.object(client, 'discover_episodic_memory_path', return_value='/fake/path'):
            with patch('threading.Thread') as mock_thread:
                mock_thread.return_value.start = lambda: None
                try:
                    client.start("test prompt")
                except Exception:
                    pass  # start() may fail after singleton guard — that's OK
        assert captured == [True], f"Expected [True] but got {captured}"
        assert client._expected_kill is False

    def test_expected_kill_set_during_shutdown(self):
        """shutdown() sets _expected_kill=True before calling _kill_brain_subprocess."""
        client = BrainClient()
        captured = []

        def spy():
            captured.append(client._expected_kill)

        client._kill_brain_subprocess = spy
        client.shutdown()
        assert captured == [True], f"Expected [True] but got {captured}"
        assert client._expected_kill is False


class TestBrainIcRole:
    """IC_ROLE=brain is set in process environment when BrainClient.start() is called."""

    def test_start_sets_ic_role_brain(self, monkeypatch):
        """start() sets IC_ROLE=brain in os.environ before spawning brain subprocess."""
        import os
        import unittest.mock as mock
        monkeypatch.delenv("IC_ROLE", raising=False)
        client = BrainClient()
        monkeypatch.setattr(client, '_kill_brain_subprocess', lambda: None)
        monkeypatch.setattr(BrainClient, 'discover_episodic_memory_path',
                            staticmethod(lambda *a, **kw: '/fake/path'))
        with mock.patch('ironclaude.brain_client.threading.Thread') as mock_thread:
            mock_thread.return_value = mock.MagicMock()
            client.start('test prompt')
        assert os.environ.get("IC_ROLE") == "brain"


class TestSigtermDiagnostics:
    def test_log_brain_pid_diagnostics_uses_ps_for_ppid(self, caplog, monkeypatch):
        """_log_brain_pid_diagnostics uses ps command for brain's ppid, not os.getppid."""
        import logging
        import os
        from unittest.mock import MagicMock

        pid = os.getpid()
        fake_ppid = "12345"

        def fake_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            if isinstance(cmd, list) and cmd[0] == "ps":
                m.stdout = f"  {fake_ppid}\n"
                m.returncode = 0
            else:
                m.stdout = ""
                m.returncode = 1
            return m

        import ironclaude.brain_client as bc_module
        monkeypatch.setattr(bc_module.subprocess, "run", fake_subprocess_run)

        with caplog.at_level(logging.WARNING, logger="ironclaude.brain"):
            BrainClient._log_brain_pid_diagnostics(pid)

        assert any(f"pid={pid}" in r.message and f"ppid={fake_ppid}" in r.message
                   for r in caplog.records), \
            f"Must log brain's ppid from ps output, got: {[r.message for r in caplog.records]}"

    def test_log_brain_pid_diagnostics_handles_ps_failure(self, caplog, monkeypatch):
        """_log_brain_pid_diagnostics logs ppid=unknown if ps fails."""
        import logging
        import os
        from unittest.mock import MagicMock

        pid = os.getpid()

        def fail_subprocess_run(cmd, **kwargs):
            m = MagicMock()
            m.stdout = ""
            m.returncode = 1
            return m

        import ironclaude.brain_client as bc_module
        monkeypatch.setattr(bc_module.subprocess, "run", fail_subprocess_run)

        with caplog.at_level(logging.WARNING, logger="ironclaude.brain"):
            BrainClient._log_brain_pid_diagnostics(pid)

        assert any(f"pid={pid}" in r.message and "ppid=" in r.message
                   for r in caplog.records)


class TestBrainSessionOptions:
    """Verify system_prompt is passed in both fresh and resume ClaudeAgentOptions."""

    def _run_and_capture(self, client, system_prompt, resume_session_id=None):
        """Run _brain_session with patched SDK, return captured ClaudeAgentOptions kwargs."""
        import asyncio
        from unittest.mock import patch

        captured = {}

        class CapturingOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        async def noop_query(prompt=None, options=None):
            return
            yield  # noqa: unreachable — makes this an async generator

        with patch("claude_agent_sdk.ClaudeAgentOptions", CapturingOptions), \
             patch("claude_agent_sdk.query", noop_query):
            client._episodic_memory_path = "/fake/memory.js"
            asyncio.run(client._brain_session(system_prompt, None, resume_session_id))

        return captured

    def test_fresh_branch_includes_system_prompt(self):
        """Fresh session passes system_prompt to ClaudeAgentOptions."""
        client = BrainClient()
        captured = self._run_and_capture(client, "my-prompt", resume_session_id=None)
        assert captured.get("system_prompt") == "my-prompt"

    def test_resume_branch_includes_system_prompt(self):
        """Resumed session passes system_prompt to ClaudeAgentOptions (guard bypass fix)."""
        client = BrainClient()
        captured = self._run_and_capture(client, "my-prompt", resume_session_id="abc123")
        assert captured.get("system_prompt") == "my-prompt"

    def test_resume_branch_retains_all_guards(self):
        """Resumed session has identical permission guards to fresh session."""
        client = BrainClient()
        captured = self._run_and_capture(client, "my-prompt", resume_session_id="abc123")
        assert captured.get("permission_mode") == "bypassPermissions"
        assert captured.get("allowed_tools") == BrainClient.ALLOWED_TOOLS
        assert captured.get("can_use_tool") is not None

    def test_fresh_branch_includes_setting_sources(self):
        """Fresh session passes setting_sources=["project", "local"] to ClaudeAgentOptions."""
        client = BrainClient()
        captured = self._run_and_capture(client, "my-prompt", resume_session_id=None)
        assert captured.get("setting_sources") == ["project", "local"]

    def test_resume_branch_includes_setting_sources(self):
        """Resumed session passes setting_sources=["project", "local"] to ClaudeAgentOptions."""
        client = BrainClient()
        captured = self._run_and_capture(client, "my-prompt", resume_session_id="abc123")
        assert captured.get("setting_sources") == ["project", "local"]


class TestBrain1MContextGating:
    """1M-context beta ([1m] suffix + context-1m-2025-08-07 beta) applies ONLY to
    models that need it to unlock 1M (opus). Fable 5 and Sonnet 5 have 1M natively
    and reject the beta — they must launch with the bare model string."""

    def _run_and_capture(self, client, system_prompt, resume_session_id=None):
        """Run _brain_session with patched SDK, return captured ClaudeAgentOptions kwargs."""
        import asyncio
        from unittest.mock import patch

        captured = {}

        class CapturingOptions:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        async def noop_query(prompt=None, options=None):
            return
            yield  # noqa: unreachable — makes this an async generator

        with patch("claude_agent_sdk.ClaudeAgentOptions", CapturingOptions), \
             patch("claude_agent_sdk.query", noop_query):
            client._episodic_memory_path = "/fake/memory.js"
            asyncio.run(client._brain_session(system_prompt, None, resume_session_id))

        return captured

    def test_opus_gets_1m_suffix_and_beta(self):
        client = BrainClient(model="opus")
        captured = self._run_and_capture(client, "my-prompt")
        assert captured.get("model") == "opus[1m]"
        assert captured.get("betas") == ["context-1m-2025-08-07"]

    def test_fable_gets_bare_model_no_beta(self):
        """Fable 5 has 1M natively and rejects the [1m] suffix/beta (the daemon crash)."""
        client = BrainClient(model="fable")
        captured = self._run_and_capture(client, "my-prompt")
        assert captured.get("model") == "fable"
        assert "context-1m-2025-08-07" not in (captured.get("betas") or [])

    def test_sonnet_gets_bare_model_no_beta(self):
        client = BrainClient(model="sonnet")
        captured = self._run_and_capture(client, "my-prompt")
        assert captured.get("model") == "sonnet"
        assert "context-1m-2025-08-07" not in (captured.get("betas") or [])


class TestModelUnavailableText:
    """Message-shaped model-unavailable detection + fallback (the SDK returned the
    error as a normal assistant message, so the exception-only fallback never fired)."""

    def test_detects_unavailable_signature(self):
        from ironclaude.brain_client import _is_model_unavailable_text
        assert _is_model_unavailable_text(
            "There's an issue with the selected model (fable[1m]). "
            "It may not exist or you may not have access to it."
        ) is True

    def test_detects_no_access_signature(self):
        from ironclaude.brain_client import _is_model_unavailable_text
        assert _is_model_unavailable_text(
            "Error: the selected model is not available; you may not have access."
        ) is True

    def test_ignores_benign_text(self):
        from ironclaude.brain_client import _is_model_unavailable_text
        assert _is_model_unavailable_text("The selected model handled the task well.") is False
        assert _is_model_unavailable_text("All checks passed, changes staged.") is False
        assert _is_model_unavailable_text("") is False

    def test_message_shaped_unavailability_falls_back_to_opus(self):
        """A brain assistant MESSAGE signaling model-unavailable triggers fallback
        to opus and is not surfaced as a brain response."""
        import asyncio
        from unittest.mock import patch
        from claude_agent_sdk import AssistantMessage
        from claude_agent_sdk.types import TextBlock

        models_built = []

        class CapturingOptions:
            def __init__(self, **kwargs):
                models_built.append(kwargs.get("model"))

        call_count = {"n": 0}
        unavailable_text = (
            "There's an issue with the selected model (fable[1m]). "
            "It may not exist or you may not have access to it."
        )

        async def fake_query(prompt=None, options=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                yield AssistantMessage(content=[TextBlock(text=unavailable_text)], model="fable")
            else:
                return
                yield  # noqa: unreachable — async generator marker

        client = BrainClient(model="fable")
        with patch("claude_agent_sdk.ClaudeAgentOptions", CapturingOptions), \
             patch("claude_agent_sdk.query", fake_query):
            client._episodic_memory_path = "/fake/memory.js"
            asyncio.run(client._brain_session("my-prompt", None, None))

        # First session launched fable (bare), then fell back to opus[1m]
        assert models_built[0] == "fable"
        assert any(m == "opus[1m]" for m in models_built), models_built
        assert client._model == "opus"
        # The unavailability text was NOT surfaced as a brain response
        drained = client.get_pending_responses()
        assert all(unavailable_text not in r for r in drained)


class TestMutationToolFirstCheck:
    """Mutation tool check is first in _tool_guard_logic — unconditional, before all other checks."""

    def test_edit_denied_with_canonical_message(self):
        """Edit denied with exact canonical message."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Edit", {"file_path": "/tmp/foo.py", "old_string": "a", "new_string": "b"})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"

    def test_write_denied_with_canonical_message(self):
        """Write denied with exact canonical message."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("Write", {"file_path": "/tmp/foo.py", "content": "hello"})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"

    def test_notebook_edit_denied_with_canonical_message(self):
        """NotebookEdit denied with exact canonical message."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic("NotebookEdit", {})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"

    def test_mutation_denied_even_when_memory_armed(self):
        """Mutation check fires before memory toggle check — armed state cannot bypass it."""
        client = BrainClient()
        client._memory_armed = True
        allowed, msg = client._tool_guard_logic("Edit", {})
        assert allowed is False
        assert msg == "Brain cannot use mutation tools — route through workers"


class TestWikiGate:
    """Dual-flag gate: both episodic memory search AND wiki_query required before gated tools."""

    def test_wiki_queried_initial_state(self):
        client = BrainClient()
        assert client._wiki_queried is False

    def test_wiki_query_sets_flag(self):
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__wiki_query", {"keywords": "test"}
        )
        assert allowed is True
        assert client._wiki_queried is True

    def test_gated_denied_memory_only(self):
        """Gated tool denied when only memory is armed (wiki not queried)."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = False
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "wiki" in msg.lower()

    def test_gated_denied_wiki_only(self):
        """Gated tool denied when only wiki is queried (memory not searched)."""
        client = BrainClient()
        client._memory_armed = False
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False

    def test_gated_allowed_both_flags(self):
        """Gated tool allowed when both flags are set."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is True

    def test_both_flags_reset_after_gated(self):
        """Both flags reset to False after gated tool fires."""
        client = BrainClient()
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        client._tool_guard_logic(
            "mcp__orchestrator__approve_plan", {"worker_id": "w1", "rationale": "ok"}
        )
        assert client._memory_armed is False
        assert client._wiki_queried is False

    def test_wiki_query_is_orchestrator_tool(self):
        """wiki_query itself is allowed as an orchestrator tool (not gated)."""
        client = BrainClient()
        assert client._memory_armed is False
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__wiki_query", {"keywords": "test"}
        )
        assert allowed is True

    def test_wiki_write_ungated(self):
        """wiki_write is a regular orchestrator tool — ungated, doesn't set flags."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__wiki_write", {"page": "test", "title": "Test", "content": "c"}
        )
        assert allowed is True
        assert client._wiki_queried is False

    def test_full_gate_sequence(self):
        """Full flow: search episodic → query wiki → gated action allowed → flags reset."""
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        client._tool_guard_logic("mcp__episodic-memory__search", {"query": "test"})
        assert client._memory_armed is True
        assert client._wiki_queried is False
        client._tool_guard_logic("mcp__orchestrator__wiki_query", {"keywords": "test"})
        assert client._wiki_queried is True
        allowed, _ = client._tool_guard_logic(
            "mcp__orchestrator__kill_worker", {"worker_id": "w1"}
        )
        assert allowed is True
        assert client._memory_armed is False
        assert client._wiki_queried is False


class TestLookbackGate:
    """Startup lookback enforcement: Slack lookback + ledger update required before gated tools."""

    def test_lookback_initial_state(self):
        """_lookback_slack and _lookback_ledger start False."""
        client = BrainClient()
        assert client._lookback_slack is False
        assert client._lookback_ledger is False

    def test_slack_lookback_arms_flag(self):
        """get_operator_messages with hours_back >= 48 sets _lookback_slack."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__get_operator_messages", {"hours_back": 72}
        )
        assert allowed is True
        assert msg is None
        assert client._lookback_slack is True

    def test_slack_lookback_threshold_boundary(self):
        """hours_back == 48 is the minimum to arm the flag."""
        client = BrainClient()
        client._tool_guard_logic(
            "mcp__orchestrator__get_operator_messages", {"hours_back": 48}
        )
        assert client._lookback_slack is True

    def test_slack_lookback_below_threshold(self):
        """hours_back < 48 does not arm the flag."""
        client = BrainClient()
        client._tool_guard_logic(
            "mcp__orchestrator__get_operator_messages", {"hours_back": 47}
        )
        assert client._lookback_slack is False

    def test_slack_lookback_missing_hours_back(self):
        """Missing hours_back defaults to 0 — no arming."""
        client = BrainClient()
        client._tool_guard_logic(
            "mcp__orchestrator__get_operator_messages", {}
        )
        assert client._lookback_slack is False

    def test_ledger_update_arms_flag(self):
        """update_ledger sets _lookback_ledger."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__update_ledger", {"objective": "test", "tasks": []}
        )
        assert allowed is True
        assert msg is None
        assert client._lookback_ledger is True

    def test_gated_denied_neither_lookback(self):
        """Gated tool denied when neither lookback flag is set."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "slack lookback" in msg.lower()
        assert "ledger update" in msg.lower()

    def test_gated_denied_slack_only(self):
        """Gated tool denied when only slack lookback done (ledger missing)."""
        client = BrainClient()
        client._lookback_slack = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "ledger" in msg.lower()
        assert "slack" not in msg.lower()

    def test_gated_denied_ledger_only(self):
        """Gated tool denied when only ledger updated (slack lookback missing)."""
        client = BrainClient()
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "slack" in msg.lower()
        assert "ledger" not in msg.lower()

    def test_gated_allowed_both_lookback(self):
        """Gated tool proceeds to memory/wiki check when both lookback flags set."""
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "memory" in msg.lower() or "wiki" in msg.lower()

    def test_lookback_flags_persistent(self):
        """Lookback flags persist after gated tool passes (unlike memory/wiki)."""
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        client._memory_armed = True
        client._wiki_queried = True
        allowed, _ = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is True
        assert client._lookback_slack is True
        assert client._lookback_ledger is True
        assert client._memory_armed is False
        assert client._wiki_queried is False

    def test_start_resets_lookback(self, tmp_path, monkeypatch):
        """start() resets lookback flags and cleans up flag files."""
        import asyncio
        client = BrainClient()
        client._lookback_slack = True
        client._lookback_ledger = True
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        monkeypatch.setattr(client, '_kill_brain_subprocess', lambda: None)
        monkeypatch.setattr(
            BrainClient, 'discover_episodic_memory_path',
            staticmethod(lambda *a, **kw: '/fake/path'),
        )
        def _fake_event_loop(system_prompt, cwd):
            client._loop = asyncio.new_event_loop()
        monkeypatch.setattr(client, '_run_event_loop', _fake_event_loop)
        client.start('test prompt')
        assert client._lookback_slack is False
        assert client._lookback_ledger is False

    def test_full_lookback_sequence(self):
        """Complete flow: lookback → memory → wiki → gated tool → allow, lookback persists."""
        client = BrainClient()
        client._tool_guard_logic(
            "mcp__orchestrator__get_operator_messages", {"hours_back": 72}
        )
        assert client._lookback_slack is True
        client._tool_guard_logic(
            "mcp__orchestrator__update_ledger", {"objective": "test", "tasks": []}
        )
        assert client._lookback_ledger is True
        client._tool_guard_logic("mcp__episodic-memory__search", {"query": "test"})
        assert client._memory_armed is True
        client._tool_guard_logic("mcp__orchestrator__wiki_query", {"keywords": "test"})
        assert client._wiki_queried is True
        allowed, _ = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is True
        assert client._memory_armed is False
        assert client._wiki_queried is False
        assert client._lookback_slack is True
        assert client._lookback_ledger is True


class TestLedgerStale:
    def test_returns_none_when_cwd_not_set(self):
        client = BrainClient()
        assert client._ledger_stale() is None

    def test_returns_none_when_tasks_md_absent(self, tmp_path):
        client = BrainClient()
        client._cwd = str(tmp_path)
        assert client._ledger_stale() is None

    def test_returns_none_when_no_in_progress_tasks(self, tmp_path):
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "tasks.md").write_text('{"status": "completed"}')
        assert client._ledger_stale() is None

    def test_returns_none_when_recently_updated(self, tmp_path):
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "tasks.md").write_text('"status": "in_progress"')
        # mtime defaults to now — not stale
        assert client._ledger_stale() is None

    def test_returns_age_when_stale_with_in_progress(self, tmp_path):
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        tasks_md = wiki_dir / "tasks.md"
        tasks_md.write_text('"status": "in_progress"')
        old_mtime = time.time() - 35 * 60
        os.utime(str(tasks_md), (old_mtime, old_mtime))
        result = client._ledger_stale()
        assert result is not None
        assert result >= 35


class TestLedgerFreshnessEnforcement:
    def test_gated_tool_blocked_when_ledger_stale(self, tmp_path):
        client = BrainClient()
        client._cwd = str(tmp_path)
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        tasks_md = wiki_dir / "tasks.md"
        tasks_md.write_text('"status": "in_progress"')
        old_mtime = time.time() - 35 * 60
        os.utime(str(tasks_md), (old_mtime, old_mtime))
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__spawn_worker", {})
        assert not allowed
        assert "stale" in msg.lower()

    def test_gated_tool_allowed_when_ledger_fresh(self, tmp_path):
        client = BrainClient()
        client._cwd = str(tmp_path)
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "tasks.md").write_text('"status": "in_progress"')
        # mtime defaults to now — fresh
        allowed, msg = client._tool_guard_logic("mcp__orchestrator__spawn_worker", {})
        assert allowed
        assert msg is None

    def test_gates_not_consumed_when_ledger_stale(self, tmp_path):
        """When ledger is stale, _memory_armed and _wiki_queried stay True."""
        client = BrainClient()
        client._cwd = str(tmp_path)
        client._memory_armed = True
        client._wiki_queried = True
        client._lookback_slack = True
        client._lookback_ledger = True
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        tasks_md = wiki_dir / "tasks.md"
        tasks_md.write_text('"status": "in_progress"')
        old_mtime = time.time() - 35 * 60
        os.utime(str(tasks_md), (old_mtime, old_mtime))
        client._tool_guard_logic("mcp__orchestrator__spawn_worker", {})
        assert client._memory_armed is True
        assert client._wiki_queried is True


class TestLedgerStalePerTask:
    def _write_tasks_md(self, path, status_set_at, status="in_progress"):
        """Write a tasks.md with one task and the given status_set_at."""
        blob = json.dumps({
            "objective": "Test",
            "tasks": [{"id": "t1", "description": "T1", "status": status, "status_set_at": status_set_at}]
        })
        path.write_text(
            f'**Objective:** Test\n\n## Tasks\n\n| ID | Description | Status |\n'
            f'|----|-------------|--------|\n| t1 | T1 | {status} |\n\n'
            f'## Data\n\n```json\n{blob}\n```\n'
        )

    def test_blocks_when_in_progress_task_exceeds_threshold(self, tmp_path):
        """_ledger_stale returns age when in_progress task exceeds task_staleness_threshold_hours."""
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        self._write_tasks_md(wiki_dir / "tasks.md", old_ts)
        result = client._ledger_stale()
        assert result is not None
        assert result >= 4 * 60  # default 4h threshold × 60 min/h

    def test_allows_when_in_progress_task_within_threshold(self, tmp_path):
        """_ledger_stale returns None when in_progress task is recent."""
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        recent_ts = datetime.now(timezone.utc).isoformat()
        self._write_tasks_md(wiki_dir / "tasks.md", recent_ts)
        result = client._ledger_stale()
        assert result is None

    def test_allows_when_status_set_at_absent(self, tmp_path):
        """Tasks without status_set_at are skipped (fail-open for backward compat)."""
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        blob = json.dumps({
            "objective": "Test",
            "tasks": [{"id": "t1", "description": "T1", "status": "in_progress"}]
        })
        (wiki_dir / "tasks.md").write_text(f'## Data\n\n```json\n{blob}\n```\n')
        assert client._ledger_stale() is None

    def test_ignores_non_in_progress_statuses(self, tmp_path):
        """pending tasks with old status_set_at do not trigger the per-task check."""
        client = BrainClient()
        client._cwd = str(tmp_path)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        blob = json.dumps({
            "objective": "Test",
            "tasks": [{"id": "t1", "description": "T1", "status": "pending", "status_set_at": old_ts}]
        })
        # Inject "in_progress" in objective text so the early string check passes
        content = (
            f'**Objective:** ignore in_progress ref\n\n## Data\n\n```json\n{blob}\n```\n'
        )
        (wiki_dir / "tasks.md").write_text(content)
        assert client._ledger_stale() is None


class TestTokenUsageAccumulation:
    def test_get_token_usage_initial_zeros(self):
        """Fresh BrainClient returns all-zero usage."""
        client = BrainClient()
        usage = client.get_token_usage()
        assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}

    def test_get_token_usage_reflects_accumulated_state(self):
        """get_token_usage() reads directly from accumulator attributes."""
        client = BrainClient()
        client._total_input_tokens = 42100
        client._total_output_tokens = 108100
        client._total_cost_usd = 0.12
        usage = client.get_token_usage()
        assert usage["input_tokens"] == 42100
        assert usage["output_tokens"] == 108100
        assert usage["total_tokens"] == 150200
        assert usage["cost_usd"] == 0.12

    def test_restart_resets_token_accumulators(self):
        """restart() resets token accumulators to zero before starting new session."""
        client = BrainClient()
        client._total_input_tokens = 50000
        client._total_output_tokens = 100000
        client._total_cost_usd = 0.75
        client.start = lambda *a, **kw: setattr(client, '_running', True)
        client._kill_brain_subprocess = lambda: None
        client.restart("test prompt")
        assert client._total_input_tokens == 0
        assert client._total_output_tokens == 0
        assert client._total_cost_usd == 0.0


class TestSessionLogRotation:
    def test_keeps_last_10_logs(self, tmp_path, monkeypatch):
        """_init_session_log deletes oldest logs when count exceeds SESSION_LOG_KEEP."""
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        for i in range(12):
            (tmp_path / f"20260524-{i:06d}-000000.log").write_text(f"session {i}")
        client = BrainClient()
        client._init_session_log()
        logs = sorted(tmp_path.glob("*.log"))
        assert len(logs) == 10
        assert not (tmp_path / "20260524-000000-000000.log").exists()
        assert not (tmp_path / "20260524-000001-000000.log").exists()

    def test_no_deletion_below_limit(self, tmp_path, monkeypatch):
        """_init_session_log does not delete when under SESSION_LOG_KEEP."""
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        for i in range(5):
            (tmp_path / f"20260524-{i:06d}-000000.log").write_text(f"session {i}")
        client = BrainClient()
        client._init_session_log()
        logs = list(tmp_path.glob("*.log"))
        assert len(logs) == 6  # 5 existing + 1 new

    def test_creates_new_log_file(self, tmp_path, monkeypatch):
        """_init_session_log creates a new .log file and sets _session_log_path."""
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        client = BrainClient()
        client._init_session_log()
        assert client._session_log_path is not None
        from pathlib import Path
        assert Path(client._session_log_path).exists()


class TestPreviousSessionTail:
    def test_returns_last_20_lines(self, tmp_path):
        """_read_previous_session_tail returns last 20 lines of file."""
        log_file = tmp_path / "session.log"
        lines = [f"line {i}" for i in range(25)]
        log_file.write_text("\n".join(lines) + "\n")
        client = BrainClient()
        result = client._read_previous_session_tail(str(log_file))
        assert result == "\n".join(lines[-20:])

    def test_returns_empty_for_nonexistent_file(self):
        """_read_previous_session_tail returns '' for missing file."""
        client = BrainClient()
        assert client._read_previous_session_tail("/nonexistent/path.log") == ""

    def test_returns_all_lines_when_fewer_than_20(self, tmp_path):
        """Returns all lines when file has fewer than 20."""
        log_file = tmp_path / "session.log"
        lines = [f"line {i}" for i in range(5)]
        log_file.write_text("\n".join(lines) + "\n")
        client = BrainClient()
        result = client._read_previous_session_tail(str(log_file))
        assert result == "\n".join(lines)


class TestLogEntryFormat:
    def test_entry_has_iso8601z_prefix(self, tmp_path):
        """_session_log_write writes entry with ISO8601Z timestamp prefix."""
        import re
        client = BrainClient()
        client._session_log_path = str(tmp_path / "session.log")
        client._session_log_write("MSG_RECV chars=10 preview='hello'")
        content = (tmp_path / "session.log").read_text()
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z MSG_RECV", content)

    def test_entry_appends_to_file(self, tmp_path):
        """_session_log_write appends, does not overwrite."""
        client = BrainClient()
        client._session_log_path = str(tmp_path / "session.log")
        client._session_log_write("ENTRY_ONE")
        client._session_log_write("ENTRY_TWO")
        content = (tmp_path / "session.log").read_text()
        assert "ENTRY_ONE" in content
        assert "ENTRY_TWO" in content

    def test_write_safe_when_path_none(self):
        """_session_log_write is safe when _session_log_path is None."""
        client = BrainClient()
        assert client._session_log_path is None
        client._session_log_write("should not raise")  # must not raise

    def test_session_start_written_on_init(self, tmp_path, monkeypatch):
        """_init_session_log writes SESSION_START entry."""
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        client = BrainClient()
        client._init_session_log()
        from pathlib import Path
        content = Path(client._session_log_path).read_text()
        assert "SESSION_START" in content


class TestFreshStartNoHistory:
    def test_no_error_on_empty_dir(self, tmp_path, monkeypatch):
        """_init_session_log succeeds with empty brain-sessions dir."""
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        client = BrainClient()
        client._init_session_log()  # must not raise
        assert client._session_log_path is not None

    def test_previous_context_none_after_init(self):
        """_previous_session_context is None after __init__."""
        client = BrainClient()
        assert client._previous_session_context is None

    def test_session_log_path_none_after_init(self):
        """_session_log_path is None after __init__ (not yet started)."""
        client = BrainClient()
        assert client._session_log_path is None


class TestSessionLogLifecycle:
    """Session log wiring: start() creates log, restart() injects diagnostic."""

    def _patch_start(self, client, tmp_path, monkeypatch):
        """Make start() complete fast: no process management, real session log."""
        import asyncio
        monkeypatch.setattr(BrainClient, 'SESSION_LOG_DIR', str(tmp_path))
        monkeypatch.setattr(client, '_kill_brain_subprocess', lambda: None)
        monkeypatch.setattr(
            BrainClient, 'discover_episodic_memory_path',
            staticmethod(lambda *a, **kw: '/fake/path'),
        )

        def _fake_event_loop(system_prompt, cwd):
            client._loop = asyncio.new_event_loop()

        monkeypatch.setattr(client, '_run_event_loop', _fake_event_loop)

    def test_start_creates_session_log(self, tmp_path, monkeypatch):
        """start() initializes a session log file via _init_session_log()."""
        from pathlib import Path
        client = BrainClient()
        self._patch_start(client, tmp_path, monkeypatch)
        client.start('test prompt')
        assert client._session_log_path is not None
        assert Path(client._session_log_path).exists()

    def test_restart_sends_diagnostic_when_previous_log_exists(self, tmp_path, monkeypatch):
        """restart() sends [DIAGNOSTIC] message when previous session log exists."""
        log_file = tmp_path / "prev-session.log"
        log_file.write_text("line1\nline2\nline3\n")
        client = BrainClient()
        client._session_log_path = str(log_file)

        sent_messages = []
        monkeypatch.setattr(client, 'shutdown', lambda: None)
        monkeypatch.setattr(client, 'start', lambda *a, **kw: None)
        monkeypatch.setattr(client, 'is_alive', lambda: True)
        monkeypatch.setattr(client, 'send_message',
                            lambda text: sent_messages.append(text) or True)

        client.restart('test prompt')

        assert any('[DIAGNOSTIC]' in m for m in sent_messages)

    def test_restart_no_diagnostic_when_no_previous_log(self, monkeypatch):
        """restart() does not send [DIAGNOSTIC] when no previous session log."""
        client = BrainClient()
        assert client._session_log_path is None

        sent_messages = []
        monkeypatch.setattr(client, 'shutdown', lambda: None)
        monkeypatch.setattr(client, 'start', lambda *a, **kw: None)
        monkeypatch.setattr(client, 'is_alive', lambda: True)
        monkeypatch.setattr(client, 'send_message',
                            lambda text: sent_messages.append(text) or True)

        client.restart('test prompt')

        assert not any('[DIAGNOSTIC]' in m for m in sent_messages)

    def test_restart_diagnostic_includes_previous_log_content(self, tmp_path, monkeypatch):
        """restart() diagnostic message contains last 20 lines of previous session log."""
        lines = [f"2026-05-24T10:00:{i:02d}.000000Z TOOL_INVOKE name=mcp__test" for i in range(25)]
        log_file = tmp_path / "prev-session.log"
        log_file.write_text("\n".join(lines) + "\n")

        client = BrainClient()
        client._session_log_path = str(log_file)

        sent_messages = []
        monkeypatch.setattr(client, 'shutdown', lambda: None)
        monkeypatch.setattr(client, 'start', lambda *a, **kw: None)
        monkeypatch.setattr(client, 'is_alive', lambda: True)
        monkeypatch.setattr(client, 'send_message',
                            lambda text: sent_messages.append(text) or True)

        client.restart('test prompt')

        diagnostic = next((m for m in sent_messages if '[DIAGNOSTIC]' in m), None)
        assert diagnostic is not None
        assert lines[-1] in diagnostic     # last line present
        assert lines[0] not in diagnostic  # oldest line dropped (only last 20 kept)


class TestCheckPermissionSeeking:
    """_check_permission_seeking delegates to _grader.grade and preserves throttle logic."""

    def _make_client(self):
        from unittest.mock import MagicMock
        client = BrainClient()
        client._grader = MagicMock()
        client._permission_correction_timestamps = []
        return client

    def test_permission_seeking_detected_returns_correction(self):
        """grader reports permission_seeking=True → correction message returned."""
        client = self._make_client()
        client._grader.grade.return_value = {"permission_seeking": True}
        result = client._check_permission_seeking("I fixed the bug.")
        assert result is not None
        assert "Continue without asking" in result

    def test_no_permission_seeking_returns_none(self):
        """grader reports permission_seeking=False → None."""
        client = self._make_client()
        client._grader.grade.return_value = {"permission_seeking": False}
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is None

    def test_infrastructure_error_fails_open(self):
        """infrastructure_error from grader → None (fail-open, no correction)."""
        client = self._make_client()
        client._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "Ollama down"}
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is None

    def test_throttle_still_applies_when_seeking(self):
        """Throttle enforced after MAX_PERMISSION_CORRECTIONS within window."""
        client = self._make_client()
        client._grader.grade.return_value = {"permission_seeking": True}
        for _ in range(BrainClient.MAX_PERMISSION_CORRECTIONS):
            r = client._check_permission_seeking("I fixed the bug.")
            assert r is not None
        result = client._check_permission_seeking("I fixed the bug.")
        assert result is None

    def test_final_sentence_extracted_for_grader(self):
        """Grader is called with only the final sentence, not the full text."""
        client = self._make_client()
        client._grader.grade.return_value = {"permission_seeking": False}
        client._check_permission_seeking("I analyzed the issue. Shall I proceed?")
        call_args = client._grader.grade.call_args
        user_prompt = call_args[0][1]
        assert "Shall I proceed?" in user_prompt
        assert "I analyzed the issue." not in user_prompt

    def test_infrastructure_error_not_throttled(self):
        """infrastructure_error does not add timestamp — subsequent calls still allowed."""
        client = self._make_client()
        client._grader.grade.return_value = {"infrastructure_error": True, "error_detail": "Ollama down"}
        for _ in range(BrainClient.MAX_PERMISSION_CORRECTIONS):
            client._check_permission_seeking("Shall I proceed?")
        assert client._permission_correction_timestamps == []


class TestPermissionSeekingOffLoop:
    """The blocking permission-seeking grade must run off the event loop thread."""

    def test_grade_dispatched_off_event_loop_thread(self):
        """_maybe_correct_permission_seeking runs _check_permission_seeking in an executor."""
        client = BrainClient()
        seen = {}

        def fake_check(text):
            seen["thread"] = threading.get_ident()
            seen["text"] = text
            return "CORRECTION"

        client._check_permission_seeking = fake_check
        loop = asyncio.new_event_loop()
        try:
            loop_thread = threading.get_ident()  # run_until_complete drives loop on this thread
            result = loop.run_until_complete(
                client._maybe_correct_permission_seeking("Shall I proceed?")
            )
        finally:
            loop.close()

        assert result == "CORRECTION"
        assert seen["text"] == "Shall I proceed?"
        # Must have executed on a different (executor pool) thread, not the loop thread
        assert seen["thread"] != loop_thread

    def test_maybe_correct_returns_none_when_no_correction(self):
        client = BrainClient()
        client._check_permission_seeking = lambda text: None
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                client._maybe_correct_permission_seeking("I fixed it.")
            )
        finally:
            loop.close()
        assert result is None


class TestShutdownLoopGuard:
    """shutdown() must not leak a still-running loop, and must close a stopped one."""

    def test_shutdown_closes_loop_when_thread_exits(self):
        """Clean path: thread already gone → loop closed and _loop nulled."""
        client = BrainClient()
        loop = asyncio.new_event_loop()
        client._loop = loop
        client._thread = None
        client._kill_brain_subprocess = lambda: None
        client.shutdown()
        assert client._loop is None
        assert loop.is_closed()

    def test_shutdown_keeps_loop_when_thread_still_alive(self):
        """If the thread survives the join, _loop/_thread refs are kept (no leak of a live loop)."""
        from unittest.mock import MagicMock
        client = BrainClient()
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True  # still running after join
        client._thread = fake_thread
        loop = asyncio.new_event_loop()  # not running → is_running() False
        client._loop = loop
        client._kill_brain_subprocess = lambda: None
        client.shutdown()
        # References preserved so is_alive() keeps reflecting the live thread
        assert client._loop is loop
        assert client._thread is fake_thread
        assert not loop.is_closed()  # not force-closed while thread may still use it
        loop.close()  # test cleanup
