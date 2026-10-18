# Drift Check End-Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the two findings from the drift-check end review: skip unreadable windows (R3d), and close the main lineage's leftover windows at submit and Stop (R3c).

**Requirements:** `docs/plans/2026-10-15-drift-session-attribution-design.md` (§Requirements R3c, R3d)

**Architecture:**
- `_drift_close_window_file` reads the window file once, with no pipes. A failed read or an empty root line returns 1 and appends nothing.
- A new `ic_drift_settle_windows SESSION` closes the `main` lineage's windows, then checkpoints the rest.
- `submit_task` and `ic_drift_stop_violations` call it instead of `ic_drift_checkpoint_windows`.

**Tech Stack:** bash hook, bash test suite.

Loop: full — guard/hook enforcement code.

## Execution invariants

- Literal absolute paths. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- The hook runs under `set -euo pipefail` with an ERR trap. The new read uses parameter expansion, not `head`/`tail` pipes.
- `LC_ALL=C comm -23` must still appear exactly once (`test-execution-drift-roots.sh` case 5).
- Each suite exits non-zero unless its pass count equals `EXPECTED_PASSES`.
- `docs/` is gitignored, so stage docs with `git add -f`.

**Measured at planning time:**
- `test-execution-drift-check.sh` has `EXPECTED_PASSES=33`.
- Case 12 asserts `WIN_AT_SUBMIT = "1"` (lines 196-202).
- `ic_drift_checkpoint_windows` is called at `execution-drift-check.sh:368` (Stop) and `:498` (submit).
- `_drift_close_window_file` reads with `head`/`tail` at `:281-282`.

---

## Task 1: Settle main windows at submit/Stop and skip unreadable windows

