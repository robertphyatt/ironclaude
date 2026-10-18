# Skill/Task Hook Audit (v1.1.16 L2b-1, R3)

> Created: 2026-10-03. Requirements: `docs/plans/2026-10-03-client-parity-hooks-requirements.md` (D3).
> Outcomes are those allowed by D3: **real gap** (port the rule to a trigger Codex has), or
> **covered by MCP** (propose removal on both clients). A rule that is needed but has no
> verified Codex trigger gets a cross-client proposal. Nothing is removed in this loop; the
> operator decides each proposal.

## Facts that apply to every hook

- These four hooks are keyed on Claude tool names (`Skill`, `Task`, `TaskOutput`; `hooks.json`
  PreToolUse/PostToolUse). Codex has no `Skill` or `Task` tool, so none of them fires on Codex.
- Before this loop, the Codex hook `tool_name` for MCP tools had never been captured
  (`docs/plans/2026-07-29-codex-hook-matcher-forms.md:401`). The client-parity plan's Task 3
  captures it and records the result in
  `docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md` (`MCP classification:` line).
  Matchers on state-manager tool names follow that recorded shape.
- Codex `SubagentStart` exists only as an output wire found in the Codex binary
  (`docs/plans/2026-07-21-codex-stop-hook-diagnosis-findings.md:44`). It is not a confirmed
  registrable input event.

## 1. skill-state-bridge.sh (PreToolUse `Skill`)

| What it sets | MCP coverage | Outcome |
|---|---|---|
| `inherit_review` from the FROM-stage (`skill-state-bridge.sh:78-86`) | `inheritReviewOnDesignReentry` (`state-machine.ts:146`), used by `mark_brainstorming`/`mark_debugging` (`write-tools.ts:1167`, `:1180`) | covered by MCP |
| `testing_theatre_checked=1` (`:65`) | `set_testing_theatre_checked` (`write-tools.ts:1388`) | covered by MCP |
| `workflow_stage` brainstorming/debugging (`:41`, `:45`) | `mark_brainstorming`, `mark_debugging` | covered by MCP |
| `active_skill` (`:41-68`) | none: no MCP read or write (Step 1 grep) | **real gap** |
| `memory_search_required=1` for brainstorming and writing-plans (`:41`, `:49`) | none | **real gap** |
| `testing_theatre_checked=0` on code-review (`:57`) | none | **real gap** |
| stages design_marked_for_use, plan_marked_for_use, reviewing, plan_interrupted, and the transition validation block (`:97-147`) | no MCP tool sets these four stages | **real gap** |

Effect on Codex: `active_skill` is never set. GBTW's skill-aware firing matrix
(`get-back-to-work-impl.sh:849`) therefore runs its "no skill" row, and the memory-search
gate (`:1040`) never fires. Once GBTW grades Codex turns (this loop, R1), the no-skill row
means false continuation and bypass blocks while brainstorming, and rigor, prediction and
evidence checks during execution.

Ported in this loop (plan Task 7): `active_skill` only. The existing mcp-state-logger.sh
PostToolUse entry already matches the transitions and parses changed:true:
- `mark_brainstorming` sets `brainstorming`;
- `mark_debugging` sets `systematic-debugging`;
- `mark_executing` and `start_execution` set `executing-plans`;
- `mark_design_ready` and `mark_plan_ready` set NULL.
Only the brainstorming and executing-plans rows of the firing matrix differ from the no-skill
row. Setting `active_skill` alone does not arm the memory gate: `memory_search_required`
stays 0. On Claude, the port also moves `active_skill` from `code-review` to
`executing-plans` after an A/B review's `mark_executing`.

Not ported in this loop:
- `memory_search_required=1`: real gap, ported in L2b-2 (operator decision D8). See section 2.
- The writing-plans flag and the four hook-only stages have no MCP call at skill start.
  Cross-client proposal: move these writes into MCP transition tools.

## 2. memory-search-tracker.sh (PostToolUse `Task`)

- Clears `memory_search_required` when an `Agent` call names `search-conversations`
  (`memory-search-tracker.sh:15`, `:22`, `:30`). No MCP clear exists.
- **Real gap. Outcome: port in L2b-2 (operator decision D8).** L2b-2 ports both halves on
  both clients: the setter in section 1 and this clearer. It also carries the instruction-parity
  fixes the port depends on. GBTW's block text tells the agent to call the Claude `Agent` tool,
  which Codex does not have.
- Candidate trigger for L2b-2: PostToolUse on the episodic-memory `search` MCP tool, using the
  matcher form `mcp__(plugin_ironclaude_)?episodic[-_]memory__.*` (`hooks.json` PreToolUse
  entry 1).

## 3. subagent-circuit-breaker.sh (PreToolUse `Task`, PostToolUse `Task` and `TaskOutput`)

- Sets `circuit_breaker=1` on a subagent context-limit failure (`:32`) and blocks further
  `Agent` dispatch while it is set (`:45`, `:55`). Nothing else sets it; MCP only clears it
  (`write-tools.ts:1280`, `state-machine.ts:573`).
- **Real gap**, with no verified Codex trigger: Codex subagent tool names and SubagentStart are
  unobserved. Cross-client proposal: the operator decides between an MCP-side breaker and
  removal.
- UNCERTAIN on Claude, not proven defects (there is no positive control for either):
  - The matcher is `Task`, but the script acts only on `tool_name == "Agent"` (`:55`). The
    Brain template uses `Agent|Task` (`commander/tests/test_daemon.py:2366`).
  - The PostToolUse branch keys on `has("tool_output")` (`:23`). Repo fixtures for Claude
    PostToolUse use `tool_response` (`mcp-state-logger.sh:52`).

## 4. task-completion-validator.sh (PostToolUse `Skill`)

- An advisory LLM check that a task-boundary code review matches the in-progress task. A
  mismatch prints a `systemMessage` and exits 0. Nothing reads its result: it writes no
  DB state (`task-completion-validator.sh:110-117`, `:125`).
- MCP `record_review_verdict` (`write-tools.ts:1037`) records grades without a task-match
  check, so this is not covered by MCP. The rule is advisory, though, and nothing depends on it.
- **Proposal: remove on both clients.** Operator decision.

## What ships and what waits

- **Ships:** the `active_skill` port (section 1), on the state-manager matcher shape that
  `docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md` records for Codex.
- **Port in L2b-2 (operator decision D8):** the memory-search pair (sections 1 and 2).
- **Waits:** the circuit breaker and the hook-only stages, which have no Codex trigger.
- Each proposal above waits for the operator's decision.

## Also changed in this task

`apply_patch` (Codex's native edit tool, `professional-mode-guard.sh:112-115`) is added to the
plan-task-context PreToolUse matcher, so review_pending enforcement covers Codex edits.
