# Validation of Codex's 2026-10-04..06 Work Design

> **Created:** 2026-10-06
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-06-codex-work-validation-requirements.md`
> **Loop:** light, checked by the reviewer. The output is documentation only. There are no product, hook or configuration writes.

## Summary

This loop independently validates everything Codex did while Claude was unavailable. That covers three committed loops: hook corrections, boundary safeguards, and GBTW warnings. It also covers the staged automatic Claude↔Codex worker handoff. The loop's output is evidence and a recommendation, not fixes.

## Approach

The validation runs in three stages:

1. **Run the checks** and record exact results.
2. **Hold an independent Fable adversarial review** of the committed diff and the staged diff. The reviewer works blind to Codex's review verdicts, and every material finding is verified against the source.
3. **Write one findings report.** The orchestrator independently confirms each material finding before it goes into the report.

**Rejected alternatives:**
- An Opus or dual review. The operator chose Fable.
- Validating only the handoff work. The operator chose all of Codex's work.

## Components

1. **Checks task.** Runs the full hook suites, the workspace-manager vitest and `tsc --noEmit`, the full Commander pytest, the handoff loop's own required pytest selection, and the focused handoff/CLI vitest.
   - Output: `docs/reviews/2026-10-06-codex-work-check-results.md`.
   - The Commander suite runs against the working tree, which includes the staged handoff source. The report says so.
2. **Review task.** The orchestrator dispatches the Fable review. It runs read-only, with the checklist from the requirements (V2).
   - Output: `docs/reviews/2026-10-06-codex-work-fable-review.md`.
3. **Report task.** Verify each material finding, grade each loop, and give the recommendations and deploy prerequisites.
   - Output: `docs/reviews/2026-10-06-codex-work-validation-report.md`.

## Error Handling

A failing check is recorded as a finding, not fixed. A review finding that the orchestrator cannot verify against the source is listed as an observation, not a finding.

## Testing Strategy

This loop adds no tests. Its evidence is the recorded check output plus the source-verified review.

**Final gate.** The operator's final-review rule applies before commits of enforcement changes. This loop produces only findings documents, and its central deliverable is itself a Fable review. Any fix loop it spawns gets its own final gate.
