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
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.notebook_path // .tool_input.command // empty' 2>/dev/null || true)
FILE_PATH=$(normalize_path "$FILE_PATH")
RAW_PROJECT_ROOT=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
if [ -n "$RAW_PROJECT_ROOT" ]; then
  PROJECT_ROOT=$(cd -- "$RAW_PROJECT_ROOT" 2>/dev/null && pwd -P) || \
    PROJECT_ROOT=$(pwd -P)
else
  PROJECT_ROOT=$(pwd -P)
fi
PROJECT_ROOT=$(normalize_path "$PROJECT_ROOT")
if [ "$PROJECT_ROOT" != "/" ]; then
  PROJECT_ROOT="${PROJECT_ROOT%/}"
fi

SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

is_root_setup_file() {
  local candidate="$1"
  local filename="$2"
  if ! [[ "$candidate" == "$filename" \
    || "$candidate" == "./$filename" \
    || "$candidate" == "$PROJECT_ROOT/$filename" ]]; then
    return 1
  fi
  is_safe_setup_file_target "$PROJECT_ROOT/$filename" "$PROJECT_ROOT"
}

is_behavioral_rules_file() {
  local candidate="$1"
  if ! [[ "$candidate" == ".claude/rules/behavioral.md" \
    || "$candidate" == "./.claude/rules/behavioral.md" \
    || "$candidate" == "$PROJECT_ROOT/.claude/rules/behavioral.md" ]]; then
    return 1
  fi
  is_safe_setup_file_target \
    "$PROJECT_ROOT/.claude/rules/behavioral.md" \
    "$PROJECT_ROOT/.claude/rules"
}

is_physically_owned_ancestor() {
  local requested="$1"
  local ancestor="$requested"
  local physical

  while [ ! -e "$ancestor" ] && [ ! -L "$ancestor" ] \
      && [ "$ancestor" != "$PROJECT_ROOT" ]; do
    ancestor=$(dirname "$ancestor")
  done

  [ -d "$ancestor" ] || return 1
  [ ! -L "$ancestor" ] || return 1
  physical=$(cd -- "$ancestor" 2>/dev/null && pwd -P) || return 1
  [ "$physical" = "$ancestor" ]
}

is_safe_setup_file_target() {
  local target="$1"
  local expected_parent="$2"
  local physical_parent
  [ ! -L "$target" ] || return 1
  [ -d "$expected_parent" ] || return 1
  [ ! -L "$expected_parent" ] || return 1
  physical_parent=$(cd -- "$expected_parent" 2>/dev/null && pwd -P) || return 1
  [ "$physical_parent" = "$expected_parent" ]
}

is_safe_rules_directory_target() {
  local target="$PROJECT_ROOT/.claude/rules"
  [ ! -L "$target" ] || return 1
  is_physically_owned_ancestor "$target"
}

has_command_input_key() {
  printf '%s' "$INPUT" | jq -e \
    '(.tool_input | type) == "object" and (.tool_input | has("command"))' \
    >/dev/null 2>&1
}

