#!/bin/bash
# test-gbtw-block-tone.sh — asserts the anti-context-anxiety retone:
# the six execution-lifecycle block messages carry the durability footer and
# preserve their directive tokens; the anxiety lines are gone and the footer is
# bounded to exactly the six.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK_SCRIPT="$HOOKS_DIR/get-back-to-work-claude.sh"
IMPL="$HOOKS_DIR/get-back-to-work-impl.sh"
GBTW_TEST_MODE=1 source "$HOOK_SCRIPT"
pass=0; fail=0
assert_contains() { # label haystack needle
    if printf '%s' "$2" | grep -qF -- "$3"; then echo "PASS $1"; pass=$((pass+1));
    else echo "FAIL $1 — missing: $3"; fail=$((fail+1)); fi
}
assert_not_in_file() { # label file needle
    if grep -qF -- "$3" "$2"; then echo "FAIL $1 — still present: $3"; fail=$((fail+1));
    else echo "PASS $1"; pass=$((pass+1)); fi
}
assert_count_in_file() { # label file needle expected
    local n; n=$(grep -cF -- "$3" "$2")
    if [ "$n" = "$4" ]; then echo "PASS $1 (count=$n)"; pass=$((pass+1));
    else echo "FAIL $1 — count=$n expected=$4"; fail=$((fail+1)); fi
}
SENT="ARE your checkpoint"
# Footer present in each of the six constants
assert_contains "footer/code-review-required" "$_IC_MSG_CODE_REVIEW_REQUIRED" "$SENT"
assert_contains "footer/grade-too-low"        "$_IC_MSG_GRADE_TOO_LOW"        "$SENT"
assert_contains "footer/tasks-in-progress"    "$_IC_MSG_TASKS_IN_PROGRESS"    "$SENT"
assert_contains "footer/wave-complete"        "$_IC_MSG_WAVE_COMPLETE"        "$SENT"
assert_contains "footer/memory-search"        "$_IC_MSG_MEMORY_SEARCH"        "$SENT"
assert_contains "footer/work-incomplete"      "$_IC_MSG_WORK_INCOMPLETE"      "$SENT"
# Directive tokens preserved (block-unique phrases, absent from the footer)
assert_contains "dir/code-review-required" "$_IC_MSG_CODE_REVIEW_REQUIRED" "--task-boundary"
assert_contains "dir/grade-too-low"        "$_IC_MSG_GRADE_TOO_LOW"        "--task-boundary"
assert_contains "dir/wave-complete"        "$_IC_MSG_WAVE_COMPLETE"        "get_next_tasks"
assert_contains "dir/memory-search"        "$_IC_MSG_MEMORY_SEARCH"        "search-conversations"
assert_contains "dir/tasks-in-progress"    "$_IC_MSG_TASKS_IN_PROGRESS"    "following the plan steps exactly as written"
assert_contains "dir/work-incomplete"      "$_IC_MSG_WORK_INCOMPLETE"      "complete the current task"
# Negative / widened-guard bounds (grep the source)
assert_not_in_file "neg/no-do-not-stop" "$IMPL" "Do NOT stop"
# Footer referenced exactly 6 times (once per message constant) + 1 definition = 7 lines
assert_count_in_file "neg/footer-bounded" "$IMPL" "_IC_DURABILITY_FOOTER" "7"
echo "---- tone: $pass passed, $fail failed ----"
[ "$fail" -eq 0 ]
