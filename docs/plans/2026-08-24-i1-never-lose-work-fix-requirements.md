# I-1 Never-Lose-Work Fix — Requirements (operator-approved)

> **Created:** 2026-08-24
> **Status:** Approved
> **Scope mode:** hold (fixed scope, maximum rigor within it)

## Problem (operator-approved statement)

Two "active" finalizer branches in `worker/mcp-servers/workspace-manager/src/integration.ts`
call `recycleFinalized` **unconditionally** right after `finalizeLocalCommit`, silently
destroying a `push-pending` disposition that `finalizeLocalCommit` (via `markIntegrated`)
produces when the incoming `active` assignment carried an `integration-pending` disposition.
That is a **never-lose-work violation**: an unfulfilled push obligation is deleted with no
surface and no recovery record.

Reachable precondition (confirmed against source): a human `commit-and-push` sets its
`integration-pending` disposition (`integration.ts:1065`) and then throws at the dirty gate
(`finalizeLocalCommit:834-837`) **before** the `active → ready_for_integration` transition
(`:838`), leaving a real `active` + `integration-pending` row. A later `/reconcile` or `/commit`
on that row hits the unguarded active branch.

## Requirements

R1. **`finalizeReconcile` active branch** (`~1144-1148`) MUST preserve a push-pending
    obligation instead of recycling it: after `finalizeLocalCommit`, if
    `decodePushDisposition(local.assignment.disposition)` is truthy, return
    `{ state: 'integrated-local', … }` (mirroring the repair branch at `:1154-1158` and the
    reconcile `SKILL.md` contract), otherwise recycle → `{ state: 'reconciled', … }`.

R2. **`finalizeDirectAuthority` `/commit` active branch** (`~1070-1073`) MUST use the vetted
    helper `finishLocalIntegration(db, local)` (Approach A) rather than an unconditional
    recycle. This preserves the obligation (`integrated-local`) when present and keeps today's
    `{ state: 'cleaned', … }` otherwise. (Chosen over an inline copy because the file's
    convention is: `LocalFinalization` + `'cleaned'` success ⇒ use the helper; inline only where
    the helper's shape does not fit.)

R3. **Option C — structural never-lose-work backstop.** `recycleFinalized` (after its fresh
    re-read at `:573`) and `releaseFinalized` (after its fresh re-read at `:620`) MUST throw when
    the fresh row still carries a `push-pending` disposition. Every correct caller nulls or guards
    the disposition before recycling, so the throw fires only on a regression. This covers the
    Commander twin (`finalizeCommanderLocalCommit` `disposeFinalized` at `:1198`/`:1204`) and all
    future callers without a reachability proof.

R4. **Falsifiable tests.** Add RED tests that, evaluated at the post-merge state, fail if any
    guard is deleted:
    - reconcile active branch: `active` + `integration-pending` → `finalizeReconcile` → assert
      `state === 'integrated-local'` AND the row is `integrated` with the `push-pending`
      disposition **preserved** and the worktree not recycled.
    - `/commit` active branch: same precondition → `finalizeDirectAuthority` (operation `commit`)
      → same assertions (`integrated-local` + preserved).
    - Option C backstop: a direct `recycleFinalized` (and `releaseFinalized`) on an integrated
      `push-pending` row throws.
    Template: the existing repair preserve test at `integration-cases.ts:375`. The full
    workspace-manager vitest suite MUST stay green.

R5. **O-2 (Fable observation).** Correct the stale source-line references in the comment at
    `git-authority.test.ts:1114-1117` (`:614/:613/:563`) to the current lines verified against
    `git-authority.ts` (single-use gate `:640`; reconcile push-exclusion `:589`; the third ref
    pinned during planning by reading the file).

R6. **O-3/O-4 (Fable observations).** Tighten the two least-actionable `finalizeReconcile`
    refusal messages for clarity (author-chosen wording; error strings, not a contract).

R7. **Release v1.1.7.** Bump the release version across the 5 files enforced by
    `commander/tests/test_version_consistency.py` (`commander/pyproject.toml`,
    `worker/.claude-plugin/plugin.json`, `worker/.codex-plugin/plugin.json` release part of the
    `+codex.<ts>` cachebuster, `worker/mcp-servers/workspace-manager/package.json`,
    `.claude-plugin/marketplace.json`), keep that test green, and add a `CHANGELOG.md` entry and a
    README "What's New in v1.1.7" section.

## Non-goals (held out of scope)

- Gracefully handling `finalizeCommanderLocalCommit`'s two sites (1198/1204) beyond the Option C
  throw — recorded as backlog; the backstop turns silent-delete into a fail-loud throw there.
- Any behavior change to the push lanes, the reconcile happy path, or the repair branches.
- The reconcile conflict Q&A, `/close-out`, and the other remaining epic loops.

## Accepted behavioral consequence (record for the reviewer)

Preserving the obligation leaves the row `integrated` + `push-pending`, worktree **not**
recycled. A subsequent `/commit` on that assignment fails ("Only active or ready repair
assignment can begin finalization") until the obligation resolves. The recovery path exists and
is correct: `reconcileFinalization`'s integrated branch (`~1375-1411`) probes the remote, nulls
the disposition when the remote proves the candidate, then recycles; `/push` also consumes it.
This converts silent loss into a temporarily-blocked worktree with a documented recovery tool —
not a new bug.