is_safe_codex_agents_patch() {
  local patch_command counts begin_count end_count operation_count move_count
  local operation_line operation target

  # Native Codex ApplyPatchHandler emits exact tool_name=apply_patch with one
  # command field. Reject hybrid or invented input shapes.
  printf '%s' "$INPUT" | jq -e '
    .tool_name == "apply_patch"
    and (.tool_input | type) == "object"
    and ((.tool_input | keys) == ["command"])
    and (.tool_input.command | type) == "string"
    and (.tool_input.command
      | explode
      | all(. == 10 or (. >= 32 and . != 127)))
    and (
      (.tool_input.command | startswith("*** Begin Patch\n"))
      and (
        (.tool_input.command | endswith("\n*** End Patch"))
        or (.tool_input.command | endswith("\n*** End Patch\n"))
      )
    )
  ' >/dev/null 2>&1 || return 1

  patch_command=$(printf '%s' "$INPUT" \
    | jq -r '.tool_input.command' 2>/dev/null) || return 1

  counts=$(printf '%s\n' "$patch_command" | awk '
    $0 == "*** Begin Patch" { begin_count++ }
    $0 == "*** End Patch" { end_count++ }
    $0 ~ /^\*\*\* (Add|Update|Delete) File: / { operation_count++ }
    $0 ~ /^\*\*\* Move to: / { move_count++ }
    END {
      printf "%d %d %d %d", begin_count, end_count, operation_count, move_count
    }
  ') || return 1
  read -r begin_count end_count operation_count move_count <<< "$counts"
  [ "$begin_count" -eq 1 ] || return 1
  [ "$end_count" -eq 1 ] || return 1
  [ "$operation_count" -eq 1 ] || return 1
  [ "$move_count" -eq 0 ] || return 1

  operation_line=$(printf '%s\n' "$patch_command" \
    | awk '/^\*\*\* (Add|Update|Delete) File: / { print; exit }') || return 1
  case "$operation_line" in
    "*** Add File: "*)
      operation="Add"
      target="${operation_line#*** Add File: }"
      ;;
    "*** Update File: "*)
      operation="Update"
      target="${operation_line#*** Update File: }"
      ;;
    *)
      return 1
      ;;
  esac

  is_root_setup_file "$target" "AGENTS.md" || return 1
  case "$operation" in
    Add)
      [ ! -e "$PROJECT_ROOT/AGENTS.md" ] \
        && [ ! -L "$PROJECT_ROOT/AGENTS.md" ]
      ;;
    Update)
      [ -f "$PROJECT_ROOT/AGENTS.md" ] \
        && [ ! -L "$PROJECT_ROOT/AGENTS.md" ]
      ;;
    *)
      return 1
      ;;
  esac
}

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
# NotebookEdit is not routed to config_guard_decision: it is tool-gated to
# Edit/MultiEdit/Write/Bash, so NotebookEdit falls through to "allow" here regardless.
# FILE_PATH IS populated for NotebookEdit (from .tool_input.notebook_path at the
# extraction above) so the executing-stage allowed_files whitelist applies to it.
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
prof_mode=$(db_read_allow_missing_session "professional-mode-guard" \
  "SELECT professional_mode FROM sessions WHERE terminal_session='${SAFE_SESSION}';") || {
  block_pretooluse "professional-mode-guard" "BLOCKED — DATABASE ERROR

Cannot read professional_mode from the database. This is a temporary error.

Try your action again. If this persists, report the error to the user."
}
if [ -z "$prof_mode" ]; then
  prof_mode="undecided"
fi

# ─── UNDECIDED: block everything except Read/Grep/Glob and mode-toggle Skills ───
if [ "$prof_mode" = "undecided" ]; then
  # AskUserQuestion: always allow in UNDECIDED (activation skill needs to prompt user)
  if [ "$TOOL_NAME" = "AskUserQuestion" ]; then
    log_hook "professional-mode-guard" "Allowed" "AskUserQuestion in undecided"
    exit 0
  fi
  # Native Codex apply_patch: allow one exact root AGENTS.md Add/Update patch.
  # Only the exact native apply_patch event can enter this strict command route.
  # Synthetic Write events carrying command stay out of legacy file-path setup.
  if [ "$TOOL_NAME" = "apply_patch" ] && has_command_input_key; then
    if is_safe_codex_agents_patch; then
      log_hook "professional-mode-guard" "Allowed" \
        "native Codex root AGENTS patch during undecided setup"
      exit 0
    fi
  fi
  # Provider-owned root setup files: allow only exact project-root targets.
  if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]] \
      && ! has_command_input_key; then
    if is_root_setup_file "$FILE_PATH" "AGENTS.md" \
        || is_root_setup_file "$FILE_PATH" "CLAUDE.md"; then
      log_hook "professional-mode-guard" "Allowed" "root instruction write during undecided setup"
      exit 0
    fi
  fi
  # Claude behavioral rules: allow only the exact owned file at project root.
  if [[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]]; then
    if is_behavioral_rules_file "$FILE_PATH"; then
      log_hook "professional-mode-guard" "Allowed" "behavioral rules write during undecided setup"
      exit 0
    fi
  fi
  # Bash mkdir: allow only the literal project-relative behavioral-rules directory.
  if [[ "$TOOL_NAME" == "Bash" ]]; then
    if ! _has_blocked_metachars "$FILE_PATH" \
        && [[ "$FILE_PATH" =~ ^[[:space:]]*mkdir[[:space:]]+(-p[[:space:]]+)?(\./)?\.claude/rules/?[[:space:]]*$ ]] \
        && is_safe_rules_directory_target; then
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
    if [ "$TOOL_NAME" = "Bash" ] && ! _has_blocked_metachars "$FILE_PATH" && is_readonly_git "$FILE_PATH"; then
      log_hook "professional-mode-guard" "Allowed" "read-only git command"
      exit 0
    fi
    # Exception: allow specific read-only commands during code review
    if [ "$TOOL_NAME" = "Bash" ] && [ "$WORKFLOW" = "reviewing" ]; then
      if _has_blocked_metachars "$FILE_PATH"; then
        block_pretooluse "professional-mode-guard" "BLOCKED — COMMAND CHAINING/REDIRECTION NOT ALLOWED DURING REVIEW

