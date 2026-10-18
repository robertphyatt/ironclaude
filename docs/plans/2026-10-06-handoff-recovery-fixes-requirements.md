# Handoff Recovery Fixes (L-H1) Requirements

> **Created:** 2026-10-06
> **Status:** Operator-approved. Resumed 2026-10-09 ahead of the Codex worker spawn fix.
> **Source findings:** `docs/reviews/2026-10-06-codex-work-validation-report.md`. It covers C1, C2, I1–I5, W1 and V1, plus T1, renamed here as L-T1.

## Operator decisions (verbatim)

- **Fable's three design questions:** "Adopt all (Rec.)".
- **I5, Brain collision:** "Bounded wait (Rec.)".
- **Detection:** "Per-cycle, cheap (Rec.)". This replaces Fable's suggestion to move the check to the reap, kill and finalize points. That suggestion assumed workers write `.done`, and they do not: `ENABLE_STOP_REVIEW=0` makes the Stop hook exit at `get-back-to-work-impl.sh:368`, before the `.done` write at `:1249`. A worker blocked on quota would therefore never reach those points.
- **Design sections 1, 2 and 3:** each approved "Yes (Rec.)".
- **Sequencing (superseded 2026-10-09):** "Spawn fix first (Rec.)" was replaced by "L-H1 first (Rec.)". Both loops change Commander code, and the Commander cannot be safely restarted while this unfinished handoff code is in `commander/src`, so this loop runs first and the spawn fix follows.
- **Planning gap-fills (2026-10-09):** "Approve all (Rec.)" — H5a, H5b, H7a and the accepted limitation L1 below.
- **Post-transfer recovery and related fixes (2026-10-09):** "Approve all (Rec.)" — H6a, the H6 quota amendment, the H9 reset-time note and H7b below.
- **Finish after the target commits, and one accepted edge (2026-10-09):** "Fix A, accept B (Rec.)" — H6b and limitation L2 below.
- **Design reuse (2026-10-09):** "Use for planning (Rec.)". The design was re-checked against the current source: the handoff files have not changed since the 10-06 validation, and every named symbol exists.

## Requirements

- **H1 (C1). Detection never blocks normal worker handling.**
  - Per-cycle detection only starts a handoff, and only on a positive `exhausted` verdict.
  - Lifecycle handling is suppressed only while a durable, unpaused `worker_handoffs` row exists.
  - Remote and path-less workers are excluded before any read, and they produce no notifications.
- **H2 (C1). Bad lines are skipped, not retried forever.**
  - The reader skips an oversize line (over 256 KiB) or a line that does not parse as JSON. It advances the cursor and line hash and increments `skipped_records`.
  - A torn final line, one with no newline, stays `unknown` and does not advance.
  - The 256 KiB cap is unchanged.
- **H3. An unchanged log is not re-read.** When the log's size and file identity match the cursor, the read is skipped.
- **H4 (I1). Both Claude limit windows count.** `rateLimitType` values `five_hour` and `seven_day` are both treated as exhausted. Any other type stays `unknown`.
- **H5 (C2). A handoff paused before the source is stopped releases automatically.**
  - **Scope:** phase `claimed`, with no stop intent recorded.
  - **Triggers:**
    - `native_quota_evidence_changed`;
    - any other pre-effect reason that will never succeed on retry.

    `opposite_capability_unavailable` is not a trigger. It keeps retrying until the source's quota resets.
  - **What release does:**
    - a compare-and-swap moves the worker from `handoff_pending` to `running`;
    - the handoff row is completed with disposition `released`;
    - the workspace hold moves to `finished`, through a new internal command `release-handoff`, and only while the assignment's owner is still the source.
  - **Refusal:** release is refused if a target was ever recorded.
- **H5a (approved 2026-10-09). A dead source also releases.** A pre-stop paused handoff also releases automatically when tmux positively reports the source session absent; otherwise a dead source would stay paused forever.
- **H5b (approved 2026-10-09). A consumed exhaustion event is not re-claimed.** Once a released handoff has consumed an exhaustion event, the daemon does not start a new handoff for that same event; only fresh exhaustion starts one. This prevents a claim/release loop every cycle.
- **H6 (C2). A handoff paused after the source is stopped retries forward.**
  - **Retry:** any client that is available again counts, including the source's own client after its quota resets. (Amended 2026-10-09: the post-stop retry does not re-read the stopped source's native log — that log is frozen by the proven stop and a re-read could only re-confirm the same event or freeze progress; the source client's availability is governed by the H9 quarantine expiry. Supersedes the earlier "Quota is re-checked with `require_same_event=False`".)
  - **H6a (approved 2026-10-09). After ownership has moved to the target:**
    - **Before the transfer completes** (workspace target recorded, assignment still owned by the source): release as the source and preserve; any recorded or launched read-only bootstrap is stopped and proved gone first (only an unprovable stop holds). If workspace readback shows the transfer actually landed, advance to the post-transfer case instead.
    - **After the transfer** (owner transferred, runtime registered, or continuation dispatched): retry forward whenever possible — a stale or half-started resume session is cleaned up first, and dispatch or finish proofs are retried.
    - **When forward is impossible for good:** stop any target session that received no continuation, adopt the TARGET's runtime, finish the workspace hold, and release with `preserve` so the work goes through the existing dead-session preserve path. Reverting ownership to the source is not done (no primitive exists and consumed human intents cannot be restored).
  - **When moving forward is impossible for good:** the hold is released and the worker goes through the existing dead-session preserve path (`reason="session ended"`, `terminal=True`), with one notification.
