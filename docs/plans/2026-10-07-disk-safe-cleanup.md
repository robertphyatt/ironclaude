# Disk Safe Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Delete exactly the approved crash-leftover files, about 14 GB in total, and prove the repo is still healthy with before/after evidence. The files are:
- `tmp_pack_*` and `index.stash.*` in roleplaying-agents;
- the stale renamed `index.lock`;
- two named `/private/tmp` review JSONs.

**Requirements:** `docs/plans/2026-10-07-disk-safe-cleanup-design.md` (`## Requirements`, D1–D6)

**Design:** `docs/plans/2026-10-07-disk-safe-cleanup-design.md`

**Loop:** light. One task, run inline because it is destructive. No product code changes.

**Architecture:**
1. Enumerate the exact files and check them against strict patterns.
2. Before deleting anything, prove that no process has any listed file open, and that no listed file was modified in the last 10 minutes.
   - The known snapshot writer never takes `index.lock`, so these two checks are the real precondition.
   - The git-process listing is informational.
3. Record `df`, `count-objects -v` and a lock-free `status`.
4. Delete only the files on the list.
5. Record `df` and `count-objects` again, and prove nothing remains.
6. Run a read-only `fsck` in the background, then a separate status diff.

**Tech Stack:** bash and zsh, `find`, `xargs`, `lsof`, `pgrep` (list only), `df`, read-only git (`GIT_OPTIONAL_LOCKS=0`, `count-objects`, `fsck --connectivity-only`).

**Deviation from the design.** The design's D1 lists `pgrep` with a cwd check as the precondition. The plan strengthens that with an open-handle check and a freshness check:
- `pgrep` misses `git-lfs` and the Codex linked worktrees under `~/.codex/worktrees/{a535,8735,8859}/roleplaying-agents`, which share the repo's `objects/pack`.
- The `stash` snapshot writer never takes `index.lock`, so checking for a lock cannot detect it.

## Execution invariants

- **Shell use:**
  - Shell state does not persist between steps, so every path is absolute.
  - zsh `nomatch` is on, so every pattern is a quoted `find -name`.
- **Evidence location:** evidence goes to `<SP>/disk-cleanup/`, where `<SP>` = `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad`.
- **Error output:** no evidence command uses `2>/dev/null`.
- **Deletion limits:**
  - Delete only the files listed in `<SP>/disk-cleanup/delete.list0`.
  - No `rm -rf`, no directory deletes, no unquoted globs.
- **Processes:** kill nothing. `pgrep` and `lsof` only list.
- **Git:**
  - Run no git command that writes.
  - `status` runs with `GIT_OPTIONAL_LOCKS=0`. For a racily-clean LFS path it may refill the `.git/lfs/objects` cache; that is not repo state.
- **STOP conditions.** Delete nothing and write the note if any of these holds:
  - Step 2 reports a non-matching line, or the two counts differ.
  - Step 3 lists an open handle, finds a file under 10 minutes old, finds a git process whose cwd is under a roleplaying-agents path, or finds a live `index.lock`.
- **fsck scope.** `fsck` never reads `tmp_pack_*` or `index.stash.*`, so any error it reports already existed before this cleanup. The note records such errors without attributing them to the cleanup.
- **Expected values.** Where a count or size is recorded, the measured value is what counts. Diagnosis-time numbers are context only.

---

## Task 1: Verify, delete, and prove health

**Files:**
- Create: `docs/reviews/2026-10-07-disk-safe-cleanup.md`
- Stage, not edited: `docs/plans/2026-10-07-disk-safe-cleanup-design.md`, `docs/plans/2026-10-07-disk-safe-cleanup.md`, `docs/plans/2026-10-07-disk-safe-cleanup.plan.json`

**No tests required.** This is an operational cleanup, verified by the before/after evidence, `count-objects` and `fsck`.

**Step 1: enumerate**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; R=/Users/roberthyatt/Code/roleplaying-agents/.git; mkdir -p "$D"; { find "$R/objects/pack" -maxdepth 1 -type f -name 'tmp_pack_*'; find "$R" -maxdepth 1 -type f -name 'index.stash.*'; find "$R" -maxdepth 1 -type f -name 'index.lock.inkarnate-stale-20261002'; find /private/tmp -maxdepth 1 -type f \( -name 'inventory_preservation_plan_review.json' -o -name 'inventory_preservation_plan_review_fixed.json' \); } > "$D/delete.txt"; tr '\n' '\0' < "$D/delete.txt" > "$D/delete.list0"; xargs -0 ls -l < "$D/delete.list0" > "$D/manifest.txt"; echo "ls_rc=$?"; awk '{s+=$5} END {printf "%.2f GB\n", s/1e9}' "$D/manifest.txt"
```

Expected: `ls_rc=0`. Record the total in GB.

**Step 2: check the list against strict patterns, and check manifest integrity**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; grep -v -E '^/Users/roberthyatt/Code/roleplaying-agents/\.git/objects/pack/tmp_pack_[A-Za-z0-9]{6}$|^/Users/roberthyatt/Code/roleplaying-agents/\.git/index\.stash\.[0-9]+$|^/Users/roberthyatt/Code/roleplaying-agents/\.git/index\.lock\.inkarnate-stale-20261002$|^/private/tmp/inventory_preservation_plan_review(_fixed)?\.json$' "$D/delete.txt"; echo "nonmatch_rc=$?"; wc -l "$D/delete.txt" "$D/manifest.txt"
```

