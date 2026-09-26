# kill_worker Retry Wording, Guard Robustness and Grader Claim Accuracy — Requirements

> **Created:** 2026-09-26
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-26-kill-worker-retry-wording-design.md

## Origin

This loop follows the tier-up (Fable) adversarial end review of the staged v1.1.13 diff. That review returned SOLID: every invariant holds. It raised one Important finding and several observations. The operator chose "Fix all 3 in a new loop (Recommended)".

## Operator directives

- "Fix all 3 in a new loop":
  1. `kill_worker`'s false "daemon will retry" claim for a worker the daemon never sweeps;
  2. the unwrapped `has_session` call in the new terminal guard;
  3. the README "Grader truncation can no longer happen" over-claim, with the same check applied to the CHANGELOG.
- Retry claim keyed on "A: post-seam registry status (Recommended)". "Daemon will retry" is said only if the registry re-read shows `status == 'running'`. Otherwise the message names the status and tells the caller to call `kill_worker` again.
- Design sections approved: "Yes, continue", then "Yes, write the design".
- Standing constraints:
  - The work folds into the unpushed v1.1.13 commit.
  - The seam owns all completion.
  - No push or deploy without an explicit go.
  - No commit trailers.
  - Never use `git stash`.

## Acceptance criteria

1. Both non-completed `kill_worker` status branches (seam-ok-but-not-completed, and finalization FAILED) end with `daemon will retry.` only when the post-seam registry re-read shows `status == "running"`. Otherwise they end with `worker status is '<status>' (not swept by the daemon); call kill_worker again to retry finalization.`, where a missing row or status renders as `'unknown'`.
2. When `has_session` raises in the terminal guard, `kill_worker` returns a dict with the status `Worker <id> is already <status>; nothing to finalize (session state unknown: <ExceptionType>).`. It does not call `kill_session`, and it logs a WARNING.
3. The README and CHANGELOG make no truncation claim broader than the Commander's local grader. Hook-side validation calls are described as getting the 8192-token floor only, still bounded by the hook transport budget. The CHANGELOG `kill_worker` bullet mentions the retry wording and the unknown-session case.
4. New tests fail before the change and pass after it. The full commander pytest suite reports 0 failed.
