#!/bin/bash
# episodic-memory-sync.sh - Wrapper with singleton pattern
#
# Prevents multiple sync processes from running simultaneously.
# Uses PID file + lock directory for robust singleton behavior.

LOCK_DIR="$HOME/.claude/episodic-memory-sync.lock"
PID_FILE="$HOME/.claude/episodic-memory-sync.pid"

# Source shared logging
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  echo '{"systemMessage": "🔥 [EPISODIC-MEMORY-SYNC]: CRITICAL - Failed to load hook-logger.sh!"}'
  echo "🔥 CRITICAL: hook-logger.sh failed to load" >&2
fi
run_hook "EPISODIC-MEMORY-SYNC"

# Professional mode gate — read from SQLite (not flag file)
INPUT=$(cat)
init_session_id
PROF_MODE=$(db_read_or_fail "EPISODIC-MEMORY-SYNC" \
  "SELECT professional_mode FROM sessions WHERE terminal_session='$(echo "$SESSION_TAG" | sed "s/'/''/g")';")
if [ "$PROF_MODE" != "on" ]; then
    log_hook "EPISODIC-MEMORY-SYNC" "Disabled" "professional mode ${PROF_MODE}"
    exit 0
fi

# Check if already running
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    log_hook "EPISODIC-MEMORY-SYNC" "Passed" "already running (PID $OLD_PID)"
    exit 0
  fi
  # Stale PID file, clean up
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null
fi

# Acquire lock (mkdir is atomic)
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log_hook "EPISODIC-MEMORY-SYNC" "Passed" "could not acquire lock"
  exit 0
fi

# Store our PID
echo $$ > "$PID_FILE"

# Cleanup on exit (normal or error)
cleanup() {
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null
}
trap cleanup EXIT

if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    echo "CLAUDE_PLUGIN_ROOT not set, skipping sync" >&2
    exit 0
fi

# Run the actual sync
log_hook "EPISODIC-MEMORY-SYNC" "Started" "syncing..."
node "${CLAUDE_PLUGIN_ROOT}/mcp-servers/episodic-memory/dist/sync-cli.js" "$@"
RESULT=$?
if [ "$RESULT" -eq 0 ]; then
  log_hook "EPISODIC-MEMORY-SYNC" "Complete" "sync successful"
else
  log_hook "EPISODIC-MEMORY-SYNC" "ERROR" "sync failed (exit $RESULT)"
fi

exit $RESULT
