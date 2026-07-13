# GBTW Monitor-Aware Tasks-In-Progress Gate — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Stop the GBTW hook from blocking a worker with "TASKS STILL IN PROGRESS" when it is legitimately waiting on a persistent `Monitor` (or other waiting tool), by adding a `_gbtw_recent_waiting_tool` helper and wiring it into the hard tasks-in-progress gate.

**Architecture:** New helper `_gbtw_recent_waiting_tool` in `worker/hooks/get-back-to-work-claude.sh` (defined above the `GBTW_TEST_MODE` early-return), detecting Monitor/TaskOutput/ScheduleWakeup/AskUserQuestion in the last 3 assistant turns. The tasks-in-progress gate consults it after the existing completion-aware `_gbtw_extract_in_flight` check and before `block_stop` → approves the stop when a waiting tool is active (gate = classifier OR helper). **Scope note (gate-only):** the continuation check is intentionally left untouched — it already handles Monitor correctly, so rewiring it would change a working check with no upside. Fail-safe: on any error the helper reports not-found (gate blocks). Tested via the `GBTW_TEST_MODE` fixture harness.

**Tech Stack:** Bash hook script, jq, fixture-driven bash tests (`GBTW_TEST_MODE=1 source`).

> **Deploy is an operator step (like `git push`).** After these tasks stage the change, the operator commits and runs `make deploy-hooks` to copy the hook into `~/.claude/ironclaude-hooks/` (un-sticking the live worker). Deployment is kept out of the plan tasks to avoid a plan step writing outside the repo under the execution-stage file guard.

---

## Task 1: RED — waiting-tool test harness + fixtures

**Files:**
- Create: `worker/hooks/tests/test-gbtw-waiting.sh`
- Create: `worker/hooks/tests/fixtures/w1-monitor-last-turn.jsonl`
- Create: `worker/hooks/tests/fixtures/w2-monitor-within-window.jsonl`
- Create: `worker/hooks/tests/fixtures/w3-monitor-outside-window.jsonl`
- Create: `worker/hooks/tests/fixtures/w4-no-waiting-tool.jsonl`
- Create: `worker/hooks/tests/fixtures/w5-schedulewakeup.jsonl`
- Create: `worker/hooks/tests/fixtures/w6-bash-bg.jsonl`
- Create: `worker/hooks/tests/fixtures/w7-askuserquestion.jsonl`
- Create: `worker/hooks/tests/fixtures/w8-malformed-line.jsonl`

**Step 1: Create the 8 fixtures.** Each is a JSONL transcript (one JSON object per line).

`w1-monitor-last-turn.jsonl`:
```
{"type":"assistant","requestId":"w1r1","message":{"content":[{"type":"text","text":"starting the suite"}]}}
{"type":"assistant","requestId":"w1r2","message":{"content":[{"type":"tool_use","name":"Monitor","input":{"description":"watch test-all suite"}}]}}
```

`w2-monitor-within-window.jsonl` (Monitor 2 turns back, still in the last-3 window):
```
{"type":"assistant","requestId":"w2r1","message":{"content":[{"type":"tool_use","name":"Monitor","input":{"description":"watch suite"}}]}}
{"type":"assistant","requestId":"w2r2","message":{"content":[{"type":"text","text":"monitor armed"}]}}
{"type":"assistant","requestId":"w2r3","message":{"content":[{"type":"text","text":"waiting"}]}}
```

`w3-monitor-outside-window.jsonl` (Monitor is the 4th-from-last assistant turn → outside last-3):
```
{"type":"assistant","requestId":"w3m","message":{"content":[{"type":"tool_use","name":"Monitor","input":{}}]}}
{"type":"assistant","requestId":"w3t1","message":{"content":[{"type":"text","text":"a"}]}}
{"type":"assistant","requestId":"w3t2","message":{"content":[{"type":"text","text":"b"}]}}
{"type":"assistant","requestId":"w3t3","message":{"content":[{"type":"text","text":"c"}]}}
```

`w4-no-waiting-tool.jsonl`:
```
{"type":"assistant","requestId":"w4r1","message":{"content":[{"type":"text","text":"thinking"}]}}
{"type":"assistant","requestId":"w4r2","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/x"}}]}}
```

