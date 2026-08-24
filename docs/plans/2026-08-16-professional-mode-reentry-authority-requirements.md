# Professional-Mode Re-entry Authority Requirements

> **Created:** 2026-08-16
> **Status:** Approved Requirements

## R0. Same-session recovery bootstrap

1. Implementation must preserve the current provider-root session, plan lineage,
   canonical `HAS-ISSUES`, `advisor-remediated` evidence, active receipt, index,
   working bytes, and Git refs; it must not obtain another blind plan review.
2. One post-advisor plan-artifact reseal is permitted only in `final_plan_prep`
   when every task is pending, no review candidate or grade exists, and the
   active artifact receipt matches the current lineage and plan hash.
3. That reseal must create or replay one exact inactive receipt, write one audit,
   and preserve the active receipt, real index, working bytes, and existing refs.
4. A conflicting active review receipt may be superseded only when it belongs to
   an older lineage, its object and checkout are authenticated, the current plan
   has exact advisor evidence, the whole wave is submitted, no candidate or
   current-wave grade exists, and every historical receipt path remains
   authenticated cumulative reviewed authority. Current submitted-task scope
   gates only new or changed task paths; historical-path drift remains fatal.
5. Candidate creation, stale-authority retirement, audit, and index promotion
   must fail or succeed as one compensated operation. The stale receipt ref and
   all review history remain reachable.
6. Missing or changed identity, plan, task, receipt, ref, branch, `HEAD`, index,
   artifact, or advisor evidence must fail before task unwind or operator handback.
7. Bootstrap installation must preserve the exact terminal session. A new
   provider-root session is not a substitute for same-lineage review authority.
8. When task-boundary review exposes a contradiction in the immutable plan
   artifacts, one same-lineage post-F reseal may run only for the exact reopened
   wave, exact task-boundary F grade, current plan hash, existing canonical
   `HAS-ISSUES` plus `advisor-remediated` evidence, zero pending candidates, and
   unchanged active artifact receipt. It enters `final_plan_prep` for plan reload
   without another blind review. Exact replay is idempotent; every mismatch fails
   without changing workflow, receipt, index, working bytes, or refs.
9. An authenticated no-candidate unwind must bind canonical repository, checkout,
   branch, `HEAD`, receipt, lineage, and wave before changing task or review state.

## R1. Authority epochs

1. Every professional-mode `on -> off` transition must close the current
   IronClaude authority epoch.
2. Every `off -> on` transition must establish a new authority epoch before
   reporting activation success.
3. Exact-on activation must be idempotent and must not disturb current plan,
   receipt, task, assignment, index, or worktree state.
4. Historical authority must remain auditable and must not enforce against work
   performed while mode was off.

## R2. Deactivation

1. Deactivation must remain available even when repository evidence is missing,
   unreadable, dirty, conflicted, or malformed.
2. The epoch-close record must capture all repository, plan, receipt, and
   assignment evidence that can be authenticated without mutation.
3. Missing evidence must be recorded as uncertainty, not silently omitted.
4. Deactivation must not stage, clean, reset, stash, copy, commit, push, move
   refs, alter assignments, or retire receipts.

## R3. Re-entry inspection

1. Activation from exact off must perform a read-only repository and authority
   inspection before changing mode.
2. Inspection must bind provider-root session, client, canonical repository,
   checkout, branch, `HEAD`, index tree, assignment generation, receipts, plan,
   and dirty paths.
3. Dirty inventory must distinguish tracked, staged, unstaged, untracked,
   deleted, renamed, mode, executable, symlink, conflict, and ignored state.
4. Inspection must make no Git, database, worktree, receipt, task, plan, or mode
   mutation.

## R4. Operator guidance

1. IronClaude must first persist and display exact command-bearing options. A
   subsequent trusted direct-human instruction that unambiguously selects one
   disclosed option must not trigger redundant confirmation; pre-disclosure,
   generic, stale, or mismatched instructions cannot authorize recovery.
2. If disposition is ambiguous, IronClaude must use a client-native
   `AskUserQuestion` dialogue.
3. Each option must state the problem, exact affected state, recommendation,
   and why that option is recommended.
4. Each option must list every mutating Git command with complete argument
   vectors and execution order.
5. Each command must include its expected effect, affected paths and refs,
   reversibility, and any permitted compensation.
6. Vague choices that conceal the Git sequence are forbidden.
7. IronClaude must never ask the operator to run the listed commands.

## R5. Recovery capability

1. Selecting an option must mint one server-held, single-use recovery
   capability.
