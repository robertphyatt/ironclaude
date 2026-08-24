# Worktree Reaper — Leak Closure Requirements (operator-approved)

> **Created:** 2026-08-19
> **Source:** brainstorm decisions (Loop 3, slice 1). Scope: reduction. Human commits, no push.

## Approved scope

Close the managed-worktree leak: the Commander reaper reclaims dead/finished-worker
worktrees (and tombstones rows whose worktree is gone), PRESERVING any unintegrated
work durably first. Reuse the existing `rescueAbandon`/`cleanupWorkspace` primitives;
add no epoch/receipt machinery.

- **R0 — durable preserve (never-lose-work foundation).** `rescueAbandon`
  (workspace-service.ts:599) currently stores `recovery_ref` as a RAW SHA and relies on
  the temporary branch as the sole anchor. Fix it to mint a durable git ref
  `refs/ironclaude/recovery/<workspace_guid>` at the rescued commit and store that REF
  NAME in `recovery_ref` (the convention the existing test fixture already uses). This
  is the anchor that lets the branch be safely deleted later. Git-native, no new
  machinery.

- **R1 — gone-tolerant tombstone.** A private `tombstoneTerminalAssignment(repository,
  assignment)` deletes a terminal (`integrated`/`abandoned`) row: **present worktree** →
  today's exact proof BYTE-FOR-BYTE (`worktreeIsClean`, `worktreeHead`,
  `isAncestor(actualHead, recovery_ref)` for abandoned / integration evidence for
  integrated, `removeWorktree`); **absent worktree** → skip the on-disk steps, require
  the recorded durable evidence to RESOLVE in the primary checkout (abandoned:
  `recovery_ref` is a resolvable ref reaching a commit; integrated: `integrated_commit`
  matches the integration record and is an ancestor of the target ref), then transition
  `→cleaned`. `deleteTemporaryBranch` runs ONLY once `recovery_ref` is a durable ref (or
  the work is integrated) — never before a durable anchor exists. `cleanupWorkspace`
  keeps its owner-match + status guard + present-case `validateManagedIdentity` and
  delegates the terminal delete to this helper (present-worktree behavior byte-identical).

- **R2 — reaper-only ownerless reap.** New `reapLeakedAssignment({repositoryPath,
  workspaceGuid})` — NO owner match. Resolve by `getAssignment(db, workspaceGuid)`; verify
  repository identity + canonical `managedWorktreePath`/`managedBranch` SHAPE. For a
  PRESENT worktree, also run the observed-branch check (`validateManagedIdentity`, :170)
  and refuse+surface on a reused-path/foreign-branch mismatch (preserve the ambiguous
  worktree). Then:
  - present + unresolved → `rescueAbandon` (mint recovery ref → remove dir → `abandoned`);
  - absent + unresolved + managed branch `ironclaude/<guid>` resolves → mint
    `refs/ironclaude/recovery/<guid>` at the branch tip, record it, transition `→abandoned`;
  - absent + unresolved + branch also gone → nothing preservable exists; a
    nothing-to-preserve carve-out (record `recovery_ref = base_commit`, which is NOT NULL
    and always reachable) then `→abandoned`;
  - `reserved` (never-materialized) → route to the existing `cleanupReservedAssignment`
    (:624) which deletes the row;
  - then `tombstoneTerminalAssignment`.
  It NEVER writes `→abandoned` (or removes a worktree) before durable recovery evidence
  exists, and NEVER removes a present worktree whose work is not first preserved.

- **R3 — reaper wiring + client verb.** Add a `reap` command to `WorkspaceClient`
  (`workspace_client.py`: the class method + the `_COMMANDS` set). The Commander reaper
  ownerless branch (main.py:466-479, today `counts["surfaced"]`) calls
  `workspace_client.reap({repository_path, workspace_guid}, **transport)` where
  `repository_path` is derived from `worktree_path` by stripping
  `/.ironclaude/worktrees/<guid>` and `transport = {}` for an ownerless (`worker is None`)
  candidate (NOT `resolve_transport(None)`, which raises on the real daemon transport).
  Success → `counts["released"]`; any exception → the existing surfaced log +
  `counts["surfaced"]` (un-derivable rows still surface).

- **R4 — bounded authority.** The owner-match bypass lives ONLY in `reapLeakedAssignment`
  + the `reap` CLI verb + `WorkspaceClient.reap`, reached only from the daemon reaper for
  rows the existing `_find_leaked_worktrees` + `_is_protected` (TTL/liveness/locks) gates
  already deemed leaked. No session-facing path loses its owner check.

- **R5 — no regression.** Owner-matched `cleanupWorkspace`/`abandonWorkspace` byte-unchanged
  for the present-worktree case (the recovery-ref change makes the existing abandoned proof
  and its test pass, not fail). Loop 1/2 untouched. Full regression green (workspace-manager
  vitest, state-manager vitest, hook suites, commander pytest).

## Non-goals

- Loop 3 items 3/4/5 (corrective-git, shared-resource relink, H8 resolution-worker) —
  later slices. Push authority, integration, reverted epoch/receipt machinery, automatic
  conflict resolution.
