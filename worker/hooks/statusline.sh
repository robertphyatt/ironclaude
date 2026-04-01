#!/bin/bash
#
# statusline.sh - Claude Code status line provider
#
# Displays plugin version, professional mode status, and workflow state.
# Claude Code pipes JSON metadata via stdin and expects colored text on stdout.
#
# Format: ironclaude v1.0.4 | Professional Mode: ON | Status: idle | <session_id>
#
# Color scheme:
#   Professional Mode: green=ON, red=OFF, yellow=UNDECIDED/UNKNOWN
#   Workflow Status: green=idle, bright_green=execution_complete, cyan=brainstorming, yellow=design_ready,
#                    orange=plan_ready, red=executing, magenta=debugging, pink=plan_interrupted

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_JSON="$SCRIPT_DIR/../.claude-plugin/plugin.json"
DB_PATH="$HOME/.claude/ironclaude.db"

# Read version from plugin.json
VERSION="?.?.?"
if command -v jq &>/dev/null && [ -f "$PLUGIN_JSON" ]; then
  VERSION=$(jq -r '.version // "?.?.?"' "$PLUGIN_JSON" 2>/dev/null || echo "?.?.?")
fi

# Read stdin JSON from Claude Code and parse session_id
INPUT=$(cat)
SESSION_ID=""
if command -v jq &>/dev/null; then
  SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
fi

# Query DB for session state
PROF_MODE=""
WORKFLOW=""

if [ -n "$SESSION_ID" ] && [ -f "$DB_PATH" ]; then
  SAFE_SESSION=$(echo "$SESSION_ID" | sed "s/'/''/g")
  ROW=$(sqlite3 "$DB_PATH" ".timeout 10000" "SELECT professional_mode, workflow_stage FROM sessions WHERE terminal_session = '${SAFE_SESSION}' LIMIT 1;" 2>/dev/null || true)
  if [ -n "$ROW" ]; then
    PROF_MODE=$(echo "$ROW" | cut -d'|' -f1)
    WORKFLOW=$(echo "$ROW" | cut -d'|' -f2)
  fi
fi

# Defaults for missing data
PROF_MODE="${PROF_MODE:-UNKNOWN}"
WORKFLOW="${WORKFLOW:-unknown}"

# ANSI codes
RESET='\033[0m'
GREEN='\033[32m'
RED='\033[31m'
YELLOW='\033[33m'
CYAN='\033[36m'
MAGENTA='\033[35m'
ORANGE='\033[38;5;208m'
PINK='\033[38;5;213m'
BRIGHT_GREEN='\033[92m'

# Read config flag for session ID display
CONFIG_FILE="$HOME/.claude/ironclaude-hooks-config.json"
LOG_SESSION_IDS="true"
if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
  FLAG=$(jq -r '.log_session_ids // empty' "$CONFIG_FILE" 2>/dev/null || true)
  if [ "$FLAG" = "false" ]; then
    LOG_SESSION_IDS="false"
  fi
fi

# Professional mode color (always the same regardless of workflow)
case "$PROF_MODE" in
  "on")        PROF_COLOR="$GREEN";  PROF_LABEL="ON" ;;
  "off")       PROF_COLOR="$RED";    PROF_LABEL="OFF" ;;
  "undecided") PROF_COLOR="$YELLOW"; PROF_LABEL="UNDECIDED" ;;
  *)           PROF_COLOR="$YELLOW"; PROF_LABEL="UNKNOWN" ;;
esac

# Workflow color (independent of professional mode)
case "$WORKFLOW" in
  "idle")          WORK_COLOR="$GREEN" ;;
  "brainstorming") WORK_COLOR="$CYAN" ;;
  "design_ready")           WORK_COLOR="$YELLOW"   ;;
  "design_marked_for_use")  WORK_COLOR="$YELLOW"   ;;
  "plan_ready")             WORK_COLOR="$ORANGE"   ;;
  "plan_marked_for_use")    WORK_COLOR="$ORANGE"   ;;
  "final_plan_prep")        WORK_COLOR="$ORANGE"   ;;
  "executing")              WORK_COLOR="$RED"      ;;
  "reviewing")              WORK_COLOR="$RED"      ;;
  "plan_interrupted")       WORK_COLOR="$PINK"        ;;
  "debugging")              WORK_COLOR="$MAGENTA"     ;;
  "execution_complete")     WORK_COLOR="$BRIGHT_GREEN" ;;
  *)               WORK_COLOR="$GREEN" ;;
esac

OUTPUT="ironclaude v${VERSION} | ${PROF_COLOR}Professional Mode: ${PROF_LABEL}${RESET} | ${WORK_COLOR}Status: ${WORKFLOW}${RESET}"
if [ "$LOG_SESSION_IDS" = "true" ] && [ -n "$SESSION_ID" ] && [ "$SESSION_ID" != "null" ]; then
  OUTPUT="${OUTPUT} | ${SESSION_ID}"
fi
printf '%b' "$OUTPUT"
