# roleplaying-agents Git: What Fills `.git`

> 2026-10-11. Plan: `docs/plans/2026-10-11-ra-git-history-measure.md` (blind review HAS-ISSUES, then fix-advisor REMEDIATED). Read-only; nothing was modified.

## Headline

**About 100 GB of the 113 GB `.git` is unreachable objects.** No branch, tag, stash or other valid ref points to them. Everything reachable from all 276 valid ref tips and worktree HEADs takes only **2.43 GB** on disk. The bloat is almost entirely the abandoned Codex Desktop turn-diff snapshot writes, not project history. **A garbage-collect that prunes unreachable objects reclaims most of it, with no history rewrite and no force-push.**

## Measurements

**Object store** (`git count-objects -vH`):

| | |
|---|---|
| packs | **500**, size-pack **88.20 GiB** (largest 2.59 GB, 1.72 GB, 2 × 0.80 GB, then many near-identical 373 MB and 267 MB packs) |
| loose objects | **23,120**, **16.89 GiB** |
| in-pack objects | 202,894 |
| prune-packable | 1,892 |
| garbage | 3 `tmp_obj_*` files (40 MiB) |

**`.git` by entry:**

| Entry | Size |
|---|---|
| `objects` | 105 G |
| `lfs` | 6.8 G |
| `modules` (4 submodules incl. llama.cpp) | 844 M |
| `worktrees` | 213 M |
| `index` | 25 M |
| `ironclaude-recovery` | 9.4 M |
| other | < 1 M each (filter-repo state from **2026-01-25**, lost-found 200 K, logs 68 K, 9 stray 41-byte SHA files at the root) |

**Reachable content.** `rev-list --objects --all` aborts on the broken refs (`fatal: bad object refs/codex/turn-diffs/checkpoints (1)/…`). Instead, valid refs from `for-each-ref` plus worktree HEADs were fed to `rev-list --stdin`: 276 tips, 195,225 objects, 152,889 blobs, 7,472 revisions.
- **The total on-disk size of reachable blobs is 2,427,688,431 bytes (2.43 GB).**
- The largest reachable item is `tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`: 4 versions, 801 MB on disk. The biggest version is 3.65 GB raw, blob `f121f0d2…`.
- Next come `libai_inference_extension…arm64.dylib` (39 versions, 98 MB), `libMLXInferenceBridge.a` (62 MB), `mlx.metallib` (30 MB), and a committed `qwen3_tts_extension/mlx_swift/.build/…` SwiftPM cache (about 25 MB). Then `pathfinder_rule_chunks*.json` (16 MB, 24 versions), music `.ogg` files (7–9 MB each) and a website `.zip` (11 MB).
- **The 3.65 GB catalog blob is held only by Codex turn-diff refs.** 5 of the 7 valid `refs/codex/turn-diffs/*` refs point straight at trees containing it. The 2 captures made after the untrack, `1791778315968` and `1791778321124`, do not contain it, which confirms the untrack stopped the re-capturing. No branch commit contains it (`log --find-object` returned none), and neither does `refs/stash`.

**Refs:**

| Namespace | Count |
|---|---|
| `refs/ironclaude/recovery` | 126 |
| `refs/ironclaude/finalization` | 78 |
| `refs/ironclaude/review-receipts` | 71 |
| `refs/ironclaude/plan-artifacts` | 26 |
| `refs/tags/archive` | 11 |
| `refs/heads/rescue` | 9 |
| `refs/remotes/origin` | 8 |
| `refs/codex/turn-diffs` | 7 valid + **2 broken** (a space in `checkpoints (1)`, pointing at missing objects) |
| `refs/heads/ironclaude` | 6 |
| `refs/original/refs` | 1 (left over from the Jan filter-repo) |

Plus 9 more branches (`main`, `portrait-gen-structural-sclera-gate`, `preserved/…`, `parked/…`, `gguf-fixes`, `experiment/…`, `structure-cleanup*`, `fix-new-player-…`). Stashes: 6. Reflogs: HEAD 56 entries, stash 6; `.git/logs` is 68 K.

**History:** 7,472 revisions reachable.
- `main` was last changed 2026-09-23 and is ahead of `origin/main` by 2.
- `portrait-gen-structural-sclera-gate` was last changed 2026-10-11 and is ahead by 2.
- **The untrack commit `f6f0d3134` is only on `portrait-gen-structural-sclera-gate` and 3 IronClaude worktree branches. It is not on `main`.**

**Remote and LFS:**
- `origin` is GitHub (`robertphyatt/roleplaying-agents`).
- `git lfs ls-files --all` exited 2 with 2 lines of output; most likely an error on the broken refs, but its output was not recorded.
- `.git/lfs` is 6.8 G (`.gitattributes` routes `*.pdf`, `*.txt`, `*.gguf`, `*.ogg`, `*.dblite` to LFS).