`w5-schedulewakeup.jsonl`:
```
{"type":"assistant","requestId":"w5r1","message":{"content":[{"type":"tool_use","name":"ScheduleWakeup","input":{"delaySeconds":1200}}]}}
```

`w6-bash-bg.jsonl`:
```
{"type":"assistant","requestId":"w6r1","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"sleep 999","run_in_background":true}}]}}
```

`w7-askuserquestion.jsonl`:
```
{"type":"assistant","requestId":"w7r1","message":{"content":[{"type":"tool_use","name":"AskUserQuestion","input":{"questions":[]}}]}}
```

`w8-malformed-line.jsonl` (malformed line adjacent to a valid Monitor turn — must not crash):
```
{"type":"assistant","requestId":"w8v","message":{"content":[{"type":"tool_use","name":"Monitor","input":{}}]}}
{ this is not valid json at all
```

**Step 2: Create the test harness** `worker/hooks/tests/test-gbtw-waiting.sh`:
```bash
#!/bin/bash
# test-gbtw-waiting.sh — fixture-driven tests for the GBTW hook's
# recent-waiting-tool detector (_gbtw_recent_waiting_tool).
#
# Usage: bash worker/hooks/tests/test-gbtw-waiting.sh
#
# Strategy: source get-back-to-work-claude.sh with GBTW_TEST_MODE=1 (exposes
# helper definitions only), then call _gbtw_recent_waiting_tool <fixture> and
# compare its trimmed stdout ("true" or "") against the expected value.
#
# Exits 0 iff all cases pass.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
HOOK_SCRIPT="$HOOKS_DIR/get-back-to-work-claude.sh"

if [ ! -f "$HOOK_SCRIPT" ]; then
    echo "FATAL: hook script not found: $HOOK_SCRIPT" >&2
    exit 2
fi
# shellcheck disable=SC1090
GBTW_TEST_MODE=1 source "$HOOK_SCRIPT"

if ! type _gbtw_recent_waiting_tool &>/dev/null; then
    echo "FATAL: _gbtw_recent_waiting_tool not defined after sourcing hook (Task 2 not landed yet — RED baseline)" >&2
    _gbtw_recent_waiting_tool() { echo ""; }
fi

pass=0
fail=0

assert_waiting() {
    local case="$1"
    local expected="$2"   # "true" or ""
    local fixture="$3"
    local got
    got="$(_gbtw_recent_waiting_tool "$fixture" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ "$got" = "$expected" ]; then
        echo "PASS $case  waiting=[$got]"
        pass=$((pass + 1))
    else
        echo "FAIL $case  expected=[$expected]  got=[$got]"
        fail=$((fail + 1))
    fi
}

assert_waiting "w1 monitor-last-turn"      "true" "$FIXTURES_DIR/w1-monitor-last-turn.jsonl"
assert_waiting "w2 monitor-within-window"  "true" "$FIXTURES_DIR/w2-monitor-within-window.jsonl"
assert_waiting "w3 monitor-outside-window" ""     "$FIXTURES_DIR/w3-monitor-outside-window.jsonl"
assert_waiting "w4 no-waiting-tool"        ""     "$FIXTURES_DIR/w4-no-waiting-tool.jsonl"
assert_waiting "w5 schedulewakeup"         "true" "$FIXTURES_DIR/w5-schedulewakeup.jsonl"
# w6: run_in_background is the completion-aware classifier's job, NOT this helper's —
# the helper must NOT match it (regression witness for the completion-blind-hole fix).
assert_waiting "w6 bash-bg-not-matched"    ""     "$FIXTURES_DIR/w6-bash-bg.jsonl"
assert_waiting "w7 askuserquestion"        "true" "$FIXTURES_DIR/w7-askuserquestion.jsonl"
assert_waiting "w8 malformed-line"         "true" "$FIXTURES_DIR/w8-malformed-line.jsonl"

echo
echo "results: $pass pass, $fail fail"
[ "$fail" -eq 0 ] || exit 1
```

**Step 3: Run — expect RED.**
```bash
bash worker/hooks/tests/test-gbtw-waiting.sh
```
Expected: FATAL note that `_gbtw_recent_waiting_tool` is undefined, then the "expect true" cases (w1, w2, w5, w7, w8 — 5 cases) FAIL because the stub returns empty; the "expect empty" cases (w3, w4, w6 — 3 cases) PASS against the stub. Nonzero exit. So: `results: 3 pass, 5 fail`.

