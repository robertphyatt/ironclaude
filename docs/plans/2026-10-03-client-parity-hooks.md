# Client Parity: Hooks Implementation Plan (v1.1.16 L2b-1)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make IronClaude's hooks behave the same on Claude and Codex. The plan adds a shared last-assistant-text helper, a next-prompt warning queue, a Skill/Task hook audit with the `apply_patch` matcher, the `EnterPlanMode` matcher and an execution drift check. It then redeploys.

**Requirements:** `docs/plans/2026-10-03-client-parity-hooks-requirements.md`

**Design:** `docs/plans/2026-10-03-client-parity-hooks-design.md`

**Evidence:** `docs/plans/2026-10-02-codex-hook-probe-findings.md` (L2a)

**Architecture:**
- Shared helpers live in `worker/hooks/hook-logger.sh`: `ic_last_assistant_text`, `ic_queue_warning` and `ic_drain_warnings`.
- `get-back-to-work-impl.sh` and `subagent-drift-detector.sh` read assistant text through the helper. GBTW queues its borderline (grade C) results.
- `state-activator.sh`, the first UserPromptSubmit hook, drains the queue as `hookSpecificOutput.additionalContext`.
- A new hook, `execution-drift-check.sh`, records a baseline at `claim_task` and checks at `submit_task`. GBTW also calls it at Stop while executing.
- `hooks.json` gains `apply_patch` in the plan-task-context matcher and `EnterPlanMode` in the guard matcher. Two drift entries are appended at the end of their arrays.
- `mcp-state-logger.sh` sets `active_skill` from changed:true MCP transitions on both clients. This is the one R3 port. The others are proposals in the audit note.
- There are no per-client behavior forks. Client detection still only chooses output shapes.

**Tech Stack:** Bash hooks, jq, sqlite3, git; shell tests in `worker/hooks/tests`, run by `make test-hooks`; Commander pytest suite.

**Loop classification:** full loop. The change modifies guard and hook enforcement code.

**Task map (requirement → task):** R6 → Task 1, R1 → Task 2, R2 → Task 3, R3 → Task 4 (audit and `apply_patch`) and Task 7 (`active_skill` port), R4 → Task 5, R5 → Task 6, R7 → every task's tests plus Task 8. The brief's T0–T5 are Tasks 1–6 here, because plan task IDs must be positive integers. Its T6 is Task 8, and Task 7 is the R3 port.

## Execution invariants

- **Shell state does not persist between steps.** Every command spells out literal absolute paths. No step relies on a variable set by an earlier step.
- **Bash cwd is `commander/`.** Staging uses `git -C /Users/roberthyatt/Code/ironclaude`. Tests run by absolute path.
- **zsh `nomatch`.** No command uses an unquoted glob.
- **Foreground `sleep` is blocked.** No step waits.
- **`docs/` is gitignored.** The audit note, findings file and deploy log need `git add -f`.
- **Empty versus failed.** Evidence commands keep stderr. The tests print `PASS`/`FAIL` per assertion and a `Results:` line, and exit non-zero on any failure.
- **Evidence outlives cleanup.** Task 3 writes its findings file before it removes the Codex trust entry it created.
- **Measured baselines (planning time, HEAD `d3dae6a` plus the staged docs).**
  - `diff /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml /Users/roberthyatt/.codex/config.toml` prints the 3-line `1845a1846,1848` hunk quoted in Task 1. The lines are at 1846–1848, followed by `[memories]`. They are **not** the file's last lines.
  - `diff -rq /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837 -x node_modules -x __pycache__ -x .DS_Store` printed nothing. Without the `-x` flags it prints 47 `Only in …/node_modules/.bin` lines.
  - `worker/hooks/` holds 30 `*.sh` files (counted from `ls`).
  - `hooks.json` PreToolUse has 6 entries. The plan-task-context matcher is `Read|Grep|Glob|Edit|Write|NotebookEdit|MultiEdit|Bash|Task`. The guard matcher is `Edit|Write|NotebookEdit|MultiEdit|Bash|Read|Grep|Glob|apply_patch|exec_command`. PostToolUse has 6 entries.
  - `~/.claude/ironclaude-hooks-config.json` has no `verbose_hook_logs` key, so `log_hook` prints nothing for non-block decisions.
  - **Not measured:** the hook-suite and Commander-suite pass counts. Test execution is blocked in the planning stage. Their expected value is "exit 0, 0 failed".
- **Falsifiability.** Each new test was designed against HEAD; the "RED" steps state why it fails there. The matcher tests use the `grep -qE "^($M)$"` form from `docs/plans/2026-07-29-codex-hook-matcher-forms.md`, with negative cases.
- **Stop output stays one JSON object.** The Codex wrapper (`get-back-to-work-claude.sh:61-79`) turns more than one object into a fail-closed block. New Stop-path code never calls `log_warning` or `log_error` and writes queue entries only to a file.
- **Stop-block path.** Every new Stop block calls `increment_block_counter` and then `block_stop`, so the 3-blocks-in-5-minutes escape hatch (`get-back-to-work-impl.sh:372-403`) still applies.
- **Professional-mode gate.** The new hook copies the `plan-execution-reminder.sh:25-29` idiom: when mode is not `on`, `log_hook … "Disabled"` and `exit 0`.
- **Allowlists match exactly.** The drift check compares exact repo-relative strings against the plan's `allowed_files`. It does no pattern matching on filenames.
- **Grading is unchanged.** Task 2 changes only where the assistant text comes from. The prompts, schemas and grade logic stay byte-identical.
- **Known limitation, drift check.** The drift check reads the git state of the session's `cwd` repo. Another session writing into the same checkout after a claim shows up as drift. The Stop path has the throttle escape. A `submit_task` block can only be cleared by removing the files, or by retreat and amending the plan.
- **Deploy scope.** `make deploy-hooks` copies `worker/hooks/*.sh` only. `hooks.json` reaches Codex through `codex plugin add`. It reaches Claude through an explicit copy into Claude's installed plugin path (Task 8 Step 3b), and Claude loads it at its next session start.
- **D9 gate.** The drift-check hook entries ship only if Task 3 shows Codex firing PreToolUse/PostToolUse hooks for state-manager MCP calls. Tasks 6 and 7 read the recorded classification and adapt to it, or stop.

---

## Task 1: Remove the probe's trust entry from `~/.codex/config.toml` (R6)

**Files:**
- Modify: `/Users/roberthyatt/.codex/config.toml:1846-1848`. This path is outside the repo. It is listed in `allowed_files` as an absolute path so the state machine permits the edit. It is not staged.

No tests required: this is a one-entry config removal outside the repo, verified by a diff against the pre-probe snapshot.

**Step 1: Confirm the current difference**

Run:
```bash
diff /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml /Users/roberthyatt/.codex/config.toml
```

Expected (exit 1):
```
1845a1846,1848
> [projects."/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe"]
> trust_level = "trusted"
>
```

**Step 2: Delete the three lines**

Edit `/Users/roberthyatt/.codex/config.toml`. Replace this text:
```
[projects."/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-probe"]
trust_level = "trusted"

[memories]
```
with this text:
```
[memories]
```

**Step 3: Verify against the snapshot**

Run:
```bash
diff /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml /Users/roberthyatt/.codex/config.toml
```

Expected: no output, exit 0.

---

## Task 2: `ic_last_assistant_text` helper and its callers (R1)

**Files:**
- Modify: `worker/hooks/hook-logger.sh`. Insert after `ic_skill_ref` (ends at line 128).
- Modify: `worker/hooks/get-back-to-work-impl.sh:550-596`.
- Modify: `worker/hooks/subagent-drift-detector.sh:11-23` and `:70-71`.
- Create: `worker/hooks/tests/test-last-assistant-text.sh`.

**Scope note, waiting-tool exemption.** `_gbtw_recent_waiting_tool` (`get-back-to-work-impl.sh:104-126`) inspects `tool_use` block names in the transcript. `last_assistant_message` carries text only, so it cannot feed that check, and the helper is left unchanged. The text-based waiting exemption, the "holding/waiting" last-line suppression at `:955-965`, reads `RECENT_CONTEXT`, so it switches to the new source automatically. `_gbtw_should_rearm_check` does the same. Waiting-tool detection on Codex stays a gap.

**Step 1: Write the test (RED)**

Create `worker/hooks/tests/test-last-assistant-text.sh`:
```bash
#!/usr/bin/env bash
# test-last-assistant-text.sh - ic_last_assistant_text (hook-logger.sh) and its
# callers: GBTW grading input and subagent-drift-detector.sh (v1.1.16 L2b-1 R1).
# Codex-shaped Stop input carries last_assistant_message and a rollout path that
# is not Claude JSONL; Claude-shaped input may carry either source.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"

PASSES=0
FAILS=0
EXPECTED_PASSES=7
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

# shellcheck disable=SC1091
source "$HOOKS_DIR/hook-logger.sh"
trap 'rm -rf "$TMP_ROOT"; rm -f /tmp/.claude-block-throttle-ic-lat-e2e /tmp/.claude-bypass-counter-ic-lat-e2e' EXIT

if ! type ic_last_assistant_text >/dev/null 2>&1; then
  bad "ic_last_assistant_text is not defined in hook-logger.sh"
  printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
  exit 1
fi

# Claude transcript fixture: one assistant response (requestId req_1) with a text block.
CLAUDE_T="$TMP_ROOT/claude.jsonl"
jq -cn '{type:"assistant",requestId:"req_1",message:{content:[{type:"text",text:"Claude transcript text"}]}}' > "$CLAUDE_T"

# 1. Codex-shaped: the field is used; the rollout path is missing.
CODEX_IN=$(jq -cn --arg t "$TMP_ROOT/missing-rollout.jsonl" \
  '{session_id:"x",hook_event_name:"Stop",last_assistant_message:"Codex final text",transcript_path:$t,stop_hook_active:false}')
[ "$(ic_last_assistant_text "$CODEX_IN")" = "Codex final text" ] \
  && ok "1 codex last_assistant_message" || bad "1 codex last_assistant_message"

# 2. Claude-shaped with both sources: the field wins.
BOTH_IN=$(jq -cn --arg t "$CLAUDE_T" '{last_assistant_message:"Field text",transcript_path:$t}')
[ "$(ic_last_assistant_text "$BOTH_IN")" = "Field text" ] \
  && ok "2 field preferred over transcript" || bad "2 field preferred over transcript"

# 3. Claude-shaped without the field: transcript fallback.
T_ONLY=$(jq -cn --arg t "$CLAUDE_T" '{transcript_path:$t}')
[ "$(ic_last_assistant_text "$T_ONLY")" = "Claude transcript text" ] \
  && ok "3 transcript fallback" || bad "3 transcript fallback"

# 4. Neither source: empty.
NONE_IN=$(jq -cn --arg t "$TMP_ROOT/none.jsonl" '{transcript_path:$t}')
[ -z "$(ic_last_assistant_text "$NONE_IN")" ] \
  && ok "4 both absent -> empty" || bad "4 both absent -> empty"

# 5. GBTW end-to-end with Codex-shaped input. An unreachable Ollama endpoint makes
#    every grading call fail fast, which run_check grades A, so a graded turn ends
#    "Allowed - all checks passed". At HEAD the missing transcript short-circuits
#    to "Passed - no transcript" and grading never runs.
E2E_HOME="$TMP_ROOT/home"
mkdir -p "$E2E_HOME/.claude"
printf '{"session_id":"ic-lat-e2e","source":"startup"}' | HOME="$E2E_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$E2E_HOME/.claude/ironclaude.db" \
  "UPDATE sessions SET professional_mode='on', workflow_stage='idle' WHERE terminal_session='ic-lat-e2e';"
printf '%s' '{"validation_backend":"ollama","ollama":{"url":"http://127.0.0.1:9","model":"none"},"connect_timeout_seconds":1,"hook_validation_budget_seconds":2}' \
  > "$E2E_HOME/.claude/ironclaude-hooks-config.json"
E2E_IN=$(printf '%s' "$CODEX_IN" | jq -c '.session_id = "ic-lat-e2e"')
E2E_OUT=$(printf '%s' "$E2E_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE -u TRON_WORKER_ID -u IC_OLLAMA_CONFIG_PATH \
  HOME="$E2E_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
E2E_MSG=$(printf '%s' "$E2E_OUT" | jq -r '.systemMessage // empty' 2>/dev/null)
case "$E2E_MSG" in
  "[GET-BACK-TO-WORK]: Allowed - all checks passed"*) ok "5 GBTW grades Codex-shaped input" ;;
  *) bad "5 GBTW grades Codex-shaped input (got: $E2E_OUT)" ;;
esac

# 6. subagent-drift-detector, Codex-shaped: an anti-pattern in last_assistant_message
#    blocks with no transcript. At HEAD it reads only transcript_path and does not block.
printf '{"session_id":"ic-lat-sad","source":"startup"}' | HOME="$E2E_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$E2E_HOME/.claude/ironclaude.db" \
  "UPDATE sessions SET professional_mode='on', workflow_stage='executing' WHERE terminal_session='ic-lat-sad';"
SAD_IN=$(jq -cn --arg t "$TMP_ROOT/missing-rollout.jsonl" \
  '{session_id:"ic-lat-sad",hook_event_name:"SubagentStop",last_assistant_message:"Shall we checkpoint here?",transcript_path:$t}')
SAD_OUT=$(printf '%s' "$SAD_IN" | HOME="$E2E_HOME" bash "$HOOKS_DIR/subagent-drift-detector.sh" 2>/dev/null)
[ "$(printf '%s' "$SAD_OUT" | jq -r '.decision // empty' 2>/dev/null)" = "block" ] \
  && ok "6 drift detector blocks on Codex-shaped anti-pattern" \
  || bad "6 drift detector blocks on Codex-shaped anti-pattern (got: $SAD_OUT)"

# 7. Negative control: clean text does not block.
SAD_OK=$(printf '%s' "$SAD_IN" | jq -c '.last_assistant_message = "Grade: A. Ready to proceed."' \
  | HOME="$E2E_HOME" bash "$HOOKS_DIR/subagent-drift-detector.sh" 2>/dev/null)
[ -z "$(printf '%s' "$SAD_OK" | jq -r '.decision // empty' 2>/dev/null)" ] \
  && ok "7 clean subagent text does not block" || bad "7 clean subagent text does not block (got: $SAD_OK)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh
```

