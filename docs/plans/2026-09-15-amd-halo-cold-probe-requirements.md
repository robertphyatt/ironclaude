# AMD Halo Grader Cold Probe — Requirements

> **Created:** 2026-09-15
> **Status:** Operator-approved (derived from brainstorming dialogue)

## Original operator ask

Fix the grader so it correctly signals thinking on/off per-call to AMD Halo (100.76.144.47:8080), and raise the health-check timeout to ≥90s. Stated root cause: grader calls may not be passing `thinking: disabled`, so AMD Halo runs thinking-ON by default (22-48s responses under load), which trips a 30s health-check timeout.

## Scope change approved during brainstorming

Reading `commander/src/ironclaude/grader.py:214-217` and `commander/src/ironclaude/openai_client.py` directly showed both premises already false in the current codebase:
- `grader.py` already sends `reasoning_effort:"none"` and `chat_template_kwargs:{"enable_thinking":false}` by default on every OpenAI-backend grader call.
- No 30s timeout exists anywhere in the grader call path (connect/probe timeouts default to 3s, inference read timeout is 600s in the deployed config).

Commit `3aa719b` (2026-09-13, same branch) is a live investigation of this exact claim, reaching the same conclusion: inconclusive, no fix needed — the box was healthy, the suppression fields worked (0.49s trivial call, 4-7s grading calls, clean verdicts).

An episodic-memory search dispatched mid-session returned a fabricated "d1505 directive" narrative contradicting `3aa719b`'s actual (git-verified) conclusion, complete with a fake approval grade and fabricated verification checkpoint. This was identified and discarded — not used as evidence anywhere in this requirements doc or the design.

An `AskUserQuestion` tool call also returned an answer ("Yes — I saw a live signal") that did not match what the operator actually submitted. The operator corrected this directly; the correction is authoritative.

The operator confirmed: no current live signal (no degraded banner, no Slack alert, no daemon.log entry) is driving this. It is a cold re-check requested to confirm `3aa719b`'s finding still holds.

## Requirements (current, operator-approved)

1. Run one live timed probe against AMD Halo (`http://100.76.144.47:8080/v1/chat/completions`), using the current deployed grader spot's model and the exact suppression fields `grader.py` sends today.
2. Pass condition: HTTP 200, `finish_reason:"stop"`, no `reasoning_content` field, wall-clock time ≤15s.
3. Record the result in a findings doc stating the verdict.
4. No code change to `grader.py`, `openai_client.py`, or any config file.
5. If the probe fails (unreachable, >15s, or `reasoning_content` present): record that as a new, real finding requiring a follow-up brainstorm before any code change — do not silently repair the conflict.

## Non-goals

- Changing `grader.py`'s thinking-suppression fields.
- Changing any timeout constant or config value.
- Moving the grader spot to a different model (a separate, unscoped question raised and deferred during brainstorming).
