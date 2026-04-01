#!/bin/bash
# plan-task-context.sh - PreToolUse Hook
# Injects current wave-task context before every tool call during plan execution.
# Enforces review_pending blocking (agent must invoke code-review between tasks).
# Reads state directly from SQLite — no state-manager.sh dependency.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "PLAN-TASK-CONTEXT"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# ─── Read session state from SQLite ───
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
ROW=$(db_read_or_fail "PLAN-TASK-CONTEXT" \
  "SELECT professional_mode, workflow_stage, review_pending FROM sessions WHERE terminal_session='${SAFE_SESSION}';") || {
  exit 0
}
IFS='|' read -r PROF_MODE WORKFLOW REVIEW_PENDING <<< "$ROW"

# Gate: professional_mode must be 'on' AND workflow_stage must be 'executing'
if [ "$PROF_MODE" != "on" ] || [ "$WORKFLOW" != "executing" ]; then
  exit 0
fi

# ─── REVIEW-PENDING ENFORCEMENT ───
if [ "$REVIEW_PENDING" = "1" ]; then
  case "$TOOL_NAME" in
    Read|Grep|Glob|Skill) ;; # Allow — needed for code review
    *)
      block_pretooluse "PLAN-TASK-CONTEXT" "BLOCKED — CODE REVIEW REQUIRED

You must run code review before continuing with the next task.

Call the Skill tool with:
  skill: \"ironclaude:code-review\"
  args: \"--task-boundary\"

Do NOT proceed to the next task without running code review."
      ;;
  esac
fi

# ─── WAVE TASK CONTEXT INJECTION ───
WAVE_NUM=$(sqlite3 "$DB_PATH" \
  "SELECT current_wave FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>&1 || echo "0")
SAFE_WAVE_NUM=$(echo "$WAVE_NUM" | sed "s/'/''/g")

WAVE_INFO=$(sqlite3 "$DB_PATH" \
  "SELECT task_id, task_name, status FROM wave_tasks WHERE terminal_session='${SAFE_SESSION}' AND wave_number='${SAFE_WAVE_NUM}';" 2>&1 || true)

if [ -n "$WAVE_INFO" ]; then
  log_hook "PLAN-TASK-CONTEXT" "Active" "wave ${WAVE_NUM}"
  {
    echo "━━━ Wave ${WAVE_NUM} Tasks ━━━"
    echo "$WAVE_INFO" | while IFS='|' read -r tid tname tstatus; do
      echo "  Task ${tid}: ${tname} [${tstatus}]"
    done
    echo "━━━━━━━━━━━━━━━━━━━━"
  } >&2
fi

exit 0