Expected: exit 1, with `FAIL  ic_last_assistant_text is not defined in hook-logger.sh`. The function does not exist at HEAD.

**Step 3: Add the helper to `hook-logger.sh`**

Insert this block right after the closing `}` of `ic_skill_ref` (line 128) and before the `# json_escape VALUE` comment:
```bash

# _ic_transcript_last_assistant_text TRANSCRIPT_PATH
# Claude transcript fallback (moved from get-back-to-work-impl.sh): the text blocks
# of the last assistant requestId; if that yields nothing, recent assistant text
# (capped at 200 lines). Prints nothing when the file is unset, missing,
# unreadable or not Claude JSONL.
_ic_transcript_last_assistant_text() {
  local transcript="${1:-}" req_id text=""
  if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
    return 0
  fi
  req_id=$(tail -n 500 "$transcript" 2>/dev/null | \
    jq -r 'select(.type == "assistant") | .requestId // empty' 2>/dev/null | \
    tail -1 || true)
  if [ -n "$req_id" ]; then
    text=$(tail -n 500 "$transcript" 2>/dev/null | grep -F "\"$req_id\"" | \
      jq -r '.message.content[]? | select(.type == "text") | .text // empty' 2>/dev/null || true)
  fi
  if [ -z "$text" ]; then
    text=$(tail -n 500 "$transcript" 2>/dev/null | \
      jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text // empty' \
      2>/dev/null | tail -n 200 || true)
  fi
  printf '%s' "$text"
}

# ic_last_assistant_text HOOK_INPUT_JSON
# The last assistant text for a Stop/SubagentStop event. Prefers
# .last_assistant_message (sent by Codex, observed in L2a; documented for Claude
# Code), then falls back to the Claude transcript at .transcript_path. Prints
# nothing only when both sources are absent.
ic_last_assistant_text() {
  local input="${1:-}" text transcript
  text=$(printf '%s' "$input" | jq -r 'if type == "object" and (.last_assistant_message | type) == "string" then .last_assistant_message else empty end' 2>/dev/null || true)
  if [ -n "$text" ]; then
    printf '%s' "$text"
    return 0
  fi
  transcript=$(printf '%s' "$input" | jq -r 'if type == "object" then (.transcript_path // "") else "" end' 2>/dev/null || true)
  transcript=$(normalize_path "$transcript")
  _ic_transcript_last_assistant_text "$transcript"
}
```

**Step 4: Switch GBTW grading to the helper**

In `worker/hooks/get-back-to-work-impl.sh`, replace this text (lines 550-596):
```bash
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
```
with this text:
```bash
# =============================================================================
# LAST ASSISTANT TEXT
# =============================================================================
# ic_last_assistant_text (hook-logger.sh) prefers the Stop input's
# last_assistant_message (sent by Codex and Claude Code) and falls back to the
# Claude transcript extraction this block used to inline. Only the source of the
# text changed; the prompts and grading below are unchanged.
RECENT_CONTEXT=$(ic_last_assistant_text "$EVENT")

# If nothing was extracted, approve — sending raw JSONL to the grader
# always produces a false F grade (grader sees requestId/type/timestamp/usage metadata, not prose).
if [ -z "$RECENT_CONTEXT" ]; then
  echo '{"decision": "approve", "reason": "Could not extract Claude response text from transcript", "systemMessage": "[GET-BACK-TO-WORK]: Passed - extraction failed, grading skipped"}'
  exit 0
fi
```
`TRANSCRIPT_PATH` (line 350) stays: the in-flight and waiting-tool helpers still use it.

**Step 5: Switch `subagent-drift-detector.sh` to the helper**

Replace this text (lines 11-23):
```bash
# _sad_last_assistant_text TRANSCRIPT_PATH
# Echoes the final assistant "text" content block from the SubagentStop transcript,
# or empty if the path is unset/unreadable. Consumed by the anti-pattern block
# below via _ic_is_antipattern_proposal (from hook-logger.sh).
_sad_last_assistant_text() {
    local transcript="${1:-}"
    if [ -z "$transcript" ] || [ ! -r "$transcript" ]; then
        return 0
    fi
    tail -50 "$transcript" \
        | jq -s -r '[.[] | select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text // empty] | last // empty' 2>/dev/null \
        || true
}
```
with this text:
```bash
# _sad_last_assistant_text TRANSCRIPT_PATH
# Claude-transcript-only extraction, kept for test-sad-antipattern.sh. Delegates to
# the shared fallback in hook-logger.sh. The hook body uses ic_last_assistant_text,
# which prefers the SubagentStop input's last_assistant_message.
_sad_last_assistant_text() {
    _ic_transcript_last_assistant_text "${1:-}"
}
```

Then replace this text (lines 70-71):
```bash
      TRANSCRIPT=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)
      LAST_TEXT=$(_sad_last_assistant_text "$TRANSCRIPT")
```
with this text:
```bash
      LAST_TEXT=$(ic_last_assistant_text "$INPUT")
```

**Step 6: Run the new test and the affected existing tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh
```
Expected: `Results: 7 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-sad-antipattern.sh
```
Expected: `Results: 9 pass, 0 fail`, exit 0. Its six fixtures have no `requestId`, so they take the shared secondary extraction. A single-message transcript yields the same text there.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
```
Expected: last line `ALL WRAPPER TESTS PASSED`, exit 0.

