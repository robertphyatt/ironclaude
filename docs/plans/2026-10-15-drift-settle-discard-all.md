# Drift Check Settle-Discard-All Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** At `submit_task` and Stop, discard every open window of the session, main and subagent, without appending (R3g). Remove the now-dead checkpoint mode.

**Requirements:** `docs/plans/2026-10-15-drift-session-attribution-design.md` (§Requirements R3g)

**Architecture:**
- `ic_drift_settle_windows` deletes every `ironclaude-drift-window-<session>.*.txt`.
- `ic_drift_checkpoint_windows` and the checkpoint branch of `_drift_close_window_file` are removed. That function becomes the single append-and-delete path used by a command's own PostToolUse.
- `ic_drift_stop_violations` and the `submit_task` branch already call `ic_drift_settle_windows`, so neither changes.

**Tech Stack:** bash hook, bash test suite.

Loop: full — guard/hook enforcement code.

## Execution invariants

- Literal absolute paths. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- The hook runs under `set -euo pipefail` with an ERR trap. Keep the `if [ -f ]` and `if …; then :; else` forms.
- `LC_ALL=C comm -23` must appear exactly once.
- Each suite exits non-zero unless its pass count equals `EXPECTED_PASSES`.
- `docs/` needs `git add -f`.

**Measured at planning time:**

| What | Where |
|---|---|
| `EXPECTED_PASSES=40` | test `:26` |
| Case 33 (checkpoint race lib test) | `:409-418` |
| Case 34 (subagent checkpoint kept) | `:420-434` |
| `printf 'Results` | `:501` |
| `_drift_close_window_file` | hook `:275-313`, checkpoint branch `:300-311` |
| `ic_drift_close_window` call with `delete` | `:319` |
| `ic_drift_checkpoint_windows` | `:336-344` |
| `ic_drift_settle_windows` | `:346-354` |
| Header lines mentioning checkpoint | `:11` and `:24` |
| SubagentStop comment ("be checkpointed at every later submit/Stop") | `:439` |

---

## Task 1: Settle discards every open window

