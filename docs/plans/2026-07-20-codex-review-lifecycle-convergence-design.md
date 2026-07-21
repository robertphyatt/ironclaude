# Codex Review Lifecycle Convergence Design

> **Created:** 2026-07-20
> **Status:** Design Complete
> **Roadmap loop:** Wave 1R corrective retreat
> **Authoritative inputs:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md`, `docs/plans/2026-07-20-v1-1-overall-roadmap.md`, `docs/plans/2026-07-20-codex-runtime-activation-fingerprint-design.md`, `docs/plans/2026-07-20-codex-runtime-activation-fingerprint.md`, and live Task 3 evidence from native task `019f7742-abd8-7c62-af7b-fe07189f1ffd`

## Summary

Wave 1R proved that the intended IronClaude cache is active in the same Codex task: the native task identity survived restart, source and installed-cache bytes matched, and `run_diagnostics(expected_runtime)` passed 13/13. Its final behavioral acceptance still failed closed. Codex does not emit Claude's `Skill` tool event, so `skill-state-bridge.sh` never changed the workflow from `executing` to `reviewing`. Task 3 review therefore ran while the session remained `executing`, making the required review return `mark_executing changed:true` impossible without fabricating a transition.

The correction uses the existing MCP lifecycle. `submit_task` becomes the provider-neutral review-entry coordinator. Sequential submission, or the final submission in a parallel batch, atomically establishes `reviewing`; intermediate parallel submissions remain in `executing` with no review gate until every already-claimed sibling finishes. No new MCP tool, workflow stage, lifecycle object, transcript mechanism, or provider-neutral subsystem is introduced.

## Confirmed Root Cause

Review entry is currently owned by `skill-state-bridge.sh`, which reacts only to a Claude `Skill` tool call. Codex exposes IronClaude skills as injected skill content rather than that tool event. Consequently:

1. `submit_task` sets the task to `submitted` and `review_pending=true` but leaves workflow stage `executing`;
2. Codex cannot trigger the bridge's `executing -> reviewing` transition;
3. the review-pending hook blocks even read-only review inspection because it expects the unavailable `Skill` call;
4. after a passing verdict, `mark_executing` would be an `executing -> executing` no-op with `changed:false`;
5. after a failing verdict, the existing submitted task and review gate can remain locked with no supported rework transition.

The live Task 3 result and existing idempotency tests jointly prove this behavior. Same-target transition calls must remain side-effect-free; the fix must create the missing real transition, not weaken idempotency.

## Selected Architecture

### Provider-neutral, parallel-safe review entry

`submit_task` performs one transaction that re-reads the persisted session and requires its source stage to still equal `executing`, then revalidates the claimed task and changes its status from `in_progress` to `submitted`. It then counts other current-wave `in_progress` tasks inside the same transaction. No literal or pre-transaction source-stage value is trusted:

- zero siblings: set `review_pending`, change workflow stage to `reviewing`, and return `review_ready:true`, `changed:true`, `from:"executing"`, `to:"reviewing"`;
- one or more siblings: leave workflow stage `executing`, keep both review-gate fields clear, and return `review_ready:false`, `changed:false`, `from:"executing"`, `to:"executing"` plus the exact remaining count.

Subagent-parallel execution claims every released task before dispatch. Therefore intermediate completion cannot stop sibling writers, while the final completion opens one task-boundary review over the submitted batch. Sequential execution has no other `in_progress` sibling, so its existing per-task review cadence remains unchanged. Pending, unclaimed tasks do not delay a sequential review. A second submission while `reviewing` is rejected before the transition helper's same-target no-op path. Any failed prerequisite rolls back every part of the submission transaction.

Claude's existing skill bridge remains compatible. When Claude subsequently invokes the code-review skill, the bridge sees the target stage already equals `reviewing` and performs no mutation or duplicate audit. Codex needs no synthetic `Skill` event because the review-ready submission already established the correct stage.

### Review-safe inspection

When `review_pending=true` and workflow stage is `reviewing`, `plan-task-context.sh` must not issue its blanket "invoke Skill" block. Current source already exits that hook unless workflow stage is exactly `executing`; focused characterization protects that behavior.

`professional-mode-guard.sh` owns the combined reviewing allowlist. Current source permits its docs-path exception before the reviewing restriction and before executing `allowed_files`, so docs edits can bypass both protections. Narrow the exception to the actual design/plan stages: `brainstorming`, `design_ready`, `design_marked_for_use`, `plan_ready`, `plan_marked_for_use`, and `final_plan_prep`, while retaining existing design-consumption checks. It does not apply in `idle`, `debugging`, `plan_interrupted`, `execution_complete`, `executing`, or `reviewing`. In `reviewing`, read-only Git and file inspection continue through the established allowlist while Bash mutation and every `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, source mutation, and docs mutation are rejected. In `executing`, docs writes continue to the normal exact `allowed_files` check. No new command parser or permission surface is introduced.

