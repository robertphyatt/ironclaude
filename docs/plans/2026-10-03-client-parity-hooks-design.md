# Client Parity: Hooks Design (v1.1.16 L2b-1)

> **Created:** 2026-10-03
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-03-client-parity-hooks-requirements.md`
> **Loop:** full. This loop changes guard and hook enforcement code.

## Summary

L2a showed that IronClaude's hooks do run on Codex. The gaps are in the content the hooks handle.

**What is wrong today:**
- The Stop hook grades from Claude's transcript shape, so its grading silently skips on Codex.
- Codex hides `systemMessage` warnings.
- Hooks keyed on `Skill` and `Task` never fire on Codex.
- The `EnterPlanMode` block is dead code on both clients.
- Nothing catches shell-written side-quest files during execution.

This loop fixes all five. The fixes go in shared helpers, so both clients behave the same way.

## Architecture

The approach is shared helpers in `worker/hooks/hook-logger.sh`, the library every hook sources, used by each hook.

The alternative was per-hook patches. It was rejected because it would duplicate client handling across hooks and let them drift apart.

## Components

1. **`ic_last_assistant_text`** (in `hook-logger.sh`).
   - Prefer `.last_assistant_message` from the stdin JSON.
   - Fall back to the existing Claude transcript extraction.
   - Return empty only when both sources are absent.
   - Callers: GBTW grading, `_gbtw_recent_waiting_tool` (waiting-tool detection uses the same text) and `subagent-drift-detector.sh`.
2. **Warning queue.**
   - `ic_queue_warning SESSION MSG` appends to a per-session queue file under `~/.claude/` (named by session id; the exact path is set in the plan).
   - The existing UserPromptSubmit hook drains the queue: it outputs the queued text as `hookSpecificOutput.additionalContext` and then truncates the file.
   - Warning-class notices are queued at their source, in `get-back-to-work-impl.sh`, which both clients run. These are the grade-C grading warnings, the 3-block escape hatch firing, and the "plan active but no plan JSON" anomaly. The Codex wrapper still maps approve to `{}`, which is unchanged. Per-turn status notices, such as deferrals and "Allowed – all checks passed", are not warnings and are not queued.
   - Claude behavior is unchanged: it still emits `systemMessage`, and it also queues.
   - Verification: one headless `codex exec` in a scratch repo checks whether UserPromptSubmit context is visible. If it is not, the Codex warnings are dropped, and the finding is recorded.
3. **Skill/Task hook audit.** The audit note `docs/plans/2026-10-03-skill-task-hook-audit.md` gives each of the 4 hooks:
   - what it enforces;
   - whether the MCP state machine enforces the same thing on both clients, with code evidence;
   - a classification: **real gap** (port it) or **covered by MCP** (propose removing it on both clients).

   There is no Claude-only "keep" outcome, because the operator wants a single version of IronClaude for both clients. If a rule is needed but has no Codex hook trigger, the audit proposes a cross-client equivalent, such as an MCP-side check, for the operator to decide on.

   Real gaps are ported to triggers Codex has: PreToolUse or PostToolUse on the state-manager MCP tool names, or Codex SubagentStart and SubagentStop events. Hooks covered by MCP get a removal proposal for the operator, and stay in place.

   Separately, `apply_patch` is added to the plan-task-context matcher.
4. **EnterPlanMode wiring.** Add `EnterPlanMode` to the guard's PreToolUse matcher in `worker/hooks/hooks.json`, so the existing block at `professional-mode-guard.sh:492-501` runs.
5. **Drift check** (a new hook, `execution-drift-check.sh`).
   - **Baseline.** A PostToolUse hook on the state-manager `claim_task` tool records the dirty-file set from `git status --porcelain` for that session and task.
   - **Check.** It runs from a PreToolUse hook on `submit_task`, and is also called from the Stop hook while the workflow is executing. It computes the current dirty set, minus the baseline, minus the current plan's `allowed_files`. If anything remains, it blocks and lists those files.
   - Gitignored files are ignored, because `git status` does not show them.
   - **Proven on Codex before it ships (D9).** A headless Codex run captures the real hook `tool_name` for a state-manager MCP call.
     - If the name matches the matcher shape, the matchers stand.
     - If it has another shape, the matchers are adapted to the observed one.
     - If Codex fires no Pre/PostToolUse hook for MCP calls, the drift task stops and the gap is reported to the operator as a blocker.

     Either way, it cannot ship as a silent no-op on Codex.
6. **Codex config cleanup.** This runs first, outside the repo. Remove the 3-line probe trust entry from `~/.codex/config.toml`, then confirm that a diff against the pre-probe snapshot shows nothing else changed.

## Data Flow

- **Stop:** the hook input goes to `ic_last_assistant_text`, then to grading. Its verdict is a block reason, or a warning that goes to `systemMessage` (Claude) and to the queue (both clients). The next prompt's UserPromptSubmit hook drains the queue into the context.
- **Drift:** `claim_task` triggers the baseline. `submit_task`, or Stop while executing, triggers the drift check, which blocks or allows.

## Error Handling

- Missing `jq` fields fall back as described in Component 1.
- Queue I/O errors never block a turn; they fail open for warnings only.
- If the drift check cannot read git state or the plan, it fails open with a logged warning. Enforcement already exists through the `allowed_files` write guard.
- The plan states the drift check's fail-closed and fail-open choices explicitly, for the reviewer.

## Testing Strategy

Each change gets a RED→GREEN shell test in `worker/hooks/tests`. The fixtures cover:
- Stop with Codex-shaped input (has `last_assistant_message`, no Claude transcript);
- Stop with Claude-shaped input;
- warning queue and drain;
- the `EnterPlanMode` block;
- drift: a new file outside the allowed files blocks; a pre-existing dirty file, a file in the allowed files, and a gitignored file do not.

The plan locates the existing hook test runner and the Commander suite, and both must still pass. A redeploy and Codex cache refresh happen at the end of the loop.

## Implementation Notes

- No forks of behavior by client. Client detection (`ic_is_codex`) is used only to choose an output shape.
- Hook script names and `hooks.json` entries must match their deployed copies in `~/.claude/ironclaude-hooks` after `make deploy-hooks`.
