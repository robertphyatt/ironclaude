# tests/test_notifications.py
import pytest
from ironclaude.notifications import (
    _escape_mrkdwn,
    format_worker_spawned,
    format_worker_completed,
    format_worker_failed,
    format_heartbeat,
    format_brain_restarted,
    format_brain_circuit_breaker,
    format_objective_received,
    format_task_progress,
    format_worker_checkin,
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
        workers = [
            {"id": "worker-abc", "description": "Implement auth flow", "workflow_stage": "executing"},
            {"id": "worker-def", "description": "Fix CSS layout", "workflow_stage": "brainstorming"},
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
        workers = [{"id": "w1", "description": "A" * 80, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "A" * 60 in msg
        assert "..." in msg
        assert "A" * 80 not in msg

    def test_heartbeat_none_description(self):
        workers = [{"id": "w1", "description": None, "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "no task" in msg

    def test_heartbeat_none_stage(self):
        workers = [{"id": "w1", "description": "Do stuff", "workflow_stage": None}]
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
        workers = [{"id": "w1", "description": "<script>evil</script>", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg

    def test_heartbeat_escapes_ampersand(self):
        workers = [{"id": "w1", "description": "foo & bar", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "foo & bar" not in msg
        assert "&amp;" in msg

    def test_heartbeat_escapes_url_link(self):
        workers = [{"id": "w1", "description": "<https://evil.com|click>", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "<https://evil.com|click>" not in msg
        assert "&lt;https://evil.com|click&gt;" in msg

    def test_heartbeat_escapes_user_mention(self):
        workers = [{"id": "w1", "description": "<@U_ADMIN> do this", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "<@U_ADMIN>" not in msg
        assert "&lt;@U_ADMIN&gt;" in msg

    def test_objective_received_escapes_mrkdwn(self):
        msg = format_objective_received("<alert> & 'test' > end")
        assert "<alert>" not in msg
        assert "&lt;alert&gt;" in msg
        assert "&amp;" in msg
        assert "&gt;" in msg
