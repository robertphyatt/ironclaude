#!/bin/bash
# skill-state-bridge.sh — PreToolUse hook
# Detects Skill tool invocations and posts events to MCP state manager.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "skill-state-bridge"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Only process Skill tool
if [ "$TOOL_NAME" != "Skill" ]; then
  exit 0
fi

SKILL_NAME=$(echo "$INPUT" | jq -r '.tool_input.skill // empty' 2>/dev/null || true)

if [ -z "$SKILL_NAME" ]; then
  exit 0
fi

# Normalize: strip namespace prefix (e.g., ironclaude:brainstorming -> brainstorming)
SKILL_NAME="${SKILL_NAME##*:}"

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
SAFE_SKILL_NAME=$(echo "$SKILL_NAME" | sed "s/'/''/g")

# Read current workflow_stage to validate transition (H2 fix)
CURRENT_STAGE=""
if [ -f "$DB_PATH" ]; then
  CURRENT_STAGE=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null) || true
fi

# Map skill name to DB updates (migrated from handleSkillActivated in http-routes.ts)
STAGE_CHANGE=""
case "$SKILL_NAME" in
  brainstorming)
    UPDATES="active_skill='brainstorming', workflow_stage='brainstorming', memory_search_required=1"
    STAGE_CHANGE="workflow_stage=brainstorming"
    ;;
  systematic-debugging)
    UPDATES="active_skill='systematic-debugging', workflow_stage='debugging'"
    STAGE_CHANGE="workflow_stage=debugging"
    ;;
  writing-plans)
    UPDATES="active_skill='writing-plans', workflow_stage='design_marked_for_use', memory_search_required=1"
    STAGE_CHANGE="workflow_stage=design_marked_for_use"
    ;;
  executing-plans)
    UPDATES="active_skill='executing-plans', workflow_stage='plan_marked_for_use'"
    STAGE_CHANGE="workflow_stage=plan_marked_for_use"
    ;;
  code-review)
    UPDATES="active_skill='code-review', workflow_stage='reviewing', testing_theatre_checked=0"
    STAGE_CHANGE="workflow_stage=reviewing"
    ;;
  plan-interruption)
    UPDATES="active_skill='plan-interruption', workflow_stage='plan_interrupted'"
    STAGE_CHANGE="workflow_stage=plan_interrupted"
    ;;
  testing-theatre-detection)
    UPDATES="active_skill='testing-theatre-detection', testing_theatre_checked=1"
    ;;
  *)
    UPDATES="active_skill='${SAFE_SKILL_NAME}'"
    ;;
esac

# Validate transition is legal per state machine (H2 fix)
# Warn but don't block — skills may need to activate post-compaction
if [ -n "$STAGE_CHANGE" ] && [ -n "$CURRENT_STAGE" ]; then
  TARGET_STAGE="${STAGE_CHANGE#workflow_stage=}"
  if [ "$CURRENT_STAGE" != "$TARGET_STAGE" ]; then
    VALID="false"
    case "$TARGET_STAGE" in
      brainstorming|debugging)
        # Reachable from most stages (forward + retreat)
        case "$CURRENT_STAGE" in
          idle|brainstorming|debugging|design_ready|design_marked_for_use|plan_ready|plan_marked_for_use|final_plan_prep|executing|reviewing|plan_interrupted|execution_complete)
            VALID="true" ;;
        esac
        ;;
      design_marked_for_use)
        [ "$CURRENT_STAGE" = "design_ready" ] && VALID="true" ;;
      plan_marked_for_use)
        [ "$CURRENT_STAGE" = "plan_ready" ] && VALID="true" ;;
      reviewing)
        [ "$CURRENT_STAGE" = "executing" ] && VALID="true" ;;
      plan_interrupted)
        [ "$CURRENT_STAGE" = "executing" ] && VALID="true" ;;
    esac

    if [ "$VALID" = "false" ]; then
      # brainstorming/debugging are reachable from many states (post-compaction recovery) — warn only
      case "$TARGET_STAGE" in
        brainstorming|debugging)
          log_hook "skill-state-bridge" "Warning" "Suspect transition: ${CURRENT_STAGE} -> ${TARGET_STAGE} (skill=${SKILL_NAME})"
          ;;
        *)
          block_pretooluse "skill-state-bridge" "BLOCKED — INVALID WORKFLOW TRANSITION

Cannot transition from '${CURRENT_STAGE}' to '${TARGET_STAGE}' (skill=${SKILL_NAME}).

Valid source states for '${TARGET_STAGE}':
$(case "$TARGET_STAGE" in
    reviewing) echo '  - executing' ;;
    plan_interrupted) echo '  - executing' ;;
    design_marked_for_use) echo '  - design_ready' ;;
    plan_marked_for_use) echo '  - plan_ready' ;;
    *) echo '  - (see state machine)' ;;
  esac)

Current state: ${CURRENT_STAGE}

If you need to run this skill, first transition to the correct workflow stage."
          ;;
      esac
    fi
  fi
fi

(db_write_or_fail "skill-state-bridge" \
  "UPDATE sessions SET ${UPDATES}, updated_at=datetime('now') WHERE terminal_session='${SAFE_SESSION}';") || {
  block_pretooluse "skill-state-bridge" "BLOCKED — DATABASE ERROR

Cannot update session state in the database. This is a temporary error.

Try invoking the skill again. If this persists, report the error to the user."
}

db_audit_log "hook:skill-state-bridge" "skill_activated" "" "$SKILL_NAME" ""

if [ -n "$STAGE_CHANGE" ]; then
  log_hook "skill-state-bridge" "Activated" "skill=$SKILL_NAME → $STAGE_CHANGE"
else
  log_hook "skill-state-bridge" "Activated" "skill=$SKILL_NAME"
fi
exit 0
