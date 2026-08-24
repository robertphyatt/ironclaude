# Reviewed Index and Primary Commit Design

> **Created:** 2026-08-12
> **Status:** Design Complete

## Summary

IronClaude currently loses staging durability when later Git operations, such as `git stash pop`, leave earlier reviewed files unstaged. Its commit-intent hook then authorizes only the surviving staged tree. A separate contract mismatch prevents primary-checkout sessions from committing at all: activation treats an unassigned primary checkout as normal, while workspace-manager commit authority requires a managed assignment.

This design adds one cumulative reviewed-tree receipt per provider-root session and repository. Review preparation reconstructs exact prior reviewed staging before adding current task paths. Human commit-intent issuance restores and validates that receipt before capturing authority. Primary-checkout sessions can bind direct authority without inventing a managed assignment. The design also makes review-checklist loading canonical and fail-closed.

## Architecture

### Cumulative reviewed-tree receipt

Each passing task-boundary review seals the exact staged Git tree. The receipt binds:

- canonical repository identity;
- provider-root session;
- checkout mode and managed assignment when applicable;
- parent commit;
- plan lineage, wave, and reviewed task IDs;
- staged tree object; and
- a durable Git object reference that keeps every reviewed blob and mode reachable.

The latest passing receipt is cumulative. A later review begins from the prior receipt, adds the current submitted tasks' declared paths, and replaces the receipt only after an A or B verdict. C, D, or F leaves the prior receipt intact.

### Candidate-index preparation

IronClaude prepares review and commit state through a temporary Git index. It starts from the latest receipt, or from `HEAD` when no receipt exists, then stages only the exact submitted-task paths. This preserves additions, modifications, deletions, renames, executable bits, and symlinks without trusting the live index.

The preparer validates repository containment and compares the candidate with the real index. Unexpected staged entries fail with exact paths. Git's index-lock mechanism owns promotion. Preparation failure or a concurrent index change leaves the real index untouched.

### Human-intent ordering

The trusted `UserPromptSubmit` hook prepares and validates the latest reviewed receipt before it records `/commit` or `/commit-and-push` authority. The hook then captures the already-prepared staged tree, parent, branch, repository, checkout mode, destination, and provider-root session.

The later public MCP call consumes that exact server-held intent. It does not restage. Any state change between issuance and consumption invalidates authority.

### Primary-checkout authority

Human intent supports two explicit checkout identities:

1. **Managed assignment:** retain existing assignment binding, integration, cleanup, and recovery behavior.
2. **Unassigned primary checkout:** bind directly to the canonical repository, provider-root session, current branch, parent, and reviewed tree. Commit the exact tree to the verified current local branch without managed integration or cleanup.

The unassigned form never creates a synthetic assignment. A transition between managed and primary modes invalidates outstanding authority.

### Canonical checklist loading

Code review resolves `review-checklist.md` from one canonical installed skill or plugin root. It does not depend on the process working directory. Missing or unreadable checklist content stops review before grading and reports the expected path; generic silent fallback is removed.

## Components

### Receipt store

The receipt store holds one active cumulative receipt per provider-root session and canonical repository. It records the binding metadata and reachable Git object reference. Replacing a receipt is monotonic: only a later passing review can supersede it.

### Review-index preparer

The preparer owns temporary-index construction, exact-path staging, containment checks, candidate validation, and locked promotion. It exposes no general staging surface and accepts no globs.

### Review-verdict sealing

The passing-verdict transaction seals the candidate before it advances tasks. Receipt failure prevents the A/B record from becoming authoritative. Failed reviews preserve the earlier receipt for repair.

### Commit-intent preparer

The trusted prompt-hook path restores the latest reviewed index, validates drift, determines managed or unassigned-primary checkout identity, and only then issues server-held human intent.

### Direct commit executor

The executor preserves the current managed flow. For unassigned primary mode, it creates one exact local commit and advances only the authorized current branch. It never pushes under `/commit`.

### Checklist resolver

The resolver derives one absolute checklist path from trusted runtime metadata and returns either verified content or a distinct infrastructure failure.

## Data Flow

### Passing task review

1. Read the latest receipt or `HEAD` baseline.
2. Build a temporary index.
3. Stage submitted tasks' exact `allowed_files`.
4. Validate containment and unexpected staged entries.
5. Promote the candidate under index lock.
6. Run task-boundary review against that staged tree.
7. For A/B, create a durable receipt object and record the receipt with the verdict.
8. For C/D/F, preserve the prior receipt and reopen execution through the existing path.

### Direct human commit

1. Human submits an exact provider-native commit command.
2. Trusted hook resolves the latest receipt and checkout identity.
3. Hook reconstructs and validates the reviewed index.
4. Hook rejects drift or unexpected state.
5. Hook records exact server-held authority over the prepared tree.
6. Assistant invokes workspace-manager with repository, optional managed workspace identity, and message only.
7. Workspace-manager re-observes exact evidence, consumes authority once, and commits.
8. Managed mode integrates through the existing flow; unassigned primary mode advances only the verified current branch.
9. Verified completion retires the receipt idempotently.

## Error Handling

- Temporary-index failure preserves the real index and reports the exact Git error.
- Path escape or unexpected staged state fails before promotion and lists exact paths.
- Content, mode, deletion, parent, branch, repository, receipt, or checkout-mode drift blocks authority.
- Missing receipt objects require fresh review; live bytes never substitute for reviewed bytes.
- Receipt-seal failure prevents a passing verdict and task advancement.
- Index races lose safely through Git locking.
- Failed human binding creates no intent.
- Checkout-mode transitions invalidate existing authority.
- Commit success remains valid if receipt cleanup fails; cleanup is idempotent and retryable.
- Checklist failure stops review before grading and names the canonical path.

Every refusal preserves the working tree, real index, latest valid receipt, and Git refs.

## Testing Strategy

Use temporary real Git repositories. Assert tree and blob IDs, modes, refs, index entries, database rows, and working-tree bytes.

Required coverage includes:

- stash/pop unstaging and exact cumulative restoration;
- multiple PM loops and newest-review precedence for overlapping paths;
- additions, modifications, deletions, renames, executable bits, and symlinks;
- passing and failing verdict receipt behavior;
- seal failure and missing receipt objects;
- undeclared staged paths and all post-review drift classes;
- concurrent index mutation and lock failure;
- unchanged managed-assignment commits;
- exact unassigned-primary commits;
- cross-session, repository, expiry, replay, and model-generated authority denial;
- `/commit` no-push and Commander no-push invariants;
- checklist resolution from repository, `commander/`, and installed-plugin working directories;
- visible failure for missing checklist content; and
- Claude/Codex parity.

Run focused suites, then the complete hook, state-manager, workspace-manager, Commander, and plugin validation suites. Reinstall IronClaude last and verify the active same-session runtime and diagnostics.

## Scope Boundaries

This effort does not add `Monitor` enforcement, stash automation, automatic conflict resolution, generalized artifact provenance, new Git transport, configurable receipt policies, push-authority changes, or unrelated roadmap work.

The implementation plan receives exactly one blind plan review. Findings from that review are repaired in the same plan without a second blind review. Execution defects are repaired in place or through bounded follow-up tasks.
