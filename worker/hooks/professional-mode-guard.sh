#!/bin/bash
# professional-mode-guard.sh — PreToolUse hook
# Enforces professional mode restrictions via MCP state manager.
# Thin client: reads sqlite3 for fast enforcement, delegates to MCP for complex decisions.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
run_hook "professional-mode-guard"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.command // empty' 2>/dev/null || true)
FILE_PATH=$(normalize_path "$FILE_PATH")

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# ─── Read professional_mode from sqlite3 ───
prof_mode=$(db_read_or_fail "professional-mode-guard" \
  "SELECT professional_mode FROM sessions WHERE terminal_session='${SAFE_SESSION}';") || {
  block_pretooluse "professional-mode-guard" "BLOCKED — DATABASE ERROR

Cannot read professional_mode from the database. This is a temporary error.

Try your action again. If this persists, report the error to the user."
}

# ─── UNDECIDED: block everything except Read/Grep/Glob and mode-toggle Skills ───
if [ "$prof_mode" = "undecided" ]; then
  # AskUserQuestion: always allow in UNDECIDED (activation skill needs to prompt user)
  if [ "$TOOL_NAME" = "AskUserQuestion" ]; then
    log_hook "professional-mode-guard" "Allowed" "AskUserQuestion in undecided"
    exit 0
  fi
  # CLAUDE.md: allow Write/Edit during undecided setup window (prerequisites before restrictions)
  if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
    if [[ "$FILE_PATH" == "CLAUDE.md" || "$FILE_PATH" == */CLAUDE.md ]]; then
      log_hook "professional-mode-guard" "Allowed" "CLAUDE.md write during undecided setup"
      exit 0
    fi
  fi
  case "$TOOL_NAME" in
    Read|Grep|Glob)
      log_hook "professional-mode-guard" "Allowed" "read-only tool (undecided)"
      exit 0
      ;;
    Skill)
      skill_name=$(echo "$INPUT" | jq -r '.tool_input.skill // empty' 2>/dev/null || true)
      if [ "$skill_name" = "activate-professional-mode" ] || [ "$skill_name" = "deactivate-professional-mode" ]; then
        log_hook "professional-mode-guard" "Allowed" "mode toggle skill (undecided)"
        exit 0
      fi
      block_pretooluse "professional-mode-guard" "BLOCKED — PROFESSIONAL MODE NOT SET

Professional mode has not been activated or deactivated yet. You can only use read-only tools (Read, Grep, Glob) until the user decides.

To activate, call the Skill tool with:
  skill: \"ironclaude:activate-professional-mode\"

Or wait for the user to run /activate-professional-mode or /deactivate-professional-mode.

Do NOT use Edit, Write, Bash, or any other write tool until professional mode is set."
      ;;
    *)
      block_pretooluse "professional-mode-guard" "BLOCKED — PROFESSIONAL MODE NOT SET

Professional mode has not been activated or deactivated yet. You can only use read-only tools (Read, Grep, Glob) until the user decides.

To activate, call the Skill tool with:
  skill: \"ironclaude:activate-professional-mode\"

Or wait for the user to run /activate-professional-mode or /deactivate-professional-mode.

Do NOT use Edit, Write, Bash, or any other write tool until professional mode is set."
      ;;
  esac
fi

# ─── OFF: no enforcement ───
if [ "$prof_mode" = "off" ]; then
  log_hook "professional-mode-guard" "Allowed" "professional mode off"
  exit 0
fi

# ─── ON: enforce restrictions ───

# Read-only tools: always allow
case "$TOOL_NAME" in
  Read|Grep|Glob)
    log_hook "professional-mode-guard" "Allowed" "read-only tool"
    exit 0
    ;;
  Skill)
    log_hook "professional-mode-guard" "Allowed" "skill tool"
    exit 0
    ;;
  EnterPlanMode|ExitPlanMode)
    block_pretooluse "professional-mode-guard" "BLOCKED — USE BRAINSTORMING INSTEAD

EnterPlanMode and ExitPlanMode are disabled when professional mode is active.

Call the Skill tool with:
  skill: \"ironclaude:brainstorming\"

Do NOT use EnterPlanMode or ExitPlanMode. Use the brainstorming skill for all design work."
    ;;
esac

# docs/ path whitelist (design + plan gate)
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]; then
  if [[ "$FILE_PATH" == *"/docs/"* ]] || [[ "$FILE_PATH" == "docs/"* ]]; then
    # Design documents require active brainstorming
    if [[ "$FILE_PATH" == *-design.md ]]; then
      workflow=$(db_read_or_fail "professional-mode-guard" \
        "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';") || {
        block_pretooluse "professional-mode-guard" "BLOCKED — DATABASE ERROR

Cannot read workflow_stage from the database. This is a temporary error.

Try your action again. If this persists, report the error to the user."
      }
      if [ "$workflow" != "brainstorming" ] && [ "$workflow" != "design_ready" ]; then
        block_pretooluse "professional-mode-guard" "BLOCKED — BRAINSTORMING REQUIRED FIRST

