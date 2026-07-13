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
