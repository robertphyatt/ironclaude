# Disk Usage Findings: 2026-10-11

> Plan: `docs/plans/2026-10-11-disk-usage-measure.md` (blind review SOLID). Read-only; nothing was deleted, moved or changed.

## Free space

`/System/Volumes/Data` has 926 Gi in total, 885 Gi used and **13 Gi available (99%)**. The 2026-10-07 cleanup had left 29 GiB free.

## Consumers

| Consumer | Now | 2026-10-07 | Change |
|---|---|---|---|
| `~/Code/roleplaying-agents` (whole repo) | 495 G | — | — |
| ↳ `.git` | 122 G | ~105 G (packs) | **+17 G in 4 days** |
| ↳ `.git/objects/pack` | 95 G, 500 packs | 500 packs | — |
| ↳ `tmp_pack_*` (22 files, 2026-10-10 12:36 to 2026-10-11 16:33) | 6.9 G | cleaned to 0 | +6.9 G |
| ↳ `index.stash.*` (~84 files, 27.9 MB each, newest 2026-10-11 18:25) | 2.0 G | cleaned to 0 | +2.0 G |
| ↳ `probe_out/*/catalog-before.sqlite3` (9 files, 2026-10-04 01:45–05:52) | 21.6 G | ~22 G | — |
| ↳ duplicate GGUFs under `godot/artificial-adventures/build/` (3 copies × 7.33 G) | ~22 G | ~10.3 G noted | — |
| `~/.config/superpowers/conversation-index` (`db.sqlite` 41.94 GB plus WAL 0.76 GB) | 40 G | 41.8 G | ~ |
| `~/.config/superpowers/conversation-archive` | 44 G | 43.9 G | ~ |
| `~/.cache/huggingface` | 32 G | — | new to the list |
| `~/Library/Application Support/Google` | 25 G | — | new to the list |
| `~/Library/Caches` (claude-cli 5.1 G, Google 5.0 G, pip 3.2 G, Spotify 2.7 G) | 22 G | — | new to the list |
| `~/Models` | 17 G | — | new to the list |
| `~/Downloads` | 14 G | — | new to the list |
| `~/.codex` | 13 G | — | new to the list |
| `~/Library/Application Support/Claude` | 11 G | — | new to the list |

## Root cause of the roleplaying-agents `.git` growth

- `tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` is **tracked** (`100644 2785c415…`) and **modified** in the working tree (`M`): 3,654,037,504 bytes, mtime 2026-10-06 02:11.
- **Every turn in the Codex Desktop app captures a snapshot of the main checkout.** Today, 28 of the 30 Codex sessions with a roleplaying-agents cwd since 15:00 were `originator: "Codex Desktop"` (guardian and subagent threads) in the main checkout. The other 2 were Commander `codex-tui` workers in `.ironclaude/worktrees`.
  - The snapshot leaves `refs/codex/turn-diffs/captures/<ms>/…` and `checkpoints/…`, at timestamps 14:44:34, 15:31:00, 15:33:36 and 18:12:09. It uses a temporary `index.stash.<pid>` index, and a new one appeared every 5–10 minutes while the sessions were active (18:00, 18:06, 18:09, 18:20, 18:25).
  - Each snapshot has to store the modified 3.65 GB database as a blob. The pack write dies partway (`tmp_pack_*`, 200–730 MB each), so nothing completes, and the next turn tries again.
- **The Commander's worktrees are not the cause.** There are 4 `.ironclaude/worktrees` and 3 `~/.codex/worktrees`, all sharing this object store, but only 2 Commander workers ran today. IronClaude refs (126 `refs/ironclaude/recovery`, 26 plan-artifacts, review receipts) pin objects against gc, but they are not the writer.
- A 10-minute live watch from about 18:35 caught no stash process, because no new `index.stash` appeared after 18:25 even though Desktop subagent threads ran until 18:45. So the snapshot runs on a subset of turns, and the exact trigger is not captured. Two broken refs `refs/codex/turn-diffs/checkpoints (1)/…` (a space in the name) remain from earlier.

## Episodic-memory `maintain --dry-run`

The opt-out `{"maintain":{"autoRun":false}}` is still in place, and `sync.log` shows "Maintain: skipped" on every sync. The `db.sqlite` and WAL size and mtime were the same before and after the run.

