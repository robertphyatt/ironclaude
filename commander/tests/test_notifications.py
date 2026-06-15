# tests/test_notifications.py
import pytest
from ironclaude.notifications import (
    _escape_mrkdwn,
    _fmt_tokens,
    format_worker_spawned,
    format_worker_completed,
    format_worker_failed,
    format_heartbeat,
    format_brain_restarted,
    format_brain_circuit_breaker,
    format_objective_received,
    format_task_progress,
    format_worker_checkin,
    format_worker_checkin_slack,
    format_worker_gate_stuck_slack,
)


class TestWorkerNotifications:
    def test_worker_spawned(self):
        msg = format_worker_spawned("worker-1", "claude-max", "/tmp/repo", "Process chapter 1")
        assert "worker-1" in msg
        assert "Claude Max" in msg
        assert "Process chapter 1" in msg

    def test_worker_completed(self):
        msg = format_worker_completed("worker-1", "Added 3 files, modified 2")
        assert "worker-1" in msg
        assert "Added 3 files" in msg

    def test_worker_failed(self):
        msg = format_worker_failed("worker-1", "Context limit exceeded", attempts=3)
        assert "worker-1" in msg
        assert "3" in msg


class TestSystemNotifications:
    def test_heartbeat_with_workers(self):
        prefix = "Professional mode is active. You must follow the workflow.\n\nYour task: "
        workers = [
            {"id": "worker-abc", "description": prefix + "Implement auth flow", "workflow_stage": "executing"},
            {"id": "worker-def", "description": prefix + "Fix CSS layout", "workflow_stage": "brainstorming"},
        ]
        msg = format_heartbeat(workers)
        assert "worker-abc" in msg
        assert "Implement auth flow" in msg
        assert "executing" in msg
        assert "worker-def" in msg
        assert "Fix CSS layout" in msg
        assert "brainstorming" in msg

    def test_heartbeat_no_workers(self):
        msg = format_heartbeat([])
        assert "No active workers" in msg

    def test_heartbeat_truncates_long_description(self):
        workers = [{"id": "w1", "description": "Your task: " + "A" * 80, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "A" * 60 in msg
        assert "..." in msg
        assert "A" * 80 not in msg

    def test_heartbeat_none_description(self):
        workers = [{"id": "w1", "description": None, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "no task" in msg

    def test_heartbeat_none_stage(self):
        workers = [{"id": "w1", "description": "Your task: Do stuff", "workflow_stage": None}]
        msg = format_heartbeat(workers)
        assert "unknown" in msg

    def test_brain_restarted_no_snapshot_claim(self):
        """format_brain_restarted must not claim snapshot recovery."""
        msg = format_brain_restarted(restart_count=2)
        assert "snapshot" not in msg.lower()

    def test_brain_restarted_honest_messaging(self):
        """format_brain_restarted must mention context loss."""
        msg = format_brain_restarted(restart_count=2)
        assert "context lost" in msg.lower() or "fresh session" in msg.lower()
        assert "2" in msg

    def test_circuit_breaker_notification(self):
        """format_brain_circuit_breaker includes restart count and limit."""
        msg = format_brain_circuit_breaker(restart_count=5, max_restarts=3, window_seconds=600)
        assert "5" in msg
        assert "3" in msg
        assert "circuit breaker" in msg.lower() or "paused" in msg.lower()


class TestHeartbeatTaskExtraction:
    def test_extracts_task_from_pm_preamble(self):
        preamble = (
            "Professional mode is active. You must follow the brainstorm → "
            "write-plans → execute-plans workflow before making any code changes. "
            "Start by invoking /brainstorming --scope=hold.\n\n"
            "Your task: Consolidate HAL audio daemon"
        )
        workers = [{"id": "w1", "description": preamble, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "Consolidate HAL audio daemon" in msg
        assert "Professional mode" not in msg

    def test_no_marker_uses_first_line(self):
        workers = [{"id": "w1", "description": "Do some work without proper format", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "Do some work" in msg
        assert "[malformed objective]" not in msg

    def test_extracts_only_first_line_after_marker(self):
        description = "Your task: First line of task\nMore details on second line"
        workers = [{"id": "w1", "description": description, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "First line of task" in msg
        assert "More details" not in msg

    def test_none_description_is_no_task_not_malformed(self):
        workers = [{"id": "w1", "description": None, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "no task" in msg
        assert "[malformed objective]" not in msg

    def test_empty_after_marker_shows_malformed(self):
        workers = [{"id": "w1", "description": "Your task: \n", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "[malformed objective]" in msg

    def test_no_marker_multiline_uses_first_line(self):
        workers = [{"id": "w1", "description": "First line task\nSecond line details", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "First line task" in msg
        assert "Second line" not in msg

    def test_empty_string_shows_malformed(self):
        workers = [{"id": "w1", "description": "", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "[malformed objective]" in msg

    def test_whitespace_only_shows_malformed(self):
        workers = [{"id": "w1", "description": "   \n  ", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "[malformed objective]" in msg

    def test_pm_preamble_extracts_task_marker(self):
        preamble = (
            "Professional mode is active. Start with /brainstorming --scope=hold.\n"
            "\n"
            "Task: Debug ONNX TTS AR decode loop"
        )
        workers = [{"id": "w1", "description": preamble, "workflow_stage": "brainstorming"}]
        msg = format_heartbeat(workers)
        assert "Professional mode" not in msg
        assert "Debug ONNX TTS AR decode loop" in msg

    def test_pm_preamble_no_marker_extracts_directive(self):
        description = (
            "Professional mode is active. Start with /brainstorming --scope=hold.\n\n"
            "d1142: Fix heartbeat worker summaries to show useful descriptions"
        )
        workers = [{"id": "w1", "description": description, "workflow_stage": "brainstorming"}]
        msg = format_heartbeat(workers)
        assert "d1142: Fix heartbeat worker summaries" in msg
        assert "Professional mode" not in msg


class TestBrainNotifications:
    def test_objective_received(self):
        msg = format_objective_received("Process the remaining D&D chapters")
        assert "D&amp;D" in msg

    def test_task_progress(self):
        msg = format_task_progress(current=2, total=5, description="Writing tests")
        assert "2/5" in msg
        assert "Writing tests" in msg


class TestWorkerCheckinNotification:
    def test_basic_checkin(self):
        msg = format_worker_checkin("w1", 15, "executing", "Running tests...", False)
        assert "w1" in msg
        assert "15min" in msg
        assert "executing" in msg
        assert "Running tests..." in msg
        assert "waiting for your input" not in msg

    def test_prompt_waiting_adds_warning(self):
        msg = format_worker_checkin("w1", 5, "brainstorming", "Which approach?", True)
        assert "Waiting for input" in msg

    def test_checkin_prefix(self):
        msg = format_worker_checkin("w1", 10, "design_ready", "Ready", False)
        assert "[CHECK-IN]" in msg

    def test_action_required_prefix_when_prompt_waiting(self):
        msg = format_worker_checkin("w1", 5, "brainstorming", "Which approach?", True)
        assert "[ACTION REQUIRED]" in msg
        assert "[CHECK-IN]" not in msg


class TestEscapeMrkdwn:
    def test_escape_ampersand(self):
        assert _escape_mrkdwn("&") == "&amp;"

    def test_escape_lt(self):
        assert _escape_mrkdwn("<") == "&lt;"

    def test_escape_gt(self):
        assert _escape_mrkdwn(">") == "&gt;"

    def test_escape_order_no_double_escape(self):
        # "&lt;" input — & must escape first → "&amp;lt;"
        assert _escape_mrkdwn("&lt;") == "&amp;lt;"

    def test_no_change_plain_text(self):
        assert _escape_mrkdwn("hello world") == "hello world"


class TestMrkdwnInjectionPrevention:
    def test_spawned_escapes_objective(self):
        msg = format_worker_spawned("w1", "claude-max", "/repo", "<@U_ID> task")
        assert "<@U_ID>" not in msg
        assert "&lt;@U_ID&gt;" in msg

    def test_completed_escapes_summary(self):
        msg = format_worker_completed("w1", "<url|label>")
        assert "<url|label>" not in msg
        assert "&lt;url|label&gt;" in msg

    def test_failed_escapes_error(self):
        msg = format_worker_failed("w1", "<critical> & warning", attempts=1)
        assert "<critical>" not in msg
        assert "&lt;critical&gt;" in msg
        assert "&amp;" in msg

    def test_heartbeat_escapes_lt_gt(self):
        workers = [{"id": "w1", "description": "Your task: <script>evil</script>", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg

    def test_heartbeat_escapes_ampersand(self):
        workers = [{"id": "w1", "description": "Your task: foo & bar", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "foo & bar" not in msg
        assert "&amp;" in msg

    def test_heartbeat_escapes_url_link(self):
        workers = [{"id": "w1", "description": "Your task: <https://evil.com|click>", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "<https://evil.com|click>" not in msg
        assert "&lt;https://evil.com|click&gt;" in msg

    def test_heartbeat_escapes_user_mention(self):
        workers = [{"id": "w1", "description": "Your task: <@U_ADMIN> do this", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "<@U_ADMIN>" not in msg
        assert "&lt;@U_ADMIN&gt;" in msg

    def test_objective_received_escapes_mrkdwn(self):
        msg = format_objective_received("<alert> & 'test' > end")
        assert "<alert>" not in msg
        assert "&lt;alert&gt;" in msg
        assert "&amp;" in msg
        assert "&gt;" in msg


class TestFmtTokens:
    def test_below_1000(self):
        assert _fmt_tokens(500) == "500"

    def test_exact_1000(self):
        assert _fmt_tokens(1000) == "1.0k"

    def test_thousands(self):
        assert _fmt_tokens(150200) == "150.2k"

    def test_millions(self):
        assert _fmt_tokens(1500000) == "1.5M"

    def test_zero(self):
        assert _fmt_tokens(0) == "0"


class TestHeartbeatBrainLine:
    def test_heartbeat_with_brain_usage_appends_line(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        brain_usage = {"total_tokens": 150200, "input_tokens": 42100, "output_tokens": 108100, "cost_usd": 0.12}
        msg = format_heartbeat(workers, brain_usage=brain_usage)
        assert "🧠 Brain:" in msg
        assert "150.2k" in msg
        assert "42.1k" in msg
        assert "108.1k" in msg

    def test_heartbeat_without_brain_usage_no_brain_line(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers, brain_usage=None)
        assert "🧠" not in msg
        assert "Brain:" not in msg

    def test_heartbeat_no_workers_no_brain_line(self):
        """Early-return path must not include brain line even with brain_usage provided."""
        brain_usage = {"total_tokens": 5000, "input_tokens": 2000, "output_tokens": 3000, "cost_usd": 0.0}
        msg = format_heartbeat([], brain_usage=brain_usage)
        assert "No active workers" in msg
        assert "🧠" not in msg


class TestWorkerCheckinSlackNotification:
    def test_action_required_format(self):
        msg = format_worker_checkin_slack("w1", 5, "brainstorming", True)
        assert "[ACTION REQUIRED]" in msg
        assert "w1" in msg
        assert "5min" in msg
        assert "brainstorming" in msg
        assert "waiting for input" in msg

    def test_action_required_has_no_log_tail(self):
        msg = format_worker_checkin_slack("w1", 5, "brainstorming", True)
        assert "\n" not in msg

    def test_normal_checkin_format(self):
        msg = format_worker_checkin_slack("w1", 12, "executing", False)
        assert "[CHECK-IN]" in msg
        assert "w1" in msg
        assert "12min" in msg
        assert "executing" in msg

    def test_normal_checkin_no_waiting_phrase(self):
        msg = format_worker_checkin_slack("w1", 12, "executing", False)
        assert "waiting for input" not in msg

    def test_normal_checkin_has_no_log_tail(self):
        msg = format_worker_checkin_slack("w1", 12, "executing", False)
        assert "\n" not in msg


class TestWorkerGateStuckSlack:
    def test_format_includes_worker_id_and_stage(self):
        msg = format_worker_gate_stuck_slack("w-1", 30, "plan_ready")
        assert "w-1" in msg
        assert "plan_ready" in msg
        assert "30" in msg

    def test_format_includes_alert_prefix(self):
        msg = format_worker_gate_stuck_slack("w-1", 15, "design_ready")
        assert "[ALERT]" in msg