**Step 7: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hook-logger.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/subagent-drift-detector.sh worker/hooks/tests/test-last-assistant-text.sh
```
Expected: no output, exit 0.

---

## Task 3: Warning queue, drain and the Codex visibility check (R2)

Depends on Tasks 1 and 2.

**Files:**
- Modify: `worker/hooks/hook-logger.sh`. Insert after `ic_last_assistant_text` (added in Task 2).
- Modify: `worker/hooks/get-back-to-work-impl.sh`:
  - add `_gbtw_warn` before the `GBTW_TEST_MODE` shim (line 241 at HEAD);
  - change the six grade-C branches;
  - add queue lines before the escape-hatch approve (line 398 at HEAD) and the no-`plan_json` approve (line 438 at HEAD).
- Modify: `worker/hooks/state-activator.sh:34`.
- Create: `worker/hooks/tests/test-warning-queue.sh`.
- Create: `docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md`.
- Modify (outside the repo, not staged): `/Users/roberthyatt/.codex/config.toml`, to remove the trust entry that `codex exec` appends.
- Create (scratch, not staged), under `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/` (SCRATCH below; every command spells out the full path):
  - `codex-ups-check/.codex/hooks.json`
  - `codex-ups-check/.codex/ups-marker.sh`
  - `codex-ups-check/.codex/ups-input.json`, written by the UserPromptSubmit hook.
  - `codex-ups-check/.codex/capture.sh`
  - `codex-ups-check/.codex/capture/PreToolUse.jsonl`, `codex-ups-check/.codex/capture/PostToolUse.jsonl` and `codex-ups-check/.codex/capture/Stop.jsonl`. `capture.sh` writes each one, taking the event name as `$1` and appending one JSON line per hook call.
  - the run logs `codex-ups-check-run1.jsonl` through `codex-ups-check-run4.jsonl`. `run2` and `run4` exist only if a rerun happens.
  - the last-message files `codex-ups-check-last1.txt` through `codex-ups-check-last4.txt`, likewise.

**What counts as a warning.** The wrapper drops every approve systemMessage on Codex (`get-back-to-work-claude.sh:69-70` maps approve to `{}`).
- **Status notices.** At HEAD, `get-back-to-work-impl.sh` emits a systemMessage at 12 places: 11 approve outputs plus the `:26` logger-load notice. Ten of them are per-turn status and are not queued: `:26` logger-load, `:255`, `:328`, `:482`, `:522`, `:534`, `:555`, `:594`, `:605`, and `:1267` "Allowed - all checks passed".
- **Warnings at approve sites.** The other two are warning-class and are queued at the source: the escape hatch (`:398`) and the plan-active-without-`plan_json` anomaly (`:438`).
- **Approve outputs with no systemMessage.** The five at `:293`, `:301`, `:319`, `:338` and `:921` carry nothing to drop.
- **Grade-C warnings.** GBTW's borderline results (grade C) go through `log_llm_result … "Warned"` at six sites: bypass, rigor, evidence, prediction, COA quality and continuation. `log_hook` prints them only when `verbose_hook_logs` is true, and that key is unset, so they are silent on **both** clients today. They are queued at the source too.

All queueing happens in the impl, which both clients run. The wrapper file stays unedited. Task 2 rewrites lines 550-596, so the edits below anchor on text, not line numbers.

**Queue location:** `$HOME/.claude/ironclaude-warnings-<session id, [A-Za-z0-9_-] only>.queue`, one warning per line. The drain renames the file to `.draining.<pid>`, reads it and deletes it, so a write that races the drain lands in a fresh file. Queue I/O errors are ignored: warnings fail open.

**Step 1: Codex visibility check — set up the scratch repo**

Run:
```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex
```
Expected: no output, exit 0.

Run:
```bash
git init -q /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check
```
Expected: no output, exit 0.

Write `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/ups-marker.sh` with exactly:
```bash
#!/bin/bash
# Positive control: record that the hook ran, then inject a marker as context.
cat > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/ups-input.json
printf '%s' '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"IC-UPS-CHECK: the secret word is PERIWINKLE-7731."}}'
```

Write `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture.sh` with exactly:
```bash
#!/bin/bash
# Capture hook: append this hook's stdin as one JSON line to capture/<EVENT>.jsonl.
# $1 is the event name (PreToolUse, PostToolUse or Stop). Emits nothing on stdout.
CAPTURE_DIR=/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture
mkdir -p "$CAPTURE_DIR"
jq -c . >> "$CAPTURE_DIR/$1.jsonl"
exit 0
```

Write `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/hooks.json` with exactly:
```json
{"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/ups-marker.sh"}]}],
  "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture.sh PreToolUse"}]}],
  "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture.sh PostToolUse"}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "bash /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture.sh Stop"}]}]
}}
```

**Step 2: Codex visibility check — run one headless turn**

Run:
```bash
codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check -s workspace-write --json --dangerously-bypass-hook-trust --output-last-message /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-last1.txt "If your context contains a secret word, reply with exactly that word and nothing else. Otherwise reply NONE. Do not run any commands." > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-run1.jsonl 2>&1
```
Expected: exit 0.

**Step 3: Read the result and the positive control**

Run:
```bash
ls -la /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex
```
Run:
```bash
cat /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-last1.txt
```
Classify:
- `ups-input.json` is listed and the last message is `PERIWINKLE-7731`: **visible**.
- `ups-input.json` is listed and the last message is `NONE` or anything else: **invisible**.
- `ups-input.json` is absent: the hook did not run, and the run proves nothing. Repeat Step 2 once, with `codex-ups-check-last2.txt` and `codex-ups-check-run2.jsonl` in place of `…last1.txt` and `…run1.jsonl`, then repeat this step for those files. If the file is still absent, record **UNCERTAIN (hook did not run)**.

**Step 4: MCP hook capture (D9) — run a second headless turn**

Run:
```bash
codex exec -C /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check -s workspace-write --json --dangerously-bypass-hook-trust --output-last-message /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-last3.txt "Call the state-manager MCP tool get_professional_mode exactly once, then reply DONE. Run no shell commands." > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-run3.jsonl 2>&1
```
Expected: exit 0.

**Step 5: Classify the MCP capture**

Run each command. Missing-file errors are evidence; keep them.
```bash
grep -c get_professional_mode /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-run3.jsonl
```
This is the call-happened control. Expected: an integer of 1 or more if the call happened, or `0` (exit 1) if it did not.

```bash
jq -r .tool_name /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture/PreToolUse.jsonl
```
```bash
jq -c keys /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture/PostToolUse.jsonl
```
```bash
jq -c keys /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check/.codex/capture/Stop.jsonl
```
These three print the captured shapes. Values were not measured during planning.

Classify, letting O be the PreToolUse `tool_name` captured for the call:
- **(a) matcher shape holds.** O matches `^mcp__(plugin_ironclaude_)?state[-_]manager__get_professional_mode$`. The repo's matchers stand. Record O, plus the PostToolUse response key: `tool_response`, `tool_output` or neither.
- **(b) other shape, same suffix.** O ends with `get_professional_mode` but fails that regex. Record O, and its prefix P (O with the trailing `get_professional_mode` removed). Tasks 6 and 7 adapt to P, as their conditional steps state.
- **(c) blocker.** The control is ≥ 1 but no PreToolUse or PostToolUse capture names the call, or O does not end with `get_professional_mode`. Record **BLOCKER** in the findings file, noting whether `Stop.jsonl` or `ups-input.json` exists to show that hooks ran. Task 6 must not ship as-is. Before Task 6, stop and report the blocker to the operator with the cross-client alternative: record the baseline at `claim_task`, and refuse at `submit_task`, inside the state-manager server.
- **(d) no call.** The control is `0`: the run proves nothing. Repeat Step 4 once, with `codex-ups-check-last4.txt` and `codex-ups-check-run4.jsonl` in place of `…last3.txt` and `…run3.jsonl`. Then repeat this step against `run4.jsonl`. If the control is still `0`, treat it as (c).

**Step 6: Record the session rows `codex exec` inserted**

Run:
```bash
grep -o -E '"thread_id":"[^"]+"' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-run1.jsonl /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check-run3.jsonl
```
If a rerun produced `run2.jsonl` or `run4.jsonl`, run the same `grep` on each of them as well.

Expected: one thread id per run log. The values were not measured. If nothing prints, record "thread id not found in exec output".

For each id, run this, substituting the literal id for `<THREAD_ID>`:
```bash
sqlite3 /Users/roberthyatt/.claude/ironclaude.db "SELECT terminal_session, professional_mode, workflow_stage FROM sessions WHERE terminal_session='<THREAD_ID>';"
```
Expected: one row, `<THREAD_ID>|undecided|idle`. That is the session-init default, as seen in the L2a findings. Record the rows. Do not delete them.

**Step 7: Write the findings file before any cleanup**

Create `docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md` with:
- the date and the exact Step 2 and Step 4 commands, plus any reruns;
- the `ls` output, the last-message contents and the Step 5 outputs, quoted;
- the UPS classification (visible / invisible / UNCERTAIN) with its evidence, and its consequence:
  - **visible:** queued warnings reach Codex at the next prompt;
  - **invisible or UNCERTAIN:** per requirements decision D2, Codex warnings stay dropped. The shared drain is harmless there, so no client-specific code is added. The UI was not tested; `exec` is headless.
- the MCP classification on a line of its own: `MCP classification: (a) O=<O> envelope=<key>`, `MCP classification: (b) O=<O> P=<P> envelope=<key>`, or `MCP classification: (c) BLOCKER — <evidence>`;
- the Step 6 session rows.

**Step 8: Remove the trust entry `codex exec` appended**

Run:
```bash
diff /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml /Users/roberthyatt/.codex/config.toml
```
Expected: exit 1, with one added hunk of three lines: `[projects."/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-ups-check"]`, `trust_level = "trusted"` and a blank line. All runs share one working directory, so there is one entry. Codex chooses where to insert it; the line numbers were not measured.

If the diff is empty instead, record that in the findings file and skip the edit. If it shows more than one entry or any other change, record the extra content in the findings file and leave it untouched.

Edit `/Users/roberthyatt/.codex/config.toml`: delete exactly the `codex-ups-check` entry's three lines, matching them with the line that follows them in the file.

Run:
```bash
diff /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/config.before.toml /Users/roberthyatt/.codex/config.toml
```
Expected: no output, exit 0.

**Step 9: Write the queue test (RED)**

Create `worker/hooks/tests/test-warning-queue.sh`:
```bash
#!/usr/bin/env bash
# test-warning-queue.sh - per-session warning queue (ic_queue_warning) and its
# UserPromptSubmit drain (ic_drain_warnings via state-activator.sh), plus GBTW's
# borderline-grade queueing (_gbtw_warn). v1.1.16 L2b-1 R2.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"; rm -f /tmp/.claude-block-throttle-ic-wq-session /tmp/.claude-bypass-counter-ic-wq-session' EXIT

SESSION="ic-wq-session"
QUEUE="$TMP_HOME/.claude/ironclaude-warnings-${SESSION}.queue"
PASSES=0
FAILS=0
EXPECTED_PASSES=7
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }

queue() { HOME="$TMP_HOME" bash -c 'source "$1/hook-logger.sh"; ic_queue_warning "$2" "$3"' _ "$HOOKS_DIR" "$1" "$2"; }
drain() { HOME="$TMP_HOME" bash -c 'source "$1/hook-logger.sh"; ic_drain_warnings "$2"' _ "$HOOKS_DIR" "$1"; }

# 1. Queueing writes the per-session file.
queue "$SESSION" "W-ONE first"
queue "$SESSION" "W-TWO second"
[ -s "$QUEUE" ] && ok "1 queue file written" || bad "1 queue file written"

# 2. Draining emits one UserPromptSubmit additionalContext object with both, in order.
DRAIN_OUT=$(drain "$SESSION")
CTX=$(printf '%s' "$DRAIN_OUT" | jq -r 'select(.hookSpecificOutput.hookEventName == "UserPromptSubmit") | .hookSpecificOutput.additionalContext' 2>/dev/null)
case "$CTX" in
  *"W-ONE first"*"W-TWO second"*) ok "2 drain emits both warnings in order" ;;
  *) bad "2 drain emits both warnings in order (got: $DRAIN_OUT)" ;;
esac

# 3. Draining empties the queue: the file is gone and a second drain prints nothing.
SECOND=$(drain "$SESSION")
if [ ! -e "$QUEUE" ] && [ -z "$SECOND" ]; then ok "3 drain empties the queue"; else bad "3 drain empties the queue (second: $SECOND)"; fi

# 4. state-activator.sh (UserPromptSubmit) drains, and its stdout is exactly one JSON object.
queue "$SESSION" "W-THREE via hook"
UPS_IN=$(jq -cn --arg s "$SESSION" --arg c "$TMP_ROOT" '{session_id:$s,hook_event_name:"UserPromptSubmit",prompt:"hello",cwd:$c}')
UPS_OUT=$(printf '%s' "$UPS_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/state-activator.sh" 2>/dev/null)
if printf '%s' "$UPS_OUT" | jq -cse 'length == 1 and (.[0].hookSpecificOutput.additionalContext | contains("W-THREE via hook"))' >/dev/null 2>&1; then
  ok "4 state-activator drains as its only stdout object"
else
  bad "4 state-activator drains as its only stdout object (got: $UPS_OUT)"
fi

# 5. GBTW's borderline grade is queued (both clients) and prints nothing to stdout.
WARN_OUT=$(HOME="$TMP_HOME" GBTW_TEST_MODE=1 bash -c 'source "$1/get-back-to-work-impl.sh"; SESSION_TAG="$2"; _gbtw_warn "rigor" "R-REASON" "raw"' _ "$HOOKS_DIR" "$SESSION")
if [ -z "$WARN_OUT" ] && grep -qF "[GET-BACK-TO-WORK] rigor borderline (C): R-REASON" "$QUEUE" 2>/dev/null; then
  ok "5 _gbtw_warn queues and keeps stdout empty"
else
  bad "5 _gbtw_warn queues and keeps stdout empty (stdout: $WARN_OUT)"
fi

# 6. No session id ("none") queues nothing.
queue "none" "W-NONE"
[ ! -e "$TMP_HOME/.claude/ironclaude-warnings-none.queue" ] && ok "6 session 'none' is not queued" || bad "6 session 'none' is not queued"

# 7. Escape hatch on Codex: the wrapper still answers {}, and the warning is queued.
#    (DB seed idiom from test-stop-wrapper.sh:154-156; it runs last so cases 1-6 see no DB.)
sqlite3 "$TMP_HOME/.claude/ironclaude.db" 'CREATE TABLE sessions (terminal_session TEXT PRIMARY KEY, professional_mode TEXT); INSERT INTO sessions VALUES ("ic-wq-session","on");'
printf '3:%s\n' "$(date +%s)" > /tmp/.claude-block-throttle-ic-wq-session
HATCH_OUT=$(printf '%s' '{"session_id":"ic-wq-session","hook_event_name":"Stop","stop_hook_active":true}' \
  | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" GBTW_FORCE_CODEX=1 bash "$HOOKS_DIR/get-back-to-work-claude.sh")
if [ "$HATCH_OUT" = "{}" ] && grep -qF "max blocks reached" "$QUEUE" 2>/dev/null; then
  ok "7 escape hatch queued on Codex (wrapper output {})"
else
  bad "7 escape hatch queued on Codex (wrapper output: $HATCH_OUT)"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 10: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-warning-queue.sh
```
Expected: exit 1, with `FAIL  1 queue file written` and `FAIL  7 escape hatch queued on Codex (wrapper output: {})`. At HEAD, `ic_queue_warning` is undefined and the escape hatch queues nothing.

**Step 11: Add the queue helpers to `hook-logger.sh`**

Insert right after the closing `}` of `ic_last_assistant_text`:
```bash

# ic_queue_warning SESSION_ID MESSAGE
# Appends one non-blocking warning to the session's queue file. A UserPromptSubmit
# hook drains it as next-turn context on both clients (ic_drain_warnings). Writes
# only to the file, never to stdout, so a Stop hook's output stays one JSON object.
# I/O errors are ignored: warnings fail open.
ic_queue_warning() {
  local safe msg
  safe=$(printf '%s' "${1:-}" | tr -cd 'a-zA-Z0-9_-')
  if [ -z "$safe" ] || [ "$safe" = "none" ]; then
    return 0
  fi
  msg="${2:-}"
  msg="${msg//$'\n'/ }"
  msg="${msg//$'\r'/ }"
  [ -n "$msg" ] || return 0
  mkdir -p "$HOME/.claude" 2>/dev/null || return 0
  printf '%s\n' "$msg" >> "$HOME/.claude/ironclaude-warnings-${safe}.queue" 2>/dev/null || true
  return 0
}

