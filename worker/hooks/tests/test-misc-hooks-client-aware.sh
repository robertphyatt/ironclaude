#!/bin/bash
# test-misc-hooks-client-aware.sh
# Verifies plan-task-context.sh uses the client-aware ic_skill_ref helper
# for human-readable skill-invocation guidance text, instead of hardcoding
# Claude-specific "Skill tool" wording. Also guards that the inert
# topic-change-detector hook (its systemMessage output never reached the
# model) stays fully removed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PT="$SCRIPT_DIR/../plan-task-context.sh"
HOOKS_JSON="$SCRIPT_DIR/../hooks.json"

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

# (1) topic-change-detector.sh no longer exists (inert hook removed)
[ ! -e "$SCRIPT_DIR/../topic-change-detector.sh" ]
check "topic-change-detector.sh does not exist" "$?"

# (2) hooks.json no longer references topic-change-detector
count=$(grep -cF 'topic-change-detector' "$HOOKS_JSON")
[ "$count" = "0" ]
check "hooks.json no longer references topic-change-detector" "$?"

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
