#!/bin/bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT_DIR/hooks/subagent-circuit-breaker.sh"
PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s%s\n' "$1" "${2:+ — $2}"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$label"; else fail "$label" "expected=$expected actual=$actual"; fi
}

TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT
TEST_HOME="$TEST_ROOT/home"
SESSION="019fc5e5-fc72-7493-b785-bee8cda62b1b"
STATE_DB="$TEST_HOME/.claude/ironclaude.db"
mkdir -p "$TEST_HOME/.claude"
printf '{"verbose_hook_logs":false}\n' > "$TEST_HOME/.claude/ironclaude-hooks-config.json"

sqlite3 "$STATE_DB" <<SQL
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
  terminal_session TEXT PRIMARY KEY,
  professional_mode TEXT NOT NULL,
  circuit_breaker INTEGER NOT NULL DEFAULT 0
);
INSERT INTO sessions (terminal_session, professional_mode, circuit_breaker)
  VALUES ('$SESSION', 'on', 1);
SQL

run_agent_pretooluse() {
  printf '{"session_id":"%s","tool_name":"Agent","tool_input":{}}' "$SESSION" \
    | HOME="$TEST_HOME" bash "$HOOK" >/dev/null 2>&1
  printf '%s' "$?"
}

# (a) PM on + tripped breaker: dispatch BLOCKED (breaker intact)
RC_ON=$(run_agent_pretooluse)
assert_eq "PM on + tripped breaker blocks Agent dispatch" "2" "$RC_ON"

# (b) PM off + tripped breaker: dispatch ALLOWED (Invariant B)
sqlite3 "$STATE_DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='$SESSION';"
RC_OFF=$(run_agent_pretooluse)
assert_eq "PM off + tripped breaker allows Agent dispatch" "0" "$RC_OFF"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
