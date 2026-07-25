#!/bin/bash
# Tests get-back-to-work-claude.sh (now a client-aware wrapper over get-back-to-work-impl.sh):
#  - non-codex: byte-identical pass-through of impl (real cmp, not $())
#  - codex: valid JSON, no decision/reason; allow -> {}, block -> {systemMessage}
#  - GBTW_TEST_MODE still exposes the enforcement _gbtw_* helpers (via impl delegation)
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAP="$DIR/get-back-to-work-claude.sh"; IMPL="$DIR/get-back-to-work-impl.sh"
T="$(mktemp -d)"; FAIL=0
pass(){ echo "PASS: $1"; }; fail(){ echo "FAIL: $1"; FAIL=1; }
STDIN='{"session_id":"wraptest","hook_event_name":"Stop","stop_hook_active":false}'

# 1) Non-codex: wrapper output byte-identical to impl (real cmp). PLUGIN_ROOT unset => not codex.
printf '%s' "$STDIN" | bash "$IMPL" > "$T/impl" 2>/dev/null; RC_I=$?
printf '%s' "$STDIN" | env -u PLUGIN_ROOT -u CLAUDE_PLUGIN_ROOT bash "$WRAP" > "$T/wrap" 2>/dev/null; RC_W=$?
cmp -s "$T/impl" "$T/wrap" && pass "non-codex byte-identical (cmp)" || fail "non-codex differs (cmp)"
[ "$RC_I" = "$RC_W" ] && pass "non-codex exit equal ($RC_I)" || fail "exit differs: $RC_I vs $RC_W"

# 2) Codex (forced): valid JSON, no decision/reason.
CX="$(printf '%s' "$STDIN" | GBTW_FORCE_CODEX=1 bash "$WRAP")"
echo "$CX" | jq -e . >/dev/null 2>&1 && pass "codex valid JSON" || fail "codex not JSON: [$CX]"
echo "$CX" | jq -e 'has("decision") or has("reason")' >/dev/null 2>&1 && fail "codex has decision/reason" || pass "codex no decision/reason"

# 3) Translator units: approve -> {} (silent), block -> {systemMessage:reason}.
GBTW_WRAPPER_TEST_MODE=1 source "$WRAP"
A="$(printf '%s' '{"decision":"approve","reason":"Professional mode disabled"}' | _translate_stop_to_codex)"
[ "$A" = "{}" ] && pass "approve -> {} (silent)" || fail "approve not {}: [$A]"
B="$(printf '%s' '{"decision":"block","reason":"do X now"}' | _translate_stop_to_codex)"
echo "$B" | jq -e . >/dev/null 2>&1 && pass "block valid JSON" || fail "block not JSON: [$B]"
echo "$B" | jq -e 'has("decision") or has("reason")' >/dev/null 2>&1 && fail "block has decision/reason: [$B]" || pass "block no decision/reason"
echo "$B" | grep -q "do X now" && pass "block reason preserved (systemMessage)" || fail "block reason lost: [$B]"

# 4) Helper-exposure compat: GBTW_TEST_MODE still exposes the enforcement helpers (via impl).
( GBTW_TEST_MODE=1 source "$WRAP"; type _gbtw_extract_in_flight >/dev/null 2>&1 ) && pass "GBTW_TEST_MODE exposes helpers" || fail "helpers not exposed via GBTW_TEST_MODE"

rm -rf "$T"
[ "$FAIL" = "0" ] && echo "ALL WRAPPER TESTS PASSED" || echo "WRAPPER TESTS FAILED"
exit $FAIL
