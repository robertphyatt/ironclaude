#!/usr/bin/env bash
# Focused regression harness for human professional-mode deactivation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${HOOK:-${SCRIPT_DIR}/../state-activator.sh}"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="${TMP_ROOT}/home"
DB_PATH="${TMP_HOME}/.claude/ironclaude.db"
SESSION="deactivation-session"
OTHER_SESSION="other-session"
PASSES=0
FAILS=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "${TMP_HOME}/.claude"
sqlite3 "$DB_PATH" <<'SQL'
PRAGMA journal_mode=WAL;
CREATE TABLE sessions (
  terminal_session TEXT PRIMARY KEY,
  professional_mode TEXT NOT NULL,
  workflow_stage TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE wave_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  terminal_session TEXT NOT NULL,
  status TEXT NOT NULL
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
INSERT INTO sessions VALUES ('deactivation-session', 'on', 'brainstorming', 'initial');
INSERT INTO sessions VALUES ('other-session', 'on', 'executing', 'initial');
SQL

pass() { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then pass "$name"; else
    fail "$name expected=$(printf %q "$expected") actual=$(printf %q "$actual")"
  fi
}
assert_contains() {
  local name="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then pass "$name"; else fail "$name missing=$(printf %q "$needle")"; fi
}
assert_not_contains() {
  local name="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then pass "$name"; else fail "$name unexpected=$(printf %q "$needle")"; fi
}
state() {
  sqlite3 "$DB_PATH" "SELECT professional_mode || '|' || workflow_stage FROM sessions WHERE terminal_session='$1';"
}
reset_current() {
  sqlite3 "$DB_PATH" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming', updated_at='initial' WHERE terminal_session='${SESSION}'; DELETE FROM wave_tasks WHERE terminal_session='${SESSION}';"
}
run_prompt() {
  local prompt="$1" session_id="${2:-$SESSION}"
  jq -cn --arg prompt "$prompt" --arg session_id "$session_id" '{prompt: $prompt, session_id: $session_id}' \
    | HOME="$TMP_HOME" bash "$HOOK"
}

assert_deactivates() {
  local name="$1" prompt="$2"
  reset_current
  run_prompt "$prompt" >/dev/null
  assert_eq "$name" "off|idle" "$(state "$SESSION")"
}

assert_ignored() {
  local name="$1" prompt="$2"
  reset_current
  run_prompt "$prompt" >/dev/null
  assert_eq "$name" "on|brainstorming" "$(state "$SESSION")"
}

assert_deactivates "slash invocation" "/deactivate-professional-mode"
assert_deactivates "namespaced slash invocation" "/ironclaude:deactivate-professional-mode"
assert_deactivates "Codex dollar invocation" '$ironclaude:deactivate-professional-mode'
assert_deactivates "Codex dollar invocation with outer whitespace" '  $ironclaude:deactivate-professional-mode  '
assert_deactivates "Codex Markdown skill link" '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)'
assert_deactivates "Codex Markdown skill link with outer whitespace" '  [$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)  '

assert_ignored "dollar invocation embedded in prose" 'please $ironclaude:deactivate-professional-mode'
assert_ignored "dollar invocation in code span" '`$ironclaude:deactivate-professional-mode`'
assert_ignored "escaped dollar invocation" '\$ironclaude:deactivate-professional-mode'
assert_ignored "dollar invocation with suffix" '$ironclaude:deactivate-professional-mode-extra'
assert_ignored "unnamespaced dollar invocation" '$deactivate-professional-mode'
assert_ignored "uppercase dollar invocation" '$IRONCLAUDE:DEACTIVATE-PROFESSIONAL-MODE'
assert_ignored "slash invocation with suffix" '/deactivate-professional-mode-extra'
assert_ignored "Markdown skill link embedded in prose" 'please [$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "Markdown skill link wrong skill path" '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/activate-professional-mode/SKILL.md)'
assert_ignored "Markdown skill link relative target" '[$ironclaude:deactivate-professional-mode](skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "Markdown skill link URL target" '[$ironclaude:deactivate-professional-mode](https://example.invalid/skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "Markdown skill link uppercase label" '[$IRONCLAUDE:DEACTIVATE-PROFESSIONAL-MODE](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)'
assert_ignored "Markdown skill link in code span" '`[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md)`'
assert_ignored "Markdown skill link with suffix" '[$ironclaude:deactivate-professional-mode](/Users/example/.codex/plugins/cache/ironclaude/ironclaude/1.1.2/skills/deactivate-professional-mode/SKILL.md) now'

reset_current
sqlite3 "$DB_PATH" "INSERT INTO wave_tasks (terminal_session, status) VALUES ('${SESSION}', 'in_progress');"
run_prompt "/deactivate-professional-mode" >/dev/null
assert_eq "active task preserves workflow stage" "off|brainstorming" "$(state "$SESSION")"
assert_eq "other session remains unchanged" "on|executing" "$(state "$OTHER_SESSION")"

missing_output=$(run_prompt "/deactivate-professional-mode" "missing-session")
assert_contains "zero-row warning requires verification" "verification required" "$missing_output"
assert_not_contains "zero-row warning promises no sqlite fallback" "sqlite fallback" "$missing_output"

printf '\nResults: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ]
