# Gone-Worktree Close-Out Completion (R3) — Unified with the Reaper — Design

> **Created:** 2026-08-27
> **Status:** Design Complete
> **Requirements:** docs/plans/2026-08-26-m6-close-out-requirements.md (R3, R6, R1b, D4)
> **Follows:** the M6 `/close-out` remediation (C1/C2/I2, staged). Folds into the M6 commit.

## Summary

The tier-up Fable end-review of the staged M6 diff confirmed C1/C2 closed and D5 held, but
found one Important gap: **R3's crash-mid-teardown DB-only completion is unimplemented.** When
`/close-out` targets an `integrated` row whose managed worktree was already removed (a crash
after `removeWorktree` at integration.ts:1351 but before the `integrated→cleaned` transition at
:1356), the verb cannot complete: `closeOutRelease` calls `worktreeHead`/`worktreeIsClean` on the
missing directory (throw), and re-entry can't even mint authority because
`resolveEffectiveCheckout` (git-authority.ts:295-298) requires the worktree in `listWorktrees`,
producing a raw `managed workspace Git identity does not match`. The reviewed commit is already in
local `main`, so no work is lost — but the row wedges and the operator hits a scary raw error,
violating R3/R6/R1b.

The operator chose to **unify** the fix with the reaper: the same gone-worktree shape reaches the
Commander reaper (`reapLeakedAssignment` → `tombstoneTerminalAssignment`) for a dead worker, and
that path **refuses** a push-pending obligation (workspace-service.ts:730-732) rather than carrying
it — the tracked backlog `project_never_lose_work_tombstone_gap`. One consistent gone-worktree
behavior for both the human verb and the reaper closes both gaps.

## Architecture

Two coordinated changes. Neither pushes; the live-worktree `/close-out` path
(`finalizeCloseOut`/`closeOutRelease`) and the C1/C2/I2 remediation stay byte-untouched; the shared
authority path (`resolveEffectiveCheckout`) is NOT modified (that would be the rejected Approach B —
it gates every verb, wrong blast radius).

1. **`tombstoneTerminalAssignment` carries instead of refuses** (workspace-service.ts:699-744).
   Replace the push-pending refusal (:730-732) with a carry: `pushPendingSummary(current.disposition)`
   (exported from integration.ts; excludes `push-succeeded`, matching obs-2) → when defined, wrap the
   final `transitionAssignment('…','cleaned')` + `insertPreservedWork(kind:'pending-push', payload
   JSON.stringify(summary))` in ONE `db.transaction`. `insertPreservedWork` idempotency covers a crash
   re-run. This affects BOTH callers — `cleanupWorkspace` (owner-bound, cli.ts:168) and
   `reapLeakedAssignment` (owner-agnostic reaper, cli.ts:196) — so a push-pending dead worker is now
   reclaimed and its owed push preserved instead of wedging.

2. **Close-out handler routes the gone case** (index.ts `closeOutWorktree`, :354). BEFORE the status
   probe, detect an `integrated` row whose worktree is absent using the SAME present-check
   `cleanupWorkspace` uses (`existsSync(worktree_path) && worktreeExists(primary, worktree_path)`); when
   absent, route to `service.cleanupWorkspace({ repositoryPath, workspaceGuid, ownerSessionId:
   identity.sessionId })` (owner-bound via `getWorkspaceAssignment`) and map the returned `Assignment`
   to `{ state: 'closed-out', integratedCommit: <row.integrated_commit>, pendingPush?:
   pushPendingSummary(<pre-cleanup disposition>) }`. A PRESENT worktree still takes the existing
   status-probe → authority → `finalizeCloseOut` path unchanged.

**Authorization:** the gone-worktree completion is pure DB bookkeeping on an already-integrated row
(no `main` advance, no push, work already in local main). `cleanupWorkspace` is owner-bound
(`getWorkspaceAssignment`: `owner_session_id === ownerSessionId` + repo match) and is today reached
only Commander-internally (cli.ts:168) — routing the handler into it adds ZERO new authority surface.
The human already invoked `/close-out` (the hook issued the intent). Owner-binding is therefore
sufficient; no bespoke gone-worktree intent-consume is added (that would either duplicate surface or
force a change to the shared `resolveEffectiveCheckout`).

## Components

- **worker/mcp-servers/workspace-manager/src/workspace-service.ts** — `tombstoneTerminalAssignment`
  refuse→carry; import `pushPendingSummary` + `insertPreservedWork`. (`hasPushPendingObligation` import
  from integration.ts already exists, so no circular-import risk.)
