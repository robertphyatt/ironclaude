#!/bin/bash
# attention-sweep-enforcer.sh + attention-sweep-arm.sh: gated worker actions need a
# full-fleet get_worker_status within 3 minutes (flag /tmp/ic/sweep-armed-<session>).
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
TEST_ROOT=$(mktemp -d)
SCRIPT_DIR="$TEST_ROOT/hooks"
mkdir -p "$SCRIPT_DIR"
cp "$ROOT_DIR/hooks/attention-sweep-enforcer.sh" "$ROOT_DIR/hooks/attention-sweep-arm.sh" "$REPO_ROOT/worker/hooks/hook-logger.sh" "$SCRIPT_DIR/"
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude"
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
SESSION="ic-sweep-test-$$-$(date +%s)"
FLAG="/tmp/ic/sweep-armed-$SESSION"
trap 'rm -rf "$TEST_ROOT" "$FLAG"' EXIT
PASS=0; FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected=$2 actual=$3"; fi; }
run() {  # run SCRIPT TOOL TOOL_INPUT_JSON -> exit code
  jq -cn --arg s "$SESSION" --arg t "$2" --argjson i "$3" '{session_id:$s,tool_name:$t,tool_input:$i}' \
    | bash "$SCRIPT_DIR/$1" >/dev/null 2>&1
  printf '%s' "$?"
}
rm -f "$FLAG"
assert_eq "spawn without sweep blocked" "2" "$(run attention-sweep-enforcer.sh mcp__orchestrator__spawn_worker '{}')"
assert_eq "get_* query passes" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__get_task_ledger '{}')"
assert_eq "ungated tool passes" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__wiki_query '{}')"
run attention-sweep-arm.sh mcp__orchestrator__get_worker_status '{"worker_id":"w1"}' >/dev/null
assert_eq "single-worker query does not arm" "2" "$(run attention-sweep-enforcer.sh mcp__orchestrator__send_to_worker '{}')"
run attention-sweep-arm.sh mcp__orchestrator__get_worker_status '{}' >/dev/null
assert_eq "full sweep arms send_to_worker" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__send_to_worker '{}')"
assert_eq "flag retained within TTL" "0" "$(run attention-sweep-enforcer.sh mcp__orchestrator__kill_worker '{}')"
touch -t 200001010000 "$FLAG"
assert_eq "expired sweep blocked" "2" "$(run attention-sweep-enforcer.sh mcp__orchestrator__spawn_worker '{}')"
if [ -e "$FLAG" ]; then fail "expired flag removed"; else pass "expired flag removed"; fi
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
