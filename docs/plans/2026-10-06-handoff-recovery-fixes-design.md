# Handoff Recovery Fixes (L-H1) Design

> **Created:** 2026-10-06
> **Status:** Design complete; resumed 2026-10-09 ahead of the Codex worker spawn fix (operator: "L-H1 first (Rec.)", "Use for planning (Rec.)")
> **Requirements:** `docs/plans/2026-10-06-handoff-recovery-fixes-requirements.md`
> **Loop:** full, with a final Fable review before commit

## Summary

The staged Claude↔Codex worker handoff has the right shape, but its recovery behaviour is wrong in four ways:
- Ambiguous native evidence freezes the normal handling of every managed worker.
- Some paused handoffs can never return to running.
- Locks left behind by a crash are never cleared.
- The Claude five-hour limit is ignored.

This design keeps the feature and fixes all four. Every failure path ends in one of three ways: the worker runs again, the handoff moves forward, or the work is preserved through the existing dead-session path. The operator is needed only for an effect that genuinely cannot be decided, and then through one action, never a database edit.

## Architecture

1. **Detection is separate from suppression.**
   - The per-cycle quota read only starts a handoff, and only on a positive `exhausted` verdict.
   - Lifecycle handling is suppressed only by an unpaused `worker_handoffs` row.
   - `_native_handoff_gate` is split into `maybe_start_handoff(worker)`, which never suppresses, and `handoff_suppresses(worker)`, which checks the row.
2. **The reader always moves forward.**
   - `read_native_quota` skips an oversize or unparseable complete line, advancing the cursor and hash and counting `skipped_records`.
   - A torn final line returns `unknown` without moving the cursor.
   - If the log's size and identity are unchanged, the read is skipped.
   - Remote and path-less workers return before any read.
3. **Recovery depends on how far the handoff got.** When a handoff is paused, the controller checks its phase and evidence:

   | Case | Outcome |
   |---|---|
   | Before the source is stopped | Release automatically, back to running |
   | After the source is stopped | Retry forward; the source's own client becomes a valid target once its quota resets |
   | After the source is stopped, and forward is impossible | Release, then preserve through the dead-session path |
   | Uncertain external effect | Stay paused; the operator can run `abort_handoff` |

   Post-transfer recovery (2026-10-09, requirements H6a, H7b, amended H6 and H9): a pause before the transfer completes releases as the source after stopping and proving gone any read-only bootstrap (if readback shows the transfer landed, it is treated as post-transfer); after the transfer, retry forward (clean a stale resume session, retry dispatch/finish proofs); when forward is impossible for good, stop any target session that got no continuation, adopt the target runtime, finish the workspace hold and preserve through the dead-session path. The post-stop retry never re-reads the stopped source's log (H9 expiry governs the source client). A reset time not later than its event counts as absent. A deleted worktree is proved gone by PID plus start time instead of blocking forever. A finish after the target has committed accepts a HEAD that fast-forwards from the granted HEAD while the target owns the assignment (H6b). Accepted limitation L2: a launch-intent-only bootstrap plus an externally deleted worktree still holds.

   Approved additions (2026-10-09, requirements H5a, H5b, H7a, L1): a pre-stop pause also releases when tmux positively reports the source session absent; a released handoff's exhaustion event is never re-claimed (only fresh exhaustion starts a new handoff); with only a launch intent and no recorded process, abort treats the bootstrap as gone only when its tmux session is absent and no process is tied to the worktree; the reader's other mid-scan `unknown` outcomes keep the cursor (accepted limitation).

4. **One dead-owner rule for every lock.** The same check, PID plus process start time, runs at every acquire and at startup. It covers Commander `worker_operations` (both `claimed` and `effects_started` rows) and workspace `workspace_mutations`. A lock whose owner cannot be proven dead still blocks.

## Components

- **`worker_quota.py`**:
  - skip-and-advance for bad lines;
  - the torn-line wait;
  - short-circuit state (`size`, `ino`/`dev`);
  - `CLAUDE_RATE_LIMIT_TYPES = {"five_hour","seven_day"}`.
- **`main.py`**:
  - the gate split, used in `check_workers` and `_confirm_and_kill_stuck_worker`;
  - excluding remote and path-less workers;
  - the startup sweep;
  - routing a permanently blocked post-stop pause to `_finalize_and_release_worker(reason="session ended", terminal=True)`.
- **`worker_handoff.py`**:
  - phase classification in the retry and reconcile path;
  - automatic release;
  - forward retry that allows the source's own client;
  - the `abort_handoff` entry;
  - rebuilding the lease from `evidence["bootstrap_process"]`.
