# Seam Finalization-Failure Observability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make a terminal finalize that keeps failing visible. Log the exceptions that are
swallowed today, surface repeated non-finalization failures once to the operator, and make
`kill_worker`'s failure status impossible to read as success.

**Requirements:** docs/plans/2026-09-25-finalize-failure-observability-requirements.md

**Design:** docs/plans/2026-09-25-finalize-failure-observability-design.md

**Architecture:** Three additive changes. None of them completes, abandons or kills a worker.
The invariant "the seam owns ALL completion; the daemon never calls update_worker_status"
still holds.
- In `orchestrator_mcp.py`, three `except` blocks gain `logger.warning` calls, and the
  failure branch of `kill_worker` gets a new status string.
- In `main.py`, `_drive_finalization_recovery` gains a per-worker counter of consecutive
  terminal non-finalization failures. Once the counter passes a cap, the daemon posts once to
  Slack and the Brain.

**Tech Stack:** Python 3, pytest, unittest.mock (commander).

## Grounding (verified against live source while planning)

- `orchestrator_mcp.py`:
  - `logger = logging.getLogger("ironclaude.orchestrator_mcp")` at :72.
  - The swallow sites:
    - `_probe_finalization_status` has `except Exception:` at :3033 and returns
      `recovery_payload, None, transport`;
    - `_abandon_rescue_worker` has the authority `except Exception as exc:` at :3906 and
      the abandon `except Exception as exc:` at :3943.
  - `kill_worker`'s non-completed status string is at :6677-6680.
- `_workspace_failure` (:2924) puts the error under the `"error"` key and the phase under
  `"failure_phase"`.
- `main.py`:
  - `FINALIZE_DRIFT_RETRY_CAP = 3` at :894.
  - `self._finalize_drift_retry` at :1588.
  - `_drive_finalization_recovery(self, worker_id, outcome)` runs from :1676 to :1775, with
    `return "transient"` at :1775.
  - `_finalize_drift_retry` is pruned in two places:
    - the non-running sweep in `check_stuck_workers` (:4168-4170);
    - the new-marker re-arm in `check_workers` (:4477).
- Driver call sites:
  - Terminal outcomes: stuck-kill (:4237), idle-TTL post-kill (:4434), session-died
    (:4568).
  - Non-terminal outcomes: idle-TTL pre-kill (:4421) and the idle marker (:4540). The idle
    marker path runs on every tick while the Brain is unreachable, and an unmanaged
    non-terminal worker always returns `failure_phase: "authority"`
    (`test_worker_finalize_release.py:966-973`).
  - The counter therefore counts ONLY terminal outcomes. Otherwise a healthy idle worker
    would trigger a false "terminal finalize failed" alert. The driver gets a keyword
    `terminal: bool = False`, and only the three terminal call sites pass `terminal=True`.
    This matches the design's own wording ("terminal finalize has failed…").
- No existing test asserts the old kill string. `rg -F "unintegrated work preserved"`
  matches only `orchestrator_mcp.py:6678`. The existing
  `TestKillWorkerPositivePredicate` asserts only `"marked completed" not in status`, so the
  change keeps it passing.
- No existing daemon test drives the driver with a non-finalization phase more than once
  (`_STAYS_RUNNING_OUTCOMES` in `test_daemon.py:4974` calls it once per test), so no
  existing call-count assertion changes.

## Execution invariants

- Bash cwd is `/Users/roberthyatt/Code/ironclaude/commander`. Every command uses absolute
  paths or `git -C /Users/roberthyatt/Code/ironclaude`.
- Shell state does not persist between steps. No step uses a variable exported by an
  earlier step.
- `docs/` is gitignored, so plan and doc artifacts need `git add -f`.
- Always run pytest with `PYTHONUNBUFFERED=1 .venv/bin/python -m pytest`.

---

## Task 1: Swallow-site WARNINGs and an unambiguous `kill_worker` status (orchestrator_mcp.py)

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (:3033, :3906, :3943, :6677-6680)
- Test: `commander/tests/test_worker_finalize_release.py`

**Step 1 (RED): Add the tests.** In `commander/tests/test_worker_finalize_release.py`:

1. Add `import logging` below `import os` (line 9).
2. Append the class below after `TestAbandonRescueWorkerFailClosedGuard` (ends ~:445):

