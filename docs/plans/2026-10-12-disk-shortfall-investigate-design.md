# Disk Free-Space Shortfall Investigation Design

> **Created:** 2026-10-12
> **Status:** Design Complete (amended 2026-10-12)

Loop: light — read-only measurement and one findings doc. The only writes are files under `/private/tmp/disk-shortfall/` and a `git add -f` of these docs. No code, DB, guard, worktree, config or API changes; nothing deleted.

## Requirements

Operator, 2026-10-12, chose "Find the missing 50 GiB (Rec.)": "a small read-only investigation loop: files deleted but still held open (lsof +L1), the Google Drive cache, purgeable space and snapshots. It ends in a findings note; nothing gets deleted."

**The gap, in GiB.** The roleplaying-agents prune's `du -h` figures are base-1024.
- `.git` went from 113 to 9.9 GiB, about 103 GiB removed. Objects went from 88.20 GiB of packs plus 16.89 GiB of loose objects down to 1.99 GiB, and LFS went from 6.8 to 4.6 GB.
- `df` free space went from 113 to 163 GiB, about 50 GiB gained.
- The 2.7 GiB mirror in `~/Backups` was written during the prune.
- So the unexplained gap is about 103 − 50 − 2.7 ≈ 50 GiB.

Source: `docs/reviews/2026-10-11-ra-git-history-findings.md`, the "Prune results" table. The findings doc must show this arithmetic with each term's source.

- R1. **Volume, purgeable space and snapshots.** Record:
  - `df -h`;
  - `diskutil info /System/Volumes/Data`: Container Free Space and Volume Used Space;
  - `diskutil apfs list`: capacity in use per volume;
  - an estimate of purgeable space: available capacity for important usage minus plain available capacity, from the macOS volume resource keys;
  - APFS snapshots, from both `diskutil apfs listSnapshots /System/Volumes/Data` and `tmutil listlocalsnapshots /`. `tmutil` alone lists only Time Machine snapshots.

  A tool that fails is recorded as "not measured", with its `.err` file cited.
- R2. **Files deleted but still open:** `lsof +L1`, with sizes and the holding process. Flag pack, idx, `tmp_pack_` and roleplaying-agents paths. Run without `sudo`, so it covers this user's processes only.
- R3. **Drive's upload staging folder** (`roleplaying-agents/.tmp.driveupload`):
  - its total size;
  - its size split by link count, over all files with no size filter, so loose objects count too;
  - for each file over 50 MB, where its other hard link lives: inside the repo (excluding the staging folder), then elsewhere under `$HOME` for any not found.

  Link count 1 means the staging folder holds the only copy. Link count 2 or more means the data is also alive somewhere else, and that location is recorded.
- R4. **Drive's local stores:** `~/Library/Application Support/Google/DriveFS` (including any content cache) and `~/Library/CloudStorage`.
- R5. **Other writes since the prune:** the mirror, the episodic archive and index, `~/.codex/sessions`, `~/.claude/projects`, roleplaying-agents `.git`, and the first-level children of `$HOME`. Compare with `docs/reviews/2026-10-11-disk-usage-findings.md`.
- R6. **Write `docs/reviews/2026-10-12-disk-shortfall-findings.md`** with:
  - the gap arithmetic;
  - one row per candidate (purgeable, snapshots, deleted-but-open files, Drive staging, Drive local store, other growth), each with measured GiB, its evidence file, and the share of the gap it explains, ranked by measured size rather than by the starting hypothesis;
  - the unexplained remainder, stated plainly;
  - options and risks, all of them operator decisions;
  - the limits: no `sudo`, and Drive staging changes while the commands run, so each measurement is timestamped.

  Stage the doc with `git add -f`.
- R7. **Change nothing.** Delete, move, kill, unmount or reconfigure nothing. Use `GIT_OPTIONAL_LOCKS=0` for `git status` so it does not refresh the roleplaying-agents index.

## Background (verified this session)

- The catalog database's second hard link was recorded as `roleplaying-agents/.tmp.driveupload/8527915` during the prune. That file no longer exists, so Drive staging changes on its own.
- A pre-execution check by the plan reviewer found 12 staged files over 50 MB, all with link count 2, and no file over 100 KB with link count 1. If that holds at execution time, the staging folder is not the only holder of pruned packs. The other candidates in R1, R2 and R4 then carry the investigation.
- The GBTW budget and gemma latency are out of scope. The operator moved the Haiku-backend switch to the next loop, which makes that measurement moot.