```
slim: refused — free disk 13967024128 bytes is below 2 x estimated slim size 7833649152 bytes; reclaimable 34109202432 bytes
backfill: skipped — the slim did not complete this run
compact: would-run — would compact 84499 files (43696093768 raw bytes)
mirror: skipped — no backup.mirrorDir configured
maintain exit=0
```

`slim` needs about 1.7 GB more free space.

## Options (none done; the operator approves each)

| # | Action | Reclaim | Risk and reversibility |
|---|---|---|---|
| a | **Delete the 8 older `catalog-before.sqlite3` snapshots and keep the newest** (`fantasy-battlemaps-20261004-recovered`, 2.98 G). The operator approved this in principle. | ~18.6 G | Untracked and gitignored (`tools/inkarnate_mcp/.gitignore:3`). Not reversible, but they are snapshots of the catalog. |
| b | **Replace the build GGUF copies with links to `models/`.** The operator asked for symlinks. Verify with `shasum` first. APFS clones (`cp -c`) also cost no space and keep the `.app` bundles self-contained and signable; the next Godot build may copy the files back either way. | ~22 G | Untracked and gitignored (`.gitignore:43 build/`). Reversible by copying back. |
| c | **Stop the `.git` growth:** `git rm --cached` `asset_catalog.db`, add it to `.gitignore` and commit in roleplaying-agents. Then delete the `tmp_pack_*` and `index.stash.*` debris. | 8.9 G now, and the growth stops | The working file stays. The commit is in roleplaying-agents, and pushing it is the operator's call. |
| d | **`git gc` / prune after (c).** The 3.65 GB blob stays in history; removing it from history needs a filter-repo rewrite (separate decision). IronClaude recovery refs pin some objects. | unknown, needs measuring | gc rewrites packs and needs free space headroom. |
| e | **Episodic-memory rollout** after (a) frees space: `maintain --keep-bak`, check search and `read`, then `autoRun: true`. | 34.1 G (slim) plus compact (gzip ~45% on samples, so ~20 G of 43.7 G) | Verified, lossless design (`f57d69c`); `db.sqlite.bak` kept. |
| f | **Caches:** `~/.cache/huggingface` (32 G), `~/Library/Caches` pip and claude-cli (8 G), `~/Downloads` (14 G). | up to ~50 G | The operator decides per item; unused model caches can be re-downloaded. |

**Suggested order:** (a), then (e), then (c), then (b), then (d). Freeing space with (a) unblocks the episodic-memory slim.

## Results (2026-10-11 cleanup)

Plan `docs/plans/2026-10-11-disk-cleanup.md`. The blind review found the vector-db over-deletion and the pathspec-commit defect (both fixed before execution). The fix-advisor caught the HEAD blob size. Task grades: 1 A, 2 A, 3 A, 4 A, 5 B.

| Step | What happened | Free after |
|---|---|---|
| Start | — | 13 GiB (99%) |
| C1 snapshots | Gate: 9 `successor-inventory-*`, newest `…fantasy-battlemaps-20261004-recovered` (Oct 4 05:52:28). Deleted exactly 8 and kept 1. The 11 `vector-db-*` snapshots are untouched. | 31 GiB |
| C5 caches | Recorded the HF list: gemma-4-E4B-it 15 G, sam3 3.2 G, siglip 3.3 G, nomic-embed-text 1.9 G, nomic-embed-vision 1.2 G, others under 0.6 G, xet 4.8 G. Emptied `hub` and `xet` (`token`, `stored_tokens` and `modules` kept). pip and claude-cli-nodejs are at 0 B. | 72 GiB |
| C3 GGUF clones | All 9 build copies hashed equal to their originals (`e8b6a059…` Q4_0, `c6398448…` mmproj, `cc328834…` qwen-talker). Each replaced by `cp -c` plus `mv`. Rehash: 3 × 3 matches, no `.clone-tmp` left. | 92 GiB |
| C2 episodic | Dry-run then `slim: would-run`. `maintain --keep-bak` exit 0. `slim: done`. `backfill: done — sessions 30426; Q→A exchanges 6255; superseded rows replaced 5582; machine sessions removed 129; missing 0; unreadable 0`. `compact: done — compacted 84499, errors 0`. New `db.sqlite` is 2.3 GB; `db.sqlite.bak` is 42 GB (kept). Archive 44 G → 16 G. `search "codex brain parity"` returned 10 results, and `show` read a `.jsonl.zst`. `autoRun: true`; the next background sync re-verifies, then deletes the `.bak` (about 39 GB more). | 118 GiB |
| C4 git | ChatGPT.app quit; no git process. Deleted 22 `tmp_pack_*` and 78 `index.stash.*`. The guard blocked the agent's `git commit`, so the operator ran it from the temp index: `f6f0d3134`. It contains only `asset_catalog.db \| Bin 6033408 -> 0 bytes` and the `.gitignore` line. `ls-files --error-unmatch` fails, and `check-ignore` matches `.gitignore:8`. The file is still on disk at 3654037504 bytes. The real index was mirrored by the agent, and all 63 earlier staged entries are intact. Two new staged entries (`GMAgent.gd`, `test_gm_clarifying_question.gd`) appeared from another process mid-task. No push. `.git` 122 G → 113 G. | 124 GiB |

