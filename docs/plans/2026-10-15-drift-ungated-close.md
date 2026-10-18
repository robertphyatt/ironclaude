# Drift Check Ungated Window Close Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Close drift windows on shell PostToolUse and same-lineage PreToolUse without waiting on the workflow-stage gate, tool routing or root resolution (R3e), so a leftover window never survives a stage change.

**Requirements:** `docs/plans/2026-10-15-drift-session-attribution-design.md` (§Requirements R3e)

**Architecture:** In `execution-drift-check.sh` dispatch, the first `case "$TOOL_NAME"` (current `:449-456`) handles `Bash|exec_command` as follows:
- read the key and lineage;
- skip events that have no `tool_use_id`;
- close the command's window on PostToolUse;
- close the lineage's leftovers on PreToolUse;

It does all of this right after the professional-mode and SubagentStop sections, before the stage gate (`:458`) and `_drift_effective_root` (`:472`). The later `shell)` branch keeps only the open path.

**Tech Stack:** bash hook, bash test suite.

Loop: full — guard/hook enforcement code.

## Execution invariants

- Literal absolute paths. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- The hook runs under `set -euo pipefail` with an ERR trap. The moved code keeps its `|| true` and `if` forms.
- `LC_ALL=C comm -23` must appear exactly once.
- Each suite exits non-zero unless its pass count equals `EXPECTED_PASSES`.
- `docs/` needs `git add -f`.

**Measured at planning time:**
- `test-execution-drift-check.sh` has `EXPECTED_PASSES=36`.
- The dispatch lines cited are from the current file: first case at `:449-456`, stage gate `:458-462`, root `:472-475`, shell branch key/lineage/close at `:479-493`.

---

## Task 1: Run window closes before the stage gate and root resolution

TDD.

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh` (`:449-456`, `:479-493`)
- Modify: `worker/hooks/tests/test-execution-drift-check.sh` (cases 37-38; `EXPECTED_PASSES=38`)
- Modify: `docs/reviews/2026-10-15-drift-session-attribution-findings.md` (append section)

### Step 1: Tests (RED)

1. Set `EXPECTED_PASSES=38`.
2. Insert before `printf 'Results`:
```bash
# 37. A leftover main window is closed by the next shell PreToolUse even outside
#     executing (R3e), so writes after a retreat are never attributed later.
shell_event PreToolUse s37 "touch s37.txt" >/dev/null
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
shell_event PreToolUse r37 "cat pre.txt" >/dev/null
WIN_AFTER_STAGE_PRE=$(open_windows)
printf 'later\n' > "$REPO/late-r3e.txt"
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='${SESSION}';"
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$WIN_AFTER_STAGE_PRE" = "0" ]; then
  ok "37 leftover window closes at the next shell PreToolUse outside executing"
else
  bad "37 leftover window closes at the next shell PreToolUse outside executing (rc=$RC windows=$WIN_AFTER_STAGE_PRE: $(cat "$TMP_ROOT/err"))"
fi

# 38. A command's own PostToolUse closes its window outside executing (R3e).
shell_event PreToolUse p38 "touch p38.txt" >/dev/null
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='brainstorming' WHERE terminal_session='${SESSION}';"
shell_event PostToolUse p38 "touch p38.txt" >/dev/null
WIN_AFTER_STAGE_POST=$(open_windows)
sqlite3 "$DB" "UPDATE sessions SET workflow_stage='executing' WHERE terminal_session='${SESSION}';"
[ "$WIN_AFTER_STAGE_POST" = "0" ] && ok "38 PostToolUse closes its window outside executing" || bad "38 PostToolUse closes its window outside executing (windows=$WIN_AFTER_STAGE_POST)"
```
3. RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/r4.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/r4.out
```
Expected: `check=1`. Record the measured FAIL labels verbatim. If it exits 0, stop and report.

### Step 2: Hook (GREEN)

1. Replace the first `case "$TOOL_NAME" in … esac` (`:449-456`) with:
```bash
case "$TOOL_NAME" in
  Bash|exec_command)
    SHORT_TOOL="shell"
    WINDOW_KEY=$(printf '%s' "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null || true)
    LINEAGE=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || true)
    [ -n "$LINEAGE" ] || LINEAGE="main"
    if [ -z "$WINDOW_KEY" ]; then
      log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "shell event without tool_use_id"
      exit 0
    fi
    # Closing needs neither the workflow stage nor the effective root (line 1 of the
    # window file), so it runs before both gates (R3e): a window never outlives its
    # command across a retreat, a stage change or a failed root lookup.
    if [ "$HOOK_EVENT" = "PostToolUse" ]; then
      ic_drift_close_window "$SESSION_TAG" "$LINEAGE" "$WINDOW_KEY"
      log_hook "EXECUTION-DRIFT-CHECK" "Window" "closed ${LINEAGE}/${WINDOW_KEY}"
      exit 0
    fi
    # This lineage's windows still open here belong to its earlier commands that had
    # no PostToolUse (denied, or a failed Claude Bash). Other lineages are untouched.
    ic_drift_close_lineage "$SESSION_TAG" "$LINEAGE"
    ;;
  *state_manager__*|*state-manager__*) SHORT_TOOL="${TOOL_NAME##*__}" ;;
  *)
    log_hook "EXECUTION-DRIFT-CHECK" "Skipped" "not a drift tool: ${TOOL_NAME:-none}"
    exit 0
    ;;
esac
```
2. In the later `shell)` branch, delete the lines from `WINDOW_KEY=$(printf '%s' "$INPUT" | jq -r '.tool_use_id // empty' …` through `ic_drift_close_lineage "$SESSION_TAG" "$LINEAGE"`, including its two-line comment (current `:479-493`). The branch then starts at `if [ -z "$(_drift_in_progress_rows "$SESSION_TAG")" ]; then`.

### Step 3: GREEN plus all hook suites

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; echo "n=$?"; grep -c -F 'LC_ALL=C comm -23' /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; grep -c -F 'ic_drift_close_lineage "$SESSION_TAG" "$LINEAGE"' /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/g4.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/g4.out; make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/th4.out 2>&1; echo "make=$?"
```
Expected: `n=0`, count `1`, count `1` (the leftover close now appears once, in the first case), `Results: 38 pass, 0 fail`, `check=0`, `make=0`.

### Step 4: Findings and stage

1. Append `## End-review #2 fix (R3e)` to the findings note. It contains:
   - the finding, in one line;
   - the fix;
   - the RED FAIL labels and the GREEN `Results` line;
   - `make` exit code;
   - the recorded accepted gap: this session's own edit to an already-dirty file never enters the ledger.
2. Stage:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh; git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-15-drift-session-attribution-findings.md docs/plans/2026-10-15-drift-ungated-close.md docs/plans/2026-10-15-drift-ungated-close.plan.json docs/plans/2026-10-15-drift-session-attribution-design.md
```
Expected: no output.
