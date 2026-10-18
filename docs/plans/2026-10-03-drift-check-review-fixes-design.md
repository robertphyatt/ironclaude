# Drift-Check Review Fixes Design (v1.1.16 L2b-1b)

> **Created:** 2026-10-03
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-03-drift-check-review-fixes-requirements.md`
> **Loop:** full. This loop changes guard and hook enforcement code.

## Summary

This loop fixes four verified defects that the L2b-1 end review found in the new execution drift check and in `state-activator`. All of the fixes stay within `execution-drift-check.sh`, its Stop caller and `state-activator.sh`, plus tests.

## Approach

The drift check reuses the write guard's own effective-root resolver. Duplicating the assignment lookup was rejected: it would let the drift check and the guard disagree about which checkout a session owns, which is exactly finding F1. There was no other viable approach.

## Components

1. **Effective root (R1).**
   - `execution-drift-check.sh` gains `_drift_effective_root SESSION CWD`.
   - It sources `workspace-path-adapter.sh`, which is already deployed next to it.
   - It computes the same inputs the guard's `workspace_prepare_input` derives: the workspace DB path, the repository identity (the canonical git common dir of the cwd), the cwd and the quoted session.
   - If the session has an active assignment row, it calls `workspace_bind_effective_root` and uses `WORKSPACE_EFFECTIVE_ROOT`.
   - With no assignment, it uses `git rev-parse --show-toplevel` of the cwd.
   - If an assignment exists but binding fails, it returns failure, which means fail open and log.
   - `ic_drift_record_baseline`, `ic_drift_violations` and the Stop path all use this one function.
   - The plan grounds the exact calling contract in `workspace_prepare_input` (`workspace-path-adapter.sh:395-480`): which variables must be set, and the side effects, such as the owner-heartbeat update, that are acceptable.
2. **Absolute allowed_files (R2).** `_drift_allowed_files` gains the effective root as an argument:
   - an entry beginning with `$root/` is stripped to the repo-relative form;
   - any other absolute entry is dropped;
   - relative entries pass through unchanged.

   Matching stays exact.
3. **`comm` locale (R3).** `ic_drift_violations` runs `LC_ALL=C comm -23`.
4. **Single JSON object (R4).**
   - `state-activator.sh` redirects the body's stdout into a buffer.
   - At exit it emits one object: `systemMessage` is the newline-joined `.systemMessage` values of every buffered object, and `hookSpecificOutput.additionalContext` comes from the drain when it is present.
   - It prints nothing when the buffer is empty.
   - Non-JSON lines in the buffer are appended to `systemMessage` text, so nothing is lost.

## Error Handling

The fail-open rules from L2b-1 are unchanged: no git repo, no plan or baseline, or professional mode off. One new fail-open is added: assignment binding failed. That case is logged.

## Testing Strategy

Each fix gets a RED→GREEN shell test, extending `test-execution-drift-check.sh` or adding a state-activator test:
- **F1:** a session with an active assignment to a worktree, plus a dirty file in the primary checkout. The check must not block. A new file inside the worktree must block.
- **F2:** `allowed_files` holds an absolute path under the root. A write to that file must not block.
- **F3:** the test runs under `LC_ALL=en_US.UTF-8`, using mixed-case and non-ASCII paths, and checks for no false drift.
- **F4:** a queued warning plus a forced `log_error` must yield exactly one parseable JSON object containing both texts.

Then the full hook suites, the Commander suite and the redeploy.
