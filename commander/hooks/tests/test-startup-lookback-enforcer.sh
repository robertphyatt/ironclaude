#!/bin/bash
# SF2: characterization test for startup-lookback-enforcer.sh — the 48h-lookback
# gate that this loop registers into the Brain's settings.json. Uses a FAKE
# session_id so SESSION_TAG (= session_id, hook-logger.sh:95-98) isolates the
# /tmp/ic flags from any live Brain session; arms via the hook's own ARM tools so
# it does not need to replicate the flag-path derivation.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # -> commander/
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"                          # -> repo root
HOOK_SRC="$ROOT_DIR/hooks/startup-lookback-enforcer.sh"
LOGGER_SRC="$REPO_ROOT/worker/hooks/hook-logger.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$label"; else fail "$label" "expected=$expected actual=$actual"; fi
}

TEST_ROOT=$(mktemp -d)
SCRIPT_DIR="$TEST_ROOT/hooks"
mkdir -p "$SCRIPT_DIR"
cp "$HOOK_SRC" "$SCRIPT_DIR/"          # deploy the hook under test
cp "$LOGGER_SRC" "$SCRIPT_DIR/"        # its sourced dependency (deployed by make deploy-hooks in prod)
HOOK="$SCRIPT_DIR/startup-lookback-enforcer.sh"
SESSION="ic-sf2-test-$$-$(date +%s)"  # fake session_id -> isolated SESSION_TAG
export HOME="$TEST_ROOT/home"
mkdir -p "$HOME/.claude"
printf '{"verbose_hook_logs":false}\n' > "$HOME/.claude/ironclaude-hooks-config.json"
trap 'rm -rf "$TEST_ROOT"; rm -f "/tmp/ic/lookback-slack-$SESSION" "/tmp/ic/lookback-ledger-$SESSION"' EXIT

feed() {  # feed TOOL_NAME [HOURS_BACK] -> prints the hook exit code
  local tool="$1" hours="${2:-}"
  if [ -n "$hours" ]; then
    printf '{"session_id":"%s","tool_name":"%s","tool_input":{"hours_back":%s}}' "$SESSION" "$tool" "$hours" \
      | bash "$HOOK" >/dev/null 2>&1
  else
    printf '{"session_id":"%s","tool_name":"%s","tool_input":{}}' "$SESSION" "$tool" \
      | bash "$HOOK" >/dev/null 2>&1
  fi
  printf '%s' "$?"
}

# (1) unarmed: gated action tools are blocked (block_pretooluse -> exit 2)
assert_eq "spawn_worker blocked without lookback" "2" "$(feed mcp__orchestrator__spawn_worker)"
assert_eq "kill_worker blocked without lookback"  "2" "$(feed mcp__orchestrator__kill_worker)"

# (2) query tools bypass the gate
assert_eq "get_worker_status allowed (query bypass)" "0" "$(feed mcp__orchestrator__get_worker_status)"

# (3) arm both flags via the hook's own ARM tools
assert_eq "get_operator_messages(48h) arms slack flag" "0" "$(feed mcp__orchestrator__get_operator_messages 48)"
assert_eq "update_ledger arms ledger flag"             "0" "$(feed mcp__orchestrator__update_ledger)"

# (4) armed: the gated action is now allowed
assert_eq "spawn_worker allowed after lookback" "0" "$(feed mcp__orchestrator__spawn_worker)"

# (5) fail-closed: with hook-logger.sh absent a gated action blocks, a query passes.
NOLOG_DIR="$TEST_ROOT/nolog"
mkdir -p "$NOLOG_DIR"
cp "$HOOK_SRC" "$NOLOG_DIR/"          # hook only — deliberately NO hook-logger.sh
NOLOG_HOOK="$NOLOG_DIR/startup-lookback-enforcer.sh"
nofeed() {  # nofeed TOOL -> exit code, against the logger-less hook
  printf '{"session_id":"%s","tool_name":"%s","tool_input":{}}' "$SESSION" "$1" \
    | bash "$NOLOG_HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}
assert_eq "no-logger: spawn_worker blocked"      "2" "$(nofeed mcp__orchestrator__spawn_worker)"
assert_eq "no-logger: get_worker_status allowed" "0" "$(nofeed mcp__orchestrator__get_worker_status)"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
