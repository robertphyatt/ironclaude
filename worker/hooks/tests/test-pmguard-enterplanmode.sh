#!/usr/bin/env bash
# test-pmguard-enterplanmode.sh - the guard's PreToolUse matcher includes
# EnterPlanMode, and the existing block (professional-mode-guard.sh, "USE
# BRAINSTORMING INSTEAD") fires for it when professional mode is on. v1.1.16 L2b-1 R4.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-epm-session"
DB="$TMP_HOME/.claude/ironclaude.db"
PASSES=0
FAILS=0
EXPECTED_PASSES=3
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

# 1. Matcher admits EnterPlanMode as an exact name and keeps existing tools.
M=$(jq -r '.hooks.PreToolUse[] | select(.hooks[].command | contains("professional-mode-guard.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
if matches "$M" "EnterPlanMode" && matches "$M" "apply_patch" && matches "$M" "exec_command" \
   && ! matches "$M" "EnterPlanModeX" && ! matches "$M" "ExitPlanMode"; then
  ok "1 guard matcher admits EnterPlanMode only as an exact name"
else
  bad "1 guard matcher admits EnterPlanMode only as an exact name (matcher: $M)"
fi

printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
EPM_IN=$(jq -cn --arg s "$SESSION" --arg c "$TMP_ROOT" '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:"EnterPlanMode",tool_input:{}}')

# 2. Professional mode on: blocked with the brainstorming redirect.
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
printf '%s' "$EPM_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/professional-mode-guard.sh" >/dev/null 2>"$TMP_ROOT/err"
RC=$?
if [ "$RC" = "2" ] && grep -qF "BLOCKED — USE BRAINSTORMING INSTEAD" "$TMP_ROOT/err"; then
  ok "2 EnterPlanMode blocked with professional mode on"
else
  bad "2 EnterPlanMode blocked with professional mode on (rc=$RC)"
fi

# 3. Negative control: professional mode off allows it (Invariant B).
sqlite3 "$DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='${SESSION}';"
printf '%s' "$EPM_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/professional-mode-guard.sh" >/dev/null 2>&1
RC=$?
[ "$RC" = "0" ] && ok "3 EnterPlanMode allowed with professional mode off" || bad "3 EnterPlanMode allowed with professional mode off (rc=$RC)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
