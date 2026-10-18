# Blocked-Task Follow-up Hardening Design

> **Created:** 2026-10-15
> **Status:** Design Complete

Loop: light. This touches no DB, auth, guard, concurrency, git or public API; about 3 files and 25 lines.

## Summary

This follows the staged blocked-task visibility change (`docs/plans/2026-10-15-blocked-task-visibility-design.md`, not yet committed). The Opus end review of that diff found three gaps, and the orchestrator confirmed each against the source:

1. **Crash path.** `check_idle_enforcement` (`commander/src/ironclaude/main.py:5279-5281`) builds its WARNING message with `t.get("description", "unknown")[:40]`, outside any try. Its caller, `run()` (`main.py:5768`), has no try either.
   - Before the staged change, that code could not be reached, because the daemon read a ledger file nothing writes.
   - It now reads the real wiki ledger, and `update_ledger` does not validate field types.
   - So a `pending` or `in_progress` task with a `null` or numeric description raises `TypeError` and stops the Commander.
2. **Unescaped task ids.** The new code in `notifications.py` puts the ledger task id into Slack raw, both in the heartbeat's blocked-task line and in `format_blocked_reminder`. Descriptions go through `_escape_mrkdwn` (`notifications.py:12`); ids do not. An id such as `<!channel>` would ping the channel.
3. **Untested first-reminder delay.** No test checks that a recently blocked task gets no reminder yet. Every reminder test uses a task blocked 5 hours ago.

## Requirements

- **R1:** idle enforcement renders each ledger task description as `str(t.get("description") or "unknown")[:40]`, so a non-string or missing description can never raise. *(Operator 2026-10-15: "Approve (Recommended)".)*
- **R2:** the blocked-task heartbeat line and `format_blocked_reminder` render the task id as `_escape_mrkdwn(str(task.get("id", "?")))`.
- **R3:** a test proves that an escalated task blocked 1 hour ago gets no reminder on the first heartbeat, and gets one once 4 hours have passed since `status_set_at`.

**Rejected alternative:** wrapping all of `check_idle_enforcement` in try/except. It would hide every future defect in that function and is broader than this one.

**Non-goals:** everything else from the end review's observations (reminders repeating after a restart, the `## Data` parser fragility, negative ages).

## Testing Strategy

All tests go in `commander/tests/test_blocked_tasks.py` and are written to fail first:

- **Idle enforcement.** A daemon whose `_ledger_path` points at a wiki ledger with a `pending` task whose description is `None`, set to the tier-2 window and with no live workers. `check_idle_enforcement()` raises `TypeError` before the fix. After the fix it sends the Brain a message containing "unknown".
- **Escaping.** With task id `<!channel>`, the output of both `format_heartbeat` and `format_blocked_reminder` contains `&lt;!channel&gt;` and never `<!channel>`.
- **First-reminder delay.** The R3 case above.
- **Regression.** The full commander suite.
