# Episodic Memory Size Measurement Design

> **Created:** 2026-10-07
> **Status:** Design Complete
> **Loop:** light. Read-only measurement: it writes only scratch logs and one findings note, and changes no product code or data.

## Requirements

**Operator directives:**
- "What about compressing episodic memory/pruning stuff that doesn't matter?"
- "Measure, then design (Rec.)": "Small read-only investigation loop (sqlite3 -readonly table/column sizes, archive-vs-original overlap, compressibility sample, code paths reading archive) → findings → IronClaude fix design (slim index, CoW/skip archive copies, compression, prune rules, VACUUM, single-instance sync)."

The measurement covers six areas:

- **E1. Index composition.** The index is `~/.config/superpowers/conversation-index/db.sqlite` (42.6 GB plus its WAL). Measure it with `sqlite3 -readonly`. The schema is in `worker/mcp-servers/episodic-memory/src/db.ts:63-108`.
  - **File-level space:** `page_size`, `page_count` and `freelist_count`. Free pages are what VACUUM would reclaim.
  - **Space per table and index:** read the `dbstat` virtual table, if this build of sqlite3 has it.
  - **Column bytes:** row counts and `SUM(length(...))` for each column:
    - in `exchanges`: `user_message`, `assistant_message`, `embedding` and `archive_path`;
    - in `tool_calls`: `tool_input` and `tool_result`.
  - **Top 15 tools by `tool_result` bytes.**
  - **Top 15 projects by exchange bytes.**
  - **Size distribution:** the count of `tool_result` values over 10 KB, over 100 KB and over 1 MB, with the bytes each band holds.
- **E2. Archive composition.** The archive is `~/.config/superpowers/conversation-archive/`. Measure:
  - bytes and file counts per project subdirectory;
  - the 20 largest files.
- **E3. Overlap with originals.** Read the indexer (`src/indexer.ts`, plus `paths.ts` and the sync entrypoint) to find how source transcripts map to archive paths. For each archive file, check whether its original still exists:
  - Claude transcripts under `~/.claude/projects/...`;
  - Codex transcripts under `~/.codex/sessions/...` or `~/.codex/archived_sessions/...`.

  If an original exists, record whether its size matches. Report the bytes in each of three groups: archive copies whose original still exists, copies whose original is gone (only copy), and copies whose size differs.
- **E4. Compressibility.** Pick a sample of 8 archive files: the 3 largest, plus 5 spread across projects. Measure each one's gzip ratio by streaming `gzip -c` into `wc -c`, writing no file. Do the same for a sample of `tool_result` text.
- **E5. Code paths.** List every place in `worker/mcp-servers/episodic-memory/src` that reads the archive:
  - the `read` tool;
  - the `archive_path` and `line_start`/`line_end` usage;
  - re-indexing.

  Also list every place that reads `tool_calls.tool_input` or `tool_result` for search or display, and whether the embeddings are computed from tool data. Cite `file:line` for each.
- **E6. Write the findings** to `docs/reviews/2026-10-07-episodic-memory-size-findings.md`. Use measured numbers only, and add an estimate of the savings for each candidate fix:
  - slimming the index;
  - VACUUM;
  - cloning or skipping archive copies;
  - compression;
  - prune rules.

  Each estimate must say what it is based on. No product change or deletion happens in this loop.

## Approach

One task, run by a Sonnet subagent. The large `sqlite3` scans and the `dbstat` scan run as background Bash jobs, each with a long timeout and its own log. The episodic-memory server may be writing to the database at the same time; `-readonly` with WAL is safe. The findings must record the time of each measurement.

## Final gate

None. This is an investigation note, and the fix design that follows has its own review.
