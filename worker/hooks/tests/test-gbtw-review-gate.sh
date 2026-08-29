#!/bin/bash
# test-gbtw-review-gate.sh — asserts G2: _gbtw_review_gate_suppress reports a
# legitimate wait (in-flight bg job OR waiting tool) so the CODE-REVIEW-REQUIRED
# gate can defer instead of nagging. Reuses existing fixtures.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIX="$SCRIPT_DIR/fixtures"
GBTW_TEST_MODE=1 source "$HOOKS_DIR/get-back-to-work-claude.sh"
if ! type _gbtw_review_gate_suppress &>/dev/null; then
    echo "FATAL: _gbtw_review_gate_suppress not defined (Task not landed — RED baseline)" >&2
    _gbtw_review_gate_suppress() { echo ""; }
fi
pass=0; fail=0
assert_gate() { # label expected fixture
    local got; got="$(_gbtw_review_gate_suppress "$3" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ "$got" = "$2" ]; then echo "PASS $1 gate=[$got]"; pass=$((pass+1));
    else echo "FAIL $1 expected=[$2] got=[$got]"; fail=$((fail+1)); fi
}
assert_gate "g1 subagent-in-flight" "true" "$FIX/f1-subagent-in-flight.jsonl"
assert_gate "g2 waiting-monitor"    "true" "$FIX/w1-monitor-last-turn.jsonl"
assert_gate "g3 subagent-completed" ""     "$FIX/f2-subagent-completed.jsonl"
assert_gate "g4 no-wait"            ""     "$FIX/w4-no-waiting-tool.jsonl"
echo "---- review-gate: $pass passed, $fail failed ----"
[ "$fail" -eq 0 ]