# ic_drain_warnings SESSION_ID
# Prints the queued warnings as one UserPromptSubmit hookSpecificOutput.additionalContext
# JSON object and empties the queue (rename, read, delete). Prints nothing when the
# queue is empty or unreadable.
ic_drain_warnings() {
  local safe file tmp text
  safe=$(printf '%s' "${1:-}" | tr -cd 'a-zA-Z0-9_-')
  if [ -z "$safe" ] || [ "$safe" = "none" ]; then
    return 0
  fi
  file="$HOME/.claude/ironclaude-warnings-${safe}.queue"
  [ -s "$file" ] || return 0
  tmp="${file}.draining.$$"
  mv "$file" "$tmp" 2>/dev/null || return 0
  text=$(cat "$tmp" 2>/dev/null || true)
  rm -f "$tmp" 2>/dev/null || true
  [ -n "$text" ] || return 0
  jq -cn --arg ctx "IronClaude warnings from your previous turn (address them):
$text" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' 2>/dev/null || true
  return 0
}
```

**Step 12: Queue GBTW's warnings at their source**

In `worker/hooks/get-back-to-work-impl.sh`, replace this text:
```bash
# Test-mode shim: sourcing with GBTW_TEST_MODE=1 exposes helpers without
# running the hook body.
```
with this text:
```bash
# _gbtw_warn CHECK_LABEL REASONING RAW
# A borderline (grade C) result: log it as before (log_hook, verbose-gated) and
# queue it for the next prompt's context on both clients (ic_queue_warning).
# Prints nothing unless verbose logging is on, so the Stop output stays one JSON object.
_gbtw_warn() {
    local label="$1" reasoning="$2" raw="${3:-}"
    log_llm_result "GET-BACK-TO-WORK" "Warned" "${label} borderline (C): ${reasoning}" "$raw"
    ic_queue_warning "$SESSION_TAG" "[GET-BACK-TO-WORK] ${label} borderline (C): ${reasoning}"
}

# Test-mode shim: sourcing with GBTW_TEST_MODE=1 exposes helpers without
# running the hook body.
```

Then make these six one-line replacements. Each is unique in the file, and each log message stays byte-identical.

| Replace | With |
|---|---|
| `    log_llm_result "GET-BACK-TO-WORK" "Warned" "bypass borderline (C): $BYPASS_REASONING" "$BYPASS_RAW"` | `    _gbtw_warn "bypass" "$BYPASS_REASONING" "$BYPASS_RAW"` |
| `    log_llm_result "GET-BACK-TO-WORK" "Warned" "rigor borderline (C): $RIGOR_REASONING" "$RIGOR_RAW"` | `    _gbtw_warn "rigor" "$RIGOR_REASONING" "$RIGOR_RAW"` |
| `    log_llm_result "GET-BACK-TO-WORK" "Warned" "evidence borderline (C): $EVIDENCE_REASONING" "$EVIDENCE_RAW"` | `    _gbtw_warn "evidence" "$EVIDENCE_REASONING" "$EVIDENCE_RAW"` |
| `    log_llm_result "GET-BACK-TO-WORK" "Warned" "prediction borderline (C): $PREDICTION_REASONING" "$PREDICTION_RAW"` | `    _gbtw_warn "prediction" "$PREDICTION_REASONING" "$PREDICTION_RAW"` |
| `    log_llm_result "GET-BACK-TO-WORK" "Warned" "COA quality borderline (C): $COA_REASONING" "$COA_RAW"` | `    _gbtw_warn "COA quality" "$COA_REASONING" "$COA_RAW"` |
| `    log_llm_result "GET-BACK-TO-WORK" "Warned" "continuation borderline (C): $CONTINUATION_REASONING" "$CONTINUATION_RAW"` | `    _gbtw_warn "continuation" "$CONTINUATION_REASONING" "$CONTINUATION_RAW"` |

Queue the escape hatch (line 398 at HEAD). Replace this text:
```bash
        echo '{"decision": "approve", "reason": "Maximum block cycles reached (3 blocks in 5 min)", "systemMessage": "[GET-BACK-TO-WORK]: Stopped - max blocks reached, likely false positives"}'
```
with this text:
```bash
        ic_queue_warning "$SESSION_TAG" "[GET-BACK-TO-WORK] Stopped - max blocks reached (3 in 5 min), likely false positives — the Stop gate was bypassed this turn"
        echo '{"decision": "approve", "reason": "Maximum block cycles reached (3 blocks in 5 min)", "systemMessage": "[GET-BACK-TO-WORK]: Stopped - max blocks reached, likely false positives"}'
```

Queue the plan-active-without-`plan_json` anomaly (line 438 at HEAD). Replace this text:
```bash
        echo '{"decision": "approve", "reason": "No plan loaded", "systemMessage": "[GET-BACK-TO-WORK]: Passed - no plan_json"}'
```
with this text:
```bash
        ic_queue_warning "$SESSION_TAG" "[GET-BACK-TO-WORK] workflow_stage=${WORKFLOW_STAGE} but no plan_json is loaded — the Stop gate skipped all plan checks this turn"
        echo '{"decision": "approve", "reason": "No plan loaded", "systemMessage": "[GET-BACK-TO-WORK]: Passed - no plan_json"}'
```

**Step 13: Drain in `state-activator.sh`**

Replace this text (lines 33-34):
```bash
# Surface any MCP tool errors from the sideband log
surface_mcp_errors
```
with this text:
```bash
# Surface any MCP tool errors from the sideband log
surface_mcp_errors

# Deliver warnings queued by earlier hooks (GBTW) as next-turn context on both
# clients. Prints one JSON object, or nothing when the queue is empty.
ic_drain_warnings "$SESSION_TAG"
```

**Step 14: Run the tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-warning-queue.sh
```
Expected: `Results: 7 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-last-assistant-text.sh
```
Expected: `Results: 7 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
```
Expected: last line `ALL WRAPPER TESTS PASSED`, exit 0.

Run:
```bash
grep -c -F 'log_llm_result "GET-BACK-TO-WORK" "Warned"' /Users/roberthyatt/Code/ironclaude/worker/hooks/get-back-to-work-impl.sh
```
Expected: `1`, the call inside `_gbtw_warn`. At HEAD this prints `6`.

**Step 15: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hook-logger.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/state-activator.sh worker/hooks/tests/test-warning-queue.sh
```
Expected: no output, exit 0.

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md
```
Expected: no output, exit 0.

---

## Task 4: Skill/Task hook audit note and the `apply_patch` matcher (R3)

**Files:**
- Create: `docs/plans/2026-10-03-skill-task-hook-audit.md`.
- Modify: `worker/hooks/hooks.json`, the plan-task-context matcher (PreToolUse entry 3 of 6).
- Create: `worker/hooks/tests/test-plan-task-context-apply-patch.sh`.

The audit itself needs no tests: it is a document. Its evidence lines are re-checked in Step 1. The matcher change gets a RED→GREEN test.

**Step 1: Re-check the audit's evidence lines**

Run each command. Expected output is shown after each one; all of it was measured at planning time.

```bash
grep -n -e "memory_search_required" -e "active_skill" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/write-tools.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/state-machine.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/read-tools.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tool-dispatch.ts
```
Expected: no output, exit 1. No MCP tool reads or writes either column.

```bash
grep -n -e "active_skill" -e "memory_search_required" /Users/roberthyatt/Code/ironclaude/worker/hooks/get-back-to-work-impl.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/memory-search-tracker.sh
```
Expected: lines `get-back-to-work-impl.sh:881`, `get-back-to-work-impl.sh:1072` (Task 2 and Task 3 shift these; record the measured numbers in the note), and `memory-search-tracker.sh:4`, `:26`, `:30`, `:32`.

```bash
grep -n -e "circuit_breaker" /Users/roberthyatt/Code/ironclaude/worker/hooks/subagent-circuit-breaker.sh /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/write-tools.ts /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/state-machine.ts
```
Expected:
- `subagent-circuit-breaker.sh:32` sets it to 1, and `:45` reads it;
- `write-tools.ts:435`, `:1280` and `:1297` are the reset_session description, clear and audit context;
- `state-machine.ts:562` and `:573` clear it.

```bash
grep -n -e "case 'mark_brainstorming'" -e "case 'mark_debugging'" -e "case 'set_testing_theatre_checked'" -e "case 'record_review_verdict'" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/tools/write-tools.ts
```
Expected: `1037`, `1167`, `1180`, `1388`.

```bash
grep -n -e "export function inheritReviewOnDesignReentry" /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/src/state-machine.ts
```
Expected: `146`.

If any result differs, write the measured value into the note, not the planned one.

**Step 2: Write the audit note**

Create `docs/plans/2026-10-03-skill-task-hook-audit.md` with this content. Replace the bracketed line numbers with the values measured in Step 1.

```markdown
# Skill/Task Hook Audit (v1.1.16 L2b-1, R3)

> Created: 2026-10-03. Requirements: `docs/plans/2026-10-03-client-parity-hooks-requirements.md` (D3).
> Outcomes are those allowed by D3: **real gap** (port the rule to a trigger Codex has), or
> **covered by MCP** (propose removal on both clients). A rule that is needed but has no
> verified Codex trigger gets a cross-client proposal. Nothing is removed in this loop; the
> operator decides each proposal.

## Facts that apply to every hook

- These four hooks are keyed on Claude tool names (`Skill`, `Task`, `TaskOutput`; `hooks.json`
  PreToolUse/PostToolUse). Codex has no `Skill` or `Task` tool, so none of them fires on Codex.
- Before this loop, the Codex hook `tool_name` for MCP tools had never been captured
  (`docs/plans/2026-07-29-codex-hook-matcher-forms.md:401`). The client-parity plan's Task 3
  captures it and records the result in
  `docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md` (`MCP classification:` line).
  Matchers on state-manager tool names follow that recorded shape.
- Codex `SubagentStart` exists only as an output wire found in the Codex binary
  (`docs/plans/2026-07-21-codex-stop-hook-diagnosis-findings.md:44`). It is not a confirmed
  registrable input event.

## 1. skill-state-bridge.sh (PreToolUse `Skill`)

| What it sets | MCP coverage | Outcome |
|---|---|---|
| `inherit_review` from the FROM-stage (`skill-state-bridge.sh:78-86`) | `inheritReviewOnDesignReentry` (`state-machine.ts:[146]`), used by `mark_brainstorming`/`mark_debugging` (`write-tools.ts:[1167]`, `:[1180]`) | covered by MCP |
| `testing_theatre_checked=1` (`:65`) | `set_testing_theatre_checked` (`write-tools.ts:[1388]`) | covered by MCP |
| `workflow_stage` brainstorming/debugging (`:41`, `:45`) | `mark_brainstorming`, `mark_debugging` | covered by MCP |
| `active_skill` (`:41-68`) | none: no MCP read or write (Step 1 grep) | **real gap** |
| `memory_search_required=1` for brainstorming and writing-plans (`:41`, `:49`) | none | **real gap** |
| `testing_theatre_checked=0` on code-review (`:57`) | none | **real gap** |
| stages design_marked_for_use, plan_marked_for_use, reviewing, plan_interrupted, and the transition validation block (`:97-147`) | no MCP tool sets these four stages | **real gap** |

Effect on Codex: `active_skill` is never set. GBTW's skill-aware firing matrix
(`get-back-to-work-impl.sh:[881]`) therefore runs its "no skill" row, and the memory-search
gate (`:[1072]`) never fires. Once GBTW grades Codex turns (this loop, R1), the no-skill row
means false continuation and bypass blocks while brainstorming, and rigor, prediction and
evidence checks during execution.

Ported in this loop (plan Task 7): `active_skill` only. The existing mcp-state-logger.sh
PostToolUse entry already matches the transitions and parses changed:true:
- `mark_brainstorming` sets `brainstorming`;
- `mark_debugging` sets `systematic-debugging`;
- `mark_executing` and `start_execution` set `executing-plans`;
- `mark_design_ready` and `mark_plan_ready` set NULL.
Only the brainstorming and executing-plans rows of the firing matrix differ from the no-skill
row. Setting `active_skill` alone does not arm the memory gate: `memory_search_required`
stays 0. On Claude, the port also moves `active_skill` from `code-review` to
`executing-plans` after an A/B review's `mark_executing`.

Not ported in this loop:
- `memory_search_required=1`: real gap, ported in L2b-2 (operator decision D8). See section 2.
- The writing-plans flag and the four hook-only stages have no MCP call at skill start.
  Cross-client proposal: move these writes into MCP transition tools.

## 2. memory-search-tracker.sh (PostToolUse `Task`)

- Clears `memory_search_required` when an `Agent` call names `search-conversations`
  (`memory-search-tracker.sh:15`, `:22`, `:30`). No MCP clear exists.
- **Real gap. Outcome: port in L2b-2 (operator decision D8).** L2b-2 ports both halves on
  both clients: the setter in section 1 and this clearer. It also carries the instruction-parity
  fixes the port depends on. GBTW's block text tells the agent to call the Claude `Agent` tool,
  which Codex does not have.
- Candidate trigger for L2b-2: PostToolUse on the episodic-memory `search` MCP tool, using the
  matcher form `mcp__(plugin_ironclaude_)?episodic[-_]memory__.*` (`hooks.json` PreToolUse
  entry 1).

## 3. subagent-circuit-breaker.sh (PreToolUse `Task`, PostToolUse `Task` and `TaskOutput`)

- Sets `circuit_breaker=1` on a subagent context-limit failure (`:32`) and blocks further
  `Agent` dispatch while it is set (`:45`, `:55`). Nothing else sets it; MCP only clears it
  (`write-tools.ts:[1280]`, `state-machine.ts:[573]`).
- **Real gap**, with no verified Codex trigger: Codex subagent tool names and SubagentStart are
  unobserved. Cross-client proposal: the operator decides between an MCP-side breaker and
  removal.
- UNCERTAIN on Claude, not proven defects (there is no positive control for either):
  - The matcher is `Task`, but the script acts only on `tool_name == "Agent"` (`:55`). The
    Brain template uses `Agent|Task` (`commander/tests/test_daemon.py:2366`).
  - The PostToolUse branch keys on `has("tool_output")` (`:23`). Repo fixtures for Claude
    PostToolUse use `tool_response` (`mcp-state-logger.sh:52`).

## 4. task-completion-validator.sh (PostToolUse `Skill`)

- An advisory LLM check that a task-boundary code review matches the in-progress task. A
  mismatch prints a `systemMessage` and exits 0. Nothing reads its result: it writes no
  DB state (`task-completion-validator.sh:110-117`, `:125`).
- MCP `record_review_verdict` (`write-tools.ts:[1037]`) records grades without a task-match
  check, so this is not covered by MCP. The rule is advisory, though, and nothing depends on it.
- **Proposal: remove on both clients.** Operator decision.

## What ships and what waits

- **Ships:** the `active_skill` port (section 1), on the state-manager matcher shape that
  `docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md` records for Codex.
- **Port in L2b-2 (operator decision D8):** the memory-search pair (sections 1 and 2).
- **Waits:** the circuit breaker and the hook-only stages, which have no Codex trigger.
- Each proposal above waits for the operator's decision.

## Also changed in this task

`apply_patch` (Codex's native edit tool, `professional-mode-guard.sh:112-115`) is added to the
plan-task-context PreToolUse matcher, so review_pending enforcement covers Codex edits.
```

