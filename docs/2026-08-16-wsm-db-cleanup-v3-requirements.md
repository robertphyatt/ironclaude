# WSM DB Stale-Workspace Cleanup (v3) Requirements

> **Created:** 2026-08-16
> **Status:** Requirements Complete
> **Design:** docs/plans/2026-08-16-wsm-db-cleanup-v3-design.md

## Objective

Remove the stale `workspace_guid = 082b7cb0-e725-4064-8355-2495b1eb3877` entry
and its FK child rows from `/Users/roberthyatt/.claude/ironclaude-workspaces.db`
using an FK-safe transactional delete, record a findings note, and enter
`execution_complete`. No repo code change.

## Functional Requirements

- **FR1 — Substring sweep first.** `.dump | grep -F '082b7cb0'` before any
  mutation. Foreign-row hit (GUID in a row whose `workspace_guid` ≠ target) →
  STOP + report. Zero lines → treat as already clean; skip deletes; findings note
  says "already clean".
- **FR2 — Discovery baseline.** Per-table SELECT for the target GUID across
  `assignments`, `primary_checkout_owners`, `integration_locks`,
  `integration_records`, `human_intents`; record per-table baseline counts.
  Any returned row whose `workspace_guid` ≠ target → STOP.
- **FR3 — Backup + verify.** Copy DB to
  `/Users/roberthyatt/.claude/ironclaude-workspaces.db.bak-d1475`; confirm the
  backup exists and is > 0 bytes before any delete; else STOP.
- **FR4 — FK-safe transactional delete.** `PRAGMA foreign_keys=ON` before
  `BEGIN`; delete FK children first, `assignments` last, single transaction;
  each `changes()` equals the FR2 baseline for that table. SQL error → automatic
  rollback → report exact error + STOP. Never re-run with `foreign_keys=OFF`.
- **FR5 — Post-verify.** Sum of target-GUID rows across all 5 tables = 0 AND
  negative-control d1475 GUID (`2d8031c7-1ba3-4722-8de5-cb8a69b70e55`) count in
  `assignments` = 1.
- **FR6 — Findings note.** Write `docs/2026-08-16-wsm-db-cleanup-findings.md`
  recording: tables/columns/rows touched, exact queries, per-table `changes()`,
  Step 5 results, Step 0 sweep note. Do NOT `git add` it. Enter
  `execution_complete`.

## Constraints

- **C1 — Absolute paths only.** No `~` / `$HOME` in any Bash command
  (`workspace-path-adapter` hook blocks them).
- **C2 — No git staging of mutations.** No `git add` / `git commit`; everything
  mutated is outside the repo. (Planning artifacts — design/requirements — are
  staged as planning evidence, not implementation output.)
- **C3 — Negative control inviolate.** `2d8031c7-1ba3-4722-8de5-cb8a69b70e55`
  must not be read-for-delete, matched, or removed.
- **C4 — Single-task plan; seal discipline.** Plan is ONE task with non-empty
  `allowed_files: ["docs/2026-08-16-wsm-db-cleanup-findings.md"]`. Verify
  `plan.json` top-level `plan_file` / `machine_plan_file` and per-task
  `allowed_files` are populated and match on-disk filenames BEFORE the first
  `mark_plan_ready`. Never edit artifacts after seal; never `retreat`; no second
  `mark_plan_ready` after an error.
- **C5 — Recovery is human-authorized.** Any STOP condition → report to Brain and
  hold; never self-authorize recovery.

## Out of Scope

- Fixing the PM seal-before-validate plugin bug itself (worked around, not fixed).
- Any change to `roleplaying-agents` repo files or the deleted worktree.
- Removing any workspace row other than the target GUID.

## Success Criteria

- `total_remaining = 0` after FR5.
- d1475 GUID still has exactly 1 `assignments` row (negative control).
- Findings note written at `docs/2026-08-16-wsm-db-cleanup-findings.md` (unstaged).
- `execution_complete` entered.
