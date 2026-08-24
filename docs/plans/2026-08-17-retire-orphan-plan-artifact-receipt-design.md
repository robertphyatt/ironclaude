# Retire Orphan plan_artifact_receipts Row Design

> **Created:** 2026-08-17
> **Status:** Design Complete

## Summary

Session `bc27e45c-2bcd-4c7b-a245-aed7a9850b99` (workspace `roleplaying-agents`,
worktree `d2e4456e-313a-46a0-9343-0618aeeb668b`) is blocked at `mark_plan_ready`
by an orphaned `plan_artifact_receipts` row with `status='inactive'`. This is
the documented D2 defect class ("retreat orphans the receipt") from
[[project_plan_receipt_wedge_d1_d2]]: `retreat` after a seal does not clear the
session's inactive receipt, and every later `mark_plan_ready` in that session
then fails the seal-conflict check.

Independent evidence from episodic-memory search confirms the exact check:
`mark_plan_ready`'s seal-conflict lookup filters
`WHERE terminal_session=? AND repository_identity=? AND status='inactive'`
(`plan-artifacts.ts:229`, deployed 1.1.6 state-manager bundle). The block is
already scoped per `terminal_session`, so retiring only this session's row(s)
fully clears the block without touching any other session's receipts.

This design covers a single administrative DB correction: no repository files
change, no code changes, no other session's state is touched.

## Architecture

One execution task, one shell step: a single `python3 -c` invocation against
`~/.claude/ironclaude.db` that runs two SQL statements in one connection:

1. `SELECT COUNT(*) FROM plan_artifact_receipts WHERE status='inactive' AND terminal_session='bc27e45c-2bcd-4c7b-a245-aed7a9850b99'`
   — read-only verification that the targeted row(s) exist before mutating.
2. `UPDATE plan_artifact_receipts SET status='retired', retired_at=datetime('now') WHERE status='inactive' AND terminal_session='bc27e45c-2bcd-4c7b-a245-aed7a9850b99'`
   — the fix, scoped by both `status` and `terminal_session` so no other
   session's rows are affected.

No repository files are read or written. No `git add`/`git commit`. The task
produces a durable, gitignored findings note under `docs/` (per the avoid-recipe
in [[project_plan_receipt_wedge_d1_d2]]: a no-repo-file DB task still needs a
non-empty `allowed_files` entry to pass plan-schema validation) recording the
pre-check count, the rowcount retired, and the command run.

## Components

- **Verification step:** `SELECT COUNT(*)` scoped to `status='inactive' AND terminal_session=<target>`.
  If the count is not exactly 1, stop before running the `UPDATE` and report
  the mismatch (wrong session id, row already cleared, or more rows present
  than expected) instead of proceeding on an unverified assumption.
- **Mutation step:** scoped `UPDATE ... SET status='retired', retired_at=datetime('now')`,
  same `WHERE` clause as the verification step. Uses `UPDATE`, not `DELETE` —
  preserves the row for audit trail, matches the precedent from the 2026-08-16
  wsm-db-fix-1475c resolution and the FK-safety lesson in the search-agent
  findings (bare single-table `DELETE` orphaned child rows elsewhere in this
  DB; `UPDATE`-to-retired avoids that class of risk entirely since it is not a
  delete).
- **Findings note:** `docs/2026-08-17-retire-orphan-plan-artifact-receipt-findings.md`
  (gitignored, per [[project_docs_gitignored]]), listed as the task's one
  `allowed_files` entry, recording verified count, retired rowcount, and the
  exact command executed.

## Data Flow

Single sqlite3 connection, opened, both statements executed in sequence, one
`commit()`, connection closed. No transaction spans multiple connections, no
other table is touched.

## Error Handling

- If the verification `SELECT` returns 0: stop, do not run the `UPDATE`,
  report that no matching row was found for the named session (row already
  cleared, or session id mismatch) — do not guess or widen the filter.
- If the verification `SELECT` returns >1: stop, do not run the `UPDATE`
  blindly; report the actual count and rows found before deciding how to
  proceed, since the operator's success criterion assumes exactly 1.
- If the verification count is exactly 1: run the `UPDATE`, and confirm its
  `rowcount` also equals 1 (the two counts must agree — a mismatch between
  verified-count and update-rowcount indicates a race and must be reported,
  not silently accepted).
- Command must exit non-zero / print a clear message on any sqlite3 exception
  rather than swallowing it.

## Testing Strategy

Not applicable in the unit/integration-test sense — this is a one-time
administrative data correction, not a code change. "Testing" here is the
verification step itself (pre-count) plus the post-update rowcount check,
both asserted inline in the same command before declaring success.

## Implementation Notes

- Per [[project_plan_receipt_wedge_d1_d2]]: this DB is accessed from the
  **parent checkout**, not from inside a managed worktree — the
  professional-mode-guard blocks `~/.claude/ironclaude.db` access from inside
  a managed worktree. Confirm the execute-stage task runs in a context where
  this path is reachable; if blocked, that is itself a finding, not something
  to route around.
- Do not touch `ironclaude-workspaces.db` or any other table — this fix is
  scoped to `plan_artifact_receipts` only, for the one named session.
- `retired_at` uses `datetime('now')` (SQLite's own clock) rather than a
  Python-side timestamp, avoiding any host/DB clock skew question.

## Requirements

Folded into this single document (rather than a separate `-requirements.md`)
because the fix is a one-line, single-task administrative DB correction; a
second paired file would add no information the sections above don't already
state precisely.

### Functional Requirements

1. Verify, via a read-only `SELECT COUNT(*)`, that exactly one
   `plan_artifact_receipts` row exists with `status='inactive' AND
   terminal_session='bc27e45c-2bcd-4c7b-a245-aed7a9850b99'` before mutating
   anything.
2. Update that row (and only that row) to `status='retired'`, setting
   `retired_at=datetime('now')`.
3. The `WHERE` clause on the `UPDATE` must include both `status='inactive'`
   and `terminal_session='bc27e45c-2bcd-4c7b-a245-aed7a9850b99'` — never a
   bare `status='inactive'` filter that would touch other sessions' rows.
4. Confirm the `UPDATE`'s `rowcount` equals 1 and matches the verified
   pre-count before declaring success.
5. Record a findings note (gitignored, under `docs/`) with the verified
   count, the retired rowcount, and the exact command run.

### Safety Requirements

1. No repository file may be modified, staged, or committed as part of this
   task.
2. No table other than `plan_artifact_receipts` may be read or written.
3. No other session's `terminal_session` value may appear in the `WHERE`
   clause of the mutating statement.
4. Use `UPDATE ... SET status='retired'`, never `DELETE`, to preserve the row
   for audit trail.
5. If the pre-check count is 0 or greater than 1, the task must stop before
   running the `UPDATE` and report the discrepancy rather than proceeding on
   an unverified assumption.
6. This is a single administrative DB correction executed via direct human
   authority (per [[project_plan_receipt_wedge_d1_d2]]: "Recovery once wedged
   needs human authority — do NOT self-authorize"); the operator has
   explicitly authorized this specific session-scoped fix.

### Out of Scope

- Fixing the underlying D1/D2 state-manager defects themselves (seal-before-
  validate, retreat-orphans-receipt) — those are deployed-bundle product
  defects, tracked separately, not touched by this task.
- Any change to `ironclaude-workspaces.db` or other databases.
- Retiring inactive receipts for any session other than
  `bc27e45c-2bcd-4c7b-a245-aed7a9850b99`.