**Step 3: Write the matcher test (RED)**

Create `worker/hooks/tests/test-plan-task-context-apply-patch.sh`:
```bash
#!/usr/bin/env bash
# test-plan-task-context-apply-patch.sh - plan-task-context.sh is wired for
# Codex's native apply_patch (hooks.json matcher) and blocks it while a code
# review is pending. v1.1.16 L2b-1 R3.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-ptc-apply-patch"
DB="$TMP_HOME/.claude/ironclaude.db"
PASSES=0
FAILS=0
EXPECTED_PASSES=3
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

# 1. Matcher admits apply_patch, keeps existing tools, and stays anchored.
M=$(jq -r '.hooks.PreToolUse[] | select(.hooks[].command | contains("plan-task-context.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
if matches "$M" "apply_patch" && matches "$M" "Read" && matches "$M" "Task" \
   && ! matches "$M" "apply_patchX" && ! matches "$M" "exec_command"; then
  ok "1 plan-task-context matcher admits apply_patch only as an exact name"
else
  bad "1 plan-task-context matcher admits apply_patch only as an exact name (matcher: $M)"
fi

# Session: executing, review pending, one submitted task in wave 1.
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', review_pending=1, current_wave=1 WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'submitted');"

# 2. apply_patch is blocked while review is pending.
AP_IN=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"PreToolUse",tool_name:"apply_patch",tool_input:{command:"*** Begin Patch\n*** Add File: x.txt\n+hi\n*** End Patch"}}')
printf '%s' "$AP_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/plan-task-context.sh" >/dev/null 2>"$TMP_ROOT/err"
RC=$?
if [ "$RC" = "2" ] && grep -qF "BLOCKED — CODE REVIEW REQUIRED" "$TMP_ROOT/err"; then
  ok "2 apply_patch blocked during review_pending"
else
  bad "2 apply_patch blocked during review_pending (rc=$RC)"
fi

# 3. Negative control: Read is allowed in the same state.
RD_IN=$(jq -cn --arg s "$SESSION" '{session_id:$s,hook_event_name:"PreToolUse",tool_name:"Read",tool_input:{file_path:"x.txt"}}')
printf '%s' "$RD_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/plan-task-context.sh" >/dev/null 2>&1
RC=$?
[ "$RC" = "0" ] && ok "3 Read allowed during review_pending" || bad "3 Read allowed during review_pending (rc=$RC)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 4: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-plan-task-context-apply-patch.sh
```
Expected: exit 1, with `FAIL  1 plan-task-context matcher admits apply_patch only as an exact name (matcher: Read|Grep|Glob|Edit|Write|NotebookEdit|MultiEdit|Bash|Task)`. Cases 2 and 3 pass at HEAD, because the script already handles any tool name.

**Step 5: Add `apply_patch` to the matcher**

In `worker/hooks/hooks.json`, replace this text:
```json
        "matcher": "Read|Grep|Glob|Edit|Write|NotebookEdit|MultiEdit|Bash|Task"
```
with this text:
```json
        "matcher": "Read|Grep|Glob|Edit|Write|NotebookEdit|MultiEdit|Bash|Task|apply_patch"
```

**Step 6: Run the test**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-plan-task-context-apply-patch.sh
```
Expected: `Results: 3 pass, 0 fail`, exit 0.

Run:
```bash
jq empty /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json
```
Expected: no output, exit 0.

**Step 7: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hooks.json worker/hooks/tests/test-plan-task-context-apply-patch.sh
```
Expected: no output, exit 0.

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-skill-task-hook-audit.md
```
Expected: no output, exit 0.

---

## Task 5: Wire the `EnterPlanMode` block (R4)

Depends on Task 4, which also edits `hooks.json`.

**Files:**
- Modify: `worker/hooks/hooks.json`, the professional-mode-guard matcher (PreToolUse entry 4 of 6).
- Create: `worker/hooks/tests/test-pmguard-enterplanmode.sh`.

**Step 1: Write the test (RED)**

Create `worker/hooks/tests/test-pmguard-enterplanmode.sh`:
```bash
#!/usr/bin/env bash
# test-pmguard-enterplanmode.sh - the guard's PreToolUse matcher includes
# EnterPlanMode, and the existing block (professional-mode-guard.sh, "USE
# BRAINSTORMING INSTEAD") fires for it when professional mode is on. v1.1.16 L2b-1 R4.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-epm-session"
DB="$TMP_HOME/.claude/ironclaude.db"
PASSES=0
FAILS=0
EXPECTED_PASSES=3
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

