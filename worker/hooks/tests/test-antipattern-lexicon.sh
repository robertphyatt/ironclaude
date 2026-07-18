#!/usr/bin/env bash
# test-antipattern-lexicon.sh - Unit tests for _ic_is_antipattern_proposal
#
# Sources hook-logger.sh directly (pure lib except EXIT trap — no shim needed).
# Follows the sanctioned source-and-call test seam used by
# worker/hooks/tests/test-gbtw-waiting.sh.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_LOGGER="${SCRIPT_DIR}/../hook-logger.sh"

if [ ! -r "$HOOK_LOGGER" ]; then
  echo "FAIL: hook-logger.sh not found at $HOOK_LOGGER"
  exit 1
fi

# shellcheck disable=SC1090
source "$HOOK_LOGGER"

if ! type _ic_is_antipattern_proposal >/dev/null 2>&1; then
  echo "FAIL: _ic_is_antipattern_proposal not defined after sourcing hook-logger.sh"
  exit 1
fi

PASSES=0
FAILS=0

check() {
  local name="$1" input="$2" expected="$3"
  local got
  got=$(_ic_is_antipattern_proposal "$input")
  if [ "$got" = "$expected" ]; then
    printf 'PASS  %s (expected=%s got=%s)\n' "$name" "$expected" "$got"
    PASSES=$((PASSES + 1))
  else
    printf 'FAIL  %s (expected=%s got=%s)\n' "$name" "$expected" "$got"
    printf '      input: %q\n' "$input"
    FAILS=$((FAILS + 1))
  fi
}

# Single-line fixtures
check A "Shall we checkpoint here and resume fresh?"                              true
check B "Should we pause here?"                                                   true
check C "You run these queries yourself after a !"                                true
check D "Which approach do you prefer — A or B?"                                  false
check E "Grade: A. Ready to proceed to next task."                                false

# Meta-discussion fixtures (blockquote / heading / fenced)
check F "> - Claude PROPOSED to checkpoint / bank progress"                       false
check G "# Anti-pattern: checkpoint anxiety and let me pause here"                false

# Multi-line: checkpoint proposal on line 1, option bullets after (bullets are NOT meta)
check H "$(printf 'Shall we checkpoint here?\n\n- Yes\n- No')"                    true

# Multi-line: only lexicon-matching line is blockquoted (meta wins on that line)
check I "$(printf 'Design note:\n> - Claude PROPOSED to checkpoint (D/F example)\n\nEnd of doc.')"  false

# Common permission-request grammar
check J "Would you like me to checkpoint here?"                                  true
check K "Do you want me to pause here?"                                          true
check L "Could we find a natural stopping point?"                                true
check M "May I bank progress and resume fresh?"                                  true

echo
printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ]
