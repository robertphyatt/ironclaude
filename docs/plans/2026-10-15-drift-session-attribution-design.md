# Drift Check Session Attribution Design

> **Created:** 2026-10-15
> **Status:** Design Complete

Loop: full — guard/hook enforcement code shared by Claude and Codex (hooks.json, execution-drift-check.sh, get-back-to-work-impl.sh).

## Summary

`execution-drift-check.sh` blocks `submit_task` (and Stop) on any path that became dirty in the session's checkout after the task was claimed and is not in the plan's `allowed_files`. In a shared checkout this charges every other session's work to the executing session. The roleplaying-agents C1 session was blocked twice: 3 files on 2026-10-11, and 24 files from four other efforts on 2026-10-14. C1 had touched none of them. Waiting cannot clear it, and the managed-worktree escape refuses on a dirty primary (`worker/skills/use-managed-worktree/SKILL.md:42-44`).

This design attributes drift to the session that made it. The hook records what changed during this session's own shell commands, and the check considers only those paths.

## Requirements

Operator directives:
- 2026-10-14, "Yes please": fix this.
- 2026-10-11: "why do you care about another session in any way?"
- One IronClaude for Claude and Codex.
- Operators never hand-fix worktrees.

Decisions taken in this brainstorm (AskUserQuestion, 2026-10-15):

- **R1:** drift counts only paths that changed during this session's own shell commands, including its subagents' commands. Other sessions' changes are never counted. *(Approach: "Approve (Recommended)", the Fable-advised command-window delta ledger.)*
- **R2:** the same mechanism serves both clients: Claude `Bash` and Codex `exec_command`. There is no client fork.
- **R3:** accept these known fail-open channels and record them as limits:
  - Codex `write_stdin` input to long-running exec sessions;
  - background commands that write after their PostToolUse;
  - files written by MCP tools.
  
  *("Accept gaps (Recommended)".)*
