#!/bin/bash
# block-pin-enforcer.sh: PostToolUse reminder when update_ledger records blocked tasks.
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/block-pin-enforcer.sh"
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
out() { jq -cn --arg t "$1" --argjson i "$2" '{session_id:"bpe",tool_name:$t,tool_input:$i}' | bash "$HOOK"; }
OUT=$(out mcp__orchestrator__update_ledger '{"tasks":[{"id":"t7","status":"blocked"},{"id":"t8","status":"in_progress"}]}')
if printf '%s' "$OUT" | jq -e '.systemMessage | contains("BLOCKED TASK PIN REQUIRED") and contains("t7") and (contains("t8") | not)' >/dev/null; then pass "blocked task reminder names only t7"; else fail "blocked task reminder" "$OUT"; fi
OUT=$(out mcp__orchestrator__update_ledger '{"tasks":[{"id":"t8","status":"in_progress"}]}')
if [ -z "$OUT" ]; then pass "no blocked tasks -> no output"; else fail "no blocked tasks" "$OUT"; fi
OUT=$(out mcp__orchestrator__get_task_ledger '{"tasks":[{"id":"t7","status":"blocked"}]}')
if [ -z "$OUT" ]; then pass "other tool ignored"; else fail "other tool ignored" "$OUT"; fi
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
