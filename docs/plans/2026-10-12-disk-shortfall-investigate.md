# Disk Free-Space Shortfall Investigation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Find out where the roughly 50 GiB went: the prune removed about 103 GiB from roleplaying-agents `.git`, but `df` free space rose only about 50 GiB. Record the answer in a findings doc. Nothing is deleted or changed.

**Requirements:** `docs/plans/2026-10-12-disk-shortfall-investigate-design.md` (R1–R7)

**Architecture:** One read-only task. Each step writes its raw output and its stderr to its own files under `/private/tmp/disk-shortfall/`, and appends each command's exit status to `exits.txt`. The findings doc ranks the candidates by measured GiB.

**Tech Stack:** macOS `df`, `diskutil`, `osascript` (JXA), `tmutil`, `lsof`, `find`, `stat`, `du`, `awk`.

**Execution invariants:**
- Shell state does not carry over between steps, so every path is a literal absolute path.
- Globs are quoted, because zsh has `nomatch` set.
- Evidence commands don't use `2>/dev/null`. Each one sends stderr to its own `.err` file and records its exit status in `exits.txt`.
- A tool that fails produces a "not measured" row that cites its `.err` file. It is never a silent gap.
- Each step's output carries a `date` stamp, because Drive staging changes while the investigation runs.
- Long scans (Step 6 and Step 8) run in the background with a 1,800,000 ms timeout.
- Nothing is deleted, moved, killed, unmounted or reconfigured. No `sudo` is used.
- `git status` runs with `GIT_OPTIONAL_LOCKS=0`, so it doesn't refresh the index.
- `docs/` is gitignored, so docs are staged with `git -C … add -f`.

---

## Task 1: Measure and explain the shortfall (R1–R7)

No tests required: this is a read-only investigation, and its only output is a findings doc.

**Files:**
- Create: `docs/reviews/2026-10-12-disk-shortfall-findings.md`

**Step 1: Volume state and roleplaying-agents status (R1, R7)**
```bash
mkdir -p /private/tmp/disk-shortfall && cd /private/tmp/disk-shortfall && date > stamp-1.txt; df -h / /System/Volumes/Data > df.txt 2> df.err; echo "df=$?" >> exits.txt; diskutil info /System/Volumes/Data > apfsinfo.txt 2> apfsinfo.err; echo "diskutil-info=$?" >> exits.txt; diskutil apfs list > apfs.txt 2> apfs.err; echo "apfs-list=$?" >> exits.txt; GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents status --short > ra-status-before.txt 2>&1; echo "status-before=$?" >> exits.txt; cat exits.txt
```
Expected: all four exit codes are recorded. Write down the Data volume's free space from `df`, Container Free Space and Volume Used Space from `diskutil info`, and each volume's Capacity Consumed.

**Step 2: Purgeable space and snapshots (R1)**
```bash
cd /private/tmp/disk-shortfall && osascript -l JavaScript -e 'ObjC.import("Foundation");var u=$.NSURL.fileURLWithPath("/System/Volumes/Data");var r=u.resourceValuesForKeysError(["NSURLVolumeAvailableCapacityKey","NSURLVolumeAvailableCapacityForImportantUsageKey"],null);JSON.stringify(ObjC.deepUnwrap(r))' > purgeable.txt 2> purgeable.err; echo "purgeable=$?" >> exits.txt; diskutil apfs listSnapshots /System/Volumes/Data > snapshots-apfs.txt 2> snapshots-apfs.err; echo "listSnapshots=$?" >> exits.txt; tmutil listlocalsnapshots / > snapshots-tm.txt 2> snapshots-tm.err; echo "tmutil=$?" >> exits.txt; cat purgeable.txt purgeable.err snapshots-apfs.txt snapshots-tm.txt
```
Expected: purgeable space is computed as the important-usage figure minus the plain available figure, in GiB. If `purgeable` didn't exit 0, the purgeable row says "not measured" and cites `purgeable.err`. Both snapshot lists are recorded.

**Step 3: Files deleted but still open (R2)**
```bash
cd /private/tmp/disk-shortfall && lsof +L1 > lsof-L1.txt 2> lsof-L1.err; echo "lsof=$?" >> exits.txt; wc -l lsof-L1.txt; awk 'NR>1 {s+=$7} END {printf "bytes=%d GiB=%.2f\n", s, s/1073741824}' lsof-L1.txt > lsof-L1-sum.txt; cat lsof-L1-sum.txt; grep -n -e '\.pack' -e '\.idx' -e tmp_pack_ -e roleplaying-agents lsof-L1.txt > lsof-L1-flags.txt; echo "flags-grep=$?" >> exits.txt; cat lsof-L1-flags.txt
```
Expected: the total is recorded. `lsof` exiting 1 with no rows means there are no such files. `flags-grep` exiting 1 means none of the flagged path types turned up. Holders are named in column 1, with their PID in column 2.

**Step 4: Staging folder totals, split by link count (R3)**
```bash
cd /private/tmp/disk-shortfall && date > stamp-4.txt; du -sk /Users/roberthyatt/Code/roleplaying-agents/.tmp.driveupload > driveupload-du.txt 2> driveupload-du.err; echo "staging-du=$?" >> exits.txt; find /Users/roberthyatt/Code/roleplaying-agents/.tmp.driveupload -type f -exec stat -f '%z %l %i %N' {} + > driveupload-all.txt 2> driveupload-all.err; echo "staging-find=$?" >> exits.txt; awk '{k=($2==1)?"links=1":"links>1"; s[k]+=$1; n[k]++} END {for (k in s) printf "%s files=%d GiB=%.2f\n", k, n[k], s[k]/1073741824}' driveupload-all.txt > driveupload-bylinks.txt; cat driveupload-du.txt driveupload-bylinks.txt
```
Expected: the total size and the GiB for `links=1` and `links>1` are recorded, covering every file with no size filter.