## Catalog backup (2026-10-11)

Plan: `docs/plans/2026-10-11-inkarnate-catalog-backup.md`. The blind review found that a read-back from the Drive mount only proves the local cache; fixed with a cloud check through the Drive connector.

- **Source:** `roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`, 3,654,037,504 bytes, mtime 2026-10-06 02:11. It was untracked by `f6f0d3134`, and **HEAD only ever held a 6 MB version, so this file had no backup**.
- **Snapshot:** `sqlite3 -readonly SRC ".backup …"` produced 3,654,037,504 bytes. `PRAGMA integrity_check` is `ok`. All 17 tables have identical row counts in source and snapshot (assets 30544, asset_semantic_receipts 30536, asset_roles 33067, catalog_inventory_members 61610, catalog_inventory_observations 20, plus the FTS tables).
- **Compressed:** `asset_catalog-2026-10-11.db.zst` (zstd -19 --long=27), 463,061,017 bytes, MD5 `0ca734ccb3d998bd9ea2a5feb787d373`.
- **SHA-256:**
  - snapshot `cb9eb28e8f2cdcec23d8ead8d0ff1462539a8709cd1a4bad44c05ea30400f641`;
  - `.zst` `208c32d392c025f1bb55877ee3626acdd3f38a4c612194e2abab1666912a64c8`;
  - the decompressed round trip matches the snapshot.
- **Restore:** `zstd -d --long=27 asset_catalog-2026-10-11.db.zst -o asset_catalog.db`.
- **Drive:**
  - Copied to `My Drive/Backups/roleplaying-agents/inkarnate-asset-catalog/`. The local cache copy read back identical.
  - Google Drive for desktop was not running. Once started, it uploaded 362.5 of 463 MB, then stalled with "Transfer progress timeout … UNAVAILABLE_WHILE_OFFLINE" and did not retry within 20 minutes. The operator is uploading by hand at drive.google.com.
  - **Cloud: CONFIRMED (2026-10-11 ~22:15).** The operator's drive.google.com screenshots show `My Drive/…/roleplaying-agents/inkarnate-asset-catalog/` containing `asset_catalog-2026-10-11.db.zst`, Type Binary, Size 441.6 MB, Storage used 441.6 MB, modified 8:07 PM by me, plus `SHA256SUMS` (439 bytes).
    - 441.6 MiB equals 463,061,017 bytes. The stalled attempt stopped at 362,540,778 bytes (345.7 MiB).
    - The Drive app uploads with the multipart API, which is all-or-nothing, so the file exists in the cloud only if the full body arrived. A later retry completed it.
  - **Deviation from the plan.** The Drive connector's search index had not picked up the new files, and the local item IDs (`1OMpHts3…`, `14hK9eNW…`) were not cloud IDs. So the size came from the web UI at display precision, not byte-exact from the connector.
  - The temp copy was removed after this confirmation. The local Drive-folder copy remains.

**Open items:**
1. Merge the untrack commit `f6f0d3134` to `main` (or the branches in use) before switching branches in the main checkout. On every other branch the database is still tracked, and a checkout would silently replace the ignored 3.65 GB file with the 6 MB version.
2. A separate roleplaying-agents git history cleanup (operator-approved rewrite and shallowing) remains for the 95 G of packs.
3. The 42 GB `.bak` goes at the next background sync.
