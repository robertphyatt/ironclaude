# Archive Abandoned Opus-Backed Checks Design

> **Created:** 2026-10-17
> **Status:** Design Complete

Loop: light — doc-only (no code, guard, schema, DB or git-workflow change); 1 doc edited, 2 untracked docs deleted, ~10 lines.

## Summary

On 2026-10-16 the per-call difficulty tiering design replaced the "Opus-backed checks keep Opus" design, which was never executed. Its three files still sit untracked under the gitignored `docs/plans/`, and the design doc still reads "Status: Design Complete". The operator asked to keep the design as a record of the rejected option, with a note on why it was dropped, and to remove the plan files (2026-10-17: "Proceed as suggested"). This matches an earlier operator preference (2026-10-16) to keep the record of a decision in the commit.

## Requirements

- **R1.** In `docs/plans/2026-10-16-opus-backed-checks-keep-opus-design.md`, change the Status line to `Abandoned — superseded by docs/plans/2026-10-16-per-call-difficulty-tiering-design.md (never executed)`. Then add a `## Why abandoned` section directly under it. The section says three things:
  - Keeping Opus as the backup for the five checks would overuse Opus. The operator asked to tier each call by the difficulty of its task instead: "I don't want to overuse opus when times haiku should be better suited for the task".
  - Under the tiering, those checks use the Haiku class first and fall back one class up to the Sonnet class. Only approve_plan and kill_worker stay on the Opus class.
  - The doc is kept so that "why not keep Opus as the backup?" has a recorded answer.

  Leave the rest of the document unchanged.
- **R2.** Delete the untracked `docs/plans/2026-10-16-opus-backed-checks-keep-opus.md` and `docs/plans/2026-10-16-opus-backed-checks-keep-opus.plan.json`.
- **R3.** Stage the annotated design doc and this loop's own plan docs with `git add -f`. Commit them; do not push.
- **Non-goals:** no change to any other doc, code, or memory beyond the release memory's mention of these docs.

Predicted answer: yes (operator approved "Proceed as suggested").
