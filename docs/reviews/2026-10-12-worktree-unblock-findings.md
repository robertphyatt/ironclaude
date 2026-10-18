# Unblocking worktree 019f7d26-9faa-7e71-b7cf-ee2a2334b9f4: findings

Date: 2026-10-12. Read-only investigation. Nothing was written to the DB, and no checkout, fetch, commit, reset or rebase ran. Evidence files are in `/private/tmp/worktree-unblock/`.

## Facts both options rest on (from source, per the plan; not re-verified here)

- `/commit` creates the commit and stops; it does not integrate (`integration.ts:1204-1211`).
- `/commit` does not include untracked files.
- `/reconcile` refuses any dirty or untracked worktree (`integration.ts:976-978`).

## 1. Facts (measured)

Step 1 (`s1-assignment.txt`, `s1=0`, one row):
- `worktree_path` = `/Users/roberthyatt/Code/roleplaying-agents/.ironclaude/worktrees/019f7d26-9faa-7e71-b7cf-ee2a2334b9f4`
- `branch` = `ironclaude/019f7d26-9faa-7e71-b7cf-ee2a2334b9f4`
- `base_commit` = `4864ad02155048b92b3de01de6379a4ade6ae589`
- `current_head` (DB) = `4864ad02155048b92b3de01de6379a4ade6ae589`
- `lifecycle_status` = `active`; `disposition` = empty; `integrated_commit` and `recovery_ref` = empty
- `integration_target` = `portrait-gen-structural-sclera-gate`
- `created_at` 2026-10-10 18:56:49; `updated_at` 2026-10-10 18:57:14

Step 2 (`s2-related.txt`, `s2=0`): the file contains only the `'done'` header line and `done`. There are no lock, record, owner or preserved_work rows for this workspace_guid. The `preserved_work` SELECT ran without error (`s2.err` empty).

Step 3 (`s3-sclera.txt`, `s3=0`, final line `done`): five non-cleaned, non-abandoned assignments target `portrait-gen-structural-sclera-gate`:
- `3106ccc6-5690-4784-8021-6f683cb87409` (worker `d1501-r4-blind-review`, active, created 2026-10-06 20:55:45)
- `019f7d26-9faa-7e71-b7cf-ee2a2334b9f4` (this one)
- `7fe1774e-1aca-4830-8a6d-961bf43922c1` (worker `d1501-fix-loop-5-recovery-2`, active, 2026-10-12 14:53:54)
- `47128b03-3459-47e1-8e4d-dcaf65f2d289` (worker `d1501-r6-cleanroom-review`, active, 2026-10-12 19:39:50)
- `709df37b-a1da-46b9-9f82-f73e6d765131` (worker `d1501-fix-loop-6`, active, 2026-10-12 21:16:15)

Step 4 (`head=0 branch=0 status=0 log=0`):
- Actual worktree HEAD = `69e6d9aaa3a997dcc1987ed1dd5b2b5856cec0d9`, branch `ironclaude/019f7d26-9faa-7e71-b7cf-ee2a2334b9f4`.
- HEAD does NOT equal the DB `current_head` (`4864ad02...`). The DB is stale by one commit.
- `s4-local-commits.txt`, exactly: `69e6d9aaa feat: integrate hybrid music retrieval with foreground-safe DJ selection`. That is the only local commit. It changes 57 files (`s7-localfiles.txt`).
- Status is dirty: ` M AGENTS.md` (+28 lines vs HEAD) plus untracked music research and plans. That is `docs/plans/2026-10-1{0,1}-music-*`, `docs/research/2026-10-11-music-upgrade-runtime-readiness.md`, `docs/reviews/2026-10-10-music-calibration-goal-alignment-findings.md`, and 20 files under `research/music-*-2026-10-1{0,1}/`. The full list is in `s4-status.txt`.

Step 5 (`revs=0 lr=0 sclera-only=0`; 73 lines in `s5-sclera-only.txt`):
- `refs/heads/main` = `3b61f9721e5416e84a6868bdb4472be27bc1d369`
- `refs/heads/portrait-gen-structural-sclera-gate` = `029c87be917b225cf8d2ddb86145fed6a0f8a9ea`
- left/right count (main...sclera) = `7	73`. Main has 7 commits sclera lacks; sclera has 73 commits main lacks.
- sclera-only commits: 73.
- The plan's `merge-base` commands (`mb`, `base-in-main`, `base-in-sclera`) were blocked by the `professional-mode-guard` hook, which matches the text "merge" and reports "GIT COMMIT/PUSH NOT ALLOWED". Their exit codes are not measured, and the merge-base SHA is not measured. I did not work around the guard to run them.
- Deviation: equivalent read-only `rev-list` queries (`s5x.err` empty, all rc=0):
  - `rev-list -n1 <base> ^main` printed `4864ad02155048b92b3de01de6379a4ade6ae589`. Base is NOT an ancestor of main.
  - `rev-list -n1 <base> ^sclera` printed nothing. Base IS an ancestor of sclera.
  - base...main left/right = `62	7`.
  - base...sclera = `0	11`. Sclera is 11 commits ahead of base.
  - main...HEAD(69e6d9aaa) = `7	63`. HEAD...sclera orientation `sclera...HEAD` = `11	1`.
