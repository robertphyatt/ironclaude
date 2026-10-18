#!/bin/bash
# block-push.sh — PreToolUse hook: blocks git push in Brain session.
# Claude sends Bash with tool_input.command; Codex sends Bash (.command) or
# exec_command (.cmd, a string or an argv array).

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

case "$TOOL_NAME" in
  Bash|exec_command) ;;
  *) exit 0 ;;
esac

COMMAND=$(echo "$INPUT" | jq -r '(.tool_input.command // .tool_input.cmd // "") | if type == "array" then join(" ") else . end' 2>/dev/null || true)

if echo "$COMMAND" | grep -qE '^\s*git\s+(-C\s+\S+\s+)?push\b'; then
  echo "git push is blocked for Brain session. Commits are staged — Robert will push." >&2
  exit 2
fi

exit 0
