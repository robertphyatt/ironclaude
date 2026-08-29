# M1 — Never-Lose-Work Completeness — Requirements (operator-approved)

> **Created:** 2026-08-24
> **Status:** Approved
> **Scope mode:** hold. Approach **B** (TS tombstone guard + reaper recognition), operator-chosen.

## Problem (corrected framing)

The v1.1.7 backstop guards `recycleFinalized`/`releaseFinalized` against discarding a push-pending
obligation, but a THIRD teardown primitive — `workspace-service.ts tombstoneTerminalAssignment`
(`:698`) — has no such guard. Its integrated-row branch (`:713-728`) proves integration evidence but
never checks `disposition`, so an integrated push-pending row (the durable state a failed
`commit-and-push` push leaves) is `removeWorktree`'d (`:733`) + branch-deleted (`:736`) + `cleaned`
(`:739`), dropping the push obligation.

**Severity (precise):** this drops a **push obligation**, not committed work — the integrated commits
are already on local main; what's lost is the disposition (record that a push is owed) + the
auto-recovery path (recovery needs the worktree, now gone). A manual `/push` could still complete it.
Reachable via `cleanupWorkspace` (`:755`, on-demand) and the daemon reaper `reapLeakedAssignment`
(`:804`; integrated rows skip rescue → straight to tombstone). Trigger: a session dies right after a
failed push, then the reaper runs.

**Reaper reality (verified, corrects the "crash-loop" premise):** a bare throw in tombstone is caught
per-candidate by the reaper (`main.py:514`) → the row is preserved (never-lose-work holds) — but the
same row logs `"release failed"` + `errors++` **every maintenance cycle** until the push resolves.
Silent-loss becomes loud-stuck spam, not a crash.

## Requirements

R1. **TS tombstone guard.** `tombstoneTerminalAssignment`'s integrated-row branch MUST refuse-and-preserve
    when the row carries a push-pending disposition: throw BEFORE `removeWorktree` (`:733`) so the
    worktree, branch, and row survive. Place it after the integrated-evidence proof (`:721-728`), before
    `:730`.

R2. **Shared disposition predicate.** Export from `integration.ts` a predicate the tombstone guard can
    call — `decodePushDisposition` (private at `:156`) exported, or a thin `hasPushPendingObligation`
    wrapper. It matches phases `push-pending` / `push-succeeded` / `push-failed`.

R3. **Reaper recognition (no spam).** The daemon reaper MUST treat an integrated push-pending row as
    PROTECTED, not reaped: add the check to `_is_protected` (`main.py:288`), which already receives the
    full assignment dict (`SELECT *`, so `disposition` is present) and errs toward protect. A small
    Python `_has_push_pending(disposition_json)` helper mirrors the TS phase set. Surface it ONCE
    (a one-time per-guid WARNING that the row is preserved pending a push) rather than the per-cycle
    "release failed" spam.

R4. **I-1 Commander graceful handling.** `finalizeCommanderLocalCommit`'s two `disposeFinalized` sites
    (`integration.ts` ~`:1198`/`:1204`) MUST guard inline: when `decodePushDisposition(local.assignment.disposition)`
    is truthy, return `{ state: 'integrated-local', … }`; otherwise `disposeFinalized(db, local.repositoryPath,
    local.assignment, input.dispose)` (unchanged). It MUST NOT be replaced by `finishLocalIntegration`,
    which hardcodes recycle and would break the `dispose: 'release'` close-out teardown path.

R5. **Falsifiable tests.**
    - TS: an integrated push-pending row survives `tombstoneTerminalAssignment` (throws; row + disposition
      + worktree + `integration_records` preserved). Pre-guard it is discarded (RED fires). Reuse
      `seedIntegratedPushPending`.
    - Python: the reaper `_is_protected` returns True (protected) for an integrated push-pending assignment,
      and the sweep does NOT reap it. Pre-change it is reaped/errored.
    - I-1: a Commander finalize on a push-pending row returns `integrated-local` and preserves the row;
      pre-guard it throws (v1.1.7 backstop) or discards.
    Full workspace-manager vitest suite + relevant commander pytest stay green.

R6. **Release v1.1.8.** Bump the 5 declared version files + `test_version_consistency.py` green +
    CHANGELOG + README.

## Non-goals (named so they are not rediscovered)

- **Anchoring the push obligation onto a durable recovery ref + a resume-push path** so teardown can
  safely PROCEED (mirroring `ensureDurableRecoveryAnchor` for abandoned rows). Bigger than M1; needs a
  push-resume mechanism. Its own loop.
- Any change to the recycle/release backstops (shipped in v1.1.7), the reconcile/`/commit` active
  branches (v1.1.7), or the recovery-ref machinery.

## Invariant to confirm in the plan
push-pending only ever rides `integrated` rows (`markIntegrated` is the sole setter and sets
`lifecycle_status='integrated'` atomically) — so the guard belongs in tombstone's integrated branch and
an abandoned row cannot smuggle one past. Verify both push-pending setters in `integration.ts` set
integrated.
