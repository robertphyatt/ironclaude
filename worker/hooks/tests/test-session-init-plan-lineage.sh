#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../session-init.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="${TMP_ROOT}/home"
SESSION="non-default-plan-lineage-session"

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "${TMP_HOME}/.claude"
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOK" >/dev/null

DB_PATH="${TMP_HOME}/.claude/ironclaude.db"
test "$(sqlite3 "$DB_PATH" "SELECT dflt_value FROM pragma_table_info('sessions') WHERE name = 'plan_lineage';")" = "0"
test "$(sqlite3 "$DB_PATH" "SELECT dflt_value FROM pragma_table_info('tier_up_reviews') WHERE name = 'plan_lineage';")" = "0"
test "$(sqlite3 "$DB_PATH" "SELECT plan_lineage FROM sessions WHERE terminal_session = '$SESSION';")" = "0"
printf 'PASS session-init creates plan lineage columns with default 0\n'
