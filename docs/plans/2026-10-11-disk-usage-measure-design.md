# Disk Usage: Measure and Dry-Run Design

> **Created:** 2026-10-11
> **Status:** Design Complete

Loop: light — read-only measurement plus a `maintain --dry-run`, which changes nothing; no code, DB-write, guard, git or API changes; one findings doc.

## Requirements

Operator, 2026-10-11 (after two low-disk alerts): "let's figure out the disk stuff". The operator chose "A: Measure + dry-run": measure what has grown since 2026-10-07, run `episodic-memory maintain --dry-run`, and bring numbers and options back. Nothing is deleted until the operator approves each item.

- R1. Record free disk on the system volume (`df -h /`).
- R2. Record sizes of the known consumers:
  - `~/.config/superpowers/conversation-index` (db.sqlite 41.9 GB plus a 757 MB WAL, already seen);
  - `~/.config/superpowers/conversation-archive`;
  - `~/Code/roleplaying-agents/.git`, plus its `objects/pack` count and any `tmp_pack_*` and `index.stash.*`;
  - `probe_out` snapshots;
  - the GGUF model directories named in `docs/reviews/2026-10-07-disk-safe-cleanup.md`;
  - the top-level size of the home directory's largest children, to catch anything new.
- R3. Run `episodic-memory maintain --dry-run` and record its full output. It must change nothing. Confirm the opt-out stays `{"maintain":{"autoRun":false}}` and that the index size is unchanged afterwards.
- R4. Write `docs/reviews/2026-10-11-disk-usage-findings.md`. For each consumer it gives the size, the change since 2026-10-07 (from `project_disk_full_2026_10_07` and the two 2026-10-07 review docs), what the dry-run says each `maintain` phase would reclaim or need, and options with risks. Stage it with `git add -f`.
- R5. Delete, move, compress or gc nothing. Change no config.

## Background (verified this session)

- Episodic-memory lean (`f57d69c`, 2026-10-09) adds `maintain`, with these phases:
  - `slim` rebuilds the index without the write-only `tool_calls` table, which was 34 GB of 41.8 GB on 2026-10-07. It needs free disk of at least 2× the new index and keeps `db.sqlite.bak`.
  - `backfill`.
  - `compact` zstd-compresses archive transcripts after a 24 h live window, keeping the original until a verify passes.
  - `mirror`, optional.
- The rollout was never run. `~/.config/superpowers/episodic-memory.json` still holds the opt-out, and `sync.log` shows "Maintain: skipped" on every sync.
- CLI: `worker/mcp-servers/episodic-memory/cli/episodic-memory` (README.md:657-691).
