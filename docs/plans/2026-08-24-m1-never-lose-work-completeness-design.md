# M1 — Never-Lose-Work Completeness Design

> **Created:** 2026-08-24
> **Status:** Design Complete
> **Requirements:** docs/plans/2026-08-24-m1-never-lose-work-completeness-requirements.md

## Summary

Close the last never-lose-work gap the v1.1.7 backstop did not reach: the `tombstoneTerminalAssignment`
teardown primitive silently drops a push-pending obligation. Approach B: guard tombstone
(refuse-and-preserve, the structural guarantee) AND teach the daemon reaper to recognize an integrated
push-pending row as protected (surface-once, no per-cycle spam), plus convert the Commander finalize
lane to graceful handling. Ships as v1.1.8.

## Architecture

Three teardown primitives now share one never-lose-work discipline:
`recycleFinalized`/`releaseFinalized` (guarded in v1.1.7) and `tombstoneTerminalAssignment` (this
change). The guard is the structural guarantee (a throw preserves the row at any caller). The daemon
reaper — the one caller that would otherwise retry the preserved row every cycle — gains a
disposition-aware protection so it surfaces the stuck row once instead of spamming "release failed."
The Commander finalize lane converts from a fail-loud backstop throw to a graceful `integrated-local`
return.

## Data Flow — the defect and the fix

**Defect:** `tombstoneTerminalAssignment` integrated branch (`workspace-service.ts:713-728`) proves
integration evidence, then `removeWorktree` (:733) + `deleteTemporaryBranch` (:736) + transition
`cleaned` (:739) — dropping the push obligation. `markIntegrated` (`integration.ts`) is the sole setter
of a push-pending disposition and sets `lifecycle_status='integrated'` atomically, so push-pending only
ever rides integrated rows.

**Fix flow:** at tombstone, if the fresh row carries a push-pending disposition → throw before
`removeWorktree`, preserving worktree + branch + row + `integration_records`. `cleanupWorkspace`
(on-demand) surfaces the throw to the operator; `reapLeakedAssignment` catches it (main.py:514) and
preserves — but the reaper's `_is_protected` now recognizes the push-pending row first and protects it
(never reaches the reap call), surfacing once instead of erroring every cycle.

## Components

**1. `integration.ts` — export the predicate.** Export `decodePushDisposition` (`:156`) — or add and
export a thin `export function hasPushPendingObligation(disposition: string | null): boolean { return
decodePushDisposition(disposition) !== undefined; }`. The tombstone guard imports it.

**2. `workspace-service.ts` — tombstone guard.** In `tombstoneTerminalAssignment`, after the
integrated-evidence proof (`:721-728`) and before the `if (assignment.lifecycle_status === 'abandoned')`
recovery-anchor block (`:730`), add:
```ts
if (hasPushPendingObligation(assignment.disposition)) {
  throw new Error('Refusing to tombstone a worktree with a push-pending obligation; resolve or push it first');
}
```
(Only the integrated branch can carry push-pending; abandoned rows cannot. Import the predicate from
`../integration.js`.)

**3. `main.py` — reaper recognition.** Add a helper:
```python
def _has_push_pending(disposition_json: str | None) -> bool:
    if not disposition_json:
        return False
    try:
        phase = json.loads(disposition_json).get("phase")
    except (ValueError, TypeError):
        return False
    return phase in ("push-pending", "push-succeeded", "push-failed")
```
In `_is_protected` (`:288`, which already receives the full `SELECT *` assignment dict and errs toward
protect), add — before the `return False` — a check that protects a push-pending row, and surface it
once (a per-guid set on the daemon, mirroring `_message_aging_alerted`) so the operator learns a row is
preserved pending a push rather than seeing silence or per-cycle spam.

**4. `integration.ts` — I-1 Commander graceful.** At each `finalizeCommanderLocalCommit`
`disposeFinalized` site (~`:1198`/`:1204`), guard inline:
```ts
if (decodePushDisposition(local.assignment.disposition)) {
  return { state: 'integrated-local', integratedCommit: <candidate|local.integratedCommit>, pushError: 'Remote has not proved the exact integrated candidate' };
}
disposeFinalized(db, local.repositoryPath, local.assignment, input.dispose);
return { state: 'cleaned', integratedCommit: <candidate|local.integratedCommit> };
```
NOT `finishLocalIntegration` — that hardcodes recycle and would break `dispose: 'release'`.

## Error Handling

- Tombstone guard: fail-closed throw preserves the row; on-demand `cleanupWorkspace` surfaces it to the
  operator, reaper protects+surfaces-once.
- Reaper `_is_protected` already errs toward PROTECT on any exception — the push-pending check is a
  positive protect condition, consistent with that discipline.
- I-1: graceful `integrated-local` (no throw) when preserving; the `dispose: 'release'` teardown path is
  untouched when no obligation is present.

## Testing Strategy

- **TS (vitest, integration-cases.ts):** reuse `seedIntegratedPushPending`; call
  `tombstoneTerminalAssignment` (exported/reachable) on the integrated push-pending row → assert throw
  AND row/disposition/worktree/`integration_records` survive. Deleting the guard makes it discard → RED.
- **Python (commander pytest):** unit-test `_has_push_pending` (phase set) and `_is_protected` returns
  True for an integrated push-pending assignment; a sweep test that the reaper does NOT reap it. Mirror
  the existing reaper test fixtures.
- **I-1 (vitest):** a Commander finalize on a push-pending row returns `integrated-local` + preserves;
  pre-guard throws/discards.
- Full workspace-manager vitest + the reaper commander pytest stay green.

## Implementation Notes

- **Predicate consistency:** the TS export and the Python `_has_push_pending` must agree on the phase set
  (`push-pending`/`push-succeeded`/`push-failed`, from `decodePushDisposition:160`). State it so a future
  phase addition updates both.
- **Invariant to verify in the plan:** confirm both push-pending setters in `integration.ts` set
  `integrated` (so the guard's integrated-branch home is complete).
- **v1.1.8:** 5 version files + `test_version_consistency.py` + CHANGELOG + README (fresh codex cachebuster).
- **Non-goal (recorded):** anchoring the obligation on a recovery ref + a resume-push path so teardown
  can proceed — its own loop.
- **Blind-reviewer note:** severity is "dropped push obligation" (commits are safe on local main), not
  data loss; the reaper catches a bare throw (preserve), so the guard is safe even without the reaper
  half — the reaper half exists to replace loud-stuck spam with surface-once.
