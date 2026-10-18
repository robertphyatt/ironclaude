# Parity Output and Path Fixes (v1.1.16 L2b-1c) Requirements

> **Created:** 2026-10-03
> **Status:** Operator-approved.
> - After the L2b-1b Fable end review, the operator chose "Small fix loop first".
> - The operator asked a Fable subagent to choose the output approach, and adopted D with no probe.
> - The operator chose to include the MCP level fix.
> **Parent:** `docs/plans/2026-10-03-drift-check-review-fixes-requirements.md` (L2b-1b)

## Operator directives

- "Have a fable sub agent determine which approach is the best to simultaneously have full Claude/codex parity without bloat or vendor specific logic. Remember, the purpose of iron Claude is to help Claude/codex appropriately apply/enforce my engineering workflow to improve the work done."
- There is one IronClaude for both clients: no client-specific behavior forks.

## Findings

- **M1 (Minor, from the L2b-1b end review): non-canonical absolute paths are dropped.**
  - The write guard (`professional-mode-guard.sh:780`) accepts an absolute `allowed_files` entry by exact raw string.
  - `_drift_allowed_files` (`execution-drift-check.sh:140-147`) keeps an absolute entry only when it begins with git's physical toplevel. An entry spelled through a symlink or a different APFS case is dropped.
  - Result: false blocks at `submit_task` and at Stop for writes the guard allowed.
- **Combined output was never tested on Codex (observation from the L2b-1b end review).**
  - `state-activator.sh` emits one `{systemMessage, hookSpecificOutput}` object.
  - Only a lone `hookSpecificOutput.additionalContext` has been observed reaching Codex (`2026-10-03-client-parity-hooks-codex-ups-findings.md`).
  - Fable analysis: state-activator's 5 always-on `systemMessage` texts are enforcement failures the model must act on: `log_error` at `:146`, `:148` and `:159`, and `log_warning` at `:202` and `:212`. On Claude a `systemMessage` reaches only the operator.
- **MCP error level mismatch (found by the Fable analysis, confirmed by reading the source).**
  - `surface_mcp_errors` calls `log_hook "MCP-ERROR" "Error"` (`hook-logger.sh:614`).
  - The gate at `hook-logger.sh:236` passes only `Blocked`, `BLOCKED` and `ERROR`.
  - Result: MCP sideband errors are emitted only when verbose logging is on.

## Requirements

- **R1 (M1): identify the root by file identity.**
  - An absolute entry not under `"$root"/` maps to repo-relative by walking from its longest existing ancestor up to `/`.
  - At each directory, test `[ "$dir" -ef "$root" ]`. On a match, the entry's remainder below that directory is the repo-relative path. With no match, the entry is dropped.
  - Relative entries and entries under `"$root"/` are unchanged.
  - No client or OS branch.
- **R2 (D): state-activator emits context only.**
  - Its stdout is at most one object: `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":TEXT}}`.
  - TEXT is every buffered text, in buffer order, joined by newlines: a `systemMessage`, a drained `additionalContext`, or a non-JSON line.
  - It never emits `systemMessage`. It prints nothing when there is nothing to say. The exit code is preserved.
- **R3: fix the MCP error level.**
  - `surface_mcp_errors` logs at level `ERROR`, so queued MCP errors are emitted with verbose logging off.
  - First, `git log -S` confirms the lowercase level was not deliberate.
- **R4: tests.** Each of R1–R3 gets a RED→GREEN regression test:
  - R1: a symlinked-parent spelling, a not-yet-existing directory, and a sibling-prefix negative. A case-variant spelling is tested when the volume is case-insensitive, and the suite reports when it skips that case.
  - R2: the context-only shape, and the exit code preserved at 1 on the ERR path.
  - R3: an MCP error reaches context with verbose logging off.

  Existing suites that assert the old combined shape are updated. Every hook suite and the Commander suite pass.
- **R5: redeploy.**
  - Run `deploy-hooks`.
  - Diff the Claude plugin's `hooks.json`; copy it only if it changed.
  - Run `codex plugin add`, then the cache `diff -rq`.
  - Write the deploy log.
- **R6: commit.** The operator commits L2b-1, L2b-1b and L2b-1c together, then re-trusts the 4 Codex hooks.

## Out of scope

- The verbose prompt echo at `state-activator.sh:172`.
- A case difference below the root (`Src/x` against `src/x`).
- The existing backlog: orphaned baseline and queue files; the SAD fallback; `index.lock` at claim; untracked byproducts; the `ExitPlanMode` matcher; the drift root not stored with the baseline; the zero-assignment-rows test.
