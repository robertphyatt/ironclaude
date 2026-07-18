#!/usr/bin/env bash
# test-gbtw-antipattern-override.sh - Unit tests for _gbtw_should_rearm_check
#
# Follows the sanctioned GBTW_TEST_MODE=1 source-and-call seam used by
# worker/hooks/tests/test-gbtw-waiting.sh. Sourcing get-back-to-work-claude.sh
# with GBTW_TEST_MODE=1 exposes helpers (including _gbtw_should_rearm_check
# and, transitively via hook-logger.sh, _ic_is_antipattern_proposal) without
# executing the hook body.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../get-back-to-work-claude.sh"

if [ ! -r "$HOOK" ]; then
  echo "FAIL: hook not found at $HOOK"
  exit 1
fi

# shellcheck disable=SC1090
GBTW_TEST_MODE=1 source "$HOOK"

if ! type _gbtw_should_rearm_check >/dev/null 2>&1; then
  echo "FAIL: _gbtw_should_rearm_check not defined after sourcing"
  exit 1
fi

PASSES=0
FAILS=0

check() {
  local name="$1" stage="$2" context="$3" expected="$4"
  local got
  got=$(_gbtw_should_rearm_check "$stage" "$context")
  if [ "$got" = "$expected" ]; then
    printf 'PASS  %s stage=%s expected=%s got=%s\n' "$name" "$stage" "$expected" "$got"
    PASSES=$((PASSES + 1))
  else
    printf 'FAIL  %s stage=%s expected=%s got=%s\n' "$name" "$stage" "$expected" "$got"
    printf '      context: %q\n' "$context"
    FAILS=$((FAILS + 1))
  fi
}

# In-scope stages with anti-pattern proposal → re-arm (true)
check  1 executing       "Shall we checkpoint here and resume fresh?"                     true
check  2 reviewing       "Should we pause here?"                                          true
check  3 brainstorming   "You run these queries yourself"                                 true
check  9 final_plan_prep "You paste the sqlite output"                                    true

# In-scope stages with workflow-required question / normal completion → don't re-arm (false)
check  4 plan_ready      "Fable review returned SOLID — proceed with plan?"               false
check  5 executing       "Grade: A. Ready to proceed to next task."                       false

# Out-of-scope stages must never re-arm even if lexicon matches
check  6 plan_interrupted "Shall we checkpoint here?"                                     false
check  7 idle             "Shall we checkpoint here?"                                     false

# Meta-discussion escape (blockquote)
check  8 brainstorming   "> - Claude PROPOSED to checkpoint"                              false

# Multi-line: proposal on line 1, option bullets after (bullets are NOT meta)
check 10 executing       "$(printf 'Shall we checkpoint?\n\n- Yes\n- No')"                true

# Common permission-request grammar must also re-arm during execution
check 11 executing       "Would you like me to checkpoint here?"                         true

echo
printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ]