Expected:
- No non-matching lines printed, and `nonmatch_rc=1`.
- Both `wc` counts equal.

Otherwise STOP.

**Step 3: check the precondition (D1, strengthened)**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; xargs -0 lsof -- < "$D/delete.list0"; echo "lsof_rc=$?"; find /Users/roberthyatt/Code/roleplaying-agents/.git/objects/pack -maxdepth 1 -type f -name 'tmp_pack_*' -mmin -10; find /Users/roberthyatt/Code/roleplaying-agents/.git -maxdepth 1 -type f -name 'index.stash.*' -mmin -10; echo "fresh_check_done"; for p in $(pgrep git); do echo "pid=$p name=$(ps -p "$p" -o comm=) cwd=$(lsof -a -p "$p" -d cwd -Fn | grep '^n')"; done; echo "git_procs_done"; ls -la /Users/roberthyatt/Code/roleplaying-agents/.git/index.lock; echo "lock_ls_rc=$?"
```

Expected:
- `lsof` prints no lines, and `lsof_rc=1`.
- Neither `find -mmin -10` prints anything.
- No git process has a cwd under `/Users/roberthyatt/Code/roleplaying-agents`, `/Users/roberthyatt/.codex/worktrees/a535/roleplaying-agents`, `/Users/roberthyatt/.codex/worktrees/8735/roleplaying-agents` or `/Users/roberthyatt/.codex/worktrees/8859/roleplaying-agents`.
- `ls` reports `No such file or directory` with `lock_ls_rc=1`.

Otherwise STOP.

**Step 4: record the before-state**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; df -h /System/Volumes/Data > "$D/df-before.txt"; git -C /Users/roberthyatt/Code/roleplaying-agents count-objects -v > "$D/count-before.txt"; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents status --porcelain=v1 -uno > "$D/status-before.txt"; echo "status_rc=$?"; cat "$D/df-before.txt" "$D/count-before.txt"
```

Expected: `status_rc=0`. Record the `df` output and `garbage`/`size-garbage`.

**Step 5: delete exactly the listed files (D2)**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; xargs -0 rm -f -- < "$D/delete.list0"; echo "rm_rc=$?"
```

Expected: `rm_rc=0`.

**Step 6: record the after-state and check that nothing remains**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; df -h /System/Volumes/Data > "$D/df-after.txt"; git -C /Users/roberthyatt/Code/roleplaying-agents count-objects -v > "$D/count-after.txt"; cat "$D/df-before.txt" "$D/df-after.txt" "$D/count-after.txt"; xargs -0 ls -l < "$D/delete.list0"; echo "remaining_check_done"
```

Expected:
- More free space after than before.
- `garbage` is lower than before.
- Every `ls -l` line reports `No such file or directory`.

**Step 7: run the read-only fsck** as its own background Bash run (`run_in_background`, timeout 1800000), then read the output when it completes:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents fsck --connectivity-only --no-dangling > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup/fsck.txt 2>&1; echo "fsck_rc=$?"
```

Expected: record `fsck_rc` and its output. By construction, any error already existed before the cleanup.

**Step 8: compare status before and after**

```bash
D=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents status --porcelain=v1 -uno > "$D/status-after.txt"; diff "$D/status-before.txt" "$D/status-after.txt"; echo "status_diff_rc=$?"
```

Expected: `status_diff_rc=0`.

**Step 9: write `docs/reviews/2026-10-07-disk-safe-cleanup.md`.** It covers:
- the results of the pattern and precondition checks;
- the file count, and GB per pattern;
- `df` before and after, and the space freed;
- `count-objects` before and after;
- the result of the remaining-files check;
- the `fsck` result, with any errors labelled as already existing;
- the status diff.

It also records that the snapshot writer was live on Oct 6 at 22:47, so these leftovers will come back until that writer is found. It lists these items for the operator to decide:
- untracking `asset_catalog.db` and running `git gc`;
- `probe_out` snapshots;
- duplicate gemma GGUFs;
- the episodic-memory fix loop;
- finding the snapshot writer.

**Step 10: stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-07-disk-safe-cleanup.md docs/plans/2026-10-07-disk-safe-cleanup-design.md docs/plans/2026-10-07-disk-safe-cleanup.md docs/plans/2026-10-07-disk-safe-cleanup.plan.json
```

Expected: no output, exit 0.
