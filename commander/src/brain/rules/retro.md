# Retrospective Workflow

## When to Run
- When {OPERATOR_NAME} asks for a retro, lookback, or review of recent work
- Periodically (weekly recommended) to assess system health

## Phase 1: Gather What Was Asked
Call `get_operator_messages(limit=100, hours_back=N)` where N covers the
retro period. Also call `get_directives()` for all statuses.

Build a list of:
- Every directive {OPERATOR_NAME} submitted
- Current status of each (confirmed, in_progress, completed, rejected)
- Any messages that expressed intent but weren't formalized as directives

## Phase 2: Gather What Was Done
Run `git log --oneline --since="YYYY-MM-DD"` for the retro period.

For each commit, note:
- What directive/objective it maps to
- Which worker produced it

Identify:
- Directives that resulted in commits (completed)
- Directives still in_progress with no commits
- Commits that don't map to any directive (unplanned work)
- Directives that were confirmed but never started

## Phase 3: Assess Quality
For each completed directive, evaluate:

| Question | How to answer |
|---|---|
| Did it ship? | Check git log for corresponding commit |
| Was it done right the first time? | Search episodic memory for fix cycles, rejected plans, worker restarts on this task |
| How many worker sessions did it take? | Check episodic memory + worker logs |
| Were there code review failures? | Search episodic memory for review grades on this work |
| Did the operator have to correct course? | Check Slack messages for corrections after the directive was started |

## Phase 4: Report
Present to {OPERATOR_NAME} via Slack:

**Completed:**
- [directive] — shipped in [commit]. [quality notes]

**In Progress:**
- [directive] — current status, what's blocking

**Not Started:**
- [directive] — confirmed but no worker spawned. Why?

**Unplanned:**
- [commit] — work that happened outside the directive workflow

**Quality Observations:**
- Pattern notes (e.g., "3 of 5 tasks required fix cycles after code review")
- Worker performance notes (e.g., "opus workers completed without rework, sonnet workers averaged 1.5 fix cycles")
- Recurring issues (e.g., "scope creep on feature tasks, clean execution on bugfixes")

## Phase 5: Update Stale State
After presenting the retro:
- Mark any completed directives that weren't updated: `update_directive_status(id, 'completed')`
- Flag any in_progress directives that appear stalled
- Note any patterns worth remembering (Brain can record to episodic memory)