- **H7 (C2). An uncertain external effect needs an explicit abort.**
  - **Default:** on `uncertain_external_effect`, the pause stays, with one notification.
  - **The abort:** `recover_worker_integration(worker_id, "abort_handoff")` releases the handoff.
  - **Refusal:** the abort is refused while a recorded target bootstrap is alive, or cannot be proven gone by PID plus start time.
- **H6b (approved 2026-10-09). Finishing after the target has committed.** The workspace finish today requires the worktree HEAD to equal the HEAD recorded at grant. A finish used by H6a's retry-forward and preserve paths also accepts a HEAD that fast-forwards from (descends from) the granted HEAD, only while the assignment is owned by the target; a non-descendant HEAD or any other owner is refused. Without it, a target that commits after the transfer and then fails for good would stay stuck.
- **L2 (accepted limitation, 2026-10-09).** If only a bootstrap launch intent was recorded (no PID snapshot) AND the worktree directory was deleted outside IronClaude, H7a's proof needs the directory, so abort and preserve still hold for that case.
- **L3 (accepted limitation, 2026-10-09, "Accept as L3 (Rec.)").** If a live target rewrites history (a non-descendant HEAD, e.g. an amend) between continuation dispatch and the workspace finish, H6b's fast-forward-only finish refuses it and that worker stays `handoff_pending` with mutations blocked. Non-descendant finishes stay refused by design.
- **H7b (approved 2026-10-09). A deleted worktree does not block forever.** When the worktree directory no longer exists, abort and preserve accept a proof by PID plus start time that every recorded bootstrap process is gone (AccessDenied or an unprovable identity still blocks).
- **H7a (approved 2026-10-09). Launch intent without a recorded process.** When only a bootstrap launch intent was recorded (no PID snapshot), the bootstrap counts as gone only when its tmux session is absent AND no process is associated with the worktree; otherwise the abort is refused.
- **L1 (accepted limitation, 2026-10-09).** The reader's other mid-scan `unknown` outcomes (`unrelated_codex_quota_scope`, `native_metadata_missing`, `native_turn_mismatch`) still hold the cursor. After H1 they no longer block lifecycle handling, but they stop exhaustion detection for that worker. Not changed in this loop.
- **H8 (I2). A new controller rebuilds the bootstrap lease** from the persisted `evidence["bootstrap_process"]` (PID plus start time). A restart mid-handoff then resumes instead of pausing.
- **H9 (I4). Usage quarantines expire.**
  - **Reset time:** a quarantine row records the native reset time (Claude `resetsAt`, or a Codex reset time if present).
  - **Fallback:** without one, it expires after `worker_usage_quarantine_default_seconds` (default 18000).
  - **Validity (approved 2026-10-09):** a reset time that is not later than its quota event's time counts as absent, so the default applies.
  - **What expiry means:** it only re-enables trying that client. Fresh exhaustion quarantines it again.
- **H10 (I3). Dead owners' worker-operation leases are reaped.**
  - **What is reaped:** rows in phase `claimed` or `effects_started` whose owner is provably dead (PID plus start time, via `_operation_owner_status`). Each reap logs a `worker_operation_reaped` event.
  - **What still blocks:** a live owner, or one that cannot be proven dead.
- **H11 (W1). Dead processes' workspace-mutation claims are reclaimed.**
  - **Identity:** `process_identity` stores the PID plus the process start time.
  - **Reaping:** on a conflict, `withAssignmentMutation`, Commander `grant` and `beginHandoff` each reap a provably dead claim and retry once.
  - **Legacy rows:** rows without a start time still block, unless the PID no longer exists.
- **H12. The Commander sweeps dead-owner rows at startup.** It clears them in `worker_operations`, and in `workspace_mutations` through a new internal command `reap-stale-mutations`.
- **H13 (I5). Brain operations wait briefly on a busy worker.**
  - When a Brain-side wrapped operation finds a lease held by a live owner, it polls for up to 5 seconds, every 200 ms.
  - If the lease is still held, it raises "worker <id> is busy (daemon cycle in progress); retry shortly".
- **H14 (V1). The stale tests are updated and input is validated first.**
  - `db.test.ts` expects migrations `[1..7]`.
  - The `tool-dispatch.test.ts` command list includes the new commands, and its forbidden-name checks stay.
  - Private-CLI input is validated before `withAssignmentMutation`.
- **H15 (L-T1). The two hook tests use `grep`, not `rg`.** This applies to `worker/hooks/tests/test-gbtw-continuation-judge.sh` and `test-last-assistant-text.sh`.
- **H16. Everything is verified in full before commit:**
  - every check runs in full: all hook suites, the full vitest suite, `tsc`, and the full Commander pytest;
  - the dist bundles are rebuilt;
  - a final Fable review passes.

## Out of scope

- The Codex worker spawn fix, which gets its own loop.
- Changing the `.done` and stop-review contract.
- Push and deploy, which need an explicit go.
