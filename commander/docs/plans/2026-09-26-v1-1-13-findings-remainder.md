# v1.1.13 Findings — Remainder Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Finish the v1.1.13 findings work: complete the reaper repo list (with the `test_daemon.py` fixture adaptation), add the separate terminal-failure gate and the consecutive reset, bisect the vitest `onTaskUpdate` culprit, and update docs and run the full suites.

**Requirements:** docs/plans/2026-09-26-v1-1-13-findings-remainder-requirements.md

**Design:** docs/plans/2026-09-26-v1-1-13-findings-remainder-design.md

**Architecture:** Only the unfinished items are in scope. The reviewed, staged work from lineage 132 is not touched: `orchestrator_mcp.py`, `grader.py`, `shadow_grader.py`, `plan-validator.sh`, the Task 3 schema edits in `main.py`, the TS test and comment, `package-lock.json`, and the vitest findings note. The seam keeps sole ownership of worker completion.

**Tech stack:** Python 3.11 with pytest, TypeScript with vitest, and bash hook tests.

## Execution invariants (every command below satisfies these)

- **Shell state does not persist between steps.** Every command uses absolute paths or its own `cd` prefix.
- **Bash cwd is `commander/`.** Repository-root operations use `git -C /Users/roberthyatt/Code/ironclaude`.
- **zsh `nomatch`.** Globs are quoted.
- **Each evidence log is written once.** Every run gets its own log file, and the logs are never deleted.
- **`docs/` is gitignored.** Docs use `git add -f`.
- **Every pytest run uses `PYTHONUNBUFFERED=1`.**
- **Anchors are content, not line numbers.** Line numbers in `main.py` shifted after the lineage-132 edits. Every insertion point is named by the exact code it follows.
- **No commits.** Changes are staged only.
- **Never `git stash`.** A stash pop can silently drop staged content. The staged lineage-132 work must survive.

## Current working-tree state (verified at plan time)

- `commander/src/ironclaude/main.py` is `MM`:
  - Staged: Task 3's schema bounds.
  - Unstaged: the new `_managed_repositories` body. Marker normalization, skipping vanished local paths with `os.path.isdir`, dedupe by `git rev-parse --path-format=absolute --git-common-dir`, and the docstring sentence "Local paths are normalized (worktree → primary), vanished ones skipped, and duplicates of one repository (same git common dir) collapsed to the first seen."
- `commander/tests/test_worktree_reaper.py` is ` M` (unstaged): 3 new tests plus 4 existing tests adapted to real `tmp_path` directories.
- `commander/tests/test_daemon.py` is unmodified.

---

## Task 1: Complete the reaper repo list, including the `test_daemon.py` fixture

**Files:**
- Modify: `commander/tests/test_daemon.py` (`TestRunMaintenance._commander_conn_with_repo` and its three callers)
- Verify and stage (already edited): `commander/src/ironclaude/main.py` (`_managed_repositories`), `commander/tests/test_worktree_reaper.py`

**Step 1: confirm the reaper tests are green with the working-tree implementation.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worktree_reaper.py -k "ManagedRepositories or ReapRowLessOrphans"
```

Expected: 7 passed. The RED for these three new tests was observed during lineage-132 execution: 3 failed and 4 passed against the old body. Step 5 below re-proves falsifiability for the daemon path.

**Step 2 (RED): the daemon fixture gap.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py -k TestRunMaintenance
```

Expected: 2 failed, 6 passed. The failures are:
- `test_surfaces_preserved_unmerged_orphans_to_slack_and_counts_them`
- `test_second_run_with_same_preserved_unmerged_names_does_not_repost`

This count was measured in lineage 132; confirm it here. Record the exact failing names. The fixture seeds the nonexistent local `/repo`, which the new code skips.

**Step 3 (GREEN): point the fixture at a real directory.** In `test_daemon.py` class `TestRunMaintenance`:

(a) Change the helper signature from `def _commander_conn_with_repo(repo="/repo"):` to `def _commander_conn_with_repo(repo):`. It then has no default: every caller must pass a real directory.

(b) In each of the three callers, replace `daemon._db = self._commander_conn_with_repo()` with:

