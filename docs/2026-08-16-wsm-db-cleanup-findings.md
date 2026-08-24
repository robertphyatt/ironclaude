# WSM DB Stale-Workspace Cleanup (v3) — Findings

> **Date:** 2026-08-16
> **Session:** wsm-db-fix-1475c (3rd attempt; first to clear the mark_plan_ready seal wedge)
> **DB:** /Users/roberthyatt/.claude/ironclaude-workspaces.db
> **Target GUID (deleted):** 082b7cb0-e725-4064-8355-2495b1eb3877
> **Negative-control GUID (untouched):** 2d8031c7-1ba3-4722-8de5-cb8a69b70e55

## Outcome

Stale workspace row removed via FK-safe transactional delete. `total_remaining = 0`
across all 5 tables; negative control intact (d1475 own GUID still 1 row). Backup
retained at `/Users/roberthyatt/.claude/ironclaude-workspaces.db.bak-d1475`.

## Step 0 — substring sweep

Command:

```
sqlite3 /Users/roberthyatt/.claude/ironclaude-workspaces.db ".dump" | grep -F '082b7cb0'
```

Result: exactly ONE line — an `INSERT INTO assignments` whose first column
(`workspace_guid`) is the target GUID. The other `082b7cb0` substrings on that line
are that same row's `worktree_path`
(`/Users/roberthyatt/Code/roleplaying-agents/.ironclaude/worktrees/082b7cb0-…`) and
`branch` (`ironclaude/082b7cb0-…`), which are named after the GUID. No foreign row
anywhere in the dump held the GUID, and no rows appeared for the 4 FK child tables.
Not a STOP condition → proceeded.

## Step 1 — discovery baseline

The GUID appears only in the `workspace_guid` column, in every case. Per-table
baseline counts:

| Table | Column | Baseline rows |
|---|---|---|
| assignments | workspace_guid | 1 (lifecycle_status = `abandoned`, worker_id `d1466-phase3-config-fix`) |
| primary_checkout_owners | workspace_guid | 0 |
| integration_locks | workspace_guid | 0 |
| integration_records | workspace_guid | 0 |
| human_intents | workspace_guid | 0 |

Only the parent `assignments` row existed; the FK child tables held no rows for this
GUID. Every returned row's `workspace_guid` equalled the target.

## Step 2–3 — backup

```
cp /Users/roberthyatt/.claude/ironclaude-workspaces.db /Users/roberthyatt/.claude/ironclaude-workspaces.db.bak-d1475
ls -la /Users/roberthyatt/.claude/ironclaude-workspaces.db /Users/roberthyatt/.claude/ironclaude-workspaces.db.bak-d1475
```

Both files present at 122880 bytes (> 0). Backup verified.

## Step 4 — FK-safe transactional delete

`PRAGMA foreign_keys = ON` set before `BEGIN`; FK children deleted first, parent
`assignments` last, single transaction. Per-table `changes()` (in delete order),
each equal to the Step 1 baseline:

| Delete order | Table | changes() | Baseline | Match |
|---|---|---|---|---|
| 1 | primary_checkout_owners | 0 | 0 | ✓ |
| 2 | integration_locks | 0 | 0 | ✓ |
| 3 | integration_records | 0 | 0 | ✓ |
| 4 | human_intents | 0 | 0 | ✓ |
| 5 | assignments | 1 | 1 | ✓ |

`COMMIT` succeeded with no FK-violation error.

## Step 5 — post-verify

```
-- 5a: sum of target-GUID rows across all 5 tables
SELECT (…COUNT(*) across assignments + primary_checkout_owners + integration_locks + integration_records + human_intents…) AS total_remaining;
-- => 0

-- 5b: negative control
SELECT COUNT(*) FROM assignments WHERE workspace_guid='2d8031c7-1ba3-4722-8de5-cb8a69b70e55';
-- => 1
```

- `total_remaining = 0` — target GUID fully removed (delete did not under-reach).
- d1475 own-GUID count = `1` — negative control untouched (delete did not over-reach).

## Notes

- All commands used absolute paths (no `~` / `$HOME`), per the workspace-path-adapter
  hook constraint.
- No repo files were mutated except this findings note; it is intentionally NOT
  git-staged. Everything else changed is outside the repo (the workspace-manager DB
  and its backup).
