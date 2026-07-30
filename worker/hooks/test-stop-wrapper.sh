#!/bin/bash
# Tests the client-aware Stop wrapper:
#  - Claude/default: byte-identical implementation stdout and exit status
#  - Codex: native approve/block JSON and exit-zero consumption
#  - wrapper failures: one verification continuation, then visible termination
#  - valid implementation throttle: forced approve reaches wrapper and removes seed
#  - mutation mode: native assertions reject the old systemMessage-only shape
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAP="$DIR/get-back-to-work-claude.sh"
IMPL="$DIR/get-back-to-work-impl.sh"
T="$(mktemp -d)"
FAIL=0
STDIN='{"session_id":"wraptest","hook_event_name":"Stop","stop_hook_active":false}'

pass(){ echo "PASS: $1"; }
fail(){ echo "FAIL: $1"; FAIL=1; }
is_single_json(){ printf '%s' "$1" | jq -cse 'length == 1' >/dev/null 2>&1; }
is_single_block(){
  printf '%s' "$1" |
    jq -cse 'length == 1 and
      (.[0].decision == "block") and
      (.[0].reason | type == "string" and length > 0)' >/dev/null 2>&1
}
is_single_termination(){
  printf '%s' "$1" |
    jq -cse 'length == 1 and
      (.[0].continue == false) and
      (.[0].stopReason | type == "string" and length > 0)' >/dev/null 2>&1
}
assert_json(){
  is_single_json "$1" && pass "$2" || fail "$2: [$1]"
}

cleanup() {
  rm -f /tmp/.claude-block-throttle-wrapthrottle
  rm -f /tmp/.claude-bypass-counter-wrapthrottle
  rm -rf "$T"
}
trap cleanup EXIT

# Source wrapper helpers without running its body.
GBTW_WRAPPER_TEST_MODE=1 source "$WRAP"

if [ "${1:-}" = "--mutation-system-message-only" ]; then
  _translate_stop_to_codex() {
    jq -c 'if (.decision // "approve") == "block"
           then {systemMessage: (.reason // .systemMessage // "")}
           else {} end'
  }
  MUTATED="$(printf '%s' '{"decision":"block","reason":"do X now"}' |
    _translate_stop_to_codex false 0)"
  if echo "$MUTATED" | jq -e '.decision == "block" and .reason == "do X now"' >/dev/null 2>&1; then
    echo "MUTATION SURVIVED: systemMessage-only translator"
    exit 1
  fi
  echo "MUTATION REJECTED: systemMessage-only translator"
  exit 0
fi

# 1) Claude/default: real byte-identical pass-through.
printf '%s' "$STDIN" | bash "$IMPL" > "$T/impl" 2>/dev/null
RC_I=$?
printf '%s' "$STDIN" |
  env -u PLUGIN_ROOT -u CLAUDE_PLUGIN_ROOT bash "$WRAP" > "$T/wrap" 2>/dev/null
RC_W=$?
cmp -s "$T/impl" "$T/wrap" &&
  pass "non-codex byte-identical (cmp)" ||
  fail "non-codex differs (cmp)"
[ "$RC_I" = "$RC_W" ] &&
  pass "non-codex exit equal ($RC_I)" ||
  fail "exit differs: $RC_I vs $RC_W"

# 2) Translator units.
A="$(printf '%s' '{"decision":"approve","reason":"disabled"}' |
  _translate_stop_to_codex false 0)"
[ "$A" = "{}" ] && pass "approve -> {}" || fail "approve not {}: [$A]"

B="$(printf '%s' '{"decision":"block","reason":"do X now"}' |
  _translate_stop_to_codex false 0)"
assert_json "$B" "block valid JSON"
printf '%s' "$B" |
  jq -cse 'length == 1 and
    (.[0].decision == "block") and
    (.[0].reason == "do X now")' >/dev/null 2>&1 &&
  pass "block -> native decision/reason" ||
  fail "block not native: [$B]"

BF="$(printf '%s' '{"decision":"block","reason":""}' |
  _translate_stop_to_codex false 0)"
is_single_block "$BF" &&
  pass "empty block reason -> deterministic fallback" ||
  fail "empty block reason not replaced: [$BF]"

