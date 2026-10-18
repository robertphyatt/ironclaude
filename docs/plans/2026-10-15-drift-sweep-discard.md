# Drift Check Sweep-Discard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make every sweep of a leftover drift window discard its delta (R3f), so writes made after a failed or denied command, including other sessions' writes, are never charged to this session.

**Requirements:** `docs/plans/2026-10-15-drift-session-attribution-design.md` (§Requirements R3f)

**Architecture:** `ic_drift_close_lineage` is the only sweep primitive. It is called at the same lineage's next shell PreToolUse (`:469`), at SubagentStop (`:441`) and by settle-main (`:348`). It now deletes each matching window file without reading it or appending anything. The command's own PostToolUse close (`ic_drift_close_window`, delete mode) and the subagent checkpoint at submit/Stop are unchanged.

**Tech Stack:** bash hook, bash test suite.

Loop: full — guard/hook enforcement code.

## Execution invariants

- Literal absolute paths. Use `git -C /Users/roberthyatt/Code/ironclaude`.
- The hook runs under `set -euo pipefail` with an ERR trap. Keep the `if [ -f ]` form.
- `LC_ALL=C comm -23` must appear exactly once.
- Each suite exits non-zero unless its pass count equals `EXPECTED_PASSES`.
- `docs/` needs `git add -f`.

**Measured at planning time:**
- `EXPECTED_PASSES=38`, at test line 26.
- Case 12 is at test lines 190-204 and expects rc=2 with `codex-side.txt` listed.
- `printf 'Results` is at test line 478.
- `ic_drift_close_lineage` is at hook lines 323-331, and its loop body at line 328 calls `_drift_close_window_file "$f" "$1" delete`.

---

## Task 1: Sweeps discard leftover windows

TDD.

**Files:**
- Modify: `worker/hooks/execution-drift-check.sh` (`:323-331`, plus the header known-gaps comment)
- Modify: `worker/hooks/tests/test-execution-drift-check.sh` (case 12; new cases 39-40; `EXPECTED_PASSES=40`)
- Modify: `docs/reviews/2026-10-15-drift-session-attribution-findings.md` (relabel the gap and append a section)

### Step 1: Tests (RED)

1. Set `EXPECTED_PASSES=40`.
2. Replace case 12 (from `# 12. Codex shape` through the `fi` that precedes `rm -f "$REPO/codex-side.txt"`) with:
```bash
# 12. Codex shape: a main-lineage window still open at submit is a leftover; settle
#     discards it (R3c + R3f), so its delta is not counted and the window is gone.
shell_event PreToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
printf 'x\n' > "$REPO/codex-side.txt"
RC=$(invoke "$SUBMIT" "PreToolUse")
ERR=$(cat "$TMP_ROOT/err")
WIN_AT_SUBMIT=$(open_windows)
shell_event PostToolUse c1 "printf x > codex-side.txt" exec_command >/dev/null
if [ "$RC" = "0" ] && [ "$WIN_AT_SUBMIT" = "0" ] && [ "$(open_windows)" = "0" ] \
   && ! grep -qxF "codex-side.txt" "$LEDGER"; then
  ok "12 submit discards a leftover main-lineage window"
else
  bad "12 submit discards a leftover main-lineage window (rc=$RC at_submit=$WIN_AT_SUBMIT after_post=$(open_windows): $ERR)"
fi
```
   Keep the existing `rm -f "$REPO/codex-side.txt"` line that follows it.
3. Insert before `printf 'Results`:
```bash
# 39. A write made between a leftover window and its lineage sweep (another session's,
#     after the denied/failed command ended) is discarded, not charged (R3f).
shell_event PreToolUse f39 "touch f39.txt" >/dev/null
printf 'gap\n' > "$REPO/gap-f39.txt"
shell_event PreToolUse r39 "cat pre.txt" >/dev/null
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ] && ! grep -qxF "gap-f39.txt" "$LEDGER"; then
  ok "39 lineage sweep discards a leftover window's delta"
else
  bad "39 lineage sweep discards a leftover window's delta (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi

# 40. Same at SubagentStop: a finished subagent's leftover window is discarded (R3f).
shell_event PreToolUse g40 "touch g40.txt" Bash agentG >/dev/null
printf 'gap\n' > "$REPO/gap-g40.txt"
subagent_stop agentG
RC=$(invoke "$SUBMIT" "PreToolUse")
if [ "$RC" = "0" ] && [ "$(open_windows)" = "0" ] && ! grep -qxF "gap-g40.txt" "$LEDGER"; then
  ok "40 SubagentStop discards a leftover window's delta"
else
  bad "40 SubagentStop discards a leftover window's delta (rc=$RC windows=$(open_windows): $(cat "$TMP_ROOT/err"))"
fi
```
4. RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/r5.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/r5.out
```
Expected: `check=1`. Record the measured FAIL labels verbatim. If it exits 0, stop and report.

### Step 2: Hook (GREEN)

1. Replace `ic_drift_close_lineage` (`:323-331`, its comment included) with:
```bash
# ic_drift_close_lineage SESSION LINEAGE: sweep one agent lineage's leftover windows
# (R3b next PreToolUse, SubagentStop, R3c settle-main). A leftover belongs to a command
# that failed or was denied; any change seen now may be another session's write made
# after it ended, so the window is discarded without appending (R3f). Other
# lineages' windows are never touched.
ic_drift_close_lineage() {
  local f
  for f in "$HOME/.claude/ironclaude-drift-window-$(_drift_safe "$1").$(_drift_safe "$2")".*.txt; do
    if [ -f "$f" ]; then rm -f "$f"; fi
  done
  return 0
}
```
2. In the header's "Known gaps" comment, after the sentence ending `same lineage's next shell PreToolUse.`, add: `Sweeps (next same-lineage PreToolUse, SubagentStop, settle-main at submit/Stop) discard the leftover's delta (R3f), so a failed or denied command's own writes are not counted.`

### Step 3: GREEN plus all hook suites

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; echo "n=$?"; grep -c -F 'LC_ALL=C comm -23' /Users/roberthyatt/Code/ironclaude/worker/hooks/execution-drift-check.sh; bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-execution-drift-check.sh > /private/tmp/claude-502/g5.out; echo "check=$?"; grep -e '^FAIL' -e '^Results' /private/tmp/claude-502/g5.out; make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/th5.out 2>&1; echo "make=$?"
```
Expected: `n=0`, count `1`, `Results: 40 pass, 0 fail`, `check=0`, `make=0`.

### Step 4: Findings and stage

1. In `docs/reviews/2026-10-15-drift-session-attribution-findings.md`:
   - Replace the "Failed Claude Bash calls ..." gap bullet under "Accepted gaps (fail open)" with: `Failed or denied commands (Claude fires PostToolUseFailure, which is not registered; denied commands fire nothing): their leftover windows are swept (next same-lineage PreToolUse, SubagentStop, settle-main at submit/Stop) and the delta is discarded (R3f), so their own off-plan writes are not counted.`
   - Append `## End-review #3 fix (R3f)` containing:
     - the finding, in one line;
     - the operator decision ("Discard on sweep");
     - the fix;
     - the RED FAIL labels and the GREEN `Results` line;
     - `make` exit code.
2. Stage:
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/hooks/execution-drift-check.sh worker/hooks/tests/test-execution-drift-check.sh; git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-15-drift-session-attribution-findings.md docs/plans/2026-10-15-drift-sweep-discard.md docs/plans/2026-10-15-drift-sweep-discard.plan.json docs/plans/2026-10-15-drift-session-attribution-design.md
```
Expected: no output.
