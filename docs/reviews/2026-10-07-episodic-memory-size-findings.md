# Episodic memory size findings

Measured 2026-10-07 between 05:19 and 05:47 MDT. All database reads used `sqlite3 -readonly`. Nothing under `~/.config/superpowers`, `~/.claude` or `~/.codex` was modified. The episodic-memory server may write to the index concurrently, so numbers can drift slightly (one example is noted under the archive overlap). Raw logs are in `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/epmem/` (s1 to s8 logs, plus s3b-toolinput.log, an extra query described below).

Note: `-readonly` sessions take read-marks in `db.sqlite-shm`. That is SQLite's own locking, not user data.

## Short answer

The roughly 80 GB is two things of about equal size: the index database (41.79 GB) and the archive of copied transcripts (43.86 GB).

Inside the index, 73% of the file is one table, `tool_calls`. Every row of its `tool_result` column is NULL, so that table's bulk is the `tool_input` text (23.6 GB of content). No code in `src/` reads `tool_calls` at all.

Inside the archive, 73% of the bytes belong to the single project `roleplaying-agents`.

## Index

Files in `~/.config/superpowers/conversation-index/`:

| File | Bytes |
|---|---|
| db.sqlite | 41,791,868,928 |
| db.sqlite-wal | 756,638,032 |
| db.sqlite-shm | 458,752 |

- Page size is 4096. Page count is 10,203,093, which is 41,791,868,928 bytes and matches the file size exactly.
- Freelist count is 0. **Free bytes: 0 × 4096 = 0.** A VACUUM would reclaim nothing from the main file. (It could fold the 756 MB WAL back in, but that is not free-page space.)

Bytes per object, from `dbstat` (s2-dbstat.log; the query worked, `dbstat` is available):

| Object | Bytes | Pages |
|---|---|---|
| tool_calls | 30,572,392,448 | 7,463,963 |
| vec_exchanges_vector_chunks00 | 5,386,219,520 | 1,314,995 |
| exchanges | 2,181,681,152 | 532,637 |
| sqlite_autoindex_tool_calls_1 | 1,473,724,416 | 359,796 |
| idx_tool_exchange | 1,352,896,512 | 330,297 |
| idx_tool_name | 710,180,864 | 173,384 |
| vec_exchanges_chunks | 29,773,824 | 7,269 |
| idx_project | 14,774,272 | 3,607 |
| vec_exchanges_rowids | 14,188,544 | 3,464 |
| idx_timestamp | 13,623,296 | 3,326 |
| idx_session_id | 12,439,552 | 3,037 |
| everything else (8 smaller objects) | under 12 MB each | |

Derived totals (arithmetic on the rows above):
- `tool_calls` plus its three indexes: 30,572,392,448 + 1,473,724,416 + 1,352,896,512 + 710,180,864 = 34,109,194,240 bytes (81.6% of the file).
- Vector table: 5,386,219,520 bytes (12.9%).
- `exchanges`: 2,181,681,152 bytes (5.2%).
- Remainder of the file, after the three groups above: 41,791,868,928 - 34,109,194,240 - 5,386,219,520 - 2,181,681,152 = 114,774,016 bytes, i.e. about 0.1 GB of small indexes and metadata.

Column byte totals (s3-columns.log, s3b-toolinput.log):
- `exchanges`: 249,047 rows. `user_message` 1,620,802,321 bytes. `assistant_message` 300,993,909 bytes. `archive_path` 37,515,239 bytes. The `embedding` column in `exchanges` is NULL in every row (sum is empty); the real vectors are in `vec_exchanges_vector_chunks00`.
- `tool_calls`: 28,751,868 rows. `tool_input` 23,631,048,455 bytes. **`tool_result` is NULL in all rows** (count of non-NULL = 0).
- So the 30.57 GB `tool_calls` table holds 23.63 GB of `tool_input` text. The other 6,941,343,993 bytes (30,572,392,448 - 23,631,048,455, derived) are the id, exchange_id, tool_name, timestamp columns plus page overhead across 28.75 million rows.

Size bands for `tool_result`: all 28,751,868 rows are in the "10 KB or less" band, because every value is NULL. There are no large results to trim.

