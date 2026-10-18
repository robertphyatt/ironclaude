#!/usr/bin/env bash
# test-memory-search-gate.sh - the brainstorming memory-search gate, the same on
# Claude and Codex (v1.1.16 L2b-2 R5, R6). It arms on a new effort only
# (mcp-state-logger.sh on mark_brainstorming; skill-state-bridge.sh on the
# brainstorming Skill), never for writing-plans. It clears on an episodic-memory MCP
# search (both clients' measured tool names) or a search-conversations Agent
# dispatch. hooks.json routes those calls to the tracker. GBTW gates brainstorming
# only, and its block message names both clients' search paths.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-memory-search-gate"
DB="$TMP_HOME/.claude/ironclaude.db"
CLAUDE_SEARCH="mcp__plugin_ironclaude_episodic-memory__search"
CODEX_SEARCH="mcp__episodic_memory__search"
CLAUDE_BRAIN="mcp__plugin_ironclaude_state-manager__mark_brainstorming"
CODEX_BRAIN="mcp__state_manager__mark_brainstorming"
PASSES=0
FAILS=0
EXPECTED_PASSES=23
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
flag() { sqlite3 "$DB" "SELECT memory_search_required FROM sessions WHERE terminal_session='${SESSION}';"; }
row() {  # STAGE FLAG
  sqlite3 "$DB" "UPDATE sessions SET workflow_stage='$1', memory_search_required=$2 WHERE terminal_session='${SESSION}';"
}
check() {  # LABEL EXPECTED_FLAG
  local got
  got=$(flag)
  if [ "$got" = "$2" ]; then ok "$1"; else bad "$1 (flag=$got, expected $2)"; fi
}
logger() {  # TOOL_NAME CHANGED FROM ('-' = no from field); state-manager text envelope
  local body
  if [ "$3" = "-" ]; then
    body=$(jq -cn --argjson c "$2" '{success:true,changed:$c}')
  else
    body=$(jq -cn --argjson c "$2" --arg f "$3" '{success:true,changed:$c,from:$f}')
  fi
  jq -cn --arg s "$SESSION" --arg t "$1" --arg b "$body" \
    '{session_id:$s,hook_event_name:"PostToolUse",tool_name:$t,tool_response:{content:[{type:"text",text:$b}]}}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/mcp-state-logger.sh" >/dev/null 2>&1
}
logger_payload() {  # TOOL_NAME JSON_PAYLOAD
  jq -cn --arg s "$SESSION" --arg t "$1" --argjson p "$2" \
    '{session_id:$s,hook_event_name:"PostToolUse",tool_name:$t,tool_response:$p}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/mcp-state-logger.sh" >/dev/null 2>&1
}
bridge() {  # SKILL
  jq -cn --arg s "$SESSION" --arg k "$1" \
    '{session_id:$s,hook_event_name:"PreToolUse",tool_name:"Skill",tool_input:{skill:$k}}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/skill-state-bridge.sh" >/dev/null 2>&1
}
tracker() {  # TOOL_NAME SUBAGENT_TYPE
  jq -cn --arg s "$SESSION" --arg t "$1" --arg a "$2" \
    '{session_id:$s,hook_event_name:"PostToolUse",tool_name:$t,tool_input:{subagent_type:$a,query:"probe"},tool_response:{}}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/memory-search-tracker.sh" >/dev/null 2>&1
}
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1

# Arm: mcp-state-logger.sh on a changed:true mark_brainstorming that starts a new effort.
row idle 0; logger "$CLAUDE_BRAIN" true idle
check "1 Claude spelling, new effort from idle arms" 1
row execution_complete 0; logger "$CODEX_BRAIN" true execution_complete
check "2 Codex spelling, new effort from execution_complete arms" 1
row debugging 0; logger "$CODEX_BRAIN" true debugging
check "3 resume from debugging does not arm" 0
row idle 0; logger "$CLAUDE_BRAIN" false idle
check "4 changed:false does not arm" 0
row idle 0; logger "$CLAUDE_BRAIN" true -
check "5 response without from does not arm" 0
row brainstorming 0; logger "mcp__state_manager__mark_design_ready" true brainstorming
check "6 mark_design_ready does not arm" 0
row debugging 0; logger_payload "mcp__state_manager__retreat" '{"changed":true,"from":"debugging","to":"brainstorming"}'
check "20 retreat to brainstorming from debugging does not arm" 0
row execution_complete 0; logger_payload "mcp__state_manager__retreat" '{"changed":true,"from":"execution_complete","to":"brainstorming"}'
check "21 retreat to brainstorming from completed execution does not arm" 0
row brainstorming 1; logger_payload "mcp__state_manager__retreat" '{"changed":true,"from":"brainstorming","to":"debugging"}'
check "22 retreat to debugging leaves armed flag unchanged" 1
row idle 0; logger_payload "$CODEX_BRAIN" '{"isError":true,"content":[{"type":"text","text":"{\"changed\":true,\"from\":\"idle\",\"to\":\"brainstorming\"}"}]}'
check "23 error result cannot arm gate" 0