Shell chaining/redirection operators (; && || | backtick \$() > <) are not permitted during code review.

Allowed commands: sqlite3, git diff/status/log/show/blame/ls-files/check-ignore, git -C <path> forms, pytest, make test, cat, head, tail, wc, grep, rg, find, ls, diff

Do NOT run commands with shell operators during the reviewing stage."
      elif is_review_allowed "$FILE_PATH"; then
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
  sqlite3, git diff/status/log/show/blame/ls-files/check-ignore, git -C <path> forms,
  pytest, make test, cat, head, tail, wc, grep, rg, find, ls, diff
  (pytest also accepts VAR=x prefixes and <path>/python -m pytest)

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
    # Exception: allow Edit/Write/MultiEdit to allowed_files DURING code review.
    # code-review Step 7.1 (Fix-First Pass) makes mechanical edits while
    # workflow_stage='reviewing'; without this they deadlock. FAIL-CLOSED: exit 0
    # ONLY on a positive allowed-file match. Any state where membership cannot be
    # determined (no wave, empty allowed_files, missing jq, query failure) does NOT
    # exit — it falls through to the write-tools block below. Distinct REVIEW_*
    # vars avoid clobbering the executing-stage check's vars.
    if [ "$WORKFLOW" = "reviewing" ] \
       && { [ "$TOOL_NAME" = "Edit" ] || [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "MultiEdit" ] || [ "$TOOL_NAME" = "NotebookEdit" ]; } \
       && [ -n "$FILE_PATH" ]; then
      REVIEW_WAVE=$(sqlite3 "$DB_PATH" ".timeout 10000" \
        "SELECT current_wave FROM sessions WHERE terminal_session='${SAFE_SESSION}';" 2>/dev/null || echo "0")
      SAFE_REVIEW_WAVE=$(echo "$REVIEW_WAVE" | sed "s/'/''/g")
      if [ -n "$REVIEW_WAVE" ] && [ "$REVIEW_WAVE" != "0" ]; then
        REVIEW_ALLOWED_FILES=$(sqlite3 "$DB_PATH" ".timeout 10000" \
          "SELECT allowed_files FROM wave_tasks WHERE terminal_session='${SAFE_SESSION}' AND wave_number='${SAFE_REVIEW_WAVE}';" 2>/dev/null || true)
        if [ -n "$REVIEW_ALLOWED_FILES" ] && command -v jq &>/dev/null; then
          REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
          if [ -n "$REPO_ROOT" ]; then
            REVIEW_NORMALIZED_FILE="${FILE_PATH#${REPO_ROOT}/}"
          else
            REVIEW_NORMALIZED_FILE="$FILE_PATH"
          fi
          REVIEW_FILE_ALLOWED="false"
          while IFS= read -r allowed_json; do
            if echo "$allowed_json" | jq -e 'type == "array"' &>/dev/null; then
              if echo "$allowed_json" | jq -r '.[]' 2>/dev/null | grep -qxF "$FILE_PATH"; then
                REVIEW_FILE_ALLOWED="true"
                break
              fi
              if echo "$allowed_json" | jq -r '.[]' 2>/dev/null | grep -qxF "$REVIEW_NORMALIZED_FILE"; then
                REVIEW_FILE_ALLOWED="true"
                break
              fi
            fi
          done <<< "$REVIEW_ALLOWED_FILES"
          if [ "$REVIEW_FILE_ALLOWED" = "true" ]; then
            log_hook "professional-mode-guard" "Allowed" "reviewing-stage edit to allowed_file"
            exit 0
          fi
        fi
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

Read-only research commands ARE permitted at this stage:
  cat, head, tail, wc, grep, rg, find, ls, diff
They must contain no shell metacharacters (; & | \` \$( < >) — note that a pipe
inside a quoted regex is currently treated as a shell pipe and rejected.

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
