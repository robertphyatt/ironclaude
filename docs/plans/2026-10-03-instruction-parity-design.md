# Instruction Parity Design (v1.1.16 L2b-2)

> **Created:** 2026-10-03. Revised after a retreat from planning.
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-03-instruction-parity-requirements.md`
> **Loop:** full. This loop changes hooks, `hooks.json`, two skills, and the operator's global instruction and approval files.

## Summary

This loop does four things:

- It brings the repository's three instruction surfaces to the same 15 directives and adds a test that keeps them aligned.
- It aligns the operator's two global instruction files and prunes Codex's standing approvals.
- It gives Codex a real entry into brainstorming, and makes the brainstorming memory-search gate behave the same on both clients.
- It replaces the writing-plans gate with an instruction to search before drafting, because the gate's measured value was low.

The worker templates and the activation skill move to L2b-3.

## Approach

**Instruction surfaces.** Convergence is done by hand, guarded by a parity test.

**Gates.** Gates are armed by MCP transitions that both clients make. Gates were tied to Claude's Skill and Agent tools; that tie is removed.

**Rejected alternatives:**
- generating the instruction surfaces from one source;
- text fixes with no test;
- removing the memory gates;
- a new state-manager transition for writing-plans;
- a guard on plan-file writes.

## Components

### 1. Repository surfaces

The canonical directive list, by number and heading:

1. Challenge Assumptions
2. Verify with Evidence
3. Refuse Impossible Requests
4. Persistent Questioning
5. No Premature Optimization
6. Search Before Guessing
7. Subagent Discipline
8. No Sycophantic Responses
9. Right-Size Every Subagent
10. Advisor Fallback
11. No Workflow Avoidance Under Stage/Context Restrictions
12. Boy Scout Rule
13. Recipient-Based Communication Profiles
14. Professional-Mode-Off Operator Authority
15. Managed Worktree — Missing Shared Data Is a Blocker

Changes per surface:

- **`AGENTS.md`:**
  - Add directives 14 and 15.
  - Line 35 gets the Codex search wording.
  - Rewrite line 72 so it is accurate for Codex.
  - Keep "Actual Claude Fable from Codex".
- **`CLAUDE.md`:** add directives 10, 11, 14 and 15.
- **`behavioral.md`:** line 59 adds `astra` to the client-aware ladder.
- **Parity test:** a new file under `commander/tests/` checks the three surfaces.

### 2. Global files

- Back up both files, then apply the directive 7 change to both.
- Apply the Codex search line to `~/.codex/AGENTS.md` only.

### 3. Codex rules

Back up `~/.codex/rules/default.rules`, then reduce it to its original lines 1, 2, 4, 5 and 6.

### 4. Brainstorming entry and gate

- **Entry (skill text).** In `worker/skills/brainstorming/SKILL.md` Phase 1, after the announcement and before Step 0.5:
  - run the Mandatory Direct Transition Preflight;
  - if the result is a different target, call `mark_brainstorming` once.
- **Arming.** Both arm paths arm only when the from-stage is neither `debugging` nor `brainstorming`.
  - `mcp-state-logger.sh` reads the from-stage from the tool response (`from`), which the tool already returns.
  - `skill-state-bridge.sh` reads it from `CURRENT_STAGE`.
- **Clearing.** `memory-search-tracker.sh` clears the flag when either of these happens:
  - an episodic-memory MCP search call, matched by the names measured in R7;
  - an Agent call to search-conversations, as today.
- **Routing.** A `hooks.json` PostToolUse entry routes the measured search tool to the tracker.
- **Message.** `_IC_MSG_MEMORY_SEARCH` names both clients' search paths.

### 5. Writing-plans

- **Arm removed.** `skill-state-bridge.sh:49` loses `memory_search_required=1`.
- **Gate narrowed.** GBTW's gate regex is reduced to `^brainstorming$`.
- **Step 0.5 rewritten.** The writing-plans Step 0.5 text now says:
  - search before drafting the plan;
  - query implementation specifics, such as the files to touch, known gotchas, and prior build decisions;
  - do not repeat the brainstorm query.

### 6. Measurement

- **Codex probe.** A headless `codex exec` run in a scratch repo. A capture hook records the search call's PostToolUse `tool_name`; a positive control confirms the call happened.
- **Claude Task/Agent check.** A decisive check, with its own positive control, of whether the `"Task"` matcher receives Agent calls.

Findings are written as machine-checkable lines, and the hook task consumes them.

## Error Handling

- The fail-open rules are unchanged.
- The arm write is best-effort and logs on failure.
- If a measurement contradicts an assumption, the executor stops and reports.

## Testing Strategy

**Parity test.** RED against today's files, GREEN after convergence.

**Hook tests.** Each has a RED→GREEN pair:
- the gate arms on a new effort, under both MCP tool-name spellings and on the Claude Skill path;
- it does not arm when the from-stage is `debugging` or `brainstorming`, or when the change reports `changed:false`;
- the measured search tool clears the flag;
- the Agent call clears the flag;
- writing-plans does not arm;
- GBTW does not block for writing-plans;
- the block message names both clients' search paths.

**Existing tests.** Tests that assert the writing-plans gate or the old message are updated.

**Global and rules files.** Checked with `diff`.

**Release checks.** All suites pass, the change is redeployed, and the final Fable end review comes back CLEAN.
