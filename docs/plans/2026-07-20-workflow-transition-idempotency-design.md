# Workflow Transition Idempotency Design

> **Created:** 2026-07-20
> **Status:** Design Complete
> **Release target:** IronClaude v1.1.0, Roadmap Wave 1
> **Scope mode:** selective
> **Requirements:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md` (MP-W08; preserve MP-W11)

## Summary

Make every explicit workflow-stage transition disciplined at both boundaries. A caller reads the current provider-native session state before calling a transition and skips the call when the target already matches. If stale or concurrent code still sends the request, the state manager returns a successful, side-effect-free response with `changed: false`, `from`, and `to`.

Valid different-state transitions continue through the existing prerequisite checks and mutate exactly once. Invalid different-state transitions remain errors. The completed provider-native session identity work remains the binding invariant for both preflight and mutation.

## Confirmed Root Cause

Current source has four mutually reinforcing defects:

1. `validateWorkflowTransition()` treats same-state requests as valid no-ops, but individual transition handlers bypass that result or use `canTransitionTo()`, which rejects the same state.
2. Other handlers update session timestamps, flags, and audit history even if no stage change is necessary.
3. Transition-calling skills direct unconditional MCP calls without first reading the current state.
4. `skill-state-bridge.sh` reads the current stage for validation but still performs its SQL update and activation audit when target equals current; `mcp-state-logger.sh` logs every matching tool completion without checking `changed`.

No focused workflow same-target tests exist. Episodic evidence confirms the operator observed `brainstorming -> brainstorming` requests and required caller preflight plus a server-side `changed: false` defense. The earlier cross-task collision had a separate native-session identity root cause that is already repaired; this wave protects that fix rather than reopening it.

## Scope

This PM loop covers explicit workflow-stage transitions and their direct callers:

- shared state-manager transition execution and result shape;
- `mark_design_ready`, `mark_plan_ready`, `mark_brainstorming`, `mark_debugging`, `start_execution`, `mark_executing`, and `retreat`;
- workflow-stage calls in `brainstorming`, `writing-plans`, and `executing-plans` skills;
- the workflow-stage path in `skill-state-bridge.sh`;
- same-target suppression in `mcp-state-logger.sh`;
- Codex cachebuster-aware release-version consistency so the required local-install suffix cannot make the final source tree fail its own full suite;
- tool metadata, focused regression tests, generated state-manager distribution, plugin validation, local reinstall, and source/cache parity evidence.

### Compound-operation boundary

Operations whose primary purpose is domain mutation are not converted into total no-ops merely because their resulting workflow stage already matches:

- `create_plan` must still reload a coherently revised plan while already in `final_plan_prep`;
- `reset_session` must still clear plan and review state while already `idle`;
- design consumption and wave progression retain their artifact/task behavior.

When a compound operation does not change the workflow stage, it omits a redundant stage assignment and transition-specific logging but still performs its authorized domain mutation and domain audit. This preserves the anti-flailing revised-plan reload contract.

Professional-mode state changes, provider routing, failover, Slack, deactivation syntax, episodic-memory capture, Git guard changes, and `create_plan` call-shape guidance remain in their separately approved roadmap waves.

### Complete v1.1.0 ledger disposition

The complete active requirements ledger remains release-blocking. This Wave 1 loop implements only MP-W08 and protects completed MP-W11; sequencing does not defer, weaken, or complete any later requirement. The named durable dispositions are:

| Requirement set | Named PM loop | Current release-blocking status |
|---|---|---|
| MP-W01–MP-W07, MP-W09, MP-W10 | Roadmap Wave 0 — Review convergence prerequisite | Implemented and hot-deployed; must remain green through final release verification. |
| MP-W08 | Roadmap Wave 1 — Workflow transition idempotency | Current loop; not complete until all four tasks and restart verification pass. |
| MP-W11 | Native session-identity prerequisite retained in v1.1.0 working tree | Implemented prerequisite; identity/dispatch/diagnostic regressions block this loop and release. |
| MP-001, MP-002, MP-011, MP-012, MP-D04, MP-C01 | Roadmap Wave 2 — Provider foundations | Pending; release-blocking. |
| MP-003, MP-006, MP-D01, MP-D05, MP-D10 | Roadmap Wave 3 — Native role routing | Pending; release-blocking. |
| MP-004, MP-007, MP-009, MP-010, MP-C02, MP-C03 | Roadmap Wave 4 — Automatic failover and conversation continuity | Pending; release-blocking. |
| MP-005, MP-008, MP-D02, MP-D03, MP-D06, MP-D07, MP-C04 | Roadmap Wave 5 — Slack controls and signaling | Pending; release-blocking. |
| MP-D08, MP-D09, MP-C05 | Roadmap Wave 6 — Shadow mode and remote resilience | Pending; release-blocking. |
| MP-C06 | Roadmap Wave 10 — Read-only Git guard hardening | Pending; release-blocking. |
| MP-013, MP-014, MP-C07 | Roadmap Wave 12 — Release integration and documentation | Pending; release-blocking. |
| Every active requirement above | Roadmap Wave 13 — Release verification and history | Final evidence audit; release and squash remain blocked until every mapped loop passes. |

Roadmap Waves 7–9 and 11 retain the operator-queued Codex compatibility defects described in the durable roadmap. They are additional release-blocking corrective loops, not substitutes for any mapped MP requirement.

## Approaches Considered

### Shared transition executor plus caller preflight — selected

A shared state-manager path owns same-target detection, transition validation, mutation, and transition audit. Callers independently preflight and skip redundant calls.

**Pros:** directly implements MP-W08; removes handler divergence; supports atomic mutation; makes exhaustive tests possible; preserves defense in depth.
**Cons:** requires careful classification of pure transitions and compound operations.
**Guidance alignment:** exact match for the approved master design and operator guidance.

### Handler-local guards — rejected

Adding an equality branch to each handler is locally small but preserves duplicated rules and future drift. It contradicts the approved single transition path.

### Database-level identical-update suppression — rejected

Suppressing equal SQL values cannot prevent artifact registration, flag clearing, audit insertion, or hook output. It hides one symptom without satisfying the side-effect-free contract.

## Architecture

### Shared transition execution

The state-machine layer exposes one transition operation with an explicit result:

- changed transition: `{ success: true, changed: true, from, to, session_id }`;
- same-target defense: `{ success: true, changed: false, from, to, session_id }`;
- invalid different-state request: existing structured error semantics.

The operation resolves no identity itself. The existing dispatcher supplies the already validated provider-native root session ID. Inside one database transaction it re-reads that exact row, compares current and target stages, validates prerequisites through `validateWorkflowTransition()`, performs an optional transition-specific mutation, writes the stage, and inserts the transition audit.

Same-target detection occurs before prerequisites or callbacks. Therefore a no-op cannot update `updated_at`, register or consume a design, clear flags, alter waves, insert audit history, or emit a state-change record.

### Compound operations

Compound handlers separate domain mutation from workflow transition. They continue performing required plan, design, task, or reset work. A workflow-stage field is included only when the stage actually changes, and transition-specific audit output is not fabricated when it does not.

`create_plan` remains callable from `final_plan_prep` so a revised plan replaces `session.plan_json` and rebuilds Wave 1. Its existing domain audit records plan installation; it does not falsely claim a workflow transition when `from === to`.

### Caller preflight

Before any explicit transition call, the owning skill calls `get_resume_state`, verifies the returned native session ID and current stage, then:

- continues without a transition call when current equals target;
- calls the transition once when target differs;
- stops on missing, ambiguous, or mismatched identity;
- reports a different-state transition error rather than retrying blindly.

Caller preflight is mandatory even though the server is defensive. Server idempotency protects stale or concurrent callers; it is not permission to omit the read.

Current source has no skill that directly calls the `mark_debugging` MCP tool. `systematic-debugging` enters the `debugging` stage through the identity-bound `skill-state-bridge.sh` mapping. Therefore the caller contract covers `mark_debugging` defensively at the server and covers the real systematic-debugging caller at the hook bridge; it must not invent a new skill-side MCP transition solely to satisfy a name-based checklist.

### Hook behavior

`skill-state-bridge.sh` keeps its current identity-bound stage read. When current equals the mapped target, it exits the workflow-transition path without writing session fields or a transition/activation audit. Different-state validation and mutation retain current fail-closed behavior.

`mcp-state-logger.sh` examines the completed tool response. A response with `changed: false` exits without reading the session or writing a hook log. Changed transitions retain the existing status log. Missing or malformed response metadata remains best-effort and cannot mutate workflow state.

### Tool metadata

Genuinely idempotent transition tools advertise `idempotentHint: true`. Compound operations do not gain this annotation solely because their stage assignment can be omitted.

## Components

### State manager

- `worker/mcp-servers/state-manager/src/state-machine.ts`
  - Add shared transition execution/result contract.
  - Reuse the existing transition table and prerequisite validator.
  - Preserve native-session row binding and transactional audit behavior.
- `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
  - Route explicit transition handlers through the shared path.
  - Return `changed`, `from`, and `to` consistently.
  - Preserve review gates, recovery invariants, plan reload, reset, and other domain behavior.
