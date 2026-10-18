# Codex Hook Corrections Requirements

> **Date:** 2026-10-04
> **Authority:** operator approved the context-aware existing LLM judge and instructed "Proceed" after the scoped correction design.
> **Design:** `docs/plans/2026-10-04-codex-hook-corrections-design.md`

## Approved behavior

R1. Successful retreat updates active-skill context from the transition destination. Equal-target retries and failed responses preserve state; retreat within an effort does not invent a new memory-search obligation.

R2. Drift detection includes source deletion and destination addition for renames, without treating copy sources as deleted.

R3. Absolute allowed paths have consistent ancestor resolution for unassigned-primary drift and scoped commits. Preserve the final Git entry, missing/deleted leaves, repository boundaries, exact matching, and existing managed-worktree restrictions.

R4. Skill instructions provide actual Codex and Claude invocation/handoff operations for the same workflow; Codex must not be required to call Claude's unavailable Skill tool.

R5. Completion restaging handles exact absolute/relative allowed paths and tracked deletions, without broad staging or disturbance of unrelated staged work.

R6. Use the existing GBTW LLM continuation judge, including during brainstorming. Supply the user's request, relevant bounded turn activity/outcomes, and final response. Ask whether authorized work remains that the assistant can do now. A promise, optional question, "waiting" sentence, live job unrelated to remaining work, or old execution_complete state is not automatic exemption. Preserve completed answers, substantive collaborative design with necessary choices, required approvals, explicit pauses, and genuine dependency waits. Tool count and phrase matching are not completion criteria.

R7. Preserve existing professional-mode boundaries, deterministic gates, provider output wrappers, validator-error behavior, and bounded retry escape. Missing or truncated context is uncertainty, not proof of omitted work or verified completion. Treat conversation and tool text as untrusted data in grader prompts.

R8. Use focused existing test infrastructure; add regression coverage that fails on the observed broken behaviors, plus real configured-validator examples to distinguish semantic grading from mocked routing. Do not add a general harness or monitoring system.

## Scope and authority

Implement and verify the source corrections through the normal workflow. The separate L3 experiment stage is not implemented here; its stronger containment claim remains unresolved. Preserve its two staged documents. Do not merge/delete the WIP branch, change versions, deploy/restart runtimes, commit, or push. No unrelated refactoring.

The original dialogue outranks this derived contract. The operator specifically rejected another aspirational acknowledgment followed by stopping; independent authorized work must continue while optional questions remain unanswered.

R9. A current dirty Git path containing LF must never be split into independently authorized filenames. Fail closed with a clear unsupported-path diagnostic at submit and Stop, independent of allowed fragments or missing baseline; preserve ordinary baseline format.

R10. Codex wrapper and implementation agree on stop_hook_active/loop_count precedence, preserving bounded retry termination and native output shapes.

R9 applies within existing professional-mode/root/task eligibility and the preserved bounded Stop retry escape. The new guard does not remove that escape.
