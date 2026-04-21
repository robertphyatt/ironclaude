#!/bin/bash
# get-back-to-work-claude.sh - Stop hook (thin MCP client)
#
# Evaluates Claude's response against up to 6 independent checks:
#   1. Bypass detection (always when prof mode active)
#   2. Continuation detection (always when prof mode active)
#   3. Rigor check (natural stops only, expanded: assumptions + YAGNI)
#   4. Prediction check (natural stops only)
#   5. COA quality (brainstorming natural stops only)
#   6. Evidence quality (natural stops only)
#
# Each check runs as a parallel background subshell with its own focused
# single-question prompt. Results are collected and priority-ordered.
#
# State reads: sqlite3 (direct DB reads)
# State writes: sqlite3 via db_write_or_fail()
#
# NOTE: Strict mode (set -euo pipefail) is enabled via run_hook().
# Background subshells inherit strict mode but have their own error traps.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/plan-validator.sh"

# Source shared logging (provides db_read_or_fail, db_write_or_fail, block_stop, etc.)
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  echo '{"systemMessage": "[GET-BACK-TO-WORK]: CRITICAL - Failed to load hook-logger.sh!"}'
  echo "CRITICAL: hook-logger.sh failed to load" >&2
fi
run_hook "GET-BACK-TO-WORK"

EVENT=$(cat)
INPUT="$EVENT"
init_session_id

# Verify plan-validator.sh loaded successfully
if ! type call_validation_llm &>/dev/null; then
    echo '{"decision": "approve", "reason": "Could not load plan-validator.sh", "systemMessage": "[GET-BACK-TO-WORK]: Passed - plan-validator load failed"}'
    exit 0
fi

# =============================================================================
# DB PATH + HELPER
# =============================================================================

DB_PATH="$HOME/.claude/ironclaude.db"
SAFE_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")

# Read a column from the sessions table for the current session.
# Falls back to a default if DB or session doesn't exist.
db_read() {
    local column="$1"
    local default="${2:-}"
    if [ ! -f "$DB_PATH" ]; then
        echo "$default"
        return
    fi
    local val
    val=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
      "SELECT $column FROM sessions WHERE terminal_session = '${SAFE_SESSION}' LIMIT 1;" 2>/dev/null) || true
    if [ -z "$val" ]; then
        echo "$default"
    else
        echo "$val"
    fi
}

# Read a column from sessions for critical gate decisions in Stop hooks.
# Unlike db_read: logs failures visibly instead of silently defaulting.
# Unlike db_read_or_fail: approves stop on failure (fail-open for Stop hooks).
db_read_or_approve() {
    local column="$1"
    local default="${2:-}"
    if [ ! -f "$DB_PATH" ]; then
        log_hook "GET-BACK-TO-WORK" "Warning" "DB not found reading $column — approving stop"
        echo '{"decision": "approve", "reason": "DB not found — fail-open for Stop hook"}'
        exit 0
    fi
    local val
    val=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
      "SELECT $column FROM sessions WHERE terminal_session = '${SAFE_SESSION}' LIMIT 1;" 2>/dev/null)
    if [ $? -ne 0 ]; then
        log_hook "GET-BACK-TO-WORK" "Warning" "DB read failed for $column — approving stop"
        echo '{"decision": "approve", "reason": "DB read failed — fail-open for Stop hook"}'
        exit 0
    fi
    if [ -z "$val" ]; then
        echo "$default"
    else
        echo "$val"
    fi
}

# =============================================================================
# PROFESSIONAL MODE GATE
# =============================================================================

PROF_MODE=$(db_read_or_approve "professional_mode" "undecided")

if [ "$PROF_MODE" = "off" ] || [ "$PROF_MODE" = "undecided" ]; then
    log_hook "GET-BACK-TO-WORK" "Disabled" "professional mode $PROF_MODE"
    echo '{"decision": "approve", "reason": "Professional mode disabled"}'
    exit 0
fi

# =============================================================================
# RECURSION PREVENTION
# =============================================================================

if [ "${CLAUDE_HOOK_JUDGE_MODE:-false}" = "true" ]; then
    echo '{"decision": "approve", "reason": "Running in judge mode", "systemMessage": "[GET-BACK-TO-WORK]: Passed - judge mode"}'
    exit 0
fi

# =============================================================================
# READ HOOK EVENT
# =============================================================================

