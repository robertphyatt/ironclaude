# Handoff Final-Review Fixes (L-H1b) Implementation Plan

> **Goal:** Make the approved H6a/H7 recovery reachable in production. Once a target session may occupy the worktree, a gone bootstrap is proved by PID plus start time instead of worktree quiescence (R1). An already-gone bootstrap is recorded rather than re-stopped (R2). An undeliverable continuation to a dead target session goes to the preserve path instead of replaying forever (R3).
> **Requirements:** `docs/plans/2026-10-09-handoff-final-review-fixes-design.md` (R1–R4, inline)
> **Design:** `docs/plans/2026-10-09-handoff-final-review-fixes-design.md`
> **Architecture:** Only `commander/src/ironclaude/worker_handoff.py` changes, plus its tests in `commander/tests/test_worker_handoff.py`.
> - `_bootstrap_proven_gone(evidence, phase=None)` gains a "target may occupy the worktree" branch.
> - `_stop_bootstrap` records the proof for an already-gone bootstrap.
> - `retry_paused_preflight` routes a `runtime_registered` / `continuation_dispatch_unproved` pause whose target session tmux reports absent to `_preserve_after_stop`.
> - No schema, workspace-manager or MCP changes.
>
> **Tech Stack:** Python 3 and pytest. The tests use the real `NativeProcessAdapter` with the existing fake psutil and tmux fixtures (`_HandoffProc`, `_HandoffPs`, `_HandoffTmux`, `_bootstrap_tree`) in `commander/tests/test_worker_handoff.py`.
> **Loop: full** — process-lifecycle proofs and worker recovery paths in the Commander handoff controller: design (approved, "Fix M1+M2 (Rec.)", design "Yes (Rec.)"), then this plan, then execute with per-task code review, then verification (Task 3), then a final Fable review of the whole staged handoff before any commit.

## Execution invariants

1. **Paths and shell:** every path is absolute, or the command uses `git -C /Users/roberthyatt/Code/ironclaude`. Shell state does not persist between commands. The shell is zsh with `nomatch`. No foreground `sleep`, and no `2>/dev/null` on evidence commands.
2. **Commander tests:** run them with `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q <files or -k>`.
3. **No live state:**
   - Never restart the Commander.
   - Never touch `~/.ironclaude` or any live database.
   - Tests use `tmp_path` only.
4. **Staging:**
   - No commits.
   - Stage only each task's `allowed_files`, with `git -C /Users/roberthyatt/Code/ironclaude add <paths>`.
   - `docs/` is gitignored, so docs need `git add -f`.
5. **TDD per task:** a RED test step, then a run (the named tests fail for the stated reason; record actual counts), then GREEN code, then a run (record actual counts), then stage.
6. **Edits:**
   - **Anchors:** every "replace exactly" anchor is copied from the current staged source and is unique; uniqueness was confirmed with `grep -c -F` / `grep -n -F`.
   - **`replace_all`:** where a step uses `replace_all`, it states the expected count.
7. **Grounded symbols** (verified in the current source):
   - `WorkerHandoffController._bootstrap_proven_gone(self, evidence)`: `worker_handoff.py` lines 660–685, with callers at lines 705, 706, 778 and 784.
   - `_stop_bootstrap`: lines 687–723. It writes `bootstrap_process`, `bootstrap_stop_intent`, `bootstrap_stopped` and `bootstrap_stop_proof`.
   - `_release_after_stop`: lines 760–793.
   - `retry_paused_preflight`: lines 440–507. It routes `POST_STOP_RETRY_REASONS` (lines 423–431) to `_retry_forward`.
   - `_post_transfer_cleanup`: lines 586–588. It replays `continuation_dispatch_unproved` by clearing `dispatch_intent`.
   - `POST_TRANSFER_PHASES`: line 432.
   - `NativeProcessAdapter.proved_absent`: `worker_handoff_process.py` line 89.
   - `verify_stopped`: lines 105–112. It asserts worktree quiescence.
   - The adapter's `_HandoffTmux` test fake has no `has_session`. Tests that need it assign one.

---

## Task 1: Prove a gone bootstrap by PID plus start time once a target may occupy the worktree, and record an already-gone bootstrap (R1, R2, R4 a–d)

**Depends on:** none

**Files:**
- `commander/src/ironclaude/worker_handoff.py`
- `commander/tests/test_worker_handoff.py`

### Step 1: RED — tests