**Active users of this repo right now:**
- **Worktrees (9):** the main checkout, 3 Codex Desktop worktrees (`~/.codex/worktrees/{8735,8859,a535}`) and 5 IronClaude worktrees.
- **Processes:** ChatGPT.app (PID 20773), a Commander worker tmux session (`ic-d1501-r1-rev3`), blender_mcp and flux_texture_mcp servers, a portrait_gen http server, `serve_cap8_review.py`, and a `tail` on `lineart_remask.log`.

## Options (ranked by reclaim ÷ risk)

| # | Option | Reclaim | Risk | Needs |
|---|---|---|---|---|
| **1** | **Delete the 2 broken `refs/codex/turn-diffs/checkpoints (1)/…` refs.** They point at missing objects and abort `--all` operations, so gc and LFS prune cannot run cleanly without this. | ~0 | None (they are already unusable) | Operator go |
| **2** | **Prune unreachable objects:** `git reflog expire --expire-unreachable=now --all` then `git gc --prune=now`, with `git fsck --lost-found` first to list dangling commits for review. | **~100 GB** (105 G objects → ~3 G) | Deletes objects no ref points to. Dangling commits could hold uncommitted work captured by snapshots, so review the fsck list first. Concurrent writers during `--prune=now` can lose in-flight objects. | **All sessions using the repo stopped** (ChatGPT/Codex Desktop, Commander worker `d1501`, other Claude sessions), a fsck review, operator go. **No push.** |
| 3 | Delete the 5 older Codex turn-diff refs that hold the 3.65 GB catalog blob, then gc. | ~0.8 GB | Codex Desktop loses those old turn diffs (its "undo turn" for old turns) | Operator go |
| 4 | `git lfs prune` (after option 1). | Part of 6.8 GB | Removes local LFS objects not referenced by recent commits; they can be re-downloaded from GitHub LFS if pushed | Operator go |
| 5 | Clean up IronClaude refs (301) and the `refs/original` leftover. | Small (they point into the 2.43 GB of reachable history) | Loses IronClaude recovery/review audit refs | Separate IronClaude decision |
| 6 | History rewrite or shallow history (filter-repo / cut before a date / fresh root). | **≤ ~2 GB** (all reachable history is 2.43 GB) | **Force-push to GitHub**, every clone and worktree must re-clone, IronClaude/Codex refs invalidated | Not worth it given option 2 |