STOP_HOOK_ACTIVE=$(echo "$EVENT" | jq -r '
  if .stop_hook_active != null then .stop_hook_active
  elif .loop_count != null then (.loop_count > 0)
  else false end' 2>/dev/null || true)
TRANSCRIPT_PATH=$(echo "$EVENT" | jq -r '.transcript_path // ""' 2>/dev/null || true)
TRANSCRIPT_PATH=$(normalize_path "$TRANSCRIPT_PATH")
SESSION_ID=$(echo "$EVENT" | jq -r '.session_id // .conversation_id // "unknown"' 2>/dev/null || true)

# Sanitize session ID for use in filenames
SAFE_SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')

# =============================================================================
# THROTTLE FILES
# =============================================================================

BLOCK_THROTTLE_FILE="/tmp/.claude-block-throttle-${SAFE_SESSION_ID}"
BYPASS_COUNTER_FILE="/tmp/.claude-bypass-counter-${SAFE_SESSION_ID}"
CURRENT_TIME=$(date +%s)

# =============================================================================
# UNIVERSAL BLOCK THROTTLE CHECK
# =============================================================================

# If this is already a continuation from a previous stop hook block, check throttle.
# This applies to ALL block types (rigor, prediction, continuation) -- not just continuation.
# Without this, false positives on rigor/prediction create unrecoverable infinite loops.
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    BLOCK_COUNT=0
    LAST_BLOCK_TIME=0

    if [ -f "$BLOCK_THROTTLE_FILE" ]; then
        THROTTLE_DATA=$(cat "$BLOCK_THROTTLE_FILE")
        BLOCK_COUNT=$(echo "$THROTTLE_DATA" | cut -d: -f1)
        LAST_BLOCK_TIME=$(echo "$THROTTLE_DATA" | cut -d: -f2)
        # Validate they're numbers
        if ! [[ "$BLOCK_COUNT" =~ ^[0-9]+$ ]]; then
            BLOCK_COUNT=0
        fi
        if ! [[ "$LAST_BLOCK_TIME" =~ ^[0-9]+$ ]]; then
            LAST_BLOCK_TIME=0
        fi
    fi

    TIME_SINCE_LAST=$((CURRENT_TIME - LAST_BLOCK_TIME))

    # Reset counter if it's been more than 5 minutes
    if [ "$TIME_SINCE_LAST" -gt 300 ]; then
        BLOCK_COUNT=0
    fi

    # If we've been blocked too many times recently, force stop to prevent infinite loops
    if [ "$BLOCK_COUNT" -ge 3 ] && [ "$TIME_SINCE_LAST" -lt 300 ]; then
        echo '{"decision": "approve", "reason": "Maximum block cycles reached (3 blocks in 5 min)", "systemMessage": "[GET-BACK-TO-WORK]: Stopped - max blocks reached, likely false positives"}'
        rm -f "$BLOCK_THROTTLE_FILE"
        rm -f "$BYPASS_COUNTER_FILE"
        exit 0
    fi
fi

# =============================================================================
# HELPER: INCREMENT BLOCK COUNTER
# =============================================================================
# Defined here (before first use) so it's available in both the workflow stage
# check and the grading decision logic below.

increment_block_counter() {
    local count=0
    if [ -f "$BLOCK_THROTTLE_FILE" ]; then
        local data
        data=$(cat "$BLOCK_THROTTLE_FILE")
        count=$(echo "$data" | cut -d: -f1)
        if ! [[ "$count" =~ ^[0-9]+$ ]]; then
            count=0
        fi
    fi
    count=$((count + 1))
    [ -L "$BLOCK_THROTTLE_FILE" ] && rm -f "$BLOCK_THROTTLE_FILE"
    echo "$count:$CURRENT_TIME" > "$BLOCK_THROTTLE_FILE"
}

# =============================================================================
# WORKFLOW STAGE CHECK + CODE REVIEW GATE
# =============================================================================
# If executing a plan, gate stop on: tasks complete OR code review passed.

WORKFLOW_STAGE=$(db_read_or_approve "workflow_stage" "idle")

if is_plan_active "$WORKFLOW_STAGE"; then
    # Defense-in-depth: verify a plan is actually loaded
    PLAN_JSON_CHECK=$(db_read "plan_json" "")
    if [ -z "$PLAN_JSON_CHECK" ]; then
        log_hook "GET-BACK-TO-WORK" "Approved" "plan-active stage but no plan_json loaded — approving stop"
        echo '{"decision": "approve", "reason": "No plan loaded", "systemMessage": "[GET-BACK-TO-WORK]: Passed - no plan_json"}'
        exit 0
    fi

    # Detect if this is a subagent session (skip code review gate for subagents)
    IS_SUBAGENT="false"
    if [ -f "$DB_PATH" ]; then
        PARENT_SESSION=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
          "SELECT parent_session FROM subagent_sessions WHERE child_session = '${SAFE_SESSION}' LIMIT 1;" 2>/dev/null) || true
        if [ -n "$PARENT_SESSION" ]; then
            IS_SUBAGENT="true"
        fi
    fi

    # Read current_wave for wave-scoped queries (H3 fix)
    CURRENT_WAVE=$(db_read "current_wave" "0")
    SAFE_WAVE_NUM=$(echo "$CURRENT_WAVE" | sed "s/'/''/g")

    # Count tasks by status (scoped to current wave)
    SUBMITTED_COUNT="0"
    IN_PROGRESS_OR_PENDING_COUNT="0"
    if [ -f "$DB_PATH" ]; then
        SUBMITTED_COUNT=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
          "SELECT COUNT(*) FROM wave_tasks WHERE terminal_session = '${SAFE_SESSION}' AND wave_number = '${SAFE_WAVE_NUM}' AND status = 'submitted';" \
          2>/dev/null) || SUBMITTED_COUNT="0"
        IN_PROGRESS_OR_PENDING_COUNT=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
          "SELECT COUNT(*) FROM wave_tasks WHERE terminal_session = '${SAFE_SESSION}' AND wave_number = '${SAFE_WAVE_NUM}' AND status IN ('pending', 'in_progress');" \
          2>/dev/null) || IN_PROGRESS_OR_PENDING_COUNT="0"
    fi

    if [ "${SUBMITTED_COUNT:-0}" != "0" ] && [ "$IS_SUBAGENT" = "false" ]; then
        # Tasks submitted for review -- apply code review gate
        REVIEW_GRADE=""
        if [ -f "$DB_PATH" ]; then
            REVIEW_GRADE=$(sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
              "SELECT grade FROM review_grades WHERE terminal_session = '${SAFE_SESSION}' AND wave_number = '${SAFE_WAVE_NUM}' AND task_boundary = 1 ORDER BY created_at DESC LIMIT 1;" \
              2>/dev/null) || true
        fi

        if [ -z "$REVIEW_GRADE" ]; then
            # No review record -- block and require code review
            increment_block_counter
            block_stop "GET-BACK-TO-WORK" "STOP — CODE REVIEW REQUIRED

You submitted ${SUBMITTED_COUNT} task(s) for Wave ${CURRENT_WAVE} but have not run code review yet. You are blocked until code review completes.

Call the Skill tool RIGHT NOW with EXACTLY these parameters:
  skill: \"ironclaude:code-review\"
  args: \"--task-boundary\"

