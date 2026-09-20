# reopen_for_edit leftover-candidate discriminator (I-1) — Design

> **Created:** 2026-09-21
> **Status:** Design Complete
> **Type:** Corrective for the final end review of lineage 119 (1 verified MATERIAL: I-1). Folds into unpushed v1.1.11 alongside lineage 119.
> **Scope mode:** hold.

## Summary (root cause + evidence)

Lineage 119 widened the `reopen_for_edit` interrupted-CAS guard from exact equality to ancestry (`isAncestor(candidate, targetRef)`). That correctly refuses a genuine landed-then-advanced row (routing it to reconcile, which now completes it), but it **over-refuses** a stale prior-lifecycle **leftover** candidate: `recycleFinalized` deletes the candidate ref best-effort AFTER its DB commit (`integration.ts:732-734`), so a crash there leaves `C_old` (an ancestor of the advanced target `T`); the next lifecycle of the same GUID acquires the lock at `expected_target=T` (`:877`) BEFORE rewriting the candidate ref (`:909`), and a kill there leaks the lock while `C_old` survives and worktree HEAD = frozen `F`. Then EVERY managed verb refuses: reconcile at `:2187` (HEAD `F` ≠ `C_old`), and reopen at `:1974` (`isAncestor(C_old, T) && lockHeld`). Under lineage 118's equality guard this reopened cleanly (`T ≠ C_old`), so lineage 119 introduced a two-crash-window strand — the exact "no managed exit" class this work targets. (The reconcile side is provably safe: the effect proof at `:2219` blocks any false completion; only the reopen guard lacks a bound.) The df69c3a4 incident exhibited exactly this stale-candidate poison.

Discriminator: a **genuine** this-lifecycle landed candidate strictly **descends** from the lock's `expected_target` (candidate = `expected_target` + reviewed content); a **stale leftover** is an **ancestor** of `expected_target` (it predates this lock).

## Components

### C1 — expected_target discriminator on the reopen guard (`integration.ts` reopenForEdit, ~:1961-1977)

Read the held lock's `expected_target` (column exists, `db.ts:119`) instead of `SELECT 1`, and refuse only when the candidate genuinely descends from it:
```ts
let landedCandidate: string | undefined;
try {
  landedCandidate = runGit(exact.primaryCheckoutPath, ['rev-parse', '--verify', `${candidateRef(assignment.workspace_guid)}^{commit}`]).trim();
} catch { /* no candidate ref: not an interrupted-CAS row */ }
if (landedCandidate) {
  const landed = isAncestor(exact.primaryCheckoutPath, landedCandidate, targetRef(assignment));
  const lockRow = db.prepare('SELECT expected_target FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?')
    .get(assignment.repository_identity, assignment.workspace_guid) as { expected_target: string } | undefined;
  // Refuse only a GENUINE this-lifecycle landed CAS: the candidate landed on the target
  // (reachable from it) AND the integration lock is held AND the candidate DESCENDS from
  // the lock's expected_target (candidate = expected_target + reviewed content). A stale
  // prior-lifecycle leftover candidate is an ANCESTOR of expected_target — it predates this
  // lock — so let it proceed to a cleaning reopen rather than strand it. Refusal routes to
  // reconcile, which completes the genuine landed row via markIntegrated.
  if (landed && lockRow && isAncestor(exact.primaryCheckoutPath, lockRow.expected_target, landedCandidate)) {
    throw new Error('reopen_for_edit refused: integration already landed on the target (interrupted-CAS); run reconcile — it will finish the integration or report the repair needed — do not reopen');
  }
}
```
This ALSO replaces the stale guard comment (lineage-119 grade-B nit: it still said "target ref equals it") with an accurate ancestry+discriminator description.

`isAncestor(cwd, ancestor, descendant)` = `merge-base --is-ancestor ancestor descendant` (git.ts:565). For a genuine candidate, `isAncestor(expected_target, candidate)` is true (candidate descends) → refuse. For a leftover `C_old`, `isAncestor(T, C_old)` is false (`C_old` is an ancestor of `T`) → proceed.

## Data Flow / Error Handling

Only narrows the reopen refusal. The genuine landed-then-advanced case still refuses (routes to reconcile → completes); the stale-leftover case now proceeds to a reopen that clears `C_old`/freeze/lock and returns the row to `active`. Never-discard-work upheld (the leftover is a stale ref, not this lifecycle's reviewed work, which is the frozen `F` preserved by reopen).

## Testing Strategy

TDD (vitest, `integration-cases.ts` recovery part):
- **Leftover-candidate proceeds:** seed a stale candidate `C_old` (ancestor of an advanced target `T`), a held lock with `expected_target = T`, frozen `F` and worktree HEAD = `F` (≠ `C_old`), `ready_for_integration`. `reconcileFinalization(…, reopen_for_edit)` → PROCEEDS (`state: 'finalization-reopened-for-edit'`), candidate ref cleared. RED against the current lineage-119 ancestry guard (which refuses).
- **Genuine landed-then-advanced still refuses:** the lineage-119 "reopen refuses a landed candidate whose target has advanced" test must still pass (its candidate descends from `expected_target`).
- Then rebuild `dist/`, full vitest (0 failed) + full commander pytest (0 failed).

## Implementation Notes

Local tests only; no version bump; commit/push operator-gated; no trailers; `dist/` staged with `git add -f`; folds into `8ed4807` alongside lineage 119 (operator-gated PM-off amend). New lineage — earns its own blind plan review. Docs under `commander/docs/plans/` (the guard's writable base at the current cwd).
