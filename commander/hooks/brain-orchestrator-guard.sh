#!/bin/bash
# brain-orchestrator-guard.sh — PreToolUse hook for IronClaude Brain session.
# Hard-blocks Edit, Write, NotebookEdit, MultiEdit, and non-allowlisted Bash commands.
# The Brain is an orchestrator — all code changes must go through workers.

# Require IC_ROLE to be set — daemon sets it; direct sessions are blocked.
if [ -z "$IC_ROLE" ]; then
  echo "BLOCKED — Brain requires IC_ROLE. Start via daemon." >&2
  exit 2
fi

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Block mutation tools unconditionally
case "$TOOL_NAME" in
  Edit|Write|NotebookEdit|MultiEdit)
    echo "BLOCKED — Brain cannot use mutation tools. Route through workers." >&2
    exit 2
    ;;
esac

# Block non-allowlisted Bash commands
if [ "$TOOL_NAME" = "Bash" ]; then
  CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)

  # Block shell chaining first (before any allowlist checks)
  # Also block newlines which can be used to chain commands
  if echo "$CMD" | grep -qE '[;&|`!]|\$\(' || [[ "$CMD" == *$'\n'* ]]; then
    echo "BLOCKED — Shell chaining is not allowed for Brain." >&2
    exit 2
  fi

  # Normalize: strip -C <path> so downstream subcommand extraction works unchanged
  CMD=$(echo "$CMD" | sed -E 's/^([[:space:]]*git)[[:space:]]+-C[[:space:]]+[^[:space:]]+[[:space:]]+/\1 /')

  # Allow make test* commands
  if echo "$CMD" | grep -qE '^\s*make\s+test'; then
    exit 0
  fi

  # Must be a git command
  if ! echo "$CMD" | grep -qE '^\s*git\s+'; then
    echo "BLOCKED — Brain can only run git commands and make test* via Bash. Route other commands through workers." >&2
    exit 2
  fi

  # Extract git subcommand
  GIT_SUBCMD=$(echo "$CMD" | sed -E 's/^[[:space:]]*git[[:space:]]+//' | awk '{print $1}')

  case "$GIT_SUBCMD" in
    log|diff|show|status|ls-files|blame|add)
      exit 0
      ;;
    branch)
      # Only allow read-only branch operations (listing)
      # Block: -d, -D, -m, -M, -c, -C (delete/rename/copy), and bare branch <name> (create)
      if echo "$CMD" | grep -qE '\s-[dDmMcC]'; then
        echo "BLOCKED — git branch modification is not allowed for Brain. Only listing permitted." >&2
        exit 2
      fi
      # Check if there's a branch name argument (creation attempt)
      # git branch <name> creates a branch, git branch (no args) lists
      BRANCH_ARGS=$(echo "$CMD" | sed -E 's/^[[:space:]]*git[[:space:]]+branch[[:space:]]*//')
      if [ -n "$BRANCH_ARGS" ] && ! echo "$BRANCH_ARGS" | grep -qE '^-'; then
        echo "BLOCKED — git branch creation is not allowed for Brain. Only listing permitted." >&2
        exit 2
      fi
      exit 0
      ;;
    commit)
      # Block git commit --amend (destructive history rewrite)
      if echo "$CMD" | grep -qE '\-\-amend'; then
        echo "BLOCKED — git commit --amend is not allowed for Brain." >&2
        exit 2
      fi
      exit 0
      ;;
    *)
      echo "BLOCKED — git $GIT_SUBCMD is not allowed for Brain. Allowed: log, diff, show, status, ls-files, blame, branch, add, commit (not --amend)." >&2
      exit 2
      ;;
  esac
fi

exit 0
