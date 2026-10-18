#!/bin/bash
# mcp-state-logger.sh — PostToolUse hook
# Logs workflow_stage transitions triggered by MCP state-manager tools, and sets
# sessions.active_skill from them on both clients (see the active_skill block below).
# Fires after: mark_design_ready, mark_plan_ready, mark_brainstorming,
#              mark_executing, create_plan, start_execution, retreat
#
# Best-effort: if DB read fails, emits a warning rather than hard-failing.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "mcp-state-logger"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Extract the short tool name (last segment after __)
SHORT_TOOL="${TOOL_NAME##*__}"

# Only state-manager responses carry workflow transition results.  Ignore every
# other post-tool event before touching the database or emitting hook output.
case "$TOOL_NAME" in
  *state_manager__*|*state-manager__*) ;;
  *) exit 0 ;;
esac

# Codex and Claude hook payloads use different response envelopes. Decode the
# transition once so the skill mapping and memory gate use the same verified
# result. Error, no-op, and malformed responses are silent.
TRANSITION=$(printf '%s' "$INPUT" | jq -cer '
  def decode:
    if type == "object" then .
    elif type == "string" then fromjson?
    else null
    end;
  def transition:
    decode as $decoded |
    if ($decoded | type) != "object" or $decoded.isError == true then empty
    elif ($decoded | has("changed")) then $decoded
    elif (($decoded.content? | type) == "array") then
      first(
        $decoded.content[]?
        | select((.type? == "text") and (.text? | type == "string"))
        | .text
        | fromjson?
        | select(type == "object" and .isError != true and has("changed"))
      )
    else empty
    end;
  (if has("tool_output") then .tool_output
   elif has("tool_response") then .tool_response
   else null end)
  | transition
  | select(.changed == true)
' 2>/dev/null || true)

if [ -z "$TRANSITION" ]; then
  exit 0
fi

# Cross-client active_skill (v1.1.16 L2b-1 R3 port; docs/plans/2026-10-03-skill-task-hook-audit.md).
# skill-state-bridge.sh sets active_skill only for Claude's Skill tool; these MCP
# transitions run on both clients. GBTW's firing matrix reads active_skill.
# memory_search_required is armed by the separate block below (L2b-2 R5), not here.
# Writes only when the value differs and leaves updated_at alone, so an equal value is a no-op.
NEW_SKILL_SQL=""
case "$SHORT_TOOL" in
  mark_brainstorming) NEW_SKILL_SQL="'brainstorming'" ;;
  mark_debugging) NEW_SKILL_SQL="'systematic-debugging'" ;;
  mark_executing|start_execution) NEW_SKILL_SQL="'executing-plans'" ;;
  mark_design_ready|mark_plan_ready) NEW_SKILL_SQL="NULL" ;;
  retreat)
    TO_STAGE=$(printf '%s' "$TRANSITION" | jq -r '.to | strings' 2>/dev/null || true)
    case "$TO_STAGE" in
      brainstorming) NEW_SKILL_SQL="'brainstorming'" ;;
      debugging) NEW_SKILL_SQL="'systematic-debugging'" ;;
    esac
    ;;
esac
if [ -n "$NEW_SKILL_SQL" ] && [ -n "$SESSION_TAG" ] && [ "$SESSION_TAG" != "none" ] && [ -f "$DB_PATH" ]; then
  SKILL_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  sqlite3 "$DB_PATH" ".timeout 10000" \
    "UPDATE sessions SET active_skill=${NEW_SKILL_SQL} WHERE terminal_session='${SKILL_SESSION}' AND active_skill IS NOT ${NEW_SKILL_SQL};" \
    2>/dev/null || log_hook "mcp-state-logger" "Skipped" "active_skill update failed after $SHORT_TOOL"
fi

# Brainstorming memory-search gate arm (v1.1.16 L2b-2 R5), on both clients. A
# changed:true mark_brainstorming that starts a new effort arms the gate. The
# response's `from` is the prior stage. A resume from debugging or brainstorming,
# or a response without `from`, does not arm. Writes only when the value differs
# and leaves updated_at alone. memory-search-tracker.sh clears the flag.
if [ "$SHORT_TOOL" = "mark_brainstorming" ] && [ -n "$SESSION_TAG" ] && [ "$SESSION_TAG" != "none" ] && [ -f "$DB_PATH" ]; then
  FROM_STAGE=$(printf '%s' "$TRANSITION" | jq -r '.from | strings' 2>/dev/null || true)
  case "$FROM_STAGE" in
    ''|debugging|brainstorming) ;;
    *)
      ARM_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
      sqlite3 "$DB_PATH" ".timeout 10000" \
        "UPDATE sessions SET memory_search_required=1 WHERE terminal_session='${ARM_SESSION}' AND memory_search_required IS NOT 1;" \
        2>/dev/null || log_hook "mcp-state-logger" "Skipped" "memory_search_required arm failed after $SHORT_TOOL"
      ;;
  esac
fi

# Read current workflow_stage from DB (best-effort, no hard-fail)
STAGE=""
if [ -n "$SESSION_TAG" ] && [ "$SESSION_TAG" != "none" ] && [ -f "$DB_PATH" ]; then
  SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  STAGE=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}' LIMIT 1;" \
    2>/dev/null || true)
fi

if [ -z "$STAGE" ]; then
  log_warning "mcp-state-logger" "Could not read workflow_stage after $SHORT_TOOL"
  exit 0
fi

log_hook "mcp-state-logger" "Transition" "tool=$SHORT_TOOL → workflow_stage=$STAGE"
exit 0
