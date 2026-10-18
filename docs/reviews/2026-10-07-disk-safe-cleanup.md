# Disk Safe Cleanup: Results (2026-10-07)

**Plan:** `docs/plans/2026-10-07-disk-safe-cleanup.md`.

**Evidence:** `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/disk-cleanup/` (`delete.txt`, `manifest.txt`, `df-*.txt`, `count-*.txt`, `status-*.txt`, `fsck.txt`, `remaining.txt`).

## Result

179 files were deleted, totalling 14.44 GB (1e9-byte GB) by manifest size. Free space on `/System/Volumes/Data` went from **16 GiB to 29 GiB**: capacity dropped from 99% to 97%, and used space from 877 GiB to 864 GiB.

| Pattern | Files |
|---|---|
| `roleplaying-agents/.git/objects/pack/tmp_pack_*` | 71 |
| `roleplaying-agents/.git/index.stash.*` | 105 |
| `roleplaying-agents/.git/index.lock.inkarnate-stale-20261002` | 1 |
| `/private/tmp/inventory_preservation_plan_review.json`, `..._fixed.json` | 2 |

## Gates passed before deletion

- **Pattern gate:** `nonmatch_rc=1`, with no lines outside the four approved patterns. The `delete.txt` and `manifest.txt` counts were equal (179 and 179).
- **Open handles:** `xargs -0 lsof -- <list>` returned `lsof_rc=1` with no output. No process had any listed file open.
- **Freshness:** no `tmp_pack_*` or `index.stash.*` file had been modified in the last 10 minutes.
- **Git processes:** none were running.
- **Live lock:** `.git/index.lock` was absent (`lock_ls_rc=1`).

## Verification after deletion

- **Remaining files:** all 179 paths now report `No such file or directory`.
- **Git garbage** (`count-objects -v`): `garbage` dropped from 74 to 3, and `size-garbage` from 10,213,572 KiB to 41,028 KiB.
  - The remaining 3 are loose `objects/{1b,1c,d8}/tmp_obj_*` files.
  - These were not in the approved set, so they were left alone.
- **Packs:** unchanged, `packs: 500` and `size-pack: 92,490,464 KiB` (about 88 GiB).
- **`git status --porcelain=v1 -uno`** (run with `GIT_OPTIONAL_LOCKS=0`): identical before and after (`status_diff_rc=0`).
- **`git fsck --connectivity-only --no-dangling`:** `fsck_rc=10`, with 4 errors. All of them are in Codex app refs under `refs/codex/turn-diffs/checkpoints (1)/…`:
  - `badRefName: invalid refname format`, because the path contains a space;
  - `invalid sha1 pointer 0000000000000000000000000000000000000000`.

  These errors were there before the cleanup. fsck never reads `tmp_pack_*` or `index.stash.*`. The duplicated `checkpoints (1)` directory, with all-zero refs, is further evidence that the Codex app's turn-diff snapshotter is the writer that has been dying mid-write.

## Recurrence

The snapshot writer was still running at Oct 6 22:47, when the newest `index.stash.*` was written. While `asset_catalog.db` (3.65 GB) is tracked as a plain git blob, each snapshot adds another 267–800 MB pack, and each crash leaves temp files behind. This cleanup frees space but does not stop that.

## Decisions for the operator

1. **Stop tracking `tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`.** Add it to gitignore, move it to LFS, or move it out of the repo. Then run `git gc --prune` to reclaim most of the ~88 GiB of packs. Removing the broken `refs/codex/turn-diffs/checkpoints (1)` refs belongs in the same step.
2. **Prune the old snapshots and duplicate models.** That means the `probe_out/*/catalog-before.sqlite3` snapshots (~22 GB) and the duplicate gemma GGUFs under `godot/.../build/` (~10.3 GB).
3. **Fix episodic memory in an IronClaude loop.** Its index plus archive takes about 80 GB. The loop needs to cap or skip huge transcripts, use copy-on-write copies, add VACUUM or retention, and finish the single-instance sync fix.
4. **Find and stop the snapshot writer, most likely the Codex app's turn-diff snapshots.** Alternatively, keep huge tracked blobs out of its reach.
