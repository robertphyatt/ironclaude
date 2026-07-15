#!/bin/bash
# professional-mode-guard.sh — PreToolUse hook
# Enforces professional mode restrictions via MCP state manager.
# Thin client: reads sqlite3 for fast enforcement, delegates to MCP for complex decisions.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/hook-logger.sh"
source "$SCRIPT_DIR/bash-readonly-guard.sh"
source "$SCRIPT_DIR/config-guard.sh" 2>/dev/null || true
# FAIL CLOSED: if config-guard.sh failed to load, block ALL config-file operations
# (revert to v1.0.19 hard-block) rather than silently allowing them.
if ! type config_guard_decision >/dev/null 2>&1; then
  config_guard_decision() {
    local tool="$1" fp="$2" lc
    lc=$(printf '%s' "$fp" | tr '[:upper:]' '[:lower:]')
    case "$tool" in
      Edit|MultiEdit|Write|Bash)
        [[ "$lc" == *"ironclaude-hooks-config"* ]] && { echo "block"; return; } ;;
    esac
    echo "allow"
  }
fi
run_hook "professional-mode-guard"

INPUT=$(cat)
init_session_id

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.command // empty' 2>/dev/null || true)
FILE_PATH=$(normalize_path "$FILE_PATH")

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# ─── Human-only: never let the agent write the hooks-config file ───
# tier_up_review_policy and other guardrail settings live here. The agent must
# have no normal write-path to its own constraints (mirrors human-only PM
# deactivation). Runs before the prof_mode branches, so it holds when PM is off.
_HOOKS_CFG_BLOCK="BLOCKED — HUMAN-ONLY CONFIG

~/.claude/ironclaude-hooks-config.json holds guardrail settings. The guardrail keys
(tier_up_review_policy, debug_allow_config_writes) can only be changed by a HUMAN editing
the file on disk. The benign keys (validation_backend, ollama, timeout_seconds) may be
changed via a full-file Write tool call that preserves the guardrail keys.

Do NOT change guardrail keys."
# NotebookEdit is intentionally not routed: FILE_PATH (extracted at line ~15 from
# .tool_input.file_path // .tool_input.command) is never populated for a NotebookEdit
# event (notebook_path). config_guard_decision routes Edit/MultiEdit/Write/Bash; every
# other tool falls through to "allow".
#
# Key-scoped anti-tamper for the hooks-config file. All routing + policy is in the tested
# config_guard_decision (config-guard.sh): Write is key-scoped (benign keys allowed,
# guardrail/unknown blocked, case-insensitive), Edit/MultiEdit are hard-blocked (partial
# fragments), Bash is BEST-EFFORT (NOT provable — interpreter/split-filename/aliasing
# writes evade it). Runs before the prof_mode branches, so it holds when PM is off.
if [ "$(config_guard_decision "$TOOL_NAME" "$FILE_PATH" "$INPUT")" = "block" ]; then
  block_pretooluse "professional-mode-guard" "$_HOOKS_CFG_BLOCK"
fi