```python
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        repo = str(repo_dir)
        daemon._db = self._commander_conn_with_repo(repo)
```

The three callers are `test_surfaces_preserved_unmerged_orphans_to_slack_and_counts_them`, `test_second_run_with_same_preserved_unmerged_names_does_not_repost` and `test_no_preserved_unmerged_orphans_leaves_count_zero_and_no_post`. All three already take `tmp_path`.

(c) In `test_surfaces_preserved_unmerged_orphans_to_slack_and_counts_them`, replace:

```python
        assert "/repo:ironclaude/y" in posted
        assert "/repo:ironclaude/z" in posted
```

with:

```python
        assert f"{repo}:ironclaude/y" in posted
        assert f"{repo}:ironclaude/z" in posted
```

(d) In each of the three callers, add this directly after the `daemon._run_maintenance()` call. In the second test, add it after the first `_run_maintenance()`, before the `reset_mock()`. This makes every test prove it reached the reaper:

```python
        orch._workspace_client.reap_orphans.assert_called()
        assert orch._workspace_client.reap_orphans.call_args.args[0]["repository_path"] == repo
```

Run the Step 2 command again. Expected: every `TestRunMaintenance` test passes, 0 failed.

**Step 4: reverse check, proving the tests exercise the skip-vanished path.** Temporarily change one caller, `test_no_preserved_unmerged_orphans_leaves_count_zero_and_no_post`: replace `repo_dir.mkdir()` with `pass  # REVERSE-CHECK`, so the directory is never created.

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py -k test_no_preserved_unmerged_orphans_leaves_count_zero_and_no_post
```

Expected: 1 failed on the `reap_orphans.assert_called()` line. The vanished path is skipped, so the reaper is never called.

Then restore `repo_dir.mkdir()` and confirm the marker is gone:

```bash
rg -n -F "REVERSE-CHECK" /Users/roberthyatt/Code/ironclaude/commander/tests/test_daemon.py
```

Expected: no output.

Requirements criterion 2 names two pieces of evidence. The first is that a fixture pointing at a nonexistent path makes the two posting tests fail; Step 2 above provides it. This step extends that evidence to the third test, which previously passed vacuously.

**Step 5: regression.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worktree_reaper.py tests/test_daemon.py
```

Expected: 0 failed.

