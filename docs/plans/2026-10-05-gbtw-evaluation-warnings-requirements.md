# GBTW evaluation warning requirements

Operator: “Please tell me you're failing open when the LLM call fails, and then just posts warnings, right?” Following confirmation that failure currently becomes grade A without a warning, operator instructed: “Do a new pm loop to add warnings”.

1. Failed LLM calls and malformed/missing grades must remain non-blocking and must emit a non-blocking warning identifying unavailable evaluation.
2. Reuse existing session warning queue for both clients; preserve native Stop output (Codex approve remains {}). Warnings reach next prompt context through existing drain.
3. Do not describe failed evaluations as passed. Preserve valid grades, deterministic gates, bounded retry escape and existing C warnings. Suppressed checks are not failures.
4. Cover failed calls, malformed/invalid grades, successful and suppressed checks, warning queue delivery and warning-write failure. No live model needed for deterministic failure routing.
5. Source and tests only plus PM docs. No deployment, config changes, commits or pushes. Preserve prior staged work.
