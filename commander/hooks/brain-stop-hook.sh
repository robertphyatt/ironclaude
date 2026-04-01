#!/bin/bash
# brain-stop-hook.sh — Claude Code Stop hook for IronClaude worker sessions.
# Blocks the session from stopping if:
#   1. The last response contains permission-seeking language, OR
#   2. The worker (via IC_WORKER_ID) has incomplete tasks in ironclaude.db,
#      or any workers have status != 'completed' (brain context fallback).
# Throttle: max 3 blocks per 5 minutes per session.
#
# Input (stdin): JSON with transcript_path, stop_hook_active, session_id
# Output (stdout): JSON with decision ("approve"/"block"), reason, systemMessage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="${IC_DB_PATH:-${SCRIPT_DIR}/../data/db/ironclaude.db}"

# --- Parse stdin ---
INPUT=$(cat)
TRANSCRIPT_PATH=$(printf '%s' "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null || true)
STOP_HOOK_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || true)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // .conversation_id // "unknown"' 2>/dev/null || true)
SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
THROTTLE_FILE="/tmp/ic-stop-hook-${SAFE_SESSION_ID}"
NOW=$(date +%s)

# --- Throttle check (only when stop_hook_active=true) ---
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    BLOCK_COUNT=0
    LAST_BLOCK_TIME=0
    if [ -f "$THROTTLE_FILE" ]; then
        DATA=$(cat "$THROTTLE_FILE")
        BLOCK_COUNT=$(printf '%s' "$DATA" | cut -d: -f1)
        LAST_BLOCK_TIME=$(printf '%s' "$DATA" | cut -d: -f2)
        [[ "$BLOCK_COUNT" =~ ^[0-9]+$ ]] || BLOCK_COUNT=0
        [[ "$LAST_BLOCK_TIME" =~ ^[0-9]+$ ]] || LAST_BLOCK_TIME=0
    fi
    TIME_SINCE=$((NOW - LAST_BLOCK_TIME))
    [ "$TIME_SINCE" -gt 300 ] && BLOCK_COUNT=0
    if [ "$BLOCK_COUNT" -ge 3 ] && [ "$TIME_SINCE" -lt 300 ]; then
        printf '{"decision": "approve", "reason": "Block throttle limit reached (3 blocks in 5 min)"}\n'
        exit 0
    fi
fi

# --- Block helper: increments throttle, emits JSON, exits ---
block() {
    local reason="$1" msg="$2"
    local count=0
    if [ -f "$THROTTLE_FILE" ]; then
        count=$(cat "$THROTTLE_FILE" | cut -d: -f1)
        [[ "$count" =~ ^[0-9]+$ ]] || count=0
    fi
    count=$((count + 1))
    [ -L "$THROTTLE_FILE" ] && rm -f "$THROTTLE_FILE"
    printf '%s:%s\n' "$count" "$NOW" > "$THROTTLE_FILE"
    printf '{"decision": "block", "reason": %s, "systemMessage": %s}\n' \
        "$(printf '%s' "$reason" | jq -Rs .)" \
        "$(printf '%s' "$msg" | jq -Rs .)"
    exit 0
}

# --- Extract last assistant text from transcript ---
LAST_TEXT=""
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
    # Find last assistant requestId, then extract text blocks for that request
    LAST_REQ_ID=$(
        tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | \
        jq -r 'select(.type == "assistant") | .requestId // empty' 2>/dev/null | \
        tail -1 || true
    )
    if [ -n "$LAST_REQ_ID" ]; then
        LAST_TEXT=$(
            tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | grep -F "\"$LAST_REQ_ID\"" | \
            jq -r '.message.content[]? | select(.type == "text") | .text // empty' 2>/dev/null || true
        )
    fi
    # Fallback: scan all recent assistant messages for any text
    if [ -z "$LAST_TEXT" ]; then
        LAST_TEXT=$(
            tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | \
            jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text // empty' \
            2>/dev/null | tail -n 50 || true
        )
    fi
fi

# --- Permission-seeking check ---
PERM_RE='(shall I|should I|would you like me to|do you want|want me to proceed|awaiting your|let me know if|would you like|shall we|should we)'
if [ -n "$LAST_TEXT" ] && printf '%s' "$LAST_TEXT" | grep -qiE "$PERM_RE" 2>/dev/null; then
    block \
        "Permission-seeking language detected in last response" \
        "You asked for permission instead of proceeding. Just do the work — there is no need to ask. Continue with the implied task immediately."
fi

# --- DB check ---
if command -v sqlite3 >/dev/null 2>&1 && [ -f "$DB_PATH" ]; then
    if [ -n "${IC_WORKER_ID:-}" ]; then
        SAFE_WORKER_ID=$(printf '%s' "$IC_WORKER_ID" | sed "s/'/''/g")
        PENDING=$(sqlite3 -cmd ".timeout 1000" "$DB_PATH" \
            "SELECT description FROM tasks WHERE worker_id='${SAFE_WORKER_ID}' AND status != 'completed' LIMIT 3;" \
            2>/dev/null || true)
        if [ -n "$PENDING" ]; then
            TASK_LIST=$(printf '%s' "$PENDING" | tr '\n' '; ' | sed 's/; $//')
            block \
                "Worker ${IC_WORKER_ID} has incomplete tasks" \
                "You have incomplete tasks: ${TASK_LIST} Complete them before stopping."
        fi
    else
        RUNNING=$(sqlite3 -cmd ".timeout 1000" "$DB_PATH" \
            "SELECT id FROM workers WHERE status != 'completed' LIMIT 5;" \
            2>/dev/null || true)
        if [ -n "$RUNNING" ]; then
            WORKER_LIST=$(printf '%s' "$RUNNING" | tr '\n' ', ' | sed 's/, $//')
            block \
                "Workers still running: ${WORKER_LIST}" \
                "Workers are still running: ${WORKER_LIST}. Check on them with get_worker_log before stopping."
        fi
    fi
fi

# --- Approve ---
printf '{"decision": "approve", "reason": "No pending work detected"}\n'