# 1. Matcher admits EnterPlanMode as an exact name and keeps existing tools.
M=$(jq -r '.hooks.PreToolUse[] | select(.hooks[].command | contains("professional-mode-guard.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
if matches "$M" "EnterPlanMode" && matches "$M" "apply_patch" && matches "$M" "exec_command" \
   && ! matches "$M" "EnterPlanModeX" && ! matches "$M" "ExitPlanMode"; then
  ok "1 guard matcher admits EnterPlanMode only as an exact name"
else
  bad "1 guard matcher admits EnterPlanMode only as an exact name (matcher: $M)"
fi

printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
EPM_IN=$(jq -cn --arg s "$SESSION" --arg c "$TMP_ROOT" '{session_id:$s,hook_event_name:"PreToolUse",cwd:$c,tool_name:"EnterPlanMode",tool_input:{}}')

# 2. Professional mode on: blocked with the brainstorming redirect.
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
printf '%s' "$EPM_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/professional-mode-guard.sh" >/dev/null 2>"$TMP_ROOT/err"
RC=$?
if [ "$RC" = "2" ] && grep -qF "BLOCKED — USE BRAINSTORMING INSTEAD" "$TMP_ROOT/err"; then
  ok "2 EnterPlanMode blocked with professional mode on"
else
  bad "2 EnterPlanMode blocked with professional mode on (rc=$RC)"
fi

# 3. Negative control: professional mode off allows it (Invariant B).
sqlite3 "$DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='${SESSION}';"
printf '%s' "$EPM_IN" | HOME="$TMP_HOME" bash "$HOOKS_DIR/professional-mode-guard.sh" >/dev/null 2>&1
RC=$?
[ "$RC" = "0" ] && ok "3 EnterPlanMode allowed with professional mode off" || bad "3 EnterPlanMode allowed with professional mode off (rc=$RC)"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-pmguard-enterplanmode.sh
```
Expected: exit 1, with `FAIL  1 guard matcher admits EnterPlanMode only as an exact name (matcher: Edit|Write|NotebookEdit|MultiEdit|Bash|Read|Grep|Glob|apply_patch|exec_command)`. Cases 2 and 3 pass at HEAD: the script-side block at `professional-mode-guard.sh:492-501` already exists, but no matcher routes `EnterPlanMode` to it.

**Step 3: Add `EnterPlanMode` to the guard matcher**

In `worker/hooks/hooks.json`, replace this text:
```json
        "matcher": "Edit|Write|NotebookEdit|MultiEdit|Bash|Read|Grep|Glob|apply_patch|exec_command"
```
with this text:
```json
        "matcher": "Edit|Write|NotebookEdit|MultiEdit|Bash|Read|Grep|Glob|apply_patch|exec_command|EnterPlanMode"
```

**Step 4: Run the tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-pmguard-enterplanmode.sh
```
Expected: `Results: 3 pass, 0 fail`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-managed-worktree-guard.sh
```
Expected: exit 0. Its matcher assertions at lines 173-175 still hold.

**Step 5: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hooks.json worker/hooks/tests/test-pmguard-enterplanmode.sh
```
Expected: no output, exit 0.

---

## Task 6: Execution drift check (R5)

Depends on Tasks 3 and 5, which edit `get-back-to-work-impl.sh` and `hooks.json`.

**Step 0: Gate on the Codex MCP classification (D9)**

Run:
```bash
grep -n -e "MCP classification:" /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md
```
Expected: one line, recorded by Task 3 Step 7. What happens next depends on it:
- **(a):** do Steps 1–8 as written.
- **(b) O=… P=…:** do Steps 1–8, and apply Step 6b before Step 7. Use the recorded P literally wherever `<P>` appears.
- **(c) BLOCKER:** stop. Change no file in this task. Report to the operator that Codex fires no PreToolUse or PostToolUse hook for state-manager MCP calls, quoting the findings line. Propose the cross-client alternative: record the baseline at `claim_task`, and refuse at `submit_task`, inside the state-manager server. Task 6 does not ship as-is.

**Files:**
- Create: `worker/hooks/execution-drift-check.sh`.
- Modify: `worker/hooks/get-back-to-work-impl.sh`, after the hook-logger source block (lines 24-28) and inside the plan-active branch after the subagent detection (ends at line 450 at HEAD).
- Modify: `worker/hooks/hooks.json`. Append one entry at the **end** of PreToolUse and one at the **end** of PostToolUse. Codex trust keys are positional (`pre_tool_use:N:0`), so appending leaves existing keys in place.
- Create: `worker/hooks/tests/test-execution-drift-check.sh`.

**Fail-open and fail-closed choices:**

| Condition | Result |
|---|---|
| professional mode not `on` | skip: `log_hook "Disabled"`, exit 0 |
| tool is not a state-manager tool, or stage is not executing/reviewing | skip: `log_hook "Skipped"`, exit 0 |
| no `cwd`, or `cwd` not in a git repo; git status fails | fail open: `log_hook "Skipped"` |
| session row, `plan_json` or its `tasks` array unreadable | fail open |
| no baseline for the task (claimed before deploy, or the baseline write failed) | fail open |
| baseline write fails at `claim_task` | `log_hook "Skipped"`, exit 0; never blocks the claim |
| readable baseline and plan, non-empty drift | **fail closed**: `submit_task` gets exit 2 with the file list; Stop gets `increment_block_counter` then `block_stop` |

- **Why fail open.** The `allowed_files` write guard in `professional-mode-guard.sh` already enforces tool writes. This check only adds coverage for shell-written side quests.
- **Library mode** has no logging of its own. It returns status 1 for fail-open, and the caller logs.

**Step 1: Write the test (RED)**

Create `worker/hooks/tests/test-execution-drift-check.sh`:
```bash
#!/usr/bin/env bash
# test-execution-drift-check.sh - execution-drift-check.sh (v1.1.16 L2b-1 R5).
# Baseline at claim_task (PostToolUse), check at submit_task (PreToolUse) and at
# Stop while executing (get-back-to-work-impl.sh). Drift = dirty now - baseline -
# plan allowed_files; gitignored files never appear.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$HOOKS_DIR/execution-drift-check.sh"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
REPO="$TMP_ROOT/repo"
SESSION="ic-drift-session"
DB="$TMP_HOME/.claude/ironclaude.db"
BASELINE="$TMP_HOME/.claude/ironclaude-drift-baseline-${SESSION}-1.txt"
THROTTLE="/tmp/.claude-block-throttle-${SESSION}"
mkdir -p "$TMP_HOME/.claude"
rm -f "$THROTTLE"
trap 'rm -rf "$TMP_ROOT"; rm -f "$THROTTLE" "/tmp/.claude-bypass-counter-${SESSION}"' EXIT

PASSES=0
FAILS=0
EXPECTED_PASSES=9
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
matches() { printf '%s' "$2" | grep -qE "^($1)$"; }

invoke() {  # TOOL_NAME HOOK_EVENT -> exit code; stderr in $TMP_ROOT/err
  local input
  input=$(jq -cn --arg s "$SESSION" --arg c "$REPO" --arg t "$1" --arg e "$2" \
    '{session_id:$s,hook_event_name:$e,cwd:$c,tool_name:$t,tool_input:{task_id:1},tool_response:{content:[{type:"text",text:"{\"success\":true}"}]}}')
  printf '%s' "$input" | HOME="$TMP_HOME" bash "$HOOK" >/dev/null 2>"$TMP_ROOT/err"
  echo $?
}
CLAIM="mcp__plugin_ironclaude_state-manager__claim_task"
SUBMIT="mcp__plugin_ironclaude_state-manager__submit_task"

# Repo with a committed file, a .gitignore, and dirt that predates the claim.
git init -q "$REPO"
printf 'base\n' > "$REPO/pre.txt"
printf 'ignored.log\n' > "$REPO/.gitignore"
git -C "$REPO" add pre.txt .gitignore
git -C "$REPO" -c user.email=t@example.com -c user.name=t commit -q -m init
printf 'dirty before claim\n' >> "$REPO/pre.txt"
printf 'untracked before claim\n' > "$REPO/pre-untracked.txt"

# Session: executing, wave 1, task 1 in progress; plan allows src/allowed.txt (task 1) and docs/other.md (task 2).
printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='executing', current_wave=1, plan_name='t',
  plan_json='{\"name\":\"t\",\"tasks\":[{\"id\":1,\"allowed_files\":[\"src/allowed.txt\"]},{\"id\":2,\"allowed_files\":[\"docs/other.md\"]}]}'
  WHERE terminal_session='${SESSION}';
INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES ('${SESSION}', 1, 1, 'Task one', 'in_progress');"

# 1. claim_task records the baseline with both pre-claim dirty paths.
RC=$(invoke "$CLAIM" "PostToolUse")
if [ "$RC" = "0" ] && grep -qxF "pre.txt" "$BASELINE" 2>/dev/null && grep -qxF "pre-untracked.txt" "$BASELINE" 2>/dev/null; then
  ok "1 claim_task records the dirty-file baseline"
else
  bad "1 claim_task records the dirty-file baseline (rc=$RC)"
fi

# Work after the claim: an allowed file in a new directory, a gitignored file,
# and a further change to a file that was already dirty.
mkdir -p "$REPO/src"
printf 'allowed\n' > "$REPO/src/allowed.txt"
printf 'ignored\n' > "$REPO/ignored.log"
printf 'changed again\n' >> "$REPO/pre.txt"

# 2. Allowed, gitignored and pre-existing-dirty files do not block.
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "2 allowed, gitignored and pre-dirty files pass" || bad "2 allowed, gitignored and pre-dirty files pass (rc=$RC: $(cat "$TMP_ROOT/err"))"

# A side quest: a new file outside allowed_files.
mkdir -p "$REPO/side"
printf 'quest\n' > "$REPO/side/quest.txt"

# 3. Professional mode off: skipped, no block.
sqlite3 "$DB" "UPDATE sessions SET professional_mode='off' WHERE terminal_session='${SESSION}';"
RC=$(invoke "$SUBMIT" "PreToolUse")
[ "$RC" = "0" ] && ok "3 professional mode off skips the check" || bad "3 professional mode off skips the check (rc=$RC)"
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on' WHERE terminal_session='${SESSION}';"

# 4. A repeated claim keeps the original baseline (write-once).
invoke "$CLAIM" "PostToolUse" >/dev/null
if ! grep -qxF "side/quest.txt" "$BASELINE" 2>/dev/null; then ok "4 baseline is write-once"; else bad "4 baseline is write-once"; fi

# 5. submit_task blocks and lists only the side quest.
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qF "BLOCKED — EXECUTION DRIFT" \
   && printf '%s' "$ERR" | grep -qxF "  - side/quest.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - src/allowed.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - pre.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - pre-untracked.txt" \
   && ! printf '%s' "$ERR" | grep -qxF "  - ignored.log"; then
  ok "5 submit_task blocks on the new file outside allowed_files"
else
  bad "5 submit_task blocks on the new file outside allowed_files (rc=$RC: $ERR)"
fi

# 6. The underscore MCP name form also blocks. Under classification (b), Step 6b
#    replaces this name with the observed Codex form.
RC=$(invoke "mcp__state_manager__submit_task" "PreToolUse")
[ "$RC" = "2" ] && ok "6 alternate MCP name form blocks" || bad "6 alternate MCP name form blocks (rc=$RC)"

# 7. Stop while executing blocks through increment_block_counter + block_stop.
STOP_IN=$(jq -cn --arg s "$SESSION" --arg c "$REPO" '{session_id:$s,hook_event_name:"Stop",stop_hook_active:false,cwd:$c,last_assistant_message:"Working."}')
STOP_OUT=$(printf '%s' "$STOP_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
if printf '%s' "$STOP_OUT" | jq -cse 'length == 1 and .[0].decision == "block" and (.[0].reason | contains("BLOCKED — EXECUTION DRIFT")) and (.[0].reason | contains("side/quest.txt"))' >/dev/null 2>&1 \
   && grep -q '^1:' "$THROTTLE" 2>/dev/null; then
  ok "7 Stop blocks on drift and increments the block counter"
else
  bad "7 Stop blocks on drift and increments the block counter (out: $STOP_OUT)"
fi

# 8-9. hooks.json: the drift entries are last in their arrays and their matchers are bounded.
POST_M=$(jq -r '.hooks.PostToolUse[-1] | select(.hooks[0].command | contains("execution-drift-check.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
PRE_M=$(jq -r '.hooks.PreToolUse[-1] | select(.hooks[0].command | contains("execution-drift-check.sh")) | .matcher' "$HOOKS_DIR/hooks.json")
if [ -n "$POST_M" ] && matches "$POST_M" "$CLAIM" && matches "$POST_M" "mcp__state_manager__claim_task" \
   && ! matches "$POST_M" "$SUBMIT" && ! matches "$POST_M" "mcp__other_state-manager__claim_task"; then
  ok "8 PostToolUse claim_task matcher is last and bounded"
else
  bad "8 PostToolUse claim_task matcher is last and bounded (matcher: $POST_M)"
fi
if [ -n "$PRE_M" ] && matches "$PRE_M" "$SUBMIT" && matches "$PRE_M" "mcp__state_manager__submit_task" \
   && ! matches "$PRE_M" "$CLAIM" && ! matches "$PRE_M" "mcp__other_state-manager__submit_task"; then
  ok "9 PreToolUse submit_task matcher is last and bounded"
else
  bad "9 PreToolUse submit_task matcher is last and bounded (matcher: $PRE_M)"
fi

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
```
Expected: exit 1, with `FAIL  1 claim_task records the dirty-file baseline (rc=127)`. The hook script does not exist at HEAD, so bash exits 127.

**Step 3: Create `worker/hooks/execution-drift-check.sh`**

```bash
#!/bin/bash
# execution-drift-check.sh - execution side-quest detector (v1.1.16 L2b-1, R5).
#
# Triggers (worker/hooks/hooks.json):
#   PostToolUse  state-manager claim_task   -> record the dirty-file baseline
#   PreToolUse   state-manager submit_task  -> block (exit 2) on drift
#   Library      IC_DRIFT_LIB=1 source      -> ic_drift_stop_violations, used by
#                                              get-back-to-work-impl.sh at Stop
#
# Drift = paths dirty now (git status; gitignored files never appear)
#         minus paths dirty when the task was claimed (the baseline)
#         minus the union of the plan's tasks[].allowed_files (exact repo-relative match).
#
# Fail-open (no block; the caller logs via log_hook): professional mode not 'on';
#   stage not executing/reviewing; no cwd or cwd not in a git repo; session row,
#   plan_json or its tasks array unreadable; no baseline for the task. The
#   allowed_files write guard in professional-mode-guard.sh still covers tool writes.
# Fail-closed (block): a readable baseline and plan with a non-empty drift set.
# Baselines are write-once per wave_tasks row id, so a repeated or failed
# claim_task never moves the baseline later.

_drift_sql() {
  local db="$HOME/.claude/ironclaude.db"
  [ -f "$db" ] || return 0
  sqlite3 -cmd ".timeout 10000" "$db" "$1" 2>/dev/null || true
}

_drift_q() { printf '%s' "${1:-}" | sed "s/'/''/g"; }

_drift_repo_root() {
  local cwd="${1:-}" top
  [ -n "$cwd" ] && [ -d "$cwd" ] || return 1
  top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || return 1
  [ -n "$top" ] || return 1
  printf '%s' "$top"
}

# Repo-relative dirty paths, one per line, sorted. --untracked-files=all lists
# files inside new directories individually; -z avoids path quoting. A rename or
# copy record is followed by its source path, which is skipped.
_drift_dirty_paths() {
  local root="$1"
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || return 1
  git -C "$root" status --porcelain=v1 -z --untracked-files=all 2>/dev/null | {
    rec=""
    skip_next=0
    while IFS= read -r -d '' rec; do
      if [ "$skip_next" = "1" ]; then
        skip_next=0
        continue
      fi
      case "${rec:0:2}" in
        R?|C?|?R|?C) skip_next=1 ;;
      esac
      printf '%s\n' "${rec:3}"
    done
  } | LC_ALL=C sort -u
}

# Union of the plan's tasks[].allowed_files, sorted. Status 1 when no plan or no tasks array.
_drift_allowed_files() {
  local plan out
  plan=$(_drift_sql "SELECT plan_json FROM sessions WHERE terminal_session='$(_drift_q "$1")';")
  [ -n "$plan" ] || return 1
  out=$(printf '%s' "$plan" | jq -r 'if (.tasks | type) == "array" then [.tasks[] | (.allowed_files // [])[] | strings] | .[] else error("plan has no tasks array") end' 2>/dev/null) || return 1
  printf '%s\n' "$out" | sed '/^$/d' | LC_ALL=C sort -u
}

_drift_baseline_file() {  # SESSION ROW_ID
  local safe
  safe=$(printf '%s' "$1" | tr -cd 'a-zA-Z0-9_-')
  printf '%s/.claude/ironclaude-drift-baseline-%s-%s.txt' "$HOME" "$safe" "$2"
}

# Newest wave_tasks row id for SESSION's TASK_ID in the current wave.
_drift_row_id() {  # SESSION TASK_ID
  local id
  [[ "${2:-}" =~ ^[0-9]+$ ]] || return 1
  id=$(_drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.task_id=$2 AND w.wave_number = s.current_wave ORDER BY w.id DESC LIMIT 1;")
  [ -n "$id" ] || return 1
  printf '%s' "$id"
}

# ic_drift_record_baseline SESSION TASK_ID CWD
# Status 0: baseline written or already present. Status 1: skipped.
ic_drift_record_baseline() {
  local row file root paths tmp
  row=$(_drift_row_id "$1" "$2") || return 1
  file=$(_drift_baseline_file "$1" "$row")
  [ -e "$file" ] && return 0
  root=$(_drift_repo_root "$3") || return 1
  paths=$(_drift_dirty_paths "$root") || return 1
  mkdir -p "$(dirname "$file")" || return 1
  tmp="${file}.tmp.$$"
  printf '%s\n' "$paths" > "$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
  return 0
}

# ic_drift_violations SESSION CWD ROW_ID...
# Prints drift paths (possibly none) with status 0; status 1 means fail-open.
ic_drift_violations() {
  local session="$1" cwd="$2" root allowed baseline="" row file current
  shift 2
  root=$(_drift_repo_root "$cwd") || return 1
  allowed=$(_drift_allowed_files "$session") || return 1
  for row in "$@"; do
    file=$(_drift_baseline_file "$session" "$row")
    [ -f "$file" ] || return 1
    baseline="${baseline}$(cat "$file")"$'\n'
  done
  current=$(_drift_dirty_paths "$root") || return 1
  comm -23 \
    <(printf '%s\n' "$current" | sed '/^$/d' | LC_ALL=C sort -u) \
    <(printf '%s\n%s\n' "$baseline" "$allowed" | sed '/^$/d' | LC_ALL=C sort -u)
}

# ic_drift_stop_violations SESSION CWD
# Stop-time check across every in_progress task of the current wave. Status 0 with
# no output when nothing is in progress; status 1 means fail-open.
ic_drift_stop_violations() {
  local rows
  rows=$(_drift_sql "SELECT w.id FROM wave_tasks w JOIN sessions s ON s.terminal_session = w.terminal_session WHERE w.terminal_session='$(_drift_q "$1")' AND w.status='in_progress' AND w.wave_number = s.current_wave ORDER BY w.id;")
  [ -n "$rows" ] || return 0
  # shellcheck disable=SC2086
  ic_drift_violations "$1" "$2" $rows
}

# ic_drift_block_message FILES -> the block text listing each drift path.
ic_drift_block_message() {
  local list
  list=$(printf '%s\n' "$1" | sed '/^$/d; s/^/  - /')
  printf '%s\n\n%s\n%s\n\n%s' \
    "BLOCKED — EXECUTION DRIFT" \
    "These files changed after the current task was claimed, and none of them is in the plan's allowed_files:" \
    "$list" \
    "Remove them if they are a side quest. If the plan needs them, retreat and amend the plan so they are listed in allowed_files."
}

# Library mode: define the functions only.
if [ "${IC_DRIFT_LIB:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  exit 0
fi
run_hook "EXECUTION-DRIFT-CHECK"

INPUT=$(cat)
init_session_id

# Professional-mode gate (idiom from plan-execution-reminder.sh). A plain read,
# not db_read_or_fail: its hard_fail exits 2 and would block claim/submit.
SAFE_SESSION=$(_drift_q "$SESSION_TAG")
PROF_MODE=$(_drift_sql "SELECT professional_mode FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
if [ "$PROF_MODE" != "on" ]; then
  log_hook "EXECUTION-DRIFT-CHECK" "Disabled" "professional mode ${PROF_MODE:-unreadable}"
  exit 0
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || true)
EVENT_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || true)
TASK_ID=$(printf '%s' "$INPUT" | jq -r '.tool_input.task_id // empty' 2>/dev/null || true)
case "$TOOL_NAME" in
  *state_manager__*|*state-manager__*) ;;
  *)
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "not a state-manager tool: ${TOOL_NAME:-none}"
    exit 0
    ;;
esac
SHORT_TOOL="${TOOL_NAME##*__}"

WORKFLOW=$(_drift_sql "SELECT workflow_stage FROM sessions WHERE terminal_session='${SAFE_SESSION}';")
if [ "$WORKFLOW" != "executing" ] && [ "$WORKFLOW" != "reviewing" ]; then
  log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "workflow_stage=${WORKFLOW:-unreadable}"
  exit 0
fi

case "$SHORT_TOOL" in
  claim_task)
    if ic_drift_record_baseline "$SESSION_TAG" "$TASK_ID" "$EVENT_CWD"; then
      log_hook "EXECUTION-DRIFT-CHECK" "Baseline" "task ${TASK_ID}: baseline present (write-once)"
    else
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: no baseline (no wave_tasks row, no git repo, or git failed)"
    fi
    exit 0
    ;;
  submit_task)
    if ! ROW_ID=$(_drift_row_id "$SESSION_TAG" "$TASK_ID"); then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID:-none}: no wave_tasks row in the current wave"
      exit 0
    fi
    if ! DRIFT=$(ic_drift_violations "$SESSION_TAG" "$EVENT_CWD" "$ROW_ID"); then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "task ${TASK_ID}: fail-open (no git repo, plan, or baseline)"
      exit 0
    fi
    if [ -n "$DRIFT" ]; then
      block_pretooluse "EXECUTION-DRIFT-CHECK" "$(ic_drift_block_message "$DRIFT")"
    fi
    log_hook "EXECUTION-DRIFT-CHECK" "Allowed" "task ${TASK_ID}: no drift"
    exit 0
    ;;
  *)
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "unhandled state-manager tool: ${SHORT_TOOL}"
    exit 0
    ;;
esac
```

**Step 4: Load the library in GBTW**

In `worker/hooks/get-back-to-work-impl.sh`, replace this text (lines 24-28):
```bash
# Source shared logging (provides db_read_or_fail, db_write_or_fail, block_stop, etc.)
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  echo '{"systemMessage": "[GET-BACK-TO-WORK]: CRITICAL - Failed to load hook-logger.sh!"}'
  echo "CRITICAL: hook-logger.sh failed to load" >&2
fi
```
with this text:
```bash
# Source shared logging (provides db_read_or_fail, db_write_or_fail, block_stop, etc.)
if ! source "$SCRIPT_DIR/hook-logger.sh" 2>/dev/null; then
  echo '{"systemMessage": "[GET-BACK-TO-WORK]: CRITICAL - Failed to load hook-logger.sh!"}'
  echo "CRITICAL: hook-logger.sh failed to load" >&2
fi

# Execution drift check library (ic_drift_stop_violations). When it is missing,
# the Stop-time drift check below is skipped (fail-open).
IC_DRIFT_LIB=1 source "$SCRIPT_DIR/execution-drift-check.sh" 2>/dev/null || true
```

**Step 5: Call the drift check at Stop while executing**

Replace this text:
```bash
    # Read current_wave for wave-scoped queries (H3 fix)
```
with this text:
```bash
    # Execution drift check (execution-drift-check.sh, R5): while executing, block on
    # files changed since the in-progress task's claim that are outside the plan's
    # allowed_files. It goes through increment_block_counter like every Stop block,
    # so the 3-blocks-in-5-minutes escape hatch above still applies. Fail-open when
    # git, the plan, or a baseline is unavailable.
    if [ "$WORKFLOW_STAGE" = "executing" ] && [ "$IS_SUBAGENT" = "false" ] \
       && type ic_drift_stop_violations >/dev/null 2>&1; then
        _ic_drift_cwd=$(printf '%s' "$EVENT" | jq -r '.cwd // empty' 2>/dev/null || true)
        if _ic_drift_files=$(ic_drift_stop_violations "$SESSION_TAG" "$_ic_drift_cwd"); then
            if [ -n "$_ic_drift_files" ]; then
                increment_block_counter
                block_stop "GET-BACK-TO-WORK" "$(ic_drift_block_message "$_ic_drift_files")"
            fi
        else
            log_hook "GET-BACK-TO-WORK" "Skipped" "execution drift check fail-open (no git repo, plan, or baseline)"
        fi
    fi

    # Read current_wave for wave-scoped queries (H3 fix)
```

**Step 6: Append the hooks.json entries**

In `worker/hooks/hooks.json`, replace this text (the last PreToolUse entry and the start of PostToolUse):
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"
          }
        ],
        "matcher": "Read|Bash|Grep|Glob"
      }
    ],
    "PostToolUse": [
```
with this text:
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"
          }
        ],
        "matcher": "Read|Bash|Grep|Glob"
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh"
          }
        ],
        "matcher": "mcp__(plugin_ironclaude_)?state[-_]manager__submit_task"
      }
    ],
    "PostToolUse": [
```

Then replace this text (the last PostToolUse entry and the start of UserPromptSubmit):
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"
          }
        ],
        "matcher": "Read|Bash|Grep|Glob"
      }
    ],
    "UserPromptSubmit": [
```
with this text:
```json
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/poll-dedup.sh"
          }
        ],
        "matcher": "Read|Bash|Grep|Glob"
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh"
          }
        ],
        "matcher": "mcp__(plugin_ironclaude_)?state[-_]manager__claim_task"
      }
    ],
    "UserPromptSubmit": [
```

**Step 6b (classification (b) only): adapt to the observed Codex prefix**

Let `<P>` be the recorded prefix and `<Pre>` the same text with every character outside `[A-Za-z0-9_-]` escaped with a backslash, for use in a regex. Then:
1. In `worker/hooks/hooks.json`:
   - replace `"matcher": "mcp__(plugin_ironclaude_)?state[-_]manager__submit_task"` with `"matcher": "mcp__(plugin_ironclaude_)?state[-_]manager__submit_task|<Pre>submit_task"`;
   - replace `"matcher": "mcp__(plugin_ironclaude_)?state[-_]manager__claim_task"` with `"matcher": "mcp__(plugin_ironclaude_)?state[-_]manager__claim_task|<Pre>claim_task"`.
2. In `worker/hooks/execution-drift-check.sh`, replace `  *state_manager__*|*state-manager__*) ;;` with `  *state_manager__*|*state-manager__*|"<P>"*) ;;`. The `SHORT_TOOL="${TOOL_NAME##*__}"` line stays, unless `<P>` does not end in `__`. In that case, replace it with `SHORT_TOOL="${TOOL_NAME#"<P>"}"` guarded by `case "$TOOL_NAME" in "<P>"*) ;; *) SHORT_TOOL="${TOOL_NAME##*__}" ;; esac`. Write it out literally.
3. In `worker/hooks/tests/test-execution-drift-check.sh`:
   - in case 6, replace `"mcp__state_manager__submit_task"` with `"<P>submit_task"`;
   - in case 8, add `&& matches "$POST_M" "<P>claim_task"`;
   - in case 9, add `&& matches "$PRE_M" "<P>submit_task"`.

**Step 7: Run the tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh
```
Expected: `Results: 9 pass, 0 fail`, exit 0.

