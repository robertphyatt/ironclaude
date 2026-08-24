# Professional-Mode Re-entry Authority Design

> **Created:** 2026-08-16
> **Status:** Design Complete

## Summary

Professional-mode deactivation ends an IronClaude authority epoch. While mode is
off, the operator may direct Git and repository work without IronClaude workflow,
review, worktree, staging, commit, or push enforcement. Reactivation must not
silently revive receipts, plans, assignments, or staged-state assumptions from the
prior epoch.

This design makes re-entry explicit and auditable. Clean operator-authorized
PM-off work becomes the baseline of a new authority epoch. Dirty or conflicting
state is inspected without mutation. IronClaude first persists and displays the
exact recovery options, including every mutating Git command and its expected
effect. A subsequent direct-human instruction that unambiguously selects one of
those disclosed options may authorize it without redundant confirmation;
otherwise IronClaude presents a client-native `AskUserQuestion` dialogue.
Selecting an option grants authority for exactly that recovery sequence.
IronClaude then performs the work; it never hands Git or worktree commands back
to the operator.

## Chosen Approach

### Authority epochs with command-bound recovery capabilities

An `on -> off` transition closes the current authority epoch. An `off -> on`
transition opens a new epoch only after repository state and dormant IronClaude
authority agree. Historical receipts and plans remain durable evidence, but they
cannot enforce against work performed while mode was off.

Two rejected approaches define important boundaries:

- **Task-path conflict override:** allowing submitted paths to overwrite stale
  receipt conflicts would silently change reviewed authority and weaken the
  fail-closed candidate builder.
- **Retrospective review of PM-off work:** forcing operator-authorized off-period
  work through review would contradict the PM-off contract and reproduce the
  refusal deadlock this design fixes.

## Architecture

### 0. Same-session recovery bootstrap

The current provider-root session contains both a completed blind-review history
and a conflicting active receipt. Moving to a fresh session would discard the
session-bound review authority and require a prohibited second blind review.
Instead, one PM-off bootstrap repairs the two missing lifecycle operations, then
returns to this exact provider-root session and lineage.

The first operation permits one post-advisor plan-artifact reseal only while the
session is in `final_plan_prep`, every task remains pending, the lineage contains
an earlier canonical `HAS-ISSUES` followed by `advisor-remediated` for the current
plan hash, and no candidate or task grade exists. It creates or reuses one exact
inactive receipt while preserving the active receipt, index, working bytes, Git
refs, lineage, and review history.

The second operation runs only when Task 1 candidate preparation authenticates a
conflicting active receipt from an older lineage. It requires the current active
plan-artifact receipt, current-lineage advisor evidence, a wholly submitted wave,
no pending candidate or current-wave grade, and exact authentication of every
historical receipt path as cumulative reviewed authority. Current task scope
authorizes only new or changed submitted paths; it does not reauthorize historical
receipt bytes, and any historical-path drift remains fatal. Candidate creation and
exact active-receipt retirement share one transactional promotion boundary. The
stale ref remains reachable. Any mismatch fails without semantic grade, task
unwind, receipt retirement, index mutation, or operator Git handback.

If task-boundary review finds that immutable plan wording contradicts the
operator-authorized recovery, one additional same-lineage reseal is available
only after the exact submitted wave receives a task-boundary F verdict and is
reopened. The active artifact receipt and current plan hash must still match the
canonical `HAS-ISSUES` plus `advisor-remediated` chain; no candidate may remain.
The reseal creates or reuses one inactive replacement and enters
`final_plan_prep`, allowing a corrected plan reload and advisor-remediation record
without another blind plan review. No-candidate unwind authentication includes
the exact branch as well as repository, checkout, `HEAD`, receipt, lineage, and
wave.

### 1. Authority-epoch ledger

The state manager records a durable epoch identifier and mode transitions. An
epoch-close record binds the provider-root session, canonical repository and
checkout, branch, `HEAD`, index tree, working-state inventory, active and pending
receipt identifiers, plan lineage, and managed-worktree assignment generation.

Deactivation must always remain available. If any repository observation fails,
mode still becomes `off`; the epoch records the missing or unreadable evidence.
That uncertainty is handled during re-entry rather than used to refuse
deactivation.

### 2. Re-entry inspector

The inspector is read-only. It compares epoch-close evidence with current
canonical state and returns one typed classification:

- unchanged and clean;
- clean with off-period commit movement;
- dirty with a matching post-disclosure operator selection;
- dirty and ambiguous;
- identity, branch, lock, ref, index, assignment, or repository mismatch.

Inspection reports exact paths and entry classes, including tracked, untracked,
deleted, renamed, executable-mode, and symlink state. It does not stage, clean,
reset, stash, copy, commit, move a ref, or change mode.

### 3. Operator-guidance renderer

IronClaude generates and durably records the options before any instruction can
authorize them. When a later trusted `UserPromptSubmit` event unambiguously
selects one disclosed option, IronClaude proceeds without redundant confirmation.
Pre-disclosure, generic, stale, or mismatched instructions cannot mint authority.
When no matching post-disclosure selection exists, IronClaude uses one
client-native `AskUserQuestion` dialogue. Each option includes:

- the recovery reason and current facts;
- exact repository, checkout, paths, refs, branch, `HEAD`, and index identity;
- every mutating Git command as an ordered argument vector;
- the expected effect and reversibility of each command;
- preservation refs or backups created before destructive work;
- compensation commands that may run after partial failure;
- a recommended option and the reason it is safer or better suited.

The dialogue must never reduce the choice to vague labels such as "fix it" or
"clean the worktree." The operator must be able to understand exactly which Git
mutations their selection authorizes.

