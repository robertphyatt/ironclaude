# QW-CAP — Free-Text Git-Verb Intent Guidance — Requirements

> **Created:** 2026-08-27 · Derived from the QW-CAP brainstorm (Fable-scoped). Design:
> `docs/plans/2026-08-27-cap-freetext-intent-guidance-design.md`. Authority: operator directives →
> brainstorming → this file → plan.

## Functional requirements
- **R1.** The shared MCP tool DESCRIPTION for `commit`/`commit_and_push`/`push`
  (index.ts:122-137) states that human intent exists ONLY when the operator typed the rendered
  `/<verb>` form as their literal prompt this turn; on a free-text request the agent must reply with
  the `/<verb>` form for the operator to type rather than calling the tool; and it must carry any
  remaining instruction from the prose (the compound-prompt tail) forward after the verb completes.
- **R2.** Both missing-intent refusal sites (git-authority.ts:556 and :612) APPEND a remedy to the
  existing `'Direct Git operation requires a matching human intent'` message naming the fix (invoke the
  rendered `/commit` / `/commit-and-push` / `/push` form; free-text prose does not mint intent). The
  existing `matching human intent` text is PRESERVED as a prefix.

## Hard constraints
- **HC1 — mint gate byte-unchanged.** `state-activator.sh` (the intent-mint hook) is NOT edited — it
  is not in `allowed_files`, so the file guard structurally enforces this. Free-text is NEVER promoted
  to a human intent (security: an agent could infer/hallucinate a git verb from prose and manufacture
  push authority — [[feedback_never_push_without_explicit_go]]).
- **HC2 — refusal APPEND, not rewrite.** The `matching human intent` substring stays; the 18 existing
  `.toThrow` substring assertions (git-authority.test.ts ×15, workspace-service.test.ts ×3) stay green.
  The plan verifies none use an anchored regex or exact `toBe`.
- **HC3 — no confirmation-click / no activate-PM banner.** Out of scope (click-auth is M7c's C4a; the
  banner is cut per Fable — a load-once surface invisible at the decision point).
- **HC4 — doc filenames avoid "push"/"commit"** (execution_complete bash guard matches them as path
  substrings).

## Acceptance
- The published `commit_and_push` (and `commit`, `push`) tool description CONTAINS the new guidance
  substrings (tool-dispatch.test.ts via `publicToolDefinitions`). The missing-intent error message
  CONTAINS both the preserved `matching human intent` prefix AND the new remedy substring
  (git-authority.test.ts). The whole workspace-manager suite stays green (the 18 existing substring
  assertions survive). `state-activator.sh` is unchanged (not in the change set).
