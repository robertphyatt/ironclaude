# Opus-Backed Checks Keep Opus Design

> **Created:** 2026-10-16
> **Status:** Abandoned — superseded by docs/plans/2026-10-16-per-call-difficulty-tiering-design.md (never executed)

## Why abandoned

This design kept Opus as the backup for five Commander checks: the spawn pre-filter, the batch spawn pre-filter, send_to_worker, send_keys and post_message. Before it ran, the operator rejected that approach because it would overuse Opus: "I don't want to overuse opus when times haiku should be better suited for the task". The operator asked instead for every harness LLM call to be tiered by the difficulty of its task. Under that tiering these checks use the Haiku class first and, as fail-closed gates, step up one class to the Sonnet class. Only approve_plan and kill_worker stay on the Opus class. This document is kept so that "why not keep Opus as the backup?" has a recorded answer.

Loop: full. This changes Commander grader routing. It is small: about 2 source files plus tests and docs.

## Summary

This corrects the Haiku-class fast-model fallback (`docs/plans/2026-10-16-haiku-class-fast-model-fallback-design.md`, Tasks 1-8 staged).

That change made `LocalGrader.grade()` answer through the Haiku-class model whenever the local box failed. Five Commander checks already escalated to the **Opus grader** (`_call_grader`) on a local `infrastructure_error`. After the change they got Haiku's answer instead and never reached Opus, which silently replaced Opus as their backup. Under `backend: haiku` this happens on every call.

Operator, 2026-10-16: "Um I didn't intend for haiku to replace opus. HOLD UP let's fix this". Asked how these checks should work, the operator chose "Local → Opus, as before (Recommended)": these five checks skip the Haiku step, and Haiku is used only where there was no stronger backup. The operator approved this design ("Yes, write it up").

## Requirements

- **C1.** These five sites never use the Haiku-class model. On any local failure they escalate to `_call_grader` exactly as before. A local failure here means: connection error, open breaker, client-selection failure, non-JSON or missing-field output, or `backend: "haiku"` with no local box.
  - spawn pre-filter (`commander/src/ironclaude/orchestrator_mcp.py:4852-4860`)
  - batch spawn pre-filter (`:5717-5722`)
  - `send_to_worker` (`:6250-6255`)
  - `send_keys` (`:6388-6391`)
  - `post_message` (`:7491-7496`)
- **C2.** Every other local-grader consumer keeps the Haiku chain unchanged: the health check (`:6333`), Brain-message validator, permission-seeking check, prompt detection, awaiting-operator capture, and session summaries.
- **C3.** Mechanism:
  - `LocalGrader.grade(..., fast_fallback: bool = True)`. When `False`, every path that would call `_fast_model_step` instead returns the local `infrastructure_error`. For `backend: "haiku"` that is a new infrastructure error stating that no local box is configured.
  - `on_fast_model` is never called when `fast_fallback=False`.
  - `OrchestratorTools._call_local_grader(..., fast_fallback: bool = True)` passes the flag through, and the five sites pass `fast_fallback=False`.
- **C4.** Tests prove three things:
  - each of the five sites reaches `_call_grader` and never calls `fast_model._run_child` on a local failure, including under `backend: "haiku"`;
  - a positive control: a default (`fast_fallback=True`) grade still reaches the fast model;
  - `grade(fast_fallback=False)` returns the local error detail.
- **C5.** `worker/config-schema/llm-backend.md` (Haiku-class chain section) and `README.md` (Validation Backend section) name the exception: checks that already escalate to the Opus grader skip the Haiku step.
- **Non-goals:**
  - no change to the hook chain, Task 1-8 behavior elsewhere, the Opus grader itself, or the `_grade_bounded` fast-lane logic;
  - the `test_notifications.py` default-label fix stays in the next loop (operator decision "Carry into next loop").

## Testing Strategy

Extend `commander/tests/test_fast_model_chain.py` with tests per C4. Stub `fast_model._run_child`, which already fails by default via the conftest autouse fixture, so a test can assert it was never called. Patch `OrchestratorTools._call_grader` and assert it was called. Then run the full commander suite.
