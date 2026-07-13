# GBTW Monitor-Aware Tasks-In-Progress Gate — Design

> **Created:** 2026-07-12
> **Status:** Design Complete
> **Scope mode:** hold (fixed scope; maximum rigor within it)

## Summary

The IronClaude get-back-to-work (GBTW) stop hook has a deterministic
"tasks-in-progress" gate that blocks a worker from stopping while it has a
`wave_task` still `pending`/`in_progress` during plan execution. A worker that is
legitimately **waiting on a long-running background process streamed by a persistent
`Monitor`** (with a wave_task it *correctly* has not submitted yet) is blocked on
every stop attempt, thrashing against the block-throttle for the multi-hour life of
the run. This wastes the worker's turns and floods it with "keep executing" nudges
when there is genuinely nothing to do but wait.

**Confirmed root cause (systematic-debugging, evidence from
`~/.claude/ironclaude-hooks/get-back-to-work-claude.sh`):** the block at
`:392-398` (branch `:372`, gated by `is_plan_active` = `executing`/`reviewing` at
`:287`) is suppressed **only** by the in-flight check at `:378-389` →
`_gbtw_extract_in_flight` (`:37-93`), which recognizes background **Agent**
(`async_launched`, `:56-57`) and background **Bash** (`Command running in background
with ID`, `:58-60`) jobs — but **not** the `Monitor` tool. The hook *does* know a
Monitor means "legitimately waiting," but only in a different mechanism: the
continuation-suppression at `:790-812` (which lists Monitor/TaskOutput/ScheduleWakeup/
AskUserQuestion at `:801`) and the holding/waiting text matcher at `:820-826`. Both
only set `FIRE_CONTINUATION=false`, silencing the *LLM continuation check* (Priority 4,
`:1084`) — which never runs, because the deterministic gate at `:372` calls
`block_stop` and exits first. So the one gate that fires is Monitor-blind, while the
Monitor-awareness that exists is wired to a softer check the hard gate preempts.