```python
class TestSwallowSiteWarnings:
    """Each swallowed workspace-client exception on the terminal abandon path
    must log at WARNING (it was invisible for ~13 days on r2) while the
    returned failure dict stays unchanged."""

    def _call(self, tools, worker):
        assignment = OrchestratorTools._registry_workspace_assignment(worker)
        return tools._abandon_rescue_worker(
            "w1", worker["repo"], worker["client"], assignment, None,
            already_integrated=False,
        )

    @staticmethod
    def _warnings(caplog):
        return [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]

    def test_probe_raise_logs_warning(self, tmp_path, caplog):
        worker = _worker(tmp_path / "wt")
        tools = _make_tools(worker)
        tools._workspace_client.reconcile.side_effect = WorkspaceClientError(
            "probe down",
        )
        with caplog.at_level(logging.WARNING, logger="ironclaude.orchestrator_mcp"):
            out = self._call(tools, worker)
        assert out["failure_phase"] == "finalization"
        assert any(
            "finalization status probe failed" in m and "probe down" in m
            for m in self._warnings(caplog)
        )

    def test_authority_raise_logs_warning(self, tmp_path, caplog):
        worker = _worker(tmp_path / "wt")
        tools = _make_tools(worker)
        tools._workspace_client.discover_installed_plugin_root.side_effect = (
            WorkspaceClientError("no plugin root")
        )
        with caplog.at_level(logging.WARNING, logger="ironclaude.orchestrator_mcp"):
            out = self._call(tools, worker)
        assert out["failure_phase"] == "authority"
        assert out["action"] == "surfaced"
        assert any(
            "w1" in m and "plugin-root discovery failed" in m and "no plugin root" in m
            for m in self._warnings(caplog)
        )

    def test_abandon_raise_logs_warning(self, tmp_path, caplog):
        worker = _worker(tmp_path / "wt")
        tools = _make_tools(worker)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        tools._workspace_client.abandon.side_effect = WorkspaceClientError(
            "sqlite bindings missing",
        )
        with caplog.at_level(logging.WARNING, logger="ironclaude.orchestrator_mcp"):
            out = self._call(tools, worker)
        assert out["failure_phase"] == "abandon"
        assert out["action"] == "surfaced"
        tools.registry.update_worker_status.assert_not_called()
        assert any(
            "w1" in m and "abandon failed" in m and "sqlite bindings missing" in m
            for m in self._warnings(caplog)
        )
```

3. Append the class below after `TestKillWorkerPositivePredicate` (at the end of the file or
   after that class):

```python
class TestKillWorkerFailureStatusWording:
    """kill_worker's non-completed status must read as a FAILURE (the Brain
    misread the old 'killed; …preserved' wording as success 5x)."""

    @staticmethod
    def _kill(release):
        tools = object.__new__(OrchestratorTools)
        tools.registry = MagicMock()
        tools.registry.get_worker.return_value = {
            "id": "w9", "machine": None,
            "spawned_at": "2026-01-01T00:00:00+00:00",
        }
        tools.registry.update_worker_status = MagicMock()
        tools.registry.log_event = MagicMock()
        tools.tmux = MagicMock()
        tools.tmux.list_pane_pid.return_value = "123"
        tools._ensure_ssh_manager = MagicMock()
        tools._resolve_ssh_host = MagicMock(return_value=None)
        tools._db = None
        tools._get_remaining_work_after_kill = MagicMock(return_value={})
        tools._finalize_and_release_worker = MagicMock(return_value=release)
        return tools, tools.kill_worker("w9")

    def test_failure_status_names_phase_and_error(self):
        tools, result = self._kill({
            "failure_phase": "abandon",
            "error": "sqlite bindings missing",
            "assignment_preserved": True,
        })
        status = result["status"]
        assert "finalization FAILED" in status
        assert "NOT completed" in status
        assert "phase=abandon" in status
        assert "sqlite bindings missing" in status
        assert "unintegrated work preserved" not in status
        tools.registry.update_worker_status.assert_not_called()

    def test_none_release_status_says_unknown(self):
        _tools, result = self._kill(None)
        status = result["status"]
        assert "finalization FAILED" in status
        assert "NOT completed" in status
        assert "phase=unknown" in status
        assert "no result" in status
```

