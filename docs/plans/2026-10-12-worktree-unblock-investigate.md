# Unblock the roleplaying-agents Worktree: Investigation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Gather the facts that decide how to land managed worktree `019f7d26-9faa-7e71-b7cf-ee2a2334b9f4` into roleplaying-agents `main` without a product change, and write a findings doc ranking unblock options A and B.

**Requirements:** `docs/plans/2026-10-12-worktree-unblock-investigate-design.md` (R1–R7)

**Architecture:** The task runs read-only `sqlite3 -readonly` queries against the workspace DB and read-only `git` commands against the repo and the worktree. Evidence files go in `/private/tmp/worktree-unblock/`, then the task writes and stages one findings doc.

**Tech Stack:** sqlite3, git, zsh.

**Execution invariants:**
- Shell state does not persist between steps. Use literal absolute paths. Where a step shows `<worktree_path>`, `<base_commit>` or `<head>`, replace it with the literal value recorded in Step 1 or Step 4 output, never a shell variable.
- Send each command's stderr to its own `.err` file. No `2>/dev/null`. Echo each exit code.
- Mutate nothing:
  - no writes to the DB;
  - no checkout, fetch, commit or reset;
  - every git command runs with `GIT_OPTIONAL_LOCKS=0` so it doesn't refresh the index.
- In the primary checkout, run status with `--untracked-files=no`. `.tmp.driveupload` has about 234k untracked files, and listing them is noise.
- `docs/` is gitignored, so stage with `git -C /Users/roberthyatt/Code/ironclaude add -f`.
- The DB path is `/Users/roberthyatt/.claude/ironclaude-workspaces.db`, which uses WAL. The default applies because `WORKSPACE_MANAGER_DB_PATH` is unset. If Step 1 returns no row, record that and stop: do not guess a different DB.

---

## Task 1: Gather the facts and write the findings (R1–R7)

No tests required: this task runs read-only queries and writes one doc.

**Files:**
- Create: `docs/reviews/2026-10-12-worktree-unblock-findings.md`

**Step 1: Assignment row (R1)**
```bash
mkdir -p /private/tmp/worktree-unblock && sqlite3 -readonly -line /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT * FROM assignments WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';" > /private/tmp/worktree-unblock/s1-assignment.txt 2> /private/tmp/worktree-unblock/s1.err; echo "s1=$?"; cat /private/tmp/worktree-unblock/s1-assignment.txt /private/tmp/worktree-unblock/s1.err
```
Expected: `s1=0` and one row whose `integration_target` is `portrait-gen-structural-sclera-gate`. Record `worktree_path`, `branch`, `base_commit`, `current_head`, `lifecycle_status` and `disposition`.

**Step 2: Related rows (R1)**
```bash
sqlite3 -readonly -header /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT 'lock',* FROM integration_locks WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4'; SELECT 'record',* FROM integration_records WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4'; SELECT 'owner',* FROM primary_checkout_owners WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4'; SELECT 'preserved',* FROM preserved_work WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4'; SELECT 'done';" > /private/tmp/worktree-unblock/s2-related.txt 2> /private/tmp/worktree-unblock/s2.err; echo "s2=$?"; cat /private/tmp/worktree-unblock/s2-related.txt /private/tmp/worktree-unblock/s2.err
```
Expected: `s2=0`, with `done` as the last line, which proves the query ran to the end. Any lock, record, owner or preserved line is recorded as found. If `preserved_work` has no `workspace_guid` column, the error appears in `s2.err`. In that case, re-run the query without that SELECT and note the deviation.

**Step 3: Other assignments on sclera-gate (R5)**
```bash
sqlite3 -readonly -header /Users/roberthyatt/.claude/ironclaude-workspaces.db "SELECT workspace_guid, worker_id, owner_session_id, lifecycle_status, worktree_path, created_at FROM assignments WHERE integration_target='portrait-gen-structural-sclera-gate' AND lifecycle_status NOT IN ('cleaned','abandoned') ORDER BY created_at; SELECT 'done';" > /private/tmp/worktree-unblock/s3-sclera.txt 2> /private/tmp/worktree-unblock/s3.err; echo "s3=$?"; cat /private/tmp/worktree-unblock/s3-sclera.txt /private/tmp/worktree-unblock/s3.err
```
Expected: `s3=0`, the final line `done`, and at least the `019f7d26…` row. Record every other row by name.