Design documents can only be created during the brainstorming skill.

Call the Skill tool with:
  skill: \"ironclaude:brainstorming\"

Do NOT create design documents outside of brainstorming."
      fi
      log_hook "professional-mode-guard" "Allowed" "design write during brainstorming"
      exit 0
    fi
    # Plan files require a consumed design
    if [[ "$FILE_PATH" == */docs/plans/*.md ]] || [[ "$FILE_PATH" == docs/plans/*.md ]]; then
      consumed=$(db_read "professional-mode-guard" \
        "SELECT 1 FROM registered_designs WHERE consumed=1 AND terminal_session='${SAFE_SESSION}' LIMIT 1;")
      if [ "$consumed" != "1" ]; then
        block_pretooluse "professional-mode-guard" "BLOCKED — NO DESIGN DOCUMENT

You must create a design document before writing plan files.

Follow this workflow:
1. Call Skill tool with skill: \"ironclaude:brainstorming\" to create a design
2. Call Skill tool with skill: \"ironclaude:writing-plans\" to create the plan

Do NOT create plan files without completing brainstorming first."
      fi
    fi
    log_hook "professional-mode-guard" "Allowed" "docs/ path"
    exit 0
  fi
fi

# Allow writes to auto-memory files regardless of workflow stage
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" ]]; then
  CANONICAL_PATH=$(realpath -m "$FILE_PATH" 2>/dev/null || echo "$FILE_PATH")
  if [[ "$CANONICAL_PATH" != *".."* ]] && [[ "$CANONICAL_PATH" == "$HOME/.claude/projects/"*"/memory/"* ]]; then
    log_hook "professional-mode-guard" "Allowed" "memory file"
    exit 0
  fi
fi

# Edit/Write/MultiEdit/Bash/NotebookEdit: inline access check (replaces HTTP check-access)
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then
  # Read workflow_stage
  WORKFLOW=$(db_read_or_fail "professional-mode-guard" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';") || {
    block_pretooluse "professional-mode-guard" "BLOCKED — DATABASE ERROR

Cannot read workflow_stage from the database. This is a temporary error.

Try your action again. If this persists, report the error to the user."
  }

  # Not executing: block write tools (architect mode)
  if [ "$WORKFLOW" != "executing" ]; then
    # Deny-first: block dangerous git commands before any allow-exception
    if [ "$TOOL_NAME" = "Bash" ] && echo "$FILE_PATH" | grep -qE '\bgit\b.*\b(commit|push|merge|rebase)\b'; then
      block_pretooluse "professional-mode-guard" "BLOCKED — GIT COMMIT/PUSH NOT ALLOWED

Git commit, push, merge, and rebase are blocked when not in the executing stage.

Do NOT run git commit, git push, git merge, or git rebase outside of plan execution."
    fi
    # Exception: allow git add (staging) in Bash — anchored, no chaining
    if [ "$TOOL_NAME" = "Bash" ]; then
      if ! echo "$FILE_PATH" | grep -qE '[;&|`]|\$\(' && echo "$FILE_PATH" | grep -qE '^\s*git\s+add\b'; then
        log_hook "professional-mode-guard" "Allowed" "git staging"
        exit 0
      fi
    fi
    # Exception: allow read-only git commands at any workflow stage
    if [ "$TOOL_NAME" = "Bash" ] && echo "$FILE_PATH" | grep -qE '\bgit\s+(diff|status|log|show|blame|branch)\b'; then
      log_hook "professional-mode-guard" "Allowed" "read-only git command"
      exit 0
    fi
    # Exception: allow specific read-only commands during code review
    if [ "$TOOL_NAME" = "Bash" ] && [ "$WORKFLOW" = "reviewing" ]; then
      if echo "$FILE_PATH" | grep -qE '[;&|`]|\$\('; then
        block_pretooluse "professional-mode-guard" "BLOCKED — COMMAND CHAINING NOT ALLOWED DURING REVIEW

Shell chaining operators (; && || | backtick \$()) are not permitted during code review.

Allowed commands: sqlite3, git diff/status/log/show/blame/ls-files, pytest, make test, cat, head, tail, wc, grep, rg, find, ls

Do NOT run commands with shell operators during the reviewing stage."
      elif echo "$FILE_PATH" | grep -qE '^\s*(sqlite3|git\s+(diff|status|log|show|blame|ls-files)|pytest|make\s+test|cat|head|tail|wc|grep|rg|find|ls)\b'; then
        log_hook "professional-mode-guard" "Allowed" "safe bash during code review"
        exit 0
      else
        block_pretooluse "professional-mode-guard" "BLOCKED — COMMAND NOT ALLOWED DURING REVIEW

Only the following commands are allowed during code review:
  sqlite3, git diff/status/log/show/blame/ls-files, pytest, make test,
  cat, head, tail, wc, grep, rg, find, ls

Do NOT run destructive or write commands during the reviewing stage."
      fi
    fi
    # Exception: allow make test* commands at any workflow stage — anchored, no chaining
    if [ "$TOOL_NAME" = "Bash" ]; then
      if ! echo "$FILE_PATH" | grep -qE '[;&|`]|\$\(' && echo "$FILE_PATH" | grep -qE '^\s*make\s+test'; then
        log_hook "professional-mode-guard" "Allowed" "make test* command"
        exit 0
      fi
    fi
    block_pretooluse "professional-mode-guard" "BLOCKED — WRITE TOOLS NOT ALLOWED

The current workflow stage is '${WORKFLOW}'. Write tools (Edit, Write, Bash) are only allowed during plan execution.

To reach execution, follow the workflow:
1. Call Skill tool with skill: \"ironclaude:brainstorming\" to design
2. Call Skill tool with skill: \"ironclaude:writing-plans\" to plan
3. Call Skill tool with skill: \"ironclaude:executing-plans\" to execute

Do NOT use Edit, Write, or Bash until you are in the executing stage."
  fi

  # Executing + Edit/Write/MultiEdit/NotebookEdit: check allowed_files
  if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]] && [ -n "$FILE_PATH" ]; then
    WAVE_NUM=$(sqlite3 "$DB_PATH" ".timeout 10000" \
      "SELECT current_wave FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
    SAFE_WAVE_NUM=$(echo "$WAVE_NUM" | sed "s/'/''/g")

    if [ -n "$WAVE_NUM" ] && [ "$WAVE_NUM" != "0" ]; then
      ALLOWED_FILES=$(sqlite3 "$DB_PATH" ".timeout 10000" \
        "SELECT allowed_files FROM wave_tasks WHERE terminal_session='${SAFE_SESSION}' AND wave_number='${SAFE_WAVE_NUM}';" 2>/dev/null || true)

      if [ -n "$ALLOWED_FILES" ] && command -v jq &>/dev/null; then
        # Normalize FILE_PATH: strip git repo root to get relative path
        REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
        if [ -n "$REPO_ROOT" ]; then
          NORMALIZED_FILE="${FILE_PATH#${REPO_ROOT}/}"
        else
          NORMALIZED_FILE="$FILE_PATH"
        fi

        # Collect all allowed files from all wave tasks
        FILE_ALLOWED="false"
        while IFS= read -r allowed_json; do
          if echo "$allowed_json" | jq -e 'type == "array"' &>/dev/null; then
            # Check absolute path (handles case where allowed_files has absolute paths)
            if echo "$allowed_json" | jq -r '.[]' 2>/dev/null | grep -qxF "$FILE_PATH"; then
              FILE_ALLOWED="true"
              break
            fi
            # Check normalized (relative) path
            if echo "$allowed_json" | jq -r '.[]' 2>/dev/null | grep -qxF "$NORMALIZED_FILE"; then
              FILE_ALLOWED="true"
              break
            fi
          fi
        done <<< "$ALLOWED_FILES"

        if [ "$FILE_ALLOWED" = "false" ]; then
          block_pretooluse "professional-mode-guard" "BLOCKED — FILE NOT IN PLAN

The file '${FILE_PATH}' is not in the allowed_files list for the current wave's tasks.

You can only modify files listed in the plan. Check the plan for allowed_files.

Do NOT modify files outside the plan. If you need this file, update the plan first."
        fi
      fi
    fi
  fi

  # Executing + Bash: check for forbidden git commands
  if [ "$TOOL_NAME" = "Bash" ] && [ -n "$FILE_PATH" ]; then
    if echo "$FILE_PATH" | grep -qE '\bgit\b.*\b(commit|push|merge|rebase)\b'; then
      block_pretooluse "professional-mode-guard" "BLOCKED — GIT COMMIT/PUSH NOT ALLOWED

Git commit, push, merge, and rebase are blocked during plan execution. Only 'git add' (staging) is allowed.

Use 'git add <file>' to stage your changes. The user will commit manually after execution.

Do NOT run git commit, git push, git merge, or git rebase."
    fi
    if ! echo "$FILE_PATH" | grep -qE '[;&|`]|\$\(' && echo "$FILE_PATH" | grep -qE '^\s*git\s+add\b'; then
      log_hook "professional-mode-guard" "Allowed" "git staging"
      exit 0
    fi
  fi

  # Check review_pending
  REVIEW_PENDING=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT review_pending FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
  if [ "$REVIEW_PENDING" = "1" ]; then
    if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then
      block_pretooluse "professional-mode-guard" "BLOCKED — CODE REVIEW PENDING

You submitted work for review but code review has not completed yet. Write tools are blocked.

Call the Skill tool with:
  skill: \"ironclaude:code-review\"
  args: \"--task-boundary\"

Do NOT use Edit, Write, MultiEdit, or Bash until code review completes."
    fi
  fi

  log_hook "professional-mode-guard" "Allowed" "access check passed"
  exit 0
fi

# Tool not handled by this hook — allow.
# MCP tools (mcp__plugin_ironclaude_*) intentionally fall through here.
# They are not matched by hooks.json and are governed by their own MCP-layer validation.
log_hook "professional-mode-guard" "Allowed" "tool not handled"
exit 0
