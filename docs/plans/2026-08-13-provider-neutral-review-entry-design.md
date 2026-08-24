# Provider-Neutral Review Entry and Recovery Design

**Status:** Approved

**Date:** 2026-08-13

**Scope:** Repair task-boundary review entry, retry, and retreat behavior for Claude Code and Codex.

## Problem

IronClaude's task-review path assumes Claude Code emits a `Skill` tool event. Codex loads `$ironclaude:code-review --task-boundary` as prompt context and emits no equivalent tool event. The mismatch caused four connected failures:

1. Codex never entered `reviewing`, so review-safe reads remained blocked.
2. Hooks repeatedly instructed Codex to call a `Skill` tool that its runtime did not expose.
3. `prepare_review_candidate` rejected an exact retry after receipt 22 already existed.
4. Retreat cleared plan state but left receipt 22 pending and retained `testing_theatre_checked=1`.

Plugin reloads cannot repair this protocol mismatch. IronClaude needs one provider-neutral review handshake and client-specific activation instructions.

## Goals

- Give Claude Code and Codex equivalent task-review behavior through their native skill mechanisms.
- Make exact review re-entry safe after interruption or repeated invocation.
- Keep candidate identity, reviewed bytes, and semantic grades fail-closed.
- Clear review-scoped state when execution retreats.
- Recover the current orphaned receipt without manual SQLite changes.
- Preserve all staged work from the interrupted reviewed-index plan and complete its remaining runtime acceptance.

## Architecture

### Provider-neutral review handshake

`prepare_review_candidate` becomes the authoritative review-entry operation. It validates the authenticated session, canonical repository, checkout, active plan, wave, submitted tasks, branch, parent, index, and receipt state.

On success, it performs one of two actions:

- Create a new candidate when none exists.
- Reuse the existing candidate when every bound value still matches.

The successful operation enters `reviewing`, sets `active_skill=code-review`, and resets `testing_theatre_checked=0`. These state changes and receipt state form one logical operation. A partial failure cannot leave a pending candidate in an execution-only session.

### Client-native activation

IronClaude selects activation instructions by client:

- Claude Code invokes `ironclaude:code-review` through the `Skill` tool.
- Codex loads `$ironclaude:code-review --task-boundary` through its native skill surface.

Both paths call the same provider-native state-manager operation. Hooks must not instruct Codex to call a nonexistent generic `Skill` tool. Unknown clients fail with an explicit unsupported-invocation error.

The code-review workflow exclusively owns candidate preparation. `executing-plans` and orchestration code must not pre-create candidates.

### Strict candidate reuse

Reuse requires equality across:

- provider-root session;
- canonical repository identity;
- checkout path, checkout mode, and workspace GUID;
- plan lineage and wave;
- submitted task IDs and their declared paths;
- branch and parent commit;
- receipt ref, receipt object, and receipt tree; and
- current staged tree.

An exact retry returns the same receipt and reports `reused: true`. It creates no ref, rewrites no index, and changes no task status. Any mismatch remains a review-infrastructure failure.

### Review-pending access

The review gate admits only the complete report-only review path:

- native skill loading;
- candidate preparation;
- staged-diff, source, plan, and checklist reads;
- permitted test commands;
- testing-theatre analysis and status;
- review verdict recording.

The gate continues to block implementation edits, Git mutations, arbitrary shell commands, commits, pushes, and task advancement outside the verdict transaction. Tool matching must cover Claude Code and Codex vocabularies by capability rather than assume Claude-only tool names.

### Testing-theatre lifecycle

Every review entry resets `testing_theatre_checked` to zero. Claude Code and Codex load testing-theatre detection through their native skill mechanisms. Only successful completion sets the flag to one. An earlier task's flag cannot satisfy a later review.

### Retreat and orphan reconciliation

A retreat from execution or review atomically:

- retires pending review candidates;
- preserves the latest active reviewed receipt;
- resets `review_pending`, `review_block_count`, `testing_theatre_checked`, and `active_skill`; and
- preserves the working tree, index, staged bytes, commits, and active receipt ref.

If the database transaction fails, the retreat fails without partial workflow changes.

The next plan transition may reconcile a pending candidate only when durable state proves that a recorded retreat orphaned it. The proof requires a different current lineage and matching plan-history retreat evidence. IronClaude retires that candidate with an audit entry. During active execution, an unproven mismatch fails closed and reports its lineage, wave, task set, and identity differences.

This rule retires current receipt 22 automatically after the repair reaches the new runtime. It requires no operator command or direct database edit.

## Failure Handling

- **Exact retry:** Return the existing validated receipt with `reused: true`.
- **Identity or content drift:** Preserve the receipt, index, refs, tasks, and working bytes; record no grade.
- **Candidate creation failure:** Restore the original index and remove only newly created, unreferenced candidate state.
- **Review-entry state failure:** Compensate candidate creation and leave the session in its prior stage.
- **Testing-theatre failure:** Keep the flag at zero and block verdict recording.
- **Unknown client:** Return a client-specific infrastructure error.
- **C, D, or F verdict:** Retire the pending candidate, preserve the active receipt, reopen submitted tasks, and return to execution.
- **Retreat failure:** Preserve workflow and Git state for safe retry.
- **Orphan without proof:** Report the mismatch and preserve all evidence.

Infrastructure failures never fabricate or record semantic grades.

## Bootstrap and Completion

The installed runtime cannot review an incremental repair to its own review gate. The replacement implementation plan therefore uses one bounded bootstrap task:

1. Implement and test the complete repair.
2. Build and validate tracked runtime bundles.
3. Update the Codex cachebuster once.
4. Reinstall Claude Code and Codex as the task's final mutation.
5. Restart Codex and verify the same native task uses the new runtime.
6. Submit and review the bootstrap task through the repaired path.

The replacement plan carries forward the staged and reviewed work from Tasks 1-6 of `2026-08-12-reviewed-index-and-primary-commit`. It does not reimplement those tasks. Its final review completes the interrupted plan's Task 7 runtime acceptance and seals the cumulative reviewed tree.

Exactly one blind plan review applies to the replacement plan. Findings from that review are repaired in place without a second blind plan review. Task code re-reviews remain required after C, D, or F findings.

## Verification

Tests must prove:

- Codex enters `reviewing` without a Claude `Skill` event.
- Claude Code's `Skill` route produces equivalent state.
- An interrupted review reuses the exact receipt, ref, tree, and index.
- Every bound identity or content mismatch refuses reuse.
- Testing-theatre state resets per candidate and cannot leak across reviews.
- Both clients can perform required report-only operations while writes remain blocked.
- Stop and recovery messages name the correct native action.
- C, D, and F preserve the active receipt and reopen execution.
- Retreat atomically clears review-scoped state and retires its candidate.
- Recorded retreat evidence reconciles receipt 22 without manual intervention.
- Infrastructure failures record no semantic grade.
- Full hook, state-manager, Commander, Claude/Codex parity, version, and plugin-validation suites pass.
- The reinstalled runtime completes same-task review and diagnostics.

## Out of Scope

- Generalized skill-routing infrastructure
- Changes to semantic grading
- Broad shell access during review
- Receipt reconstruction from unreviewed bytes
- Professional-mode-off authority semantics
- Worktree freshness, ownership convergence, or manual-copy elimination
- Operational-action lanes
- Live-process and lock-resource protection
- Other roadmap work
