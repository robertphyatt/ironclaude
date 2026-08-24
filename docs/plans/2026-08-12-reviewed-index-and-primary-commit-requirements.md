# Reviewed Index and Primary Commit Requirements

> **Created:** 2026-08-12
> **Status:** Operator Approved

## Purpose

IronClaude must preserve cumulative reviewed staging across task reviews, PM loops, and index-disrupting Git operations. It must also let a directly operated professional-mode session commit reviewed work from the primary checkout when no managed assignment exists.

## Functional Requirements

### R1. Durable reviewed-tree receipts

1. A passing task-boundary review must seal the exact staged Git tree reviewed by that verdict.
2. Each receipt must bind the canonical repository identity, provider-root session, checkout mode, parent commit, plan lineage, wave, reviewed task IDs, tree object, and durable receipt object.
3. A newer passing receipt must supersede the prior receipt for the same session and repository.
4. A failing review must not replace the prior passing receipt.
5. A passing verdict must not advance tasks when receipt sealing fails.

### R2. Cumulative review-index preparation

1. Before task review, IronClaude must build a candidate index from the latest passing receipt, or from `HEAD` when no receipt exists.
2. It must stage only the submitted tasks' exact `allowed_files` into the candidate.
3. It must preserve additions, modifications, deletions, renames, modes, and symlinks exactly.
4. It must reject malformed paths, repository escapes, and unexpected staged entries.
5. It must promote the candidate with Git index locking and leave the real index unchanged on failure or a concurrent race.

### R3. Commit-intent ordering

1. The trusted `UserPromptSubmit` boundary must restore and validate the latest reviewed index before it records `/commit` or `/commit-and-push` authority.
2. Authority must bind the already-prepared exact tree, parent, branch, repository, checkout mode, destination, and provider-root session.
3. The later workspace-manager operation must not restage files.
4. Drift in content, mode, deletion state, parent, branch, repository identity, checkout mode, or receipt identity must block intent issuance or consumption.
5. Missing receipt objects must require a fresh review; IronClaude must not infer reviewed bytes from the live working tree.

### R4. Unassigned primary-checkout commits

1. A professional-mode session that legitimately uses the primary checkout without a managed assignment must be able to receive direct human commit authority.
2. Primary authority must bind the repository, provider-root session, current branch, parent, and reviewed tree without inventing a managed assignment.
3. An unassigned primary commit must update only the verified current local branch.
4. It must not perform managed-worktree integration or cleanup.
5. Managed-assignment commit and integration behavior must remain unchanged.
6. A checkout-mode change after authorization must invalidate authority.

### R5. Review checklist resolution

1. Code review must resolve one canonical checklist path from the installed skill or plugin root.
2. Resolution must work from the repository root, `commander/`, and installed-plugin working directories.
3. A missing or unreadable checklist must stop review before grading with an explicit infrastructure error.
4. IronClaude must not silently substitute generic checks.

### R6. Authority and safety invariants

1. Human-only commit, commit-and-push, and push authority must remain single-use, exact, session-bound, repository-bound, and provider-native.
2. `/commit` must never push.
3. Commander must remain unable to push.
4. Cross-session, cross-repository, expired, replayed, model-generated, or malformed authority must fail.
5. Every failure must preserve the working tree, real index, latest valid receipt, and existing Git refs.
6. Receipt cleanup must occur only after verified commit completion, explicit assignment abandonment, or repository/session retirement.

## Acceptance Requirements

1. Real-repository tests must reproduce stash/pop silently unstaging earlier work and prove exact cumulative recovery.
2. Tests must cover multiple PM loops, overlapping paths with newest-review precedence, all supported Git entry types, failing-review preservation, seal failure, unexpected staged paths, every drift class, missing objects, and index-lock races.
3. Tests must prove managed and unassigned-primary commit behavior, authority denial cases, no-push behavior, checklist working-directory independence, and visible checklist failure.
4. Claude and Codex must receive equivalent behavior.
5. Focused and full hook, state-manager, workspace-manager, Commander, and plugin validation suites must pass.
6. IronClaude reinstall must be the final implementation task, followed by same-session runtime and diagnostic verification.
7. The implementation plan receives exactly one blind plan review. Review findings are repaired in the same plan without a second blind review.
8. Execution defects are repaired in place or through bounded follow-up tasks.

## Out of Scope

- `Monitor` enforcement
- Stash automation
- Automatic conflict resolution
- General artifact provenance
- New Git transport or messaging protocols
- Configurable receipt policies
- Push-authority changes
- Unrelated roadmap work