**Step 6: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_worktree_reaper.py commander/tests/test_daemon.py
```

---

## Task 2: Separate terminal-failure once-gate, and a real "consecutive" reset

Depends on Task 1, because both edit `main.py` and `test_daemon.py`.

**Files:**
- Modify: `commander/src/ironclaude/main.py`. The edits go in the daemon `__init__`, `_drive_finalization_recovery`, the non-running sweep in `check_stuck_workers`, and the marker re-arm in `check_workers`.
- Test: `commander/tests/test_daemon.py` (class `TestTerminalFinalizeFailureSurface`)

**Step 1 (RED).** Append these tests to `TestTerminalFinalizeFailureSurface`, verbatim:

```python
    def test_prior_recovery_alert_does_not_suppress_terminal_failure_surface(self, daemon):
        # A conflict/drift/no-mode alert earlier in the worker's life must not
        # swallow the later terminal-failure surface (separate once-gates).
        daemon._finalize_recovery_alerted.add("w1")
        for _ in range(FINALIZE_FAILURE_SURFACE_CAP + 1):
            daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        assert daemon.slack.post_message.call_count == 1
        assert "consecutive cycles" in daemon.slack.post_message.call_args[0][0]
        assert "w1" in daemon._finalize_failure_alerted

    def test_terminal_failure_surface_does_not_suppress_conflict_surface(self, daemon):
        for _ in range(FINALIZE_FAILURE_SURFACE_CAP + 1):
            daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        conflict = {
            "failure_phase": "finalization",
            "recovery": {"reconcile": {"mode": "conflict"}},
        }
        assert daemon._drive_finalization_recovery("w1", conflict, terminal=True) == "surfaced"
        assert daemon.slack.post_message.call_count == 2

    @pytest.mark.parametrize("interleaved", [
        None,
        {"state": "integrated"},
        {"failure_phase": "finalization", "recovery": {"reconcile": {"mode": "drift"}}},
    ])
    def test_non_counted_terminal_outcome_resets_consecutive_count(self, daemon, interleaved):
        daemon._get_orchestrator = MagicMock(return_value=None)  # drift arm: no seam call
        for _ in range(FINALIZE_FAILURE_SURFACE_CAP):
            daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        daemon._drive_finalization_recovery("w1", interleaved, terminal=True)
        assert "w1" not in daemon._finalize_failure_count
        daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        assert daemon._finalize_failure_count["w1"] == 1
        assert daemon.slack.post_message.call_count == 0

    def test_non_terminal_outcome_neither_counts_nor_resets(self, daemon):
        for _ in range(2):
            daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        daemon._drive_finalization_recovery("w1", None)  # idle, non-terminal
        assert daemon._finalize_failure_count["w1"] == 2

    def test_non_running_sweep_clears_failure_alerted(self, daemon):
        daemon._finalize_failure_alerted.add("w1")
        daemon.registry.get_running_workers.return_value = []
        daemon._last_stuck_check = 0
        daemon.check_stuck_workers()
        assert "w1" not in daemon._finalize_failure_alerted

    def test_new_marker_rearms_failure_alerted(self, daemon):
        _live_worker(daemon)
        daemon.registry.get_events_for_worker.return_value = [
            {"id": 4, "event_type": "finalize_integrated", "worker_id": "w1"},
        ]
        daemon._finalize_failure_alerted.add("w1")
        daemon.check_workers()
        assert "w1" not in daemon._finalize_failure_alerted
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py -k TestTerminalFinalizeFailureSurface
```

Expected (predicted, not yet measured): `7 failed, 9 passed`, because the parametrized test contributes three items. The new tests that fail:
- `test_prior_recovery_alert_does_not_suppress_terminal_failure_surface`: `post_message` count is 0, because the shared gate suppresses the surface today.
- `test_terminal_failure_surface_does_not_suppress_conflict_surface`: the count is 1, not 2.
- `test_non_counted_terminal_outcome_resets_consecutive_count`: all three parameters fail, because nothing resets the count today.
- `test_non_running_sweep_clears_failure_alerted`: `AttributeError` on `_finalize_failure_alerted`.
- `test_new_marker_rearms_failure_alerted`: `AttributeError` on `_finalize_failure_alerted`.

`test_non_terminal_outcome_neither_counts_nor_resets` passes against the current code, because the terminal counter block already skips non-terminal outcomes. It is kept as a regression guard: it fails if the new reset drops its `terminal and` condition. The existing tests pass.

**Step 2 (GREEN).** In `main.py`:

(a) Init: directly after the line `self._finalize_recovery_alerted: set[str] = set()`, add:

```python
        # Separate once-per-worker gate for the terminal-failure surface
        # (FINALIZE_FAILURE_SURFACE_CAP), so an earlier drift/conflict/no-mode
        # alert never swallows it and vice versa. Pruned with its counter.
        self._finalize_failure_alerted: set[str] = set()
```

(b) Driver: in `_drive_finalization_recovery`, directly before the line `mode = self._finalization_recovery_mode(outcome)`, add:

```python
        phase = outcome.get("failure_phase") if isinstance(outcome, dict) else None
        counted_failure = bool(phase) and phase != "finalization"
        if terminal and not counted_failure:
            # 'Consecutive' means consecutive: any terminal outcome that is not
            # a counted (non-finalization) failure breaks the streak.
            self._finalize_failure_count.pop(worker_id, None)
```

In the terminal counter block near the end of the same function:
- Delete the now-duplicate line `phase = outcome.get("failure_phase") if isinstance(outcome, dict) else None`.
- Change `if terminal and phase and phase != "finalization":` to `if terminal and counted_failure:`.
- Change both uses of `self._finalize_recovery_alerted` inside that block, the `not in` check and the `.add(...)`, to `self._finalize_failure_alerted`.

In the docstring's `'transient'` entry, after "surfaced once (never completed)", append "via its own _finalize_failure_alerted gate; any other terminal outcome resets the count".

(c) Non-running sweep: directly after this loop:

```python
        for wid in list(self._finalize_recovery_alerted):
            if wid not in running_ids:
                self._finalize_recovery_alerted.discard(wid)