Size bands for `tool_input` (extra query, s3b-toolinput.log):

| Band | Rows | Bytes |
|---|---|---|
| 10 KB or less | 28,375,513 | 17,160,176,918 |
| over 10 KB up to 100 KB | 376,303 | 6,464,894,068 |
| over 100 KB | 52 | 5,977,469 |
| over 1 MB | 0 | 0 |

Top tools by `tool_input` bytes: Write 8,870,437,043 (1,099,539 calls); Edit 3,556,636,512; Bash 2,786,838,322; create_plan 1,927,952,497; Agent 1,794,012,024; Task 1,094,451,040; followup_task 632,844,795; AskUserQuestion 490,778,697; TodoWrite 448,149,540; Read 444,311,976. (The plan's top-tools query sorted by `tool_result`, which is all NULL, so its list in s3-columns.log is in arbitrary order; the table above is from the extra query.)

Top projects by message text bytes (user plus assistant): roleplaying-agents 948,068,728; `-private-tmp` 315,014,975; claude-double-shot-latte 180,207,819; ironclaude 140,628,334; ironclaude-brain 57,900,050. All message text together is 1.92 GB, 4.6% of the file.

## Archive

Total: 43,860,214,016 bytes (s4-archive.log).

Per project, largest first:

| Project directory | Bytes | Files |
|---|---|---|
| -Users-roberthyatt-Code-roleplaying-agents | 32,203,283,638 | 29,199 |
| -Users-roberthyatt-Code-ironclaude | 5,057,732,523 | 3,265 |
| -Users-roberthyatt--ironclaude-brain | 1,875,716,724 | 2,580 |
| -private-tmp | 651,460,353 | 18,931 |
| -Users-roberthyatt--ironclaude-grader | 639,262,293 | 13,942 |
| -Users-roberthyatt-Code-claudetraderbot | 583,050,237 | 1,771 |

Largest files:
- A Codex rollout in roleplaying-agents: 1,711,787,375 bytes.
- A second roleplaying-agents rollout: 679,186,878 bytes.
- A Claude session in roleplaying-agents: 548,533,591 bytes.

The top 20 are listed in s4-archive.log. The three largest files together are 2,939,507,844 bytes.

### Overlap with the originals (s6-overlap.log)

The script classifies each archive file by whether the original it was copied from still exists and whether its size is the same.

By class:

| Class | Bytes | Files |
|---|---|---|
| ORIG_DIFF_SIZE (original exists, different size) | 22,754,973,698 | 1,684 |
| ORIG_MISSING (original gone) | 16,632,286,215 | 75,260 |
| ORIG_SAME_SIZE (original exists, same size) | 4,467,739,256 | 4,521 |
| GENERATED (summary and temp files) | 5,214,847 | 5,988 |

By class and kind:

| Class and kind | Bytes | Files |
|---|---|---|
| ORIG_DIFF_SIZE codex-rollout | 21,564,314,200 | 1,680 |
| ORIG_MISSING claude-session | 15,874,541,353 | 67,688 |
| ORIG_SAME_SIZE codex-rollout | 3,509,270,707 | 485 |
| ORIG_DIFF_SIZE claude-session | 1,190,659,498 | 4 |
| ORIG_SAME_SIZE claude-session | 956,976,821 | 3,129 |
| ORIG_MISSING claude-agent | 757,744,862 | 7,572 |
| GENERATED | 5,214,847 | 5,988 |
| ORIG_SAME_SIZE claude-agent | 1,491,728 | 907 |

The classes add up to the archive total (22,754,973,698 + 16,632,286,215 + 4,467,739,256 + 5,214,847 = 43,860,214,016).

Follow-up measurement on the 1,684 ORIG_DIFF_SIZE files (made by comparing archive size with original size, run after the main script):
- Codex rollouts: the archive copy is 21,564,314,200 bytes in total but the originals together are only 2,794,392,265 bytes. In 522 files the original is smaller than the archive copy, by 18,803,776,711 bytes in total. In 1,158 files the original is larger (by 33,854,776 bytes in total).
- Claude sessions: 4 files, the original is larger by 2,201,256 bytes in total; archive total 1,190,659,498, originals 1,192,860,754.
- Archive bytes for the files whose original is larger (a second pass, 1,162 files = 1,158 Codex + 4 Claude): 2,653,601,928 bytes. The first pass counted 1,158 Codex files, so 4 Claude files were added; the originals are live files and can change between runs.

What this says: for 522 Codex rollouts the original has been shrunk or rewritten and the archive holds data that no longer exists elsewhere (about 18.9 GB of archive bytes, 21,564,314,200 minus the 2,653,601,928 attributable to larger originals). Whether the larger originals simply extend the archived prefix is UNVERIFIED (not compared byte for byte).

## Compression (s8-gzip.log)

`gzip -c | wc -c` on eight archive files (the three largest plus the largest file from each of five other projects):

| File | Original bytes | gzip bytes | Ratio |
|---|---|---|---|
| roleplaying-agents rollout (largest) | 1,711,787,375 | 706,499,681 | 41.3% |
| roleplaying-agents rollout (2nd) | 679,186,878 | 405,618,110 | 59.7% |
| roleplaying-agents Claude session | 548,533,591 | 291,133,554 | 53.1% |
| ironclaude-brain session | 35,652,080 | 9,042,203 | 25.4% |
| -private-tmp session | 1,851,905 | 194,000 | 10.5% |
| ironclaude-grader session | 5,190,013 | 231,980 | 4.5% |
| claudetraderbot session | 117,021,643 | 19,122,481 | 16.3% |
| robertpowers session | 153,834,569 | 26,905,874 | 17.5% |

The eight files together: 3,253,058,054 bytes become 1,458,747,883 bytes (44.8%). That figure is dominated by the three largest files. The three biggest compress poorly (41% to 60%) compared to the small ones (4% to 25%).

`tool_input` sample: the first 200 rows over 10 KB were 4,125,372 bytes and gzip to 935,156 bytes (22.7%). That sample is small and taken in table order, so it is not representative; treat the ratio as indicative only.

The planned `tool_result` sample was empty (39-byte gzip of nothing), because every `tool_result` is NULL.

## Code paths (source ranges read)

Directory: `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/src`.

Where archive copies come from:
- Claude: `sync.ts:106-113`. For each `.jsonl` file directly under `~/.claude/projects/<proj>/`, `copyIfNewer` writes `ARCH/<proj>/<file>`. It is not recursive, so the current subagent layout `<proj>/<session>/subagents/*` is not archived. `copyIfNewer` is at `sync.ts:36-57` and only compares mtime.
- Codex: `codex-sync.ts:30-44` (`walkRollouts`, recursive, matches `rollout-*.jsonl`) and `codex-sync.ts:102-107` (copies to `ARCH/<mungeCwd(cwd) or codex-<id>>/<basename>`).
- Sources actually synced: `sync-cli.ts:62-63`. It passes `~/.claude/projects` and `~/.codex/sessions` only. Difference from the plan: `~/.codex/archived_sessions` is not a sync source (the overlap script indexed originals from both directories, so a rollout whose original moved to `archived_sessions` still counts as having an original).
- Older indexer path: `indexer.ts:88-116`, `indexer.ts:220-224`, `indexer.ts:300-317` (copy if archive file missing).

Who reads archive files:
- `mcp-server.ts:295`: `fs.readFileSync` of an archive path for the `read` tool (guarded to stay inside the archive directory, `mcp-server.ts:285-291`).
- `show-cli.ts:49`: `readFileSync` for the `show` command.
- `search.ts:142-146`: reads `<archive>-summary.txt` for search results. `search.ts:172`: `createReadStream` to count lines.
- `parser.ts:33` and `codex-parser.ts:28`: stream the transcript while indexing.
- `repair-cli.ts:61-82`: reads archive files to fill gaps.
- `indexer.ts:20`: reads a whole file to count lines.
- `stats.ts:56-59`: reads summary paths.

All of these use plain file reads of the `.jsonl`. Compressing the archive would require changing every one of them.

Who reads `tool_input` / `tool_result`:
- Writes only: `db.ts:196-208` inserts `tool_input` (JSON of `block.input`, from `parser.ts:132` and `codex-parser.ts:86`) and `tool_result` (`db.ts:207`).
- `tool_result` is never set. The parser's tool-result branch is a TODO (`parser.ts:143-148`) and `toolResult` is optional in `types.ts:6`. That is why all rows are NULL.
- No search, display or embedding code reads `tool_calls`. The only SQL mentions are in `db.ts` (schema at 88-96, indexes at 130-133, insert at 196-197). Embeddings use only user message, assistant message and tool names (`embeddings.ts:66-82`, the names are passed by the callers, for example `indexer.ts:164-171`).
- `exchanges.line_start` and `archive_path` are returned by `search.ts:72-73,100-101,136-137` so a hit can point into the archive.

## Estimated savings per candidate fix

Each estimate states the numbers it rests on. Total reclaimable on disk depends on how the fix is carried out; see the disk constraint at the end.

1. **Drop `tool_calls` and its three indexes from the index (nothing in `src/` reads it).** Up to 34,109,194,240 bytes (the four objects summed above), leaving about 7,682,674,688 bytes. This is the largest single item and carries no search loss that the code can show. Tool names are still folded into embeddings at index time, so existing vectors are unaffected. Caveat: it removes the stored tool inputs from the index permanently; the originals remain in the archive for transcripts that still have them.
2. **Keep `tool_calls` but slim it.** `tool_result` is already NULL, so there is nothing to strip there. Setting `tool_input` to NULL or a short stub for the 376,355 rows over 10 KB would free at most their content, 6,464,894,068 + 5,977,469 = 6,470,871,537 bytes. Dropping only the Write tool's inputs would free at most 8,870,437,043 bytes of content. These are upper bounds on content, not file size (page overhead and fragmentation are not counted), so actual shrinkage is UNVERIFIED.
3. **VACUUM.** The free-page count is 0, so VACUUM on the current file reclaims 0 bytes. It only helps after a large delete such as option 1 or 2.
4. **Skip or clone archive copies whose originals exist.** Safe-looking candidates: the ORIG_SAME_SIZE class, 4,467,739,256 bytes in 4,521 files. Adding the files whose original is larger would add up to 2,653,601,928 bytes, but whether those archive copies are exact prefixes is UNVERIFIED. Not safe: the 522 Codex rollouts whose originals are smaller than the archive copy (about 18.9 GB of archive bytes with no complete original), and the ORIG_MISSING class (16,632,286,215 bytes), which exist only in the archive.
5. **Compress the archive.** If the 44.8% ratio from the eight-file sample held across all 43,860,214,016 bytes, the archive would shrink by about 24.2 GB. That is an extrapolation from eight files; the small-file ratios were 4.5% to 25.4%, so the true figure is UNVERIFIED and could be higher or lower. It also needs changes to every reader listed above.
6. **Prune rules.**
   - Scratch `/private/tmp` projects: 651,460,353 bytes (18,931 files), about 1.5% of the archive.
   - Huge rollouts: the three largest files total 2,939,507,844 bytes (6.7% of the archive).
   - The `roleplaying-agents` project: 32,203,283,638 bytes (73.4% of the archive); a size cap on its files would be the largest prune lever, but how much a cap would remove is UNVERIFIED (file-size distribution not measured).
   - Sidechains (`is_sidechain`): UNVERIFIED, not measured.

## Disk constraint for the fix design

- Free disk right now (`df -k /System/Volumes/Data` at 05:47 MDT): 30,978,020 KiB available, which is 31,721,492,480 bytes (about 29.5 GiB; `df -h` shows 29Gi). Disk is 97% full (864 GiB used of 926 GiB at the start of the run).
- A full VACUUM, or any rebuild of the index into a new file, needs free disk roughly equal to the size of the new file while the old one still exists.
- Vacuuming the current 41,791,868,928-byte index as is would need about 41.8 GB, more than the 31.7 GB free. It cannot be done in place today.
- Deleting rows or tables does not shrink the file; it only creates free pages (see the zero count above). The file shrinks only after a VACUUM or a rebuild, and that rebuild must fit in the free space above.
- If `tool_calls` and its indexes are left out of a rebuilt index, the new file would be about 41,791,868,928 - 34,109,194,240 = 7,682,674,688 bytes, which fits in the free space with room to spare. Building a fresh slim index first, then swapping and deleting the old file, is therefore the design that fits the disk.