In `commander/tests/test_worker_handoff.py`, replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
def _bootstrap_beside_worktree_process(tmp_path, *, phase, reason, bootstrap_alive, **extra):
    """A recorded read-only bootstrap plus another live process whose cwd is the worktree."""
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    worktree = Path(source["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    if not bootstrap_alive:
        root.gone = child.gone = True
    occupant = _HandoffProc(42000, ["claude", "--dangerously-skip-permissions"], worktree)
    occupant._pool = [occupant]
    ps.processes[occupant.pid] = occupant
    tmux.has_session = lambda name, ssh_host=None: name in tmux.panes and not tmux.panes[name].gone
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, phase, reason, target_id=target_id, target_client="codex",
                       target_model="gpt-5.6-sol", bootstrap_process=bootstrap, **extra)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(source), "transferred", target_id)
    workspace.finish_handoff_after_target.return_value = {"protocol_version": 1, "phase": "finished"}
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    return registry, controller, workspace, target_id, occupant


def test_post_transfer_preserve_proves_gone_bootstrap_beside_live_target(tmp_path):
    registry, controller, workspace, target_id, occupant = _bootstrap_beside_worktree_process(
        tmp_path, phase="owner_transferred", reason="target_runtime_changed", bootstrap_alive=False)
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True, result
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["disposition"] == "released_to_target"
    assert evidence["bootstrap_stopped"] is True and evidence["bootstrap_stop_intent"] is False
    assert evidence["bootstrap_stop_proof"]["proved_absent"] is True
    worker = registry.get_worker("w1")
    assert (worker["status"], worker["client"], worker["native_session_id"]) == ("running", "codex", target_id)
    assert occupant.gone is False
    workspace.finish_handoff_after_target.assert_called_once()


def test_post_transfer_preserve_does_not_rerecord_already_stopped_bootstrap(tmp_path):
    registry, controller, workspace, target_id, occupant = _bootstrap_beside_worktree_process(
        tmp_path, phase="owner_transferred", reason="target_runtime_changed", bootstrap_alive=False,
        bootstrap_stopped=True, bootstrap_stop_intent=False)
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True, result
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["disposition"] == "released_to_target"
    assert evidence["bootstrap_stopped"] is True and evidence["bootstrap_stop_intent"] is False
    assert "bootstrap_stop_proof" not in evidence
    assert occupant.gone is False and registry.get_worker("w1")["native_session_id"] == target_id


def test_post_transfer_abort_proves_gone_bootstrap_beside_live_target(tmp_path):
    registry, controller, workspace, target_id, occupant = _bootstrap_beside_worktree_process(
        tmp_path, phase="owner_transferred", reason="uncertain_external_effect", bootstrap_alive=False)
    result = controller.abort_handoff("w1")
    assert result["status"] == "released", result
    assert json.loads(registry.get_worker_handoff("w1")["evidence_json"])["disposition"] == "released_to_target"
    assert registry.get_worker("w1")["native_session_id"] == target_id
    assert occupant.gone is False


@pytest.mark.parametrize("via,refusal", [("preserve", "target_bootstrap_stop_unproved"),
                                         ("abort", "target_bootstrap_alive_or_unproved")])
def test_post_transfer_live_bootstrap_still_refuses(tmp_path, via, refusal):
    reason = "target_runtime_changed" if via == "preserve" else "uncertain_external_effect"
    registry, controller, workspace, _target_id, occupant = _bootstrap_beside_worktree_process(
        tmp_path, phase="owner_transferred", reason=reason, bootstrap_alive=True)
    result = (controller.retry_paused_preflight("w1") if via == "preserve"
              else controller.abort_handoff("w1"))
    assert result["status"] == "held"
    assert result[f"{via}_refused"] == refusal
    assert registry.get_worker_handoff("w1")["pause_reason"] == reason
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    workspace.finish_handoff_after_target.assert_not_called()


