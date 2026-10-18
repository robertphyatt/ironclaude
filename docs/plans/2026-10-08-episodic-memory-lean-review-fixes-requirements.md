# Episodic Memory Lean — Final-Review Fixes: Requirements

> **Created:** 2026-10-08
> **Parent effort:** `docs/plans/2026-10-07-episodic-memory-lean-requirements.md` (L1–L16)
> **Source:** Fable final end-of-work review, verdict FAIL (narrow): one MATERIAL finding (M1) plus observations 2, 3, 5.

## Operator decisions (authority)

- Fix scope, chosen 2026-10-07: **"M1 + 2 + 5 + guard 3 (Rec.)"**. Fix M1; make a damaged `.zst` fail fast instead of hanging; make `--only=backfill` refuse cleanly on a never-slimmed index; and never let sync overwrite a larger archive copy with a smaller rewritten source — keep the old copy alongside (lossless).
- Design Section 1 (M1, 2, 5) approved: "Yes (Rec.)".
- Design Section 2 (guard 3) approved: "Yes (Rec.)".
- Design amendment approved 2026-10-09, "Approve (Rec.)": `--only=backfill` refuses on a never-slimmed index in dry and real runs; a full `maintain --dry-run` still reports backfill as would-run after the slim; sync copies the new file to a temp file before moving the larger old copy aside.
- Standing constraints from the parent effort still hold: lossless, one IronClaude for Claude and Codex, nothing name-specific, the operator's local opt-out (`{"maintain":{"autoRun":false}}`) stays in place, no runs against real data, and a final passing Fable review before the operator commits.

## Requirements

- **R1 (M1).** A partial `db.sqlite.new` (and its `-wal`/`-shm`) left by an interrupted slim must never cause a refusal. A real run removes it before measuring free disk. A dry run changes nothing and counts its bytes as free space, saying so in the report.
- **R2 (item 2).** `parseConversation` and `parseCodexConversation` must reject with an error when the archive stream fails (for example a damaged `.zst`), never hang and never return a partial parse.
- **R3 (item 2).** The voice backfill must treat a session whose transcript cannot be parsed as unreadable: record it in the report, advance the cursor, and leave that session's index rows untouched.
- **R4 (item 5).** The voice backfill must skip, without opening the index for writing or migrating it, when the index still has the legacy `tool_calls` table — in dry and real runs alike. Exception (operator-approved 2026-10-09): a full `maintain --dry-run` reports backfill as would-run after the slim, because the full run slims first.
- **R5 (guard 3, raw).** When sync would replace a raw archive copy that is larger than the new source, the old copy is renamed to `<session>.jsonl.superseded-<oldMtimeMs>` and kept; the new copy is written to a temp file before that rename, so the session path is never left empty. If that name already exists, the copy fails with an error and the archive is unchanged.
- **R6 (guard 3, compacted).** When compaction would replace an existing `.zst` (outside the crash-resume case) whose decoded size is larger than the new raw, the old `.zst` is first renamed to `<session>.jsonl.superseded-<oldMtimeMs>.zst` and kept, with a manifest line so the backup mirror includes it. An old `.zst` that fails to decode is kept the same way. Normal growth (new raw at least as large) replaces it as before.
- **R7.** Superseded copies are compacted losslessly (manifest line) and never indexed, summarized or listed by verify.
- **R8.** README documents superseded copies, the known limitation (old index rows open the current file in `read`), and how to restore.
