# Interactive Conflict Q&A (M7) — M7a Design

> **Created:** 2026-08-27
> **Status:** Design Complete (M7a — design only, NO code)
> **Roadmap:** docs/plans/2026-08-24-post-v117-roadmap.md (M7, split M7a/M7b/M7c)
> **Deps:** M2, M5, M6 (all shipped, committed `d55d152`).

## Summary

When `/reconcile` or `/close-out` replays the reviewed frozen work onto a drifted
integration target and the rebase **conflicts**, today the system refuses and preserves the
worktree (`rebase-paused-conflict` / `rebase-recovery-repair-required` → "preserved for automated
resolution (M7). Not an operator task."). M7 makes that resolution real: it auto-resolves every
mechanically-recoverable hunk and, only for the genuinely-ambiguous residual, resolves each hunk
**synchronously inside the verb** by asking the operator for plain-language prose guidance — never
handing the operator a raw conflict to fix by hand. The operator's live per-hunk confirmation **is
the review**; the resolved result lands on LOCAL main via a fresh commit authority those
confirmations authorize (never pushes). Aborting at any point restores the frozen reviewed commit
intact.

This document (M7a) fixes the **taxonomy, interaction contract, and abort/rollback semantics**. It
writes no code. **M7b** implements detect + classify + plain-language surface (refuse/pause stays
the fallback and delivers value alone). **M7c** implements the interactive apply + per-hunk confirm
+ land-via-fresh-authority + abort.

## Operator constraints (authority)

- **C1 — Conflicts are never the operator's to resolve manually.** Auto-resolve mechanically; ask
  the operator only PROSE guidance on genuinely ambiguous spots (rare); NEVER "go fix the conflict
  and re-run." The system, not the operator, edits files.
- **C2 — Never-lose-work is paramount.** The frozen reviewed commit is preserved until a
  fully-confirmed resolution lands; abort restores it exactly; the integration target is never
  corrupted mid-resolution.
- **C3 — Never push.** Reconcile/close-out land on LOCAL main only; resolution never pushes.
- **C4 — Never land unreviewed bytes silently.** A resolution changes the reviewed content, so the
  `cumulativeBinaryEffect` equality gate rejects a silent auto-integrate. The operator's live
  per-hunk confirmation is the review of record; the resolved result lands through the fresh-commit
  `isRepair` channel that confirmation authorizes. **Two obligations this places on M7c (named here,
  not solved):**
  - **(a) Non-forgeable confirmation authority.** Today the `isRepair` channel is "the operator
    drives a fresh commit authority." M7 substitutes "per-hunk confirmations authorize the landing" —
    a NEW authority-minting path that does not exist yet. It is the same evidence-light-authority
    shape that produced M6's C1 critical (an authority whose only proof the agent could manufacture).
    M7c MUST prove the resolved-commit authority is bound to a REAL operator confirmation,
    non-forgeable by the agent, and consistent with the human-intent/evidence model — verified against
    `finalizeAttestedCandidate`'s current authority binding. This is the crux of M7c, not a detail.
  - **(b) Interactive-operator precondition (headless fallback).** "Confirmation is the review"
    presumes a human at the keyboard answering `AskUserQuestion`. In a headless / autonomous PM loop /
    Commander-worker context there is no human to confirm; auto-confirming would land unreviewed bytes
    — the exact C4 breach this design exists to prevent. Therefore interactive resolution REQUIRES an
    interactive operator; in any non-interactive context the verb MUST fall back to preserve-and-defer
    (today's floor) and MUST NEVER auto-confirm a resolution.
- **C5 — Do not disturb the M2/M5/M6-proven lanes.** The ff-only / equality-gate / preserve-and-defer
  floor stays the fallback; M7 adds resolution on top without weakening it.

## Architecture

M7 extends the existing rebase-recovery state machine in `integration.ts`
(`classifyRebaseState` :1616, `recoverRebaseInProgress` :1551, `reconcileFinalization` :1691) — it
adds no parallel machine. Today `rebase-paused-conflict` (unmerged paths, `--diff-filter=U`) throws
and preserves; M7 turns that terminal refusal into a resumable, per-hunk, operator-guided
resolution whose only landing path is the already-proven fresh-commit `isRepair` channel
(`finalizeAttestedCandidate`), so no new integration path bypasses the equality gate.

**Because an MCP tool call is a single request/response, "synchronous inside the verb" is
skill-orchestrated**, mirroring the M6 `close_out_worktree` handler-orchestrated recovery: the MCP
tool detects the conflict and returns a structured `conflict-requires-guidance` result carrying the
classified ambiguous hunks; the verb SKILL (`reconcile` / `close-out`) surfaces each via
`AskUserQuestion`, then calls a follow-up apply-resolution MCP tool with the operator's prose;
repeat until every hunk is confirmed; then the verb completes. From the operator's side it is one
synchronous verb invocation in one session.

## Components (the conflict taxonomy)

Classification is **per hunk** of the integration rebase (reviewed frozen work replayed onto the
drifted target). Three classes:

1. **Auto-resolved — never surfaced.** git's own 3-way rebase already merges non-overlapping hunks;
   identical-effect hunks (the reviewed change and the target change are byte-identical); and, later,
   rerere-style known resolutions. When the continued rebase's `cumulativeBinaryEffect` equals the
   reviewed effect, it integrates directly through today's equality-gate path — **unchanged**. No
   operator touch, no M7 interaction.

2. **Ambiguous — requires guidance (rare, surfaced).** The residual git cannot merge cleanly:
   - overlapping edits to the same lines (both reviewed work and target changed them);
   - delete/modify (one side deleted the file, the other modified it);
   - rename/modify (one side renamed, the other edited the old path);
   - add/add with differing content at the same path.
   Each such hunk enters the prose Q&A.

3. **Unsafe — deferred, not resolved in M7.** Binary conflicts (no line-level prose resolution),
   and any hunk the operator declines. Outcome: preserve-and-defer (today's floor) or abort — never
   a guessed resolution.

M7b delivers classes 1 and 3 as value on its own: it auto-resolves class 1 and, for class 2/3,
surfaces a plain-language description of the conflict while the outcome stays refuse/pause. M7c adds
the class-2 interactive resolution.

## Data flow (the interaction contract, per ambiguous hunk)

1. The verb's skill presents the hunk in **plain language**: file path, line range, and a
   two-sided summary — "your reviewed work does A here; the integration target does B."
2. The operator gives **prose guidance**: keep-mine, take-target, or a prose description of the
   intended merged result. The operator NEVER hand-edits files (C1).
3. **The system applies the guidance** — producing a candidate resolved hunk — and **shows it back**
   to the operator.
4. The operator **confirms** (this confirmation is the review of record, C4), **revises** (return to
   step 2 with a corrected prose), or **aborts** (see Error Handling).
5. Repeat for each ambiguous hunk, oldest-first, one at a time.
6. When every ambiguous hunk is confirmed, the rebase continues to completion. The resolved result
   is a NEW content state landed on LOCAL main via a fresh commit authority the confirmations
   authorize (the `isRepair` fresh-commit channel), which the equality gate already permits. Never
   pushes (C3).

## Error handling (abort / rollback semantics)

- **Abort at any prompt.** The operator may abort at any Q&A step → `rebase --abort` (existing
  `recoverRebaseInProgress('abort')` path) → the frozen pre-rebase reviewed commit is restored
  exactly, the integration target is unchanged, and the verb returns "aborted; nothing landed;
  reviewed work preserved." (C2)
- **No partial resolution.** A conflict is all-confirmed-or-aborted; M7 never lands a subset of
  resolved hunks and defers the rest (that would split reviewed and unreviewed content
  unrecoverably). A hunk the operator cannot decide → abort, or preserve-and-defer the whole
  conflict. (C2, YAGNI)
- **Frozen commit is the invariant.** Until the fully-confirmed resolution lands, the frozen
  reviewed commit exists on its durable ref; every intermediate step is recoverable to it.
- **Session interruption mid-Q&A** leaves the preserved paused-rebase state (today's floor); a
  re-run resumes classification from the same worktree. No work is lost.
- **Malformed / offline / proof-failure** at landing preserves the worktree and surfaces the exact
  blocker (reuses the existing preserve-and-throw discipline) — never a silent integrate.

## Testing strategy (for M7b/M7c, recorded here)

- **Taxonomy classification:** seed each conflict class (overlap, delete/modify, rename/modify,
  add/add, binary, non-overlapping, identical-effect) and assert the class the detector assigns; a
  non-overlapping or identical-effect hunk must NOT surface.
- **Auto path unchanged:** an identical-effect / non-overlapping rebase still integrates via the
  equality-gate path with no operator interaction (regression guard on the M5/M6 lanes).
- **Interactive resolve (M7c):** a seeded overlap conflict → prose "take target" / "keep mine" /
  "combine" each produces the asserted resolved bytes; the confirmed result lands via the fresh
  authority; local main advances; NO push; the equality gate is satisfied by the fresh-commit
  channel, not bypassed.
- **Abort:** abort at the first prompt → frozen commit restored exactly (byte-identical),
  target unchanged, nothing landed.
- **No-partial:** confirming hunk 1 then aborting at hunk 2 → nothing landed, frozen restored.
- **Never-push falsifier:** assert no `git push` is reachable from any resolution path.
- **Never-manual falsifier:** assert the operator is never returned a raw conflict / "resolve and
  re-run" message on a class-2 conflict once M7c is live.

## Implementation notes (the M7b → M7c split)

- **M7b — detect + surface (refuse/pause stays the outcome; delivers value alone).**
  - Add per-hunk classification (class 1/2/3) over the paused-rebase worktree, extending
    `classifyRebaseState`.
  - Emit a structured `conflict-requires-guidance` result (the classified class-2 hunks, each with
    the plain-language two-sided summary) from `reconcileFinalization` / the verb handlers — but the
    verb still refuses/pauses (no apply yet). This alone replaces the raw "unmerged paths: …" throw
    with a plain-language, operator-legible surface.
  - Auto-resolve class 1 (already largely covered by `continue` + the equality gate); make the
    class-1/class-2 boundary explicit and tested.
- **M7c — interactive resolve + abort.**
  - Add an apply-resolution MCP tool: input = (hunk id, operator prose / structured choice); it
    produces the candidate resolved hunk, stages it in the paused rebase, and returns it for
    confirmation. A confirm advances; a revise re-applies; an abort runs `rebase --abort`.
  - On all-confirmed, drive the fresh-commit `isRepair` landing (existing `finalizeAttestedCandidate`
    channel). **The landing authority MUST be bound to real, non-forgeable operator confirmations
    (C4a) — this is M7c's hardest proof; design the confirmation-authority binding before the apply
    tool, and treat it with the same rigor as M6's evidence-light close-out authority.**
  - **Gate the entire interactive path on an interactive-operator precondition (C4b): a headless /
    autonomous / worker context falls back to preserve-and-defer and never auto-confirms.** Add a
    negative test proving a non-interactive caller cannot land a resolution.
  - Wire the `reconcile` and `close-out` SKILLS to orchestrate the `AskUserQuestion` loop between the
    detect tool and the apply tool.
- **Do NOT** modify `resolveEffectiveCheckout`, the push lanes, or the M5/M6 commit/close-out landing
  proofs. M7 only turns the class-2 refusal into a resumable resolution; every other path is
  byte-unchanged. The ff-only / preserve-and-defer floor remains the fallback when the operator
  aborts or defers.

## Non-goals (M7a scope=hold)

- No AI that guesses a resolution without operator confirmation.
- No partial / per-hunk deferred resolution (all-or-abort).
- No async/Slack medium (synchronous in-verb chosen); a Commander-orchestrated async variant is a
  possible future milestone, out of scope here.
- No separate/independent review pass (confirmation is the review).
- No conflict resolution for the pure `/push` lane (push is human-only, ff-only + lease; it never
  rebases).
