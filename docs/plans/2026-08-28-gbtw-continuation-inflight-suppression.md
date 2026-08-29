# GBTW Continuation In-Flight Suppression (G1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the GBTW Stop hook's continuation check honor a live in-flight Agent subagent (backlog G1), using the same completion-aware signal the tasks-in-progress block and code-review gate already use, without ever false-silencing a genuine stall.

**Requirements:** docs/plans/2026-08-28-gbtw-continuation-inflight-suppression-requirements.md

**Design:** docs/plans/2026-08-28-gbtw-continuation-inflight-suppression-design.md

**Architecture:** Extract the continuation-suppression decision into a small pure helper `_gbtw_continuation_suppressed_by_inflight()` = `_gbtw_review_gate_suppress` (completion-aware: `_gbtw_extract_in_flight` OR `_gbtw_recent_waiting_tool`) true AND the anti-pattern re-arm not triggered. Replace the ad-hoc `_BG_JOB_ACTIVE` stanza with a call to it. The helper's decision logic is unit-tested via the `GBTW_TEST_MODE=1` sourcing seam; the *wiring* is guarded by a structural two-grep falsifier so reverting the stanza fails a test.

**Tech Stack:** Bash hook (`worker/hooks/get-back-to-work-impl.sh`, invoked via the `get-back-to-work-claude.sh` wrapper), jq, the `GBTW_TEST_MODE` predicate-test seam + shared `tests/fixtures/`.

**Execution invariants:** shell state does NOT persist between steps (literal absolute paths); Bash cwd is `commander/` (use absolute paths); a presence guard asserts exact strings; every `expected:` for a NEW test is RED-first (measured at execution).

---

## Task 1: Wire completion-aware in-flight detection into the continuation suppression

**Files:**
- Modify: `worker/hooks/get-back-to-work-impl.sh` (add `_gbtw_continuation_suppressed_by_inflight` near the other `_gbtw_*` helpers, before the `GBTW_TEST_MODE` shim at :228-232; replace the `_BG_JOB_ACTIVE` stanza at :924-950)
- Create/Test: `worker/hooks/tests/test-gbtw-continuation-suppression.sh` (in `tests/`, alongside the sibling GBTW tests, reusing `tests/fixtures/`)

**Step 1 (RED):** Create `worker/hooks/tests/test-gbtw-continuation-suppression.sh`, modeled on `tests/test-gbtw-inflight.sh`: resolve `SCRIPT_DIR`, `HOOKS_DIR="$SCRIPT_DIR/.."`, `FIXTURES_DIR="$SCRIPT_DIR/fixtures"`; `GBTW_TEST_MODE=1 source "$HOOKS_DIR/get-back-to-work-claude.sh"` (the wrapper delegates to the impl and exposes the `_gbtw_*` helpers).

**Part 1 — behavioral** (`assert_eq` over `_gbtw_continuation_suppressed_by_inflight <fixture> <stage> <context>`, reusing shared fixtures):
- `f1-subagent-in-flight.jsonl`, `executing`, `` → `true` (the async_launched case that graded D)
- `f5-bash-bg-in-flight.jsonl`, `executing`, `` → `true`
- `w1-monitor-last-turn.jsonl`, `executing`, `` → `true` (named waiting tool, R3)
- `f2-subagent-completed.jsonl`, `executing`, `` → `` (S1 — a completed subagent still fires the check)
- `f7-all-terminal-statuses.jsonl`, `executing`, `` → `` (killed/failed/stopped)
- a minimal inline STALL fixture (2 plain assistant turns, no tool, no job) → `` (genuine stall still nags)
- `f1-subagent-in-flight.jsonl`, `executing`, `<a checkpoint/query-offload proposal string _ic_is_antipattern_proposal recognizes — verify against tests/test-antipattern-lexicon.sh>` → `` (re-armed, R4)

**Part 2 — structural wiring falsifier** (the fix's regression guard):
- `grep -c '_gbtw_continuation_suppressed_by_inflight "$TRANSCRIPT_PATH"' "$HOOKS_DIR/get-back-to-work-impl.sh"` must equal **1** (the runtime call-site; textually distinct from the definition, which uses `$1`/`$2`/`$3`)
- `grep -c '_BG_JOB_ACTIVE' "$HOOKS_DIR/get-back-to-work-impl.sh"` must equal **0** (the removed completion-blind block)

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-suppression.sh
```
Expected: FAIL — helper undefined (behavioral cases fail); call-site grep == 0 (not wired); `_BG_JOB_ACTIVE` grep == 3 (old block present).

**Step 2 (GREEN — helper):** In `get-back-to-work-impl.sh`, after `_gbtw_should_rearm_check` (~:140) / near `_gbtw_review_gate_suppress` (:215-226) and BEFORE the `GBTW_TEST_MODE` shim (:228):
```bash
_gbtw_continuation_suppressed_by_inflight() {
    local transcript="$1" stage="$2" context="$3"
    [ "$(_gbtw_review_gate_suppress "$transcript")" = "true" ] || return 0
    if [ "$(_gbtw_should_rearm_check "$stage" "$context")" = "true" ]; then
        return 0
    fi
    printf 'true'
    return 0
}
```

**Step 3 (GREEN — wire):** Replace the `_BG_JOB_ACTIVE` stanza (:924-950, incl. its inner re-arm) with:
```bash
if [ "$FIRE_CONTINUATION" = "true" ] \
   && [ "$(_gbtw_continuation_suppressed_by_inflight "$TRANSCRIPT_PATH" "$WORKFLOW_STAGE" "$RECENT_CONTEXT")" = "true" ]; then
    FIRE_CONTINUATION="false"
    log_hook "GET-BACK-TO-WORK" "Suppressed" "continuation check — live in-flight job / waiting tool (completion-aware)"
fi
```
Leave the holding/waiting last-line stanza (:958-968) unchanged. This removes every `_BG_JOB_ACTIVE` occurrence.

Expected: reverting THIS wiring flips the structural assertions — call-site grep 1→0 and `_BG_JOB_ACTIVE` reappears (0→3). The behavioral cases stay green either way (they exercise the helper directly), so the two greps are what guard the wiring.

**Step 4:** Run the new test (behavioral + structural all PASS); then the bg-detection smoke check and syntax check:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-continuation-suppression.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-bg-detection.sh
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/get-back-to-work-impl.sh
```
Expected: new test all PASS (incl. call-site grep 1, `_BG_JOB_ACTIVE` 0); `test-bg-detection.sh` all PASS (a self-contained replica — a no-harm smoke check, NOT wiring coverage); `bash -n` exit 0.

**Step 5:** Stage:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/get-back-to-work-impl.sh worker/hooks/tests/test-gbtw-continuation-suppression.sh
```