# ─── Helper: query design/plan paths for SUGGESTED_NEXT_ACTION ───
get_design_file() {
  sqlite3 "$DB_PATH" ".timeout 5000" \
    "SELECT file FROM registered_designs WHERE terminal_session='${SAFE_SESSION}' ORDER BY rowid DESC LIMIT 1;" 2>/dev/null || true
}
get_plan_json_path() {
  local design_file
  design_file=$(get_design_file)
  if [ -n "$design_file" ]; then
    echo "${design_file%-design.md}.plan.json"
  fi
}

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
  # .claude/rules/: allow Write/Edit during undecided setup (activation skill writes behavioral.md)
  if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
    if [[ "$FILE_PATH" == *"/.claude/rules/"* ]] || [[ "$FILE_PATH" == ".claude/rules/"* ]]; then
      log_hook "professional-mode-guard" "Allowed" ".claude/rules/ write during undecided setup"
      exit 0
    fi
  fi
  # Bash mkdir .claude/rules/: allow during undecided setup (activation skill creates directory)
  # Anchored to the command start + metachar-blocked so chained payloads cannot ride the exception.
  if [[ "$TOOL_NAME" == "Bash" ]]; then
    if ! _has_blocked_metachars "$FILE_PATH" \
        && [[ "$FILE_PATH" =~ ^[[:space:]]*mkdir[[:space:]] ]] \
        && [[ "$FILE_PATH" == *".claude/rules"* ]]; then
      log_hook "professional-mode-guard" "Allowed" "mkdir .claude/rules during undecided setup"
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
    skill_name=$(echo "$INPUT" | jq -r '.tool_input.skill // empty' 2>/dev/null || true)
    if [ "$skill_name" = "ironclaude:executing-plans" ]; then
      estimated_mem=$(db_read "professional-mode-guard" \
        "SELECT json_extract(plan_json, '\$.estimated_memory_gb') FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
      if [ -z "$estimated_mem" ] || [ "$estimated_mem" = "null" ]; then
        block_pretooluse "professional-mode-guard" "BLOCKED — MISSING MEMORY ESTIMATE

Plan is missing estimated_memory_gb. You must add a memory estimate before executing.

Call create_plan again with estimated_memory_gb in the plan JSON. Examples:
  0.5  — standard code changes (editing, linting, formatting)
  4.0  — running tests that use LLM inference indirectly
  8.0  — loading a medium Ollama model for direct inference
  14.0 — loading a large Ollama model (e.g. qwen3:32b)

Do NOT invoke executing-plans without estimated_memory_gb in the plan."
      fi
    fi
    log_hook "professional-mode-guard" "Allowed" "skill tool"
    exit 0
    ;;
esac

# ─── Debug mode: allow config writes when debug_allow_config_writes is set ───
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" ]]; then
  CONFIG_FILE="$HOME/.claude/ironclaude-hooks-config.json"
  DEBUG_WRITES="false"
  if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
    DEBUG_WRITES=$(jq -r '.debug_allow_config_writes // false' "$CONFIG_FILE" 2>/dev/null || echo "false")
  fi
  if [ "$DEBUG_WRITES" = "true" ]; then
    CANONICAL_PATH=$(realpath -m "$FILE_PATH" 2>/dev/null || echo "$FILE_PATH")
    if [[ "$CANONICAL_PATH" == "$HOME/.claude/"* ]]; then
      log_warning "professional-mode-guard" "DEBUG BYPASS — config write allowed: ${FILE_PATH}"
      exit 0
    fi
  fi
fi

case "$TOOL_NAME" in
  EnterPlanMode|ExitPlanMode)
    block_pretooluse "professional-mode-guard" "BLOCKED — USE BRAINSTORMING INSTEAD

EnterPlanMode and ExitPlanMode are disabled when professional mode is active.

Call the Skill tool with:
  skill: \"ironclaude:brainstorming\"

Do NOT use EnterPlanMode or ExitPlanMode. Use the brainstorming skill for all design work."
    ;;
esac

# Read workflow_stage once for all write-tool decisions (eliminates duplicate reads — see P5)
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || \
      "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then
  WORKFLOW=$(db_read_or_fail "professional-mode-guard" \
    "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';") || {
    block_pretooluse "professional-mode-guard" "BLOCKED — DATABASE ERROR

Cannot read workflow_stage from the database. This is a temporary error.

Try your action again. If this persists, report the error to the user."
  }
fi

# docs/ path whitelist (design + plan gate)
if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "NotebookEdit" ]]; then
  if [[ "$FILE_PATH" == *"/docs/"* ]] || [[ "$FILE_PATH" == "docs/"* ]]; then
    # Design documents require active brainstorming
    if [[ "$FILE_PATH" == *-design.md ]]; then
      if [ "$WORKFLOW" != "brainstorming" ] && [ "$WORKFLOW" != "design_ready" ] && ! ([ "$WORKFLOW" = "executing" ] && [ -f "$FILE_PATH" ]); then
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
  # Not executing: block write tools (architect mode)
  if [ "$WORKFLOW" != "executing" ]; then
    # Deny-first: block dangerous git commands before any allow-exception
    if [ "$TOOL_NAME" = "Bash" ] && echo "$FILE_PATH" | grep -qE '\bgit\b.*\b(commit|push|merge|rebase)\b'; then
      block_pretooluse "professional-mode-guard" "BLOCKED — GIT COMMIT/PUSH NOT ALLOWED

Git commit, push, merge, and rebase are blocked when not in the executing stage.

