#!/usr/bin/env bash
# test-warning-queue.sh - per-session warning queue (ic_queue_warning) and its
# UserPromptSubmit drain (ic_drain_warnings via state-activator.sh), plus GBTW's
# borderline-grade queueing (_gbtw_warn). v1.1.16 L2b-1 R2.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"; rm -f /tmp/.claude-block-throttle-ic-wq-session /tmp/.claude-bypass-counter-ic-wq-session' EXIT

SESSION="ic-wq-session"
QUEUE="$TMP_HOME/.claude/ironclaude-warnings-${SESSION}.queue"
PASSES=0
FAILS=0
EXPECTED_PASSES=7
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

queue() { HOME="$TMP_HOME" bash -c 'source "$1/hook-logger.sh"; ic_queue_warning "$2" "$3"' _ "$HOOKS_DIR" "$1" "$2"; }
drain() { HOME="$TMP_HOME" bash -c 'source "$1/hook-logger.sh"; ic_drain_warnings "$2"' _ "$HOOKS_DIR" "$1"; }

# 1. Queueing writes the per-session file.
queue "$SESSION" "W-ONE first"
queue "$SESSION" "W-TWO second"
[ -s "$QUEUE" ] && ok "1 queue file written" || bad "1 queue file written"

# 2. Draining emits one UserPromptSubmit additionalContext object with both, in order.
DRAIN_OUT=$(drain "$SESSION")
CTX=$(printf '%s' "$DRAIN_OUT" | jq -r 'select(.hookSpecificOutput.hookEventName == "UserPromptSubmit") | .hookSpecificOutput.additionalContext' 2>/dev/null)
case "$CTX" in
  *"W-ONE first"*"W-TWO second"*) ok "2 drain emits both warnings in order" ;;
  *) bad "2 drain emits both warnings in order (got: $DRAIN_OUT)" ;;
esac

# 3. Draining empties the queue: the file is gone and a second drain prints nothing.
SECOND=$(drain "$SESSION")
if [ ! -e "$QUEUE" ] && [ -z "$SECOND" ]; then ok "3 drain empties the queue"; else bad "3 drain empties the queue (second: $SECOND)"; fi

# 4. state-activator.sh (UserPromptSubmit) drains, and its stdout is exactly one JSON object.
queue "$SESSION" "W-THREE via hook"
UPS_IN=$(jq -cn --arg s "$SESSION" --arg c "$TMP_ROOT" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:"hello",cwd:$c}')
UPS_OUT=$(printf '%s' "$UPS_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/state-activator.sh" 2>/dev/null)
if printf '%s' "$UPS_OUT" | jq -cse 'length == 1 and (.[0].hookSpecificOutput.additionalContext | contains("W-THREE via hook"))' >/dev/null 2>&1; then
  ok "4 state-activator drains as its only stdout object"
else
  bad "4 state-activator drains as its only stdout object (got: $UPS_OUT)"
fi

# 5. GBTW's borderline grade is queued (both clients) and prints nothing to stdout.
WARN_OUT=$(HOME="$TMP_HOME" GBTW_TEST_MODE=1 bash -c 'source "$1/get-back-to-work-impl.sh"; SESSION_TAG="$2"; _gbtw_warn "rigor" "R-REASON" "raw"' _ "$HOOKS_DIR" "$SESSION")
if [ -z "$WARN_OUT" ] && grep -qF "[GET-BACK-TO-WORK] rigor borderline (C): R-REASON" "$QUEUE" 2>/dev/null; then
  ok "5 _gbtw_warn queues and keeps stdout empty"
else
  bad "5 _gbtw_warn queues and keeps stdout empty (stdout: $WARN_OUT)"
fi

# 6. No session id ("none") queues nothing.
queue "none" "W-NONE"
[ ! -e "$TMP_HOME/.claude/ironclaude-warnings-none.queue" ] && ok "6 session 'none' is not queued" || bad "6 session 'none' is not queued"

# 7. Escape hatch on Codex: the wrapper still answers {}, and the warning is queued.
#    (DB seed idiom from test-stop-wrapper.sh:154-156; it runs last so cases 1-6 see no DB.)
sqlite3 "$TMP_HOME/.claude/ironclaude.db" 'CREATE TABLE sessions (terminal_session TEXT PRIMARY KEY, professional_mode TEXT); INSERT INTO sessions VALUES ("ic-wq-session","on");'
printf '3:%s\n' "$(date +%s)" > /tmp/.claude-block-throttle-ic-wq-session
HATCH_OUT=$(printf '%s' '{"session_id":"ic-wq-session","hook_event_name":"Stop","stop_hook_active":true}' \
  | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" GBTW_FORCE_CODEX=1 bash "$HOOKS_DIR/get-back-to-work-claude.sh")
if [ "$HATCH_OUT" = "{}" ] && grep -qF "max blocks reached" "$QUEUE" 2>/dev/null; then
  ok "7 escape hatch queued on Codex (wrapper output {})"
else
  bad "7 escape hatch queued on Codex (wrapper output: $HATCH_OUT)"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