- **worker/mcp-servers/workspace-manager/src/index.ts** — `closeOutWorktree` gone-worktree branch
  (`existsSync && worktreeExists` detection → `service.cleanupWorkspace` → result mapping); import
  `existsSync` / `worktreeExists` / `pushPendingSummary` as needed.
- **Unchanged:** `resolveEffectiveCheckout`, `finalizeCloseOut`, `closeOutRelease`,
  `reapLeakedAssignment`'s own body (it inherits the carry through tombstone), the C1/C2/I2 code, and
  every push lane.

## Data Flow

- **Human `/close-out`, integrated + gone:** handler detects gone → `cleanupWorkspace` →
  `getWorkspaceAssignment` (owner-bind) → `validateManagedIdentity` skipped (not present) →
  `tombstoneTerminalAssignment`: integration-records proof (gone-tolerant: `worktreeHead` checked only
  when present, :726) → carry (`pushPendingSummary` → `insertPreservedWork`) + `transition→cleaned` in
  one txn → best-effort `deleteTemporaryBranch` (skipped when the branch is already gone, :740). Handler
  maps → `closed-out` + `pendingPush`. No git op on the gone worktree; no raw identity error.
- **Reaper, integrated + gone + push-pending dead worker:** `reap` → `reapLeakedAssignment` →
  `tombstoneTerminalAssignment` → carry + `transition→cleaned`. Now reclaims (was: refused/wedged).
- **Later human `/push` of local main** → `drainCarriedObligations` resolves the `preserved_work` row
  by containment (owner-agnostic), publishing the work.

## Error Handling

- **Dirty PRESENT worktree:** tombstone's :702-704 guard still throws — only clean integrated
  worktrees are reclaimed, so the reaper change never discards uncommitted bytes.
- **Failed integration proof:** tombstone's :714-729 proof still throws (preserve) — unchanged.
- **`push-succeeded` disposition:** `pushPendingSummary` returns `undefined` → not carried (consistent
  with closeOutRelease obs-2); the row simply cleans.
- **Present-but-unregistered / ambiguous worktree:** the handler's `existsSync && worktreeExists` gone
  test is false → routes to the normal `finalizeCloseOut` path, which throws its existing descriptive
  error; the gone fast-path never fires on a live/ambiguous worktree.

## Testing Strategy

- **Rewrite** workspace-service.test.ts:762 `'refuses to tombstone … push-pending'` → asserts the CARRY:
  row → `cleaned`, a `preserved_work` `pending-push` row exists with the disposition's
  candidate/remote/ref, no throw. (This test file MUST be in `allowed_files` from the start — the
  Task-2 retreat trap.)
- **Add** `reapLeakedAssignment` carries a push-pending dead-worker obligation (integrated + gone) →
  `cleaned` + `preserved_work` row (owner = the dead worker's id).
- **Add** `closeOutWorktree` on an integrated + gone row → `closed-out` + `pendingPush` + row `cleaned`
  + `preserved_work` row; assert NO raw `managed workspace Git identity does not match` (the R3 case).
- **Add** `closeOutWorktree` on an integrated + PRESENT worktree still routes to the normal
  `finalizeCloseOut` path (gone-detection does not false-positive).
- **Negative:** a dirty present worktree is still refused; a non-owner session is refused by
  `cleanupWorkspace`'s `getWorkspaceAssignment`.
- Verify whether cli.test.ts asserts the old refusal; if so, include it in `allowed_files` and update.

## Known Limitation (accepted, documented)

Reaper-carried obligations carry `owner_session_id` = the DEAD worker's id. `listUnresolvedPreservedWork`
filters by owner, so `list_preserved_work` in a human session will NOT surface a reaped worker's carried
obligation. This costs nothing material: the commit is in local `main` (visible in `git log`), a human
`/push` of main publishes it, and `drainCarriedObligations` (owner-agnostic) clears the record. Repo-wide
listing (surfacing ownerless/reaped rows to any session in the repo) is a possible future enhancement;
it is out of scope here because it would also leak other sessions' obligations into a human's view.

## Implementation Notes

- `pushPendingSummary` for BOTH detection and payload in the tombstone carry (single source of truth;
  already excludes `push-succeeded`).
- Wrap `transition→cleaned` + `insertPreservedWork` in one `db.transaction` (the I-4 pattern);
  `insertPreservedWork` idempotency (unresolved `(workspace_guid, kind, payload)`) covers crash re-run.
- Handler gone-detection uses the SAME `existsSync && worktreeExists` present-check as
  `cleanupWorkspace`, so a present-but-dirty worktree routes to the normal path, not the fast-path.
- No circular import: workspace-service.ts already imports from integration.ts.
- Fold the change into the M6 commit; then the M6 end-review re-run gate.