MCP tools bypass shell hooks, so handler stages remain part of the same review-safe contract. `claim_task` and `submit_task` require `executing`; `get_next_tasks` cannot advance a wave with submitted work; task-boundary `record_review_verdict`, `set_testing_theatre_checked`, failed-review rework, and guarded review return remain the only intended review lifecycle mutations. Focused tests must prove `set_testing_theatre_checked` succeeds in `reviewing` for the provider-native session while task/grade/stage state remains unchanged except that flag and its one audit.

### Review skill contract

`code-review/SKILL.md` becomes strictly read-only for task-boundary inspection. It does not apply AUTO-FIX edits while `reviewing`. It must record every task-boundary verdict successfully or fail closed; narrative grades cannot substitute for MCP state. A/B returns control to executing-plans for preflight plus one guarded `mark_executing`. C/D/F recording itself atomically reopens the exact submitted batch and returns to `executing`; the skill reports rework and does not offer proceed-anyway/TODO choices. Standalone review remains informational and state-neutral.

Model-facing `submit_task`, `claim_task`, `record_review_verdict`, and `mark_executing` descriptions must state their actual stages, batch behavior, rework behavior, and return contract. Shipped executing-plans and code-review skills must express the same lifecycle.

### Passing and failing verdicts

For A/B task-boundary verdicts, existing batch advancement remains authoritative. The workflow stays `reviewing` until the orchestrator preflights and calls the existing `mark_executing`; that guard requires a current-wave A/B task-boundary grade and zero still-submitted current-wave tasks, preventing an older grade from authorizing a new submission. The real `reviewing -> executing` return must report `changed:true`.

For C/D/F task-boundary verdicts, verdict recording atomically returns submitted tasks to `in_progress`, clears the review gate, and changes workflow stage from `reviewing` to `executing`. This is the existing task's rework path, not task advancement. The executor can fix the reviewed findings, resubmit, and obtain a fresh review. Informational reviews do not mutate execution state.

## State Invariants

- No submitted task with `review_pending=true` may remain in `executing` after successful `submit_task`.
- Intermediate parallel submissions may remain `submitted` in `executing` only while `review_pending=false` and at least one already-claimed current-wave sibling remains `in_progress`.
- The final parallel submission and every sequential submission enter `reviewing` atomically and expose `review_ready:true`.
- No failed `submit_task` may partially change task status, review flags, workflow stage, or audit state.
- `reviewing` plus `review_pending=true` permits only the established review-safe read-only surface.
- `claim_task`, a second `submit_task`, and wave advancement are blocked during `reviewing` without mutation.
- A/B task-boundary verdicts require `mark_executing` to perform a genuine different-stage transition.
- A historical A/B cannot authorize review return while any current-wave task remains `submitted`.
- C/D/F task-boundary verdicts reopen exactly the reviewed submitted tasks for rework; they never advance them.
- Task-boundary code review never edits files; verdict persistence failure blocks completion.
- Same-target bridge activation remains a silent, audit-free no-op.
- Session identity remains provider-native and unchanged throughout submission, review, rework, and review return.

## Error Handling

Submission validation occurs before mutation and inside the same database transaction as task status, remaining-in-progress count, gate decision, and any review entry. A database error or invalid task produces no partial state. Passing review return remains blocked until a valid A/B task-boundary grade exists and no current-wave submission remains ungraded. Failing review rework is atomic; if any task, flag, stage, or audit update fails, all changes roll back and the prior review state remains available for diagnosis.

Persisted session stage, task, in-progress sibling count, and submitted-set validation are re-read inside the transaction that consumes them. Pre-call validation supplies clear errors, but it is not trusted across the transaction boundary. Trigger-induced failures at session/task/grade/audit writes prove the rollback claim for intermediate/final submission, passing verdict, and failed-verdict rework.

Codex review inspection never receives mutation permission. If a command is not covered by the established reviewing allowlist, it remains blocked. The design does not infer skill activation from prompt text and does not add syntax-specific parsing.

## Testing Strategy

