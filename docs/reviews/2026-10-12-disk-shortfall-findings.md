# Disk free-space shortfall findings (2026-10-12)

Read-only investigation. Raw evidence is in `/private/tmp/disk-shortfall/` (file names cited per row). Nothing was deleted, moved, killed or changed.

## 1. The gap

- Removed from roleplaying-agents `.git`: about 103 GiB (`du -h`, base-1024: 113 to 9.9; packs 88.20 GiB + loose 16.89 GiB down to 1.99 GiB). Source: `docs/reviews/2026-10-11-ra-git-history-findings.md`, "Prune results".
- `df` free on Data rose from 113 GiB to 163 GiB, a gain of about 50 GiB (same source).
- A 2.7 GiB mirror was written to `~/Backups` (`known-du.txt`: Backups 2.7G).
- Gap: 103 - 50 - 2.7 = about 50 GiB that should have come back and did not, at the time of the prune.

Current readings (`stamp-1.txt`: Mon Oct 12 17:32:53 MDT 2026):
- `df.txt`: Data volume 926Gi size, 691Gi used, **207Gi avail**.
- `apfsinfo.txt`: Container Free Space 222.7 GB, Volume Used Space 741.8 GB.
- `apfs.txt`: Data volume Capacity Consumed 741.8 GB.

Free space changed since the prune: 163 GiB right after the prune, 207 GiB now. This investigation did not trace why, so no reason is claimed.

## 2. Candidates, ranked by measured GiB

| Rank | Candidate | Measured GiB | Evidence | Share of ~50 GiB gap |
|---|---|---|---|---|
| 1 | Deleted-but-open `~/.config/superpowers/conversation-index/db.sqlite.bak` (41,957,441,536 bytes), held open by node PIDs 6421, 10935, 19201, 92160 | **39.08** | `lsof-L1.txt`, `lsof-L1-dedup.txt`, `ps-holders.txt` | about 78% |
| 2 | Purgeable space (important-usage 225,195,274,304 B minus available 222,671,003,648 B = 2,524,270,656 B) | 2.35 | `purgeable.txt` | about 5% (see note) |
| 3 | Drive staging `.tmp.driveupload`, links>1: 234,530 files | 33.38 apparent (`du`: 35,578,972 KB) | `driveupload-bylinks.txt`, `driveupload-du.txt` | 0% (see below) |
| 4 | Drive staging, links=1 | 0 (no such files) | `driveupload-bylinks.txt` | 0% |
| 5 | DriveFS local store (17G account dir: mirror_metadata_sqlite.db 9.8G, metadata_sqlite_db 5.6G, mirror_sqlite.db 1.4G; Logs 685M) | about 18 total | `drivefs-du.txt`, `drivefs-children.txt`, `drivefs-acct-children.txt` | not attributable to the prune; no baseline measured |
| 6 | Drive content cache | 0.00 (48K) | `drivefs-cache.txt` | 0% |
| 7 | CloudStorage | 0.43 (442M) | `drivefs-du.txt` | 0% |
| 8 | APFS / Time Machine snapshots | 0 (none) | `snapshots-apfs.txt`, `snapshots-tm.txt` | 0% |
| 9 | Other growth under $HOME | not measured | see below | unknown |

