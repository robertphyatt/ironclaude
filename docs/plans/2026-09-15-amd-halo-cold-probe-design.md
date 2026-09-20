# AMD Halo Grader Cold Probe Design

> **Created:** 2026-09-15
> **Status:** Design Complete

## Summary

A prior task requested disabling thinking mode and raising a health-check timeout for grader calls to AMD Halo (100.76.144.47:8080). Reading `commander/src/ironclaude/grader.py:214-217` and `commander/src/ironclaude/openai_client.py` showed both premises already false in the current codebase: thinking-disable fields (`reasoning_effort:"none"`, `chat_template_kwargs:{"enable_thinking":false}`) are sent by default on every grader call to an OpenAI-backend spot, and no 30s timeout exists anywhere in the grader call path (connect/probe timeouts default to 3s, inference read timeout is 600s in the deployed config). Commit `3aa719b` (2026-09-13) is a live investigation of this exact claim that reached the same conclusion: inconclusive, no fix needed.

The operator confirmed no current live signal (no degraded banner, no Slack alert, no daemon.log entry) is driving this — it is a cold re-check. This design covers exactly that: one live timed probe against AMD Halo reproducing grader.py's actual request shape, and a findings doc recording the result. No code changes.

## Architecture

A single-task investigation: an HTTP POST to AMD Halo's OpenAI-compatible endpoint using the same payload fields `grader.py` sends today, executed once during the execute-plans stage (the only stage with Bash access), with its wall-clock time and response shape recorded.

## Components

- **Probe request:** `POST http://100.76.144.47:8080/v1/chat/completions`
  - `model`: whatever `spots.grader.model ?? openai.model` currently resolves to in `~/.claude/ironclaude-hooks-config.json` (read at probe time, not hardcoded — the deployed value may have changed since design time).
  - `messages`: a minimal single-turn prompt (e.g. asking for a one-word reply), matching the shape `grader.py` sends.
  - `reasoning_effort: "none"`, `chat_template_kwargs: {"enable_thinking": false}` — the exact suppression fields `grader.py:214-217` sends by default.
  - `max_tokens: 16`, `temperature: 0.1` — matching `grader.py`'s existing values.
- **Findings doc:** `docs/plans/2026-09-15-amd-halo-cold-probe-findings.md`, recording the raw probe output, elapsed time, and the pass/fail verdict.

## Data Flow

1. Read the deployed `~/.claude/ironclaude-hooks-config.json` to confirm the current grader-spot model.
2. Issue the probe request via `curl` with `-w` timing output.
3. Record the HTTP status, `finish_reason`, presence/absence of a `reasoning_content` field, and elapsed time.
4. Write the findings doc stating the verdict.

## Error Handling

- If the probe fails to connect or times out: record that as the finding (box unreachable right now) rather than treating it as a code defect — matches `3aa719b`'s non-goal of not fixing a confirmed network issue in code.
- If the probe succeeds but exceeds 15s or shows `reasoning_content` present: that would contradict `3aa719b`'s finding and reopen the original hypothesis — record it plainly as a new, real finding requiring a follow-up brainstorm before any code change.

## Testing Strategy

Not applicable — this is a diagnostic probe, not a code change. The "test" is the probe's pass/fail condition documented above.

## Implementation Notes

- Do not touch `grader.py`, `openai_client.py`, or any config file. This task is read/probe/document only.
- Pass condition: HTTP 200, `finish_reason:"stop"`, no `reasoning_content` field in the response, wall-clock time ≤15s.
- Conclusion to record on pass: hypothesis disproved, `grader.py` correct as of `3aa719b`, AMD Halo healthy from the grader's perspective, no code change warranted. Close the task.
