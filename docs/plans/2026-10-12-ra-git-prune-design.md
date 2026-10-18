# roleplaying-agents Git Prune Design

> **Created:** 2026-10-12
> **Status:** Design Complete

Loop: full — destructive git object pruning, ref deletion and an LFS prune in a live multi-user repo.

## Requirements

The operator chose "A: Prune + refs + LFS (Rec.)" from `docs/reviews/2026-10-11-ra-git-history-findings.md` and said "yes please" to writing the plan. The steps: mirror-back up all refs, delete the 2 broken Codex refs and the old turn-diff refs that pin the catalog blob, show the dangling-commit list for review, stop all repo users, run gc with prune, run `git lfs prune`, about 100 GB back, no push. Standing rules: never push; never pattern-kill; operators never fiddle with worktrees; no history rewrite.

Repo: `/Users/roberthyatt/Code/roleplaying-agents`. `.git` is 113 GB: 88 GiB in 500 packs, 16.9 GiB loose, 6.8 GB LFS. Reachable blobs total only 2.43 GB.

- **P1. Snapshot and backup (no user stop needed).**
  - Record every ref and its value (`for-each-ref`), the stash count (6), `worktree list`, `count-objects` and `du`.
  - Pin `stash@{1..5}` as `refs/stash-keep/1..5`, so the mirror carries them and no reflog expiry can lose them.
  - **Remove the 2 broken refs before the mirror** (operator chose A on 2026-10-12, after the first mirror attempt failed with `upload-pack: not our ref 0000…`): record their contents, then remove the loose files.
  - Make a mirror with `git clone --mirror --no-local` to `/Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git`. `--no-local` copies only reachable objects through the pack protocol (about 2.4 GB), with no hard links into the store being pruned.
  - Verify the mirror: `fsck --connectivity-only` is clean, and its branch, tag and IronClaude ref counts equal the source's.
  - Also find the catalog database's second hard link (`find -samefile` within `~/Code` and `~/.codex`).
- **P2. Delete Codex refs.**
  - The 2 broken refs, `refs/codex/turn-diffs/checkpoints (1)/…` (loose files, names with a space, pointing at missing objects or the null ID), were already removed in P1, as files, because `update-ref` refuses invalid names. P2 confirms they are gone.
  - Every valid `refs/codex/turn-diffs/*` ref whose `rev-list --objects` reaches blob `f121f0d2…` (the 3.65 GB catalog), via `update-ref -d`. The set is computed at execution time, because Codex Desktop adds and removes these refs on its own.
  - Newer Codex refs that don't reach the blob are kept.
- **P3. Dangling-commit review.** `git fsck --connectivity-only --unreachable` (reflogs count as reachable, matching gc with reflog expiry disabled) lists unreachable commits: count, date range, and the 30 most recent with subjects. The operator reviews it with AskUserQuestion and may name commits to rescue as `refs/rescue/<date>-<n>` before pruning.
- **P4. Stop all users.** The operator stops ChatGPT.app/Codex Desktop, the Commander worker sessions in roleplaying-agents worktrees (for example `ic-d1501-…`) and any other Claude sessions there. Gates:
  - `pgrep -lx ChatGPT` is empty;
  - `pgrep -lx git` is empty;
  - no tmux session cwd under `roleplaying-agents` (read-only `tmux list-panes -a -F`);
  - the operator confirms.

  Do not kill anything ourselves.
- **P5. Prune.**
  - **No reflog expiry.** `stash@{1..5}` live only in the stash reflog, and `--expire-unreachable` would delete them. The junk is not reflog-referenced. gc expires reflogs by itself (90/30-day defaults, and all 6 stashes are older), so gc runs with `-c gc.reflogExpire=never -c gc.reflogExpireUnreachable=never`.
  - Run `git gc --prune=now` with those overrides, with a timeout of up to 2 hours.
  - Then `git lfs prune --verify-remote --dry-run --verbose`, then the real `git lfs prune --verify-remote`, which only deletes local LFS objects confirmed on the GitHub LFS remote. A dry-run listing that is unexpectedly large or empty means stop and report.
- **P6. Verify and release.**
  - `fsck --connectivity-only` is clean.
  - Every ref recorded in P1 has the same value, except the P2 deletions and any P3 rescues.
  - The stash count is still 6, and `worktree list` matches.
  - Record `count-objects`, `du .git` and `df`.
  - Tell the operator they can restart sessions. Record results in the findings doc.

## Non-goals

No history rewrite, no push, no IronClaude ref cleanup (option 5), no submodule cleanup.
