# Disk Safe Cleanup Design

> **Created:** 2026-10-07
> **Status:** Design Complete
> **Loop:** light. Deletes a fixed, enumerated set of crash-leftover files. Changes no product code and runs no git command that writes.

## Requirements

**Operator directive:** "Safe cleanup loop (Rec.)". The option text was:

> Small PM loop: verify no git process in roleplaying-agents (exact PIDs only), list then delete exactly the tmp_pack_*, index.stash.*, stale index.lock, and the two /private/tmp review JSONs; report freed space (df). Nothing else touched.

This follows the read-only diagnosis of 2026-10-07. Its findings are recorded in memory under `project_disk_full_2026_10_07`.

- **D1. Precondition.** No git process may have `/Users/roberthyatt/Code/roleplaying-agents` as its working directory or argument.
  - **Detect:** list git processes with `pgrep -fl git`, which lists only and never kills. For each one, check its cwd with `lsof -a -p <pid> -d cwd -Fn`.
  - **If any is found:** stop. Delete nothing and report it.
  - **Also check:** no live `.git/index.lock` may exist.
- **D2. Delete exactly these files, and nothing else:**
  - `/Users/roberthyatt/Code/roleplaying-agents/.git/objects/pack/tmp_pack_*`. These are unreferenced temp packs. Git's own `gc` removes them as garbage.
  - `/Users/roberthyatt/Code/roleplaying-agents/.git/index.stash.*`. These are temporary index files left by snapshot processes that died.
  - `/Users/roberthyatt/Code/roleplaying-agents/.git/index.lock.inkarnate-stale-20261002`. A renamed stale lock, 0 bytes.
  - `/private/tmp/inventory_preservation_plan_review.json` and `/private/tmp/inventory_preservation_plan_review_fixed.json`. 547 MB each, dated Oct 4.
- **D3. Record evidence before deleting:**
  - `df -h /System/Volumes/Data`;
  - the full list of matched files, with their sizes, written to a scratch log.

  After deleting, record `df -h` again, and `ls` each pattern to prove it no longer matches.
- **D4. Prove repo health after deletion:**
  - run `git -C /Users/roberthyatt/Code/roleplaying-agents fsck --connectivity-only --no-dangling`, which is read-only;
  - run `git status --porcelain=v1 -uno`, and compare it with a `status` taken before deleting;
  - any new error is a finding.
- **D5. Not in scope:**
  - `git gc`;
  - untracking `asset_catalog.db`;
  - `probe_out`, models, caches, and episodic memory.

  Each of these needs its own operator decision or fix loop.
- **D6. Output:** write `docs/reviews/2026-10-07-disk-safe-cleanup.md`. It records what was deleted, how much space was freed, and the results of the health check.

## Approach

One orchestrator-run task. It is destructive, so it runs inline rather than in a subagent:

1. Check the precondition.
2. Enumerate the matched files into the log.
3. Delete each file by its explicit path from the enumerated list. No `rm -rf` and no unquoted glob.
4. Run the after-checks.
5. Write the note.

## Final gate

None beyond the task-boundary review. The deletion set was approved explicitly, and the before/after evidence plus `fsck` are the verification.