- `worker/mcp-servers/state-manager/dist/index.js`
  - Rebuild from verified TypeScript source.

### Workflow callers

- `worker/skills/brainstorming/SKILL.md`
  - Preflight before returning from debugging, marking design ready, or any other explicit transition.
- `worker/skills/writing-plans/SKILL.md`
  - Preflight before `mark_plan_ready`.
- `worker/skills/executing-plans/SKILL.md`
  - Preflight before execution start, return from review, and retreat.
  - Do not skip `create_plan` based only on stage equality.

### Hooks

- `worker/hooks/skill-state-bridge.sh`
  - Skip same-target workflow mutation and audit after its existing stage read.
- `worker/hooks/mcp-state-logger.sh`
  - Suppress post-tool transition logging for `changed: false`.

### Focused tests

- State-manager tests cover the shared helper and every transition handler.
- Commander contract tests prove each direct transition-calling skill performs identity-bound preflight, preserve the exact current caller inventory, and do not apply the rule to compound operations.
- Hook tests execute both scripts against isolated temporary state and structured hook payloads, including `systematic-debugging` as the real caller path into `debugging`.
- Commander version-consistency tests compare the Codex manifest's release prefix to other release declarations, accept only the supported `+codex.<token>` suffix shape, reject arbitrary build metadata, and rerun after the final cachebuster is written.
- Existing MP-W11 identity and dispatch tests remain mandatory regression evidence.

