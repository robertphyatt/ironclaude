# Parity Output and Path Fixes Design (v1.1.16 L2b-1c)

> **Created:** 2026-10-03
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-03-parity-output-path-fixes-requirements.md`
> **Loop:** full. This loop changes hook enforcement code.

## Summary

This loop closes the last items before L2b-1, L2b-1b and L2b-1c are committed together:

- the drift check's handling of absolute paths that are spelled differently from git's;
- state-activator's output shape on both clients;
- one level mismatch that hides MCP errors.

## Architecture

### 1. File identity walk (R1)

This changes `_drift_allowed_files` in `execution-drift-check.sh`. Its `case` statement keeps the `''`, `"$root"/*` and relative branches. The `/*` branch calls a new helper, `_drift_rel_by_identity ENTRY ROOT`:

1. Set `dir` to the dirname of the entry, then climb while `dir` is not an existing directory.
2. While `dir` is not `/`:
   - If `[ "$dir" -ef "$root" ]`, print `${entry#"$dir"/}` and return 0.
   - Otherwise, set `dir` to its dirname.
3. Return 1, so the entry is dropped.

**Why this approach:**
- `-ef` compares device and inode, so symlinks and APFS case variants resolve without depending on how `pwd -P` treats case.
- It handles a directory that does not exist yet.
- The rejected alternative was `canonicalize_path_portable`, which leaves case handling unmeasured and does not support missing parent directories.

### 2. Context-only output (D, R2)

This changes `_ic_sa_flush` in `state-activator.sh`:

- The jq program maps each buffered line to text, in order:
  - a `.systemMessage` string;
  - otherwise, a drained `.hookSpecificOutput.additionalContext`;
  - otherwise, the raw non-JSON line.
- It joins the texts with newlines and emits a single `{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext}}`.
- It prints nothing when there is no text, preserves `exit "$rc"`, keeps the cleanup, and keeps the `|| cat` fallback.

**Why this approach:**
- This shape is the one already verified on Codex and documented on Claude.
- The five always-on messages are enforcement failures that the model must act on.
- It needs less code than the merge of two keys.
- There is no branch on vendor.

### 3. MCP error level (R3)

`hook-logger.sh:614`: `"Error"` becomes `"ERROR"`, after a `git log -S` provenance check.

### 4. Verify and redeploy (R5)

This follows the same task shape as L2b-1b Task 3.

## Error Handling

The fail-open rules are unchanged.

- **The walk:** it stops at `/`. A relative or empty entry never reaches it.
- **The flush:** a jq failure falls back to raw `cat`, as before.

## Testing Strategy

| Requirement | Tests |
|---|---|
| R1 | A symlinked-parent spelling of the root does not block. A not-yet-existing directory under the root does not block. `$root-other/x` is still dropped. A case-variant spelling is tested only on a case-insensitive volume, with the skip reported. |
| R2 | Exactly one object with no `systemMessage` key, carrying both the error text and the queued warning. A forced ERR path exits 1 with its text in context. `test-warning-queue.sh` case 4 is updated to the new shape. |
| R3 | A line in `ironclaude-errors.log` appears in state-activator's context with verbose logging off. |

Each test is RED first. Then run the full hook suites and the Commander suite, and redeploy.
