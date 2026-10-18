# Validation of Codex's 2026-10-04..06 Work Requirements

> **Created:** 2026-10-06
> **Status:** Operator-approved. Decisions are quoted below.

## Operator directives

- "I had codex take a look at stuff when you were out. Please take a look at the current state of things."
- On the handoff loop, Codex reported: "source-complete and staged… did an Astra review and came back solid." The operator then said: "Please take a look and validate the changes for your next loop."
- "It had done some loops before that too."
- **Reviewer:** "Fable (Rec.)", chosen for the independent adversarial review.
- **Scope:** "All Codex work (Rec.)". Validate the committed loops and the staged handoff work. Fixes become separate loops.
- **Standing rules:**
  - One IronClaude for Claude and Codex.
  - No push.
  - No commits by the agent.
  - No live worker operations, no restarts, no configuration edits during validation.

## What is under validation

1. **Committed `55ba8b3..a3d400b`** (two commits):
   - `docs/plans/2026-10-04-codex-hook-corrections-*`, R1–R10;
   - `docs/plans/2026-10-04-codex-hook-boundary-safeguards.*`;
   - `docs/plans/2026-10-05-gbtw-evaluation-warnings-*`;
   - the workspace bundle rebuild and the Codex cachebuster bump.

   Codex's own records:
   - adversarial review: `docs/reviews/2026-10-04-codex-unpushed-adversarial-review.md`;
   - validation: `docs/reviews/2026-10-04-codex-hook-corrections-validation.md`.
2. **Staged and uncommitted: automatic Claude↔Codex worker handoff.**
   - Requirements: `docs/plans/2026-10-05-worker-provider-handoff-requirements.md` (R1–R9).
   - Designs: `-design.md` and `2026-10-06-worker-provider-handoff-continuation-design.md`.
   - Plans: `.md` and `.plan.json` for both.
   - Size: about 9.7k lines across `commander/` and `worker/mcp-servers/workspace-manager/`. The workspace bundles have not been rebuilt for the new source.

## Requirements

- **V1. Checks.** Run each loop's own required checks plus the full suites. Record exact commands, exit codes and summary lines in a results document:
  - `make test-hooks`;
  - the workspace-manager vitest;
  - the TypeScript compiler (no emit);
  - the full Commander pytest;
  - the handoff loop's required pytest selection;
  - the focused workspace handoff/CLI vitest.

  Report known limitations honestly, including the known `onTaskUpdate` RPC timeout.
- **V2. Independent review.** A Fable adversarial review, blind to Codex's review verdicts and reasoning, covering:
  - the committed diff against its requirements;
  - the staged handoff diff against R1–R9.

  It must verify every material finding from source. It must also cover these risks:
  - process-stop safety (R4);
  - ownership compare-and-swap and rebinding (R5);
  - quota-evidence false triggers and switching back and forth (R7);
  - PM progress and review attribution (R3);
  - preserving the worktree (R2);
  - parity across clients;
  - bundle and source agreement.

  The review is written to `docs/reviews/2026-10-06-codex-work-fable-review.md`.
- **V3. Findings report.** One summary with:
  - a verdict per loop: CLEAN or HAS-FINDINGS;
  - findings that the orchestrator has verified;
  - a recommendation for each item: commit, fix in a separate loop, or backlog;
  - deploy or restart prerequisites, such as rebuilding the bundles before any Commander restart.
- **V4. No product changes.** The only files this loop writes are the results, review and report documents. Any defect fix is a separate loop.

## Out of scope

- Fixing anything found.
- Committing the staged handoff work.
- Rebuilding the bundles.
- Deploying.
- Live worker handoff tests.
- L3 and L2b-3.
