#!/bin/bash
# brain-permission-seeker-guard.sh — Stop hook for IronClaude Brain session.
# Blocks the Brain from sending permission-seeking messages to the operator.
# Brain must act on confirmed directives immediately, not seek approval.
#
# Owns the complete 13-pattern permission-seeking check.
# brain-stop-hook.sh handles the DB incomplete-tasks check.
#
# Input (stdin): JSON with transcript_path, stop_hook_active, session_id
# Output (stdout): JSON with decision ("approve"/"block"), reason, systemMessage

# --- Parse stdin ---
INPUT=$(cat)
TRANSCRIPT_PATH=$(printf '%s' "$INPUT" | jq -r '.transcript_path // ""' 2>/dev/null || true)
STOP_HOOK_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || true)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // .conversation_id // "unknown"' 2>/dev/null || true)
SAFE_SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
THROTTLE_FILE="/tmp/ic-perm-seeker-${SAFE_SESSION_ID}"
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

# Fail-open: no text to scan
if [ -z "$LAST_TEXT" ]; then
    printf '{"decision": "approve", "reason": "No text content to scan"}\n'
    exit 0
fi

# --- Permission-seeking pattern check ---
# Fixed-string patterns (case-insensitive, -F for speed and safety).
# Loop reports the first matched pattern in the block message.
FIXED_PATTERNS=(
    "awaiting confirmation"
    "awaiting operator"
    "awaiting your"
    "shall i"
    "should i"
    "would you like"
    "want me to"
    "do you want"
    "ready for you to"
    "let me know if"
    "let me know when"
    "at your convenience"
)

for pattern in "${FIXED_PATTERNS[@]}"; do
    if printf '%s' "$LAST_TEXT" | grep -qiF "$pattern" 2>/dev/null; then
        block \
            "Permission-seeking language detected: '${pattern}'" \
            "BLOCKED — Permission-seeking language detected: '${pattern}'. Act on confirmed directives immediately. If you need guidance, use the decision format (explain issue, options with pros/cons, recommendation, prediction, pin)."
    fi
done

# Regex pattern: optional apostrophe handles "you're" and "youre"
if printf '%s' "$LAST_TEXT" | grep -qiE "when you'?re ready" 2>/dev/null; then
    block \
        "Permission-seeking language detected: 'when you're ready'" \
        "BLOCKED — Permission-seeking language detected: 'when you're ready'. Act on confirmed directives immediately. If you need guidance, use the decision format (explain issue, options with pros/cons, recommendation, prediction, pin)."
fi

# --- Approve ---
printf '{"decision": "approve", "reason": "No permission-seeking language detected"}\n'