MULTI_CONCAT='{"decision":"approve"}{"decision":"block","reason":"again"}'
MULTI_LINES=$'{"decision":"approve"}\n{"decision":"block","reason":"again"}'
for INVALID in '{' '[]' '{}' '{"decision":"unknown"}' "$MULTI_CONCAT" "$MULTI_LINES"; do
  FI="$(printf '%s' "$INVALID" | _translate_stop_to_codex false 0)"
  is_single_block "$FI" &&
    pass "invalid initial output blocks: [$INVALID]" ||
    fail "invalid initial output did not block: input=[$INVALID] output=[$FI]"

  FC="$(printf '%s' "$INVALID" | _translate_stop_to_codex true 0)"
  is_single_termination "$FC" &&
    pass "invalid continued output terminates: [$INVALID]" ||
    fail "invalid continued output did not terminate: input=[$INVALID] output=[$FC]"
done

EMPTY_INITIAL="$(printf '' | _translate_stop_to_codex false 0)"
is_single_block "$EMPTY_INITIAL" &&
  pass "empty initial output blocks" ||
  fail "empty initial output did not block: [$EMPTY_INITIAL]"

NONZERO_INITIAL="$(printf '%s' '{"decision":"approve"}' |
  _translate_stop_to_codex false 7)"
is_single_block "$NONZERO_INITIAL" &&
  pass "nonzero implementation exit blocks initially" ||
  fail "nonzero initial exit did not block: [$NONZERO_INITIAL]"

NONZERO_CONTINUED="$(printf '%s' '{"decision":"block","reason":"ignored"}' |
  _translate_stop_to_codex true 9)"
is_single_termination "$NONZERO_CONTINUED" &&
  pass "nonzero implementation exit terminates when continued" ||
  fail "nonzero continued exit did not terminate: [$NONZERO_CONTINUED]"

# 3) Full Codex runner always exits zero even when implementation fails.
cat > "$T/failing-impl.sh" <<'SH'
#!/bin/bash
printf '{"decision":"block","reason":"implementation failed"}'
exit 23
SH
chmod +x "$T/failing-impl.sh"
RUN_FAIL="$(_run_codex_stop "$STDIN" "$T/failing-impl.sh")"
RC_RUN_FAIL=$?
[ "$RC_RUN_FAIL" = "0" ] &&
  pass "codex runner consumes implementation failure with exit zero" ||
  fail "codex runner propagated exit $RC_RUN_FAIL"
is_single_block "$RUN_FAIL" &&
  pass "codex runner failure is fail-closed JSON" ||
  fail "codex runner failure not fail-closed: [$RUN_FAIL]"

# 4) Forced real Codex wrapper emits valid JSON and exits zero.
CX="$(printf '%s' "$STDIN" | GBTW_FORCE_CODEX=1 bash "$WRAP")"
RC_CX=$?
assert_json "$CX" "forced codex valid JSON"
[ "$RC_CX" = "0" ] &&
  pass "forced codex wrapper exits zero" ||
  fail "forced codex wrapper exit $RC_CX"

# 5) Existing implementation throttle reaches wrapper through its unique side effect.
THOME="$T/throttle-home"
mkdir -p "$THOME/.claude"
sqlite3 "$THOME/.claude/ironclaude.db" \
  'CREATE TABLE sessions (terminal_session TEXT PRIMARY KEY, professional_mode TEXT);
   INSERT INTO sessions VALUES ("wrapthrottle", "on");'
printf '3:%s\n' "$(date +%s)" > /tmp/.claude-block-throttle-wrapthrottle
THROTTLE_INPUT='{"session_id":"wrapthrottle","hook_event_name":"Stop","stop_hook_active":true}'
THROTTLED="$(printf '%s' "$THROTTLE_INPUT" |
  HOME="$THOME" GBTW_FORCE_CODEX=1 bash "$WRAP")"
RC_THROTTLED=$?
[ "$THROTTLED" = "{}" ] &&
  pass "valid implementation throttle approve -> {}" ||
  fail "valid implementation throttle output: [$THROTTLED]"
[ "$RC_THROTTLED" = "0" ] &&
  pass "valid implementation throttle exits zero" ||
  fail "valid implementation throttle exit $RC_THROTTLED"
[ ! -e /tmp/.claude-block-throttle-wrapthrottle ] &&
  pass "valid implementation throttle removed seeded file" ||
  fail "valid implementation throttle path not observed"

# 6) Helper-exposure compatibility.
( GBTW_TEST_MODE=1 source "$WRAP"
  type _gbtw_extract_in_flight >/dev/null 2>&1
) &&
  pass "GBTW_TEST_MODE exposes helpers" ||
  fail "helpers not exposed via GBTW_TEST_MODE"

[ "$FAIL" = "0" ] &&
  echo "ALL WRAPPER TESTS PASSED" ||
  echo "WRAPPER TESTS FAILED"
exit "$FAIL"