Notes:
- **Purgeable and `df`.** `df` Avail (207 GiB, `df.txt`) equals the plain available figure (222,671,003,648 B = 207.4 GiB). Purgeable space is therefore not in `df` Avail. It is reclaimable, not lost, so it explains a 2.35 GiB difference between `df` and Finder-style free space. It does not explain space the prune failed to return. It is counted below only as an upper bound.
- **Deleted-but-open sum.** The plan's `lsof-L1-sum.txt` (157.48 GiB) double-counts: `lsof` lists one row per holding process and per mapping, so the same file is summed many times. It is wrong. The deduplicated figure (key: DEVICE + NODE) is 42,580,668,282 B = **39.66 GiB** (`lsof-L1-dedup.txt`); the `.bak` is 39.08 GiB of that. The dedup step is a deviation added to the plan. The next largest deleted-but-open files are about 95 MB (`lsof-L1-dedup-list.txt`).
- **Drive staging.** All 234,530 staged files have link count above 1 (`driveupload-bylinks.txt`, zero `links=1`), so each has another live link elsewhere. None of the 12 files over 50 MB is a git pack (`driveupload-magic-lcc.txt`: JSON, SQLite, PNG, a zip). Staging holds no pruned git data and adds no unique usage. Where the other links live is not measured: the `find` over $HOME timed out at 1,800,000 ms (`otherlinks.err` holds only "Operation not permitted" lines). It is not needed for this conclusion.
- **Other growth under $HOME.** Not measured: the `du` over $HOME timed out (`home-d1.txt` is empty; `home-d1.err` holds only "Operation not permitted" lines). Sizes that were measured (`known-du.txt`): Backups 2.7G, conversation-archive 17G, conversation-index 2.2G, codex sessions 4.6G, claude projects 4.2G, roleplaying-agents `.git` 7.8G. Compared with `docs/reviews/2026-10-11-disk-usage-findings.md`:
  - conversation-index: 40G before the 10-11 slim (line 20), now 2.2G.
  - conversation-archive: 44G before compaction, then 16G after it (lines 21, 76), now 17G (+1G).
  - roleplaying-agents `.git`: 113 G after the 10-11 untrack (line 77), now 7.8G (`du -sh`; the prune's own figure was 9.9 GB).

  None of the measured consumers grew by 5 GiB or more. That doc has no baseline for Backups, codex sessions or claude projects; Backups holds only the 2.7G prune mirror, which is already counted in the gap. Line 76 of that doc also says the next sync "deletes the `.bak` (about 39 GB more)". That deletion is the 39.08 GiB file the four MCP servers still hold open (row 1).

Holders of the `.bak` (`ps-holders.txt`): all four run `node /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js`.
- PID 6421 started Fri Oct 2 09:58:49 2026
- PID 10935 started Fri Oct 2 10:00:18 2026
- PID 19201 started Fri Oct 2 10:23:16 2026
- PID 92160 started Sun Oct 11 12:44:50 2026

These are episodic-memory MCP servers. The `ps` output does not say which Claude or Codex session owns each; that was not traced.

## 3. Unexplained remainder

Against the ~50 GiB gap: 39.08 GiB (the `.bak`) is a measured cause. Adding the 2.35 GiB of purgeable space as an upper bound gives 41.43 GiB. **About 8.6 GiB of the gap is unexplained** (50 - 41.43), or about 10.9 GiB if purgeable is not counted. Drive staging, snapshots and the content cache explain none of it. The $HOME-wide scans that could have found other growth did not finish.

Also unexplained: why `df` free is 207 GiB now against 163 GiB right after the prune. This was not traced.

## 4. Options and risks (operator decisions only; none executed)

1. **Restart the processes holding the `.bak`** (PIDs 6421, 10935, 19201, 92160). Closing the last open handle releases about 39 GiB. Risk: these are MCP servers inside long-lived Claude/Codex sessions, so those sessions lose episodic-memory tools until restarted and may need to be restarted themselves. Identify the owning sessions before acting; do not pattern-kill.
2. **Do nothing.** The space stays held until those processes exit. Risk: low-disk alerts continue.
3. **Reclaim purgeable space (2.35 GiB).** The OS does this under pressure; no action needed.
4. **Rerun the $HOME scans** narrowed to specific folders to chase the remaining ~8.6 GiB. Cost: time; the full scans exceeded 30 minutes.
5. **IronClaude defect candidate.** The episodic-memory `maintain` slim step deletes `db.sqlite.bak` while live MCP servers still hold it open, so the space is not returned. This should be filed as a defect; fix options (for example closing handles first, or not deleting while servers run) belong to a separate loop.

## 5. Limits

- No `sudo`. Protected Library paths returned "Operation not permitted" in the `find` and `du` scans.
- Timestamps: Step 1 at 17:32:53, Step 4 at 17:32:57 (MDT, Oct 12). Drive staging changes while commands run, so staging totals are a moment-in-time reading.
- Not measured: where staged files' other links live (Step 6 timed out at 1,800,000 ms); the $HOME depth-1 `du` (Step 8 timed out); $HOME-wide growth beyond the measured consumers (those are compared in section 2).
- Deviations from the plan: Step 3 deduplication added (plan sum is wrong); Step 5 re-run with `LC_ALL=C` because `tr` failed with "Illegal byte sequence" (`driveupload-magic.txt` vs `driveupload-magic-lcc.txt`); Steps 6 and 8 timed out and were not rerun by instruction; extra read-only `du` of DriveFS children and `ps` of the holder PIDs.
- `exits.txt` contents: df=0, diskutil-info=0, apfs-list=0, status-before=0, purgeable=0, listSnapshots=0, tmutil=0, lsof=0, flags-grep=1 (no pack/idx/roleplaying-agents holders), staging-du=0, staging-find=0, drivefs-du=0, drivefs-cache=0, known-du=0. No `otherlinks=` or `home-du=` entry: those two commands were killed at the timeout.

## 6. Holder owners (2026-10-12)

Evidence files are in `/private/tmp/disk-shortfall/`: `trace-holders.txt` (lsof per PID), `trace-chains.txt` (parent chains), `trace-cwd.txt` (cwds), `trace-tmux.txt` (tmux panes). The `.err` files (`trace-lsof-*.err`, `trace-ps.err`, `trace-cwd.err`, `trace-tmux.err`) are empty.

All four PIDs are `episodic-memory/cli/mcp-server-wrapper.js` node processes. Each one still holds the deleted `db.sqlite.bak` (41,957,441,536 bytes, fd 14u, same inode 11030411). None is an orphan: every chain runs node, then `claude --dangerously-skip-permissions --resume`, then `-zsh`, then `login`, then iTermServer (PID 1353, whose parent is launchd). No chain PID matches a `pane_pid` in `trace-tmux.txt` (the panes are 54237, 77471, 89252, 50442, 47407, 57806, 54744 and 13736), so none of these sessions runs under tmux. They are iTerm2 tabs.

| PID | Still holding | Owning process (PID, start) | tmux session | cwd of node / owner | Live session affected? |
|---|---|---|---|---|---|
| 6421 (started Fri Oct 2 09:58:49) | yes | Claude Code, PID 5578, Fri Oct 2 09:58:36, tty ttys002 | none | `/Users/roberthyatt/Code/roleplaying-agents` | yes: interactive Claude Code session in an iTerm tab |
| 10935 (Fri Oct 2 10:00:18) | yes | Claude Code, PID 10531, Fri Oct 2 10:00:11, tty ttys004 | none | `/Users/roberthyatt/Code/slack-salesforce-communication` | yes: same |
| 19201 (Fri Oct 2 10:23:16) | yes | Claude Code, PID 15296, Fri Oct 2 10:22:49, tty ttys006 | none | `/Users/roberthyatt/Code/ironclaude` | yes: same |
| 92160 (Sun Oct 11 12:44:50) | yes | Claude Code, PID 8069, Fri Oct 2 09:59:15, tty ttys003 | none | `/Users/roberthyatt/Code/roleplaying-agents` | yes: same |

All four owners were started with `--resume`. PID 19201 is the MCP server of a Claude Code session in the ironclaude directory (PID 15296). Whether that is the session doing this work was not checked here.

### Release options (operator decisions only; nothing executed)

Space comes back only when the last of the four holders closes the file. Releasing one or two PIDs frees nothing.

For each PID, either:
- End or restart the owning Claude Code session (PIDs 5578, 10531, 15296, 8069 respectively). The MCP server exits with it, and a resumed session starts a fresh server that no longer holds the deleted file. This costs the live session's in-memory state, so finish or save its work first.
- Kill only the exact `node` PID (6421, 10935, 19201 or 92160) from a shell. The owning session loses its episodic-memory tools until it is restarted, but keeps running. This is the lighter option if the tool is not needed in that session.

None of these is an orphan under `launchd`, so there is no abandoned process to clean up without touching a live session. No pattern kill should be used: match by exact PID only.

## 7. Release check (2026-10-12)

The operator restarted the four owning Claude Code sessions. Evidence is in `/private/tmp/disk-shortfall/`.

- **No holder remains.** `lsof +L1` ran cleanly (`lsof=0`), and `grep -F db.sqlite.bak verify-lsof-L1.txt` found nothing (`bak-holders-grep=1`). No process still holds the deleted backup.
- **Free space rose by 39 GiB.** `/System/Volumes/Data` used fell from 691 GiB to 652 GiB, and available rose from 207 GiB to 246 GiB (`df.txt` compared with `verify-df.txt`). The exact reading after release is 258,223,212 KB available (`df -k`).
- **The 39 GiB came back.** The rise matches the 39.08 GiB `.bak`. Free space had already drifted by an untraced amount between the prune and the baseline (§1 and §3), so the size of the `df` change alone is not the proof. The empty `lsof` result is the independent check.

The rest of the original gap, about 8.6–10.9 GiB, is still unexplained (§3). The product defect stays open: `maintain` deletes `db.sqlite.bak` while live episodic-memory servers hold it, so the next slim run would pin the space again until those sessions restart.