**Step 4: Worktree state (R2)**
```bash
GIT_OPTIONAL_LOCKS=0 git -C <worktree_path> rev-parse HEAD > /private/tmp/worktree-unblock/s4-head.txt 2> /private/tmp/worktree-unblock/s4a.err; echo "head=$?"; GIT_OPTIONAL_LOCKS=0 git -C <worktree_path> branch --show-current >> /private/tmp/worktree-unblock/s4-head.txt 2>> /private/tmp/worktree-unblock/s4a.err; echo "branch=$?"; GIT_OPTIONAL_LOCKS=0 git -C <worktree_path> status --porcelain=v1 --untracked-files=all > /private/tmp/worktree-unblock/s4-status.txt 2> /private/tmp/worktree-unblock/s4b.err; echo "status=$?"; GIT_OPTIONAL_LOCKS=0 git -C <worktree_path> log --oneline <base_commit>..HEAD > /private/tmp/worktree-unblock/s4-local-commits.txt 2> /private/tmp/worktree-unblock/s4c.err; echo "log=$?"; cat /private/tmp/worktree-unblock/s4-head.txt /private/tmp/worktree-unblock/s4-status.txt /private/tmp/worktree-unblock/s4-local-commits.txt /private/tmp/worktree-unblock/s4a.err /private/tmp/worktree-unblock/s4b.err /private/tmp/worktree-unblock/s4c.err
```
Expected: all four exit codes are 0. The status should list `AGENTS.md` and the untracked music research. Record the local commit list exactly as printed, which may be empty. Record whether HEAD equals `current_head` from Step 1.

**Step 5: Topology (R3)**
```bash
GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents rev-parse refs/heads/main refs/heads/portrait-gen-structural-sclera-gate > /private/tmp/worktree-unblock/s5-topo.txt 2> /private/tmp/worktree-unblock/s5.err; echo "revs=$?"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents rev-list --left-right --count refs/heads/main...refs/heads/portrait-gen-structural-sclera-gate >> /private/tmp/worktree-unblock/s5-topo.txt 2>> /private/tmp/worktree-unblock/s5.err; echo "lr=$?"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents merge-base refs/heads/main <head> >> /private/tmp/worktree-unblock/s5-topo.txt 2>> /private/tmp/worktree-unblock/s5.err; echo "mb=$?"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents merge-base --is-ancestor <base_commit> refs/heads/main; echo "base-in-main=$?"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents merge-base --is-ancestor <base_commit> refs/heads/portrait-gen-structural-sclera-gate; echo "base-in-sclera=$?"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents log --oneline refs/heads/main..refs/heads/portrait-gen-structural-sclera-gate > /private/tmp/worktree-unblock/s5-sclera-only.txt 2>> /private/tmp/worktree-unblock/s5.err; echo "sclera-only=$?"; wc -l /private/tmp/worktree-unblock/s5-sclera-only.txt; cat /private/tmp/worktree-unblock/s5-topo.txt /private/tmp/worktree-unblock/s5.err
```
Expected: `revs`, `lr`, `mb` and `sclera-only` exit 0. Each `base-in-*` is 0 (ancestor) or 1 (not an ancestor). Any other value is an error and is recorded. Record the two SHAs, the left/right counts, the merge-base compared with `base_commit`, and the number of sclera-only commits.

**Step 6: Primary checkout (R4)**
```bash
GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents branch --show-current > /private/tmp/worktree-unblock/s6-primary.txt 2> /private/tmp/worktree-unblock/s6.err; echo "branch=$?"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents status --porcelain=v1 --untracked-files=no >> /private/tmp/worktree-unblock/s6-primary.txt 2>> /private/tmp/worktree-unblock/s6.err; echo "status=$?"; cat /private/tmp/worktree-unblock/s6-primary.txt /private/tmp/worktree-unblock/s6.err
```
Expected: both exit 0. Record the current branch and the tracked dirty paths, which may be none.

