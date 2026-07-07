#!/bin/bash
# test-gbtw-inflight.sh — fixture-driven tests for the GBTW hook's
# in-flight background-subagent/job detector.
#
# Usage: bash worker/hooks/tests/test-gbtw-inflight.sh
#
# Strategy: source get-back-to-work-claude.sh with GBTW_TEST_MODE=1 (which
# short-circuits the hook's run_hook + event-read block so we only get the
# helper function definitions), then call _gbtw_extract_in_flight <fixture>
# and compare its stdout (space-separated ids, order-insensitive) against
# the expected set.
#
# Exits 0 iff all cases pass. Prints PASS/FAIL per case.

set -u

# Resolve dirs
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures"
HOOK_SCRIPT="$HOOKS_DIR/get-back-to-work-claude.sh"

# Source the hook in test mode
if [ ! -f "$HOOK_SCRIPT" ]; then
    echo "FATAL: hook script not found: $HOOK_SCRIPT" >&2
    exit 2
fi
# shellcheck disable=SC1090
GBTW_TEST_MODE=1 source "$HOOK_SCRIPT"

if ! type _gbtw_extract_in_flight &>/dev/null; then
    echo "FATAL: _gbtw_extract_in_flight not defined after sourcing hook (Task 2 not landed yet — this is the RED baseline)" >&2
    # Still run through cases below so the RED signal is uniform.
    _gbtw_extract_in_flight() { echo ""; }
fi

pass=0
fail=0

# Normalize a space-separated id list to a sorted, unique newline list
_normalize() {
    tr ' ' '\n' | sed '/^$/d' | LC_ALL=C sort -u
}

assert_ids() {
    local case="$1"
    local expected="$2"   # space-separated expected in-flight ids (may be empty)
    local fixture="$3"
    local got_raw exp_norm got_norm
    got_raw="$(_gbtw_extract_in_flight "$fixture" 2>/dev/null || true)"
    exp_norm="$(echo "$expected" | _normalize)"
    got_norm="$(echo "$got_raw" | _normalize)"
    if [ "$exp_norm" = "$got_norm" ]; then
        echo "PASS $case  in_flight=[$(echo "$got_norm" | tr '\n' ' ')]"
        pass=$((pass + 1))
    else
        echo "FAIL $case  expected=[$expected]  got=[$got_raw]"
        fail=$((fail + 1))
    fi
}

# F1: subagent in flight (no completion) -> approve, id aaaaaaaaaaaaaaaaa
assert_ids "F1 subagent-in-flight" \
    "aaaaaaaaaaaaaaaaa" \
    "$FIXTURES_DIR/f1-subagent-in-flight.jsonl"

# F2: subagent completed via task-notification -> block, empty
assert_ids "F2 subagent-completed" \
    "" \
    "$FIXTURES_DIR/f2-subagent-completed.jsonl"

# F3: regression proof — in-flight subagent past 6 holding turns -> approve
assert_ids "F3 in-flight-past-window" \
    "ccccccccccccccccc" \
    "$FIXTURES_DIR/f3-in-flight-past-window.jsonl"

# F4: resumed plain-text completion -> block, empty
assert_ids "F4 plaintext-completion" \
    "" \
    "$FIXTURES_DIR/f4-plaintext-completion.jsonl"

# F5: background Bash in flight -> approve, id babcdef01
assert_ids "F5 bash-bg-in-flight" \
    "babcdef01" \
    "$FIXTURES_DIR/f5-bash-bg-in-flight.jsonl"

# F6: two dispatched, one completed -> approve, the incomplete one
assert_ids "F6 two-dispatched-one-completed" \
    "fffffffffffffffff" \
    "$FIXTURES_DIR/f6-two-dispatched-one-completed.jsonl"

# F7: four dispatched, all four terminal statuses -> block, empty
assert_ids "F7 all-terminal-statuses" \
    "" \
    "$FIXTURES_DIR/f7-all-terminal-statuses.jsonl"

# F8: malformed adjacent line — must not crash, still see the valid dispatch
assert_ids "F8 malformed-line" \
    "h5555555555555555" \
    "$FIXTURES_DIR/f8-malformed-line.jsonl"

# F9: Bash BG launched AND completed via <task-notification> — the existing
# extractor is prefix-agnostic on <task-id> so a b-prefixed completion matches
# the same jq regex Agent subagents use. Regression witness for the GB-02
# retraction (verified empirically 2026-07-05 against a real transcript).
assert_ids "F9 bash-bg-completed" \
    "" \
    "$FIXTURES_DIR/f9-bash-bg-completed.jsonl"

# F10: launch beyond the OLD tail -n 4000 horizon (>4000 padding lines after
# the dispatch). Under `tail -n 4000` this used to silently under-suppress
# (the launch scrolled out; block fired on a legitimate wait). Under the new
# `tail -c 8M` horizon the launch is still visible and its id reads as
# in-flight. Regression witness for GB-01.
assert_ids "F10 launch-beyond-horizon" \
    "bf10horizon" \
    "$FIXTURES_DIR/f10-launch-beyond-horizon.jsonl"

echo
echo "results: $pass pass, $fail fail"
[ "$fail" -eq 0 ] || exit 1