# Arm: skill-state-bridge.sh (Claude's Skill path).
row idle 0; bridge "ironclaude:brainstorming"
check "7 brainstorming Skill from idle arms" 1
row debugging 0; bridge "ironclaude:brainstorming"
check "8 brainstorming Skill from debugging does not arm" 0
row design_ready 0; bridge "ironclaude:writing-plans"
check "9 writing-plans Skill does not arm" 0

# Clear: memory-search-tracker.sh.
row brainstorming 1; tracker "$CLAUDE_SEARCH" ""
check "10 Claude episodic-memory search clears" 0
row brainstorming 1; tracker "$CODEX_SEARCH" ""
check "11 Codex episodic-memory search clears" 0
row brainstorming 1; tracker "Agent" "ironclaude:search-conversations"
check "12 search-conversations Agent dispatch clears" 0
row brainstorming 1; tracker "${CLAUDE_SEARCH%search}read" ""; tracker "${CODEX_SEARCH%search}read" ""
check "13 episodic-memory read does not clear" 1
row brainstorming 1; tracker "Agent" "general-purpose"; tracker "TaskOutput" ""
check "14 other Agent dispatch and TaskOutput do not clear" 1

# Route: exactly one tracker entry, admitting the search tools, Task and Agent only.
M=$(jq -r '.hooks.PostToolUse[] | select(.hooks[].command | contains("memory-search-tracker.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
N=$(printf '%s\n' "$M" | grep -c .)
if [ "$N" = "1" ] && matches "$M" "Task" && matches "$M" "Agent" && matches "$M" "$CLAUDE_SEARCH" && matches "$M" "$CODEX_SEARCH"; then
  ok "15 tracker matcher admits Task, Agent and both search tools"
else
  bad "15 tracker matcher admits Task, Agent and both search tools (entries=$N matcher: $M)"
fi
if [ "$N" = "1" ] && ! matches "$M" "TaskOutput" && ! matches "$M" "${CLAUDE_SEARCH%search}read" \
   && ! matches "$M" "${CODEX_SEARCH%search}read" && ! matches "$M" "$CODEX_BRAIN"; then
  ok "16 tracker matcher rejects TaskOutput, episodic read and state-manager tools"
else
  bad "16 tracker matcher rejects TaskOutput, episodic read and state-manager tools (matcher: $M)"
fi

# GBTW: brainstorming-only gate; the message names both clients' search paths.
if GBTW_TEST_MODE=1 bash -c 'source "$1/get-back-to-work-claude.sh" >/dev/null 2>&1; _gbtw_memory_gate_applies brainstorming && ! _gbtw_memory_gate_applies writing-plans && ! _gbtw_memory_gate_applies ""' _ "$HOOKS_DIR" >/dev/null 2>&1; then
  ok "17 GBTW memory gate applies to brainstorming only"
else
  bad "17 GBTW memory gate applies to brainstorming only"
fi
MSG=$(GBTW_TEST_MODE=1 bash -c 'source "$1/get-back-to-work-claude.sh" >/dev/null 2>&1; printf "%s" "$_IC_MSG_MEMORY_SEARCH"' _ "$HOOKS_DIR")
if printf '%s' "$MSG" | grep -qF 'subagent_type: "ironclaude:search-conversations"' \
   && printf '%s' "$MSG" | grep -qF "episodic-memory MCP server's search tool"; then
  ok "18 block message names both clients' search paths"
else
  bad "18 block message names both clients' search paths (got: $MSG)"
fi
IMPL="$HOOKS_DIR/get-back-to-work-impl.sh"
if [ "$(grep -cF 'if _gbtw_memory_gate_applies "$ACTIVE_SKILL"; then' "$IMPL")" = "1" ] && ! grep -qF 'writing-plans)$' "$IMPL"; then
  ok "19 GBTW decision site uses the helper; no writing-plans gate regex"
else
  bad "19 GBTW decision site uses the helper; no writing-plans gate regex"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
