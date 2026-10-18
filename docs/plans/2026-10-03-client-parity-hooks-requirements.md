# Client Parity: Hooks (v1.1.16 L2b-1) Requirements

> **Created:** 2026-10-03
> **Status:** Operator-approved in brainstorming
> **Roadmap:** `docs/plans/2026-10-02-v1-1-16-roadmap.md`
> **Evidence:** `docs/plans/2026-10-02-codex-hook-probe-findings.md` (L2a)

## Operator decisions

- **D1. Two loops.**
  - L2b-1 is a full loop for hook code. It covers items A, B, C, E and F, including the side-quest detector.
  - L2b-2 is a light loop for instruction parity.
- **D2. Codex warnings use next-prompt context.**
  - Non-blocking warnings go into a per-session queue.
  - The UserPromptSubmit hook injects them as context on the next turn. This works the same on both clients, and Claude also keeps `systemMessage`.
  - L2b-1 verifies that Codex shows UserPromptSubmit context. If it does not, Codex warnings are dropped, as they are today, and that result is recorded.
- **D3. Skill/Task-keyed hooks are audited, and only real gaps are ported.** The hooks are skill-state-bridge, task-completion-validator, the subagent circuit breaker and the memory-search tracker. Each audit outcome is one of three:
  - **real gap:** port the check to a trigger that Codex has, such as MCP tool calls or Codex subagent and collaboration events;
  - **covered by MCP:** propose removing the hook on Claude as well, with the evidence. Nothing is removed without the operator's decision on each hook. This follows the operator: "If there are things that we realize aren't needed to get the functionality in codex, let's talk about if we need them at all in Claude";
  - There is no "keep Claude-only" outcome. Operator: "I want a single version of ironclaude for both." If a hook's rule is needed but has no Codex trigger, the audit proposes a cross-client equivalent, such as an MCP-side check. Otherwise it proposes removing the hook on both clients. The operator decides.
- **D4. Wire the EnterPlanMode block.** Add `EnterPlanMode` to the guard's PreToolUse matcher so the existing block (`professional-mode-guard.sh:492-501`) runs on Claude.
- **D5. Side-quest detector: execution drift check.**
  - When a task is claimed, record a baseline of the dirty files.
  - At `submit_task`, and at Stop while executing, block on new or changed files outside the current plan's `allowed_files` that were not in the baseline, and list them.
  - The check is deterministic and works the same on both clients.
- **D6. Remove the probe's leftover lines from `~/.codex/config.toml`.** This is the first step: remove the 3-line `[projects."…/scratchpad/codex-probe"] trust_level = "trusted"` entry. Verify against the pre-probe snapshot that nothing else changes.
- **D7. One IronClaude.** Changes go in shared hooks and helpers, with no per-client forks of behavior. Client detection is used only where the platform's input or output shapes differ.

- **D8. The memory-search requirement is ported in L2b-2.**
  - The operator chose "Port it in L2b-2" for the memory-search requirement: a flag is set when a design or plan skill starts, and cleared when the episodic search runs.
  - L2b-1 records it as a real gap.
  - L2b-2 ports both halves, setting and clearing, on both clients, together with the instruction-parity fixes it depends on. L2b-1 does not port it.
- **D9. The drift check on Codex must be proven.**
  - L2b-1 captures the real Codex hook `tool_name` for a state-manager MCP call before shipping the drift check.
  - If Codex fires no Pre/PostToolUse hook for MCP calls, the drift task stops and the gap is reported to the operator as a blocker. A drift check that silently does nothing on Codex is not shipped.

## Dropped on evidence

The "SessionStart versioned cache path" item is dropped. `hooks.json:10` uses `${CLAUDE_PLUGIN_ROOT}`, which Codex expands per installed version.

## Requirements

- **R1 (A).**
  - Add a shared helper that returns the last assistant text.
  - It reads `.last_assistant_message` from the hook input first; L2a observed this field on Codex, and it is documented for Claude. It falls back to transcript parsing.
  - Use it in GBTW grading (`get-back-to-work-impl.sh` around lines 561–606), in the waiting-tool exemption (`_gbtw_recent_waiting_tool`, around lines 113–119) and in `subagent-drift-detector.sh`.
- **R2 (B).**
  - Add a warning queue keyed by session, plus a drain in a UserPromptSubmit hook.
  - The Codex Stop wrapper (`get-back-to-work-claude.sh:61-79`) queues warnings instead of dropping them.
  - Run a headless Codex check of whether the context is visible.
- **R3 (C).**
  - Produce an audit note that classifies each of the 4 hooks, with evidence.
  - Port the real gaps.
  - Add `apply_patch` to the plan-task-context matcher.
  - For hooks covered by MCP, write removal proposals for the operator. Do not delete any of them in this loop.
- **R4 (E).** Add `EnterPlanMode` to the guard matcher, with a test proving it blocks.
- **R5 (F).** Add the drift-check hook with a claim-time baseline and the blocking behavior from D5. Tests must show:
  - a new untracked file outside `allowed_files` blocks;
  - a file already dirty before the claim does not block;
  - a file inside `allowed_files` does not block;
  - gitignored files are ignored.
- **R6 (0).** Clean up `config.toml` as in D6.
- **R7.**
  - Every changed hook gets a RED→GREEN test in `worker/hooks/tests`, with Codex-shaped and Claude-shaped fixtures where the input differs.
  - The existing hook tests and the Commander suite still pass.
  - Hooks are redeployed with `make deploy-hooks` and the Codex cache is refreshed. The redeploy happens at the end of the loop only.

## Out of scope

- instruction parity and the operator's personal Codex files, which are L2b-2;
- the plan-time side-quest check;
- L3–L7.
