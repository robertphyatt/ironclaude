#!/bin/bash
# test-gbtw-continuation-suppression.sh — legacy wait signals remain available
# to deterministic gates, while continuation routing no longer uses them.
#
# Usage: bash worker/hooks/tests/test-gbtw-continuation-suppression.sh
#
# Full routing is exercised by test-gbtw-continuation-judge.sh.

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

# Minimal genuine-stall fixture: no in-flight job, no waiting tool.
STALL_FIXTURE="$(mktemp)"
NATIVE_WAIT_FIXTURE="$(mktemp)"
NATIVE_FAILED_WAIT_FIXTURE="$(mktemp)"
NATIVE_FAILED_SPAWN_FIXTURE="$(mktemp)"
NATIVE_TERMINAL_SPAWN_FIXTURE="$(mktemp)"
NATIVE_QUESTION_FIXTURE="$(mktemp)"
trap 'rm -f "$STALL_FIXTURE" "$NATIVE_WAIT_FIXTURE" "$NATIVE_FAILED_WAIT_FIXTURE" "$NATIVE_FAILED_SPAWN_FIXTURE" "$NATIVE_TERMINAL_SPAWN_FIXTURE" "$NATIVE_QUESTION_FIXTURE"' EXIT
cat > "$STALL_FIXTURE" << 'EOF'
{"type":"assistant","requestId":"req-stall-1","message":{"content":[{"type":"text","text":"Working on the analysis."}]}}
{"type":"assistant","requestId":"req-stall-2","message":{"content":[{"type":"text","text":"Still working, nothing dispatched."}]}}
EOF
cat > "$NATIVE_WAIT_FIXTURE" <<'EOF'
{"type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"native-wait","arguments":"{\"timeout_ms\":60000}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"native-wait","output":"{\"message\":\"Wait completed.\",\"timed_out\":false}"}}
EOF
cat > "$NATIVE_FAILED_WAIT_FIXTURE" <<'EOF'
{"type":"response_item","payload":{"type":"function_call","name":"wait_agent","call_id":"native-wait","arguments":"{\"timeout_ms\":60000}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"native-wait","output":"{\"error\":\"task unavailable\"}"}}
EOF
cat > "$NATIVE_FAILED_SPAWN_FIXTURE" <<'EOF'
{"type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"native-spawn","arguments":"{\"task_name\":\"work\"}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"native-spawn","output":"{\"task_name\":\"/root/work\",\"status\":\"failed\"}"}}
EOF
cat > "$NATIVE_TERMINAL_SPAWN_FIXTURE" <<'EOF'
{"type":"response_item","payload":{"type":"function_call","name":"spawn_agent","call_id":"native-spawn","arguments":"{\"task_name\":\"work\"}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"native-spawn","output":"{\"task_name\":\"/root/work\"}"}}
{"type":"event_msg","payload":{"type":"agent_message","message":"/root/work completed"}}
EOF
cat > "$NATIVE_QUESTION_FIXTURE" <<'EOF'
{"type":"response_item","payload":{"type":"function_call","name":"request_user_input_async","call_id":"question-1","arguments":"{\"questions\":[]}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"question-1","output":"{\"accepted\":true}"}}
EOF

echo "=== Wait evidence for deterministic gates ==="
assert_eq "live async Agent observed" "true" "$(_gbtw_review_gate_suppress "$FIXTURES_DIR/f1-subagent-in-flight.jsonl")"
assert_eq "live background Bash observed" "true" "$(_gbtw_review_gate_suppress "$FIXTURES_DIR/f5-bash-bg-in-flight.jsonl")"
assert_eq "recent Monitor observed" "true" "$(_gbtw_review_gate_suppress "$FIXTURES_DIR/w1-monitor-last-turn.jsonl")"
assert_eq "completed subagent no longer live" "" "$(_gbtw_review_gate_suppress "$FIXTURES_DIR/f2-subagent-completed.jsonl")"
assert_eq "all terminal subagents no longer live" "" "$(_gbtw_review_gate_suppress "$FIXTURES_DIR/f7-all-terminal-statuses.jsonl")"
assert_eq "no job or wait tool" "" "$(_gbtw_review_gate_suppress "$STALL_FIXTURE")"
assert_eq "native Codex wait_agent with result" "true" "$(_gbtw_review_gate_suppress "$NATIVE_WAIT_FIXTURE")"
assert_eq "failed native wait is not wait evidence" "" "$(_gbtw_review_gate_suppress "$NATIVE_FAILED_WAIT_FIXTURE")"
assert_eq "failed native spawn is not live evidence" "" "$(_gbtw_review_gate_suppress "$NATIVE_FAILED_SPAWN_FIXTURE")"
assert_eq "completed native spawn is not live evidence" "" "$(_gbtw_review_gate_suppress "$NATIVE_TERMINAL_SPAWN_FIXTURE")"
assert_eq "native pending question is wait evidence" "true" "$(_gbtw_review_gate_suppress "$NATIVE_QUESTION_FIXTURE")"

echo "=== Continuation routing ==="
_wire_calls="$(grep -cF '_gbtw_continuation_suppressed_by_inflight "$TRANSCRIPT_PATH"' "$IMPL" || true)"
assert_eq "wait signals do not suppress continuation" "0" "$_wire_calls"
_bg_active="$(grep -cF '_BG_JOB_ACTIVE' "$IMPL")"
assert_eq "old completion-blind _BG_JOB_ACTIVE block removed" "0" "$_bg_active"

echo
echo "results: $pass pass, $fail fail"
[ "$fail" -eq 0 ] || exit 1
