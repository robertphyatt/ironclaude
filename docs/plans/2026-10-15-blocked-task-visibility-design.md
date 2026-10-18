# Blocked-Task Visibility Design

> **Created:** 2026-10-15
> **Status:** Design Complete

Loop: full. This changes the daemon heartbeat and adds new Slack posting behaviour, across about 5 files and 150 lines.

## Summary

After d1501 R7 (2026-10-13 20:24Z), the Brain marked ledger task t14 `blocked`, pending the operator's Option A/B decision (Slack pin `1791922986.893029`). Then it went quiet for about 27 hours. The heartbeat said "No active workers" and never mentioned the decision.

Verified causes:
- **The wait expired.** The Brain's "Waiting for Robert's decision…" text was captured as an operator-wait (`main.py:3289-3374`). That suppresses it as narration and posts one `⏳` alert. Waits live only in memory with a 600 s TTL (`:200`), so it fell off the heartbeat almost at once. Nothing re-asks.
- **The heartbeat never reads the ledger** (`main.py:5542-5555`). The daemon's two ledger reads (`main.py:3523`, `:5222`) use `/tmp/ic/task-ledger.json` (`:1564`), which nothing writes. `update_ledger` writes the wiki page `<brain_cwd>/wiki/tasks.md`, ending in a `## Data` JSON block (`orchestrator_mcp.py:6429-6435`).

## Requirements

- **R1:** each heartbeat lists ledger tasks with status `blocked`, read from the wiki ledger. For each it shows the id, the description (which carries the reason by the workflow.md:918 convention) and how long it has been blocked (`status_set_at`), plus a permalink to the pinned escalation when `escalation_ts` is set. The section is deterministic: it appears only while a task is really `blocked`. *(Operator 2026-10-15: "Show blocked ledger tasks (Recommended)".)*
- **R2:** while a blocked task has an `escalation_ts` (a pinned decision for the operator), the daemon posts a top-level reminder at most every 4 hours. The reminder names the task, how long it has been blocked and the escalation permalink. Resource-paused blocked tasks without an escalation get no reminder. *(Operator 2026-10-15: "Every 4 hours (Recommended)".)*
- **R3:** the daemon reads the real wiki ledger. Its dead legacy-file reads (status display, idle enforcement) switch to the same reader. The parser is shared with the orchestrator, so there is one implementation.

**Non-goals:**
- the operator-wait capture, its TTL or its grader (the July false-positive design stays);
- Brain rule changes;
- Codex.

Once the daemon reads the wiki ledger, idle enforcement's `pending`/`in_progress` ledger check starts nudging the Brain about such tasks. That is the check's intended behaviour; the restart after this change is when it becomes observable.

## Architecture

**`protocol.py`:**
- `extract_ledger_json(body)` is moved verbatim from `OrchestratorTools._extract_ledger_json`, which now delegates to it.
- `read_task_ledger(path)` handles both formats: for a `.md` path it parses the JSON in the `## Data` block (the front matter comes before it and is ignored); JSON stays for compatibility. It returns `None` when the file is missing, undecodable or unparseable.

**`main.py`:**
- `_ledger_path` becomes `os.path.join(os.path.expanduser(brain_cwd), "wiki", "tasks.md")` when `brain_cwd` is configured, the same derivation as `orchestrator_mcp.py:516`. Production always has `brain_cwd`, through DEFAULTS. Configs without it keep the legacy JSON path.
- The Slack `status` command computes progress as completed/total from the ledger's `tasks` list, because the wiki ledger has no `current_task`/`total_tasks` keys.
- New `_blocked_ledger_tasks()` returns the list of blocked task dicts.
- `post_heartbeat` passes them to `format_heartbeat` and then calls `_maybe_remind_blocked(tasks)`.
- `_maybe_remind_blocked` keeps an in-memory `{task_id: last_reminded_epoch}`. It posts when `now − max(status_set_at, last_reminded) ≥ blocked_reminder_interval_seconds`, and only for tasks with `escalation_ts`. Entries for tasks that are no longer blocked are pruned. After a restart this can produce one reminder for a long-blocked task; that is accepted.

**`notifications.py`:**
- `format_heartbeat(..., blocked_tasks=None)` adds a `🚧 *Blocked tasks (waiting on {operator} where pinned):*` section, one line per task: `• <id> — <description, truncated> (blocked <age>)` plus the permalink when available.
- A blocked task also counts as content, so a heartbeat with only blocked tasks no longer short-circuits to "No active workers".
- New `format_blocked_reminder(task, operator_name)` builds the reminder; the age and permalink come from the task dict.

**`config.py`:** `DEFAULTS["blocked_reminder_interval_seconds"] = 14400`.

## Error Handling

- A missing or unparseable ledger is treated as no blocked tasks: logged, never a crash.
- A failed permalink lookup omits the link.
- A failed reminder post is queued by `SlackBot.post_message` and delivered later by `flush_queue`. `last_reminded` therefore advances on every attempt, because posting again would duplicate the reminder.

## Testing Strategy

- **`format_heartbeat`:** with blocked tasks only, the section appears and "No active workers" is gone; there is no section when the list is empty; the permalink is rendered when given.
- **`read_task_ledger`:** parses a wiki `.md` with front matter and a `## Data` block; JSON is still read; a missing file gives `None`.
- **`_maybe_remind_blocked`:**
  - posts once for an escalated task blocked for 4 h or more;
  - does not post again within 4 h;
  - posts again after 4 h;
  - never posts for a blocked task without `escalation_ts`;
  - prunes tasks that are no longer blocked;
  - a failed post is not posted again within the interval.
- **Status command:** with a wiki ledger, `status` shows completed/total progress and does not crash.
- **Regression:** the full commander suite.

## Implementation Notes

- The daemon runs from the working tree. After commit, restart the Commander.
- Reminders post top-level, not threaded, by design (R2).
