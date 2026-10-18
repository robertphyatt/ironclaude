#!/usr/bin/env bash
# test-state-activator-single-object.sh - state-activator.sh prints at most one JSON
# object per run, and only as UserPromptSubmit additionalContext: systemMessage texts
# and the warning drain reach the model on both clients; the MCP error sideband is
# not delivered (v1.1.16 L2b-1b R4, L2b-1c R2, L2b-1d).
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'chmod 755 "$TMP_HOME/.claude" 2>/dev/null; rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-sa-single-object"
PASSES=0
FAILS=0
EXPECTED_PASSES=6
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
# Exactly one object, whose only key is a UserPromptSubmit hookSpecificOutput.
CTX_ONLY='length == 1 and (.[0] | keys) == ["hookSpecificOutput"] and .[0].hookSpecificOutput.hookEventName == "UserPromptSubmit"'

run_prompt() {  # PROMPT -> stdout; exit code in $TMP_ROOT/rc
  jq -cn --arg s "$SESSION" --arg p "$1" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:$p}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/state-activator.sh" 2>/dev/null
  echo $? > "$TMP_ROOT/rc"
}

# A queued warning plus a forced log_error: "/commit" with professional mode not on
# reaches state-activator.sh's "Human intent issuance requires professional mode on" log_error.
HOME="$TMP_HOME" bash -c 'source "$1/hook-logger.sh"; ic_queue_warning "$2" "W-F4 queued warning"' _ "$HOOKS_DIR" "$SESSION"
OUT=$(run_prompt "/commit")
RC=$(cat "$TMP_ROOT/rc")

# 1. Exactly one object, context only (no systemMessage key).
printf '%s' "$OUT" | jq -cse "$CTX_ONLY" >/dev/null 2>&1 && ok "1 exactly one context-only object" || bad "1 exactly one context-only object (got: $OUT)"

# 2. The context carries both texts: the log_error and the queued warning.
if printf '%s' "$OUT" | jq -cse "$CTX_ONLY
    and (.[0].hookSpecificOutput.additionalContext | contains(\"Human intent issuance requires professional mode on\"))
    and (.[0].hookSpecificOutput.additionalContext | contains(\"W-F4 queued warning\"))" >/dev/null 2>&1; then
  ok "2 context holds the error and the queued warning"
else
  bad "2 context holds the error and the queued warning (got: $OUT)"
fi

# 3. The exit code is preserved.
[ "$RC" = "0" ] && ok "3 exit code 0 preserved" || bad "3 exit code 0 preserved (rc=$RC)"

# 4. Nothing to say -> no output at all.
EMPTY=$(run_prompt "hello")
[ -z "$EMPTY" ] && ok "4 empty buffer prints nothing" || bad "4 empty buffer prints nothing (got: $EMPTY)"

# 5. L2b-1d: the MCP error sideband is not delivered. ironclaude-errors.log is global
#    across sessions and every line was already returned to the calling model as its
#    tool result, so a line there produces no output and stays in the file for the
#    operator (session-init.sh truncates it at session start).
printf 'MCP-LINE-77 sideband failure\n' > "$TMP_HOME/.claude/ironclaude-errors.log"
MCP_OUT=$(run_prompt "hello")
if [ -z "$MCP_OUT" ] && grep -qxF "MCP-LINE-77 sideband failure" "$TMP_HOME/.claude/ironclaude-errors.log"; then
  ok "5 MCP sideband is not delivered and the log is left for the operator"
else
  bad "5 MCP sideband is not delivered and the log is left for the operator (got: $MCP_OUT)"
fi

# 6. ERR path: the hook's OWN pending PPID marker in a read-only ~/.claude makes
#    the unguarded mktemp in the marker handshake fail under run_hook's ERR trap,
#    which logs and exits 1. The flush keeps exit code 1 and delivers the failure
#    as context. The hook claims only its own marker, so a wrapper whose PID is
#    the hook's parent PID plants it; the hook is not the wrapper's last command.
ERR_PAYLOAD=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:"hello"}')
ERR_OUT=$(HOME="$TMP_HOME" bash -c '
  touch "$HOME/.claude/ironclaude-ppid-pending-$$"
  chmod 555 "$HOME/.claude"
  bash "$1/state-activator.sh" <<< "$2" 2>/dev/null
  echo $? > "$3"
  chmod 755 "$HOME/.claude"
  exit 0' _ "$HOOKS_DIR" "$ERR_PAYLOAD" "$TMP_ROOT/rc")
ERR_RC=$(cat "$TMP_ROOT/rc")
if [ "$ERR_RC" = "1" ] && printf '%s' "$ERR_OUT" | jq -cse "$CTX_ONLY and (.[0].hookSpecificOutput.additionalContext | contains(\"Unexpected failure\"))" >/dev/null 2>&1; then
  ok "6 ERR path exits 1 with the failure in context"
else
  bad "6 ERR path exits 1 with the failure in context (rc=$ERR_RC, got: $ERR_OUT)"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
