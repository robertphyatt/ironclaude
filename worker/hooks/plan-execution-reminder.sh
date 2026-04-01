#!/bin/bash
# plan-execution-reminder.sh - UserPromptSubmit Hook
# Injects active plan reminder into Claude's context on every user message.
# Reads state directly from SQLite — no state-manager.sh dependency.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "PLAN-EXECUTION-REMINDER"

INPUT=$(cat)
init_session_id

# ─── Read session state from SQLite ───
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
ROW=$(db_read_or_fail "PLAN-EXECUTION-REMINDER" \
  "SELECT professional_mode, workflow_stage, plan_name, current_wave FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
IFS='|' read -r PROF_MODE WORKFLOW PLAN_NAME CURRENT_WAVE <<< "$ROW"
# Fetch plan_json separately — it may contain pipe characters that break IFS parsing
PLAN_JSON=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
  "SELECT plan_json FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true)

# Gate: professional mode must be 'on'
if [ "$PROF_MODE" != "on" ]; then
  log_hook "PLAN-EXECUTION-REMINDER" "Disabled" "professional mode off"
  exit 0
fi

# Gate: must be executing a plan
if ! is_plan_active "$WORKFLOW"; then
  log_hook "PLAN-EXECUTION-REMINDER" "Passed" "not executing a plan"
  exit 0
fi

# Extract plan context
PLAN_SUMMARY=""
TOTAL_TASKS="?"
if command -v jq &>/dev/null && [ -n "$PLAN_JSON" ]; then
  PLAN_SUMMARY=$(echo "$PLAN_JSON" | jq -r '.summary // ""' 2>/dev/null || true)
  TOTAL_TASKS=$(echo "$PLAN_JSON" | jq -r '.tasks | length // "?"' 2>/dev/null || echo "?")
fi

PROGRESS="Wave ${CURRENT_WAVE:-?} of ${TOTAL_TASKS} tasks"
PROGRESS_MSG="Plan: ${PLAN_NAME:-unknown} (${PROGRESS})"
[ -n "$PLAN_SUMMARY" ] && PROGRESS_MSG="${PROGRESS_MSG} - ${PLAN_SUMMARY}"

log_hook "PLAN-EXECUTION-REMINDER" "Active" "$PROGRESS_MSG"

cat << EOF >&2
━━━ Active Plan ━━━
Plan: ${PLAN_NAME:-unknown} (${PROGRESS})
${PLAN_SUMMARY:+Summary: $PLAN_SUMMARY}
If this message changes topic, invoke /plan-interruption first.
━━━━━━━━━━━━━━━━━━━
EOF

exit 0