- **`worker_handoff_process.py`**: seeds the read-only lease from the persisted identity, after re-proving that PID plus start time still refer to the live process.
- **`worker_registry.py`**:
  - `release_worker_handoff`, a compare-and-swap from `handoff_pending` to `running` that completes the row with disposition `released`;
  - dead-owner reaping of `effects_started` rows, plus a `worker_operation_reaped` event;
  - a bounded-wait acquire used by the Brain wrapper.
- **`provider_state.py`**: a quarantine `expires_at`, taken from the native reset time or the default. `is_available` treats an expired quarantine as cleared.
- **`orchestrator_mcp.py`**:
  - `_hold_worker_operation` waits up to 5 s, polling every 200 ms;
  - `recover_worker_integration` gains an `abort_handoff` action.
- **`workspace_handoff_authority.py`**: `grant` reaps a dead claim and retries once.
- **Workspace manager**:
  - **`handoff.ts`**: the `release-handoff` primitive. `withAssignmentMutation` stores PID plus start time, reaps a dead claim and retries once. `beginHandoff` does the same.
  - **`cli.ts`**: the internal `release-handoff` and `reap-stale-mutations` commands. Input is validated before the mutation wrapper.
  - **`db.ts`**: no new migration unless the identity format needs a column.
- **Tests**:
  - fix the stale tests in `db.test.ts` and `tool-dispatch.test.ts`;
  - replace `rg` with `grep` in the two hook tests.

## Data Flow

1. **Every cycle, for each running local worker:**
   1. Stat the native log and compare it with the cursor. Read only the new bytes.
   2. On `exhausted`, start a handoff (this creates the handoff row).
   3. If `handoff_suppresses` is true, skip the worker. Otherwise run normal handling.
2. **Every cycle, for each paused handoff:**
   - **Before the source is stopped:** release it, or keep retrying while the opposite client is unavailable.
   - **After the source is stopped:** retry forward, or preserve the work.
   - **Uncertain:** notify once.
3. **At startup:** reap rows with dead owners in both tables, then run the normal reconcile. The rebuilt leases let handoffs already in progress resume.

## Error Handling

- **Unproven identity always blocks and is never reaped.** This covers AccessDenied, or a live PID with no recorded start time.
- **Every release and reap is a compare-and-swap** against a recorded snapshot or nonce. Losing the race changes nothing, and the next cycle sees fresh state.
- **Notifications fire once** per handoff per state, never for `unknown` evidence.

## Testing Strategy

Each behaviour has a positive case and a negative case.

- **Reader**:
  - an oversize line followed by an exhaustion record gives `exhausted`, with the cursor past both;
  - a malformed line is skipped;
  - a torn line stays `unknown`, then gives `exhausted` once its newline is written;
  - an unchanged file is not read (0 reads).
- **Rate-limit types**:
  - `five_hour` and `seven_day` both give `exhausted`;
  - an unknown type gives `unknown`.
- **Gate and remote workers**:
  - `unknown` no longer suppresses handling (the old test is flipped);
  - an unpaused handoff row still suppresses it;
  - a remote worker produces 0 notifications.
- **Release, retry and abort**:
  - **Release before stop:** changed evidence returns the worker to `running` and sets the workspace hold to `finished`. Release is refused once a target is recorded.
  - **After stop:** a same-client retry completes after the quota resets. A permanent failure is preserved, not completed, with one notification.
  - **Abort:** `abort_handoff` is refused while the target is alive or unproven, and allowed once it is proven gone.
  - **Restart:** a restart between `target_ready` and `owner_transferred` resumes the handoff.
- **Quarantine**:
  - it expires at the native reset time, or at the default;
  - fresh exhaustion quarantines the client again.
- **Lease reaping**:
  - a dead owner's `claimed` or `effects_started` row is reaped and the event is logged;
  - a live owner blocks, and so does an unproven one;
  - the same PID with a different start time counts as dead and is reaped.
- **Workspace claims**:
  - a claim left by a dead process no longer blocks finalize, sync, grant or begin (the `handoff.test.ts:72` assertion is inverted);
  - a live process's claim still blocks.
- **Startup sweep**: clears dead rows and leaves live ones.
- **Brain wait**:
  - it succeeds when the lease is released within 5 s;
  - otherwise it raises a clear "busy" error after 5 s.
- **Private CLI input**: malformed input is rejected without touching the database.
- **Full verification at the end**: run everything (hooks, the full vitest suite, `tsc`, the full Commander pytest), rebuild dist, then the final Fable review.

## Implementation Notes

- **Don't restart the Commander early.** It runs as an editable install from `commander/src`, so do not restart it until this loop and its review land.
- **Getting the process start time:**
  - **Python:** `psutil.Process.create_time()`.
  - **TypeScript:** `ps -o lstart= -p <pid>`, or `/proc` where available, converted to epoch seconds.

  Both sides must compare start times in the same representation.