@pytest.mark.parametrize("via,reason,refusal", [
    ("preserve", "pm_prepare_failed", "target_bootstrap_stop_unproved"),
    ("abort", "uncertain_external_effect", "target_bootstrap_alive_or_unproved"),
])
def test_pre_transfer_gone_bootstrap_still_requires_worktree_quiescence(tmp_path, via, reason, refusal):
    registry, controller, workspace, _target_id, _occupant = _bootstrap_beside_worktree_process(
        tmp_path, phase="target_ready", reason=reason, bootstrap_alive=False)
    result = (controller.retry_paused_preflight("w1") if via == "preserve"
              else controller.abort_handoff("w1"))
    assert result["status"] == "held"
    assert result[f"{via}_refused"] == refusal
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    workspace.release_handoff.assert_not_called()


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py -k "beside_live_target or does_not_rerecord or live_bootstrap_still_refuses or still_requires_worktree_quiescence"`

Expected: FAIL: test_post_transfer_preserve_proves_gone_bootstrap_beside_live_target, test_post_transfer_preserve_does_not_rerecord_already_stopped_bootstrap and test_post_transfer_abort_proves_gone_bootstrap_beside_live_target (held: verify_stopped's worktree quiescence fails on the live target process); test_post_transfer_live_bootstrap_still_refuses[*] and test_pre_transfer_gone_bootstrap_still_requires_worktree_quiescence[*] pass as guards; record actual counts

### Step 3: GREEN — `worker_handoff.py`

1. Replace exactly:

```python
    def _bootstrap_proven_gone(self, evidence: dict) -> bool:
        """A recorded target bootstrap counts as gone only on PID plus start-time proof (H7).

        H7b: when the worktree directory no longer exists, every recorded process must be
        proved absent by PID plus start time. H7a: with only a launch intent and no
        recorded process, the bootstrap tmux session must be absent and no process may
        remain associated with the worktree.
        """
        snapshot = evidence.get("bootstrap_process")
        if isinstance(snapshot, dict):
            try:
                if not Path(evidence["scope"]["expected_worktree_path"]).is_dir():
                    return bool(self.process.proved_absent(snapshot))
                return bool(self.process.verify_stopped(snapshot).get("stopped"))
            except Exception:
                return False
        name = evidence.get("bootstrap_intent")
        if name:
            try:
                if self.tools.tmux.has_session(name):
                    return False
                return bool(self.process.assert_quiescent(
                    evidence["scope"]["expected_worktree_path"]).get("quiescent"))
            except Exception:
                return False
        return True
```

with:

```python
    def _bootstrap_proven_gone(self, evidence: dict, phase: str | None = None) -> bool:
        """A recorded target bootstrap counts as gone only on PID plus start-time proof (H7).

        H7b: when the worktree directory no longer exists, every recorded process must be
        proved absent by PID plus start time. H7a: with only a launch intent and no
        recorded process, the bootstrap tmux session must be absent and no process may
        remain associated with the worktree.

        R1: once a target session may occupy the worktree (a post-transfer phase, or
        evidence of bootstrap_stopped, resume_intent or target_session), worktree
        quiescence cannot be required because the target legitimately runs there. A
        recorded bootstrap is then proved gone by PID plus start time alone, and a
        launch-intent-only bootstrap by its own tmux session being absent. Unreadable
        identities and tmux errors stay unproved.
        """
        target_may_occupy = (phase in self.POST_TRANSFER_PHASES
                             or any(evidence.get(key) for key in ("bootstrap_stopped", "resume_intent",
                                                                  "target_session")))
        snapshot = evidence.get("bootstrap_process")
        if isinstance(snapshot, dict):
            try:
                if target_may_occupy or not Path(evidence["scope"]["expected_worktree_path"]).is_dir():
                    return bool(self.process.proved_absent(snapshot))
                return bool(self.process.verify_stopped(snapshot).get("stopped"))
            except Exception:
                return False
        name = evidence.get("bootstrap_intent")
        if name:
            try:
                if self.tools.tmux.has_session(name):
                    return False
                if target_may_occupy:
                    return True
                return bool(self.process.assert_quiescent(
                    evidence["scope"]["expected_worktree_path"]).get("quiescent"))
            except Exception:
                return False
        return True
```

2. Replace exactly `            return self._bootstrap_proven_gone(evidence)` with:

```python
            return self._bootstrap_proven_gone(evidence, phase)
```

3. Replace exactly:

```python
        if self._bootstrap_proven_gone({**evidence, "bootstrap_process": snapshot}):
            return True
