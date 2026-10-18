#!/usr/bin/env bash
# test-state-activator-ppid-scope.sh - state-activator.sh claims only the pending
# PPID marker of its own client process (the PID session-init.sh writes as
# REAL_PPID). A marker for any other PID belongs to another session and is left
# untouched, so this session's id is never stamped onto another client's PID.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-sa-ppid-scope"
FOREIGN_PID=999999
PASSES=0
FAILS=0
EXPECTED_PASSES=4
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

touch "$TMP_HOME/.claude/ironclaude-ppid-pending-$FOREIGN_PID"
PAYLOAD=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:"hello"}')

# The wrapper's PID is the hook's parent PID, so the marker it plants is the
# hook's own. The hook is not the wrapper's last command, so bash cannot exec it.
HOME="$TMP_HOME" bash -c '
  printf "%s" "$$" > "$3"
  touch "$HOME/.claude/ironclaude-ppid-pending-$$"
  bash "$1/state-activator.sh" <<< "$2" > /dev/null 2>&1
  exit 0' _ "$HOOKS_DIR" "$PAYLOAD" "$TMP_ROOT/own-pid"
OWN_PID=$(cat "$TMP_ROOT/own-pid")

if [ ! -e "$TMP_HOME/.claude/ironclaude-ppid-pending-$OWN_PID" ]; then
  ok "1 own marker consumed"
else
  bad "1 own marker consumed"
fi
if [ "$(cat "$TMP_HOME/.claude/ironclaude-session-$OWN_PID.id" 2>/dev/null)" = "$SESSION" ]; then
  ok "2 own session file holds this session id"
else
  bad "2 own session file holds this session id"
fi
if [ -e "$TMP_HOME/.claude/ironclaude-ppid-pending-$FOREIGN_PID" ]; then
  ok "3 foreign marker untouched"
else
  bad "3 foreign marker untouched"
fi
if [ ! -e "$TMP_HOME/.claude/ironclaude-session-$FOREIGN_PID.id" ]; then
  ok "4 no session file written for the foreign PID"
else
  bad "4 no session file written for the foreign PID"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
