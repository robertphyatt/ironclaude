# Episodic Memory: Lean and Useful (Requirements)

> **Created:** 2026-10-07
> **Status:** Operator-approved. The operator decisions below are quoted verbatim.
> **Evidence:**
> - `docs/reviews/2026-10-07-episodic-memory-size-findings.md`
> - the Fable assessments of 2026-10-07: approach, archive, and remaining decisions

## Operator directives (verbatim)

**Purpose of episodic memory.**
- "1) deeply understand past conversations without missing a beat with every conversation 2) have the commander be able to effectively behave as my avatar: 'What would Robert do?'"
- "remember that other users will likely not be 'what would Robert do' but rather 'what would <name>' do, etc"

**Shape of the solution.**
- "Let's do an ep-memory loop! Part of this should include having a durable ironclaude script that ships with the ironclaude codebase, and we can have it run as a process in the background or maybe just a one off script? The idea here is that other users will have the same problem as me and I want to make sure episodic memory fulfills its purpose without adding bloat"
- "Can we adjust the episodic memory so that once we run this once it is fixed for good with no maintenance and the normal episodic memory stuff just works as discussed? If so, let's do the Auto + CLI approach as proposed"
- "The idea is that I want the episodic memory to be lean and useful: keep what matters vs decisions and context but cut the bloat across the board" (machine-session policy, decided now rather than deferred)

**Decisions.**

| Topic | Decision |
|---|---|
| Sessions | "Drop machine, tag Brain (Rec.)" |
| Archive | "Compressed, lossless (Rec.)", after the operator asked for Fable review of a one-way change |
| Remaining items D2, D3, D4 | "Adopt all three (Rec.)" |
| Section 1 (code changes) | approved, with the machine-session policy added |
| Section 2 (maintenance routine) | "Yes (Rec.)" |

**Standing rules.**
- Never pattern-kill processes.
- Never push without an explicit go for that push.
- The operator commits through `/commit`.
- A final passing Fable review is required before committing.

## Requirements

- **L1. Stop the index bloat at its source.**
  - Episodic memory no longer creates or writes the `tool_calls` table or its indexes. That table is write-only: `db.ts` is the only SQL that names it.
  - Tool names stay in memory, so the embedding text is unchanged.
- **L2. Capture operator-voice signal, for any operator.**
  - Index AskUserQuestion answers (`toolUseResult.answers`) as question→answer pairs in `userMessage`.
  - Store the session entrypoint (`cli` vs `sdk-py`) as a new column, added by an additive migration, and return it in search results so callers can prefer the operator's own sessions.
  - Nothing is hard-coded to one operator's name.
- **L3. Make the archive copy free on APFS.** Copies into the archive use `COPYFILE_FICLONE`, falling back to a real copy elsewhere. A file is never skipped.
- **L4. Run exactly one sync.** Verify the existing singleton guard and add a staleness or timeout check. Fix only defects you can show.
- **L5. Ship an idempotent `maintain` routine.**
  - It runs automatically from the singleton sync worker and is also available as a shipped CLI.
  - Once complete, every later run is a cheap no-op. There is no permanent daemon.
- **L6. Slim the index once, guarded.** The slim runs only when `tool_calls` exists and free disk is at least twice the estimated slim size. Otherwise it logs the reclaimable bytes and skips.
  - Under the sync lock, ATTACH-copy `exchanges` and the vector data into a fresh schema, dropping machine-session rows.
  - Verify row counts, `MAX(timestamp)` and a sample search.
  - Swap the new file in atomically and keep `db.sqlite.bak`.
  - Remove the `.bak` only after a later run verifies the new database.
  - Never VACUUM the old file.
  - Never use `index-cli rebuild`, because it drops files that exist only in the archive.
- **L7. Lossless compressed archive.**
  - Compress each archive `.jsonl` to `.jsonl.zst` once it is no longer live.
  - Remove a raw file only after it has been compressed, stream-decompressed, and its SHA-256 has matched, with a line appended to `manifest.jsonl`.
  - Process one file at a time.
  - `zstd -d` restores byte-identical originals.
  - Summaries (`-summary.txt`) stay uncompressed.
  - `exchanges.archive_path` keeps the logical `.jsonl` path.