**Step 2: Run the tests and confirm RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_finalize_release.py -k "SwallowSiteWarnings or KillWorkerFailureStatusWording"
```

Expected: 5 failed. The three WARNING tests fail on the `any(...)` assertion because nothing
is logged yet. The two wording tests fail on `"finalization FAILED" in status`.

**Step 3 (GREEN): Implement the change in `commander/src/ironclaude/orchestrator_mcp.py`.**

(a) `_probe_finalization_status`, :3033-3034. Replace

```python
        except Exception:
            return recovery_payload, None, transport
```

with

```python
        except Exception as exc:  # noqa: BLE001 - probe failure is fail-closed
            logger.warning(
                "finalization status probe failed for workspace %s: %s",
                assignment["workspace_guid"], exc,
            )
            return recovery_payload, None, transport
```

(b) `_abandon_rescue_worker`, authority `except` (:3906). Insert this as the first line of
the block, before `failure = self._workspace_failure(` / `"authority", …`:

```python
            logger.warning(
                "abandon-rescue for %s: plugin-root discovery failed: %s",
                worker_id, exc,
            )
```

(c) `_abandon_rescue_worker`, abandon `except` (:3943). Insert this as the first line of the
block, before `failure = self._workspace_failure(` / `"abandon", …`:

```python
            logger.warning(
                "abandon-rescue for %s: abandon failed: %s", worker_id, exc,
            )
```

(d) `kill_worker`, :6676-6680. Replace

```python
        else:
            _status = (
                f"Worker {worker_id} killed; unintegrated work preserved for "
                "retry (not completed)."
            )
```

with

```python
        else:
            _phase = _release.get("failure_phase") if isinstance(_release, dict) else None
            _error = _release.get("error") if isinstance(_release, dict) else None
            _status = (
                f"Worker {worker_id} session killed, but finalization FAILED "
                f"(phase={_phase or 'unknown'}: {_error or 'no result'}) — worker "
                "NOT completed; work preserved; daemon will retry."
            )
```

The success-branch string (`"… killed and marked completed."`) stays unchanged.

**Step 4: Run the tests and confirm GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_finalize_release.py
```

Expected: 0 failed. This includes the 5 new tests, plus
`TestKillWorkerPositivePredicate` and `TestAbandonRescueWorkerFailClosedGuard`, which are
unchanged.

**Step 5: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_finalize_release.py
```

Expected: both files are staged.

---

## Task 2: Bounded surface for consecutive terminal failures (main.py)

**Files:**
- Modify: `commander/src/ironclaude/main.py` (:894, :1588, :1676-1775, :4168-4170, :4237, :4434, :4477, :4568)
- Test: `commander/tests/test_daemon.py`

**Step 1 (RED): Add the tests.** In `commander/tests/test_daemon.py`:

1. Change the import at :18 to add `FINALIZE_FAILURE_SURFACE_CAP`:
   `from ironclaude.main import CHECKIN_CADENCE, FINALIZE_DRIFT_RETRY_CAP, FINALIZE_FAILURE_SURFACE_CAP, PM_GATE_STAGES, PM_GATE_SLACK_SECONDS, IroncladeDaemon, PromptDetection, ensure_brain_trusted`
2. Append the class below after `TestFinalizationFailureTransientSurface` (ends ~:5395):

```python
class TestTerminalFinalizeFailureSurface:
    """A TERMINAL finalize that keeps failing outside the 'finalization' phase
    (authority/probe/abandon) was silently 'transient' forever (r2: ~13 days).
    Past FINALIZE_FAILURE_SURFACE_CAP consecutive terminal failures it must be
    surfaced to slack+brain exactly once — never completed, never abandoned."""

    _OUTCOME = {
        "failure_phase": "abandon",
        "error": "abandon exploded",
        "assignment_preserved": True,
    }

    def test_surfaces_once_after_cap(self, daemon):
        for _ in range(FINALIZE_FAILURE_SURFACE_CAP):
            assert daemon._drive_finalization_recovery(
                "w1", self._OUTCOME, terminal=True,
            ) == "transient"
        assert daemon.slack.post_message.call_count == 0
        assert daemon.brain.send_message.call_count == 0

        assert daemon._drive_finalization_recovery(
            "w1", self._OUTCOME, terminal=True,
        ) == "transient"
        assert daemon.slack.post_message.call_count == 1
        assert daemon.brain.send_message.call_count == 1
        posted = daemon.slack.post_message.call_args[0][0]
        assert "w1" in posted
        assert f"{FINALIZE_FAILURE_SURFACE_CAP + 1} consecutive cycles" in posted
        assert "phase=abandon" in posted
        assert "abandon exploded" in posted
        assert "not completed" in posted

        for _ in range(3):
            daemon._drive_finalization_recovery("w1", self._OUTCOME, terminal=True)
        assert daemon.slack.post_message.call_count == 1
        assert daemon.brain.send_message.call_count == 1
        daemon.registry.update_worker_status.assert_not_called()

    def test_non_terminal_outcomes_never_count(self, daemon):
        for _ in range(FINALIZE_FAILURE_SURFACE_CAP + 2):
            daemon._drive_finalization_recovery("w1", self._OUTCOME)
        assert "w1" not in daemon._finalize_failure_count
        assert daemon.slack.post_message.call_count == 0

    def test_none_outcome_never_counts(self, daemon):
        for _ in range(FINALIZE_FAILURE_SURFACE_CAP + 2):
            daemon._drive_finalization_recovery("w1", None, terminal=True)
        assert "w1" not in daemon._finalize_failure_count
        assert daemon.slack.post_message.call_count == 0

    def test_session_died_site_counts_as_terminal(self, daemon):
        _seam_session_died(daemon, dict(self._OUTCOME))
        daemon.check_workers()
        assert daemon._finalize_failure_count.get("w1") == 1

    def test_stuck_kill_site_counts_as_terminal(self, daemon):
        _seam_stuck_kill(daemon, dict(self._OUTCOME))
        daemon._confirm_and_kill_stuck_worker(
            "w1", "ic-w1", 1200.0, "execution", False, None,
        )
        assert daemon._finalize_failure_count.get("w1") == 1

    def test_idle_reap_counts_only_the_post_kill_outcome(self, daemon):
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = dict(self._OUTCOME)
        daemon._get_orchestrator = MagicMock(return_value=orch)
        daemon.tmux.get_log_mtime.return_value = None
        daemon._reap_idle_worker("w1", "ic-w1", None, None, time.time() - 100000)
        # pre-kill (terminal=False) must not count; post-kill (terminal=True) counts once.
        assert daemon._finalize_failure_count.get("w1") == 1

    def test_non_running_sweep_clears_failure_count(self, daemon):
        daemon._finalize_failure_count["w1"] = 2
        daemon.registry.get_running_workers.return_value = []
        daemon._last_stuck_check = 0
        daemon.check_stuck_workers()
        assert "w1" not in daemon._finalize_failure_count

    def test_new_marker_rearms_failure_count(self, daemon):
        _live_worker(daemon)
        daemon.registry.get_events_for_worker.return_value = [
            {"id": 4, "event_type": "finalize_integrated", "worker_id": "w1"},
        ]
        daemon._finalize_failure_count["w1"] = 2
        daemon.check_workers()
        assert "w1" not in daemon._finalize_failure_count
```

**Step 2: Run the tests and confirm RED.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py -k TestTerminalFinalizeFailureSurface
```

Expected: a collection ERROR, because `FINALIZE_FAILURE_SURFACE_CAP` cannot be imported from
`ironclaude.main`. That is the RED signal.

**Step 3 (GREEN): Implement the change in `commander/src/ironclaude/main.py`.**

(a) Below `FINALIZE_DRIFT_RETRY_CAP = 3` (:894), add:

```python
# Consecutive TERMINAL finalize failures outside the 'finalization' phase
# (authority/probe/abandon) tolerated before the daemon surfaces the stuck
# worker to the operator once. Surface-only: never completes or abandons.
FINALIZE_FAILURE_SURFACE_CAP = 3
```

(b) Below `self._finalize_drift_retry: dict[str, int] = {}` (:1588), add:

```python
        self._finalize_failure_count: dict[str, int] = {}
```

(c) Change the signature at :1676 to
`def _drive_finalization_recovery(self, worker_id: str, outcome, *, terminal: bool = False) -> str:`.
Replace the `'transient'` docstring bullet (:1695-1696) with:

```
          'transient'  — None / any other mode (authority/probe/abandon/None):
                         leave the worker running. A TERMINAL outcome with a
                         non-'finalization' failure_phase is counted; past
                         FINALIZE_FAILURE_SURFACE_CAP consecutive terminal
                         failures it is surfaced once (never completed).
```

(d) Insert this immediately before the final `return "transient"` (:1775), after the
existing `phase == "finalization"` surface block:

```python
        # A TERMINAL finalize failing outside the 'finalization' phase
        # (authority/probe/abandon) used to stay silently 'transient' forever.
        # Count consecutive terminal failures and surface ONCE past the cap.
        # Non-terminal (idle) outcomes are not counted: an idle live worker
        # legitimately returns a preserved failure every cycle.
        phase = outcome.get("failure_phase") if isinstance(outcome, dict) else None
        if terminal and phase and phase != "finalization":
            failures = self._finalize_failure_count.get(worker_id, 0) + 1
            self._finalize_failure_count[worker_id] = failures
            if (
                failures > FINALIZE_FAILURE_SURFACE_CAP
                and worker_id not in self._finalize_recovery_alerted
            ):
                self._finalize_recovery_alerted.add(worker_id)
                error = outcome.get("error") or "no error detail"
                self.slack.post_message(
                    f"Worker {worker_id} terminal finalize has failed {failures} "
                    f"consecutive cycles (phase={phase}): {error}; left running, "
                    "not completed, needs operator help."
                )
                self.brain.send_message(
                    f"Worker {worker_id} terminal finalize has failed {failures} "
                    f"consecutive cycles (phase={phase}): {error}. Not completed, "
                    "not abandoned. Needs operator intervention."
                )
```

(e) Pass `terminal=True` at the three terminal call sites:
- :4237: `self._drive_finalization_recovery(worker_id, outcome, terminal=True)`
- :4434: `self._drive_finalization_recovery(worker_id, outcome, terminal=True)`
- :4568: `disposition = self._drive_finalization_recovery(worker_id, outcome, terminal=True)`

Leave :4421 (`pre`) and :4540 (idle marker) unchanged, so they stay non-terminal.

(f) In the non-running sweep, below the `_finalize_drift_retry` prune loop (:4168-4170), add:

```python
        for wid in list(self._finalize_failure_count.keys()):
            if wid not in running_ids:
                self._finalize_failure_count.pop(wid, None)
```

(g) In the new-marker re-arm, below `self._finalize_drift_retry.pop(worker_id, None)` (:4477),
add:

```python
                self._finalize_failure_count.pop(worker_id, None)
```

**Step 4: Run the tests and confirm GREEN.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_daemon.py tests/test_idle_worker_ttl.py
```

Expected: 0 failed. This includes the 8 new tests and every existing driver, surface and
idle-TTL test.

**Step 5: Stage the changes.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/main.py commander/tests/test_daemon.py
```

Expected: both files are staged.

---

## Task 3: CHANGELOG entry and full-suite verification

No tests are required for this task: it is a documentation entry plus full-suite
verification.

**Files:**
- Modify: `CHANGELOG.md` (under `## [Unreleased]`, :13)

**Step 1: Add the CHANGELOG entry.** Insert this bullet as the first entry under
`## [Unreleased]` (a blank line after the heading, then the bullet, then a blank line):

```markdown
- **A terminal finalize that keeps failing is now visible instead of silently "transient" forever.** A dead worker whose terminal `abandon` raised on every daemon tick (~13 days on one worker) left no log line, no operator alert, and a `kill_worker` status the Brain read as success. Now: (1) the swallowed workspace-client exceptions in `_probe_finalization_status` and `_abandon_rescue_worker` (authority + abandon) log at WARNING; (2) `_drive_finalization_recovery` counts consecutive TERMINAL failures outside the `finalization` phase (authority/probe/abandon) and, past `FINALIZE_FAILURE_SURFACE_CAP` (3), posts once to Slack + Brain — the worker is left running, never completed or abandoned (non-terminal idle outcomes are not counted); (3) `kill_worker`'s failure status now reads "finalization FAILED (phase=…: …) — worker NOT completed". The seam-owns-completion invariant is unchanged. (`orchestrator_mcp.py`, `main.py`; `test_worker_finalize_release.py`, `test_daemon.py`.) Deploy: Commander restart.
```

**Step 2: Run the full commander suite.**

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```

Expected: `0 failed`. The final line reads `N passed`, where N equals the pre-change count
plus 13 new tests (5 in Task 1 and 8 in Task 2).

**Step 3: Stage the CHANGELOG and the plan docs.**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f CHANGELOG.md commander/docs/plans/2026-09-25-finalize-failure-observability.md commander/docs/plans/2026-09-25-finalize-failure-observability.plan.json
```

Expected: all three files are staged.
