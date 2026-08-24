# Provider-Neutral Review Entry and Recovery Requirements

**Status:** Approved

**Design:** `docs/plans/2026-08-13-provider-neutral-review-entry-design.md`

## R1. Native client parity

1. Claude Code must enter task review through its `Skill` tool.
2. Codex must enter task review through `$ironclaude:code-review --task-boundary` without requiring a generic `Skill` tool.
3. Both clients must call the same authenticated `prepare_review_candidate` operation.
4. Hook instructions must name the active client's supported invocation.
5. An unknown client must fail with an explicit infrastructure error.

## R2. Authoritative review entry

1. `prepare_review_candidate` must own review-candidate creation and re-entry.
2. Successful preparation must set `workflow_stage=reviewing`, `active_skill=code-review`, and `testing_theatre_checked=0`.
3. Candidate receipt state and review-entry state must succeed or compensate as one logical operation.
4. `executing-plans` and orchestration code must not pre-create review candidates.
5. Infrastructure failure must record no semantic grade.

## R3. Exact idempotent retry

1. Preparation must reuse one existing pending candidate only when all authenticated bindings remain equal.
2. Revalidation must cover session, repository, checkout path and mode, workspace GUID, plan lineage, wave, submitted task IDs, declared paths, branch, parent, receipt ref, receipt object, receipt tree, and staged tree.
3. Exact reuse must return the same receipt and report `reused: true`.
4. Exact reuse must create no Git ref, rewrite no index, and alter no task status.
5. Any mismatch must fail closed and preserve all candidate and Git evidence.

## R4. Report-only review access

1. Review-pending enforcement must support Claude Code and Codex tool vocabularies.
2. It may admit only native skill loading, candidate preparation, required reads, permitted tests, testing-theatre operations, and verdict recording.
3. It must block implementation writes, Git mutations, arbitrary shell commands, commits, pushes, and direct task advancement.
4. Required staged-diff, source, plan, and canonical-checklist reads must work during `reviewing`.

## R5. Testing-theatre integrity

1. Every review entry must reset `testing_theatre_checked` to zero.
2. Each client must load testing-theatre detection through its native skill mechanism.
3. Only successful testing-theatre completion may set the flag to one.
4. Verdict recording must reject a zero or unreadable flag.
5. A prior review's flag must never authorize a later review.

## R6. Verdict recovery

1. An A or B verdict must seal the exact pending candidate and advance submitted tasks in the existing transaction.
2. C, D, or F must retire the pending candidate, preserve the active receipt, reopen submitted tasks, and return to execution.
3. Receipt validation failure must record no semantic grade.
4. Existing semantic grading rules must remain unchanged.

## R7. Retreat cleanup

1. Retreat from execution or review must retire pending candidates in the same database transaction as workflow retreat.
2. Retreat must preserve the active receipt, working tree, index, staged bytes, commits, and active receipt ref.
3. Retreat must reset `review_pending`, `review_block_count`, `testing_theatre_checked`, and `active_skill`.
4. Transaction failure must leave workflow and review state unchanged.
5. Repeated retreat cleanup must be idempotent.

## R8. Proven orphan reconciliation

1. A new plan transition may retire an older pending candidate only when plan history proves that a recorded retreat orphaned it.
2. Proof must bind the same session and repository and a different, retired plan lineage.
3. Reconciliation must write an audit entry.
4. Unproven pending candidates during active execution must fail closed with exact mismatch details.
5. The repair must reconcile current receipt 22 without manual SQLite work.

## R9. Failure preservation

1. Candidate-creation failure must restore the original index.
2. Cleanup may remove only candidate state and refs created by the failed attempt.
3. Identity, content, or lifecycle mismatch must preserve receipts, refs, tasks, index bytes, and working bytes.
4. Errors must identify the failed binding or lifecycle condition.
5. Recovery must not reset the index, reconstruct reviewed state from live bytes, fabricate a grade, or advance tasks.

## R10. Bootstrap execution

1. The replacement plan must use one bounded bootstrap implementation task because the installed review gate cannot review incremental repairs to itself.
2. The task must implement, test, build, validate, cachebust, and reinstall the repair before submission.
3. Reinstallation must be the final mutation; only restart, runtime verification, task submission, and review may follow.
4. The repaired runtime must complete its own task-boundary review in the same native Codex task.
5. The replacement plan must carry forward, not reimplement, reviewed Tasks 1-6 from the interrupted reviewed-index plan.
6. Completion must satisfy the interrupted plan's Task 7 acceptance and seal the cumulative reviewed tree.

## R11. Verification

1. Tests must cover native Claude/Codex entry parity, exact retry, every reuse mismatch, testing-theatre reset, review access, verdict recovery, retreat cleanup, and orphan reconciliation.
2. Tests must prove that writes remain blocked during report-only review.
3. Tests must prove infrastructure failures record no grade.
4. Full hook, state-manager, Commander, parity, version, and plugin-validation suites must pass.
5. Installed-runtime diagnostics and same-task review must pass after restart.
6. The plan may receive exactly one blind plan review. Review findings must be repaired in place without a second blind plan review.

## R12. Scope exclusions

This effort must not add generalized skill routing, change semantic grading, broaden review shell access, reconstruct receipts from unreviewed bytes, change professional-mode-off authority, repair worktree lifecycle behavior, add an operational-action lane, or implement unrelated roadmap items.