Do NOT run git commit, git push, git merge, or git rebase outside of plan execution."
    fi
    # Exception: allow git add (staging) in Bash — anchored, no chaining/redirection
    if [ "$TOOL_NAME" = "Bash" ]; then
      if ! _has_blocked_metachars "$FILE_PATH" && echo "$FILE_PATH" | grep -qE '^\s*git\s+add\b'; then
        log_hook "professional-mode-guard" "Allowed" "git staging"
        exit 0
      fi
    fi
    # Exception: allow read-only git commands at any workflow stage (no chaining — mirrors git-add guard above)
    if [ "$TOOL_NAME" = "Bash" ] && ! _has_blocked_metachars "$FILE_PATH" && echo "$FILE_PATH" | grep -qE '^\s*git\s+(diff|status|log|show|blame|branch|rev-list|ls-files|ls-tree|tag|remote|reflog|stash)\b'; then
      log_hook "professional-mode-guard" "Allowed" "read-only git command"
      exit 0
    fi
    # Exception: allow specific read-only commands during code review
    if [ "$TOOL_NAME" = "Bash" ] && [ "$WORKFLOW" = "reviewing" ]; then
      # Locally-scoped -C normalization for the make-test member of the allowlist
      # below only. A no-op for any non-make command, so it cannot affect the
      # sqlite3/git/pytest/cat/etc. members of the same alternation.
      MAKE_NORMALIZED_REVIEW=$(echo "$FILE_PATH" | sed -E 's/^([[:space:]]*make)[[:space:]]+-C[[:space:]]+[^[:space:]]+[[:space:]]+/\1 /')
      if _has_blocked_metachars "$FILE_PATH"; then
        block_pretooluse "professional-mode-guard" "BLOCKED — COMMAND CHAINING/REDIRECTION NOT ALLOWED DURING REVIEW

Shell chaining/redirection operators (; && || | backtick \$() > <) are not permitted during code review.

Allowed commands: sqlite3, git diff/status/log/show/blame/ls-files, pytest, make test, cat, head, tail, wc, grep, rg, find, ls

Do NOT run commands with shell operators during the reviewing stage."
      elif echo "$MAKE_NORMALIZED_REVIEW" | grep -qE '^\s*(sqlite3|git\s+(diff|status|log|show|blame|ls-files)|pytest|make\s+test|cat|head|tail|wc|grep|rg|find|ls)\b'; then
        if echo "$FILE_PATH" | grep -qE '^\s*sqlite3\b' && echo "$FILE_PATH" | grep -qiE '\b(UPDATE|INSERT|DELETE|DROP|ALTER|CREATE|REPLACE)\b'; then
          block_pretooluse "professional-mode-guard" "BLOCKED — SQLITE WRITE OPERATIONS NOT ALLOWED DURING REVIEW

You cannot modify database state during code review. Only SELECT and read-only operations are permitted.

Do NOT attempt to modify the database directly. The MCP state manager is the only authorized path to update session state."
        fi
        if _find_has_write_action "$FILE_PATH"; then
          block_pretooluse "professional-mode-guard" "BLOCKED — find write/exec action not allowed

find -exec/-execdir/-delete/-fls/-fprint*/-ok* can modify the filesystem and are not permitted during code review.

Use find for searching only."
        fi
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
    # Exception: allow read-only research bash in ALL non-executing stages.
    # This build exposes no Grep/Glob tool, so Bash is the only filesystem-
    # enumeration mechanism; read-only research must work in every stage. The
    # predicate blocks chaining, redirection, newlines, and find write/exec
    # actions, so this cannot become a write path. We are already inside the
    # `WORKFLOW != executing` branch, so executing is unaffected.
    if [ "$TOOL_NAME" = "Bash" ] && is_readonly_research_bash "$FILE_PATH"; then
      log_hook "professional-mode-guard" "Allowed" "read-only research bash"
      exit 0
    fi
    # Exception: allow make test* commands at any workflow stage — anchored, no chaining
    if [ "$TOOL_NAME" = "Bash" ]; then
      # Locally-scoped -C normalization for this check only — does not touch $FILE_PATH,
      # which is reused by unrelated git-command checks elsewhere in this file.
      MAKE_NORMALIZED=$(echo "$FILE_PATH" | sed -E 's/^([[:space:]]*make)[[:space:]]+-C[[:space:]]+[^[:space:]]+[[:space:]]+/\1 /')
      if ! _has_blocked_metachars "$FILE_PATH" && echo "$MAKE_NORMALIZED" | grep -qE '^\s*make\s+test'; then
        log_hook "professional-mode-guard" "Allowed" "make test* command"
        exit 0
      fi
    fi
    # Build SUGGESTED_NEXT_ACTION based on current workflow stage
    NEXT_ACTION=""
    case "$WORKFLOW" in
      idle|brainstorming)
        NEXT_ACTION="SUGGESTED_NEXT_ACTION: Skill(skill=\"ironclaude:brainstorming\", args=\"\")"
        ;;
      design_ready)
        _DESIGN_PATH=$(get_design_file)
        if [ -n "$_DESIGN_PATH" ]; then
          NEXT_ACTION="SUGGESTED_NEXT_ACTION: Skill(skill=\"ironclaude:writing-plans\", args=\"${_DESIGN_PATH}\")"
        fi
        ;;
      plan_ready)
        _PLAN_PATH=$(get_plan_json_path)
        if [ -n "$_PLAN_PATH" ]; then
          NEXT_ACTION="SUGGESTED_NEXT_ACTION: Skill(skill=\"ironclaude:executing-plans\", args=\"${_PLAN_PATH} --mode=inline\")"
        fi
        ;;
    esac

    block_pretooluse "professional-mode-guard" "BLOCKED — WRITE TOOLS NOT ALLOWED

