# Seam Finalization-Failure Observability Design

> **Created:** 2026-09-25
> **Status:** Design Complete
> **Scope mode:** reduction
> **Requirements:** docs/plans/2026-09-25-finalize-failure-observability-requirements.md

## Summary

Worker `gm-core-regen-1500-r2` (workspace `7973cf12`) had its terminal `abandon`
fail on every daemon tick for about 13 days, and nobody could see it. Three defects
combined to hide the failure:

1. `_abandon_rescue_worker` and `_probe_finalization_status` in
   `commander/src/ironclaude/orchestrator_mcp.py` catch exceptions and turn them into
   failure dicts or `None` without logging anything.
2. `_drive_finalization_recovery` in `commander/src/ironclaude/main.py` treats every
   non-`finalization` failure phase (authority, probe, abandon) as a silent
   `"transient"`, forever. Only `failure_phase == "finalization"` reaches the
   once-only operator surface.
3. When the seam fails, `kill_worker` returns "Worker X killed; unintegrated work
   preserved for retry (not completed)." The Brain read this as success five times.

This loop makes the failure visible. It does not change what happens to the worker.
The invariant "the orchestrator seam owns ALL worker completion; the daemon never
calls update_worker_status" still holds, and nothing added here completes, abandons
or kills a worker.

## Architecture

Three independent, additive changes:

- **(1) Logging at the swallow sites** (`orchestrator_mcp.py`). Each `except` that
  currently swallows an exception gets a `logger.warning` call. The log fires every
  time the exception occurs; there is no rate limiting. The operator chose this. The
  once-only escalation belongs to (2).
- **(2) Bounded consecutive-failure surface** (`main.py`). A per-worker counter for
  non-finalization failures, the same shape as `_finalize_drift_retry`. When the count
  passes a cap, the daemon posts once to Slack and the Brain.
- **(3) Unambiguous `kill_worker` status** (`orchestrator_mcp.py`). The
  failure-path string says FAILED and NOT completed, and names the phase and error.

## Components

### (1) Swallow-site logging — `orchestrator_mcp.py`

- `_probe_finalization_status` (the `except Exception:` at ~:3033, which returns
  `recovery_payload, None, transport`): change it to `except Exception as exc:` and log
  `logger.warning("finalization status probe failed for workspace %s: %s", assignment["workspace_guid"], exc)`
  before the unchanged return.
- `_abandon_rescue_worker`, authority `except` (~:3906): log
  `logger.warning("abandon-rescue for %s: plugin-root discovery failed: %s", worker_id, exc)`
  before the unchanged `_workspace_failure("authority", …)` return.
- `_abandon_rescue_worker`, abandon `except` (~:3943): log
  `logger.warning("abandon-rescue for %s: abandon failed: %s", worker_id, exc)`
  before the unchanged `_workspace_failure("abandon", …)` return.
- `_workspace_failure` stays unchanged. It also builds non-exception refusals, such as
  the "abandon-rescue refused" string, so logging inside it would log twice or at the
  wrong level.

### (2) Consecutive-failure surface — `main.py`

- New module constant beside `FINALIZE_DRIFT_RETRY_CAP` (~:894):
  `FINALIZE_FAILURE_SURFACE_CAP = 3`.
- New instance state beside `_finalize_drift_retry` (~:1588):
  `self._finalize_failure_count: dict[str, int] = {}`.
- In `_drive_finalization_recovery`'s final branch (~:1754-1775), for an outcome that
  is a dict with a truthy `failure_phase` other than `"finalization"`:
  - increment `_finalize_failure_count[worker_id]`;
  - once the count exceeds `FINALIZE_FAILURE_SURFACE_CAP` and `worker_id` is not in
    `_finalize_recovery_alerted`, add it to the set and post once via
    `self.slack.post_message` and `self.brain.send_message`. The message should read
    roughly: "Worker X terminal finalize has failed N consecutive cycles
    (phase=<phase>): <error>; left running, not completed, needs operator help."
  - The branch still returns `"transient"`.
- A `None` outcome or a phase-less dict does not count. The existing
  `phase == "finalization"` once-only surface is unchanged.
- Cleanup: drop `_finalize_failure_count` entries for workers no longer in
  `running_ids`, at the same two points that already prune `_finalize_drift_retry`
  (~:4168 and ~:4477).
- "Consecutive" means consecutive failed cycles while the worker is running. The worker
  leaves `running_ids` only when the seam completes it or the worker is reaped, and the
  cleanup resets the count at that point.

### (3) `kill_worker` status — `orchestrator_mcp.py` ~:6677

The non-completed branch returns:
`f"Worker {worker_id} session killed, but finalization FAILED (phase={phase}: {error}) — worker NOT completed; work preserved; daemon will retry."`
where `phase` and `error` come from the `_release` dict. If `_release` is not a dict,
`phase` is `unknown` and `error` is `no result`. The success string stays unchanged.

## Data Flow

1. The daemon tick calls `check_workers`, then `_finalize_and_release_worker(terminal=True)`,
   then the orchestrator seam, then `_abandon_rescue_worker`.
2. The abandon raises. The new WARNING goes to daemon.log, and the seam returns a
   failure dict with `failure_phase="abandon"`.
3. `_drive_finalization_recovery` increments the counter. On cycle
   `FINALIZE_FAILURE_SURFACE_CAP + 1` it posts once to Slack and the Brain. The worker
   stays running.
4. In the manual `kill_worker` path, the Brain receives a status string that states the
   failure plainly.

## Error Handling

- No new raise paths. The logging only adds calls to the existing `except` blocks.
- Slack and Brain posts follow the same style as the existing conflict/repair surface
  in the same function.

## Testing Strategy

pytest, in the existing commander test modules that cover these functions:

- (1) A `caplog` WARNING assertion for each of the three swallow sites. The test forces
  `_workspace_client.reconcile`, `discover_installed_plugin_root` and `abandon` to
  raise, then checks that the returned failure dict is unchanged.
- (2) Drive `_drive_finalization_recovery` with a `failure_phase="abandon"` outcome:
  - no post for the first `FINALIZE_FAILURE_SURFACE_CAP` calls;
  - exactly one Slack post and one Brain message on the next call;
  - no further posts after that;
  - every call returns `"transient"`;
  - `update_worker_status` is never called;
  - a `None` outcome does not increment the counter;
  - the counter is cleared when the worker leaves `running_ids`.
- (3) The `kill_worker` failure path: the status contains `FAILED`, `NOT completed`
  and the failure phase, and does not contain the old "unintegrated work preserved for
  retry" wording.

## Implementation Notes

- Out of scope:
  - seam-side completion for "nothing left to rescue" (a separate loop, once the error
    is visible);
  - stale repo paths (#2);
  - the root cause of the abandon failure itself.
- Pre-existing tests may assert the old `kill_worker` string. Grep for them and update
  them.
