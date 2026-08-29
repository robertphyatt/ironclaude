# M7b — Conflict Detect + Surface — Requirements

> **Created:** 2026-08-27 · Derived from the M7a design (operator-approved) + the M7b brainstorm scope.
> Design: `docs/plans/2026-08-27-conflict-qa-m7a-design.md`. Authority: operator directives →
> M7a brainstorming decisions → this file → plan.

## Scope (M7b = detect + classify + plain-language surface ONLY)
- **R1.** A per-conflict classifier inspects a paused-conflict integration rebase worktree and, for
  each unmerged path, produces a structured, plain-language entry: the file, a two-sided summary
  ("your reviewed work does A; the integration target does B"), and its conflict class.
- **R2.** The classifier assigns each unmerged path a class from the M7a taxonomy: **class 2**
  (ambiguous — overlapping edit, delete/modify, rename/modify, add/add-differ) is surfaced; **class
  3** (binary / unresolvable-without-guidance) is surfaced as deferred. (Class 1 — non-overlapping /
  identical-effect — never reaches this path: git's own rebase merges it and `continue` integrates
  it via the equality gate.)
- **R3.** A new optional `conflicts` field on `FinalizationResult` carries the classified entries.
- **R4.** The raw `recoverRebaseInProgress` unmerged-paths throw ("Rebase recovery stopped:
  unresolved conflicts remain … Unmerged paths: …", integration.ts ~:1573/:1580) is REPLACED by a
  structured `rebase-paused-conflict` FinalizationResult carrying `conflicts`, so `/reconcile`
  (`reconcile_finalization('continue')`) and `/close-out` surface operator-legible conflicts instead
  of a raw git error.

## Hard constraints (from M7a C1–C5)
- **HC1 — outcome unchanged.** M7b does NOT apply resolutions and does NOT change any landing: the
  verb still refuses/pauses (preserve-and-defer stays the outcome). No interactive apply, no
  fresh-commit authority, no `AskUserQuestion` loop (all M7c).
- **HC2 — never-lose-work.** No mutation of the paused rebase, the frozen commit, or the target; the
  classifier is READ-ONLY over the worktree (inspection only).
- **HC3 — never push.** No push anywhere.
- **HC4 — do not disturb proven lanes.** The M2/M5/M6 reconcile/commit/commit-and-push/push/close-out
  lanes, the `cumulativeBinaryEffect` equality gate, and the ff-only/auto-continue (class-1) path stay
  byte-behaviorally unchanged. The class-1 auto path must still integrate with no surfaced conflict.

## Acceptance
- The whole workspace-manager suite green (`--testTimeout=30000`), plus new tests: each seeded
  conflict class (overlap, delete/modify, rename/modify, add/add-differ, binary) classified
  correctly; a class-1 (non-overlapping / identical-effect) rebase still auto-integrates with an
  EMPTY `conflicts` surface (regression guard on the proven lane); `/reconcile` continue on a
  conflicted rebase returns `rebase-paused-conflict` with a populated plain-language `conflicts`
  payload and NO integration (main unmoved, frozen preserved); the classifier performs no writes.
