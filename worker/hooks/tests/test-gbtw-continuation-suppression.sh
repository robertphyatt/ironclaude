#!/bin/bash
# test-gbtw-continuation-suppression.sh — tests the GBTW continuation-check
# in-flight suppression decision (_gbtw_continuation_suppressed_by_inflight)
# AND a structural falsifier for the stanza wiring (backlog G1 fix).
#
# Usage: bash worker/hooks/tests/test-gbtw-continuation-suppression.sh
#
# Strategy: source get-back-to-work-claude.sh with GBTW_TEST_MODE=1 (the wrapper
# delegates to get-back-to-work-impl.sh, whose shim exposes the _gbtw_* helpers
# and returns), then exercise the decision helper over shared fixtures. PART 2
# greps the impl so that reverting the stanza wiring (which the behavioral cases
# alone cannot catch — the helper is defined regardless) fails a test.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
IMPL="$HOOKS_DIR/get-back-to-work-impl.sh"
HOOK_SCRIPT="$HOOKS_DIR/get-back-to-work-claude.sh"

if [ ! -f "$HOOK_SCRIPT" ]; then
    echo "FATAL: hook script not found: $HOOK_SCRIPT" >&2
    exit 2
fi
# shellcheck disable=SC1090
GBTW_TEST_MODE=1 source "$HOOK_SCRIPT"

pass=0
fail=0

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "PASS: $desc"
        pass=$((pass + 1))
    else
        echo "FAIL: $desc"
        echo "  expected: [$expected]"
        echo "  actual:   [$actual]"
        fail=$((fail + 1))
    fi
}

# Wrapper: returns a sentinel when the helper is undefined (RED baseline), so the
# empty-expected cases do not spuriously pass before Task 2 lands.
_susp() {
    if type _gbtw_continuation_suppressed_by_inflight &>/dev/null; then
        _gbtw_continuation_suppressed_by_inflight "$1" "$2" "$3"
    else
        printf '__UNDEFINED__'
    fi
}

# Minimal genuine-stall fixture: no in-flight job, no waiting tool.
STALL_FIXTURE="$(mktemp)"
trap 'rm -f "$STALL_FIXTURE"' EXIT
cat > "$STALL_FIXTURE" << 'EOF'
{"type":"assistant","requestId":"req-stall-1","message":{"content":[{"type":"text","text":"Working on the analysis."}]}}
{"type":"assistant","requestId":"req-stall-2","message":{"content":[{"type":"text","text":"Still working, nothing dispatched."}]}}
EOF

echo "=== Behavioral: the suppression decision (completion-aware) ==="
assert_eq "live async_launched Agent -> suppress" \
    "true" "$(_susp "$FIXTURES_DIR/f1-subagent-in-flight.jsonl" executing "")"
assert_eq "live run_in_background Bash -> suppress" \
    "true" "$(_susp "$FIXTURES_DIR/f5-bash-bg-in-flight.jsonl" executing "")"
assert_eq "recent Monitor waiting tool -> suppress" \
    "true" "$(_susp "$FIXTURES_DIR/w1-monitor-last-turn.jsonl" executing "")"
assert_eq "completed subagent -> NOT suppressed (S1 no false-silence)" \
    "" "$(_susp "$FIXTURES_DIR/f2-subagent-completed.jsonl" executing "")"
assert_eq "all-terminal subagents -> NOT suppressed" \
    "" "$(_susp "$FIXTURES_DIR/f7-all-terminal-statuses.jsonl" executing "")"
assert_eq "genuine stall (no job, no tool) -> NOT suppressed" \
    "" "$(_susp "$STALL_FIXTURE" executing "")"
assert_eq "live Agent + checkpoint proposal -> re-armed (NOT suppressed)" \
    "" "$(_susp "$FIXTURES_DIR/f1-subagent-in-flight.jsonl" executing "let me find a safe stopping point")"

echo "=== Structural: the stanza wiring (reverting Step 3 fails these) ==="
_wire_calls="$(grep -cF '_gbtw_continuation_suppressed_by_inflight "$TRANSCRIPT_PATH"' "$IMPL")"
assert_eq "continuation stanza calls the helper (call-site present)" "1" "$_wire_calls"
_bg_active="$(grep -cF '_BG_JOB_ACTIVE' "$IMPL")"
assert_eq "old completion-blind _BG_JOB_ACTIVE block removed" "0" "$_bg_active"

echo
echo "results: $pass pass, $fail fail"
[ "$fail" -eq 0 ] || exit 1
