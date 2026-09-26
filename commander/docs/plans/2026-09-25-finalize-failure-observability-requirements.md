# Seam Finalization-Failure Observability — Requirements

> **Created:** 2026-09-25
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-25-finalize-failure-observability-design.md

## Operator directives (this session)

- "yes, reactivate pm and proceed as recommended": fix the real #4 as observability
  in a single reduction-scope loop that covers #1, #2 and #3.
- The WARNING logs fire every time; the operator chose this over rate limiting.
- The design was approved as presented, with a cap of 3 and a once-only surface.
- Out of scope:
  - seam-side completion for "nothing left to rescue" (a later, separate loop);
  - stale repo paths (#2);
  - the root cause of the abandon failure itself.

## Acceptance criteria

1. Each swallowed exception logs at WARNING:
   - `_probe_finalization_status` when reconcile raises;
   - `_abandon_rescue_worker` when plugin-root discovery raises (authority) or when
     `abandon` raises.

   The return values stay unchanged.
2. `_drive_finalization_recovery` counts consecutive TERMINAL non-`finalization`
   failure phases for each worker. Non-terminal outcomes are not counted: the idle and
   pre-kill paths call the driver on every cycle for live workers, and an idle live
   worker legitimately returns a preserved failure, so counting those would fire false
   alerts. This follows the design's wording, "terminal finalize has failed". Once the count exceeds `FINALIZE_FAILURE_SURFACE_CAP` (3),
   it posts exactly once to Slack and the Brain, naming the worker, the cycle count,
   the phase and the error, and saying the worker was left running and not completed.
   - It still returns `"transient"`.
   - It never completes, abandons or kills the worker.
   - The counter is cleared when the worker leaves `running_ids`.
3. When the seam does not return success, `kill_worker` returns a status string that
   contains `FAILED` and `NOT completed`, plus the failure phase and error. It cannot
   be read as success.
4. Invariant preserved: the daemon never calls `update_worker_status`, and the seam
   owns completion.
5. The full commander pytest suite passes with 0 failed.

## Non-goals

- Any change to worker completion or abandonment behavior.
- Rate-limited or deduplicated logging.
- The drift, conflict/repair and `finalization` branches of the driver.