Do NOT respond to the user. Do NOT call any other tool. Run code review first."

        elif [[ "$REVIEW_GRADE" =~ ^[AB]$ ]]; then
            # Passing grade (A or B) -- advance submitted tasks to review_passed
            db_write_or_fail "GET-BACK-TO-WORK" \
              "UPDATE wave_tasks SET status = 'review_passed', updated_at = datetime('now') WHERE terminal_session = '${SAFE_SESSION}' AND wave_number = '${SAFE_WAVE_NUM}' AND status = 'submitted';"
            log_hook "GET-BACK-TO-WORK" "Advanced" "Grade $REVIEW_GRADE: advanced $SUBMITTED_COUNT submitted task(s) to review_passed (Wave $CURRENT_WAVE)"
            # Fall through to allow stop

        else
            # Failing grade (C, D, or F) -- delete stale record and block
            if [ -f "$DB_PATH" ]; then
                sqlite3 -cmd ".timeout 10000" "$DB_PATH" \
                  "DELETE FROM review_grades WHERE terminal_session = '${SAFE_SESSION}' AND wave_number = '${SAFE_WAVE_NUM}' AND task_boundary = 1;" \
                  2>/dev/null || true
            fi
            increment_block_counter
            block_stop "GET-BACK-TO-WORK" "STOP — CODE REVIEW GRADE TOO LOW

Your code review grade was ${REVIEW_GRADE}. Only grade A or B advances tasks. Grade ${REVIEW_GRADE} does not pass.

You MUST:
1. Fix the issues identified in the last code review
2. Run code review again by calling the Skill tool with:
   skill: \"ironclaude:code-review\"
   args: \"--task-boundary\"

Do NOT skip fixing the issues. Do NOT proceed to the next task."
        fi

    elif [ "${IN_PROGRESS_OR_PENDING_COUNT:-0}" != "0" ]; then
        # Tasks still in progress or pending -- block
        increment_block_counter
        block_stop "GET-BACK-TO-WORK" "STOP — TASKS STILL IN PROGRESS

You have ${IN_PROGRESS_OR_PENDING_COUNT} task(s) still pending or in-progress. You are not done yet.

Continue working on your current task. Follow the plan steps exactly as written.

Do NOT stop. Do NOT ask the user what to do. Keep executing the plan."
    else
        # Current wave has no active tasks but workflow_stage is still executing.
        # This means get_next_tasks() hasn't been called to advance to the next
        # wave or reach execution_complete. Block the stop.
        increment_block_counter
        block_stop "GET-BACK-TO-WORK" "STOP — WAVE COMPLETE, ADVANCE TO NEXT

All tasks in the current wave are done, but the plan is not finished yet.

Call the MCP tool mcp__plugin_ironclaude_state-manager__get_next_tasks RIGHT NOW to get the next wave of tasks or confirm plan completion.

Do NOT stop. Do NOT ask the user. Call get_next_tasks and continue."
    fi
fi

# =============================================================================
# TRANSCRIPT CHECK
# =============================================================================

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    echo '{"decision": "approve", "reason": "No transcript available", "systemMessage": "[GET-BACK-TO-WORK]: Passed - no transcript"}'
    exit 0
fi

# Extract Claude's last complete response from transcript JSONL
# The transcript has one JSON object per line with type, message.content, requestId fields.
# We find the last assistant requestId, then extract all text blocks for that response.
# This gives the LLM the full response to evaluate (bypass, rigor, prediction, continue checks).
RECENT_CONTEXT=""
LAST_ASSISTANT_REQ_ID=$(
  # Read last 500 lines, extract assistant requestIds, take the last one
  tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | \
  jq -r 'select(.type == "assistant") | .requestId // empty' 2>/dev/null | \
  tail -1 || true
)

if [ -n "$LAST_ASSISTANT_REQ_ID" ]; then
  # Extract all text content from this response (skip thinking blocks)
  RECENT_CONTEXT=$(
    tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | grep -F "\"$LAST_ASSISTANT_REQ_ID\"" | \
    jq -r '.message.content[]? | select(.type == "text") | .text // empty' 2>/dev/null || true
  )
fi

# Secondary extraction: the last assistant message may have been tool-use-only (no text blocks).
# This is common when Claude ends on a tool call (Skill, Bash, Read, etc.) — the assistant
# message contains only {"type": "tool_use"} blocks, so select(.type == "text") returns nothing.
# Walk backwards through ALL recent assistant messages to find one with text content.
if [ -z "$RECENT_CONTEXT" ]; then
  RECENT_CONTEXT=$(
    tail -n 500 "$TRANSCRIPT_PATH" 2>/dev/null | \
    jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text // empty' \
    2>/dev/null | tail -n 200 || true
  )
fi

# If still empty after secondary extraction, approve — sending raw JSONL to the grader
# always produces a false F grade (grader sees requestId/type/timestamp/usage metadata, not prose).
if [ -z "$RECENT_CONTEXT" ]; then
  echo '{"decision": "approve", "reason": "Could not extract Claude response text from transcript", "systemMessage": "[GET-BACK-TO-WORK]: Passed - extraction failed, grading skipped"}'
  exit 0
fi

# Cap at 200 lines to prevent oversized LLM prompts (keep tail where questions/predictions appear)
LINE_COUNT=$(echo "$RECENT_CONTEXT" | wc -l | tr -d ' ')
if [ "$LINE_COUNT" -gt 200 ]; then
  RECENT_CONTEXT=$(echo "$RECENT_CONTEXT" | tail -n 200)
fi

if [ -z "$RECENT_CONTEXT" ]; then
    echo '{"decision": "approve", "reason": "Could not read transcript content", "systemMessage": "[GET-BACK-TO-WORK]: Passed - empty transcript"}'
    exit 0
fi

# =============================================================================
# PROMPT DEFINITIONS (one per check)
# =============================================================================

# Each prompt is a focused single-question evaluation for maximum reliability
# with small models (qwen3:8b). The transcript is injected at call time.

# --- Prompt 1: Bypass Detection ---
BYPASS_PROMPT="You are a conversation analyzer. Examine this transcript and answer ONE question.

TRANSCRIPT:
TRANSCRIPT_PLACEHOLDER

---

Grade how well Claude is following professional mode workflows.