Focused state-manager tests must prove sequential/final-batch submission enters `reviewing`, intermediate parallel submission remains write-capable with `review_ready:false`, pending tasks do not delay sequential review, and failure paths roll back task, workflow, flags, and audit together. Tests cover transaction-local stale session-stage rejection, a two-task parallel wave through intermediate and final submissions with exact session/batch/gate/audit responses, second submission/claim/advance rejection during review, allowed testing-theatre flag mutation, and stale A/B rejection while a submission remains. Review-verdict tests must prove A/B leaves a valid `reviewing -> executing` return and C/D/F atomically reopens only the submitted reviewed batch for rework.

Hook tests must exercise the combined `plan-task-context.sh` and `professional-mode-guard.sh` path: reviewing `git status --short`, `cat`, and exact focused command `pytest commander/tests/test_code_review_skill.py -q` pass; unsafe Bash plus every write-tool type fails; the docs exception is restricted to named design/plan stages; executing docs writes reach `allowed_files`; and a redundant Claude code-review bridge activation produces no state or audit change. Commander contract tests must require parallel-batch orchestration, preflight current state, skip equal-target transitions, preserve exact `changed:true` review-return evidence, and keep code-review report-only/fail-closed with correct C/D/F rework semantics.

After focused and full suites pass, IronClaude must cachebust before its final build, reinstall from the confirmed local marketplace, fully restart Codex, reopen the same native task, revalidate the exact runtime fingerprint, and repeat Task 3 submission and review. Wave 1R closes only when the live review return reports `changed:true`. No commit or push is part of this corrective loop.

## Scope Boundaries

This design does not implement provider failover, Slack signaling, Commander multi-provider routing, episodic-memory validation, generalized prompt-to-skill parsing, or a new review-lifecycle MCP subsystem. Those remain in their assigned roadmap waves. This correction changes only the existing submission/review/rework transition ownership required to unblock Wave 1R faithfully.

## Preserved Prerequisites and Release Queue

Wave 0 already implemented and hot-deployed MP-W01–MP-W07, MP-W09, and MP-W10. This loop preserves them through the existing executing-plans blind-packet, review-order, source-verification, first-failure holistic-audit, coherent-regeneration, and human/machine parity regression tests; it does not claim to reimplement them. Wave 1 already implemented MP-W08 across every inventoried transition caller and handler. This corrective retreat does not repeat that completed loop. It protects the affected `submit_task`, code-review bridge, executing-plans caller, `mark_executing`, defensive same-target response, and transition/audit regressions; the existing Wave 1 validation remains authoritative for the full inventory. MP-W11 likewise remains a completed prerequisite protected by explicit session-identity and dispatch tests.

`docs/plans/2026-07-20-v1-1-overall-roadmap.md` is the durable, release-blocking queue required by the operator ledger:

- Wave 2: provider foundations;
- Wave 3: native role routing;
- Wave 4: automatic failover and conversation continuity;
- Wave 5: Slack controls and signaling;
- Wave 6: shadow mode and remote resilience;
- Wave 7: Codex professional-mode UX and hook compatibility;
- Wave 8: Codex episodic-memory completeness;
- Wave 9: required-skill availability and safe fallback;
- Wave 10: read-only Git guard hardening;
- Wave 11: `create_plan` call-shape hardening;
- Wave 12: release integration and documentation;
- Wave 13: requirement-by-requirement release verification, squash, and operator-gated push.

This loop neither weakens nor claims completion of those waves. Its validation artifact must cite the queue and keep them release-blocking.

The validation artifact must also reproduce and check the roadmap's explicit requirement-to-wave disposition so no active ID can disappear or move silently:

- Wave 2: MP-001, MP-002, MP-011, MP-012, MP-D04, MP-C01;
- Wave 3: MP-003, MP-006, MP-D01, MP-D05, MP-D10;
- Wave 4: MP-004, MP-007, MP-009, MP-010, MP-C02, MP-C03;
- Wave 5: MP-005, MP-008, MP-D02, MP-D03, MP-D06, MP-D07, MP-C04;
- Wave 6: MP-D08, MP-D09, MP-C05;
- Wave 7: queued Codex deactivation and status-parity requirements;
- Wave 8: queued episodic-memory completeness;
- Wave 9: queued required-skill availability and safe fallback;
- Wave 10: MP-C06;
- Wave 11: queued `create_plan` call-shape hardening;
- Wave 12: MP-013, MP-014, MP-C07;
- Wave 13: all active requirements, with MP-D08 final evidence emphasized.