**History (episodic memory, 2026-05-24):** the original suppression was a *tail-3
recency heuristic* (`detect_bg_job`) that *did* catch Monitor because it scanned
recent tool_use. A later rewrite to the launched-minus-completed *set-difference*
(`_gbtw_extract_in_flight`; see its own comment "Replaces the old tail-3 requestId
window") gained Bash/Agent precision but **dropped Monitor coverage**. This design
restores it — cleanly.

## Architecture

Add a single helper, **`_gbtw_recent_waiting_tool`**, to
`get-back-to-work-claude.sh`, defined alongside `_gbtw_extract_in_flight` and **above**
the `GBTW_TEST_MODE` early-return (`:97`) so the test harness can source it. It answers
one question — *"did the worker use a waiting tool in the last 3 assistant turns?"* —
and becomes the single source of truth for "waiting posture," consulted by **both** the
hard tasks-in-progress gate and the continuation check.

This is Approach 1 ("shared waiting-signal helper"), chosen over folding Monitor into
`_gbtw_extract_in_flight` (semantic mismatch: a Monitor is *persistent/watched*, not
*launched-and-awaited*, and its routine event-notifications would be hard to
distinguish from a terminal one) and over inline duplication (which would recreate the
very two-divergent-copies-of-"waiting" condition that caused this bug).

## Components

Edits — one hook file + its test harness:

1. **New helper `_gbtw_recent_waiting_tool <transcript>`** (near `_gbtw_extract_in_flight`,
   before the `GBTW_TEST_MODE` return): reads `tail -n 500` of the transcript → the last
   3 assistant `requestId`s → for each, inspects its `tool_use` blocks and returns
   *found* iff any block has `name ∈ {Monitor, TaskOutput, ScheduleWakeup,
   AskUserQuestion}` **or** `input.run_in_background == true`. This is exactly the set
   the continuation-suppression already trusts (`:801`) — reused, not re-invented.
   Contract: prints `true` (or exits 0) when found, empty (or exits 1) when not.

2. **Hard gate** (`:372` `IN_PROGRESS_OR_PENDING_COUNT != 0` branch): after the existing
   `_gbtw_extract_in_flight` suppression block (`:378-389`) and **before** `block_stop`
   (`:391`), add a second suppression: if `_gbtw_recent_waiting_tool "$TRANSCRIPT_PATH"`
   is found → emit an `approve` JSON (same shape as `:382-387`) with a distinct message
   `"[GET-BACK-TO-WORK]: Passed - waiting tool active (Monitor/ScheduleWakeup/…)"` and
   `exit 0`.

3. **Continuation-suppression** (`:790-812`): replace the inline
   `while`-over-requestIds tool_use scan with a call to `_gbtw_recent_waiting_tool`;
   on found, set `FIRE_CONTINUATION=false` (identical outcome to today). This is the
   "unify behind one signal" step — the two consumers now share one definition, so they
   can never drift apart again. The separate holding/waiting **text** matcher
   (`:820-826`) is left as-is (continuation-only).

## Data Flow

```
worker arms Monitor for a long suite → wave_task stays in_progress (correctly unsubmitted)
  → stop attempt → GBTW hook, is_plan_active + IN_PROGRESS_OR_PENDING_COUNT != 0
      → _gbtw_extract_in_flight  → empty (a Monitor is not a launched job)
      → _gbtw_recent_waiting_tool → FOUND (Monitor in last 3 turns)
          → approve + exit 0   [no block]
  ...
  Monitor ends, worker moves on → no waiting tool in last 3 turns
      → _gbtw_recent_waiting_tool → not found → block_stop resumes nudging
```

Same helper also feeds the continuation check (sets `FIRE_CONTINUATION=false`),
preserving today's behavior there.

## Error Handling

**Fail-direction is deliberate and asymmetric-safe.** On any jq/transcript failure the
helper returns **not-found**. Both consumers then behave safely:

- **Hard gate:** not-found → fall through to `block_stop`. The gate never *spuriously
  approves* a genuinely-stalled worker on a detection error (fail-closed for the gate),
  matching `_gbtw_extract_in_flight`'s existing fail-safe posture (`:37-45`).
- **Continuation check:** not-found → `FIRE_CONTINUATION` unchanged → the check fires.
  Identical to today's "any jq failure leaves FIRE_CONTINUATION unchanged" (`:789`).

The helper is wrapped so a missing/absent `$TRANSCRIPT_PATH` (already guarded at
`:378` for the gate) yields not-found rather than an error.

## Testing Strategy

Mirror `worker/hooks/tests/test-gbtw-inflight.sh`: source the hook with
`GBTW_TEST_MODE=1` (which exposes the helper functions without running the hook body),
then call `_gbtw_recent_waiting_tool <fixture>` and assert its result against new
`worker/hooks/tests/fixtures/w*.jsonl` transcripts. TDD: write the tests first (RED —
helper undefined), implement (GREEN). Cases:

- **w1** Monitor tool_use in the last assistant turn → found.
- **w2** Monitor 2 turns back (within the 3-turn window) → found.
- **w3** Monitor 5 turns back (outside the window) → not found (gate should block).
- **w4** No waiting tool, only text turns → not found.
- **w5** `ScheduleWakeup` → found.
- **w6** `run_in_background: true` Bash tool_use → found.
- **w7** `AskUserQuestion` → found.
- **w8** malformed JSONL line adjacent to a valid Monitor turn → no crash, still found.

No change to the existing `_gbtw_extract_in_flight` fixtures (F1–F10) — they must stay
green, proving the classifier is untouched.

## Implementation Notes

- **Deploy:** edit repo `worker/hooks/get-back-to-work-claude.sh` + the test file, then
  `make deploy-hooks` to sync `~/.claude/ironclaude-hooks/`. Redeploy also un-sticks the
  currently-thrashing worker. No hook/plugin/daemon restart otherwise required.
- **Window = 3 turns** (parity with today's continuation-suppression `tail -3`), so the
  continuation check's behavior is byte-for-byte preserved after extraction.
- **`_ic_`/`_gbtw_` variable prefixes** to avoid script-level collisions; propagate loop
  results with a here-string (`<<<`), not a pipe, to avoid subshell scoping (a
  documented prior gotcha).
- **Non-goals (hold scope / YAGNI):** (a) not folding Monitor into
  `_gbtw_extract_in_flight`; (b) not adding the holding/waiting *text* matcher to the
  hard gate (weaker, injection-prone — the tool-use signal suffices and is
  authored-by-harness, not worker-controlled); (c) not touching the throttle, the LLM
  checks, or any other gate.
- **Accepted residual risk:** a worker that arms a Monitor and then *truly* stalls will
  not be nudged by this gate. Mitigated by the block-throttle (still active) and by the
  fact that Monitor events are what re-invoke the worker (a dead Monitor stops producing
  events); `/goal`/operator drives continuation. Acceptable and documented.
- **No commit/push:** the operator commits and pushes manually.