**Recommendation:** options 1 → 2, with 3 folded in, and optionally 4. Run them in a single session window with every repo user stopped, after a mirror of all refs (`git clone --mirror` to the external or Drive backup) and a review of the dangling-commit list. Expected `.git`: ~4–10 GB, down from 113 GB. No history rewrite or force-push is needed. Separately, merge or cherry-pick `f6f0d3134` onto `main` (operator's commit) so a checkout of `main` can no longer clobber the untracked catalog database.

## main untrack (2026-10-12): DONE

Plan: `docs/plans/2026-10-11-ra-main-untrack.md` (blind review SOLID).

**How it was built.** The change was built from `main`'s own tree in a temporary index, with no checkout, so the working tree and the real index were untouched.
- **Gate:** `main` (`b2855019`) tracked blob `2785c415` (6,033,408 bytes). `main`'s `.gitignore` lacked the line.
- **Ignore file:** the new `.gitignore` blob is `c1930e24`.
- **Temp index:** the diff against `main` was exactly `M tools/inkarnate_mcp/.gitignore` and `D …/asset_catalog.db`, giving NEW_TREE `67144b19`.
- **Commit:** the operator ran `commit-tree` and a compare-and-swap `update-ref` (the guard blocks agent commits), producing **`main` = `3b61f972`**, "Stop tracking the 3.65 GB inkarnate asset catalog database". It touched 2 files: `.gitignore` +1, and `asset_catalog.db` `Bin 6033408 -> 0 bytes`.

**Verified:**
- `main^` is `b2855019`.
- `ls-tree main -- <db>` is empty.
- `main:…/.gitignore` line 11 is `asset_catalog_raw/asset_catalog.db`.
- `origin/main` is unchanged (`2efb92f2`): no push.
- The database on disk is still 3,654,037,504 bytes.
- HEAD is still on `portrait-gen-structural-sclera-gate`.

**Result:** checking out `main` or `portrait-gen-structural-sclera-gate` no longer replaces the database. Other old branches (`rescue/*`, `preserved/*`, `gguf-fixes`, …) still track the 6 MB version, so avoid checking those out in the main checkout. The file is backed up in Google Drive.

**Observation:** the database's hard-link count went from 1 to 2 at some point between 2026-10-11 and 2026-10-12. The same data now has a second directory entry somewhere; that is harmless (no extra space, not a copy). Locate it in the prune loop.

## Prune results (2026-10-12): DONE

Plan: `docs/plans/2026-10-12-ra-git-prune.md` (blind review HAS-ISSUES: `git gc`'s own reflog expiry would have deleted `stash@{1..5}`; fixed, then fix-advisor REMEDIATED; a reorder amendment was also REMEDIATED). Evidence is in `/private/tmp/ra-prune/`.

| | Before | After |
|---|---|---|
| `.git` | 113 GB | **9.9 GB** |
| objects | 500 packs (88.2 GiB) + 23,187 loose (16.9 GiB) | **1 pack, 1.99 GiB**, 0 loose |
| `.git/lfs` | 6.8 GB | 4.6 GB |
| free disk (`df` Data) | 113 GiB | **163 GiB** |

**Steps:**
- **Stash pins.** `stash@{1..5}` were pinned as `refs/stash-keep/1..5` (bda619ef, e340ede4, 66b00c64, b3339c6c, c966485e).
- **Broken refs.** The 2 broken `refs/codex/turn-diffs/checkpoints (1)/…` refs were recorded in `broken-refs.txt` (3d69e7ed…, b21ac123…, both trees) and removed as files.
  - This had to happen **before** the mirror. The first mirror attempt failed with `upload-pack: not our ref 0000…`, because git reports an invalid ref name with the null ID. The operator chose A.
- **Mirror.** `git clone --mirror --no-local` went to `/Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git`: 2.7 GB, `fsck --connectivity-only` clean, and per-namespace ref counts equal (25 heads, 11 tags, 302 ironclaude, 1 stash, 5 stash-keep, 1 original, 5 codex).
- **Catalog-pinning refs.** 3 Codex refs reached blob `f121f0d2…` and were deleted: captures `1791754416535/ba179358`, and `1791763929714/788d01c8` and `/a3c00b28`. The 2 checkpoints were kept. Afterwards `rev-list --objects --all` found the blob 0 times, stash refs included. Codex had already dropped the other 2 of the 5 refs measured on 2026-10-11.
- **Unreachable review.** 10,032 blobs, 3,152 revisions and 11,290 trees.
  - 3,134 of the revisions were auto-snapshot "index on …" / "WIP on …" objects.
  - The other 18 were real revisions, each with an identical-subject copy in reachable history.
  - The operator chose **"A: Rescue none, prune"**.
- **Stop.** The operator quit ChatGPT.app and stopped the Commander.
  - The Commander's 2 worker tmux sessions kept running: `ic-d1501-r5-cleanroom-review` (codex 10027) and `ic-d1501-fix-loop-5-recovery` (codex 54556). So did an orphaned Codex app-server daemon (PID 43509, running since Oct 6, in worktree 3106ccc6). The operator chose "A: You stop those 3", and they were stopped by exact name and PID.
  - **Deviation:** the planned stop check was relaxed at the operator's direction. Two idle Claude sessions (PIDs 5578 and 8069, with their MCP servers) and the static http/file servers stayed running, on the operator's promise to keep the sessions idle.
  - At gc time there was no ChatGPT, Codex or git process running.
- **gc.** `git -c gc.reflogExpire=never -c gc.reflogExpireUnreachable=never gc --prune=now` exited 0.
- **LFS.** `git lfs prune --verify-remote` exited 0.
  - The dry run's verbose listing showed a single 12-byte object, but the real run deleted 8,825 objects (about 2.2 GB). Only 6,097 were "verified with remote".
  - The rest were not referenced by any reachable revision (git-lfs verifies those only with `--verify-unreachable`).
  - Afterwards `git lfs fsck` reported OK.
  - **Lesson:** the dry-run listing doesn't show the deletion scope; read the summary counts instead.

**Verified afterwards:**
- `fsck --connectivity-only` exit 0, and `git lfs fsck` OK.
- Refs after the prune are identical to the pre-gc snapshot. Against the very first snapshot, the only differences are the 3 deleted Codex refs and the 5 added `stash-keep` refs.
- Stash list has 6 entries and the stash reflog has 6; `stash@{1..5}` equal their `stash-keep` pins.
- `worktree list` is unchanged.

**Findings:**
- **The second hard link** on `asset_catalog.db` is `roleplaying-agents/.tmp.driveupload/8527915`, Google Drive for desktop's upload staging. That suggests Drive is syncing the roleplaying-agents folder, `.git` included. Check Drive's preferences.
- **Free space** grew about 50 GiB, against about 103 GB removed from `.git`. There are no local APFS snapshots (`tmutil listlocalsnapshots /` is empty). The cause is unknown; Drive's staging of deleted packs is a possible culprit. Not investigated.
- **IronClaude backlog:** stopping the Commander (`make stop`) leaves its worker tmux sessions and their Codex processes running.
- `refs/stash-keep/*` stay in place as harmless permanent pins. Delete them with `update-ref -d` once the stashes no longer matter.