**Step 4: Stage.**
```bash
git add worker/hooks/tests/test-gbtw-waiting.sh worker/hooks/tests/fixtures/w1-monitor-last-turn.jsonl worker/hooks/tests/fixtures/w2-monitor-within-window.jsonl worker/hooks/tests/fixtures/w3-monitor-outside-window.jsonl worker/hooks/tests/fixtures/w4-no-waiting-tool.jsonl worker/hooks/tests/fixtures/w5-schedulewakeup.jsonl worker/hooks/tests/fixtures/w6-bash-bg.jsonl worker/hooks/tests/fixtures/w7-askuserquestion.jsonl worker/hooks/tests/fixtures/w8-malformed-line.jsonl
```

---

## Task 2: GREEN — add the `_gbtw_recent_waiting_tool` helper

**Files:**
- Modify: `worker/hooks/get-back-to-work-claude.sh`

**Step 1: Add the helper** immediately after the `_gbtw_extract_in_flight` function definition (which ends with its closing `}` just before the `# Test-mode shim:` comment) and BEFORE the `if [ "${GBTW_TEST_MODE:-0}" = "1" ]; then` block, so it is exposed when the hook is sourced in test mode:
```bash
# Print "true" (to stdout) iff any of the last 3 assistant turns used a *waiting*
# tool — Monitor / TaskOutput / ScheduleWakeup / AskUserQuestion. Empty otherwise.
# NOTE: run_in_background jobs are deliberately NOT matched here — those are already
# tracked *completion-aware* by _gbtw_extract_in_flight (launched-minus-completed), so
# matching them here (which is completion-BLIND, last-3-turns) would leave the gate
# suppressed for up to 3 turns after a bg job has finished. This helper covers only the
# persistent/waiting tools the classifier does not track. Callers OR the two signals.
# Never crashes; on any error prints empty (fail-safe: absence of the signal -> caller
# blocks / the continuation check fires).
_gbtw_recent_waiting_tool() {
    local transcript="$1"
    if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
        return 0
    fi
    if ! command -v jq &>/dev/null; then
        return 0
    fi
    local recent_ids _req_id
    recent_ids=$(tail -n 500 "$transcript" 2>/dev/null | \
        jq -r 'select(.type == "assistant") | .requestId // empty' 2>/dev/null | tail -3)
    [ -z "$recent_ids" ] && return 0
    while IFS= read -r _req_id; do
        [ -z "$_req_id" ] && continue
        if tail -n 500 "$transcript" 2>/dev/null | grep -F "\"$_req_id\"" | \
           jq -r '.message.content[]? | select(.type == "tool_use") | if .name == "Monitor" or .name == "TaskOutput" or .name == "ScheduleWakeup" or .name == "AskUserQuestion" then "true" else "false" end' \
           2>/dev/null | grep -q "^true$" 2>/dev/null; then
            printf 'true'
            return 0
        fi
    done <<< "$recent_ids"
    return 0
}
```

**Step 2: Run the new test — expect GREEN.**
```bash
bash worker/hooks/tests/test-gbtw-waiting.sh
```
Expected: `results: 8 pass, 0 fail`.

**Step 3: Run the classifier test — must stay GREEN (helper addition must not disturb it).**
```bash
bash worker/hooks/tests/test-gbtw-inflight.sh
```
Expected: `results: 10 pass, 0 fail`.

**Step 4: Syntax check.**
```bash
bash -n worker/hooks/get-back-to-work-claude.sh
```
Expected: no output, exit 0.

**Step 5: Stage.**
```bash
git add worker/hooks/get-back-to-work-claude.sh
```

---

## Task 3: WIRE — waiting-tool suppression into the hard gate (gate-only)

**Files:**
- Modify: `worker/hooks/get-back-to-work-claude.sh`

**No unit test for the wiring itself:** the gate approve-path runs in the hook's **main
body** (top-level script, not a function), so it is not reachable through the
`GBTW_TEST_MODE` function-sourcing seam without refactoring the gate into a function —
out of scope under `hold`, and no full-hook integration harness exists in this repo (the
established pattern tests the classifier *functions* in isolation). The behavior-bearing
unit — `_gbtw_recent_waiting_tool` — is fully unit-tested in Task 2; the wiring is verified
here by `bash -n` (syntax), call-site `grep`, and both fixture suites staying green
(proving neither the helper nor the classifier regressed).