- Main's 7 commits (`s8-main-only.txt`) touch 33 files. Three overlap files changed by `69e6d9aaa`: `godot/artificial-adventures/services/ContextCoordinator.gd`, `.../tools/model_export/assemble_model_tree.py` and `.../tools/model_export/test_assemble_model_tree.py`. Whether hunks actually conflict is not measured.

Step 6 (`branch=0 status=0`): the primary checkout is on `portrait-gen-structural-sclera-gate`, not `main`. It has 120 tracked dirty paths (`s6-primary.txt`). Two overlap the local commit's changed files:
- `godot/artificial-adventures/data/song_mapping.json` (` M`, unstaged)
- `godot/artificial-adventures/services/GMAgent.gd` (`M `, staged)

`AGENTS.md` is also ` M` in the primary, and the worktree modifies `AGENTS.md` too. It overlaps if it is staged into the `/commit`.

Step 6b (`probe=0`, 0 lines in `s6b-primary-untracked.txt`): none of the worktree's changed or untracked paths exists as an untracked file in the primary. There is no untracked overlap. This probe covered the 66 paths from `s4-status.txt` (`AGENTS.md` plus the untracked paths), not the 57 paths of the local commit. Overlap with the local commit's paths was measured separately above, for tracked dirty paths only.

## 2. Option A: commit, reconcile into sclera-gate, cherry-pick onto main

Operator steps:
1. In the worktree session, stage every wanted change. This includes the music research if it is to be kept, and `AGENTS.md` if wanted. Run `/commit`.
2. If any untracked music research is left out of the commit, remove it so the tree is clean. This is the operator's decision.
3. Run `/reconcile`. This lands the worktree HEAD into `portrait-gen-structural-sclera-gate` and keeps the worktree.
4. Check out `main` in the primary and cherry-pick the resulting commit(s) onto it.

Viability conditions:
- The untracked music research is staged or removed. Without that, `/reconcile` refuses (`:976-978`).
- The primary tracked-dirty set does not overlap the paths the commit changes. Measured: it overlaps on `song_mapping.json` and `GMAgent.gd`, and on `AGENTS.md` if it is committed. The primary is on sclera-gate, so the overlap check and carry-forward apply (`:600-616`, `:919-929`), and these two or three paths are affected. Step 6b found no untracked overlap.
- The cherry-pick needs `main` checked out in the primary. The primary has 120 tracked dirty paths, so a checkout of `main` is high risk. Whether `main` is safe to check out under the DB-tracking constraint: not measured (operator to confirm).

Risks:
- Cherry-pick conflicts. The 3 overlapping files from Step 5 are candidates; actual hunk conflicts are not measured.
- sclera-gate gains a commit that does not belong on it, and that commit lands in the lineage the 4 other sclera assignments use.
- The commit carries the 57 files of `69e6d9aaa` plus anything staged now. Those 57 files include the large `music_runtime_v1` binaries.

## 3. Option B: retarget this one row to main, sync, reconcile

Order:
1. Retarget the one row to `main`, recording the old value. This SQL is for the operator only and was NOT run:
   ```sql
   SELECT workspace_guid, integration_target FROM assignments WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';
   -- expected old value: portrait-gen-structural-sclera-gate
   UPDATE assignments SET integration_target='main' WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';
   SELECT workspace_guid, integration_target FROM assignments WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';
   ```
   DB: `/Users/roberthyatt/.claude/ironclaude-workspaces.db` (WAL). The plain `main` form is correct: `integration.ts:213-215` prefixes a bare name with `refs/heads/`, and this row already stores the bare `portrait-gen-structural-sclera-gate`. The WHERE clause limits the change to one row.
2. Run `/commit` with every wanted change staged (`AGENTS.md` included only if wanted).
3. Remove or commit the untracked music research, so the tree is clean including untracked files.
4. Run `sync_worktree_to_target`. On a clean tree it runs `rebase --onto main 4864ad02155048b92b3de01de6379a4ade6ae589` and resets `base_commit` to the `main` tip (`:549-568`), so the `:897` merge-base check passes. Only the commits in `base..HEAD` are replayed: the one local commit `69e6d9aaa` plus the new `/commit`. The 62 base-only commits that are in sclera's history are not replayed. A rebase conflict aborts and restores HEAD (`:554-563`).
5. Run `/reconcile`. The primary is off `main`, so landing is a pure ref advance and the overlap check is a no-op.

Conditions:
- Step 2 shows no lock or record rows: met (none found).
- The worktree is fully clean, including untracked files, at sync time (`git.ts:175-176`): not met now; steps 2 and 3 make it so.
- No rebase conflict between the local commits and `main`: not measured. The 3 shared files (Step 5) are the risk.

