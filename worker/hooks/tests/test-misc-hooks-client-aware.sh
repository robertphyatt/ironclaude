#!/bin/bash
# test-misc-hooks-client-aware.sh
# Verifies topic-change-detector.sh and plan-task-context.sh use the
# client-aware ic_skill_ref helper for human-readable skill-invocation
# guidance text, instead of hardcoding Claude-specific "Skill tool" wording.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TC="$SCRIPT_DIR/../topic-change-detector.sh"
PT="$SCRIPT_DIR/../plan-task-context.sh"

pass=0
fail=0

check() {
  local desc="$1"
  local result="$2"
  if [ "$result" = "0" ]; then
    echo "PASS: $desc"
    pass=$((pass + 1))
  else
    echo "FAIL: $desc"
    fail=$((fail + 1))
  fi
}

# (1) topic-change-detector.sh calls ic_skill_ref for plan-interruption
grep -qF 'ic_skill_ref "ironclaude:plan-interruption"' "$TC"
check "topic-change-detector.sh calls ic_skill_ref for plan-interruption" "$?"

# (2) old hardcoded literal is gone from topic-change-detector.sh
count=$(grep -cF 'Call the Skill tool with skill: ironclaude:plan-interruption' "$TC")
[ "$count" = "0" ]
check "topic-change-detector.sh no longer hardcodes 'Call the Skill tool with skill: ironclaude:plan-interruption'" "$?"

# (3) plan-task-context.sh calls ic_skill_ref for code-review --task-boundary
grep -qF 'ic_skill_ref "ironclaude:code-review" "--task-boundary"' "$PT"
check "plan-task-context.sh calls ic_skill_ref for code-review --task-boundary" "$?"

# (4) old hardcoded phrasing is gone from plan-task-context.sh
count=$(grep -cF 'Call the Skill tool' "$PT")
[ "$count" = "0" ]
check "plan-task-context.sh no longer hardcodes 'Call the Skill tool'" "$?"

echo ""
echo "Results: $pass passed, $fail failed"

[ "$fail" -eq 0 ]