Run:
```bash
jq -r '[.hooks.PreToolUse | length, .hooks.PostToolUse | length] | @tsv' /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json
```
Expected: `7	7`. At HEAD this prints `6	6`.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-stop-wrapper.sh
```
Expected: last line `ALL WRAPPER TESTS PASSED`, exit 0.

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-gbtw-review-gate.sh
```
Expected: exit 0.

**Step 8: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/get-back-to-work-impl.sh worker/hooks/hooks.json worker/hooks/tests/test-execution-drift-check.sh
```
Expected: no output, exit 0.

---

## Task 7: Port `active_skill` to MCP transitions (R3 real gap)

Depends on Task 3, which records the Codex MCP classification.

**Files:**
- Modify: `worker/hooks/mcp-state-logger.sh`, between the `CHANGED` check (lines 59-61) and the stage read (line 63).
- Create: `worker/hooks/tests/test-mcp-state-logger-active-skill.sh`.
- Modify, under classification (b) only: `worker/hooks/hooks.json:133` (the mcp-state-logger matcher) and `worker/hooks/mcp-state-logger.sh:22-23` (its tool-name case arm).

**Why this ships now.** Task 2 makes GBTW grade Codex turns. Without `active_skill`, Codex always gets the no-skill firing row (audit note, section 1).

**Constraints:**
- Under (a), no `hooks.json` change: the existing PostToolUse entry already matches these transitions, so its Codex trust hash is unchanged.
- Under (c), the port ships for Claude and is inert on Codex. It can block nothing; the findings file records the Codex gap.
- `memory_search_required` is not touched.
- The update writes only when the value differs (`IS NOT`) and leaves `updated_at` alone. `test-workflow-transition-idempotency.sh:115-118` asserts that a changed:true `mark_brainstorming`, on a row already set to `brainstorming`, leaves the snapshot unchanged with exactly one output line.

**Step 0: Read the Codex MCP classification**

Run:
```bash
grep -n -e "MCP classification:" /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-03-client-parity-hooks-codex-ups-findings.md
```
Expected: one line, `(a)`, `(b) … P=<P> …` or `(c) BLOCKER …`. Under (b), apply Step 3b after Step 3.

**Step 1: Write the test (RED)**

Create `worker/hooks/tests/test-mcp-state-logger-active-skill.sh`:
```bash
#!/usr/bin/env bash
# test-mcp-state-logger-active-skill.sh - mcp-state-logger.sh sets active_skill
# from changed:true state-manager transitions, on both clients (Codex tool_output,
# Claude tool_response). v1.1.16 L2b-1 R3 port.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
TMP_HOME="$TMP_ROOT/home"
mkdir -p "$TMP_HOME/.claude"
trap 'rm -rf "$TMP_ROOT"' EXIT
SESSION="ic-msl-active-skill"
DB="$TMP_HOME/.claude/ironclaude.db"
PASSES=0
FAILS=0
EXPECTED_PASSES=6
ok()  { printf 'PASS  %s\n' "$1"; PASSES=$((PASSES + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
skill() { sqlite3 "$DB" "SELECT COALESCE(active_skill, 'NULL') || '|' || memory_search_required FROM sessions WHERE terminal_session='${SESSION}';"; }
fire() {  # TOOL_SHORT_NAME CHANGED ENVELOPE(tool_output|tool_response)
  jq -cn --arg s "$SESSION" --arg t "mcp__plugin_ironclaude_state-manager__$1" --argjson c "$2" --arg e "$3" \
    '{session_id:$s,hook_event_name:"PostToolUse",tool_name:$t} + {($e): {changed:$c}}' \
    | HOME="$TMP_HOME" bash "$HOOKS_DIR/mcp-state-logger.sh" >/dev/null 2>&1
}

printf '{"session_id":"%s","source":"startup"}' "$SESSION" | HOME="$TMP_HOME" bash "$HOOKS_DIR/session-init.sh" >/dev/null 2>&1
sqlite3 "$DB" "UPDATE sessions SET professional_mode='on', workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"

fire mark_brainstorming true tool_output
[ "$(skill)" = "brainstorming|0" ] && ok "1 mark_brainstorming (Codex envelope) -> brainstorming, memory flag untouched" || bad "1 mark_brainstorming -> brainstorming (got: $(skill))"

fire mark_design_ready true tool_response
[ "$(skill)" = "NULL|0" ] && ok "2 mark_design_ready (Claude envelope) -> NULL" || bad "2 mark_design_ready -> NULL (got: $(skill))"

fire start_execution true tool_output
[ "$(skill)" = "executing-plans|0" ] && ok "3 start_execution -> executing-plans" || bad "3 start_execution -> executing-plans (got: $(skill))"

fire mark_brainstorming false tool_output
[ "$(skill)" = "executing-plans|0" ] && ok "4 changed:false leaves active_skill" || bad "4 changed:false leaves active_skill (got: $(skill))"

fire create_plan true tool_output
[ "$(skill)" = "executing-plans|0" ] && ok "5 unmapped transition leaves active_skill" || bad "5 unmapped transition leaves active_skill (got: $(skill))"

fire mark_debugging true tool_response
[ "$(skill)" = "systematic-debugging|0" ] && ok "6 mark_debugging -> systematic-debugging" || bad "6 mark_debugging -> systematic-debugging (got: $(skill))"

printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ] && [ "$PASSES" -eq "$EXPECTED_PASSES" ]
```

**Step 2: Run the test and see it fail**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-mcp-state-logger-active-skill.sh
```
Expected: exit 1, with `FAIL  1 mark_brainstorming -> brainstorming (got: NULL|0)`. The logger writes nothing at HEAD.

**Step 3: Add the port to `mcp-state-logger.sh`**

Replace this text:
```bash
if [ "$CHANGED" != "true" ]; then
  exit 0
fi
```
with this text:
```bash
if [ "$CHANGED" != "true" ]; then
  exit 0
fi

# Cross-client active_skill (v1.1.16 L2b-1 R3 port; docs/plans/2026-10-03-skill-task-hook-audit.md).
# skill-state-bridge.sh sets active_skill only for Claude's Skill tool; these MCP
# transitions run on both clients. GBTW's firing matrix reads active_skill.
# memory_search_required is deliberately untouched: its Codex clear path depends on L2b-2.
# Writes only when the value differs and leaves updated_at alone, so an equal value is a no-op.
NEW_SKILL_SQL=""
case "$SHORT_TOOL" in
  mark_brainstorming) NEW_SKILL_SQL="'brainstorming'" ;;
  mark_debugging) NEW_SKILL_SQL="'systematic-debugging'" ;;
  mark_executing|start_execution) NEW_SKILL_SQL="'executing-plans'" ;;
  mark_design_ready|mark_plan_ready) NEW_SKILL_SQL="NULL" ;;
esac
if [ -n "$NEW_SKILL_SQL" ] && [ -n "$SESSION_TAG" ] && [ "$SESSION_TAG" != "none" ] && [ -f "$DB_PATH" ]; then
  SKILL_SESSION=$(echo "$SESSION_TAG" | sed "s/'/''/g")
  sqlite3 "$DB_PATH" ".timeout 10000" \
    "UPDATE sessions SET active_skill=${NEW_SKILL_SQL} WHERE terminal_session='${SKILL_SESSION}' AND active_skill IS NOT ${NEW_SKILL_SQL};" \
    2>/dev/null || log_hook "mcp-state-logger" "Skipped" "active_skill update failed after $SHORT_TOOL"
fi
```

Also replace the header line:
```bash
# Logs workflow_stage transitions triggered by MCP state-manager tools.
```
with:
```bash
# Logs workflow_stage transitions triggered by MCP state-manager tools, and sets
# sessions.active_skill from them on both clients (see the active_skill block below).
```

**Step 3b (classification (b) only): widen to the observed Codex prefix**

`<P>` and `<Pre>` are as defined in Task 6 Step 6b.
1. In `worker/hooks/hooks.json:133`, append `|<Pre>(mark_design_ready|mark_plan_ready|mark_brainstorming|mark_executing|mark_debugging|create_plan|start_execution|retreat)` inside the matcher string, after the existing closing `)`.
2. In `worker/hooks/mcp-state-logger.sh`, replace `  *state_manager__*|*state-manager__*) ;;` with `  *state_manager__*|*state-manager__*|"<P>"*) ;;`. If `<P>` does not end in `__`, apply the same `SHORT_TOOL` change that Task 6 Step 6b item 2 specifies.
3. In `worker/hooks/tests/test-mcp-state-logger-active-skill.sh`:
   - set `EXPECTED_PASSES=7`;
   - before the `Results:` line, add a case 7. It pipes `{session_id:$SESSION, hook_event_name:"PostToolUse", tool_name:"<P>mark_design_ready", tool_output:{changed:true}}` into `mcp-state-logger.sh` and asserts `$(skill)` = `NULL|0`. The row holds `systematic-debugging` after case 6, so this assertion fails if the arm is missing.

**Step 4: Run the tests**

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-mcp-state-logger-active-skill.sh
```
Expected: `Results: 6 pass, 0 fail`, exit 0, or `Results: 7 pass, 0 fail` under (b).

Run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-workflow-transition-idempotency.sh
```
Expected: last line `Results: N pass, 0 fail`, exit 0. N was not measured during planning.

**Step 5: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/mcp-state-logger.sh worker/hooks/tests/test-mcp-state-logger-active-skill.sh
```
Expected: no output, exit 0.

Under (b) only, also run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/hooks.json
```
Expected: no output, exit 0.

---

## Task 8: Full verification and redeploy (R7)

Depends on Tasks 1–7. This is the only redeploy in the loop.

**Files:**
- Create: `docs/plans/2026-10-03-client-parity-hooks-deploy-log.md`.
- Modify (outside the repo, not staged): `/Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json`. That is Claude's installed plugin copy; `~/.claude/plugins/installed_plugins.json:27` records `installPath` `/Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15`.
- `make deploy-hooks` and `codex plugin add` also write the runtime copies under `~/.claude/ironclaude-hooks`, `~/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks` and `~/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`. These are generated install output, not edits. The 2026-10-02 redeploy plan handled them the same way.

No new tests: this task runs the existing suites and deploys.

**Step 1: Run every hook suite**

Run:
```bash
make -C /Users/roberthyatt/Code/ironclaude test-hooks
```
Expected: exit 0. Every suite's header prints, including the six new ones under `worker/hooks/tests`. Pass counts were not measured during planning.

**Step 2: Run the Commander suite**

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: `0 failed`, exit 0. The count was not measured during planning.

**Step 3: Deploy hooks**

Run:
```bash
make -C /Users/roberthyatt/Code/ironclaude deploy-hooks
```
Expected output includes:
```
Deployed to plugin cache /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks
Deployed 31 hooks to /Users/roberthyatt/.claude/ironclaude-hooks
```
The count is 31: the 30 `*.sh` files at planning time plus `execution-drift-check.sh`. If Task 6 stopped on a (c) BLOCKER, the count is 30.

**Step 3b: Install `hooks.json` into Claude's plugin copy**

Run:
```bash
grep -n -e "installPath" /Users/roberthyatt/.claude/plugins/installed_plugins.json
```
Expected: the ironclaude entry shows `"installPath": "/Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15"`. That was measured at line 27 during planning. If it shows a different path, use that path's `hooks/hooks.json` in the next two commands.

Run:
```bash
cp /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json
```
Expected: no output, exit 0.

Run:
```bash
diff /Users/roberthyatt/Code/ironclaude/worker/hooks/hooks.json /Users/roberthyatt/.claude/plugins/cache/ironclaude/ironclaude/1.1.15/hooks/hooks.json
```
Expected: no output, exit 0. Claude loads the new `hooks.json` at its next session start.

**Step 4: Refresh the Codex plugin cache**

Run:
```bash
codex plugin add ironclaude@ironclaude --json
```
Expected: exit 0, with JSON naming `installedPath` `/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837`. That path was recorded in `docs/plans/2026-10-02-redeploy-local-main-log.md:13`.

**Step 5: Verify the Codex cache matches `worker/`**

Run:
```bash
diff -rq /Users/roberthyatt/Code/ironclaude/worker /Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/1.1.15+codex.20260930172837 -x node_modules -x __pycache__ -x .DS_Store
```
Expected: no output, exit 0. The diff prints `Files … differ` or `Only in …` if the refresh missed `hooks.json` or the new hook.

**Step 6: Write the deploy log**

Create `docs/plans/2026-10-03-client-parity-hooks-deploy-log.md` with the quoted output of Steps 1–5, including 3b: the suite summary lines, the deploy lines, the `installPath` line, the empty `hooks.json` diff, `installedPath` and the empty `-rq` diff. Add these operator notes:
- **Codex trust.** Codex keys trust by hook hash, and the hash scheme is unknown. These entries changed or are new:
  - the guard matcher (`pre_tool_use:3:0`) and the plan-task-context matcher (`pre_tool_use:2:0`) changed;
  - unless Task 6 stopped on a (c) BLOCKER, two entries are new: `pre_tool_use:6:0` and `post_tool_use:6:0`;
  - under classification (b), the mcp-state-logger matcher (`post_tool_use:4:0`) also changed.

  Check Settings > Hooks for untrusted badges and approve the changed and new entries.
- **Claude.** `make deploy-hooks` copies `*.sh` only. Step 3b installs `hooks.json` into Claude's plugin copy, and Claude loads it at the next session start. The script changes are live on Claude after Step 3.

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-03-client-parity-hooks-deploy-log.md
```
Expected: no output, exit 0.