- **L8. Transparent readers.** All archive readers go through one helper that tries `.jsonl` and then `.jsonl.zst`, and handles nested `subagents/` paths. The readers today are `mcp-server.ts:295`, `show-cli.ts:49`, `search.ts:172`, `repair-cli.ts:66`, `parser.ts:33`, `codex-parser.ts:28` and `indexer.ts:20`.
- **L9. Exclude machine sessions.**
  - Grader, judge, validator and scratch/test sessions are never archived or indexed going forward. They are defined by a configurable exclude list with shipped defaults.
  - Machine sessions already archived are removed from the index, then compressed and kept, tagged as machine (D2).
  - Brain and worker sessions are kept and tagged by provenance.
- **L10. Archive subagent transcripts.**
  - Archive the current-format subagent transcripts (`<proj>/<session>/subagents/*.jsonl`): compressed, not indexed.
  - An exclude toggle can turn this off (D4).
- **L11. Optional backup mirror.**
  - A user-chosen directory set in config, off by default.
  - Each verified `.zst` is mirrored and its checksum verified there.
  - If the mirror is unavailable, log a warning and continue (D3). No remote.
- **L12. One-time backfill.** Inside `maintain`, index question→answer pairs and provenance for past sessions, using the archive. Nothing is deleted.
- **L13. README.** Add an episodic-memory section covering:
  - what is stored, and what is excluded;
  - the compressed archive, and how to restore an original;
  - the backup option;
  - the CLI.
- **L14. Verification.**
  - Fixture-based tests for each requirement, including the negative cases:
    - low disk, so the slim must refuse;
    - a checksum mismatch, so the raw file must never be removed;
    - a live file, so it must not be compressed;
    - idempotence.
  - Run the full suites.
  - A final passing Fable review before commit.
  - The operator's first real run is done by hand, with the operator's go.

- **L15. Staged rollout.** The operator asked: "we are trying it locally on my box first and then including the tooling on ironclaude, right?" The order is:
  1. Build and test in the repo against fixtures only. No push or release.
  2. Run on the operator's box by hand: `maintain --dry-run`, review the result, then a real run with the operator's go. Afterwards verify search and reads, and keep the `.bak` until the operator signs off.
  3. Turn on the automatic run on the operator's box.
  4. Ship to other users through the normal commit flow, with an explicit go for the release push.

  The automatic run inside the sync worker is controlled by a config switch. It ships **on** by default for new installs. On the operator's machine it must stay off until step 2 is done. This matters because Claude runs the episodic-memory MCP from the repo `worker/` directory, so new code can take effect locally without a release. The switch's mechanism and default are settled during planning from verified config paths.

- **L16. Decisions from plan grounding (operator, verbatim):**

  | Question | Answer |
  |---|---|
  | Auto-run | "On for all, opt me out (Rec.)" |
  | Security-review and model-probe sessions | "Yes, machine (Rec.)" |
  | roleplaying-agents work-chunk sessions | "Archive, don't index (Rec.)" |
  | Backfill | "Yes, acceptable (Rec.)" |

  What this means:
  - **Auto-run.** `autoRun` defaults to on for every install, including already-bloated ones, and the guards still apply. The operator's machine is opted out by an `autoRun=off` setting, written before the new code is deployed there (deployment means refreshing the plugin cache). It is switched back on only after the operator's manual dry run and real run.
  - **Machine sessions.** Automated security-review sessions and model-probe sessions are added to the default machine exclude rules.
  - **Work-chunk sessions.** These are archived (compressed) but not indexed.
  - **Backfill.** It may replace superseded index rows. The archive is never modified by backfill.

## Out of scope

- Lossy truncation or distillation of the archive.
- Any GitHub or remote backup.
- Pruning operator sessions.
- Fixing the GBTW grader-prompt snowball. It is queued separately.
