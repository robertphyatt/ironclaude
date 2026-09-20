# Interrupted-CAS ancestry recovery + proportionality cleanups — Design

> **Created:** 2026-09-21
> **Status:** Design Complete
> **Type:** Corrective for the fresh full-corpus adversarial review of v1.1.11 (`8ed4807`): 1 verified MATERIAL (I1) + 4 SAFE proportionality cleanups from the over-engineering review. Folds into unpushed v1.1.11.
> **Scope mode:** hold.

## Summary (root cause + evidence)

**I1 (MATERIAL).** The interrupted-CAS recovery uses **exact equality** (`currentTarget === candidate`) to decide "already landed", but its own comment (`integration.ts:2204-2205`) states the intended rule: *"Target reachability is sufficient durable proof."* When a genuinely-landed CAS's target later **advances** past the candidate (an operator commit on the target branch, or the daemon's own `merge-then-reap` CAS at `workspace-service.ts:1343`, which takes no `integration_locks`), the row escapes both recovery verbs and is stranded:
- Post-CAS crash keeps the lock (`integration.ts:947-949` releases only when `!targetAdvanced || integrationRecorded`; the CAS at `:920` sets `targetAdvanced`, and a throw at `verifyPrimaryAfterFastForward` `:929` precedes `markIntegrated` `:933`), leaving a durable `ready_for_integration` row: candidate ref = the commit now on the target, worktree HEAD = candidate, lock held.
- reconcile's crash branch refuses: `:2208` `if (currentTarget !== candidate) throw 'not the exact candidate'`.
- `reopen_for_edit`'s interrupted-CAS guard (`:1955-1964`) tests `currentTarget === landedCandidate`, so once the target advances it no longer fires → reopen proceeds → resets to frozen, clears refs/lock, row → `active`. The daemon + SHIP wizard actively steer the Brain to `reopen_for_edit` after 3 failures. Work is not lost (candidate is on the target + a recovery ref), but the assignment is stranded in a permanently-failing, misleading state (re-commit rebases the already-upstream patches away → `cumulativeBinaryEffect` empty ≠ reviewed → fails forever), violating the guard's own "can never strand an already-integrated assignment" claim.

**Cleanups (from the over-engineering review — verdict PROPORTIONATE + 5 SAFE items; folding in 1-4, keeping item 5).** All SAFE, no property lost.

## Components

### C1 (I1) — ancestry-based "already landed" detection (`integration.ts`)