Sync-before-commit would fail on the dirty tree unless HEAD is an ancestor of `main` (`:541-551`). Measured: base `4864ad02` is not an ancestor of main (via `rev-list`), so that case does not apply. The `/commit` must come first, as ordered above.

## 4. Ranking and recommendation

1. Option B (recommended). Reasons, all measured:
   - It replays only the 1 local commit onto `main`.
   - It does not touch the primary's 120 dirty tracked paths. The `:600-616` overlap check is a no-op because the primary is off `main`.
   - It does not add a commit to sclera-gate.
   - No lock or record rows block it.
   - Residual risk: a rebase conflict in up to 3 shared files, which aborts cleanly, and AGENTS.md semantics (see below).
2. Option A. Measured downsides:
   - `GMAgent.gd` and `song_mapping.json` (and `AGENTS.md`) are dirty in the primary and also changed by the commit, so the overlap check and carry-forward are in play.
   - A `main` checkout is needed in a primary with 120 dirty tracked paths, and its safety is not measured.
   - sclera-gate gets a commit that was meant for main.

Lineage-dependency risk (both options, not measured): `main...HEAD` = `7	63`. The worktree HEAD carries 63 commits `main` lacks: the 1 local music commit plus 62 sclera-lineage commits from `base_commit`'s history. Option B replays only the music commit onto `main`. If that commit depends on code introduced by those 62 commits, it may rebase cleanly and still be broken on `main`. Option A's cherry-pick has the same exposure. Before landing, the operator should either confirm the music work stands alone, or decide that the sclera lineage itself should reach `main` first. If it should, that is a separate decision about the whole branch, not this worktree.

AGENTS.md note: the worktree's +28 lines were written against the sclera-lineage `AGENTS.md`. Landed on `main` they may conflict, or may not match main's text. This is not measured. The operator should decide whether `AGENTS.md` belongs in the commit.

## 5. Notes

- The reconcile and sync tools are gated to the provider-root session, so they run in the roleplaying-agents Codex session.
- Option B needs an explicit per-action go. The SQL above must not be run without one.
- The other four sclera-gate assignments from Step 3 have the same defect: their integration target is a feature branch, not `main`. I did not examine them (their worktrees, bases and heads are not measured).
- The DB `current_head` for this workspace is stale (`4864ad02` against the actual `69e6d9aaa`). `/commit` binds only the branch name (`integration.ts:456-461`), takes its parent from the live worktree, and then sets `current_head` (`:1209`). Sync reads `base_commit`, not `current_head`. So neither recipe depends on the stale value. Whether any other reader of `current_head` matters is not measured.
- The four other sclera-gate assignments are Commander `d1501-*` workers. Whether sclera-gate is the correct target for that portrait work (as opposed to a defect) is not measured. This recipe changes only `019f7d26…`.
- The `professional-mode-guard` hook blocked read-only commands whose text contained "merge" or "commit" (`merge-base`, `--no-commit-id`, and a file named `...commit...`). That is a false positive in the hook; it is a candidate for the separate product-fix loop.
- The product fix (retargeting, stale `current_head`, and the guard false positive) is the separate loop.

## 8. Retarget applied (2026-10-13)

The operator gave an explicit go on 2026-10-13: "yes do the sql retarget". The plan was `docs/plans/2026-10-13-worktree-retarget-main.md`, and a same-tier blind review rated it SOLID.

- **Before** (`/private/tmp/worktree-unblock/r1-before.txt`): `lifecycle_status = active`, `integration_target = portrait-gen-structural-sclera-gate`, `updated_at = 2026-10-10 18:57:14`.
- **Update** (`r2-update.txt`): a guarded `UPDATE` (`WHERE workspace_guid=… AND integration_target='portrait-gen-structural-sclera-gate' AND lifecycle_status='active'`). Result: `update=0`, `changes=1`.
- **After** (`r3-after.txt`): `after=0`. The diff against the before-snapshot changed exactly one line:
  ```
  10c10
  <  integration_target = portrait-gen-structural-sclera-gate
  ---
  >  integration_target = main
  ```
  Every other column is unchanged, including `updated_at`. No other row or table was touched.

The remaining Option B steps all run in the roleplaying-agents Codex session:
1. `get_workspace_status`, to confirm the tool now reports target `main`. This is the first check through the live tool.
2. `/commit`, with the `AGENTS.md` change included (the operator decided this on 2026-10-13).
3. Commit or remove the untracked music research files, so the worktree is fully clean.
4. `sync_worktree_to_target`. It rebases the local commits onto `main`. If there is a conflict, it aborts and restores HEAD.
5. Run the music tests and a game smoke run in the worktree. If anything that exists only on sclera-gate is missing, STOP: nothing has reached `main` yet.
6. `/reconcile`. The primary is off `main`, so this is a ref-only advance.
