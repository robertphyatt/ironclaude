#!/bin/bash
# episodic-memory-reminder.sh - Inject reminder on session resume/compaction
#
# Fires on SessionStart with "resume" event to remind Claude to search
# episodic memory if context feels incomplete after compaction.

# Source shared logging
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  echo '{"systemMessage": "🔥 [EPISODIC-MEMORY-REMINDER]: CRITICAL - Failed to load hook-logger.sh!"}'
  echo "🔥 CRITICAL: hook-logger.sh failed to load" >&2
fi
run_hook "EPISODIC-MEMORY-REMINDER"

# Professional mode gate — read from SQLite (not flag file)
INPUT=$(cat)
init_session_id
PROF_MODE=$(db_read_or_fail "EPISODIC-MEMORY-REMINDER" \
  "SELECT professional_mode FROM sessions WHERE terminal_session='$(echo "$SESSION_TAG" | sed "s/'/''/g")';")
if [ "$PROF_MODE" != "on" ]; then
    log_hook "EPISODIC-MEMORY-REMINDER" "Disabled" "professional mode ${PROF_MODE}"
    exit 0
fi

# Read event from stdin (already consumed above)
EVENT="$INPUT"
EVENT_TYPE=$(echo "$EVENT" | jq -r '.event_type // .type // "startup"' 2>/dev/null || true)

# Only inject reminder on resume, not fresh startup
if [[ "$EVENT_TYPE" == "resume" ]]; then
  log_hook "EPISODIC-MEMORY-REMINDER" "Reminder" "session resumed"
  # Also output the actual reminder as systemMessage
  echo '{"systemMessage": "⚠️ This session was resumed after compaction. Earlier context may be missing. If you need details from earlier, call the Task tool with: description=\"Search episodic memory\", subagent_type=\"ironclaude:search-conversations\", prompt=\"Search for prior decisions and context about [your current topic]\". Do not guess — search first."}'
  exit 0
fi

log_hook "EPISODIC-MEMORY-REMINDER" "Passed" "fresh startup"
exit 0
