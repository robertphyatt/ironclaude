#!/usr/bin/env bash
# test-plan-task-context-apply-patch.sh - plan-task-context.sh is wired for
# Codex's native apply_patch (hooks.json matcher) and blocks it while a code
# review is pending. v1.1.16 L2b-1 R3.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-ptc-apply-patch"
DB="$TMP_HOME/.claude/ironclaude.db"
PASSES=0
FAILS=0
EXPECTED_PASSES=3
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

# 1. Matcher admits apply_patch, keeps existing tools, and stays anchored.
M=$(jq -r '.hooks.PreToolUse[] | select(.hooks[].command | contains("plan-task-context.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
if matches "$M" "apply_patch" && matches "$M" "Read" && matches "$M" "Task" \
   && ! matches "$M" "apply_patchX" && ! matches "$M" "exec_command"; then
  ok "1 plan-task-context matcher admits apply_patch only as an exact name"
else
  bad "1 plan-task-context matcher admits apply_patch only as an exact name (matcher: $M)"
fi

# Session: executing, review pending, one submitted task in wave 1.
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', review_pending=1, current_wave=1 WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'submitted');"

# 2. apply_patch is blocked while review is pending.
AP_IN=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"PreToolUse",tool_name:"apply_patch",tool_input:{command:"*** Begin Patch\n*** Add File: x.txt\n+hi\n*** End Patch"}}')
printf '%s' "$AP_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/plan-task-context.sh" >/dev/null 2>"$TMP_ROOT/err"
RC=$?
if [ "$RC" = "2" ] && grep -qF "BLOCKED — CODE REVIEW REQUIRED" "$TMP_ROOT/err"; then
  ok "2 apply_patch blocked during review_pending"
else
  bad "2 apply_patch blocked during review_pending (rc=$RC)"
fi

# 3. Negative control: Read is allowed in the same state.
RD_IN=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"PreToolUse",tool_name:"Read",tool_input:{file_path:"x.txt"}}')
printf '%s' "$RD_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/plan-task-context.sh" >/dev/null 2>&1
RC=$?
[ "$RC" = "0" ] && ok "3 Read allowed during review_pending" || bad "3 Read allowed during review_pending (rc=$RC)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