TDD.

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh` (`:279-282` read; add `ic_drift_settle_windows` after `ic_drift_checkpoint_windows`; `:368`; `:498`)
- Modify: `worker/hooks/tests/test-execution-drift-check.sh` (case 12 at `:196-202`; new cases 34-36; `EXPECTED_PASSES=36`)
- Modify: `docs/reviews/2026-10-15-drift-session-attribution-findings.md` (append section)

### Step 1: Tests (RED)

1. In `worker/hooks/tests/test-execution-drift-check.sh`, set `EXPECTED_PASSES=36`.
2. Replace case 12 (the block from `# 12. Codex shape` through `rm -f "$REPO/codex-side.txt"`) with:
```bash
# 12. Codex shape: submit closes a main-lineage exec_command window that is still
#     open (R3c): its delta blocks, and the window is gone before its PostToolUse.
shell_event PreToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
printf 'x\n' > "$REPO/codex-side.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
WIN_AT_SUBMIT=$(open_windows)
shell_event PostToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - codex-side.txt" \
   && [ "$WIN_AT_SUBMIT" = "0" ] && [ "$(open_windows)" = "0" ]; then
  ok "12 submit closes an open main-lineage window and counts its delta"
else
  bad "12 submit closes an open main-lineage window (rc=$RC at_submit=$WIN_AT_SUBMIT after_post=$(open_windows): $ERR)"
fi
rm -f "$REPO/codex-side.txt"
```
3. Insert the following after case 33's `ok`/`bad` line, before `printf 'Results`:
```bash
# 34. A subagent window still open at submit is checkpointed and kept (R3c keeps
#     subagent tracking): its delta blocks and the window stays until its PostToolUse.
shell_event PreToolUse k1 "printf k > sub-k.txt" Bash agentK >/dev/null
printf 'k\n' > "$REPO/sub-k.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
WIN_SUB_KEPT=$(open_windows)
shell_event PostToolUse k1 "printf k > sub-k.txt" Bash agentK >/dev/null
if [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -qxF "  - sub-k.txt" \
   && [ "$WIN_SUB_KEPT" = "1" ] && [ "$(open_windows)" = "0" ]; then
  ok "34 submit checkpoints and keeps a subagent window"
else
  bad "34 submit checkpoints and keeps a subagent window (rc=$RC kept=$WIN_SUB_KEPT after_post=$(open_windows): $ERR)"
fi
rm -f "$REPO/sub-k.txt"

# 35-36. An unreadable window (missing file, or empty root line) appends nothing (R3d).
#        Run from the dirty test repo so an empty root would list its dirt.
R3D_LEDGER="$TMP_HOME/.claude/ironclaude-drift-touched-r3d.1.txt"
R3D_WIN="$TMP_HOME/.claude/ironclaude-drift-window-r3d.main.k.txt"
printf '\npre.txt\n' > "$R3D_WIN"
(cd "$REPO" && IC_DRIFT_LIB=1 HOME="$TMP_HOME" HOOK="$HOOK" R3D_WIN="$R3D_WIN" bash -c '
  source "$HOOK"
  _drift_in_progress_rows() { printf "1\n"; }
  _drift_close_window_file "$R3D_WIN" r3d delete
')
[ ! -s "$R3D_LEDGER" ] && ok "35 empty-root window appends nothing" || bad "35 empty-root window appends nothing ($(cat "$R3D_LEDGER"))"
rm -f "$R3D_LEDGER" "$R3D_WIN"
(cd "$REPO" && IC_DRIFT_LIB=1 HOME="$TMP_HOME" HOOK="$HOOK" R3D_WIN="$R3D_WIN" bash -c '
  source "$HOOK"
  _drift_in_progress_rows() { printf "1\n"; }
  _drift_close_window_file "$R3D_WIN" r3d delete
')
[ ! -s "$R3D_LEDGER" ] && ok "36 missing window file appends nothing" || bad "36 missing window file appends nothing ($(cat "$R3D_LEDGER"))"
```
4. RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/r3.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/r3.out
```
Expected: `check=1`. Record the measured FAIL labels verbatim. If the run exits 0, stop and report.

### Step 2: Hook (GREEN)

In `worker/hooks/execution-drift-check.sh`:

1. In `_drift_close_window_file`, replace the two lines
   `root=$(head -n 1 "$file")` and `before=$(tail -n +2 "$file")` with the block below. Also add `content` to the function's `local` list.
```bash
  # Read once, without pipes. A window removed mid-close, or with an empty root line,
  # would make `git -C ""` list the hook cwd's whole checkout: skip it (R3d).
  if content=$(cat "$file" 2>/dev/null); then :; else return 1; fi
  root="${content%%$'\n'*}"
  if [ -z "$root" ]; then return 1; fi
  if [ "$content" = "$root" ]; then before=""; else before="${content#*$'\n'}"; fi
```
2. After the closing `}` of `ic_drift_checkpoint_windows`, add:
```bash
# ic_drift_settle_windows SESSION: submit_task and Stop (R3c). The main lineage cannot
# have a foreground command running at that point, so its open windows are leftovers
# (a failed or denied command): close them. Subagent windows may still be running:
# checkpoint and keep them.
ic_drift_settle_windows() {
  ic_drift_close_lineage "$1" "main"
  ic_drift_checkpoint_windows "$1"
  return 0
}
```
3. In `ic_drift_stop_violations`, replace `ic_drift_checkpoint_windows "$1"` with `ic_drift_settle_windows "$1"`, and change its comment line "after checkpointing the session's open windows" to "after settling the session's open windows".
4. In the `submit_task)` dispatch branch, replace `ic_drift_checkpoint_windows "$SESSION_TAG"` with `ic_drift_settle_windows "$SESSION_TAG"`.
5. In the header, replace `-> checkpoint open windows, block (exit 2) on drift` with `-> settle open windows (close main, checkpoint subagents), block (exit 2) on drift`.

### Step 3: GREEN run plus all hook suites

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; echo "n=$?"; grep -c -F 'LC_ALL=C comm -23' /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/g3.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/g3.out; make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/th3.out 2>&1; echo "make=$?"
```
Expected: `n=0`, grep count `1`, `Results: 36 pass, 0 fail`, `check=0`, `make=0`.

### Step 4: Findings and stage

1. Append a section `## End-review fixes (R3c, R3d)` to `docs/reviews/2026-10-15-drift-session-attribution-findings.md` containing:
   - the two Fable end-review findings, in one line each;
   - the fixes;
   - the RED FAIL labels and the GREEN `Results` line;
   - the `make test-hooks` exit code;
   - the operator's acceptance of the roughly 2.5 s per mutating command.
2. Stage:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh; git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-15-drift-session-attribution-findings.md docs/plans/2026-10-15-drift-end-review-fixes.md docs/plans/2026-10-15-drift-end-review-fixes.plan.json docs/plans/2026-10-15-drift-session-attribution-design.md
```
Expected: no output.
