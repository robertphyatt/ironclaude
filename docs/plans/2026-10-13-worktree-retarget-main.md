# Retarget the music worktree to main: implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Change one row in the workspace-manager DB so that the roleplaying-agents music worktree's integration target is `main`, and record the before and after values.

**Requirements:** `docs/plans/2026-10-13-worktree-retarget-main-design.md` (the `## Requirements` section, R1–R6)

**Architecture:** A single guarded `UPDATE`, run with the `sqlite3` CLI against `~/.claude/ironclaude-workspaces.db`. The database is in WAL mode and the CLI waits up to `.timeout 10000` for a lock. The full row is snapshotted before and after the update and the two snapshots are diffed.

**Tech Stack:** sqlite3 CLI.

## Execution invariants

- Shell state does not persist between steps. Every step uses literal absolute paths and no shell variables.
- Every evidence command sends stderr to its own `.err` file. None uses `2>/dev/null`. Every step echoes its exit code.
- The only write is the Step 2 `UPDATE`. No other row, table or file in the DB is touched.
- `docs/` is gitignored, so staging uses `git -C /Users/roberthyatt/Code/ironclaude add -f`.
- If Step 2 prints a `changes()` value other than `1`, stop and report it. Do not widen the `WHERE` clause.

---

## Task 1: Guarded retarget and record

No tests are required: this is a one-row operational data change, not code. The before and after snapshots, `changes()`, and the diff are the verification.

**Files:**
- Modify: `docs/reviews/2026-10-12-worktree-unblock-findings.md` (append §8)
- Plan artifacts: `docs/plans/2026-10-13-worktree-retarget-main-design.md`, `docs/plans/2026-10-13-worktree-retarget-main.md`, `docs/plans/2026-10-13-worktree-retarget-main.plan.json`

**Step 1: Snapshot the row before the change**

```bash
sqlite3 -readonly -line /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT * FROM assignments WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';" > /private/tmp/worktree-unblock/r1-before.txt 2> /private/tmp/worktree-unblock/r1-before.err; echo "before=$?"; cat /private/tmp/worktree-unblock/r1-before.txt /private/tmp/worktree-unblock/r1-before.err
```

Expected:
- `before=0`;
- exactly one row;
- `integration_target = portrait-gen-structural-sclera-gate` and `lifecycle_status = active` (both measured in the 2026-10-12 findings).

If either value differs, stop and report it.

**Step 2: Run the guarded update**

```bash
sqlite3 -cmd ".timeout 10000" /Users/roberthyatt/.claude/ironclaude-workspaces.db "UPDATE assignments SET integration_target='main' WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4' AND integration_target='portrait-gen-structural-sclera-gate' AND lifecycle_status='active'; SELECT 'changes=' || changes();" > /private/tmp/worktree-unblock/r2-update.txt 2> /private/tmp/worktree-unblock/r2-update.err; echo "update=$?"; cat /private/tmp/worktree-unblock/r2-update.txt /private/tmp/worktree-unblock/r2-update.err
```

Expected: `update=0` and `changes=1`.

If the output is `changes=0`, the row drifted: stop and report it. If the database is busy, stop and report the stderr.

**Step 3: Snapshot the row after the change and diff**

```bash
sqlite3 -readonly -line /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT * FROM assignments WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';" > /private/tmp/worktree-unblock/r3-after.txt 2> /private/tmp/worktree-unblock/r3-after.err; echo "after=$?"; diff /private/tmp/worktree-unblock/r1-before.txt /private/tmp/worktree-unblock/r3-after.txt; echo "diff=$?"; cat /private/tmp/worktree-unblock/r3-after.err
```

Expected:
- `after=0`;
- `diff=1`;
- the diff shows exactly one changed line, `integration_target` going from `portrait-gen-structural-sclera-gate` to `main`.

Any other changed line is a failure. Report it.

**Step 4: Append §8 to the findings doc**

Append §8, "Retarget applied (2026-10-13)", to `docs/reviews/2026-10-12-worktree-unblock-findings.md`. It must record:
- the operator's go;
- the before and after `integration_target` values, quoted from r1 and r3;
- the `changes=` line from r2;
- the diff output.

It must also list the remaining Option B steps, which run in the roleplaying-agents Codex session:
1. `/commit` with `AGENTS.md` included (operator decision, 2026-10-13);
2. commit or remove the untracked music research;
3. `sync_worktree_to_target`;
4. run the music tests and a game smoke run in the worktree, and stop if anything is missing from sclera-gate;
5. `/reconcile`.

**Step 5: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-12-worktree-unblock-findings.md docs/plans/2026-10-13-worktree-retarget-main-design.md docs/plans/2026-10-13-worktree-retarget-main.md docs/plans/2026-10-13-worktree-retarget-main.plan.json
```

Expected: no output.
