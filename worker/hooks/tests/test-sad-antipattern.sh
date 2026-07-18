#!/usr/bin/env bash
# test-sad-antipattern.sh - Unit tests for _sad_last_assistant_text +
# _ic_is_antipattern_proposal integration in subagent-drift-detector.sh.
#
# Sources subagent-drift-detector.sh with SAD_TEST_MODE=1 shim to bypass the
# unconditional run_hook / INPUT=$(cat) exec path at the top. Follows the
# sanctioned source-and-call seam.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../subagent-drift-detector.sh"

if [ ! -r "$HOOK" ]; then
  echo "FAIL: hook not found at $HOOK"
  exit 1
fi

# shellcheck disable=SC1090
SAD_TEST_MODE=1 source "$HOOK"

if ! type _sad_last_assistant_text >/dev/null 2>&1; then
  echo "FAIL: _sad_last_assistant_text not defined after sourcing"
  exit 1
fi

if ! type _ic_is_antipattern_proposal >/dev/null 2>&1; then
  echo "FAIL: _ic_is_antipattern_proposal not exposed (hook-logger.sh not sourced)"
  exit 1
fi

PASSES=0
FAILS=0
EXPECTED_PASSES=9

# Build a fake transcript JSONL file: array of {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
build_transcript() {
  local file="$1" text="$2"
  # shellcheck disable=SC2016
  jq -n --arg t "$text" '{
    type: "assistant",
    message: { content: [ { type: "text", text: $t } ] }
  }' > "$file"
}

check_text_extract() {
  local name="$1" transcript="$2" expected="$3"
  local got
  got=$(_sad_last_assistant_text "$transcript")
  if [ "$got" = "$expected" ]; then
    printf 'PASS  %s (text extract matches)\n' "$name"
    PASSES=$((PASSES + 1))
  else
    printf 'FAIL  %s (text extract mismatch)\n' "$name"
    printf '      expected: %q\n' "$expected"
    printf '      got:      %q\n' "$got"
    FAILS=$((FAILS + 1))
  fi
}

check_predicate_end_to_end() {
  local name="$1" transcript="$2" expected="$3"
  local text got
  text=$(_sad_last_assistant_text "$transcript")
  got=$(_ic_is_antipattern_proposal "$text")
  if [ "$got" = "$expected" ]; then
    printf 'PASS  %s (predicate=%s)\n' "$name" "$got"
    PASSES=$((PASSES + 1))
  else
    printf 'FAIL  %s (expected predicate=%s, got=%s)\n' "$name" "$expected" "$got"
    printf '      extracted text: %q\n' "$text"
    FAILS=$((FAILS + 1))
  fi
}

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# Fixture 1: subagent proposed a checkpoint — end-to-end predicate returns true
T1="${TMPDIR_TEST}/t1.jsonl"
build_transcript "$T1" "Shall we checkpoint here?"
check_text_extract         1a "$T1" "Shall we checkpoint here?"
check_predicate_end_to_end 1b "$T1" true

# Fixture 2: subagent finished cleanly — predicate returns false
T2="${TMPDIR_TEST}/t2.jsonl"
build_transcript "$T2" "Grade: A. Ready to proceed."
check_text_extract         2a "$T2" "Grade: A. Ready to proceed."
check_predicate_end_to_end 2b "$T2" false

# Fixture 3: multiline assistant text remains complete and classifies on line 1
T3="${TMPDIR_TEST}/t3.jsonl"
MULTILINE_TEXT="$(printf 'Shall we checkpoint here?\nWaiting on your answer.')"
build_transcript "$T3" "$MULTILINE_TEXT"
check_text_extract         3a "$T3" "$MULTILINE_TEXT"
check_predicate_end_to_end 3b "$T3" true

# Fixture 4: missing transcript path — helper returns empty, no crash
T4="${TMPDIR_TEST}/does-not-exist.jsonl"
got=$(_sad_last_assistant_text "$T4" || echo "CRASH")
if [ "$got" = "" ]; then
  printf 'PASS  4 (missing transcript → empty output, no crash)\n'
  PASSES=$((PASSES + 1))
else
  printf 'FAIL  4 (missing transcript → expected empty, got %q)\n' "$got"
  FAILS=$((FAILS + 1))
fi

# Fixture 5: malformed JSONL — helper returns empty, no crash
T5="${TMPDIR_TEST}/malformed.jsonl"
printf '{not-json\n' > "$T5"
got=$(_sad_last_assistant_text "$T5" || echo "CRASH")
if [ "$got" = "" ]; then
  printf 'PASS  5 (malformed transcript → empty output, no crash)\n'
  PASSES=$((PASSES + 1))
else
  printf 'FAIL  5 (malformed transcript → expected empty, got %q)\n' "$got"
  FAILS=$((FAILS + 1))
fi

# Fixture 6: unreadable transcript — helper returns empty, no crash
T6="${TMPDIR_TEST}/unreadable.jsonl"
build_transcript "$T6" "Shall we checkpoint here?"
chmod 000 "$T6"
got=$(_sad_last_assistant_text "$T6" || echo "CRASH")
chmod 600 "$T6"
if [ "$got" = "" ]; then
  printf 'PASS  6 (unreadable transcript → empty output, no crash)\n'
  PASSES=$((PASSES + 1))
else
  printf 'FAIL  6 (unreadable transcript → expected empty, got %q)\n' "$got"
  FAILS=$((FAILS + 1))
fi

# Every fixture must execute exactly one assertion path.
if [ "$PASSES" -ne "$EXPECTED_PASSES" ]; then
  printf 'FAIL  expected %d executed passes, got %d\n' "$EXPECTED_PASSES" "$PASSES"
  FAILS=$((FAILS + 1))
fi

echo
printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
if [ "$FAILS" -ne 0 ] || [ "$PASSES" -ne "$EXPECTED_PASSES" ]; then
  exit 1
fi
exit 0
