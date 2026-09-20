# Grader Circuit-Breaker Trip Against amd-halo — Requirements

> **Created:** 2026-09-13
> **Status:** Operator-approved (derived from brainstorming dialogue)

## Original operator ask

Fix `commander/src/ironclaude/grader.py` to stop the circuit breaker tripping against amd-halo (100.76.144.47:8080) under load. Operator-stated root cause: thinking mode ON for grader inference calls (responses 22-48s under GPU load) against a ~30s health-check timeout that trips every time.

Originally required: (1) disable thinking on grader calls, (2) raise health-check timeout to ~20s, (3) verdict-equivalence test before committing.

## Scope change approved during brainstorming

Static code review contradicted two of the three premises: `grader.py` already sends disable-thinking fields by default (v1.1.9), and no 30s timeout exists anywhere in the grader call path (v1.1.10 already decoupled probe/connect timeouts to 3s default). Two sources surfaced mid-session (`/tmp/ic/daemon.log` narration, an episodic-memory archive result) were found fabricating corroboration of the operator's exact claims and were excluded as evidence.

The operator selected **Approach A: live-probe-first, gated fix** over shipping the three original deliverables directly. Approved design: `docs/plans/2026-09-13-grader-thinking-timeout-investigation-design.md`.

## Requirements (current, operator-approved)

1. A live investigation task (Wave 1) must reproduce the actual root cause — process/version check of the running Commander daemon, direct curl probes against amd-halo with and without the current disable-thinking fields — before any code change is written.
2. Implementation (Wave 2) is gated on Wave 1's finding:
   - If thinking is confirmed on despite current fields (H1): implement whichever disable mechanism Wave 1's probe empirically demonstrated works, and size `probe_timeout`/`connect_timeout` (never `fast_lane_grade_timeout_seconds`) from measured latency.
   - If amd-halo is confirmed genuinely network-unreachable (H2): no `commander/` code change — the breaker is behaving correctly; document only.
   - If inconclusive: no Wave 2 code change; report findings back to the operator.
3. A verdict-equivalence test (thinking-disabled vs. thinking-enabled reference verdicts match) is required only if Wave 2A ships a payload change.
4. `fast_lane_grade_timeout_seconds` (default 5s, bounds an unrelated operator-fast-lane classifier path in `main.py`) must not be repurposed or raised as part of this work.
5. No memory write claiming root-cause resolution before Wave 1 evidence exists.

## Non-goals

- Changing circuit-breaker decision logic (`_attempt`, `record_slow`/`record_failure`) unless Wave 1 evidence specifically implicates it.
- Fixing any confirmed H2 network/DNS issue in code — that is an ops task outside this plan.
