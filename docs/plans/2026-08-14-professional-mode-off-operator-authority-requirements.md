# Professional-Mode-Off Operator Authority Requirements

> **Created:** 2026-08-14
> **Status:** Requirements Confirmed

## Goal

Make professional-mode deactivation disable every IronClaude-specific control so explicit operator
instructions execute without protocol refusal or manual recovery work.

## Requirements

### R1 — Exact off-state boundary

1. IronClaude enforcement activates only when trusted session state is `professional_mode='on'`.
2. Exact `off` must pass tool input through unchanged.
3. Missing, malformed, unreadable, or `undecided` state must not be treated as off.

### R2 — No IronClaude enforcement while off

Under exact off state, IronClaude must not enforce:

- workflow stages or skill sequencing;
- task review or testing-theatre gates;
- worktree assignment routing or health;
- staging and allowed-file restrictions;
- commit, commit-and-push, or push intent protocols;
- hooks-config anti-tamper rules;
- private workspace-transport restrictions;
- stop-hook continuation or bypass grading.

### R3 — Direct operator authority

1. Explicit operator prose may authorize ordinary edits, commands, commits, and pushes while PM is
   off.
2. Raw Git is allowed when the operator requests it.
3. IronClaude must not require `/commit`, `/commit-and-push`, `/push`, task review, or PM activation.
4. IronClaude must not ask for immediate reconfirmation after an explicit execute instruction.
5. Questions remain allowed only for missing material scope, destructive disposition, or independent
   platform safety requirements.
6. Provider-rendered skill links must retain the same direct-human authority as their source command.

### R4 — Preserved state

1. Deactivation must change only professional-mode state and its timestamp, preserving assignments,
   receipts, workflow stage and rows, staged bytes, refs, and files.
2. Preserved IronClaude state must exert no enforcement while PM is off.
3. Reactivation must resume existing PM-on validation without silently discarding preserved state.

### R5 — Independent boundaries

1. Platform safety, sandbox, filesystem permissions, credentials, and explicit operator scope remain
   in force.
2. Commander's no-push role capability remains unchanged and independent of PM state.
3. PM-on human-authority and workflow behavior remains unchanged.

### R6 — Cross-client parity

Claude Code and Codex must receive equivalent hook behavior, instruction semantics, deactivation
output, and operator-authority handling.

## Acceptance evidence

Tests must reproduce and prevent the original refusal and verify:

- exact mode-off passthrough with no input rewrite for every supported Claude/Codex write and shell
  surface;
- passthrough with no, healthy, damaged, or ambiguous assignment state;
- raw commit and push acceptance under explicit operator authority;
- no IronClaude block from configuration, transport, review, staging, or stop hooks while off;
- exact state preservation across mode-off operations;
- fail-closed behavior for unknown state;
- unchanged PM-on enforcement;
- no redundant confirmation after explicit execution instructions;
- live Claude/Codex response acceptance for prose and provider-rendered invocations;
- independent Commander no-push enforcement;
- complete hook, state-manager, workspace-manager, Commander, version, and plugin validation suites.

## Out of scope

- Automatic worktree synchronization or reconciliation
- Assignment migration or cleanup
- Integration-lane redesign
- Automatic commit or push behavior
- Configurable enforcement levels
- Generalized authorization or policy frameworks
- Unrelated roadmap items

Exactly one blind plan review is allowed. Review findings are repaired in place without a second
blind review. Reinstalling IronClaude is the final source/plugin/runtime mutation; later mandatory
review-state evidence cannot edit source, rebuild, reinstall, commit, or push.