- **R3b:** windows are scoped to an agent lineage: the main session, or a specific subagent (`agent_id`). A command's own PostToolUse closes its window. That agent's next shell PreToolUse closes any leftover windows of the same lineage, which covers commands denied before running and failed Claude Bash calls. Other lineages' windows are never touched. Accepted gap: when one agent runs two shell commands in parallel, the second can close the first's window early, and a write the first makes after that is not counted. It never causes a false block. *(2026-10-15, "Same-agent close (Recommended)", after the blind plan review found that closing every window truncated concurrent subagent windows.)* A finished subagent's windows also close at SubagentStop.
- **R3c:** at `submit_task` and at Stop, the main lineage's open windows are *closed*: the delta is appended and the window deleted. The main session cannot have a foreground command running at that moment, so any open main window is a leftover from a failed (`PostToolUseFailure`) or denied command. Checkpointing it would absorb other sessions' writes. Subagent lineages are still checkpointed and kept. The accepted gap is the same class as R3b: a shell command the main session issues in parallel with its own submit can lose its tail. *(2026-10-15, "Close at submit/Stop (Recommended)", after the Fable end review.)*
- **R3d:** a window file that cannot be read, or whose root line is empty, is skipped with nothing appended. Reading it anyway would make `git -C ""` list the whole checkout. *(Fable end review finding A, 2026-10-15.)*
- **R3e:** closing a window never waits on the workflow stage, on tool routing, or on resolving the effective root, because the root is line 1 of the window file. A shell PostToolUse closes its window, and a shell PreToolUse closes its lineage's leftovers, right after the professional-mode gate. A window left open across a retreat or stage change, or across a failed root lookup, is therefore closed at the lineage's next shell call; it is not kept until the session is back in executing. *(Fable end review #2, 2026-10-15; an ordering fix inside R3b/R3c.)*
- **R3f:** a *sweep* closes a leftover window and **discards** its delta. Sweeps are the same lineage's next shell PreToolUse (R3b), SubagentStop, and the main-lineage close at submit/Stop (R3c). A leftover belongs to a command that failed (`PostToolUseFailure`) or was denied, and any change seen at sweep time may be another session's write made after the command ended. Only a command's own PostToolUse close, and the checkpoint of a still-running subagent window at submit/Stop, append to the ledger. Accepted fail-open: this session's own off-plan writes from a failed or denied command are not counted, and neither is the tail of a main-lineage command issued in parallel with its own submit. *(2026-10-15, "Discard on sweep (Recommended)", after Fable end review #3.)*
- **R3g:** `submit_task` and Stop **discard every open window** of the session, main and subagent alike, without appending. This supersedes the checkpoint-and-keep parts of the Architecture and R3c. Only a command's own PostToolUse close appends to the ledger, so the only remaining false block is another session writing during one of this session's own running commands (R4). The checkpoint mode is removed. Accepted fail-open: a subagent command still running at the main session's submit or Stop loses its changes. If `agent_id` turns out to be absent from subagent hook payloads, subagent windows fold into `main`. That too is fail-open: writes can be missed, and nothing is falsely blocked. Check live after deploy. *(2026-10-15, "Discard all at submit/Stop (Recommended)", after Fable end review #4.)*
- **Accepted (recorded):** this session's own edit to a file that was already dirty when its window opened does not enter the ledger. The delta is a set difference of dirty paths, the same rule the claim-time baseline used.
- **Cost:** about 2.5 s per mutating shell command while a task is executing (`git status` measured at 1.23–1.29 s in roleplaying-agents). Accepted for now; *"Accept for now (Recommended)"*, 2026-10-15.
- **R4:** a residual false block (another session writes during one of this session's command windows) still blocks. The block message names the file, says it may be another session's, and lists the ways out:
  1. it clears automatically once the file is committed or reverted;
  2. retreat and add the file to `allowed_files`;
  3. ask the operator.
  
  No escape counter. *("Block, list ways out (Recommended)".)*
- **R5:** reword the block text so it never tells the session to remove, revert or touch files it did not change. *("Reword (Recommended)".)*
- **R6:** edit tools keep their write-time `allowed_files` check, unchanged.

Non-goals:
- an operator-clear mechanism;
- excluding other sessions' plan files;
- mtime attribution;
- closing the R3 channels;
- the `subagent_sessions`/`linkSubagent` dead-code observation (Boy Scout note only, recorded as backlog).

## Architecture

The design was advised by a Fable subagent (2026-10-14) and verified against source.

- The write guard already path-checks Edit/Write/MultiEdit/NotebookEdit, and Codex `apply_patch` (which is normalized to MultiEdit), against `allowed_files` (`professional-mode-guard.sh:751-759`, `:226`). Shell commands are only pattern-checked for git commit/push (`:813-814`). So the drift check's only unique job is catching shell writes.
- Every hook reads `.session_id` into `SESSION_TAG` (`hook-logger.sh:97-100`). Claude subagent tool calls fire the parent's hooks with the parent's `session_id`; the Fable subagent verified this live (tag `professional-mode-guard|7c7f63a0-…`). So the windows cover subagent shell writes.

**Command-window delta ledger.** It is active only when professional mode is `on`, the stage is executing or reviewing, and a task is in progress, which is the same gate the hook uses today.

1. **Open window.** On PreToolUse for `Bash|exec_command`:
   - the lineage is `agent_id` if present, otherwise `main`;
   - first close this lineage's leftover windows (R3b);
   - a missing `tool_use_id` opens nothing; both clients document it on Pre and Post;
   - if `workspace_input_is_readonly` (`workspace-path-adapter.sh:25`) says the command is read-only, open nothing;
   - otherwise snapshot `_drift_dirty_paths` (`execution-drift-check.sh:113`) for the effective root into `~/.claude/ironclaude-drift-window-<session>.<lineage>.<tool_use_id>.txt`.
2. **Close window.** On PostToolUse for that lineage and `tool_use_id`, or on that lineage's next shell PreToolUse (R3b):
   - delta = dirty-now − snapshot;
   - append the delta to the ledger of each in-progress task, `~/.claude/ironclaude-drift-touched-<session>.<row>.txt`;
   - delete the window file.
   
   Codex documents PostToolUse for `exec_command` with a `tool_use_id`, so both clients close by key.
3. **Check.** At `submit_task` and at Stop, close the main lineage's open windows (R3c) and *checkpoint* every other open window of the session: append its delta so far, then re-snapshot and keep the file, so a still-running command keeps being tracked. Then compute drift = (ledger ∩ dirty-now) − `allowed_files`. The intersection with dirty-now drops paths that were later committed or reverted, which gives R4's automatic clearing. The claim-time baseline is retired.

## Components

- `worker/hooks/execution-drift-check.sh`:
  - window open/close functions and ledger append;
  - `ic_drift_violations` reads the ledger instead of the baseline;
  - retire `ic_drift_record_baseline` and the baseline files;
  - new block text (R4/R5).
- `worker/hooks/hooks.json`: add `Bash|exec_command` to the existing drift PreToolUse and PostToolUse registrations, so the hook stays registered exactly once per event.
- `worker/hooks/get-back-to-work-impl.sh`: comment and log wording only. `ic_drift_stop_violations` checkpoints the windows itself.
- Tests:
  - update `worker/hooks/tests/test-execution-drift-check.sh`;
  - keep `test-execution-drift-roots.sh` and `test-execution-drift-identity.sh` passing.

## Data Flow

1. Shell PreToolUse: close this lineage's leftover windows. Resolve the effective root (existing `_drift_effective_root`), then snapshot to the window file keyed by lineage and `tool_use_id`.
2. Shell PostToolUse, or the same lineage's next PreToolUse: snapshot again, append the delta to the ledgers of the in-progress tasks, and delete the window file.
3. `submit_task` PreToolUse: checkpoint open windows, then (ledger ∩ dirty-now) − `allowed_files`. Non-empty → block with the R4/R5 text. Empty → allow.
4. Stop (GBTW): checkpoint open windows, then the same check across in-progress tasks.

## Error Handling

The existing fail-open rules stay:
- professional mode not on;
- wrong stage;
- no git root;
- unreadable assignment, plan or ledger.

An unsupported newline path keeps today's dedicated block. A missing or unreadable window file at close fails open for that window and is logged, never blocked. A `git status` failure at open skips the window and is logged.

## Testing Strategy

Every test must fail if the attribution code is deleted.

**Positive cases:**
- concurrent windows of two subagents: B opening and closing never truncates A, and A's later write blocks;
- submit with a window still open checkpoints it, so the delta is counted and the window is kept;
- the session's own shell create inside a window → blocked;
- the session's own `rm` of a tracked file → blocked;
- the session's own rename → blocked;
- Codex shape (`exec_command` PreToolUse with no PostToolUse, then `submit_task`) → blocked;
- subagent-shaped payload (`agent_id` set, same `session_id`) → blocked.

**Negative cases (the loosening is bounded):**
- a file created between this session's PostToolUse and its next PreToolUse (another session) → not blocked;
- a file that appears during a read-only command → not blocked;
- a ledger path later reverted → not blocked;
- PM off, stage not executing, or no `tool_use_id` → no window files written;
- the hooks.json matcher matches `Bash`, `exec_command` and both state-manager names, and not `Read` or `Grep`.

**Regression:** the existing root/identity suites pass.

## Implementation Notes

- **Implementer verifications.** These are evidence steps, not operator work:
  - a post-deploy live check that a Codex `exec_command` PostToolUse closes its window. The Codex hooks docs (learn.chatgpt.com/docs/hooks) list PostToolUse for unified exec with `tool_use_id`, and the payload's `tool_name` may be `Bash`; the dispatch accepts both names;
  - failed Claude Bash calls fire `PostToolUseFailure`, not PostToolUse. Their windows close at the lineage's next PreToolUse (R3b); no `PostToolUseFailure` registration is added, because the shared `hooks.json` must stay valid for Codex;
  - one-shot timing of `git status --porcelain=v1 -z --untracked-files=all` in roleplaying-agents to confirm the per-command cost is acceptable.
- Read-only commands open no window, so the cost lands only on mutating shell commands while executing.
- **Deploy.** The hooks run from the plugin cache, so the change only takes effect after a redeploy, the same as every hooks change.
- **Backlog.** `linkSubagent` (`state-manager/src/db.ts:666`) has no caller, so GBTW `IS_SUBAGENT` is always false. Recorded only.
