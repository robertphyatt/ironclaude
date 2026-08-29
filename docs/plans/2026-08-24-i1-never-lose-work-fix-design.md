# I-1 Never-Lose-Work Fix Design

> **Created:** 2026-08-24
> **Status:** Design Complete
> **Requirements:** docs/plans/2026-08-24-i1-never-lose-work-fix-requirements.md

## Summary

`finalizeReconcile` and `finalizeDirectAuthority` each have an "active" branch that recycles a
managed worktree unconditionally after `finalizeLocalCommit`, nulling a `push-pending`
disposition that `markIntegrated` produces from a pre-existing `integration-pending` row —
silently destroying an unfulfilled push obligation. This fix makes both active branches preserve
the obligation (returning `integrated-local`, exactly as the repair branches and the reconcile
`SKILL.md` contract already do), and adds a structural backstop so no current or future caller of
`recycleFinalized`/`releaseFinalized` can silently drop a push-pending obligation. Ships as
v1.1.7 with two Fable observations (O-2 stale comment, O-3/O-4 message clarity) folded in.

## Architecture

Approach **A** (operator + Fable chosen) at the two known sites, plus **Option C** (operator
chosen) as a structural backstop. Approach A honors the file's exceptionless convention — a site
holding a `LocalFinalization` whose success state is `'cleaned'` uses `finishLocalIntegration`
(1050, 1277, 1559); inline guards exist only where the helper cannot fit (reconcile branches
return `'reconciled'`; crash-reconciliation sites hold no `LocalFinalization`). Option C moves the
never-lose-work invariant from "every caller must remember to guard" to "the recycle primitives
refuse to lose work," fired only on a regression.

## Data Flow — the defect and the fix

**Defect (confirmed against source):**
1. `commit-and-push` sets `integration-pending` disposition (`integration.ts:1065`).
2. `finalizeLocalCommit` throws at the dirty gate (`:834-837`) before the `active→ready`
   transition (`:838`) ⇒ row stays `active` + `integration-pending`.
3. Later `/reconcile` (active branch `:1145-1146`) or `/commit` (active branch `:1071`):
   `finalizeLocalCommit` → `markIntegrated` (`:545-564`) converts `integration-pending` →
   `push-pending` (with `candidateCommit`) and sets lifecycle `integrated`; `recycleFinalized`
   (`:590`) then unconditionally sets `disposition = NULL` → obligation destroyed.

Because `markIntegrated` throws (`:546-548`) on any disposition that is neither null nor
`integration-pending`, the post-finalize `local.assignment.disposition` ∈ {null, push-pending};
`decodePushDisposition` cannot over-match. `local.assignment` is `markIntegrated`'s fresh re-read
(`:567`) threaded out through `continueFrozenFinalization` (`:793-795`), so the guard sees the
real post-finalize value.

**Fix — data flow after:** the common path (no disposition) is unchanged — `decodePushDisposition`
null ⇒ recycle ⇒ `reconciled`/`cleaned`. Only when a push-pending disposition is present does the
active branch now return `integrated-local` and preserve the row (`integrated`, disposition kept,
worktree alive, `integration_records` row kept).

## Components

**1. `integration.ts` — `finalizeReconcile` active branch (~1145-1147)** — insert the guard,
identical to the repair branch at 1154-1158:
```ts
const local = finalizeLocalCommit(db, exact.primaryCheckoutPath, exact.assignment, authority.worktreePath, headOid);
if (decodePushDisposition(local.assignment.disposition)) {
  return { state: 'integrated-local', integratedCommit: local.integratedCommit, pushError: 'Remote has not proved the exact integrated candidate' };
}
recycleFinalized(db, local.repositoryPath, local.assignment);
return { state: 'reconciled', integratedCommit: local.integratedCommit };
```

**2. `integration.ts` — `finalizeDirectAuthority` `/commit` branch (~1070-1073)** — replace the
unconditional recycle with the helper:
```ts
if (authority.operation === 'commit') {
  return finishLocalIntegration(db, local);
}
```
`finishLocalIntegration` (662-672) returns `integrated-local` on push-pending, else
`recycleFinalized` + `{ state: 'cleaned', integratedCommit }` — byte-identical to today's
1071-1072 in the non-push case.

**3. `integration.ts` — Option C backstop** — in `recycleFinalized`, after the fresh re-read
(`current`, :573) and in `releaseFinalized` after its re-read (`current`, :620), add:
```ts
if (decodePushDisposition(current.disposition)) {
  throw new Error('Refusing to discard a push-pending obligation; resolve or push it first');
}
```
Placed after the existing `!current || lifecycle !== 'integrated'` guard. Every correct caller
(1112→1121, the helper 663→670, 1406→1410, 1525→1532, 1597→1604, and the two branches fixed
above) nulls or guards the disposition before recycling, so this throws only on a regression —
including the Commander twin (`finalizeCommanderLocalCommit` `disposeFinalized` at 1198/1204),
where `dispose:'release'` would otherwise delete the worktree carrying the obligation.

## Error Handling

- The backstop is fail-closed: it throws and preserves the worktree rather than deleting.
- The `pushError` string on the `/commit`/reconcile active-preserve path
  ("Remote has not proved the exact integrated candidate") is inherited from the helper/repair
  branch. It describes the *prior* commit-and-push's unmet obligation, not a push attempted this
  invocation — accurate; not worth diverging.
- Blocked-worktree recovery (documented, not a new bug): a preserved row is `integrated` +
  push-pending; a later `/commit` refuses until it resolves via `reconcileFinalization` (integrated
  branch ~1375-1411) or `/push`.

## Testing Strategy

New tests in `src/__tests__/integration-cases.ts` (template: the repair preserve test at :375):
1. **reconcile active preserve** — build `active` + `integration-pending` (a `commit-and-push` that
   throws at the dirty gate, then a clean re-commit), issue reconcile authority, call
   `finalizeReconcile`; assert `state === 'integrated-local'`, DB row `integrated` +
   `push-pending`, worktree exists, `integration_records` row present. Delete the guard ⇒
   `reconciled` + disposition NULL ⇒ fails.
2. **/commit active preserve** — same precondition, `finalizeDirectAuthority` operation `commit`;
   assert `integrated-local` + preserved. Delete the helper call (revert to unconditional recycle)
   ⇒ `cleaned` + NULL ⇒ fails.
3. **Option C backstop** — seed an integrated `push-pending` row and call `recycleFinalized`
   (and `releaseFinalized`) directly; assert it throws and the row/worktree survive.

Full workspace-manager vitest suite stays green (33 existing `'cleaned'` assertions are all
normal-case, unaffected — audited).

## Implementation Notes

- **O-2:** fix the stale line-refs in `git-authority.test.ts:1114-1117` (`:614/:613/:563`) to
  current values against `git-authority.ts` (single-use gate `:640`, reconcile push-exclusion
  `:589`; the third pinned during planning).
- **O-3/O-4:** tighten `finalizeReconcile` refusals — e.g. 1137 → "…there is no primary/unassigned
  reconcile lane"; 1160 → name the states and the `reconcile_finalization` recovery path.
- **v1.1.7:** the 5 version files guarded by `test_version_consistency.py` (+ CHANGELOG + README).
- **Backlog (recorded, not built):** graceful (non-throw) handling of the Commander twin sites
  1198/1204; the Option C throw is the interim protection.
- **Blind-reviewer notes:** every guard is falsifiable at the post-merge state; the preserved
  `pushError` wording and blocked-worktree recovery are intentional and documented above.
