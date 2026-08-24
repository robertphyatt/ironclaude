# WSM DB Stale-Workspace Cleanup (v3) Design

> **Created:** 2026-08-16
> **Status:** Design Complete
> **Scope mode:** hold

## Summary

`commit_worker` for `d1475-ch10-recovery` fails with:

```
ENOENT: no such file or directory, lstat '/Users/roberthyatt/Code/roleplaying-agents/.ironclaude/worktrees/082b7cb0-e725-4064-8355-2495b1eb3877'
```

The workspace-manager DB at `/Users/roberthyatt/.claude/ironclaude-workspaces.db`
retains an `assignments` row (and FK child rows) for `workspace_guid =
082b7cb0-e725-4064-8355-2495b1eb3877`, an integration worktree that was deleted
from disk. The stale row + FK children must be removed with an FK-safe
transactional delete, then a findings note recorded. No code change; nothing
inside the repo is mutated.

This is the **3rd attempt**. The task itself (the delete) is mechanical and
fully researched. The reason prior attempts failed is a **PM state-machine bug**,
addressed in Implementation Notes below.

## Root Cause (debugging already resolved)

- **Behavior:** `commit_worker` ENOENT on `lstat` of the deleted worktree path.
- **Root cause:** workspace-manager DB holds a `workspace_guid` row whose
  on-disk worktree was deleted; a later code path stats the recorded path.
- **Evidence:** the ENOENT names the exact `082b7cb0…` worktree path under
  `roleplaying-agents/.ironclaude/worktrees/`.
- **Fix:** delete the stale row and its FK children (FK-safe, parent last).

Root cause is confirmed and evidenced; `systematic-debugging` is satisfied — no
unclear behavior remains to investigate.

## Architecture / Approach (single viable path — hold scope)

FK-safe transactional delete against the workspace-manager SQLite DB. One
approach only; the SQL and schema are pre-researched by the operator and are not
to be re-derived.

- **Target GUID (delete):** `082b7cb0-e725-4064-8355-2495b1eb3877`
- **Negative-control GUID (MUST NOT be touched):**
  `2d8031c7-1ba3-4722-8de5-cb8a69b70e55` (d1475's own row)
- **Parent table:** `assignments`
- **FK child tables (delete before parent):** `primary_checkout_owners`,
  `integration_locks`, `integration_records`, `human_intents`
- **Correlation column:** `workspace_guid` in every case.

Rationale for a single approach: hold scope + one correct way to delete
referentially-linked rows safely (children first, parent last, inside one
transaction, with `PRAGMA foreign_keys=ON` set before `BEGIN`). No alternative
offers a different trade-off worth presenting.

## Data Flow (execution order)

1. **Step 0 — substring sweep** (`.dump | grep -F '082b7cb0'`). Guards against
   the GUID living in an unexpected column/row. Foreign-row hit → STOP + report.
   Zero lines → already clean; skip deletes, write "already clean" findings note.
2. **Step 1 — discovery.** Per-table SELECT for the target GUID; record baseline
   per-table row counts. Any returned row whose `workspace_guid` ≠ target → STOP.
3. **Step 2 — backup** the DB to `…ironclaude-workspaces.db.bak-d1475`.
4. **Step 3 — verify backup** exists and is > 0 bytes; else STOP.
5. **Step 4 — FK-safe transactional delete** (children first, parent last),
   `PRAGMA foreign_keys=ON` before `BEGIN`; each `changes()` must equal the
   Step 1 baseline for that table. SQL error → automatic rollback → report exact
   error + STOP. Never retry with `foreign_keys=OFF`.
6. **Step 5 — post-verify.** Target-GUID total across all 5 tables = 0;
   negative-control d1475 GUID count in `assignments` = 1.
7. **Step 6 — findings note** at `docs/2026-08-16-wsm-db-cleanup-findings.md`;
   do NOT `git add` it; enter `execution_complete`.

## Error Handling / STOP conditions

Every STOP condition below → report to Brain and hold; do **not** self-authorize
recovery (recovery needs human authority):

- Step 0 substring appears in a row whose `workspace_guid` differs from target.
- Step 1 returns any row whose `workspace_guid` ≠ target.
- Step 3 backup missing or 0 bytes.
- Step 4 any `changes()` ≠ Step 1 baseline, or any SQL error (rollback is
  automatic; never re-run with FK enforcement off).
- Step 5 target-total ≠ 0, or negative-control ≠ 1.

## Absolute-path constraint

Every Bash command uses ABSOLUTE paths. The `workspace-path-adapter` hook blocks
any command containing `~` or `$HOME`. Never use them.

## Testing Strategy

Verification is inline, not a separate test suite (DB-state mutation outside the
repo):

- **Baseline capture** (Step 1 counts) is the pre-condition oracle.
- **`changes()` equality** (Step 4) proves the delete removed exactly the
  discovered rows — no more, no fewer.
- **Post-verify** (Step 5): target-total = 0 (positive) AND negative-control
  d1475 GUID = 1 (proves the delete did not over-reach).
- **Backup** (Steps 2–3) is the rollback safety net.

## Implementation Notes (CRITICAL — the seal wedge)

Two prior workers (`wsm-db-fix-1475`, `wsm-db-fix-1475b`) completed
brainstorming/design but were permanently wedged at `mark_plan_ready` by a PM
plugin bug: **seal-before-validate**. When `writing-plans` emits a `plan.json`
missing required top-level fields (`plan_file`, `machine_plan_file`) or with
empty `allowed_files`, `mark_plan_ready` seals the incomplete artifact and any
later correction fails a seal-comparison check. This session has a fresh
`terminal_session` UUID with **zero orphaned `plan_artifact_receipt` rows** — the
one-shot resource that makes the first seal safe.

**Plan must be ONE task** covering Steps 0–6, with
`allowed_files: ["docs/2026-08-16-wsm-db-cleanup-findings.md"]`. Splitting the
DB-delete and the findings-note into separate tasks would give the DB-delete task
zero repo writes → `allowed_files: []` → the exact wedge. One task guarantees a
non-empty `allowed_files`.

**Pre-seal checklist (do every item before calling `mark_plan_ready`):**

1. After `writing-plans` emits `plan.json`, **Read the entire file** — do not
   trust the skill's summary.
2. Verify top-level `plan_file` = `docs/plans/2026-08-16-wsm-db-cleanup-v3.md`
   and `machine_plan_file` =
   `docs/plans/2026-08-16-wsm-db-cleanup-v3.plan.json`, both non-empty and
   matching the actual on-disk filenames; and every task's `allowed_files`
   non-empty.
3. If any field is missing/empty/mismatched, **Edit it in BEFORE**
   `mark_plan_ready`.
4. Run the transition preflight (`get_resume_state`), then call
   `mark_plan_ready` **exactly once**.

**Post-seal discipline:**

- Never edit plan artifacts after the seal.
- Never `retreat` (orphans the receipt → per-session permanent failure).
- On any `mark_plan_ready` error: STOP and report — do NOT issue a second
  `mark_plan_ready`.

## Success Criteria

- `total_remaining = 0` after Step 5 (target GUID gone from all 5 tables).
- d1475's GUID still has 1 row in `assignments` (negative control untouched).
- Findings note written at `docs/2026-08-16-wsm-db-cleanup-findings.md`
  (not staged).
- `execution_complete` entered.