## Data Flow

### Normal changed transition

1. Skill reads `get_resume_state` for the current native task.
2. Current and target differ.
3. Skill calls the explicit transition tool once.
4. Dispatcher revalidates provider-native identity and passes the resolved root session ID.
5. Shared transition operation re-reads that row inside its transaction.
6. State machine validates the different-state transition and prerequisites.
7. Transition-specific callback, stage update, and transition audit commit together.
8. Tool returns `changed: true`; post-tool logger records the changed stage.

### Caller-side skip

1. Skill reads current state.
2. Current equals target.
3. Skill continues without invoking the transition tool.
4. No MCP or hook transition side effects occur.

### Defensive server no-op

1. A stale caller reads an older different stage or skips preflight incorrectly.
2. Another valid actor reaches target before the request executes.
3. Shared transition operation re-reads the current row and sees equality.
4. It returns `changed: false` before validation callbacks or mutation.
5. Post-tool logger observes `changed: false` and emits nothing.

## Error Handling

- Missing, ambiguous, mismatched, or cross-client identity fails before row access and cannot fall back by recency or process-global state.
- Unknown sessions return the existing session-not-found error.
- Invalid different-state transitions remain errors with current and requested stages.
- Failed prerequisites do not execute callbacks, mutate state, or insert transition audits.
- Database failure rolls back the changed transition and its audit together.
- Hook response-parsing failure remains best-effort logging behavior and never changes workflow state.
- A caller does not retry a transition error without a fresh state read.

## Testing Strategy

### State-manager unit and integration tests

For each explicit transition tool:

1. Seed a session already at the target stage plus representative timestamps, flags, plan/design/task rows, and audit history.
2. Snapshot the session and related tables.
3. Call the tool directly.
4. Assert `success: true`, `changed: false`, exact `from`/`to`, and native session ID.
5. Assert byte/row equality of every snapshot and unchanged audit count.

Additional cases prove:

- valid different-state calls return `changed: true` and one transition audit;
- invalid different-state calls remain errors without mutation;
- design-ready no-op does not register or consume the supplied file;
- retreat no-op does not clear artifacts or flags;
- execution no-op does not rerun review gates or clear review state;
- revised `create_plan` still replaces plan JSON and Wave 1 while stage remains `final_plan_prep`;
- `reset_session` still clears domain state while stage remains `idle`;
- tool annotations distinguish pure transitions from compound operations.

### Skill contract tests

Assert each transition call is preceded by `get_resume_state`, current/target comparison, identity validation, and same-target skip language. Assert `create_plan` remains explicitly exempt from stage-only skipping.

### Hook tests

- Same-target `skill-state-bridge.sh` invocation leaves the complete session row and audit table unchanged.
- Different-target invocation changes stage once and records one expected event.
- `mcp-state-logger.sh` produces no log for a structured `changed:false` response and logs one changed transition normally.

### Regression and deployment gates

- Run focused TypeScript and Commander tests red before implementation and green after.
- Run the full state-manager and Commander suites.
- Rebuild `dist/index.js` and validate the plugin.
- Preserve and rerun session-identity, tool-dispatch, and diagnostics tests.
- Cachebust and reinstall through the supported local marketplace flow.
- Run the complete Commander suite against the cachebusted source contract, then rerun the focused version-consistency test after the final cachebuster mutation.
- Compare SHA-256 for every changed plugin file between source and installed cache.
- Require a full Codex restart before claiming the new runtime behavior active.

## Acceptance Criteria

1. Every transition-calling skill reads the current identity-bound state and skips a same-target call.
2. Every explicit same-target transition returns `success: true`, `changed: false`, `from`, `to`, and the bound session ID.
3. A same-target request changes no session field or timestamp, artifact, flag, wave, hook log, or audit row.
4. Valid different-state transitions mutate and audit exactly once.
5. Invalid different-state transitions remain errors without mutation.
6. Compound operations retain their authorized domain behavior and avoid fabricated stage-transition effects.
7. Claude and Codex root-session identity invariants remain green under interleaved-task tests.
8. Focused and full regression suites pass; generated distribution and installed cache match source.
9. Final `worker/.codex-plugin/plugin.json` may carry exactly one supported `+codex.<token>` cachebuster while its release prefix remains equal to Commander, Claude plugin, marketplace, and any Makefile pin; unsupported metadata fails the consistency test.

## YAGNI Boundary

This wave adds no schema, new workflow stages, general event bus, provider routing abstraction, transition history subsystem, or review-lifecycle MCP. The shared helper exists only to make current workflow transitions consistent, atomic, identity-bound, and idempotent.
