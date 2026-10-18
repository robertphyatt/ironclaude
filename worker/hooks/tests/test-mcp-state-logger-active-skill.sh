#!/usr/bin/env bash
# test-mcp-state-logger-active-skill.sh - mcp-state-logger.sh sets active_skill
# from changed:true state-manager transitions, on both clients (Codex tool_output,
# Claude tool_response). v1.1.16 L2b-1 R3 port.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-msl-active-skill"
DB="$TMP_HOME/.claude/ironclaude.db"
PASSES=0
FAILS=0
EXPECTED_PASSES=16
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
skill() { sqlite3 "$DB" "SELECT COALESCE(active_skill, 'NULL') || '|' || memory_search_required FROM sessions WHERE terminal_session='${SESSION}';"; }
stamp() { sqlite3 "$DB" "SELECT updated_at FROM sessions WHERE terminal_session='${SESSION}';"; }
fire() {  # TOOL_SHORT_NAME CHANGED ENVELOPE(tool_output|tool_response)
  jq -cn --arg s "$SESSION" --arg t "mcp__plugin_ironclaude_state-manager__$1" --argjson c "$2" --arg e "$3" \
    '{session_id:$s,hook_event_name:"PostToolUse",tool_name:$t} + {($e): {changed:$c}}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/mcp-state-logger.sh" >/dev/null 2>&1
}
fire_payload() {  # TOOL_SHORT_NAME JSON_PAYLOAD ENVELOPE(default tool_output)
  jq -cn --arg s "$SESSION" --arg t "mcp__plugin_ironclaude_state-manager__$1" --argjson p "$2" --arg e "${3:-tool_output}" \
    '{session_id:$s,hook_event_name:"PostToolUse",tool_name:$t} + {($e):$p}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/mcp-state-logger.sh" >/dev/null 2>&1
}
check() {  # LABEL EXPECTED_SKILL_AND_FLAG
  [ "$(skill)" = "$2" ] && ok "$1" || bad "$1 (got: $(skill), expected: $2)"
}

printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"

fire mark_brainstorming true tool_output
[ "$(skill)" = "brainstorming|0" ] && ok "1 mark_brainstorming (Codex envelope) -> brainstorming, memory flag untouched" || bad "1 mark_brainstorming -> brainstorming (got: $(skill))"

fire mark_design_ready true tool_response
[ "$(skill)" = "NULL|0" ] && ok "2 mark_design_ready (Claude envelope) -> NULL" || bad "2 mark_design_ready -> NULL (got: $(skill))"

fire start_execution true tool_output
[ "$(skill)" = "executing-plans|0" ] && ok "3 start_execution -> executing-plans" || bad "3 start_execution -> executing-plans (got: $(skill))"

fire mark_brainstorming false tool_output
[ "$(skill)" = "executing-plans|0" ] && ok "4 changed:false leaves active_skill" || bad "4 changed:false leaves active_skill (got: $(skill))"

fire create_plan true tool_output
[ "$(skill)" = "executing-plans|0" ] && ok "5 unmapped transition leaves active_skill" || bad "5 unmapped transition leaves active_skill (got: $(skill))"

fire mark_debugging true tool_response
[ "$(skill)" = "systematic-debugging|0" ] && ok "6 mark_debugging -> systematic-debugging" || bad "6 mark_debugging -> systematic-debugging (got: $(skill))"

sqlite3 "$DB" "UPDATE sessions SET active_skill='systematic-debugging' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"changed":true,"from":"debugging","to":"brainstorming"}'
check "7 retreat direct object to brainstorming" "brainstorming|0"
sqlite3 "$DB" "UPDATE sessions SET active_skill='brainstorming' WHERE terminal_session='${SESSION}';"
fire_payload retreat '"{\"changed\":true,\"from\":\"brainstorming\",\"to\":\"debugging\"}"' tool_response
check "8 retreat JSON string to debugging" "systematic-debugging|0"
sqlite3 "$DB" "UPDATE sessions SET active_skill='systematic-debugging' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"content":[{"type":"text","text":"{\"changed\":true,\"from\":\"debugging\",\"to\":\"brainstorming\"}"}]}'
check "9 retreat MCP text to brainstorming" "brainstorming|0"
sqlite3 "$DB" "UPDATE sessions SET active_skill='brainstorming' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"content":[{"type":"text","text":"{\"changed\":true,\"from\":\"brainstorming\",\"to\":\"debugging\"}"}]}' tool_response
check "10 retreat MCP text to debugging" "systematic-debugging|0"
fire_payload retreat '{"changed":false,"to":"brainstorming"}'
check "11 retreat changed:false preserves state" "systematic-debugging|0"
fire_payload retreat '"malformed JSON"'
check "12 malformed retreat preserves state" "systematic-debugging|0"
fire_payload retreat '{"isError":true,"content":[{"type":"text","text":"{\"changed\":true,\"to\":\"brainstorming\"}"}]}'
check "13 isError retreat preserves state" "systematic-debugging|0"
fire_payload retreat '{"changed":true,"to":"execution_complete"}'
check "14 unsupported retreat destination preserves state" "systematic-debugging|0"
sqlite3 "$DB" "UPDATE sessions SET active_skill='systematic-debugging' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"changed":true,"to":"brainstorming"}'
sqlite3 "$DB" "UPDATE sessions SET updated_at='2000-01-01 00:00:00' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"changed":false,"from":"brainstorming","to":"brainstorming"}'
[ "$(skill)" = "brainstorming|0" ] && [ "$(stamp)" = "2000-01-01 00:00:00" ] && ok "15 same-target brainstorming no-op leaves state and timestamp unchanged" || bad "15 same-target brainstorming no-op changed state or timestamp (got: $(skill), $(stamp))"
sqlite3 "$DB" "UPDATE sessions SET active_skill='brainstorming' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"changed":true,"to":"debugging"}'
sqlite3 "$DB" "UPDATE sessions SET updated_at='2000-01-01 00:00:00' WHERE terminal_session='${SESSION}';"
fire_payload retreat '{"changed":false,"from":"debugging","to":"debugging"}'
[ "$(skill)" = "systematic-debugging|0" ] && [ "$(stamp)" = "2000-01-01 00:00:00" ] && ok "16 same-target debugging no-op leaves state and timestamp unchanged" || bad "16 same-target debugging no-op changed state or timestamp (got: $(skill), $(stamp))"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