```

add:

```python
        for wid in list(self._finalize_failure_alerted):
            if wid not in running_ids:
                self._finalize_failure_alerted.discard(wid)
```

(d) Marker re-arm: in the block guarded by `if _latest_marker > self._finalize_marker_seen.get(worker_id, 0):`, directly after `self._finalize_recovery_alerted.discard(worker_id)`, add `self._finalize_failure_alerted.discard(worker_id)`.

Run the Step 1 command again. Expected: PASS, 0 failed.

**Step 3: regression.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py
```

Expected: 0 failed.

**Step 4: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

---

## Task 3: Bisect the vitest `onTaskUpdate` culprit (investigation only)

No dependencies. No tests are required: this task produces an evidence note, with no product, test or config change.

**Files:**
- Modify: `commander/docs/plans/2026-09-26-v1-1-13-vitest-ontaskupdate-findings.md` (append a "Bisect" section)

Logs go to `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect/`. That directory is scratch space, not a repository file.

**Step 1: run every test file in isolation, three times each, one log per run.** The 12 files are:
- `cli`
- `db`
- `git-authority`
- `git-content-merged`
- `git`
- `integration-core`
- `integration-recovery`
- `parent-death-exit`
- `plan-scope`
- `scoped-tree`
- `tool-dispatch`
- `workspace-service`

Run three invocations, each with a Bash timeout of 600000. One pass over all files takes about 220 seconds, so a single loop of three passes would exceed the timeout.

```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect && cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && for f in integration-core; do for r in 1 2 3; do npx vitest run "src/__tests__/$f.test.ts" > "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect/$f-run$r.log" 2>&1; echo "$f run$r exit=$?"; done; done
```

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && for f in workspace-service integration-recovery; do for r in 1 2 3; do npx vitest run "src/__tests__/$f.test.ts" > "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect/$f-run$r.log" 2>&1; echo "$f run$r exit=$?"; done; done
```

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && for f in git-authority git-content-merged cli db git parent-death-exit plan-scope scoped-tree tool-dispatch; do for r in 1 2 3; do npx vitest run "src/__tests__/$f.test.ts" > "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect/$f-run$r.log" 2>&1; echo "$f run$r exit=$?"; done; done
```

Expected: 3, 6 and 27 `exit=` lines respectively, 36 in total. Never re-run a completed invocation: its logs are final.

**Step 2: find the reproductions.**

```bash
rg -l -F 'Timeout calling "onTaskUpdate"' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect
```

```bash
rg -n -e 'Test Files' -e '     Tests ' -e '    Errors ' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect
```

Expected: the list of logs that reproduce the error, which may be empty, and every run's summary lines.

**Step 3: narrow further (bounded; at most 10 more runs).** If one or two files reproduce, run their top-level `describe` blocks separately with `-t "<describe name>"`, one log per run under `bisect/`. Take the `describe` names from `rg -n "^describe\(" <file>`. If nothing reproduces in isolation, skip this step. That result points to a cross-file or concurrency cause, and it is the finding.

**Step 4: write the note.** Append a `## Bisect (remainder loop)` section to `commander/docs/plans/2026-09-26-v1-1-13-vitest-ontaskupdate-findings.md` containing:
- a table of file × run × exit × error present (yes/no), taken from Steps 1–2;
- the quoted summary lines for every reproducing run;
- the Step 3 narrowing results;
- the conclusion: either the culprit file or `describe`, or "not reproduced in isolation (cross-file/concurrency cause)", labelled VERIFIED or UNVERIFIED as appropriate;
- a one-line proposed scope for the follow-up fix loop.

**Step 5: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f commander/docs/plans/2026-09-26-v1-1-13-vitest-ontaskupdate-findings.md
```

---

## Task 4: v1.1.13 CHANGELOG and README, plus the full suites

Depends on Tasks 1, 2 and 3. No tests are required: this task is documentation only, and the full suites below are the release gate.

**Files:**
- Modify: `CHANGELOG.md` (the versioning note and the `## 1.1.13` section)
- Modify: `README.md` (`## What's New in v1.1.13`)

