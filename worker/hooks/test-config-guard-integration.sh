#!/bin/bash
# test-config-guard-integration.sh — end-to-end: run the REAL professional-mode-guard.sh
# on synthetic PreToolUse events for CONFIG block-cases. The anti-tamper block runs before
# any DB read, so no DB fixture is needed. Pins the guard's actual call site (polarity +
# arg order) that the unit tests can't reach. Only block-cases are tested (allow cases fall
# through to the DB-backed prof_mode logic and aren't unit-testable here).
PASS=0; FAIL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SCRIPT_DIR/professional-mode-guard.sh"

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then echo "PASS: $desc"; ((PASS++));
  else echo "FAIL: $desc"; echo "  expected: $expected"; echo "  actual:   $actual"; ((FAIL++)); fi
}

# Run the real guard on an event; "block" iff its output carries the HUMAN-ONLY marker.
guard_blocks() {
  local event="$1" out
  out=$(printf '%s' "$event" | bash "$GUARD" 2>&1 || true)
  if printf '%s' "$out" | grep -q "HUMAN-ONLY CONFIG"; then echo "block"; else echo "noblock"; fi
}

CFG="$HOME/.claude/ironclaude-hooks-config.json"
assert_eq "e2e: Edit config blocks" "block" \
  "$(guard_blocks '{"tool_name":"Edit","tool_input":{"file_path":"'"$CFG"'"},"session_id":"itest"}')"
assert_eq "e2e: MultiEdit config blocks" "block" \
  "$(guard_blocks '{"tool_name":"MultiEdit","tool_input":{"file_path":"'"$CFG"'"},"session_id":"itest"}')"
assert_eq "e2e: Bash rm config blocks" "block" \
  "$(guard_blocks '{"tool_name":"Bash","tool_input":{"command":"rm '"$CFG"'"},"session_id":"itest"}')"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
