# Professional-Mode-Off Operator Authority Design

> **Created:** 2026-08-14
> **Status:** Design Complete
> **Scope:** IronClaude enforcement while professional mode is off

## Summary

When a trusted session state reports `professional_mode='off'`, IronClaude imposes no workflow,
review, worktree, staging, commit, push, intent, configuration, or private-transport controls.
Direct operator instructions govern, including ordinary prose requests and raw Git. IronClaude
must not demand professional-mode activation, a slash command, task review, or redundant
confirmation before executing an already explicit instruction.

Platform safety, filesystem permissions, credentials, and the operator's stated scope remain in
force. Commander's independent no-push role capability also remains unchanged.

## Verified problem

The current PreToolUse guard treats managed-worktree isolation and selected anti-tamper rules as
always-on. It runs those controls before its professional-mode check. Its mode-off branch still
calls the workspace adapter, redirects healthy assignments, and blocks damaged assignments.

The deactivation skill says only that professional-mode workflow enforcement is disabled. That
wording allowed an assistant to treat commit authority as an unconditional repository rule. In the
recorded failure, the operator explicitly requested a manual commit and push while professional
mode was off; the assistant refused three times and demanded `/commit-and-push`, professional-mode
activation, and task review.

## Approaches considered

### Exact mode-off fast path — selected

Read the authenticated professional-mode state before any IronClaude enforcement. Exact `off`
returns an unchanged allow result. All other states continue through existing enforcement.

This approach removes the contradiction at its source and preserves every PM-on control.

### Relax only Git authority — rejected

This would leave worktree routing, review gates, configuration guards, and private-transport guards
able to block an operator after deactivation.

### Delete workflow and workspace state on deactivation — rejected

Deleting assignments, receipts, or plan state would discard recovery evidence and could strand
work. Dormant state can remain without exerting authority.

### Add a generalized policy layer — rejected

The existing professional-mode state is the necessary policy boundary. A second framework would
add configuration and routing machinery without solving another approved requirement.

## Architecture

### Enforcement boundary

The main professional-mode guard reads session state before configuration protection, private
workspace transport fencing, worktree adaptation, workflow checks, or Git restrictions.

- Exact `off`: return success with the original input and no `updatedInput`.
- Exact `on`: preserve current enforcement.
- `undecided`, missing, malformed, or unreadable state: preserve existing fail-closed behavior.

Every other registered enforcement hook must either use the same exact-state boundary or prove it
already becomes inert while professional mode is off. This inventory covers PreToolUse,
PostToolUse, UserPromptSubmit, SubagentStop, and Stop hooks.

### Operator instructions

Repository instructions, direct-session templates, worker templates, and deactivation output state
the same conditional contract:

- PM on: IronClaude workflow and authority rules apply.
- PM off: IronClaude rules are inactive; explicit operator instructions govern.

An explicit request to commit, push, edit, clean, or run another operation needs no IronClaude
command envelope and no immediate confirmation. The assistant asks only when the operator omitted
a material target or destructive disposition, or when a separate platform safety boundary requires
confirmation.

Provider-rendered skill links retain their existing exact human-command recognition. Their UI
serialization never invalidates operator intent. The `commit`, `commit-and-push`, and `push`
skills branch on trusted professional-mode state: exact `off` performs the explicitly requested
raw Git operation without IronClaude intent or review requirements; exact `on` preserves the
existing server-held authority flow; every other state fails closed.

### Dormant state

Deactivation changes only the professional-mode field and its timestamp. It preserves assignments,
review receipts, workflow stage and rows, staged bytes, refs, and files.
Those records do not redirect, block, or authorize operations while professional mode is off.
Reactivation resumes normal PM-on validation and reconciliation.

### Commander boundary

Commander no-push remains a role capability independent of professional mode. The repair must prove
that Commander cannot execute a push even though direct sessions can obey an explicit operator push
request with professional mode off.

## Failure handling

- A trustworthy exact `off` read always reaches unchanged passthrough.
- A state read failure never becomes an implicit off state.
- Raw Git failures return the actual Git error without recommending PM activation as recovery.
- Dormant stale state is reported only when relevant after reactivation; it cannot gate PM-off work.
- Deactivation changes no Git state and performs no workspace cleanup.
- PM-on negative controls must prove that the change did not weaken existing authority.

## Verification

Behavioral tests must prove:

- healthy, damaged, missing, and ambiguous workspace assignments all allow unchanged inputs while
  PM is off;
- Claude and Codex write tools, shell tools, relative paths, and absolute paths receive no
  `updatedInput` while off;
- raw `git commit` and `git push`, configuration writes, private workspace commands, staging, and
  review-state operations are not blocked by IronClaude while off;
- session assignments, receipts, workflow state, refs, index, and working bytes remain unchanged by
  the mode-off pass;
- all registered enforcement hooks are inert under exact off state;
- missing, malformed, or unreadable state remains fail-closed;
- PM-on behavior remains unchanged;
- Claude and Codex live response probes accept ordinary prose authority and provider-rendered
  commands without requiring PM activation or confirmation;
- explicit execute instructions do not trigger redundant confirmation;
- Commander remains unable to push through its independent role boundary.

## Scope boundaries

This loop does not add automatic Git actions, a policy framework, workspace reconciliation,
assignment migration, integration-lane changes, or other worktree lifecycle behavior. Complete
operator-free worktree management is the next PM loop.

Execution receives exactly one blind plan review. Findings are repaired in place or through bounded
follow-up tasks; no second blind plan review runs. Reinstalling IronClaude is the final
source/plugin/runtime mutation. Read-only runtime verification and mandatory task-review workflow
evidence may follow; they cannot edit source, rebuild, reinstall, commit, or push.
