# kill_worker Retry Wording, Guard Robustness and Grader Claim Accuracy — Design

> **Created:** 2026-09-26
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-26-kill-worker-retry-wording-requirements.md

## Summary

The Fable end review of the staged v1.1.13 diff found that `kill_worker` can tell the Brain "daemon will retry" when the daemon never will. The daemon sweeps only workers whose `status = 'running'` (`worker_registry.get_running_workers`). A worker marked `failed` by dead-session detection is never swept, and `kill_worker` is the only path that can integrate its reviewed work. If the Brain reads "daemon will retry" and moves on, that work is stranded. The Brain acts on these strings literally; it misread an earlier "preserved for retry" wording as final in real use on 2026-09-23.

The review also found two smaller defects. First, the new terminal guard calls `tmux.has_session` without a try/except, so an unreachable remote host raises out of the tool. Second, the README over-claims "Grader truncation can no longer happen": hook-side validation schemas are not length-bounded. All three fixes fold into the unpushed v1.1.13.

## Components

### 1. Retry clause keyed on post-seam registry status (`orchestrator_mcp.py` `kill_worker`)

- After the seam runs, `kill_worker` already re-reads `_wr = self.registry.get_worker(worker_id)`. From that re-read, compute `_post_status = (_wr or {}).get("status") or "unknown"` and the clause:
  - if `_post_status == "running"`, the clause is `daemon will retry.`;
  - otherwise, `worker status is '<_post_status>' (not swept by the daemon); call kill_worker again to retry finalization.`
- The seam-ok-but-not-completed branch and the FAILED branch both end with this clause in place of the hard-coded `daemon will retry.`. The completed branch is unchanged.
- The rest of each message stays as it is, character for character. That includes `status=<status>` in the seam-ok branch and `finalization FAILED (phase=…: …) — worker NOT completed; work preserved;` in the FAILED branch.

### 2. `has_session` failure in the terminal guard (`orchestrator_mcp.py` `kill_worker`)

- Wrap `self.tmux.has_session(session_name, ssh_host=ssh_host)` in `try/except Exception as exc`.
- On an exception, log a WARNING, skip `kill_session` (it needs the same host), and return the usual dict shape (`status`, `runtime_seconds: None`, `remaining_work`) with this status: `Worker <id> is already <status>; nothing to finalize (session state unknown: <type(exc).__name__>).`
- This mirrors `_complete_worker_if_session_dead`, which fails toward doing nothing when the liveness check raises.

### 3. Documentation accuracy (`README.md`, `CHANGELOG.md`)

- README "What's New in v1.1.13": replace "Grader truncation can no longer happen." Scope the claim to the Commander's local grader, where every schema is grammar-length-bounded and requests at least 8192 tokens. Hook-side validation calls get the 8192-token floor but have no per-field length caps, and stay bounded by the hook's transport budget.
- CHANGELOG `## 1.1.13`:
  - In the 8192-floor bullet, stop calling the hook's objects "grammar-bounded". The floor applies to all three call paths; the length caps apply to the Commander grader schemas only.
  - Extend the `kill_worker` bullet with one clause each for the retry-claim wording and the unknown-session case.

## Testing Strategy

The tests go in `commander/tests/test_worker_finalize_release.py`, using the existing `_unmanaged_kill_tools` fixture (shared-dict `get_worker`) and a `_finalize_and_release_worker` mock. Each test must fail against the current code.

- **FAILED branch with post status `failed`:** the status ends with the call-again clause, names `'failed'`, and does not contain `daemon will retry`.
- **FAILED branch with post status `running`:** the status still ends with `daemon will retry.`. This is a guard: the current code passes it, and it catches a regression that drops the running case.
- **Seam-ok branch with post status `failed`:** same assertion as the first test. The existing exact-string test with a `running` worker keeps passing unchanged.
- **`has_session` raising `RuntimeError` on a `completed` worker:** a dict is returned, the status says `session state unknown: RuntimeError`, and `kill_session` is not called. The current code raises instead.
- **Docs:** a grep proves the unscoped README sentence is gone.
- **Regression:** the `kill_worker` caller test files, then the full commander pytest suite at 0 failed.

## Implementation Notes

- This is a new effort, so it earns its own single blind plan review.
- The seam still owns all completion; there is no daemon change.
- Release is unchanged: amend the unpushed v1.1.13 commit and move its tag (operator-gated), redeploy, and push only on an explicit go.