### 4. Recovery capability

The selected answer or matching post-disclosure direct-human event creates a
server-held, single-use recovery capability. It is bound to:

- provider-root session and client;
- canonical repository and checkout identity;
- authority epoch and workspace assignment generation;
- observed branch, `HEAD`, index tree, paths, modes, blobs, and refs;
- exact ordered mutating command argument vectors;
- expected effects and permitted compensation commands;
- nonce, expiry, and one-time consumption state.

Agent prose, a generic or pre-disclosure approval, or possession of the command
text cannot mint or broaden the capability. Only a trusted operator-choice event
that follows and exactly matches the server-held disclosure may do so.

### 5. Trusted recovery executor

The executor revalidates every binding immediately before the first mutation. It
runs only the listed commands, in order, without a shell. Read-only Git
observations may verify preconditions and effects; every mutating command must
appear in the approved sequence.

Professional mode retains two disjoint Git-mutation lanes. Existing
workflow-controlled staging admits only narrow `git add` operations needed to
construct task review candidates; it grants no recovery, ref, worktree, commit,
or push authority. Recovery execution is the only new general Git-mutation
authority introduced here and requires the exact capability. Guards continue
blocking equivalent recovery commands through Bash and all other mutating entry
points. The capability does not imply permission for another repository, wider
paths, a different ref, an unlisted fallback, commit, or push. Commit and push are
authorized only when the selected option explicitly lists them.

### 6. Re-entry transaction

Activation remains exactly `off` while guidance or recovery is pending. After the
Git sequence succeeds and its effects are verified, one state transaction:

1. preserves old receipt refs and audit evidence;
2. retires stale pending receipts;
3. supersedes dormant active receipts and the interrupted plan as historical
   authority;
4. binds current `HEAD` and index state as the new epoch baseline;
5. clears stale review flags and task-review state;
6. changes professional mode to `on`.

The transaction is idempotent. An exact-on activation does not disturb an active
plan or receipt.

## Data Flow

```text
operator requests deactivation
  -> record epoch-close evidence where available
  -> set mode off even if evidence is incomplete

operator requests activation
  -> read-only re-entry inspection
  -> generate, persist, and display exact options, commands, and effects
  -> matching direct-human selection after disclosure?
       yes -> mint scoped authority without redundant confirmation
       no  -> AskUserQuestion with exact commands, effects, and recommendation
  -> trusted post-disclosure choice mints one-shot recovery capability
  -> revalidate all bindings
  -> trusted executor runs only approved mutations
  -> verify exact effects
  -> atomically supersede dormant authority and establish new baseline
  -> set mode on
```

## Error Handling

- **Observation failure during deactivation:** mode still becomes off; missing
  evidence is recorded and requires explicit re-entry resolution.
- **State drift before execution:** reject without consuming the capability.
  Reinspect and present a new command list for fresh operator approval.
- **Unlisted mutation becomes necessary:** stop. Do not improvise. Present the
  additional command, effect, and revised sequence through a new dialogue.
- **Command failure:** stop at the failed command. Run compensation only when it
  was included in the approved option and its preconditions still match.
- **Partial Git success with state-transaction failure:** preserve recovery refs
  and exact execution evidence; mode remains off. Retry only the idempotent state
  finalization after revalidation.
- **Identity, repository, branch, index, ref, or assignment mismatch:** fail
  closed without mutation and identify the mismatched fields.
- **Destructive disposition:** require exact affected paths and effects. Create
  and verify a recovery ref first whenever preservation is technically possible.
- **Commit or push:** blocked unless explicitly present in the chosen command
  sequence. Push authority never follows from cleanup, sync, adoption, or receipt
  recovery.

## Testing Strategy

Tests use real temporary Git repositories and isolated state/workspace databases.
They cover:

- deactivation with complete, partial, unreadable, and missing repository
  evidence;
- clean unchanged and clean off-period-commit re-entry;
- current incident: a clean PM-off commit overlapping an old active receipt
  supersedes dormant authority and permits a fresh plan without merging receipt
  bytes;
- dirty tracked, untracked, deleted, renamed, executable, symlink, staged, and
  mixed-index cases;
- rejection of generic, stale, and pre-disclosure instructions;
- acceptance of exact post-disclosure operator selection without redundant
  confirmation versus state that requires `AskUserQuestion`;
- exact command/effect rendering, recommendation rationale, and Claude/Codex
  parity;
- capability binding, expiry, replay, nonce, client, session, repository,
  checkout, assignment-generation, `HEAD`, index, ref, path, and argument drift;
- rejection of equivalent raw Git through Bash while PM is on;
- rejection of unlisted fallback, commit, and push commands;
- approved compensation, partial failure, and idempotent finalization;
- historical receipt/ref/plan preservation and absence of fabricated grades;
- exact-on idempotency and exact-off non-enforcement;
- executable mutation tests that weaken capability checks, command equality,
  state revalidation, or atomic supersession and require targeted failures;
- fresh installed Claude and Codex behavioral acceptance after release.

## Scope

This loop implements authority epochs, re-entry inspection, command-bound
recovery approval, trusted execution, and stale-authority supersession. It also
repairs the current receipt conflict so the next PM loop starts from a clean,
auditable baseline.

The following remain separate later loops:

- automatic managed-worktree allocation, adoption, synchronization,
  finalization, rescue, and reaping;
- ambient shell-command observability;
- lightweight operational-action lanes and live-process safety;
- get-back-to-work context-anxiety behavior;
- final cross-provider stability certification.

The worktree loop must reuse this recovery capability rather than invent another
Git-approval path.
