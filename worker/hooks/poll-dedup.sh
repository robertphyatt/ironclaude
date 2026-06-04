#!/bin/bash
# poll-dedup.sh — Detect and block repeated identical tool reads
#
# Dual-mode hook (same pattern as subagent-circuit-breaker.sh):
#   PreToolUse  (Read|Bash|Grep|Glob): check count, block if >= 3 and age < 5min
#   PostToolUse (Read|Bash|Grep|Glob): hash output, update tool_poll_state
#
# State survives context compaction (stored in SQLite).
# Block message: "Output unchanged — wait for completion notification or check again in 5 minutes."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! source "${SCRIPT_DIR}/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "POLL-DEDUP"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Detect mode: tool_output present = PostToolUse, absent = PreToolUse
HAS_OUTPUT=$(echo "$INPUT" | jq -r 'has("tool_output")' 2>/dev/null || echo "false")

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# compute_input_hash — stable hash of the tool's INPUT for keying poll state.
# Different tool types expose their identifying inputs under different JSON fields.
compute_input_hash() {
  local key=""
  case "$TOOL_NAME" in
    Read)
      key=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)
      ;;
    Bash)
      key=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
      ;;
    Grep)
      local pattern path_val
      pattern=$(echo "$INPUT" | jq -r '.tool_input.pattern // empty' 2>/dev/null || true)
      path_val=$(echo "$INPUT" | jq -r '.tool_input.path // empty' 2>/dev/null || true)
      key="${pattern}|${path_val}"
      ;;
    Glob)
      local pat gpath
      pat=$(echo "$INPUT" | jq -r '.tool_input.pattern // empty' 2>/dev/null || true)
      gpath=$(echo "$INPUT" | jq -r '.tool_input.path // empty' 2>/dev/null || true)
      key="${pat}|${gpath}"
      ;;
    *)
      key="$TOOL_NAME"
      ;;
  esac
  echo "$key" | portable_md5
}

# age_seconds TIMESTAMP — returns integer seconds since the SQLite datetime string.
# Falls back to 9999 on parse failure (fail-open: treat as old → allow through).
age_seconds() {
  local ts="$1"
  python3 -c "
import time, datetime
try:
    t = datetime.datetime.fromisoformat('${ts}'.replace(' ', 'T'))
    print(int(time.time() - t.timestamp()))
except Exception:
    print(9999)
" 2>/dev/null || echo "9999"
}

INPUT_HASH=$(compute_input_hash)
SAFE_INPUT_HASH=$(echo "$INPUT_HASH" | sed "s/'/''/g")
SAFE_TOOL=$(echo "$TOOL_NAME" | sed "s/'/''/g")

if [ "$HAS_OUTPUT" = "false" ]; then
  # ─── PreToolUse: check for polling pattern, block if threshold exceeded ───

  STATE=$(sqlite3 "$DB_PATH" ".timeout 5000" \
    "SELECT consecutive_count || '|' || updated_at FROM tool_poll_state
     WHERE terminal_session='${SAFE_SESSION}'
       AND tool_name='${SAFE_TOOL}'
       AND input_hash='${SAFE_INPUT_HASH}';" \
    2>/dev/null || true)

  if [ -z "$STATE" ]; then
    log_hook "POLL-DEDUP" "Allowed" "no prior state for ${TOOL_NAME}"
    exit 0
  fi

  COUNT=$(echo "$STATE" | cut -d'|' -f1)
  UPDATED_AT=$(echo "$STATE" | cut -d'|' -f2-)

  if [ "${COUNT:-0}" -lt 3 ]; then
    log_hook "POLL-DEDUP" "Allowed" "count=${COUNT:-0} for ${TOOL_NAME}"
    exit 0
  fi

  AGE=$(age_seconds "$UPDATED_AT")

  if [ "$AGE" -lt 300 ]; then
    REMAINING=$(( 300 - AGE ))
    block_pretooluse "POLL-DEDUP" "BLOCKED — POLLING DETECTED (${COUNT} consecutive identical reads)

Output unchanged since last read. Wait for a completion notification or check again
in $((REMAINING / 60))m $((REMAINING % 60))s. Do NOT re-read the same resource —
it will remain blocked until the cooldown expires or content changes.

Tool: ${TOOL_NAME} | Consecutive identical reads: ${COUNT}"
  else
    # Cooldown elapsed — reset count and allow
    sqlite3 "$DB_PATH" ".timeout 5000" \
      "UPDATE tool_poll_state
         SET consecutive_count=0, updated_at=datetime('now')
       WHERE terminal_session='${SAFE_SESSION}'
         AND tool_name='${SAFE_TOOL}'
         AND input_hash='${SAFE_INPUT_HASH}';" \
      2>/dev/null || true
    log_hook "POLL-DEDUP" "Allowed" "cooldown elapsed, count reset for ${TOOL_NAME}"
    exit 0
  fi

else
  # ─── PostToolUse: hash output, update consecutive count ───

  TOOL_OUTPUT=$(echo "$INPUT" | jq -r '.tool_output // empty' 2>/dev/null || true)
  OUTPUT_HASH=$(echo "$TOOL_OUTPUT" | portable_md5)
  SAFE_OUTPUT_HASH=$(echo "$OUTPUT_HASH" | sed "s/'/''/g")

  CURRENT=$(sqlite3 "$DB_PATH" ".timeout 5000" \
    "SELECT last_output_hash || '|' || consecutive_count FROM tool_poll_state
     WHERE terminal_session='${SAFE_SESSION}'
       AND tool_name='${SAFE_TOOL}'
       AND input_hash='${SAFE_INPUT_HASH}';" \
    2>/dev/null || true)

  LAST_HASH=$(echo "$CURRENT" | cut -d'|' -f1)
  CURRENT_COUNT=$(echo "$CURRENT" | cut -d'|' -f2)

  if [ "$OUTPUT_HASH" = "$LAST_HASH" ] && [ -n "$LAST_HASH" ]; then
    NEW_COUNT=$(( ${CURRENT_COUNT:-0} + 1 ))
  else
    NEW_COUNT=1
  fi

  sqlite3 "$DB_PATH" ".timeout 5000" \
    "INSERT OR REPLACE INTO tool_poll_state
       (terminal_session, tool_name, input_hash, last_output_hash, consecutive_count, updated_at)
     VALUES
       ('${SAFE_SESSION}', '${SAFE_TOOL}', '${SAFE_INPUT_HASH}',
        '${SAFE_OUTPUT_HASH}', ${NEW_COUNT}, datetime('now'));" \
    2>/dev/null || {
    log_error "POLL-DEDUP" "UPSERT failed for ${TOOL_NAME}"
    exit 0
  }

  if [ "$NEW_COUNT" -eq 3 ]; then
    log_hook "POLL-DEDUP" "Blocked" "WARNING — 3 consecutive identical reads of ${TOOL_NAME}. Next identical call will be blocked for 5 minutes. Output has not changed — wait for a notification before re-reading."
  else
    log_hook "POLL-DEDUP" "Allowed" "count=${NEW_COUNT} for ${TOOL_NAME}"
  fi

  exit 0
fi
