# WSM DB Stale-Workspace Cleanup (v3) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Remove the stale `workspace_guid = 082b7cb0-e725-4064-8355-2495b1eb3877` row and its FK children from `/Users/roberthyatt/.claude/ironclaude-workspaces.db` with an FK-safe transactional delete, record a findings note, and enter execution_complete.

**Requirements:** docs/2026-08-16-wsm-db-cleanup-v3-requirements.md

**Architecture:** Single-transaction delete against the workspace-manager SQLite DB — FK children first, `assignments` last, `PRAGMA foreign_keys=ON` set before `BEGIN`. A pre-delete backup and per-table `changes()` equality provide the safety net and the proof. No repo code changes; the only repo write is an unstaged findings note.

**Tech Stack:** sqlite3 CLI, bash (absolute paths only).

---

## Task 1: FK-safe delete of stale workspace row + findings note

**Files:**
- Create: `docs/2026-08-16-wsm-db-cleanup-findings.md` (the only repo write; do NOT stage it)

Everything else this task touches is the external DB at `/Users/roberthyatt/.claude/ironclaude-workspaces.db` — outside the repo, not a repo file write.

**No tests required:** this is an idempotent DB-state mutation outside the repo. Verification is inline: baseline capture (Step 1) is the pre-condition oracle, `changes()` equality (Step 4) proves the delete removed exactly the discovered rows, and post-verify (Step 5) proves target-total = 0 while the negative control stays at 1.

**Constraints carried into every step:**
- Absolute paths only. No `~` / `$HOME` (the workspace-path-adapter hook blocks those tokens).
- Any STOP condition → report to Brain and hold. Recovery needs human authority; never self-authorize.
- Never touch `2d8031c7-1ba3-4722-8de5-cb8a69b70e55` (d1475's own row — the negative control).

**Step 0: Substring sweep (run FIRST, before any mutation)**

```bash
sqlite3 /Users/roberthyatt/.claude/ironclaude-workspaces.db ".dump" | grep -F '082b7cb0'
```

- If a returned line places the GUID in a row whose `workspace_guid` differs from the target (e.g. in a `worktree_path` of a different row): STOP and report to Brain.
- If zero lines: already clean — skip Steps 1–5, write the findings note in Step 6 saying "already clean", enter execution_complete.
- Otherwise: proceed to Step 1.

**Step 1: Discovery — capture per-table baseline counts**

```bash
sqlite3 -header -column /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT 'assignments' AS tbl, * FROM assignments WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877'; SELECT 'primary_checkout_owners' AS tbl, * FROM primary_checkout_owners WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877'; SELECT 'integration_locks' AS tbl, * FROM integration_locks WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877'; SELECT 'integration_records' AS tbl, * FROM integration_records WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877'; SELECT 'human_intents' AS tbl, * FROM human_intents WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877';"
```

Record the per-table row count as the baseline. Every returned row's `workspace_guid` must equal the target; if any differs, STOP.

**Step 2: Backup the DB**

```bash
cp /Users/roberthyatt/.claude/ironclaude-workspaces.db /Users/roberthyatt/.claude/ironclaude-workspaces.db.bak-d1475
```

**Step 3: Verify the backup exists and is non-empty**

```bash
ls -la /Users/roberthyatt/.claude/ironclaude-workspaces.db /Users/roberthyatt/.claude/ironclaude-workspaces.db.bak-d1475
```

Both files present, size > 0. If the backup is missing or 0 bytes, STOP.

**Step 4: FK-safe transactional delete**

```bash
sqlite3 /Users/roberthyatt/.claude/ironclaude-workspaces.db <<'SQL'
PRAGMA foreign_keys = ON;
BEGIN;
DELETE FROM primary_checkout_owners WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877';
SELECT changes();
DELETE FROM integration_locks WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877';
SELECT changes();
DELETE FROM integration_records WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877';
SELECT changes();
DELETE FROM human_intents WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877';
SELECT changes();
DELETE FROM assignments WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877';
SELECT changes();
COMMIT;
SQL
```

`PRAGMA foreign_keys=ON` must precede `BEGIN`. Each `changes()` must equal the Step 1 baseline for that table. Any SQL error → automatic rollback → report the exact error and STOP. Never retry with `foreign_keys=OFF`.

**Step 5: Post-verify**

```bash
sqlite3 /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT (SELECT COUNT(*) FROM assignments WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877') + (SELECT COUNT(*) FROM primary_checkout_owners WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877') + (SELECT COUNT(*) FROM integration_locks WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877') + (SELECT COUNT(*) FROM integration_records WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877') + (SELECT COUNT(*) FROM human_intents WHERE workspace_guid='082b7cb0-e725-4064-8355-2495b1eb3877') AS total_remaining;"
```

Expected: `0`.

```bash
sqlite3 /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT COUNT(*) FROM assignments WHERE workspace_guid='2d8031c7-1ba3-4722-8de5-cb8a69b70e55';"
```

Expected: `1` (negative control — d1475's row untouched). If either check fails, STOP.

**Step 6: Write findings note (do NOT stage it)**

Write `docs/2026-08-16-wsm-db-cleanup-findings.md` recording: the Step 0 sweep result; which tables held rows for `082b7cb0` and the exact column (`workspace_guid` in every case); the exact queries run; the per-table rows deleted (`changes()` output from Step 4); and the Step 5 results (`total_remaining=0`; d1475 own-GUID count `=1`).

Do NOT run `git add` on this file. Then enter execution_complete (the plan's single task is done).