2. The capability must bind session, client, repository, checkout, authority
   epoch, assignment generation, branch, `HEAD`, index, paths, refs, exact
   command argument vectors, expected effects, compensation commands, nonce,
   expiry, and consumption state.
3. Agent prose, generic or pre-disclosure approval, or command possession must
   not mint or broaden recovery authority. A direct-human choice must follow and
   exactly match the server-held disclosure ID, commands, order, and effects.
4. State drift must invalidate the capability without consuming it.
5. Replay, expiry, cross-session, cross-client, cross-repository, cross-checkout,
   path widening, ref substitution, argument mutation, reordering, or additional
   commands must fail before mutation.

## R6. PM-on Git enforcement

1. Professional mode must continue blocking every non-read-only Git command by
   default except its existing narrow workflow-controlled task-staging lane.
2. Workflow staging may admit only task-scoped `git add` used to construct review
   candidates. It grants no recovery, ref, worktree, commit, or push authority.
3. Recovery execution is the only new general Git-mutation exception introduced
   by this design. It does not replace, broaden, or derive authority from staging.
4. The recovery exception applies only when a typed recovery need exists, the complete
   Git sequence and effects were shown, and the operator selected that option.
5. Only the trusted recovery executor may consume the capability.
6. Equivalent Git mutations through Bash or another tool must remain blocked.
7. Read-only Git inspection remains permitted.
8. Commit and push remain blocked unless they are explicitly listed in the
   selected recovery sequence; cleanup or synchronization never implies them.

## R7. Recovery execution

1. The executor must revalidate every capability binding before mutation.
2. It must execute only listed commands, in order, without a shell.
3. It must verify the expected effect after each command.
4. An unlisted fallback or newly required mutation must stop execution and
   require a new operator dialogue and capability.
5. Compensation may run only when listed in the approved option and still safe
   under current state.
6. IronClaude must perform all approved mechanics; operator terminal handback is
   forbidden.

## R8. Re-entry finalization

1. Professional mode must remain exactly off while guidance or recovery is
   pending.
2. After Git recovery succeeds, one transaction must retire stale pending
   receipts, supersede dormant active receipts and interrupted plan authority,
   clear stale review flags, establish the current repository baseline, and set
   mode on.
3. Receipt refs, historical plan records, audits, and recovery evidence must
   remain reachable.
4. No semantic review grade may be fabricated or inferred from re-entry.
5. Transaction failure must leave mode off and preserve recoverable Git state.
6. Exact retry after verified Git success must finalize idempotently without
   repeating mutations.

## R9. Current deadlock acceptance

1. The existing clean operator-authorized PM-off commit must be recognized as
   belonging to the off epoch.
2. Its overlap with an old active receipt must not trigger receipt-tree merge or
   retrospective review.
3. Dormant receipt and plan authority must be preserved historically and
   superseded for enforcement.
4. Current clean `HEAD` must become the baseline of a fresh PM epoch.
5. A new plan must prepare and review candidates without encountering the stale
   overlapping receipt.

## R10. Verification

1. Tests must use real temporary Git repositories and isolated databases.
2. Tests must cover every dirty entry class, missing epoch evidence, identity and
   state drift, exact command rendering, recommendation rationale, capability
   replay/expiry/widening, command mutation, partial failure, compensation,
   atomic finalization, and idempotency.
3. Tests must prove unrelated mutating Git remains blocked while PM is on.
4. Tests must prove exact off retains the operator-authority contract.
5. Tests must prove no manual Git or worktree instruction is emitted.
6. Tests must prove historical receipts, refs, plans, index bytes, working bytes,
   and grades are preserved as specified.
7. Executable mutation tests must kill weakened authority and atomicity checks.
8. Focused state-manager, hook, workspace-manager, Commander, skill-contract,
   and provider-parity suites must pass.
9. The complete repository suite, including the complete state-manager Vitest
   suite, must pass before release.
10. Fresh installed Claude and Codex sessions must demonstrate the behavior; file
    hashes or installation output alone are insufficient.

## R11. Scope boundaries

1. This loop must not implement full managed-worktree lifecycle automation.
2. It must expose the recovery capability as the sole new general
   mutation-authority primitive for the following worktree loop; existing narrow
   workflow-controlled task staging remains separate.
3. It must not add retrospective PM-off review, generic Git bypass, automatic
   conflict resolution, implicit commit, or implicit push.
4. Ambient shell observability, operational lanes, context-anxiety behavior, and
   final stability certification remain separate loops.