TDD.

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh` (`:11`, `:22-26` sentence, `:275-313`, `:319`, `:336-354`, `:439`)
- Modify: `worker/hooks/tests/test-execution-drift-check.sh` (cases 33-34 replaced; case 41 added; `EXPECTED_PASSES=41`)
- Modify: `docs/reviews/2026-10-15-drift-session-attribution-findings.md` (append section)

### Step 1: Tests (RED)

1. Set `EXPECTED_PASSES=41`.
2. Replace case 33 (from `# 33. A checkpoint never recreates` through its `ok`/`bad` line) with:
```bash
# 33. A subagent's leftover window (its command failed or was denied) is discarded at
#     submit (R3g): a later foreign write is never charged.
shell_event PreToolUse q33 "touch q33.txt" Bash agentQ >/dev/null
printf 'gap\n' > "$REPO/gap-q33.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ] && ! grep -qxF "gap-q33.txt" "$LEDGER"; then
  ok "33 submit discards a subagent's leftover window"
else
  bad "33 submit discards a subagent's leftover window (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi
```
3. Replace case 34 (from `# 34. A subagent window still open at submit` through `rm -f "$REPO/sub-k.txt"`) with:
```bash
# 34. Stop discards an open subagent window without counting it (R3g): no drift block,
#     window gone, and its later PostToolUse finds nothing to close.
shell_event PreToolUse k1 "printf k > sub-k.txt" Bash agentK >/dev/null
printf 'k\n' > "$REPO/sub-k.txt"
rm -f "$THROTTLE"
STOP_OUT34=$(printf '%s' "$STOP_IN" | env -u ENABLE_STOP_REVIEW -u CLAUDE_HOOK_JUDGE_MODE HOME="$TMP_HOME" bash "$HOOKS_DIR/get-back-to-work-impl.sh" 2>/dev/null)
WIN_AFTER_STOP34=$(open_windows)
shell_event PostToolUse k1 "printf k > sub-k.txt" Bash agentK >/dev/null
if ! printf '%s' "$STOP_OUT34" | grep -qF "BLOCKED — EXECUTION DRIFT" \
   && [ "$WIN_AFTER_STOP34" = "0" ] && [ "$(open_windows)" = "0" ] \
   && ! grep -qxF "sub-k.txt" "$LEDGER"; then
  ok "34 Stop discards an open subagent window"
else
  bad "34 Stop discards an open subagent window (windows=$WIN_AFTER_STOP34 out: $STOP_OUT34)"
fi
rm -f "$REPO/sub-k.txt"
```
4. Insert before `printf 'Results`:
```bash
# 41. hooks.json registers the drift hook for SubagentStop exactly once.
SUBSTOP_N=$(jq '[.hooks.SubagentStop[] | select(.hooks[0].command | contains("execution-drift-check.sh"))] | length' "$HOOKS_DIR/hooks.json")
[ "$SUBSTOP_N" = "1" ] && ok "41 SubagentStop drift registration present once" || bad "41 SubagentStop drift registration present once (n=$SUBSTOP_N)"
```
5. RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/r6.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/r6.out
```
Expected: `check=1`. Record the measured FAIL labels verbatim. If it exits 0, stop and report.

### Step 2: Hook (GREEN)

1. Replace `_drift_close_window_file` (its comment and body, `:275-313`) with:
```bash
# _drift_close_window_file FILE SESSION
# A command's own PostToolUse close: append the window's delta (paths dirty now that
# were not dirty at its snapshot) to each in-progress row's ledger, then remove it.
_drift_close_window_file() {
  local file="$1" session="$2" root before now delta row status content
  # Read once, without pipes. A window removed mid-close, or with an empty root line,
  # would make `git -C ""` list the hook cwd's whole checkout: skip it (R3d).
  if content=$(cat "$file" 2>/dev/null); then :; else return 1; fi
  root="${content%%$'\n'*}"
  if [ -z "$root" ]; then return 1; fi
  if [ "$content" = "$root" ]; then before=""; else before="${content#*$'\n'}"; fi
  if now=$(_drift_dirty_paths "$root"); then :; else
    status=$?
    rm -f "$file"
    return "$status"
  fi
  delta=$(LC_ALL=C comm -13 \
    <(printf '%s\n' "$before" | sed '/^$/d' | LC_ALL=C sort -u) \
    <(printf '%s\n' "$now" | sed '/^$/d' | LC_ALL=C sort -u))
  if [ -n "$delta" ]; then
    for row in $(_drift_in_progress_rows "$session"); do
      printf '%s\n' "$delta" >> "$(_drift_ledger_file "$session" "$row")"
    done
  fi
  rm -f "$file"
  return 0
}
```
2. In `ic_drift_close_window`, change `_drift_close_window_file "$f" "$1" delete || true` to `_drift_close_window_file "$f" "$1" || true`.
3. Replace `ic_drift_checkpoint_windows` and `ic_drift_settle_windows` (`:336-354`, both with their comments) with:
```bash
# ic_drift_settle_windows SESSION: submit_task and Stop (R3g). A window still open then
# cannot be told apart from a failed or denied command's leftover, and any change seen
# now may be another session's write, so every open window of SESSION (main and
# subagent) is discarded without appending.
ic_drift_settle_windows() {
  local f
  for f in "$HOME/.claude/ironclaude-drift-window-$(_drift_safe "$1")".*.txt; do
    if [ -f "$f" ]; then rm -f "$f"; fi
  done
  return 0
}
```
4. In the header at `:11`, replace `settle open windows (close main, checkpoint subagents)` with `settle (discard) all open windows`.
5. In the header attribution paragraph, replace the sentence that begins `submit_task and Stop checkpoint open windows` (it runs through `so running commands stay tracked.`) with `submit_task and Stop discard all open windows (R3g).`
6. In the SubagentStop comment (around `:439`), replace `would otherwise` + `# be checkpointed at every later submit/Stop and absorb other sessions' writes.` with `would otherwise` + `# linger until the next submit/Stop.`

### Step 3: GREEN plus all hook suites

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; echo "n=$?"; grep -c -F 'LC_ALL=C comm -23' /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; grep -c -e checkpoint /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/g6.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/g6.out; make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/th6.out 2>&1; echo "make=$?"
```
Expected: `n=0`, count `1`, checkpoint count `0`, `Results: 41 pass, 0 fail`, `check=0`, `make=0`.

### Step 4: Findings and stage

1. Append `## End-review #4 fix (R3g)` to the findings note. It contains:
   - the two findings, one line each;
   - the operator decision ("Discard all at submit/Stop");
   - the fix, including the checkpoint mode removal;
   - the RED FAIL labels and the GREEN `Results` line;
   - `make` exit code;
   - the new accepted gap (a subagent command still running at the main session's submit or Stop loses its changes);
   - the post-deploy live check for `agent_id` on subagent PreToolUse, PostToolUse and SubagentStop. A missing `agent_id` is fail-open.
2. Stage:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh; git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-15-drift-session-attribution-findings.md docs/plans/2026-10-15-drift-settle-discard-all.md docs/plans/2026-10-15-drift-settle-discard-all.plan.json docs/plans/2026-10-15-drift-session-attribution-design.md
```
Expected: no output.