The current workflow stage is '${WORKFLOW}'. Write tools (Edit, Write, Bash) are only allowed during plan execution.

Professional mode enforces a brainstorm → plan → execute workflow. Write tools are restricted to the execution phase to ensure all changes are planned and reviewed.

To reach execution, follow the workflow:
1. Call Skill tool with skill: \"ironclaude:brainstorming\" to design
2. Call Skill tool with skill: \"ironclaude:writing-plans\" to plan
3. Call Skill tool with skill: \"ironclaude:executing-plans\" to execute

Do NOT use Edit, Write, or Bash until you are in the executing stage.

${NEXT_ACTION}"
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

Each task specifies which files it may modify. This prevents unplanned changes from slipping in.

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
    if ! _has_blocked_metachars "$FILE_PATH" && echo "$FILE_PATH" | grep -qE '^\s*git\s+add\b'; then
      log_hook "professional-mode-guard" "Allowed" "git staging"
      exit 0
    fi
  fi

  # Check review_pending
  REVIEW_PENDING=$(sqlite3 "$DB_PATH" ".timeout 10000" \
    "SELECT review_pending FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
  if [ "$REVIEW_PENDING" = "1" ]; then
    if [[ "$TOOL_NAME" == "Edit" || "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "MultiEdit" || "$TOOL_NAME" == "Bash" || "$TOOL_NAME" == "NotebookEdit" ]]; then
      # Dual-check: verify a submitted task actually exists in the current wave.
      # Stale flags occur when get-back-to-work advances tasks to review_passed without
      # clearing sessions.review_pending, or after worker compaction loses review context.
      CURRENT_WAVE=$(sqlite3 "$DB_PATH" ".timeout 5000" \
        "SELECT current_wave FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
      SUBMITTED_COUNT=$(sqlite3 "$DB_PATH" ".timeout 5000" \
        "SELECT COUNT(*) FROM wave_tasks WHERE terminal_session='${SAFE_SESSION}' AND wave_number='${CURRENT_WAVE}' AND status='submitted';" 2>/dev/null || echo "0")
      if [ "${SUBMITTED_COUNT:-0}" = "0" ]; then
        sqlite3 "$DB_PATH" ".timeout 5000" \
          "UPDATE sessions SET review_pending=0, review_block_count=0 WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true
        log_hook "professional-mode-guard" "Auto-cleared" "stale review_pending — 0 submitted tasks in wave ${CURRENT_WAVE}"
        exit 0
      fi
      sqlite3 "$DB_PATH" ".timeout 5000" \
        "UPDATE sessions SET review_block_count = review_block_count + 1 WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || true
      REVIEW_BLOCK_COUNT=$(sqlite3 "$DB_PATH" ".timeout 5000" \
        "SELECT review_block_count FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
      if [ "${REVIEW_BLOCK_COUNT:-0}" -ge 5 ]; then
        block_pretooluse "professional-mode-guard" "HARD FAILURE — REVIEW DEADLOCK DETECTED

review_pending=1 has blocked 5+ tool calls. You cannot complete the pending code review (likely lost context due to compaction).

DO NOT attempt to modify the database. DO NOT attempt workarounds.

Stop all work immediately. The orchestrator will detect your idle state and take corrective action."
      else
        block_pretooluse "professional-mode-guard" "BLOCKED — CODE REVIEW PENDING

You submitted work for review but code review has not completed yet. Write tools are blocked.

Call the Skill tool with:
  skill: \"ironclaude:code-review\"
  args: \"--task-boundary\"

Do NOT use Edit, Write, MultiEdit, or Bash until code review completes."
      fi
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