```

with:

```python
        if self._bootstrap_proven_gone({**evidence, "bootstrap_process": snapshot}, phase):
            # R2: the bootstrap is already gone; record the proof instead of signalling an
            # exited tree (stop_and_prove refuses once the recorded pane root has exited).
            if evidence.get("bootstrap_stopped"):
                return True
            try:
                self.registry.resume_worker_handoff(worker_id, generation, phase)
                try:
                    self.registry.record_worker_handoff_evidence(worker_id, generation, phase, {
                        "bootstrap_process": snapshot, "bootstrap_stop_intent": False,
                        "bootstrap_stopped": True,
                        "bootstrap_stop_proof": {
                            "stopped": True, "proved_absent": True,
                            "source_pids": [item["pid"] for item in snapshot["processes"]]}})
                finally:
                    self.registry.pause_worker_handoff(worker_id, generation, reason)
            except Exception:
                return False
            return True
```

4. `replace_all`, 2 occurrences, in `_release_after_stop`. Confirm the count first with `grep -c -F "self._bootstrap_proven_gone(evidence):" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/worker_handoff.py`, which must print `2`. Replace each `self._bootstrap_proven_gone(evidence):` with `self._bootstrap_proven_gone(evidence, phase):`. In that method, `phase` is the local `row["phase"]`.

### Step 4: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py tests/test_worker_handoff_process.py tests/test_worker_handoff_registry.py tests/test_worker_handoff_daemon.py`

Expected: all pass; record actual counts

### Step 5: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_handoff.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 2: Preserve instead of replaying a continuation to a dead target session (R3, R4 e–g)

**Depends on:** Task 1

**Files:**
- `commander/src/ironclaude/worker_handoff.py`
- `commander/tests/test_worker_handoff.py`

### Step 1: RED — tests

In `commander/tests/test_worker_handoff.py`, replace exactly `def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):` with:

```python
def _undelivered_dispatch_state(tmp_path, *, has_session):
    """runtime_registered with production-shaped evidence: the bootstrap recorded at
    target_ready is gone and proved stopped, the resumed target occupies the worktree,
    and tmux refused the continuation keys. has_session: bool, or an Exception to raise."""
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    worktree = Path(source["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    root.gone = child.gone = True
    occupant = _HandoffProc(42000, ["codex", "--no-daemon", "--dangerously-bypass-approvals-and-sandbox"], worktree)
    occupant._pool = [occupant]
    ps.processes[occupant.pid] = occupant
    tmux.has_session_calls = []

    def _has_session(name, ssh_host=None):
        tmux.has_session_calls.append(name)
        if isinstance(has_session, Exception):
            raise has_session
        return has_session
    tmux.has_session = _has_session
    target_id = str(uuid.uuid4())
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-5.6-sol",
                                    native_session_id=target_id, tmux_session="ic-w1-h1", machine=None)
    registry.advance_worker_handoff("w1", 1, "owner_transferred", "runtime_registered")
    registry.record_worker_handoff_evidence("w1", 1, "runtime_registered", {
        "scope": _scope(source), "transport": {}, "capability_token": "token", "target_id": target_id,
        "target_client": "codex", "target_model": "gpt-5.6-sol", "target_session": "ic-w1-h1",
        "bootstrap_session": "ic-w1-h1-bootstrap", "bootstrap_process": bootstrap,
        "bootstrap_stop_intent": False, "bootstrap_stopped": True, "resume_intent": False,
        "dispatch_intent": True})
    registry.pause_worker_handoff("w1", 1, "continuation_dispatch_unproved")
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(source), "transferred", target_id)
    workspace.finish_handoff_after_target.return_value = {"protocol_version": 1, "phase": "finished"}
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    return registry, controller, workspace, tmux, target_id, occupant


def test_dead_target_session_preserves_instead_of_replaying_dispatch(tmp_path):
    registry, controller, workspace, tmux, target_id, occupant = _undelivered_dispatch_state(
        tmp_path, has_session=False)
    controller._dispatch = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True, result
    assert tmux.has_session_calls == ["ic-w1-h1"]
    assert tmux.sent == []
    controller._dispatch.assert_not_called()
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["disposition"] == "released_to_target"
    # R2: the recorded bootstrap was proved absent by PID plus start time and not re-recorded.
    assert evidence["bootstrap_stopped"] is True and "bootstrap_stop_proof" not in evidence
    worker = registry.get_worker("w1")
    assert (worker["status"], worker["client"], worker["native_session_id"]) == ("running", "codex", target_id)
    assert occupant.gone is False
    workspace.finish_handoff_after_target.assert_called_once()


def test_live_target_session_keeps_dispatch_replay(tmp_path):
    registry, controller, workspace, tmux, _target_id, _occupant = _undelivered_dispatch_state(
        tmp_path, has_session=True)
    controller._dispatch = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    controller._dispatch.assert_called_once()
    row = registry.get_worker_handoff("w1")
    assert row["pause_reason"] is None and row["completed_at"] is None
    assert json.loads(row["evidence_json"])["dispatch_intent"] is False
    workspace.finish_handoff_after_target.assert_not_called()


def test_target_session_check_error_keeps_the_hold(tmp_path):
    registry, controller, workspace, tmux, _target_id, _occupant = _undelivered_dispatch_state(
        tmp_path, has_session=RuntimeError("tmux server unavailable"))
    controller._dispatch = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "held"
    row = registry.get_worker_handoff("w1")
    assert row["pause_reason"] == "continuation_dispatch_unproved" and row["completed_at"] is None
    assert json.loads(row["evidence_json"])["dispatch_intent"] is True
    controller._dispatch.assert_not_called()
    workspace.finish_handoff_after_target.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"


def test_operator_paused_pm_does_not_claim_or_stop_source(tmp_path):
```