**Step 1: CHANGELOG.**
- In the versioning note, add `worker/mcp-servers/workspace-manager/package-lock.json` (both version fields) to the list of lockstep sources.
- Retitle `## 1.1.13: The seam completes a dead worker whose workspace assignment is already resolved` to `## 1.1.13: The seam completes a dead worker whose workspace assignment is already resolved, plus v1.1.13 validation fixes`.
- In the existing bullet, replace `(\`not-ready\` again means strictly an active row)` with `(\`not-ready\` now covers every other lifecycle — active, reserved, materialized)`.
- Append one bullet per shipped item, each naming its files:
  1. `kill_worker` changes: the terminal guard for `completed`/`killed` (a `failed` worker still runs the seam), the no-registry-row branch, and the honest completion status (`orchestrator_mcp.py`).
  2. The `_classify_finalization_failure` `resolved` arm (`orchestrator_mcp.py`).
  3. Grammar-bounded local grade schemas plus the guard test. This bullet covers `main.py`, `orchestrator_mcp.py`, `shadow_grader.py` and `test_grade_schema_bounds.py`, and says the prompt-waiting bounds equal the validator limits.
  4. The 8192-token floor on schema-bound calls (`grader.py`, `shadow_grader.py`, `worker/hooks/plan-validator.sh`).
  5. The reaper repo list changes: normalize, skip vanished paths, and dedupe by git common dir (`main.py`).
  6. The separate terminal-failure once-gate and the consecutive reset (`main.py`).
  7. The detached-primary plus usable-origin/HEAD reap test (`workspace-service.test.ts`).
  8. `package-lock` 1.1.13 plus the version-consistency coverage.
  9. The vitest `onTaskUpdate` defect: the root cause is a hardcoded 60-second birpc RPC timeout, the culprit has been bisected per the findings note, and the fix is pending in a follow-up loop.
- End with `Deploy: refresh the plugin-cache workspace-manager \`dist/\` and hooks, then restart Commander.`

**Step 2: README.** Under `## What's New in v1.1.13`, keep the existing bullet. Add concise user-facing bullets for items 1, 3+4 (combined as "grader truncation can no longer happen") and 5, plus one line covering items 2 and 6.

**Step 3: verify the wording fix.**

```bash
rg -n -F "strictly an active row" /Users/roberthyatt/Code/ironclaude/CHANGELOG.md /Users/roberthyatt/Code/ironclaude/README.md
```

Expected: no output.

**Step 4: full pytest.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: 0 failed. Record the exact pass line.

**Step 5: full vitest, logged.**

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run > /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/vitest-final.log 2>&1; echo "exit=$?"
```

Then:

```bash
rg -n -e 'Test Files' -e '     Tests ' -e '    Errors ' -e '^[A-Za-z]*Error: ' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/vitest-final.log
```

```bash
rg -c -F 'Timeout calling "onTaskUpdate"' /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/vitest-final.log
```

Expected:
- The `Tests` line contains no `failed`.
- Every line matching `^[A-Za-z]*Error: ` is exactly `Error: [vitest-worker]: Timeout calling "onTaskUpdate"`.
- The `N` in `Errors  N error(s)` equals the second command's count. Alternatively, the `Errors` line is absent and the count is 0, which means exit 0.

Any other error line, any `failed`, or a count mismatch fails the gate.

**Step 6: hook test.**

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-openai-backend.sh
```

Expected: `Results: N passed, 0 failed`.

**Step 7: stage.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add CHANGELOG.md README.md
```

---

## After the loop (operator-gated, outside professional mode)

1. Amend `c4ec287` with the full staged set and move the local `v1.1.13` tag. Do not push.
2. Redeploy at the same version: copy the rebuilt `dist/` into both 1.1.13 plugin caches, then restart Commander with the documented `env -u …` detached relaunch.
3. Push only on an explicit go.
4. Run the follow-up PM loop for the vitest `onTaskUpdate` fix, scoped by Task 3's findings.
