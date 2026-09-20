# Grader Circuit-Breaker Trip Against amd-halo — Investigation-Gated Fix Design

> **Created:** 2026-09-13
> **Status:** Design Complete

## Summary

The operator reported the grader circuit breaker tripping against amd-halo (100.76.144.47:8080, openai backend, model `gemma4-26b-a4b`), attributed to thinking mode staying on despite `grader.py` already sending disable-thinking fields, causing 22-48s responses that trip a health-check timeout the operator estimated at ~30s.

Static code review (HEAD `255d594`, includes v1.1.9's thinking-off default and v1.1.10's three-state busy/down breaker) could not confirm this: `grader.py:214-217` already sends `reasoning_effort:"none"` + `chat_template_kwargs.enable_thinking:false` by default; no 30s timeout literal exists anywhere in the grader call path (`grader.py`, `ollama_client.py`, `openai_client.py`, `backend_resolver.py`, `main.py`); and `openai_client.py` already routes a slow-but-reachable response to `record_slow` (not `record_failure`) per the v1.1.10 fix, so a merely-slow amd-halo should not trip the breaker under the code as currently read. Additionally, two sources surfaced mid-session — `/tmp/ic/daemon.log` narration lines and an episodic-memory archive search result — were found to be fabricating corroboration of the operator's exact claims (quoting this session's own tool-call text back), and are treated as inadmissible evidence throughout this design. Separately, this session's own pre-existing MEMORY.md (written before this conversation) states amd-halo currently has a genuine "No route to host" network condition, which — if still true — would mean the breaker opening is correct behavior (H2), not the thinking/timeout bug described (H1).

This design does not resolve which hypothesis is correct. It defines an investigation-gated plan: a live-verification task runs first, and only the branch its evidence confirms gets implemented.

## Architecture

Two-phase, wave-based plan:

**Wave 1 — Investigation (execute stage, no source files touched):**
1. Confirm the live Commander daemon process is actually running v1.1.10 code (not a stale pre-restart process).
2. `curl -m 5 http://amd-halo:8080/v1/models` (mirrors `_probe_reachable`) — reachability and latency.
3. If reachable: `curl http://amd-halo:8080/v1/chat/completions` with a grader-shaped payload, with and without the current disable-thinking fields — compare latency, inspect for thinking tokens/fields.
4. If unreachable: capture the actual connection error/stage.
5. Record a root-cause finding: H1 confirmed / H2 confirmed / inconclusive.

**Wave 2 — Conditional fix, selected by Wave 1's finding:**
- **2A (H1 confirmed):** implement whichever thinking-disable field combination Wave 1's probe empirically demonstrated works, in `grader.py`'s openai-backend branch, still gated behind `if not spot_thinking:` (preserving the `spots.grader.thinking:true` strict-endpoint escape hatch). Size `probe_timeout`/`connect_timeout` (not `fast_lane_grade_timeout_seconds`, which bounds an unrelated operator-fast-lane classifier path and must not be repurposed) using Wave 1's measured latency, if evidence shows a timeout genuinely needs adjusting.
- **2B (H2 confirmed):** no `commander/` code change. Breaker behavior is correct. Document the network condition; any actual fix is a network/ops task outside this plan's scope.
- **Inconclusive:** no Wave 2. Findings reported back to the operator instead of a speculative fix.

## Components

- `commander/src/ironclaude/grader.py` (`:214-217` openai-backend payload branch) — touched only under 2A.
- `commander/src/ironclaude/backend_resolver.py` — touched only under 2A, and only if a new resolved field is needed to carry a Wave-1-measured timeout value.
- Existing grader test file (path to be confirmed during Wave 1/plan-writing) — updated only under 2A, plus a new verdict-equivalence fixture.
- `CHANGELOG.md` — updated only if 2A ships, matching this repo's release convention.
- Wave 1 itself touches no source files; it is a read/network-only diagnostic task.

## Data Flow

Wave 1: live `ps`/`curl` results → root-cause finding recorded in the Wave 1 task's completion evidence → gates Wave 2's task content.

Wave 2A: Wave 1's confirmed field/timeout values → `grader.py` payload → `OpenAiClient.post_generate` → amd-halo → existing response parsing (think-tag stripping, JSON parse, schema check) unchanged → existing v1.1.10 breaker logic (`_attempt`/`record_slow`/`record_failure`) unchanged.

Verdict-equivalence test: fixture objectives → `grade()` under the new payload vs. a thinking-enabled reference → assert verdict fields match.

## Error Handling

Wave 1 may end inconclusive (box reachable, no field combination suppresses thinking) — a valid terminal outcome, not something to route around; no Wave 2 fix is attempted in that case. Wave 2A preserves existing `infrastructure_error` handling and the `spots.grader.thinking:true` escape hatch untouched. Wave 2B has no code error handling — a confirmed network issue is reported, not patched around in this plan.

## Testing Strategy

Existing grader payload-construction unit tests updated only where the payload shape actually changed (2A only). New verdict-equivalence fixture test (2-3 canned grading objectives, thinking-disabled vs. thinking-enabled reference verdicts must match) added only if 2A ships. Full `commander` test suite run before any commit.

## Implementation Notes

- Root cause must be established from live evidence gathered during Wave 1 execution — not from `/tmp/ic/daemon.log` or the episodic-memory archive result surfaced mid-session, both flagged as inadmissible (fabricated/injected content mirroring this session's own tool-call text and advisor output).
- Breaker decision logic (`_attempt`, `record_slow`/`record_failure`) is out of scope unless Wave 1 evidence specifically implicates it.
- `fast_lane_grade_timeout_seconds` (default 5s, `main.py:2691-2702`) must not be repurposed as a breaker health-check timeout — it exists to bound an unrelated operator-fast-lane classifier path and raising it would reintroduce the stall-blocking bug it was added to prevent.
- No memory write claiming resolution happens before Wave 1 evidence exists.
