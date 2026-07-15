# tests/test_notifications.py
import pytest
from ironclaude.notifications import (
    _escape_mrkdwn,
    _fmt_tokens,
    _fmt_duration,
    format_worker_spawned,
    format_worker_completed,
    format_worker_failed,
    format_heartbeat,
    format_brain_restarted,
    format_brain_circuit_breaker,
    format_objective_received,
    format_task_progress,
    format_plan_ready,
    format_worker_checkin,
    format_worker_checkin_slack,
    format_worker_gate_stuck_slack,
    format_worker_heartbeat_stuck_slack,
    format_fable_unavailable,
    format_fable_recovered,
    format_directive_review,
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

    def test_task_progress_escapes_description(self):
        msg = format_task_progress(current=2, total=5, description="<!channel> deploy now")
        assert "<!channel>" not in msg
        assert "&lt;!channel&gt;" in msg

    def test_plan_ready_escapes_plan_summary(self):
        msg = format_plan_ready("w1", "<!here> approve <http://evil|click>")
        assert "<!here>" not in msg
        assert "&lt;!here&gt;" in msg
        assert "<http://evil|click>" not in msg
        assert "&lt;http://evil|click&gt;" in msg

    def test_worker_checkin_escapes_log_tail(self):
        msg = format_worker_checkin("w1", 15, "executing", "<!channel> injected log", False)
        assert "<!channel>" not in msg
        assert "&lt;!channel&gt;" in msg


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

    def test_heartbeat_zero_tokens_with_recent_activity(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        brain_usage = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "seconds_since_last_activity": 180}
        msg = format_heartbeat(workers, brain_usage=brain_usage)
        assert "turn in progress" in msg
        assert "3m ago" in msg

    def test_heartbeat_zero_tokens_no_activity_signal(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        brain_usage = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "seconds_since_last_activity": None}
        msg = format_heartbeat(workers, brain_usage=brain_usage)
        assert "turn in progress" not in msg
        assert "0 tokens (0 in + 0 out)" in msg

    def test_heartbeat_nonzero_tokens_unaffected(self):
        workers = [{"id": "w-1", "description": "Your task: Fix auth", "workflow_stage": "executing"}]
        brain_usage = {"total_tokens": 1000, "input_tokens": 400, "output_tokens": 600, "seconds_since_last_activity": 5}
        msg = format_heartbeat(workers, brain_usage=brain_usage)
        assert "turn in progress" not in msg


class TestFmtDuration:
    def test_fmt_duration_seconds(self):
        assert _fmt_duration(45) == "45s"

    def test_fmt_duration_minutes(self):
        assert _fmt_duration(180) == "3m"

    def test_fmt_duration_hours(self):
        assert _fmt_duration(7200) == "2h"


class TestHeartbeatWaits:
    """format_heartbeat surfaces 'waiting on commander'/'waiting on operator' state
    in every heartbeat, as two always-paired labeled sections."""

    def test_waits_shows_both_sections_and_tags_worker(self):
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        waits = {"d1267": {"question": "approve the migration?"}}
        commander_waits = {"d1268": {"question": "deploy approved?"}}
        msg = format_heartbeat(workers, waits=waits, commander_waits=commander_waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" in msg
        assert "⏳ *WAITING ON Robert:*" in msg
        assert "d1267" in msg
        assert "approve the migration?" in msg
        # the worker's own line is tagged as waiting on the operator
        worker_line = next(ln for ln in msg.splitlines() if ln.startswith("•") and "d1267" in ln)
        assert "waiting on robert" in worker_line.lower()

    def test_commander_section_omitted_when_empty(self):
        """Regression guard for the confirmed d1389 bug: format_heartbeat always
        rendered an empty '⏳ WAITING ON COMMANDER: / there is nothing' block
        whenever any real operator wait existed, because no caller ever passes
        commander_waits. The section must now be omitted entirely when empty."""
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        waits = {"d1267": {"question": "approve the migration?"}}
        msg = format_heartbeat(workers, waits=waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" not in msg
        assert "there is nothing" not in msg

    def test_operator_section_says_there_is_nothing_when_only_commander_populated(self):
        """Symmetric fallback, exercised directly even though main.py never produces
        this combination today (commander_waits is never populated by any caller)."""
        workers = [{"id": "d1267", "description": "Your task: Fix band collapse", "workflow_stage": "executing"}]
        commander_waits = {"d1267": {"question": "deploy approved?"}}
        msg = format_heartbeat(workers, waits={}, commander_waits=commander_waits, operator_name="Robert")
        assert "⏳ *WAITING ON COMMANDER:*" in msg
        assert "deploy approved?" in msg
        lines = msg.splitlines()
        idx = lines.index("⏳ *WAITING ON Robert:*")
        assert lines[idx + 1].strip() == "there is nothing"
        worker_line = next(ln for ln in msg.splitlines() if ln.startswith("•") and "d1267" in ln)
        assert "waiting on commander" in worker_line.lower()

    def test_waits_none_is_unchanged(self):
        workers = [{"id": "w1", "description": "Your task: Do stuff", "workflow_stage": "executing"}]
        assert format_heartbeat(workers, waits=None) == format_heartbeat(workers)
        assert "WAITING ON" not in format_heartbeat(workers, waits=None)

    def test_waits_empty_is_unchanged(self):
        workers = [{"id": "w1", "description": "Your task: Do stuff", "workflow_stage": "executing"}]
        assert format_heartbeat(workers, waits={}) == format_heartbeat(workers)

    def test_waits_shown_even_with_no_active_workers(self):
        """A held wait must surface even if the worker session is no longer listed."""
        msg = format_heartbeat([], waits={"d1267": {"question": "approve?"}})
        assert "WAITING ON COMMANDER" not in msg
        assert "WAITING ON Operator" in msg
        assert "d1267" in msg
        assert "approve?" in msg

    def test_operator_name_defaults_to_operator(self):
        msg = format_heartbeat([], waits={"d1267": {"question": "approve?"}})
        assert "⏳ *WAITING ON Operator:*" in msg


class TestHeartbeatActiveWorkersHeader:
    def test_header_shown_when_waits_and_workers_present(self):
        workers = [{"id": "d1366", "description": "Your task: Convergence work", "workflow_stage": "executing"}]
        waits = {"d1397": {"question": "Status query about d1384"}}
        msg = format_heartbeat(workers, waits=waits, operator_name="Robert")
        assert "*Active Workers:*" in msg

    def test_header_appears_between_waiting_section_and_worker_line(self):
        workers = [{"id": "d1366", "description": "Your task: Convergence work", "workflow_stage": "executing"}]
        waits = {"d1397": {"question": "Status query about d1384"}}
        msg = format_heartbeat(workers, waits=waits, operator_name="Robert")
        lines = msg.splitlines()
        waiting_idx = next(i for i, ln in enumerate(lines) if "WAITING ON Robert" in ln)
        header_idx = next(i for i, ln in enumerate(lines) if ln == "*Active Workers:*")
        worker_idx = next(i for i, ln in enumerate(lines) if ln.startswith("•") and "d1366" in ln)
        assert waiting_idx < header_idx < worker_idx

    def test_header_absent_when_waits_empty(self):
        workers = [{"id": "d1366", "description": "Your task: Convergence work", "workflow_stage": "executing"}]
        msg = format_heartbeat(workers)
        assert "*Active Workers:*" not in msg

    def test_header_absent_when_workers_empty(self):
        waits = {"d1397": {"question": "Status query about d1384"}}
        msg = format_heartbeat([], waits=waits, operator_name="Robert")
        assert "*Active Workers:*" not in msg


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


class TestFormatWorkerHeartbeatStuckSlack:
    def test_contains_worker_id(self):
        msg = format_worker_heartbeat_stuck_slack("d1161-robert-brainstorm", "brainstorming")
        assert "d1161-robert-brainstorm" in msg

    def test_contains_stage(self):
        msg = format_worker_heartbeat_stuck_slack("w1", "executing")
        assert "executing" in msg

    def test_starts_with_stuck_prefix(self):
        msg = format_worker_heartbeat_stuck_slack("w1", "brainstorming")
        assert msg.startswith("[STUCK]")

    def test_mentions_brain_intervention(self):
        msg = format_worker_heartbeat_stuck_slack("w1", "brainstorming")
        assert "Brain intervention" in msg


class TestFableNotifications:
    def test_unavailable_contains_title(self):
        msg = format_fable_unavailable("spawn-died", "opus")
        assert "⚠️" in msg
        assert "Fable unavailable" in msg

    def test_unavailable_names_reason(self):
        msg = format_fable_unavailable(reason="spawn-died", redirected_to="opus")
        assert "spawn-died" in msg

    def test_unavailable_names_redirect_target(self):
        msg = format_fable_unavailable("r", redirected_to="opus")
        assert "opus" in msg

    def test_unavailable_names_worker_id_when_given(self):
        msg = format_fable_unavailable("r", "opus", worker_id="d1267")
        assert "d1267" in msg

    def test_unavailable_omits_worker_id_when_none(self):
        msg = format_fable_unavailable("r", "opus", worker_id=None)
        assert "None" not in msg

    def test_unavailable_mentions_24h_window(self):
        msg = format_fable_unavailable("r", "opus")
        assert "24" in msg

    def test_unavailable_gives_reprobe_hint(self):
        msg = format_fable_unavailable("r", "opus")
        assert "rm" in msg
        assert "~/.ironclaude/state/fable_unavailable.json" in msg

    def test_unavailable_escapes_mrkdwn_in_reason(self):
        msg = format_fable_unavailable("<script>", "opus")
        assert "&lt;script&gt;" in msg
        assert "<script>" not in msg

    def test_unavailable_escapes_mrkdwn_in_worker_id(self):
        msg = format_fable_unavailable("r", "opus", worker_id="<bad>")
        assert "&lt;bad&gt;" in msg
        assert "<bad>" not in msg

    def test_recovered_contains_title(self):
        msg = format_fable_recovered()
        assert "✅" in msg
        assert "Fable is back" in msg

    def test_recovered_mentions_recovery_effect(self):
        msg = format_fable_recovered()
        assert "claude-fable" in msg or "advisor" in msg


class TestFormatDirectiveReview:
    def test_contains_directive_id_and_interpretation(self):
        msg = format_directive_review(
            42, "do the thing", "op message", "claude-opus", True,
            "prompt", "reason1", "reason2", "reason3",
        )
        assert "Directive #42" in msg
        assert "do the thing" in msg

    def test_contains_source_text(self):
        msg = format_directive_review(
            42, "do the thing", "op message", "claude-opus", True,
            "prompt", "reason1", "reason2", "reason3",
        )
        assert "op message" in msg

    def test_lists_all_three_planned_fields_with_reasons(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", False,
            "do X", "tier-r", "goal-r", "prompt-r",
        )
        assert "claude-sonnet" in msg
        assert "tier-r" in msg
        assert "goal-r" in msg
        assert "prompt-r" in msg
        assert "do X" in msg

    def test_prompt_is_code_fenced(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", True,
            "hello world", "r1", "r2", "r3",
        )
        assert "```\nhello world\n```" in msg

    def test_use_goal_true_renders_yes_branch(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", True,
            "prompt", "r1", "r2", "r3",
        )
        goal_line = next(ln for ln in msg.splitlines() if "/goal" in ln)
        assert "yes" in goal_line
        assert "no" not in goal_line

    def test_use_goal_false_renders_no_branch(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", False,
            "prompt", "r1", "r2", "r3",
        )
        goal_line = next(ln for ln in msg.splitlines() if "/goal" in ln)
        assert "no" in goal_line
        assert "yes" not in goal_line

    def test_supersedes_none_omits_revised_from_line(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", True,
            "prompt", "r1", "r2", "r3",
        )
        assert "revised from" not in msg

    def test_supersedes_id_includes_revised_from_line(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", True,
            "prompt", "r1", "r2", "r3", supersedes=41,
        )
        assert "(revised from #41)" in msg

    def test_escapes_mrkdwn_in_all_user_strings(self):
        msg = format_directive_review(
            1, "<script>", "a&b", "opus<x>", True,
            "safe prompt", "r1>", "r2", "r3",
        )
        assert "&lt;script&gt;" in msg
        assert "<script>" not in msg
        assert "a&amp;b" in msg
        source_line = next(ln for ln in msg.splitlines() if "From your message" in ln)
        assert "a&b" not in source_line

    def test_trailing_reaction_line_includes_all_three_reactions(self):
        msg = format_directive_review(
            1, "interp", "source", "claude-sonnet", True,
            "prompt", "r1", "r2", "r3",
        )
        assert "👍" in msg
        assert "👎" in msg
        assert "🤔" in msg
        assert "React" in msg

    def test_format_directive_review_survives_triple_backtick_in_planned_prompt(self):
        """Regression NOTIF-01: `planned_prompt` may contain triple-backticks
        (LLM-authored). Without escaping, they close the fence early and
        everything after renders as live mrkdwn instead of literal prompt text."""
        out = format_directive_review(
            directive_id=1,
            interpretation="interp",
            source_text="src",
            planned_worker_type="claude-sonnet",
            planned_use_goal=False,
            planned_prompt="do the thing ```python\nfoo()\n``` then stop",
            planned_worker_type_reason="r1",
            planned_use_goal_reason="r2",
            planned_prompt_reason="r3",
        )
        # Fence markers must remain balanced.
        assert out.count("```") == 2, (
            f"expected exactly two ``` fence markers, got {out.count('```')}. "
            f"Backticks inside planned_prompt must be escaped so they don't "
            f"terminate the code fence early.\nMessage:\n{out}"
        )
        # The escaped backticks should be present as `\`` sequences.
        assert "\\`\\`\\`python" in out or "\\`\\`\\`" in out, (
            f"Expected escaped triple-backticks (\\`\\`\\`) in output; got:\n{out}"
        )

    def test_planned_prompt_mrkdwn_special_sequences_escaped(self):
        """I-4 regression: Slack parses <!channel>/<@U…> sequences at the payload
        level even inside code fences. planned_prompt is LLM-authored and must be
        mrkdwn-escaped in addition to backtick-escaped."""
        out = format_directive_review(
            directive_id=1,
            interpretation="interp",
            source_text="src",
            planned_worker_type="claude-sonnet",
            planned_use_goal=False,
            planned_prompt="ping <!channel> & <@U123> now",
            planned_worker_type_reason="r1",
            planned_use_goal_reason="r2",
            planned_prompt_reason="r3",
        )
        assert "&lt;!channel&gt;" in out, out
        assert "<!channel>" not in out, out
        assert "&lt;@U123&gt;" in out, out
        assert "<@U123>" not in out, out
        # & must be escaped exactly once (no double-escape of the &lt; entities)
        assert "&amp; " in out, out

    def test_planned_worker_type_backtick_escaped(self):
        """Q-1 regression: planned_worker_type sits inside a single-backtick span;
        an unexpected backtick must not break the span (nothing in submit_directive
        enforces the worker-type vocabulary)."""
        out = format_directive_review(
            directive_id=1,
            interpretation="interp",
            source_text="src",
            planned_worker_type="claude`x",
            planned_use_goal=False,
            planned_prompt="p",
            planned_worker_type_reason="r1",
            planned_use_goal_reason="r2",
            planned_prompt_reason="r3",
        )
        assert "claude\\`x" in out, out

    def test_format_directive_review_survives_backtick_in_source_text(self):
        """Regression NOTIF-01: `source_text` sits inside a single-backtick
        span. A backtick in source_text closes the span early."""
        out = format_directive_review(
            directive_id=1,
            interpretation="interp",
            source_text="run `ls` please",
            planned_worker_type="claude-sonnet",
            planned_use_goal=False,
            planned_prompt="p",
            planned_worker_type_reason="r1",
            planned_use_goal_reason="r2",
            planned_prompt_reason="r3",
        )
        # The `_From your message:_ `…` span should have TWO enclosing single
        # backticks and any internal backticks escaped as `\``.
        assert "_From your message:_ `run \\`ls\\` please`" in out, (
            f"Expected escaped source_text inside single-backtick span. Got:\n{out}"
        )


def test_heartbeat_shows_ollama_degraded_marker():
    from ironclaude.notifications import format_heartbeat
    out = format_heartbeat([{"id": "d1", "description": "x", "workflow_stage": "executing"}],
                           ollama_degraded=True)
    assert "validator degraded" in out.lower()


def test_heartbeat_no_marker_when_healthy():
    from ironclaude.notifications import format_heartbeat
    out = format_heartbeat([{"id": "d1", "description": "x", "workflow_stage": "executing"}],
                           ollama_degraded=False)
    assert "validator degraded" not in out.lower()


def test_heartbeat_shows_marker_in_idle_no_workers_case():
    from ironclaude.notifications import format_heartbeat
    out = format_heartbeat([], ollama_degraded=True)   # no workers/waits -> early-return path
    assert "validator degraded" in out.lower()
