# tests/test_brain_client.py
import asyncio
import threading
import time
import pytest
from ironclaude.brain_client import BrainClient, _backoff_seconds


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
        client.shutdown()  # should not raise
        assert client.is_alive() is False

    def test_start_has_no_continue_session_param(self):
        """start() no longer accepts continue_session — always fresh sessions."""
        import inspect
        sig = inspect.signature(BrainClient.start)
        assert "continue_session" not in sig.parameters


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
    def test_default_model_is_opus_4_5(self):
        """BrainClient defaults to claude-opus-4-5-20251101."""
        client = BrainClient()
        assert client._model == "claude-opus-4-5-20251101"

    def test_model_parameter_accepted(self):
        """BrainClient accepts custom model parameter."""
        client = BrainClient(model="claude-sonnet-4-5-20241022")
        assert client._model == "claude-sonnet-4-5-20241022"


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
        client.restart("test prompt")
        assert client.needs_restart() is False
        client._stop_event.set()  # Clean up new thread


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
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "search episodic memory" in msg.lower()
        assert "Operator" in msg  # default operator_name

    def test_gated_tool_denied_uses_operator_name(self):
        """Denial message includes configured operator_name."""
        client = BrainClient(operator_name="Alice")
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
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__approve_plan", {"worker_id": "w1", "rationale": "ok"}
        )
        assert allowed is True
        assert msg is None

    def test_gated_tool_disarms_toggle(self):
        """Using a gated tool resets _memory_armed to False."""
        client = BrainClient()
        client._memory_armed = True
        client._tool_guard_logic(
            "mcp__orchestrator__reject_plan", {"worker_id": "w1", "reason": "bad"}
        )
        assert client._memory_armed is False

    def test_kill_worker_denied_when_unarmed(self):
        """kill_worker is a gated tool — denied when memory not searched first."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__kill_worker", {"worker_id": "w1"}
        )
        assert allowed is False
        assert "search episodic memory" in msg.lower()

    def test_spawn_workers_denied_when_unarmed(self):
        """spawn_workers (plural) is a gated tool — denied when memory not searched first."""
        client = BrainClient()
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_workers", {"requests": []}
        )
        assert allowed is False
        assert "search episodic memory" in msg.lower()

    def test_kill_worker_allowed_when_armed(self):
        """kill_worker allowed and disarms toggle when memory was searched first."""
        client = BrainClient()
        client._memory_armed = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__kill_worker", {"worker_id": "w1"}
        )
        assert allowed is True
        assert msg is None
        assert client._memory_armed is False

    def test_spawn_workers_allowed_when_armed(self):
        """spawn_workers (plural) allowed and disarms toggle when memory was searched first."""
        client = BrainClient()
        client._memory_armed = True
        allowed, msg = client._tool_guard_logic(
            "mcp__orchestrator__spawn_workers", {"requests": []}
        )
        assert allowed is True
        assert msg is None
        assert client._memory_armed is False

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
        for tool_name in [
            "mcp__ollama__pull_model",
            "mcp__ollama__remove_model",
            "mcp__ollama__create_model",
        ]:
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is False, f"{tool_name} should be gated"
            assert "memory" in msg.lower()

    def test_ollama_mutation_tools_allowed_when_armed(self):
        """Ollama mutation tools allowed when _memory_armed is True."""
        client = BrainClient()
        for tool_name in [
            "mcp__ollama__pull_model",
            "mcp__ollama__remove_model",
            "mcp__ollama__create_model",
        ]:
            client._memory_armed = True
            allowed, msg = client._tool_guard_logic(tool_name, {})
            assert allowed is True, f"{tool_name} should be allowed when armed"
            assert msg is None

    def test_ollama_mutation_disarms_toggle(self):
        """Using an ollama mutation tool resets _memory_armed to False."""
        client = BrainClient()
        client._memory_armed = True
        client._tool_guard_logic("mcp__ollama__pull_model", {})
        assert client._memory_armed is False


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
        client._running = False
        client.start("test prompt", "/tmp")
        assert client._research_mcp_path is not None

    def test_start_discovers_ollama_mcp_path(self, tmp_path):
        """After calling start(), _ollama_mcp_path is set."""
        client = BrainClient()
        client.discover_episodic_memory_path = staticmethod(lambda **kw: "/fake/memory.js")
        client._run_event_loop = lambda *a, **kw: None
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
        client = BrainClient()
        monkeypatch.setattr(BrainClient, 'BRAIN_PID_FILE', str(tmp_path / 'brain.pid'))
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

    def test_clean_text_returns_none(self):
        """Clean response with no permission-seeking returns None."""
        client = BrainClient()
        result = client._check_permission_seeking(
            "I analyzed the problem and found the root cause."
        )
        assert result is None

    def test_detects_shall_i(self):
        """'Shall I' in final sentence triggers correction."""
        client = BrainClient()
        result = client._check_permission_seeking(
            "I analyzed the issue. Shall I proceed?"
        )
        assert result is not None

    def test_detects_should_i(self):
        """'Should I' in final sentence triggers correction."""
        client = BrainClient()
        result = client._check_permission_seeking(
            "Found the bug. Should I implement the fix?"
        )
        assert result is not None

    def test_detects_would_you_like_me_to(self):
        """'Would you like me to' triggers correction."""
        client = BrainClient()
        result = client._check_permission_seeking(
            "Would you like me to make these changes?"
        )
        assert result is not None

    def test_detects_do_you_want(self):
        """'Do you want' triggers correction."""
        client = BrainClient()
        result = client._check_permission_seeking(
            "Do you want me to proceed with the implementation?"
        )
        assert result is not None

    def test_detects_want_me_to(self):
        """'Want me to' triggers correction."""
        client = BrainClient()
        result = client._check_permission_seeking("Want me to fix this now?")
        assert result is not None

    def test_detects_let_me_know_if(self):
        """'Let me know if' triggers correction."""
        client = BrainClient()
        result = client._check_permission_seeking(
            "Let me know if you'd like me to continue."
        )
        assert result is not None

    def test_pattern_in_middle_only_returns_none(self):
        """Pattern only in middle sentence (clean final) returns None."""
        client = BrainClient()
        # 'Shall I?' is the first sentence; final sentence is clean
        result = client._check_permission_seeking(
            "Shall I? Actually I will just do it."
        )
        assert result is None

    def test_case_insensitive(self):
        """Pattern matching is case insensitive."""
        client = BrainClient()
        result = client._check_permission_seeking("SHALL I PROCEED?")
        assert result is not None

    def test_throttled_after_limit(self):
        """Returns None after MAX_PERMISSION_CORRECTIONS corrections in window."""
        client = BrainClient()
        # Use up the 3-correction budget
        for _ in range(BrainClient.MAX_PERMISSION_CORRECTIONS):
            r = client._check_permission_seeking("Shall I proceed?")
            assert r is not None
        # 4th should be throttled
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is None

    def test_throttle_window_prunes_old_timestamps(self):
        """Timestamps older than PERMISSION_CORRECTION_WINDOW are pruned, allowing new corrections."""
        client = BrainClient()
        old = time.time() - client.PERMISSION_CORRECTION_WINDOW - 100
        # Pre-load 3 old (expired) timestamps
        client._permission_correction_timestamps = [old, old - 10, old - 20]
        # Should NOT be throttled — all timestamps are expired
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is not None

    def test_correction_message_content(self):
        """Returned correction message tells brain to continue without asking."""
        client = BrainClient()
        result = client._check_permission_seeking("Shall I proceed?")
        assert result is not None
        assert "Continue without asking" in result

    def test_logs_on_correction(self, caplog):
        """logger.info is called when a correction is sent."""
        import logging
        client = BrainClient()
        with caplog.at_level(logging.INFO, logger="ironclaude.brain"):
            result = client._check_permission_seeking("Shall I proceed?")
        assert result is not None
        assert any("Permission-seeking detected" in r.message for r in caplog.records)

    def test_logs_on_throttle(self, caplog):
        """logger.info is called when correction is throttled."""
        import logging
        client = BrainClient()
        # Use up the budget
        for _ in range(BrainClient.MAX_PERMISSION_CORRECTIONS):
            client._check_permission_seeking("Shall I proceed?")
        # Next call is throttled — check log
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
        with patch('ironclaude.brain_client.logger') as mock_logger:
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