**Step 6b: Primary untracked overlap probe (R4)**

This step checks only the paths the worktree changes. Option A's overlap check (`integration.ts:600-616`, via `git.ts:558-563`) also counts untracked primary paths, and `--untracked-files=no` in Step 6 can't see those.
```bash
GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents ls-files --others --exclude-standard -- <each path listed in s4-status.txt, literal and quoted> > /private/tmp/worktree-unblock/s6b-primary-untracked.txt 2> /private/tmp/worktree-unblock/s6b.err; echo "probe=$?"; wc -l /private/tmp/worktree-unblock/s6b-primary-untracked.txt; cat /private/tmp/worktree-unblock/s6b-primary-untracked.txt /private/tmp/worktree-unblock/s6b.err
```
Expected: `probe=0`. Record every listed path. Each one is an untracked primary path that would overlap Option A's carry-forward. An empty list together with `probe=0` means no overlap.

**Step 7: Write the findings (R6, R7)**

Create `docs/reviews/2026-10-12-worktree-unblock-findings.md` with these sections.

Facts both options rest on, from source:
- `/commit` creates the commit and stops; it does not integrate (`integration.ts:1204-1211`).
- `/commit` does not include untracked files.
- `/reconcile` refuses any dirty or untracked worktree (`:976-978`).

1. **Facts.** Cite each file from Steps 1–6b.
2. **Option A:** `/commit` with every wanted change staged, then `/reconcile` into sclera-gate, then the operator cherry-picks onto `main`.
   - Give the exact operator steps.
   - Viability conditions:
     - the untracked music research is staged into the `/commit`, or removed before `/reconcile`; the operator decides which;
     - Step 6 tracked-dirty paths and Step 6b untracked paths don't overlap the paths the commit changes. The primary is on sclera-gate, so the overlap check and carry-forward apply (`:600-616`, `:919-929`);
     - the cherry-pick needs `main` checked out in the primary. Whether `main` is safe to check out under the DB-tracking constraint is written as "not measured (operator to confirm)".
   - Risks: cherry-pick conflicts, and sclera-gate gaining a commit.
3. **Option B**, in this order:
   1. Retarget the one row to `main`, recording the old value. Give the exact SQL.
   2. Run `/commit` with every wanted change staged.
   3. Remove or commit the untracked music research.
   4. Run `sync_worktree_to_target`. On a clean tree it runs `rebase --onto main <base_commit>` and resets `base_commit` to the `main` tip (`:549-568`), so the `:897` merge-base check passes. A rebase conflict aborts and restores HEAD (`:554-563`).
   5. Run `/reconcile`. The primary is off `main`, so landing is a pure ref advance and the overlap check is a no-op.

   Conditions:
   - Step 2 shows no lock or record rows;
   - the worktree is fully clean, including untracked files, at sync time (`git.ts:175-176`);
   - no rebase conflict between the local commits and `main`.

   Note that sync-before-commit would fail on the dirty tree unless HEAD is an ancestor of `main` (`:541-551`). Step 5 `base-in-main` records which case applies.
4. **Ranking** with a recommendation, based on the measured facts.
5. Notes:
   - the reconcile and sync tools are gated to the provider-root session, so they run in the roleplaying-agents Codex session;
   - Option B needs an explicit per-action go;
   - any other sclera-gate assignments from Step 3 have the same defect;
   - the product fix is the separate loop.

Don't propose copying files out of the worktree by hand. Any value a step failed to measure is written as "not measured", never guessed.

**Step 8: Stage**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-12-worktree-unblock-findings.md docs/plans/2026-10-12-worktree-unblock-investigate-design.md docs/plans/2026-10-12-worktree-unblock-investigate.md docs/plans/2026-10-12-worktree-unblock-investigate.plan.json
```
Expected: no output.