**Scope: gate-only.** The continuation check is left **untouched** — it already suppresses
itself on a recent Monitor/waiting tool, so it is not part of this bug and rewiring it
would change a working check with no upside (see the design's gate-only scope note). Only
the hard tasks-in-progress gate is modified.

**Step 1: Add the waiting-tool suppression to the hard gate.** In the `elif [ "${IN_PROGRESS_OR_PENDING_COUNT:-0}" != "0" ]` branch, AFTER the existing in-flight suppression block (the `if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then … _gbtw_extract_in_flight … fi`) and BEFORE the `# Tasks still in progress or pending -- block` / `increment_block_counter` lines, insert:
```bash
        # A persistent Monitor (or ScheduleWakeup/TaskOutput/AskUserQuestion) means the
        # worker is legitimately WAITING, not stalled. The _gbtw_extract_in_flight check
        # above already covers live (completion-aware) background Agent/Bash jobs; this
        # helper covers the waiting *tools* it does not track. Consult it before blocking.
        if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ] \
           && [ "$(_gbtw_recent_waiting_tool "$TRANSCRIPT_PATH")" = "true" ]; then
            log_hook "GET-BACK-TO-WORK" "Passed" "waiting tool active (Monitor/ScheduleWakeup/TaskOutput/AskUserQuestion)"
            echo '{"decision": "approve", "reason": "Waiting tool active (Monitor/ScheduleWakeup/...)", "systemMessage": "[GET-BACK-TO-WORK]: Passed - waiting tool active (Monitor/ScheduleWakeup/...)"}'
            exit 0
        fi
```

**Do NOT modify the continuation-suppression block** (`if [ "$FIRE_CONTINUATION" = "true" ]; then … _BG_RECENT_REQ_IDS … fi`) or the holding/waiting text matcher — both stay exactly as they are.

**Step 2: Syntax check.**
```bash
bash -n worker/hooks/get-back-to-work-claude.sh
```
Expected: no output, exit 0.

**Step 3: Verify the gate calls the helper and the definition exists.** (Call sites counted by the invocation pattern `_gbtw_recent_waiting_tool "…"` — comment-proof, since comments referencing the name have no trailing `"`.)
```bash
grep -c '_gbtw_recent_waiting_tool "' worker/hooks/get-back-to-work-claude.sh
```
Expected: `1` (the single gate call — continuation is untouched).
```bash
grep -c '_gbtw_recent_waiting_tool()' worker/hooks/get-back-to-work-claude.sh
```
Expected: `1` (the definition).

**Step 4: Confirm the continuation block is untouched** (its inline scan must still be present — gate-only scope). Target the assignment line specifically (the bare name `_BG_RECENT_REQ_IDS` appears on 3 lines — assignment, `if -n`, and the `<<<` here-string — so a bare-name count is 3; the assignment `_BG_RECENT_REQ_IDS=` is unique):
```bash
grep -c "_BG_RECENT_REQ_IDS=" worker/hooks/get-back-to-work-claude.sh
```
Expected: `1` (the continuation scan's assignment is intentionally left in place).

**Step 5: Re-run BOTH fixture suites — must stay GREEN (wiring must not disturb the helper or classifier).**
```bash
bash worker/hooks/tests/test-gbtw-waiting.sh
```
Expected: `results: 8 pass, 0 fail`.
```bash
bash worker/hooks/tests/test-gbtw-inflight.sh
```
Expected: `results: 10 pass, 0 fail`.

**Step 6: Stage.**
```bash
git add worker/hooks/get-back-to-work-claude.sh
```

---

## Deploy (operator step — not a plan task)

After the operator commits, run `make deploy-hooks` to copy the updated hook into
`~/.claude/ironclaude-hooks/` (and the plugin cache if present), which un-sticks the live
worker. Verify the deployed copy:
```bash
grep -c '_gbtw_recent_waiting_tool "' "$HOME/.claude/ironclaude-hooks/get-back-to-work-claude.sh"   # expect 1
bash -n "$HOME/.claude/ironclaude-hooks/get-back-to-work-claude.sh"                                  # expect exit 0
```
Deploy is kept out of the plan tasks (like `git push`) so no plan step writes outside the
repo under the execution-stage file guard.
