# Primary Checkout Overlap-Based Finalization Design

> **Created:** 2026-08-12
> **Status:** Design Complete

## Summary

IronClaude currently treats any live primary-checkout owner as proof that worker
finalization is unsafe. `fencePrimaryCheckout()` rejects finalization before the
existing path-overlap logic can inspect the primary checkout. This blocks safe,
non-overlapping integrations and conflates two different concerns: exclusive
permission to operate the primary checkout directly and evidence that an
integration would overwrite primary-checkout work.

This repair retains exclusive ownership for sessions that request direct
primary-checkout access. Finalization, reconciliation, push-only operations,
status probes, workspace-manager cleanup, and Commander reaping instead use
safeguards appropriate to what each operation can change. Integration is
refused only for a concrete path collision, target-ref race, or other failed
safety invariant. Cleanup is refused only when its existing identity,
liveness, lock, cleanliness, reachability, or lifecycle proofs fail.

## Architecture

Replace the universal primary-ownership fence with operation-specific guards:

- **Direct primary-checkout operations** retain exclusive live ownership. A
  second session cannot bind to the primary checkout until the owner returns to
  its managed worktree or becomes stale.
- **Ref-only operations** ignore primary-checkout ownership because they do not
  write primary-checkout files. This includes pure ref-CAS finalization when the
  target branch is not checked out there, push-only operations, and status
  probes.
- **Working-tree-affecting integration** compares the proposed integration's
  changed paths with staged, unstaged, and untracked paths in the primary
  checkout. It refuses before CAS on overlap and proceeds on no overlap.
- **Managed-worktree lifecycle operations** use assignment, integration,
  liveness, and lock state rather than an unrelated primary-checkout lease.

Existing integration locks, expected-target checks, ref CAS, carry-forward
behavior, and post-CAS recovery remain authoritative. No schema, path
reservation, distributed-lock, or cross-session allowlist system is added.

## Components

### Primary Ownership Guard

Create a narrowly named ownership guard for primary-checkout binding and
ownership transitions. It may reap a stale owner before granting access, but it
must reject a second live owner. Ownership continues to answer only who may
operate the primary checkout directly.

### Finalization Preparation

Worker finalization continues to construct and validate the proposed integrated
commit through the temporary-index and object-store path. A live primary owner
does not block this preparation.

### Primary Impact Classifier

Before target-ref update, determine whether the primary checkout currently has
the target branch checked out:

1. If not, use the existing pure ref-CAS path; no primary working-tree update is
   necessary.
2. If so, compute proposed integration paths and the checkout's staged,
   unstaged, and untracked paths.
3. Refuse before CAS if the sets overlap.
4. Otherwise, continue through the established CAS and carry-forward path while
   preserving existing bytes and index state.

### Finalization and Cleanup

After integration succeeds, worker finalization and managed-worktree recycling
or release proceed without requiring the primary checkout to be unowned.
Cleanup failures remain distinct from integration failures so successful
history is not misreported or rolled back.

`WorkspaceService.cleanupWorkspace()` retains its terminal lifecycle, managed
identity, clean-worktree, recovery/integration reachability, and exact recorded
integration proofs. It removes only the repository-wide ownership predicate.

Commander's leaked-worktree reaper retains running-worker and tmux liveness,
recent-activity, exact integration-lock, operator-assignment, and fail-safe
exception protections. It no longer treats an unrelated primary owner in the
same repository as proof that every terminal worker remains live.

### Other Operations

Push-only uses verified refs and remote state and therefore does not consult
primary ownership. Status probes report ownership and integration state without
fencing or mutating ownership. Reconciliation uses the same ref-versus-working-
tree classification as ordinary finalization.

## Data Flow

1. Resolve repository identity, worker assignment, expected target ref, and
   proposed integrated commit.
2. Acquire the existing integration lock and verify that the target still
   matches the expected commit.
3. Detect whether the primary checkout has the target branch checked out.
4. For a pure ref operation, proceed directly to ref CAS.
5. For a working-tree-affecting operation, compare proposed changed paths with
   all staged, unstaged, and untracked primary paths.
6. On overlap, return the exact conflicting paths before any ref update.
7. On no overlap, perform CAS and the existing carry-forward working-tree
   update.
8. Complete assignment finalization and recycle or release the managed
   worktree.
9. When Commander later reaps a terminal leaked worktree, evaluate worker,
   tmux, activity, integration-lock, and assignment protections without using
   repository-wide primary ownership as a liveness signal.

Direct-checkout acquisition follows a separate flow: reap a stale owner if
needed, reject a live owner, then bind the requesting session. It does not share
the integration conflict predicate.

## Error Handling

- **Path overlap:** report exact overlapping paths, leave the target ref
  unchanged, and preserve the managed worktree for retry.
- **Target moved:** preserve newer target history through the existing
  expected-target CAS failure and recovery flow.
- **State changes after precheck:** retain the integration lock and Git's
  working-tree update as final safety checks. If post-CAS carry-forward cannot
  complete safely, use existing recovery and preserve diagnostic state.
- **Live owner:** report an ownership conflict only when another session asks
  for direct primary-checkout access.
- **Stale owner:** ownership lifecycle operations may reap it and continue;
  read-only probes only report it.
- **Push failure:** preserve successful local integration and return the remote
  failure without rolling back the target ref.
- **Cleanup failure:** report integration success separately and retain enough
  assignment state for deterministic cleanup retry.
- **Reaper uncertainty:** continue to protect running workers, active tmux
  sessions, recently active assignments, exact integration locks, operator
  assignments, and any candidate whose liveness check raises.
- **Unexpected state:** fail closed with concrete repository path, refs,
  expected and actual commits, owner, and conflicting paths where applicable.

No failure path may discard worker output, overwrite primary-checkout work, or
use ownership as a substitute for conflict evidence.

## Testing Strategy

Use temporary real Git repositories and assert refs, index state, working-tree
bytes, ownership rows, and managed-worktree lifecycle:

- Live owner with target checked out elsewhere: ref-only finalization succeeds;
  primary bytes remain unchanged.
- Live owner with non-overlapping staged, unstaged, or untracked work:
  finalization succeeds and preserves every pre-existing byte and index state.
- Live owner with overlapping staged, unstaged, or untracked paths:
  finalization refuses before CAS and leaves the target ref unchanged.
- A second direct-primary request remains fenced by live ownership.
- Ownership lifecycle reaps stale owners.
- Status probes do not fence or mutate ownership.
- Push-only proceeds independently of checkout ownership.
- Managed-worktree cleanup succeeds without an unowned primary checkout.
- Direct `cleanupWorkspace()` removes a proven terminal worktree under an
  unrelated live primary owner while preserving the owner row and primary
  checkout.
- Commander reaps an otherwise eligible terminal worker under an unrelated
  live primary owner while retaining every other liveness and lock protection.
- Target movement retains CAS protection.
- Post-CAS failure retains existing recovery guarantees.
- Direct-session and Commander finalization share the same behavior.

Replace the existing test that requires every live owner to fence finalization;
it encodes the defect. Acceptance requires focused workspace-manager tests and
the full repository test suite.

## Implementation Notes

Keep the integration repair within the workspace-manager integration boundary.
Remove the same repository-wide ownership assumption from the two existing
managed-worktree cleanup seams: `WorkspaceService.cleanupWorkspace()` and the
Commander leaked-worktree reaper. Do not add a generalized policy framework,
change commit or push authority, resolve conflicts automatically, add database
state, or couple integration to another session's `allowed_files`.
