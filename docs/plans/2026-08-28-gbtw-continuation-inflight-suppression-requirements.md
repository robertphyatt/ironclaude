# GBTW Continuation In-Flight Suppression (G1) — Requirements

> **Created:** 2026-08-28
> **Status:** Operator-approved (derived from the 2026-08-28 brainstorming + operator "Capture it")
> **Design:** docs/plans/2026-08-28-gbtw-continuation-inflight-suppression-design.md

This is the derived, operator-reviewed contract. It does not replace the operator directives or the
brainstorming that produced it; where they conflict, the operator statements win.

## Operator-settled decisions

- **D1 — Fix the confirmed G1 bug only.** The GBTW continuation-check suppression must recognize a
  live in-flight Agent subagent (backlog G1). Scope is held to that one stanza + tests; NOT the
  tasks-in-progress block (its over-firing was the stale deployment, already redeployed), NOT the
  checkpoint-anti-pattern detection, NOT tone changes (M4 done).

## Functional requirements

- **R1 — Live subagent suppresses the continuation check.** When a dispatched Agent subagent (Claude
  `async_launched`) or a Bash `run_in_background` job launched earlier in the transcript has no
  matching completion, the continuation ("diligently_finished_work") check must NOT fire — the
  "holding for the subagent" turn must not be graded D/F.
- **R2 — Reuse the existing completion-aware signal.** The fix consults the same completion-aware
  helper the tasks-in-progress block and code-review gate already use (`_gbtw_extract_in_flight`,
  via `_gbtw_review_gate_suppress`), not a new detector.
- **R3 — Named waiting tools still suppress.** Monitor / TaskOutput / ScheduleWakeup / AskUserQuestion
  recency continues to suppress the continuation check (regression preserved).
- **R4 — Anti-pattern re-arm preserved.** A trailing checkpoint / query-offload proposal
  (`_gbtw_should_rearm_check`) still re-arms the continuation check even when a job is in flight.

## Safety invariants (must hold)

- **S1 — No false-silence of a genuine stall.** The suppression is completion-aware: a completed,
  failed, killed, or stopped subagent (its task-notification carries `completed|failed|killed|stopped`)
  is subtracted from the in-flight set, so a real stall (no live job, no waiting tool) still fires the
  continuation check. A false-negative (silencing a real stall) is worse than the current over-firing.
- **S2 — Fail-open.** An empty/missing transcript or absent `jq` yields no suppression (the check
  fires) — the fix never fails toward silence.
- **S3 — Scope containment.** Touches only `worker/hooks/get-back-to-work-impl.sh` (the continuation
  suppression stanza) and its `tests/` predicate tests. No other hook, no LLM prompt, no firing-matrix
  change beyond that stanza.

## Out of scope

- The tasks-in-progress block (already completion-aware; deployment issue, redeployed).
- Checkpoint-anti-pattern detection (separate backlog).
- Tone / anxiety-clause changes (M4).
