# v1.1.13 Findings — Remainder — Design

> **Created:** 2026-09-26
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-26-v1-1-13-findings-remainder-requirements.md
> **Parent design:** docs/plans/2026-09-26-v1-1-13-findings-design.md (items 2, 4, 5 carried unchanged)

## Summary

Lineage 132 executed and passed review on Tasks 1 (kill_worker guard), 2 (the classify resolved arm), 3 (grammar-bounded schemas), 4 (the 8192 token floor), 7 (the detached reap test and not-ready wording), 8 (package-lock) and 9 (the vitest findings note). All of that work is staged. Task 5 revealed that `test_daemon.py` also depends on the reaper repo list, through a fixture with a nonexistent local `/repo`. The operator retreated, and this design covers only what remains. The staged, reviewed work is not touched.

## Components

### 1. Reaper repo list, completed (`main.py`, `test_worktree_reaper.py`, `test_daemon.py`)

- `_managed_repositories` normalize/skip-vanished/dedupe is parent design item 2, unchanged. The implementation and the `test_worktree_reaper.py` changes (3 new tests, 4 adapted to real temporary directories) are already in the working tree, unstaged. The plan re-verifies them, RED then GREEN, before staging.
- **New:** `test_daemon.py` `TestRunMaintenance._commander_conn_with_repo(repo="/repo")` (`:2518`) seeds a nonexistent local path. Its three callers are:
  - `test_surfaces_preserved_unmerged_orphans_to_slack_and_counts_them`
  - `test_second_run_with_same_preserved_unmerged_names_does_not_repost`
  - `test_no_preserved_unmerged_orphans_leaves_count_zero_and_no_post`

  Each caller creates `tmp_path / "repo"` and passes `str(...)`. The posted-name assertions become `f"{repo}:ironclaude/y"` and `f"{repo}:ironclaude/z"`. This follows the operator's choice A: a real temporary directory, with no monkeypatching of `isdir`.

### 2. Terminal-failure once-gate and consecutive reset (`main.py`, `test_daemon.py`)

This is parent design items 4 and 5, unchanged. A new `self._finalize_failure_alerted` set is used only by the terminal-failure surface. It is pruned at the non-running sweep and at the new-marker re-arm. At the top of `_drive_finalization_recovery`, a terminal outcome that is not a counted failure pops `_finalize_failure_count`. A counted failure is a truthy `failure_phase` other than `"finalization"`.

### 3. Vitest `onTaskUpdate` bisect (investigation only)

- Run each of the 12 files under `src/__tests__/` with `npx vitest run <file>`, three runs per file. Keep one log per run in the scratchpad.
- If exactly one or a few files reproduce, narrow within them with `-t <describe/it name>` where the budget allows.
- Append to `commander/docs/plans/2026-09-26-v1-1-13-vitest-ontaskupdate-findings.md` a "Bisect" section: the per-file results table, quoted summary lines (`Test Files` / `Tests` / `Errors`), the culprit or culprits, or "not reproduced in isolation". That last result would itself point to a cross-file or concurrency cause.
- No product-code, test-code or config change. The fix is a follow-up PM loop.

### 4. Documentation and full suites (`CHANGELOG.md`, `README.md`)

- CHANGELOG `## 1.1.13`:
  - add `package-lock.json` to the versioning-note lockstep list;
  - retitle the section to include "plus v1.1.13 validation fixes";
  - reword `not-ready` so it no longer says "strictly an active row";
  - add one bullet per shipped item, naming its files: lineage-132 Tasks 1, 2, 3, 4, 7, 8, 9 and this remainder's reaper list and alert gate;
  - add one line on the vitest defect: root cause is a hardcoded birpc 60s timeout, culprit bisected, fix pending.
- README "What's New in v1.1.13" gets user-facing bullets for the kill_worker guard, the grader-truncation fix, and the reaper repo list, plus one line covering the classify resolved arm and the alert gate/reset.

## Testing Strategy

- Reaper: RED (the 3 new tests fail on the old body), then GREEN.
  - The `TestRunMaintenance` tests pass once the fixture points at a real directory.
  - Reverse check: temporarily point `_commander_conn_with_repo` at a nonexistent path and confirm the two posting tests fail. Then revert, all within one step.
- Alert gate and reset: the lineage-132 Task 6 tests and measured RED expectations. Five of the six new tests fail on the old code; `test_non_terminal_outcome_neither_counts_nor_resets` passes and is kept as a guard.
- Bisect: evidence only, one log per run.
- Final gates:
  - pytest: 0 failed.
  - Hook script: 0 failed.
  - vitest: `Tests … 0 failed`, and every `Errors` entry is the documented `onTaskUpdate` timeout. Any other error fails the gate.

## Implementation Notes

- This retreat inherits lineage 132's consumed blind review. The changed plan is validated by the fix advisor and recorded as `advisor-remediated`. There is no second blind review.
- Release handling is unchanged. After the loop, amend the unpushed v1.1.13 commit and move the tag (operator-gated). Redeploy to both 1.1.13 caches and restart Commander. Push only on an explicit go.
- The seam owns all completion. The daemon adds no `update_worker_status` calls.