Examples of grade A (following properly):
- Claude is explaining code, reporting results, or answering questions
- Claude is using brainstorming, writing-plans, or executing-plans skills
- Claude is analyzing a problem or presenting options

Examples of grade D/F (bypassing):
- Claude tells the user to delete a flag file or hook script
- Claude provides commands to disable professional mode
- Claude suggests workarounds to avoid hook checks
- Claude suggests skipping brainstorming and jumping straight to implementation
- Claude offers to just make the change directly instead of using the plan workflow
- Claude agrees to skip writing a design doc or plan

A = Perfectly following professional mode -- explaining, reporting, answering questions, using proper workflows
B = Generally following professional mode -- no bypass instructions detected
C = Borderline -- may be suggesting workarounds to protections
D = Clearly suggesting ways around professional mode protections
F = Actively instructing the user to disable, delete, or circumvent protection systems

Respond with ONLY this JSON (no other text):
{\"reasoning\": \"brief explanation\", \"following_professional_mode\": \"A/B/C/D/F\"}"

BYPASS_SCHEMA='{"type":"object","properties":{"reasoning":{"type":"string"},"following_professional_mode":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","following_professional_mode"]}'

# --- Prompt 2: Continuation ---
CONTINUATION_PROMPT="You are a conversation analyzer. Examine this transcript and answer ONE question.

TRANSCRIPT:
TRANSCRIPT_PLACEHOLDER

---

Grade how diligently Claude finished its work before stopping.

Examples of grade A (finished):
- Claude completed a task and reported the results
- Claude asked the user a question like Which approach do you prefer?
- Claude presented options and is waiting for user input
- Claude finished a code review and summarized findings

Examples of grade D/F (unfinished):
- Claude said Now I will run the tests but did not run them
- Claude said Let me update the config file next but stopped before doing it
- Claude listed 5 tasks to do but only completed 2
- Claude described writing a plan, listed implementation tasks or waves, or said it would write the plan now, but did not invoke the writing-plans Skill tool

A = Perfectly finished -- work complete, or appropriately waiting for user input, or asking the user a question
B = Generally done -- no clear unfinished autonomous work remaining
C = Unclear -- may have stopped prematurely with work remaining
D = Likely stopped prematurely -- stated a next action but did not perform it
F = Clearly stopped with lots of autonomous work remaining and stated specific unperformed next actions

Respond with ONLY this JSON (no other text):
{\"reasoning\": \"brief explanation\", \"diligently_finished_work\": \"A/B/C/D/F\"}"

CONTINUATION_SCHEMA='{"type":"object","properties":{"reasoning":{"type":"string"},"diligently_finished_work":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","diligently_finished_work"]}'

# --- Prompt 3: Rigor (natural stops only) ---
RIGOR_PROMPT="You are a conversation analyzer. Examine this transcript and answer ONE question.

TRANSCRIPT:
TRANSCRIPT_PLACEHOLDER

---

Grade the rigor quality of Claude's response. Evaluate THREE aspects:

1. REASONING RIGOR: Did Claude establish principles before recommending?
2. ASSUMPTION CHALLENGING: Did Claude accept contradictory or infeasible requirements without questioning them?
3. SCOPE DISCIPLINE (YAGNI): Did Claude add features, abstractions, or scope beyond what was requested?

Examples of grade A (rigorous or no recommendation needed):
- Claude reported status: All 4 checks passed, changes staged
- Claude asked a question: Which approach do you prefer?
- Claude explained how something works without recommending an action
- Claude recommended approach B and first established why from principles
- Claude questioned a requirement that seemed contradictory
- Claude kept implementation focused on exactly what was requested

Examples of grade D/F (not rigorous):
- Claude said Just use option A, it is simpler without explaining why
- Claude agreed with the user suggestion without establishing reasoning
- Claude recommended a library without discussing trade-offs
- Claude accepted contradictory requirements without questioning them
- Claude added nice-to-have features or abstractions beyond what was requested

A = No recommendation made (status report, question, explanation), or principles clearly established before recommending. No unquestioned contradictions. No scope creep.
B = Recommendation present with reasonable reasoning. Minor scope additions acceptable if justified.
C = Recommendation with weak supporting principles, OR accepted a questionable requirement without challenge, OR minor unnecessary scope additions.
D = Recommendation with minimal reasoning, OR accepted clearly contradictory requirements, OR added significant unrequested scope.
F = Made a recommendation without establishing principles, OR accepted impossible/contradictory requirements without question, OR significant scope creep.

Respond with ONLY this JSON (no other text):
{\"reasoning\": \"brief explanation\", \"rigor_quality\": \"A/B/C/D/F\"}"

RIGOR_SCHEMA='{"type":"object","properties":{"reasoning":{"type":"string"},"rigor_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","rigor_quality"]}'

# --- Prompt 4: Prediction (natural stops only) ---
PREDICTION_PROMPT="You are a conversation analyzer. Examine this transcript and answer ONE question.

TRANSCRIPT:
TRANSCRIPT_PLACEHOLDER

---

Grade the prediction quality of Claude's response.

A prediction is when Claude states what it thinks the user will answer BEFORE asking a question. Examples:
- My prediction: You'll say B because...
- **My prediction:** You'll choose option 2 because...
- I think you'll want the simpler approach because...

If Claude did not ask any questions, prediction is not applicable -- grade A.

A = No questions asked to the user, OR Claude stated a prediction before each question it asked
B = Questions asked with reasonable predictions provided before them
C = Some questions have predictions, others do not
D = Questions asked with weak or token predictions
F = Asked the user questions requiring decisions without stating any prediction first

Respond with ONLY this JSON (no other text):
{\"reasoning\": \"brief explanation\", \"prediction_quality\": \"A/B/C/D/F\"}"

PREDICTION_SCHEMA='{"type":"object","properties":{"reasoning":{"type":"string"},"prediction_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","prediction_quality"]}'

# --- Prompt 5: COA Quality (brainstorming natural stops only) ---
COA_PROMPT="You are a conversation analyzer. Examine this transcript and answer ONE question.

TRANSCRIPT:
TRANSCRIPT_PLACEHOLDER

---

First, determine: Did Claude present 2 or more distinct options or approaches for the user to choose between?

If Claude did NOT present options (e.g., asked a question, presented a design section, discussed context): Grade A — COA criteria not applicable.

If Claude DID present options, evaluate them against these criteria:

1. DISTINGUISHABILITY: Are the options meaningfully different? Different architectures, different trade-off profiles, or different risk postures count. Minor variations of the same approach (e.g., \"use helper function A\" vs \"use slightly different helper function B\") do NOT count.

2. SUITABILITY: Does each option clearly address the stated problem? Are they aligned with the user's guidance and constraints?

3. FEASIBILITY: Are the options realistic given the codebase and available resources? Does any option propose something impossible or impractical?

A = No options presented, OR all options are distinct/suitable/feasible
B = Options present with minor overlap but fundamentally different strategies
C = One option lacks clear suitability or feasibility
D = Options are variations of the same approach, not genuinely different
F = Options are essentially identical, or blatantly unsuitable/infeasible

Respond with ONLY this JSON (no other text):
{\"reasoning\": \"brief explanation\", \"coa_quality\": \"A/B/C/D/F\"}"

COA_SCHEMA='{"type":"object","properties":{"reasoning":{"type":"string"},"coa_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","coa_quality"]}'

# --- Prompt 6: Evidence Quality (natural stops only) ---
EVIDENCE_PROMPT="You are a conversation analyzer. Examine this transcript and answer ONE question.

TRANSCRIPT:
TRANSCRIPT_PLACEHOLDER

---

Grade whether Claude backed claims with evidence or used hedging language without verification.

Examples of grade A (evidence-backed):
- Claude read a file and reported what it found
- Claude ran a test and reported the results
- Claude searched for a pattern and cited the matches
- No factual claims were made (asked a question, presented options)

Examples of grade D/F (guessing):
- Claude said this will probably work without testing
- Claude said the function likely does X without reading the code
- Claude stated this should be compatible without checking
- Claude made a definitive claim that contradicts the actual code

A = All claims supported by evidence (code reads, test runs, search results) OR no factual claims made
B = Most claims supported, minor hedging acceptable in low-stakes context
C = Some claims stated without evidence, or hedging language used for verifiable facts
D = Multiple unverified claims, or likely/probably/should work used for important decisions
F = Made definitive claims that are wrong, or guessed when search/verification was available

Respond with ONLY this JSON (no other text):
{\"reasoning\": \"brief explanation\", \"evidence_quality\": \"A/B/C/D/F\"}"

EVIDENCE_SCHEMA='{"type":"object","properties":{"reasoning":{"type":"string"},"evidence_quality":{"type":"string","enum":["A","B","C","D","F"]}},"required":["reasoning","evidence_quality"]}'

# =============================================================================
# PARALLEL CHECK EXECUTION
# =============================================================================

# Create temp directory for check results, clean up on exit
CHECK_TMPDIR=$(mktemp -d /tmp/.claude-stop-checks-XXXXXX)
trap 'rm -rf "$CHECK_TMPDIR"' EXIT

# Helper: run a single check in a subshell, write result to temp file
# Args: $1=check_name, $2=prompt_template, $3=schema, $4=field_name
run_check() {
    local check_name="$1"
    local prompt_template="$2"
    local schema="$3"
    local field_name="$4"
    local result_file="$CHECK_TMPDIR/${check_name}.json"

    # Inject transcript into prompt
    local prompt="${prompt_template//TRANSCRIPT_PLACEHOLDER/$RECENT_CONTEXT}"

    # Call LLM
    local llm_response
    llm_response=$(call_validation_llm "$prompt" "$schema" 2>/dev/null) || true

    if [ -z "$llm_response" ]; then
        # LLM failed -- treat as pass (grade A, never block on failed evaluation)
        echo '{"result": "A", "reasoning": "LLM call failed", "raw": ""}' > "$result_file"
        return
    fi

    # Parse the grade field
    local field_value
    field_value=$(echo "$llm_response" | jq -r ".$field_name" 2>/dev/null) || true
    local reasoning
    reasoning=$(echo "$llm_response" | jq -r '.reasoning // "No reasoning"' 2>/dev/null) || true

    # Validate grade (A/B/C/D/F)
    if ! [[ "$field_value" =~ ^[ABCDF]$ ]]; then
        # Parse failure -- treat as pass (grade A)
        echo "{\"result\": \"A\", \"reasoning\": \"parse failed\", \"raw\": $(echo "$llm_response" | jq -Rs '.')}" > "$result_file"
        return
    fi

    echo "{\"result\": \"$field_value\", \"reasoning\": $(echo "$reasoning" | jq -Rs '.'), \"raw\": $(echo "$llm_response" | jq -Rs '.')}" > "$result_file"
}

# =============================================================================
# SKILL-AWARE FIRING MATRIX
# =============================================================================

# Query active skill from DB (set by skill-state-bridge.sh on every Skill invocation)
ACTIVE_SKILL=$(db_read "active_skill" "")

# Determine which checks to fire based on active skill context
# Firing matrix (from design doc):
#   brainstorming:    bypass(+context), suppress-continuation, rigor, prediction, COA
#   debugging:        bypass, continuation, rigor, prediction
#   writing-plans:    bypass, continuation, rigor, prediction
#   executing-plans:  bypass, continuation, suppress-rigor, suppress-prediction
#   code-review:      bypass, continuation, rigor, prediction
#   no skill:         bypass, continuation, rigor, prediction

FIRE_BYPASS="true"
FIRE_CONTINUATION="true"
FIRE_RIGOR="true"
FIRE_PREDICTION="true"
FIRE_COA="false"
FIRE_EVIDENCE="true"
BYPASS_CONTEXT=""

case "$ACTIVE_SKILL" in
  *brainstorming*)
    FIRE_CONTINUATION="false"
    FIRE_COA="true"
    BYPASS_CONTEXT="Note: The user is currently brainstorming a design. Technical discussions about hooks, guards, state management, and enforcement mechanisms are expected and legitimate. Do NOT grade these as bypass attempts."
    ;;
  *executing-plans*)
    FIRE_RIGOR="false"
    FIRE_PREDICTION="false"
    FIRE_EVIDENCE="false"
    ;;
  *activate-professional-mode*|*deactivate-professional-mode*)
    # Mechanical state-management skills — no grading needed.
    # These skills produce output that bypass detection would flag
    # (sqlite commands, mode toggle confirmations) but it's all by design.
    log_hook "GET-BACK-TO-WORK" "Allowed" "state-management skill: $ACTIVE_SKILL"
    echo '{"decision": "approve", "reason": "State-management skill — no grading needed"}'
    exit 0
    ;;
esac

# Workflow-stage overrides (independent of active skill)
if [ "$WORKFLOW_STAGE" = "execution_complete" ]; then
    FIRE_CONTINUATION="false"
fi

# Determine if this is a forced continuation from a previous block
IS_FORCED_CONTINUATION="false"
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    IS_FORCED_CONTINUATION="true"
fi

# Inject skill context into bypass prompt if available
if [ -n "$BYPASS_CONTEXT" ]; then
    BYPASS_PROMPT="${BYPASS_PROMPT}

IMPORTANT CONTEXT:
${BYPASS_CONTEXT}"
fi

# Fire checks based on skill-aware matrix
BYPASS_PID=""
if [ "$FIRE_BYPASS" = "true" ]; then
    run_check "bypass" "$BYPASS_PROMPT" "$BYPASS_SCHEMA" "following_professional_mode" &
    BYPASS_PID=$!
fi

CONTINUATION_PID=""
if [ "$FIRE_CONTINUATION" = "true" ]; then
    run_check "continuation" "$CONTINUATION_PROMPT" "$CONTINUATION_SCHEMA" "diligently_finished_work" &
    CONTINUATION_PID=$!
fi

RIGOR_PID=""
if [ "$FIRE_RIGOR" = "true" ] && [ "$IS_FORCED_CONTINUATION" = "false" ]; then
    run_check "rigor" "$RIGOR_PROMPT" "$RIGOR_SCHEMA" "rigor_quality" &
    RIGOR_PID=$!
fi

PREDICTION_PID=""
if [ "$FIRE_PREDICTION" = "true" ] && [ "$IS_FORCED_CONTINUATION" = "false" ]; then
    run_check "prediction" "$PREDICTION_PROMPT" "$PREDICTION_SCHEMA" "prediction_quality" &
    PREDICTION_PID=$!
fi

COA_PID=""
if [ "$FIRE_COA" = "true" ] && [ "$IS_FORCED_CONTINUATION" = "false" ]; then
    run_check "coa" "$COA_PROMPT" "$COA_SCHEMA" "coa_quality" &
    COA_PID=$!
fi

EVIDENCE_PID=""
if [ "$FIRE_EVIDENCE" = "true" ] && [ "$IS_FORCED_CONTINUATION" = "false" ]; then
    run_check "evidence" "$EVIDENCE_PROMPT" "$EVIDENCE_SCHEMA" "evidence_quality" &
    EVIDENCE_PID=$!
fi

# Wait for all checks to complete
[ -n "$BYPASS_PID" ] && { wait $BYPASS_PID 2>/dev/null || true; }
[ -n "$CONTINUATION_PID" ] && { wait $CONTINUATION_PID 2>/dev/null || true; }
[ -n "$RIGOR_PID" ] && { wait $RIGOR_PID 2>/dev/null || true; }
[ -n "$PREDICTION_PID" ] && { wait $PREDICTION_PID 2>/dev/null || true; }
[ -n "$COA_PID" ] && { wait $COA_PID 2>/dev/null || true; }
[ -n "$EVIDENCE_PID" ] && { wait $EVIDENCE_PID 2>/dev/null || true; }

# =============================================================================
# READ RESULTS AND APPLY PRIORITY ORDER
# =============================================================================

# Helper: read a check result
read_check_result() {
    local check_name="$1"
    local result_file="$CHECK_TMPDIR/${check_name}.json"
    if [ -f "$result_file" ]; then
        cat "$result_file"
    else
        echo '{"result": "A", "reasoning": "check did not run", "raw": ""}'
    fi
}

# Read all results
BYPASS_RESULT=$(read_check_result "bypass")
CONTINUATION_RESULT=$(read_check_result "continuation")
RIGOR_RESULT=$(read_check_result "rigor")
PREDICTION_RESULT=$(read_check_result "prediction")
COA_RESULT=$(read_check_result "coa")
EVIDENCE_RESULT=$(read_check_result "evidence")

# Extract grades
BYPASS_GRADE=$(echo "$BYPASS_RESULT" | jq -r '.result' 2>/dev/null || echo "A")
CONTINUATION_GRADE=$(echo "$CONTINUATION_RESULT" | jq -r '.result' 2>/dev/null || echo "A")
RIGOR_GRADE=$(echo "$RIGOR_RESULT" | jq -r '.result' 2>/dev/null || echo "A")
PREDICTION_GRADE=$(echo "$PREDICTION_RESULT" | jq -r '.result' 2>/dev/null || echo "A")
COA_GRADE=$(echo "$COA_RESULT" | jq -r '.result' 2>/dev/null || echo "A")
EVIDENCE_GRADE=$(echo "$EVIDENCE_RESULT" | jq -r '.result' 2>/dev/null || echo "A")

# Extract reasoning and raw responses for logging
BYPASS_REASONING=$(echo "$BYPASS_RESULT" | jq -r '.reasoning' 2>/dev/null || echo "")
BYPASS_RAW=$(echo "$BYPASS_RESULT" | jq -r '.raw' 2>/dev/null || echo "")
CONTINUATION_REASONING=$(echo "$CONTINUATION_RESULT" | jq -r '.reasoning' 2>/dev/null || echo "")
CONTINUATION_RAW=$(echo "$CONTINUATION_RESULT" | jq -r '.raw' 2>/dev/null || echo "")
RIGOR_REASONING=$(echo "$RIGOR_RESULT" | jq -r '.reasoning' 2>/dev/null || echo "")
RIGOR_RAW=$(echo "$RIGOR_RESULT" | jq -r '.raw' 2>/dev/null || echo "")
PREDICTION_REASONING=$(echo "$PREDICTION_RESULT" | jq -r '.reasoning' 2>/dev/null || echo "")
PREDICTION_RAW=$(echo "$PREDICTION_RESULT" | jq -r '.raw' 2>/dev/null || echo "")
COA_REASONING=$(echo "$COA_RESULT" | jq -r '.reasoning' 2>/dev/null || echo "")
COA_RAW=$(echo "$COA_RESULT" | jq -r '.raw' 2>/dev/null || echo "")
EVIDENCE_REASONING=$(echo "$EVIDENCE_RESULT" | jq -r '.reasoning' 2>/dev/null || echo "")
EVIDENCE_RAW=$(echo "$EVIDENCE_RESULT" | jq -r '.raw' 2>/dev/null || echo "")

# =============================================================================
# DECISION LOGIC (priority order)
# =============================================================================

# Priority 0: Memory search gate (deterministic, fail-closed)
# If brainstorming or writing-plans is active and memory search hasn't been done, block.
if [[ "$ACTIVE_SKILL" =~ ^(brainstorming|writing-plans)$ ]]; then
    MEMORY_SEARCH_REQUIRED=$(db_read "memory_search_required" "1")
    if [ "$MEMORY_SEARCH_REQUIRED" = "1" ]; then
        increment_block_counter
        block_stop "GET-BACK-TO-WORK" "STOP — MEMORY SEARCH REQUIRED

You MUST search episodic memory before continuing with $ACTIVE_SKILL. This is blocking — no other action will unblock you. Do NOT write code, do NOT respond to the user, do NOT call any other tool.

Call the Task tool RIGHT NOW with EXACTLY these parameters:
  description: \"Search episodic memory\"
  subagent_type: \"ironclaude:search-conversations\"
  prompt: \"Search for prior decisions and context about [your current task topic]\"

The subagent_type field MUST contain 'search-conversations' exactly as shown. Any other tool call will keep you blocked.

Do this NOW. You will remain blocked on every stop attempt until you make this exact Task tool call."
    fi
fi

# Priority 1: Bypass attempt detection (grade D/F = block, C = warn)
if [[ "$BYPASS_GRADE" =~ ^[DF]$ ]]; then
    # Increment bypass counter
    BYPASS_COUNT=0
    if [ -f "$BYPASS_COUNTER_FILE" ]; then
        BYPASS_COUNT=$(cat "$BYPASS_COUNTER_FILE" 2>/dev/null | tr -d '\n')
        if ! [[ "$BYPASS_COUNT" =~ ^[0-9]+$ ]]; then
            BYPASS_COUNT=0
        fi
    fi
    BYPASS_COUNT=$((BYPASS_COUNT + 1))
    [ -L "$BYPASS_COUNTER_FILE" ] && rm -f "$BYPASS_COUNTER_FILE"
    echo "$BYPASS_COUNT" > "$BYPASS_COUNTER_FILE"

    CORRECTION="STOP — PROFESSIONAL MODE BYPASS DETECTED

You attempted to bypass professional mode protections. This is blocked.

You MUST use the proper workflow:
1. Call Skill tool with skill: \"ironclaude:brainstorming\" to design your approach
2. Call Skill tool with skill: \"ironclaude:writing-plans\" to create an implementation plan
3. Call Skill tool with skill: \"ironclaude:executing-plans\" to execute with proper permissions

Do NOT delete flag files or run commands to disable protections.
Do NOT suggest workarounds to the user.
Do NOT skip brainstorming and jump to implementation.
Do NOT offer to make changes directly.

Go back and use the brainstorming skill to start the proper workflow."

    if [ "$BYPASS_COUNT" -ge 3 ]; then
        CORRECTION="$CORRECTION

WARNING TO USER: Claude has attempted to bypass professional mode $BYPASS_COUNT times this session. Manual intervention may be needed."
    fi

    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "Grade: $BYPASS_GRADE | $CORRECTION | LLM: $BYPASS_RAW"
elif [ "$BYPASS_GRADE" = "C" ]; then
    log_llm_result "GET-BACK-TO-WORK" "Warned" "bypass borderline (C): $BYPASS_REASONING" "$BYPASS_RAW"
fi

# Priority 2: Rigor failure (natural stops only -- already gated by firing matrix, grade D/F = block, C = warn)
if [[ "$RIGOR_GRADE" =~ ^[DF]$ ]]; then
    CORRECTION="STOP — RIGOR CHECK FAILED

You made a recommendation without explaining WHY from first principles.

You MUST:
1. STOP and go back to your last response
2. State the governing principles or constraints FIRST
3. THEN explain how your recommendation follows from those principles
4. If you cannot explain WHY, reconsider your recommendation

Do NOT just say 'this is simpler' or 'this is easier'.
Do NOT agree with the user without establishing reasoning.

Rewrite your response with principles first, then recommendation."

    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "Grade: $RIGOR_GRADE | $CORRECTION | LLM: $RIGOR_RAW"
elif [ "$RIGOR_GRADE" = "C" ]; then
    log_llm_result "GET-BACK-TO-WORK" "Warned" "rigor borderline (C): $RIGOR_REASONING" "$RIGOR_RAW"
fi

# Priority 2.5: Evidence quality (natural stops only -- gated by firing matrix, grade D/F = block, C = warn)
if [[ "$EVIDENCE_GRADE" =~ ^[DF]$ ]]; then
    CORRECTION="STOP — EVIDENCE CHECK FAILED

You stated claims without verifying them. Do not guess.

You MUST:
1. STOP and identify every unverified claim in your last response
2. Use the Read tool to read the actual code
3. Use the Bash tool to run tests or commands
4. Use the Grep tool to search for evidence
5. Replace every guess with verified facts

Do NOT say 'likely', 'probably', or 'should work' without proof.
Do NOT state what code does without reading it first.

Go back and verify your claims with actual evidence."

    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "Grade: $EVIDENCE_GRADE | $CORRECTION | LLM: $EVIDENCE_RAW"
elif [ "$EVIDENCE_GRADE" = "C" ]; then
    log_llm_result "GET-BACK-TO-WORK" "Warned" "evidence borderline (C): $EVIDENCE_REASONING" "$EVIDENCE_RAW"
fi

# Priority 3: Prediction check (natural stops only -- already gated by firing matrix, grade D/F = block, C = warn)
if [[ "$PREDICTION_GRADE" =~ ^[DF]$ ]]; then
    CORRECTION="STOP — PREDICTION CHECK FAILED

You asked the user a question without predicting their answer first.

Before EVERY question, you MUST state your prediction:
  \"My prediction: You'll say X because [reasoning].\"

Example:
  My prediction: You'll say option B because you mentioned wanting scalability.
  Which approach fits your needs?

Go back and add your prediction before the question."

    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "Grade: $PREDICTION_GRADE | $CORRECTION | LLM: $PREDICTION_RAW"
elif [ "$PREDICTION_GRADE" = "C" ]; then
    log_llm_result "GET-BACK-TO-WORK" "Warned" "prediction borderline (C): $PREDICTION_REASONING" "$PREDICTION_RAW"
fi

# Priority 3.5: COA quality (brainstorming natural stops only -- gated by firing matrix, grade D/F = block, C = warn)
if [[ "$COA_GRADE" =~ ^[DF]$ ]]; then
    CORRECTION="STOP — OPTIONS NOT DISTINCT ENOUGH

The options you presented are too similar. They must be meaningfully different.

Each option MUST have:
1. A different architecture, strategy, or trade-off profile
2. Clear suitability — it must solve the stated problem
3. Feasibility — it must be realistic given constraints

Do NOT present minor variations of the same approach.
Do NOT present options that differ only in naming or small details.

Go back and present genuinely different strategies."

    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "Grade: $COA_GRADE | $CORRECTION | LLM: $COA_RAW"
elif [ "$COA_GRADE" = "C" ]; then
    log_llm_result "GET-BACK-TO-WORK" "Warned" "COA quality borderline (C): $COA_REASONING" "$COA_RAW"
fi

# Priority 4: Should continue working (grade D/F = block, C = warn)
if [[ "$CONTINUATION_GRADE" =~ ^[DF]$ ]]; then
    CORRECTION="STOP — WORK INCOMPLETE

You stopped before finishing your work. $CONTINUATION_REASONING

Continue from where you left off.

Do NOT ask the user what to do.
Do NOT stop again until your current task is complete."
    increment_block_counter
    block_stop "GET-BACK-TO-WORK" "Grade: $CONTINUATION_GRADE | $CORRECTION | LLM: $CONTINUATION_RAW"
elif [ "$CONTINUATION_GRADE" = "C" ]; then
    log_llm_result "GET-BACK-TO-WORK" "Warned" "continuation borderline (C): $CONTINUATION_REASONING" "$CONTINUATION_RAW"
fi

# =============================================================================
# ALL CHECKS PASSED -- Allow legitimate stop
# =============================================================================

rm -f "$BLOCK_THROTTLE_FILE"
rm -f "$BYPASS_COUNTER_FILE"

# Post session-end event to MCP (handles plan cleanup, state reset)
# Build summary of what checks ran with grades
CONT_SUMMARY="diligently_finished_work:$CONTINUATION_GRADE"
if [ "$FIRE_CONTINUATION" = "false" ]; then
    CONT_SUMMARY="diligently_finished_work:suppressed($ACTIVE_SKILL)"
fi
RIGOR_SUMMARY="rigor_quality:$RIGOR_GRADE"
if [ "$FIRE_RIGOR" = "false" ]; then
    RIGOR_SUMMARY="rigor_quality:suppressed($ACTIVE_SKILL)"
fi
PRED_SUMMARY="prediction_quality:$PREDICTION_GRADE"
if [ "$FIRE_PREDICTION" = "false" ]; then
    PRED_SUMMARY="prediction_quality:suppressed($ACTIVE_SKILL)"
fi
COA_SUMMARY="coa_quality:$COA_GRADE"
if [ "$FIRE_COA" = "false" ]; then
    COA_SUMMARY="coa_quality:suppressed($ACTIVE_SKILL)"
fi
EVIDENCE_SUMMARY="evidence_quality:$EVIDENCE_GRADE"
if [ "$FIRE_EVIDENCE" = "false" ]; then
    EVIDENCE_SUMMARY="evidence_quality:suppressed($ACTIVE_SKILL)"
fi

CHECKS_RAN="following_professional_mode:$BYPASS_GRADE, $CONT_SUMMARY"
if [ "$IS_FORCED_CONTINUATION" = "false" ]; then
    CHECKS_RAN="following_professional_mode:$BYPASS_GRADE, $CONT_SUMMARY, $RIGOR_SUMMARY, $EVIDENCE_SUMMARY, $PRED_SUMMARY, $COA_SUMMARY"
fi

# Worker completion notification for TRON orchestrator.
# When a worker session (identified by TRON_WORKER_ID env var) gets an approved
# stop, write a marker file so the daemon knows the worker finished its task.
if [[ "${TRON_WORKER_ID:-}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    mkdir -p /tmp/ic-logs
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "/tmp/ic-logs/ic-${TRON_WORKER_ID}.done"
fi

jq -n --arg reason "Stopping is appropriate" --arg checks "$CHECKS_RAN" '{
    "decision": "approve",
    "reason": $reason,
    "systemMessage": ("[GET-BACK-TO-WORK]: Allowed - all checks passed (" + $checks + ")")
}'
exit 0