The genuine interrupted-CAS proof chain is: candidate ref resolves (this assignment's) + candidate is reachable from the current target (it landed) + the exact integration lock at `expectedTarget` is held + the effect proof (`expectedTarget..candidate` == reviewed) holds. Equality is the special case where the target has not advanced.

**Reconcile crash branch (`:2206-2242`):**
- Replace `:2208` `if (currentTarget !== candidate) throw` with `if (!isAncestor(exact.primaryCheckoutPath, candidate, currentTarget)) throw new Error('Crash reconciliation candidate did not land on the target; preserving worktree')`. (`isAncestor` returns false for a missing/unresolvable ref — `git.ts:565` — so an unresolvable target correctly refuses here.)
- Split the primary-checkout repair, which is only valid when the target is exactly the candidate: wrap the `verifyPrimaryAfterFastForward`/`repairPrimaryCheckoutAfterInterruptedCas` block (`:2215-2219`) in `if (currentTarget === candidate) { … }`. When the target has advanced (`currentTarget !== candidate` but `isAncestor` true), SKIP it — both helpers throw on `currentTarget !== candidate` (`:653`, `:823`), and the advancing operation owns the primary/ref consistency; recovery must not touch them. The remaining steps are identical and correct for both cases: `requireExactIntegrationLock(expectedTarget)`, the effect proof (`:2222-2223`, uses `expectedTarget..candidate` — fixed commits, unaffected by advancement), `markIntegrated(candidate, ref)` (never moves `ref` — `:686-700`), and `recycleFinalized` (already ancestry-based at `:717`).

**reopenForEdit guard (`:1951-1964`):** replace the equality test with ancestry. Since `isAncestor` already returns false for an unresolvable target, this **subsumes** lineage-118's obs3 try/catch — simplify the guard to:
```ts
let landedCandidate: string | undefined;
try {
  landedCandidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
} catch { /* no candidate ref: not an interrupted-CAS row */ }
if (landedCandidate) {
  const landed = isAncestor(exact.primaryCheckoutPath, landedCandidate, targetRef(assignment));
  const lockHeld = db.prepare('SELECT 1 FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
    .get(assignment.repository_identity, assignment.workspace_guid) !== undefined;
  if (landed && lockHeld) {
    throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');
  }
}
```
(`isAncestor` is already imported in integration.ts.)

**Tests (`integration-cases.ts`, recovery part):**
- **landed-then-advanced reconcile:** seed the interrupted-CAS shape (as the existing crash/advancedWithoutRecord seeds do — candidate committed, ff-merged to target, candidate ref set, lock acquired at the pre-CAS `expectedTarget`, worktree HEAD = candidate, `ready_for_integration`, frozen ref + base so the effect proof holds), THEN advance the target by one unrelated commit (`git(root,'commit','--allow-empty'…)` on the target branch). Plain `reconcileFinalization` (no `rebaseRecovery`) → completes (`state: 'cleaned'`, `integratedCommit: candidate`) instead of throwing `not the exact candidate`. RED against `===`.
- **landed-then-advanced reopen refuses:** same seed → `reconcileFinalization(…, rebaseRecovery:'reopen_for_edit')` → `toThrow('integration already landed on the target')`; assert candidate/frozen refs + lock survive. RED against the equality guard (which lets it proceed).
- Keep the existing exact-target tests passing (equal-target reconcile completes; equal-target reopen refuses — the lineage-117/118 cases).

### C2 (cleanup 1) — collapse the two daemon marker dicts (`main.py`)

Replace `_commit_failure_alerted` + `_commit_reopen_processed` with one `_finalize_marker_seen[worker_id]` episode key: when `max_marker_id(events) > seen`, set it and clear the per-worker finalize state (`_finalize_drift_retry`, `_finalize_recovery_alerted`); alert once per episode gated by `worker_id not in`. Removes one dict, the `latest_reopen_id` recompute, and the integrate/reopen asymmetry (per over-engineering review Item 1). Behavior preserved; an integrate marker on a still-running worker also resets stale drift state (equivalent-or-more-correct). The non-running sweep clears the one dict.

### C3 (cleanup 2) — delete the del-attrs test + fix fixtures (`test_daemon.py`, `test_idle_worker_ttl.py`)

Delete `test_daemon.py`'s test that `del`s the marker attrs to model a `__new__` fixture (it protects no production behavior). Give the `__new__`-built fixtures in `test_idle_worker_ttl.py` the new `_finalize_marker_seen={}` attr directly (per Item 2). Port the marker tests (`test_daemon.py:5204-5323`) to the single dict.

### C4 (cleanup 3) — delete two tautology tests (`git.test.ts`)

Delete `expect(GIT_MAX_BUFFER).toBe(64*1024*1024)` and the source-grep-for-the-literal test (per Item 3); the >1 MB round-trip tests are the real falsifiers and stay.

### C5 (cleanup 4) — extract `persistRecoveryRef` (`integration.ts`)

Extract the create-only recovery-ref mint + `recovery_ref` UPDATE + `insertPreservedWork` + re-verify sequence (duplicated between `snapshotResidualIfDirty` ~`:1498` and `reopenForEdit`'s clean-diverged-HEAD branch ~`:1978`) into one helper called from both (per Item 4). Pure refactor; existing tests cover both sites.

## Data Flow / Error Handling

C1 only widens the crash branch's admission (exact → ancestry) and gates the primary repair on the exact case; all durable proofs (lock, effect, ancestry) are preserved. Never-discard-work upheld: the advanced-landed row is now *recognized as integrated* rather than stranded. Cleanups are behavior-preserving.

## Testing Strategy

TDD for C1 (the two landed-then-advanced cases, RED→GREEN). C2/C3 port the daemon marker tests + fixture attrs (RED where the collapse changes a call count). C4 deletes tests. C5 is a refactor verified by the existing suite. Then rebuild `dist/`, full vitest (0 failed) + full commander pytest (0 failed).

## Implementation Notes

Local tests only; no version bump; commit/push operator-gated; no trailers; `dist/` staged with `git add -f`; folds into `8ed4807` (operator-gated amend, PM-off). New lineage — earns its own blind plan review. Over-engineering review item 5 (the `gitBufferOverflowError` call on cumulativeBinaryEffect's fd-stdout spawn) is kept (harmless + consistent with the helper pattern). Docs under repo-root `docs/plans/`.