**Step 5: Large staged files and their magic bytes (R3)**
```bash
cd /private/tmp/disk-shortfall && awk '$1 > 52428800' driveupload-all.txt | sort -rn > driveupload-big.txt; wc -l driveupload-big.txt; for f in $(awk '{print $4}' driveupload-big.txt); do printf '%s ' "$f"; head -c 16 "$f" | tr -c '[:print:]' '.'; echo; done > driveupload-magic.txt 2>&1; cat driveupload-big.txt driveupload-magic.txt
```
Expected: the files over 50 MB are recorded with their size, link count, inode and path, plus their signatures. `PACK` means a git pack and `SQLite format 3` means a database.

**Step 6: Where the other links live (R3). Run in the background with a 1,800,000 ms timeout.**
```bash
cd /private/tmp/disk-shortfall && bash -c 'set -f; expr=$(awk "NR>1{printf \" -o \"} {printf \"-inum %s\", \$3}" driveupload-big.txt); find /Users/roberthyatt -xdev \( -path /Users/roberthyatt/Code/roleplaying-agents/.tmp.driveupload -o -path /Users/roberthyatt/Library/CloudStorage \) -prune -o -type f \( $expr \) -exec stat -f "%i %z %N" {} + > otherlinks.txt 2> otherlinks.err; echo "otherlinks=$?" >> exits.txt'; cat otherlinks.txt; awk '{print $3}' driveupload-big.txt | sort -u > big-inodes.txt; awk '{print $1}' otherlinks.txt | sort -u > found-inodes.txt; comm -23 big-inodes.txt found-inodes.txt > unfound-inodes.txt; wc -l unfound-inodes.txt
```
Expected: each large staged file's other path is recorded. Inodes not found elsewhere under `$HOME` are listed in `unfound-inodes.txt`. Permission errors in `otherlinks.err` count as a stated limit, not a failure. If `driveupload-big.txt` is empty, record "no large staged files" and skip this step.

**Step 7: Drive's local stores (R4)**
```bash
cd /private/tmp/disk-shortfall && du -sh "/Users/roberthyatt/Library/Application Support/Google/DriveFS" /Users/roberthyatt/Library/CloudStorage > drivefs-du.txt 2> drivefs-du.err; echo "drivefs-du=$?" >> exits.txt; find "/Users/roberthyatt/Library/Application Support/Google/DriveFS" -maxdepth 3 -type d -name 'content_cache' -exec du -sh {} + > drivefs-cache.txt 2> drivefs-cache.err; echo "drivefs-cache=$?" >> exits.txt; cat drivefs-du.txt drivefs-du.err drivefs-cache.txt
```
Expected: sizes are recorded. A missing path in a `.err` file is itself a finding.

**Step 8: Other writes since the prune (R5). Run in the background with a 1,800,000 ms timeout.**
```bash
cd /private/tmp/disk-shortfall && du -sh /Users/roberthyatt/Backups /Users/roberthyatt/.config/superpowers/conversation-archive /Users/roberthyatt/.config/superpowers/conversation-index /Users/roberthyatt/.codex/sessions /Users/roberthyatt/.claude/projects /Users/roberthyatt/Code/roleplaying-agents/.git > known-du.txt 2> known-du.err; echo "known-du=$?" >> exits.txt; du -x -d 1 -g /Users/roberthyatt > home-d1.txt 2> home-d1.err; echo "home-du=$?" >> exits.txt; sort -rn home-d1.txt | head -25
```
Expected: sizes are recorded. Compare them with `docs/reviews/2026-10-11-disk-usage-findings.md` and note any child that grew by 5 GiB or more.

**Step 9: Write the findings (R6)**

Create `docs/reviews/2026-10-12-disk-shortfall-findings.md` with these sections:
1. The gap in GiB: about 103 removed, minus 50 gained, minus the 2.7 GiB mirror, is about 50. Cite the source for each term, then give the current `df` and `diskutil` readings.
2. A table of candidates: purgeable space, APFS snapshots, deleted-but-open files, Drive staging (`links=1` and `links>1` separately, and where the other links live), the Drive local store and content cache, and other growth. Each row gives the measured GiB, the evidence file, and the share of the gap it explains. Rank by measured GiB.
3. The unexplained remainder, stated plainly.
4. Options and risks, all of them operator decisions.
5. The limits: no `sudo`; the timestamps, since staging changes while the commands run; the contents of `exits.txt`; and any row marked "not measured".

**Step 10: Stage the findings and the plan**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-12-disk-shortfall-findings.md docs/plans/2026-10-12-disk-shortfall-investigate-design.md docs/plans/2026-10-12-disk-shortfall-investigate.md docs/plans/2026-10-12-disk-shortfall-investigate.plan.json
```
Expected: no output.

**Step 11: Confirm roleplaying-agents was not changed (R7)**
```bash
cd /private/tmp/disk-shortfall && GIT_OPTIONAL_LOCKS=0 git -C /Users/roberthyatt/Code/roleplaying-agents status --short > ra-status-after.txt 2>&1; diff ra-status-before.txt ra-status-after.txt; echo "diff=$?"
```
Expected: `diff=0`. A nonzero result goes into the findings doc as activity by another session, since no step writes inside roleplaying-agents.
