#!/bin/bash
# test-pmguard-client-aware.sh
# Verifies professional-mode-guard.sh guidance text uses the client-aware
# ic_skill_ref helper (Skill tool under Claude, $ironclaude:<skill> under
# Codex) instead of hardcoded "call the Skill tool with: skill: ..." prose
# or a literal Skill(skill="...", args="...") string.
#
# This test is purely textual (grep on the hook source) — it does not source
# or execute the hook.

GUARD="/Users/roberthyatt/Code/ironclaude/worker/hooks/professional-mode-guard.sh"

pass=0
fail=0

check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $desc (expected=$expected actual=$actual)"
    pass=$((pass + 1))
  else
    echo "FAIL: $desc (expected=$expected actual=$actual)"
    fail=$((fail + 1))
  fi
}

# (1) No remaining "Skill tool" prose, other than the benign log_hook label
#     ("skill tool" as a log_hook decision-reason string, not user guidance).
count1=$(grep -iF 'skill tool' "$GUARD" | grep -vF 'log_hook' | wc -l | tr -d ' ')
check "no hardcoded 'Skill tool' guidance prose" "0" "$count1"

# (2) No literal Skill(skill="...", args="...") strings left in the guard —
#     that Claude-shaped literal must come only from ic_skill_ref at runtime.
count2=$(grep -cF 'Skill(skill=' "$GUARD")
check "no literal Skill(skill=...) string" "0" "$count2"

# (3) Exact per-skill ic_skill_ref call counts.
count_activate=$(grep -cF -- 'ic_skill_ref "ironclaude:activate-professional-mode"' "$GUARD")
check "ic_skill_ref activate-professional-mode count" "2" "$count_activate"

count_brainstorming=$(grep -cF -- 'ic_skill_ref "ironclaude:brainstorming"' "$GUARD")
check "ic_skill_ref brainstorming count" "5" "$count_brainstorming"

count_writing_plans=$(grep -cF -- 'ic_skill_ref "ironclaude:writing-plans"' "$GUARD")
check "ic_skill_ref writing-plans count" "3" "$count_writing_plans"

count_executing_plans=$(grep -cF -- 'ic_skill_ref "ironclaude:executing-plans"' "$GUARD")
check "ic_skill_ref executing-plans count" "2" "$count_executing_plans"

count_code_review=$(grep -cF -- 'ic_skill_ref "ironclaude:code-review" "--task-boundary"' "$GUARD")
check "ic_skill_ref code-review --task-boundary count" "1" "$count_code_review"

# (4) No dangling `skill: "..."` guidance lines remain (leftover fragments
#     from the old multi-line "Call the Skill tool with: / skill: ..." form).
count4=$(grep -cE '^[[:space:]]*skill: ' "$GUARD")
check "no dangling skill: guidance lines" "0" "$count4"

echo "---"
echo "pass=$pass fail=$fail"

[ "$fail" -eq 0 ]
