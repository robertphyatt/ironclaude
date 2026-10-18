#!/bin/bash
# brain-orchestrator-guard.sh — PreToolUse hook for IronClaude Brain session only.
# Hard-blocks mutation tools (Claude Edit/Write/NotebookEdit/MultiEdit, Codex
# apply_patch) and non-allowlisted shell commands (Claude Bash; Codex Bash or
# exec_command). Workers (IC_ROLE=worker) and direct sessions pass through.
# Codex has no Read/Grep/Glob tools, so for IRONCLAUDE_CLIENT=codex the shell also
# admits the read-only research commands Claude reaches through those tools.

# Only apply restrictions to the Brain session.
if [ "$IC_ROLE" != "brain" ]; then
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)

# Block mutation tools unconditionally
case "$TOOL_NAME" in
  Edit|Write|NotebookEdit|MultiEdit|apply_patch)
    echo "BLOCKED — Brain cannot use mutation tools. Route through workers." >&2
    exit 2
    ;;
esac

# Block non-allowlisted shell commands
if [ "$TOOL_NAME" = "Bash" ] || [ "$TOOL_NAME" = "exec_command" ]; then
  CMD=$(echo "$INPUT" | jq -r '(.tool_input.command // .tool_input.cmd // "") | if type == "array" then join(" ") else . end' 2>/dev/null || true)

  # Block shell chaining first (before any allowlist checks)
  # Also block newlines which can be used to chain commands
  if echo "$CMD" | grep -qE '[;&|`!<>]|\$\(' || [[ "$CMD" == *$'\n'* ]]; then
    echo "BLOCKED — Shell chaining/redirection is not allowed for Brain." >&2
    exit 2
  fi

  # A brace group with , or .. expands after this check approves the command, e.g.
  # git diff {--output=x,} or find /tmp {-delete,}. Checked on the raw command, before the
  # research predicate and -C normalization (design 2026-10-18 v120-security-fixes-remediation).
  if printf '%s' "$CMD" | grep -qE '\{[^{}]*(,|\.\.)[^{}]*\}'; then
    echo "BLOCKED — Shell brace expansion is not allowed for Brain." >&2
    exit 2
  fi

  # Codex read parity: admit read-only research commands (fail closed to git-only
  # when the shared predicate file is missing).
  if [ "${IRONCLAUDE_CLIENT:-}" = "codex" ] && [ -f "$SCRIPT_DIR/bash-readonly-guard.sh" ]; then
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/bash-readonly-guard.sh"
    if is_readonly_research_bash "$CMD"; then
      exit 0
    fi
  fi

  # The raw command, kept for checks that -C normalization must not hide.
  RAW_CMD="$CMD"
  # Normalize: strip -C <path> so downstream subcommand extraction works unchanged
  CMD=$(echo "$CMD" | sed -E 's/^([[:space:]]*git)[[:space:]]+-C[[:space:]]+[^[:space:]]+[[:space:]]+/\1 /')

  # Allow make test* commands (locally-scoped -C normalization — does not touch $CMD,
  # which the git-subcommand extraction below still relies on)
  MAKE_CMD=$(echo "$CMD" | sed -E 's/^([[:space:]]*make)[[:space:]]+-C[[:space:]]+[^[:space:]]+[[:space:]]+/\1 /')
  if echo "$MAKE_CMD" | grep -qE '^\s*make\s+test'; then
    exit 0
  fi

  # Must be a git command
  if ! echo "$CMD" | grep -qE '^\s*git\s+'; then
    echo "BLOCKED — Brain can only run git commands and make test* via Bash. Route other commands through workers." >&2
    exit 2
  fi

  # A glob can hand git a file named like an option (git diff * with a file named --output=x).
  case "$RAW_CMD" in
    *[\*\?\[]*)
      echo "BLOCKED — Glob characters are not allowed in Brain git commands." >&2
      exit 2
      ;;
  esac

  # Extract git subcommand
  GIT_SUBCMD=$(echo "$CMD" | sed -E 's/^[[:space:]]*git[[:space:]]+//' | awk '{print $1}')

  case "$GIT_SUBCMD" in
    log|diff|show)
      # --output=<file> / --output <file> writes a file (design 2026-10-18 v120-security-fixes 1b).
      # Match on a copy with every " ' and \ deleted, so "--output=x" and --\output=x are caught.
      if printf '%s' "$CMD" | tr -d "\"'\\\\" | grep -qE '(^|[[:space:]])--output(=|[[:space:]]|$)'; then
        echo "BLOCKED — git --output writes a file and is not allowed for Brain." >&2
        exit 2
      fi
      exit 0
      ;;
    status|ls-files|blame|add)
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