### Step 2: Run RED

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py -k "target_session_preserves or keeps_dispatch_replay or session_check_error"`

Expected: FAIL: test_dead_target_session_preserves_instead_of_replaying_dispatch (_dispatch is called, so the status is not released) and test_target_session_check_error_keeps_the_hold (_dispatch is called, so the status is not held); test_live_target_session_keeps_dispatch_replay passes as guard; record actual counts

### Step 3: GREEN — `worker_handoff.py`

Replace exactly:

```python
            if row["pause_reason"] in self.POST_STOP_RETRY_REASONS:
                return self._retry_forward(worker_id)
            return self._preserve_after_stop(worker_id)
```

with:

```python
            if row["phase"] == "runtime_registered" and row["pause_reason"] == "continuation_dispatch_unproved":
                # R3: a target session tmux positively reports absent can never receive the
                # continuation, so preserve as the target instead of replaying. A tmux error
                # keeps the hold; a present session keeps the replay below.
                try:
                    target_alive = self.tools.tmux.has_session(evidence["target_session"])
                except Exception:
                    return self._status(worker_id)
                if not target_alive:
                    return self._preserve_after_stop(worker_id)
            if row["pause_reason"] in self.POST_STOP_RETRY_REASONS:
                return self._retry_forward(worker_id)
            return self._preserve_after_stop(worker_id)
```

### Step 4: Run GREEN

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py tests/test_worker_handoff_daemon.py`

Expected: all pass, including the existing test_undelivered_continuation_dispatch_is_retried_forward, whose MagicMock tmux reports a present session; record actual counts

### Step 5: Stage

Command: `git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/worker_handoff.py commander/tests/test_worker_handoff.py`

Expected: exit 0

---

## Task 3: Verification (affected suites, full Commander pytest, verification record)

**Depends on:** Task 2

**Files:**
- `docs/reviews/2026-10-09-handoff-final-review-fixes-verification.md`

No new tests: this task runs existing checks and records the results.

### Step 1: Log directory

Command: `mkdir -p /private/tmp/ironclaude-l-h1b-verify`

Expected: exit 0

### Step 2: Affected suites

Command: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q tests/test_worker_handoff.py tests/test_worker_handoff_process.py tests/test_worker_handoff_registry.py tests/test_worker_handoff_daemon.py tests/test_orchestrator_mcp.py > /private/tmp/ironclaude-l-h1b-verify/pytest-affected.log 2>&1; echo "exit=$?"`

Expected: exit=0; record the summary line

### Step 3: Full Commander pytest

Command (Bash timeout 600000): `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q > /private/tmp/ironclaude-l-h1b-verify/pytest-full.log 2>&1; echo "exit=$?"`

Expected: the only failure, if any, is the known pre-existing tests/test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd (tracked as L-T2; see docs/reviews/2026-10-09-handoff-recovery-fixes-verification.md); record the actual summary line; any other failure is resolved before the final review

### Step 4: Write the verification record

Write `/Users/roberthyatt/Code/ironclaude/docs/reviews/2026-10-09-handoff-final-review-fixes-verification.md` with:
- the commands from Steps 2–3, verbatim, with their exit statuses;
- the quoted summary lines;
- the known pre-existing failure, if it occurred;
- a table mapping R1–R4 to the Task 1/2 tests that cover each.

State that the final Fable review of the whole staged handoff follows and gates the commit.

### Step 5: Stage the record

Command: `git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-09-handoff-final-review-fixes-verification.md`

Expected: exit 0
