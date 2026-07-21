#!/usr/bin/env bash
# Focused regression harness for Wave 1 hook-side no-op transition behavior.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="${SCRIPT_DIR}/.."
BRIDGE="${HOOKS_DIR}/skill-state-bridge.sh"
LOGGER="${HOOKS_DIR}/mcp-state-logger.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="${TMP_ROOT}/home"
DB_PATH="${TMP_HOME}/.claude/ironclaude.db"
SESSION="workflow-transition-idempotency"
PASSES=0
FAILS=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "${TMP_HOME}/.claude"
printf '{"verbose_hook_logs":true}\n' > "${TMP_HOME}/.claude/ironclaude-hooks-config.json"
sqlite3 "$DB_PATH" <<'SQL'
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
  terminal_session TEXT PRIMARY KEY,
  professional_mode TEXT NOT NULL DEFAULT 'on',
  workflow_stage TEXT NOT NULL DEFAULT 'idle',
  active_skill TEXT,
  memory_search_required INTEGER NOT NULL DEFAULT 0,
  testing_theatre_checked INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  terminal_session TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  context TEXT
);
INSERT INTO sessions (terminal_session, workflow_stage, active_skill, memory_search_required, updated_at)
VALUES ('workflow-transition-idempotency', 'debugging', 'systematic-debugging', 0, 'initial');
SQL

pass() { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$name"; else
    fail "$name expected=$(printf %q "$expected") actual=$(printf %q "$actual")"
  fi
}
snapshot() {
  sqlite3 "$DB_PATH" "SELECT workflow_stage || '|' || COALESCE(active_skill, '') || '|' || memory_search_required || '|' || COALESCE(updated_at, '') FROM sessions WHERE terminal_session='${SESSION}'; SELECT COUNT(*) FROM audit_log WHERE terminal_session='${SESSION}';" | paste -sd ';' -
}
run_hook() {
  local hook="$1" payload="$2"
  printf '%s' "$payload" | HOME="$TMP_HOME" bash "$hook"
}

# Same target: skill-state-bridge must be entirely side-effect free, including stdout.
before=$(snapshot)
same_stdout=$(run_hook "$BRIDGE" '{"tool_name":"Skill","tool_input":{"skill":"ironclaude:systematic-debugging"},"session_id":"workflow-transition-idempotency"}')
same_status=$?
after=$(snapshot)
assert_eq "bridge same-target exits 0" "0" "$same_status"
assert_eq "bridge same-target stdout empty" "" "$same_stdout"
assert_eq "bridge same-target snapshot unchanged" "$before" "$after"

# A distinct, legal target remains observable exactly once.
different_stdout=$(run_hook "$BRIDGE" '{"tool_name":"Skill","tool_input":{"skill":"ironclaude:brainstorming"},"session_id":"workflow-transition-idempotency"}')
different_status=$?
assert_eq "bridge different-target exits 0" "0" "$different_status"
assert_eq "bridge different-target stage" "brainstorming" "$(sqlite3 "$DB_PATH" "SELECT workflow_stage FROM sessions WHERE terminal_session='${SESSION}';")"
assert_eq "bridge different-target one audit" "1" "$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM audit_log WHERE terminal_session='${SESSION}' AND action='skill_activated';")"
if [ -n "$different_stdout" ]; then pass "bridge different-target emits once"; else fail "bridge different-target emits once"; fi

reset_logger_fixture() {
  sqlite3 "$DB_PATH" "UPDATE sessions SET workflow_stage='brainstorming', active_skill='brainstorming', updated_at='logger-fixture' WHERE terminal_session='${SESSION}'; DELETE FROM audit_log WHERE terminal_session='${SESSION}';"
}
assert_logger_silent_unchanged() {
  local name="$1" payload="$2" before_snapshot logger_stdout logger_status after_snapshot
  before_snapshot=$(snapshot)
  logger_stdout=$(run_hook "$LOGGER" "$payload")
  logger_status=$?
  after_snapshot=$(snapshot)
  assert_eq "$name exits 0" "0" "$logger_status"
  assert_eq "$name stdout empty" "" "$logger_stdout"
  assert_eq "$name snapshot unchanged" "$before_snapshot" "$after_snapshot"
}

reset_logger_fixture
assert_logger_silent_unchanged "logger object changed false" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":{"changed":false},"session_id":"workflow-transition-idempotency"}'
assert_logger_silent_unchanged "logger string changed false" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":"{\"changed\":false}","session_id":"workflow-transition-idempotency"}'
assert_logger_silent_unchanged "logger content text changed false" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":{"content":[{"type":"text","text":"{\"changed\":false}"}]},"session_id":"workflow-transition-idempotency"}'
assert_logger_silent_unchanged "logger legacy response changed false" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_response":{"changed":false},"session_id":"workflow-transition-idempotency"}'
assert_logger_silent_unchanged "logger malformed object" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":{"unexpected":true},"session_id":"workflow-transition-idempotency"}'
assert_logger_silent_unchanged "logger malformed string" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":"not-json","session_id":"workflow-transition-idempotency"}'
assert_logger_silent_unchanged "logger malformed content" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":{"content":[{"type":"text","text":"not-json"}]},"session_id":"workflow-transition-idempotency"}'

assert_logger_changed_once_unchanged() {
  local name="$1" payload="$2" logger_before logger_stdout logger_status logger_after
  reset_logger_fixture
  logger_before=$(snapshot)
  logger_stdout=$(run_hook "$LOGGER" "$payload")
  logger_status=$?
  logger_after=$(snapshot)
  assert_eq "$name exits 0" "0" "$logger_status"
  if [ -n "$logger_stdout" ]; then pass "$name emits output"; else fail "$name emits output"; fi
  assert_eq "$name emits exactly one line" "1" "$(printf '%s\n' "$logger_stdout" | wc -l | tr -d ' ')"
  assert_eq "$name snapshot unchanged" "$logger_before" "$logger_after"
}

assert_logger_changed_once_unchanged "logger object changed true" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":{"changed":true},"session_id":"workflow-transition-idempotency"}'
assert_logger_changed_once_unchanged "logger string changed true" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":"{\"changed\":true}","session_id":"workflow-transition-idempotency"}'
assert_logger_changed_once_unchanged "logger content text changed true" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_output":{"content":[{"type":"text","text":"{\"changed\":true}"}]},"session_id":"workflow-transition-idempotency"}'
assert_logger_changed_once_unchanged "logger legacy response changed true" '{"tool_name":"mcp__state_manager__mark_brainstorming","tool_response":{"changed":true},"session_id":"workflow-transition-idempotency"}'

printf '\nResults: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ]
