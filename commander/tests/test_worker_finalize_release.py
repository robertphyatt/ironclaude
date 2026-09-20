# tests/test_worker_finalize_release.py
"""Tests for deterministic auto-integration: OrchestratorTools._finalize_and_release_worker
and the four daemon/orchestrator seams that call it.

Temp-isolated: every git repo lives under pytest's per-test tmp_path; no process is
ever pattern-killed (recorded stubs only, never os.kill by name).
"""

import os
import subprocess

import pytest
from unittest.mock import MagicMock

from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.workspace_client import WorkspaceClientError

_OWNER = "11111111-1111-4111-8111-111111111111"
_GUID = "22222222-2222-4222-8222-222222222222"


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    )


def _make_repo(base, *, new_work):
    """A real git checkout on branch ironclaude/wt. When new_work is False the
    branch tree is identical to main's tree (the already-integrated / nothing-new
    state); when True the branch carries a divergent commit."""
    base.mkdir(parents=True, exist_ok=True)
    _git(base, "init", "-q", "-b", "main")
    _git(base, "config", "user.email", "t@example.com")
    _git(base, "config", "user.name", "Tester")
    (base / "f.txt").write_text("base\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "base")
    _git(base, "checkout", "-q", "-b", "ironclaude/wt")
    if new_work:
        (base / "f.txt").write_text("changed\n")
        _git(base, "add", "-A")
        _git(base, "commit", "-qm", "work")
    return base


def _make_conflicted_repo(base):
    """A real git checkout on branch ironclaude/wt left with an UNMERGED index
    (an unresolved merge conflict on f.txt), the same index shape a paused
    rebase leaves behind. `git write-tree` fails against this index exactly
    like it does mid-rebase, so `_worktree_has_new_work` raises."""
    base.mkdir(parents=True, exist_ok=True)
    _git(base, "init", "-q", "-b", "main")
    _git(base, "config", "user.email", "t@example.com")
    _git(base, "config", "user.name", "Tester")
    (base / "f.txt").write_text("base\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "base")
    _git(base, "checkout", "-q", "-b", "ironclaude/wt")
    (base / "f.txt").write_text("wt-change\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "wt work")
    _git(base, "checkout", "-q", "main")
    (base / "f.txt").write_text("main-change\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "main work")
    _git(base, "checkout", "-q", "ironclaude/wt")
    # Merge conflict, left unresolved on purpose (non-zero exit expected).
    subprocess.run(
        ["git", "merge", "main"], cwd=str(base),
        capture_output=True, text=True, check=False,
    )
    return base


def _make_behind_repo(base, *, staged):
    """Real checkout on ironclaude/wt whose HEAD is an ANCESTOR of an advanced
    integration target (main moved ahead with foreign work; the worktree never
    committed). The merely-behind / churn scenario: nothing of the worker's OWN
    contribution is missing from the target. When staged is True the worktree
    also carries a reviewed-but-uncommitted change in the INDEX — the normal
    finished-worker shape (work lives in the staged tree, HEAD still at the
    merge-base); that staged tree MUST register as new work."""
    base.mkdir(parents=True, exist_ok=True)
    _git(base, "init", "-q", "-b", "main")
    _git(base, "config", "user.email", "t@example.com")
    _git(base, "config", "user.name", "Tester")
    (base / "f.txt").write_text("base\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "base")
    _git(base, "checkout", "-q", "-b", "ironclaude/wt")
    # Advance main (the integration target) with foreign work; wt stays at base.
    _git(base, "checkout", "-q", "main")
    (base / "foreign.txt").write_text("foreign\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "foreign advance")
    _git(base, "checkout", "-q", "ironclaude/wt")
    if staged:
        (base / "f.txt").write_text("reviewed\n")
        _git(base, "add", "-A")  # staged, NOT committed
    return base


def _make_recycled_integrated_repo(base):
    """Real checkout modelling a post-recycle worker whose OWN contribution is
    ALREADY on the integration target, after which the target advanced with
    foreign work. HEAD is the integrated commit (an ancestor of the advanced
    target) with nothing staged: there is genuinely nothing new to integrate."""
    base.mkdir(parents=True, exist_ok=True)
    _git(base, "init", "-q", "-b", "main")
    _git(base, "config", "user.email", "t@example.com")
    _git(base, "config", "user.name", "Tester")
    (base / "f.txt").write_text("base\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "base")
    _git(base, "checkout", "-q", "-b", "ironclaude/wt")
    (base / "f.txt").write_text("worker work\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "worker work")
    # Integrate the worker's commit onto main (ff), then advance main foreign.
    _git(base, "checkout", "-q", "main")
    _git(base, "merge", "--ff-only", "ironclaude/wt")
    (base / "foreign.txt").write_text("foreign\n")
    _git(base, "add", "-A")
    _git(base, "commit", "-qm", "foreign advance")
    _git(base, "checkout", "-q", "ironclaude/wt")  # HEAD at integrated commit
    return base


def _worker(worktree, **overrides):
    worker = {
        "id": "w1",
        "client": "codex",
        "machine": None,
        "repo": "/repo",
        "native_session_id": _OWNER,
        "tmux_session": "ic-w1",
        "workspace_guid": _GUID,
        "workspace_repository_identity": "machine:repo.git",
        "workspace_path": str(worktree),
        "workspace_branch": "ironclaude/wt",
        "workspace_base_commit": "a" * 40,
        "workspace_integration_target": "main",
    }
    worker.update(overrides)
    return worker


def _make_tools(worker, *, gates_pass=True, has_session=False):
    tools = object.__new__(OrchestratorTools)
    tools.registry = MagicMock()
    tools.registry.get_worker.return_value = worker
    tools.registry.update_worker_status = MagicMock()
    tools.tmux = MagicMock()
    tools.tmux.has_session.return_value = has_session
    tools._ssh_manager = None
    tools._workspace_client = MagicMock()
    tools._workspace_client.discover_installed_plugin_root.return_value = "/installed"
    tools._workspace_client.finalize.return_value = {
        "state": "cleaned", "integratedCommit": "d" * 40,
    }
    tools._workspace_client.abandon.return_value = {"lifecycle_status": "abandoned"}
    if gates_pass:
        state = {
            "workflow_stage": "execution_complete",
            "unfinished_tasks": 0,
            "latest_task_boundary_grade": "A",
        }
    else:
        state = {
            "workflow_stage": "execution",
            "unfinished_tasks": 2,
            "latest_task_boundary_grade": "C",
        }
    tools._read_worker_finalization_state = MagicMock(return_value=state)
    tools._ensure_ssh_manager = MagicMock()
    tools._resolve_ssh_host = MagicMock(return_value=None)
    tools._slack = MagicMock()
    return tools


def _finalize_command(tools):
    return tools._workspace_client.finalize.call_args.args[0]["command"]


# --------------------------------------------------------------------------
# Step 1 — terminal integration / rescue / transient re-queue
# --------------------------------------------------------------------------

class TestTerminalDispatch:
    def test_terminal_gates_pass_new_work_finalizes_release(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        # Probe-first router: an active worktree ('not-ready') falls through to
        # the finalize path this test pins.
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["action"] == "integrated"
        tools._workspace_client.finalize.assert_called_once()
        command = _finalize_command(tools)
        assert command["dispose"] == "release"
        assert command["workspaceGuid"] == _GUID
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")

    def test_finalize_integrated_logged_on_finalize_call_success(self, tmp_path):
        # This seam's own successful finalize CALL (action=='integrated') is an
        # integrate path with no marker today: earlier finalize_failed rows
        # from prior cycles are left uncovered, which can trip a false
        # "failing repeatedly, try reopen_for_edit" alert on an
        # already-integrated worker. It must log finalize_integrated.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["action"] == "integrated"
        tools.registry.log_event.assert_any_call(
            "finalize_integrated", worker_id="w1",
        )

    def test_terminal_gates_fail_abandons_rescue_not_finalize(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), gates_pass=False)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["action"] == "rescued"
        tools._workspace_client.abandon.assert_called_once()
        payload = tools._workspace_client.abandon.call_args.args[0]
        assert payload["mode"] == "rescue"
        assert payload["workspace_guid"] == _GUID
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")

    def test_transient_finalize_error_requeues_worker_not_lost(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.finalize.side_effect = WorkspaceClientError("lock held")
        # A recoverable (frozen-drift) state — must route through the recovery
        # classifier, NOT abandon, and must NOT complete the worker. The router
        # status probe sees 'not-ready' (active) so the flow reaches finalize;
        # the finalize CALL then fails and the post-failure classifier probe
        # sees the frozen-drift state.
        tools._workspace_client.reconcile.side_effect = [
            {"state": "not-ready"},
            {"state": "frozen-no-rebase"},
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "finalization"
        tools.registry.update_worker_status.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()


# --------------------------------------------------------------------------
# Step 2 — idle-alive safety, nothing-new idempotence (the CRITICAL guard)
# --------------------------------------------------------------------------

class TestNothingNewAndLiveSessionGuards:
    def test_idle_gates_pass_new_work_recycles_keeps_dir(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["action"] == "integrated"
        command = _finalize_command(tools)
        assert command["dispose"] == "recycle"  # NOT release; worktree dir kept
        tools._workspace_client.abandon.assert_not_called()
        # Non-terminal: the worker continues — never marked completed.
        tools.registry.update_worker_status.assert_not_called()

    def test_second_idle_cycle_unchanged_tree_does_not_integrate(self, tmp_path):
        # new_work=False => worktree tree == integration target tree: the
        # already-integrated state that persists while Brain is unreachable.
        repo = _make_repo(tmp_path / "wt", new_work=False)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["action"] == "noop"
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
        # Nothing happened => no 'integrated as <sha>' notification.
        assert not tools._slack.post_message.called

    def test_terminal_downgrades_to_recycle_when_session_alive(self, tmp_path):
        # terminal=True but the tmux session is still alive (race): never delete
        # a live worker's cwd — downgrade to the non-terminal recycle behaviour.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        command = _finalize_command(tools)
        assert command["dispose"] == "recycle"
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_nonterminal_gates_fail_neither_finalize_nor_abandon(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), gates_pass=False, has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["action"] == "surfaced"
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_terminal_no_new_work_releases_dir_without_commit(self, tmp_path):
        # Dead session, gates pass, but the worktree tree already matches the
        # target (integrated on a prior cycle): remove the leftover dir via
        # abandon rescue — NEVER finalize (which would mint an empty commit).
        repo = _make_repo(tmp_path / "wt", new_work=False)
        tools = _make_tools(_worker(repo), has_session=False)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["action"] == "released"
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_called_once()
        assert tools._workspace_client.abandon.call_args.args[0]["mode"] == "rescue"
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")

    def test_worktree_has_new_work_detects_divergence_real_git(self, tmp_path):
        tools = object.__new__(OrchestratorTools)
        tools._ssh_manager = None
        diverged = _make_repo(tmp_path / "new", new_work=True)
        identical = _make_repo(tmp_path / "same", new_work=False)
        assert tools._worktree_has_new_work(str(diverged), "main", ssh_host=None) is True
        assert tools._worktree_has_new_work(str(identical), "main", ssh_host=None) is False


# --------------------------------------------------------------------------
# I-1 — a new-work probe failure (git write-tree against an unmerged index)
# is 'authority' ONLY when nothing on the worktree explains it. A genuinely
# mid-finalization worktree (paused/conflicted rebase) must route through the
# recovery classifier instead, so the outcome is failure_phase='finalization'
# (preserved + surfaced) and the worker is never silently completed.
# --------------------------------------------------------------------------

class TestUnmergedProbeMidFinalization:
    def test_unmerged_probe_mid_finalization_routes_to_finalization(self, tmp_path):
        repo = _make_conflicted_repo(tmp_path / "wt")
        tools = _make_tools(_worker(repo))
        # The status probe confirms a genuinely mid-finalization worktree (a
        # paused/conflicted rebase): the new-work probe failure must route
        # through the recovery classifier, not the authority path. The FIRST
        # reconcile is the router status probe — 'not-ready' so it falls through
        # to the gates and the has_new_work probe (which raises on the unmerged
        # index); the has_new_work probe and the classifier probe then both see
        # the conflicted state.
        tools._workspace_client.reconcile.side_effect = [
            {"state": "not-ready"},
            {"state": "rebase-paused-conflict"},
            {"state": "rebase-paused-conflict"},
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "finalization"
        tools.registry.update_worker_status.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
        tools._workspace_client.finalize.assert_not_called()

    def test_unmerged_probe_no_mid_finalization_state_stays_authority(self, tmp_path):
        repo = _make_conflicted_repo(tmp_path / "wt")
        tools = _make_tools(_worker(repo))
        # No recognizable mid-finalization state: a real environment error.
        # The probe failure must stay on the authority path. The router status
        # probe sees 'not-ready' (fall through); the has_new_work probe then
        # raises on the unmerged index and its probe returns an unrecognized
        # state => authority.
        tools._workspace_client.reconcile.side_effect = [
            {"state": "not-ready"},
            {"state": "clean"},
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "authority"
        tools.registry.update_worker_status.assert_not_called()

    def test_unmerged_probe_plugin_root_failure_stays_authority(self, tmp_path):
        # A genuine plugin-root / runtime discovery failure (not a new-work
        # probe failure) always stays 'authority', regardless of worktree
        # state — it never even reaches the new-work probe or the status probe.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.discover_installed_plugin_root.side_effect = (
            RuntimeError("plugin root not found")
        )
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "authority"
        tools.registry.update_worker_status.assert_not_called()
        tools._workspace_client.reconcile.assert_not_called()


# --------------------------------------------------------------------------
# R4 defense-in-depth — _abandon_rescue_worker refuses to abandon-rescue
# unless a FRESH non-mutating status probe (issued internally, independent of
# any router that called it) returns 'not-ready'. This guards against a
# caller that bypasses the probe-first router: without it, abandoning a
# worktree mid-finalization (integrated / paused-rebase / frozen-drift) would
# strand the repo-wide integration_locks row. Any state other than exactly
# 'not-ready' — including a raised/None probe — refuses, fail-closed.
# --------------------------------------------------------------------------

class TestAbandonRescueWorkerFailClosedGuard:
    def _call(self, tools, worker, *, already_integrated=False):
        assignment = OrchestratorTools._registry_workspace_assignment(worker)
        return tools._abandon_rescue_worker(
            "w1", worker["repo"], worker["client"], assignment, None,
            already_integrated=already_integrated,
        )

    @pytest.mark.parametrize(
        "state",
        ["integrated", "rebase-paused-clean", "rebase-paused-conflict", "frozen-no-rebase"],
    )
    def test_mid_finalization_state_refuses_abandon(self, tmp_path, state):
        worker = _worker(tmp_path / "wt")
        tools = _make_tools(worker)
        tools._workspace_client.reconcile.return_value = {"state": state}
        out = self._call(tools, worker)
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()
        assert out["failure_phase"] == "finalization"
        assert out["assignment_preserved"] is True

    def test_probe_raised_refuses_abandon_fail_closed(self, tmp_path):
        # The probe itself raising means the state is UNKNOWN — never abandon
        # against an unknown state.
        worker = _worker(tmp_path / "wt")
        tools = _make_tools(worker)
        tools._workspace_client.reconcile.side_effect = WorkspaceClientError(
            "probe down",
        )
        out = self._call(tools, worker)
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()
        assert out["failure_phase"] == "finalization"
        assert out["assignment_preserved"] is True

    def test_not_ready_allows_abandon_positive_control(self, tmp_path):
        # Positive control: a fresh probe confirming 'not-ready' is the ONLY
        # state that lets the guard fall through to the real abandon call.
        worker = _worker(tmp_path / "wt")
        tools = _make_tools(worker)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = self._call(tools, worker)
        tools._workspace_client.abandon.assert_called_once()
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")
        assert out["action"] == "rescued"


# --------------------------------------------------------------------------
# Step 3 — seam wiring (all four)
# --------------------------------------------------------------------------

def _daemon(tmp_path):
    from ironclaude.main import IroncladeDaemon
    config = {"tmp_dir": str(tmp_path)}
    slack = MagicMock()
    registry = MagicMock()
    tmux = MagicMock()
    tmux.log_dir = str(tmp_path / "logs")
    os.makedirs(tmux.log_dir, exist_ok=True)
    brain = MagicMock()
    d = IroncladeDaemon(config, slack, None, registry, tmux, brain)
    d._state_manager_db_path = str(tmp_path / "sm.db")
    return d


class TestSeamWiring:
    def test_session_died_seam_calls_terminal_true(self, tmp_path):
        d = _daemon(tmp_path)
        worker = {"id": "w2", "tmux_session": "ic-w2"}
        d.registry.get_running_workers.return_value = [worker]
        d.tmux.has_session.return_value = False
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = {"action": "integrated"}
        d._get_orchestrator = MagicMock(return_value=orch)
        d.check_workers()
        orch._finalize_and_release_worker.assert_called_once()
        call = orch._finalize_and_release_worker.call_args
        assert call.args[0] == "w2"
        assert call.kwargs["terminal"] is True
        # The daemon completes NOTHING (v1.1.6 Task 6): the orchestrator seam owns
        # completion. Here the seam is mocked, so the daemon must not complete.
        d.registry.update_worker_status.assert_not_called()

    def test_idle_marker_seam_calls_terminal_false(self, tmp_path):
        d = _daemon(tmp_path)
        worker = {"id": "w1", "tmux_session": "ic-w1"}
        d.registry.get_running_workers.return_value = [worker]
        marker = os.path.join(d.tmux.log_dir, "ic-w1.done")
        with open(marker, "w") as f:
            f.write("t")
        d.brain.send_message.return_value = True
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = {"action": "noop"}
        d._get_orchestrator = MagicMock(return_value=orch)
        d.check_workers()
        orch._finalize_and_release_worker.assert_called_once()
        assert orch._finalize_and_release_worker.call_args.kwargs["terminal"] is False

    def test_stuck_kill_seam_calls_terminal_true(self, tmp_path):
        d = _daemon(tmp_path)
        d.tmux.list_pane_pid.return_value = None  # skip liveness probe
        d._persist_staleness_state = MagicMock()
        orch = MagicMock()
        orch._finalize_and_release_worker.return_value = {"action": "integrated"}
        d._get_orchestrator = MagicMock(return_value=orch)
        d._confirm_and_kill_stuck_worker(
            "w5", "ic-w5", 1200.0, "execution", False, None,
        )
        orch._finalize_and_release_worker.assert_called_once()
        assert orch._finalize_and_release_worker.call_args.kwargs["terminal"] is True

    def test_kill_worker_reaches_release_path_not_bare_completed_flip(self, tmp_path):
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
        tools._finalize_and_release_worker = MagicMock(
            return_value={"action": "integrated"},
        )
        tools.kill_worker("w9")  # no objective/evidence => grader skipped
        tools._finalize_and_release_worker.assert_called_once()
        assert tools._finalize_and_release_worker.call_args.kwargs["terminal"] is True


# --------------------------------------------------------------------------
# Step 4 — drive_frozen_reconcile_recovery: PLAIN reconcile only (no
# rebase_recovery key, no rerebase fallback — Task 3 deleted it), no commit
# ever minted, worker never abandoned.
# --------------------------------------------------------------------------

class TestDriftPlainReconcile:
    def test_drift_plain_reconcile_no_rebase_recovery_key_first(self, tmp_path):
        """A frozen drift is recovered by a PLAIN reconcile ONLY (no rebase_recovery
        key), even when the plain reconcile does not reach an integrated state. The
        daemon rerebase fallback is gone: exactly one reconcile call, no commit
        minted (finalize never called), worker never abandoned."""
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        # Plain reconcile leaves the worktree frozen — there is no rerebase replay.
        tools._workspace_client.reconcile.return_value = {
            "state": "rebase-recovery-repair-required"
        }
        tools._abandon_rescue_worker = MagicMock()

        result = tools.drive_frozen_reconcile_recovery("w1")

        assert result["state"] == "rebase-recovery-repair-required"
        assert tools._workspace_client.reconcile.call_count == 1
        first_payload = tools._workspace_client.reconcile.call_args_list[0].args[0]
        assert "rebase_recovery" not in first_payload
        assert first_payload["workspace_guid"] == _GUID
        assert first_payload["owner_session_id"] == _OWNER
        # No commit minted, worker never abandoned, no completion (non-integrated).
        tools._workspace_client.finalize.assert_not_called()
        tools._abandon_rescue_worker.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_drift_plain_reconcile_integrates_no_rerebase_fallback(self, tmp_path):
        """When the PLAIN reconcile already reaches an integrated state, the seam
        returns it WITHOUT issuing the rerebase fallback (exactly one reconcile)."""
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.return_value = {"state": "integrated-local"}
        tools._abandon_rescue_worker = MagicMock()

        result = tools.drive_frozen_reconcile_recovery("w1")

        assert result["state"] == "integrated-local"
        tools._workspace_client.reconcile.assert_called_once()
        only_payload = tools._workspace_client.reconcile.call_args.args[0]
        assert "rebase_recovery" not in only_payload
        tools._workspace_client.finalize.assert_not_called()
        tools._abandon_rescue_worker.assert_not_called()

    def test_drift_seam_completes_worker_when_session_dead(self, tmp_path):
        """The seam OWNS drift-success completion: an integrated plain-reconcile
        result marks the worker completed once its tmux session is confirmed
        dead."""
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=False)
        tools._workspace_client.reconcile.return_value = {"state": "cleaned"}

        result = tools.drive_frozen_reconcile_recovery("w1")

        assert result["state"] == "cleaned"
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")

    def test_drift_seam_logs_finalize_integrated_on_integrated_result(
        self, tmp_path,
    ):
        # This seam owns drift-success completion but logs no marker today —
        # a worker recovered from frozen drift can carry earlier
        # finalize_failed rows the since-marker count never clears. It must
        # log finalize_integrated whenever the plain reconcile reaches an
        # integrated state (independent of session liveness/completion).
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=False)
        tools._workspace_client.reconcile.return_value = {"state": "cleaned"}

        result = tools.drive_frozen_reconcile_recovery("w1")

        assert result["state"] == "cleaned"
        tools.registry.log_event.assert_any_call(
            "finalize_integrated", worker_id="w1",
        )

    def test_drift_seam_does_not_complete_worker_when_session_alive(self, tmp_path):
        """A live worker is integrated but NEVER completed here — completing it
        would drop a still-running worker off monitoring."""
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "cleaned"}

        result = tools.drive_frozen_reconcile_recovery("w1")

        assert result["state"] == "cleaned"
        tools.registry.update_worker_status.assert_not_called()

    def test_drift_seam_never_completes_on_non_integrated_result(self, tmp_path):
        """A non-integrated reconcile result never triggers completion, regardless
        of session liveness."""
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=False)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}

        result = tools.drive_frozen_reconcile_recovery("w1")

        assert result["state"] == "not-ready"
        tools.registry.update_worker_status.assert_not_called()


# --------------------------------------------------------------------------
# I-2 Part A — _worktree_has_new_work is CONTRIBUTION-relative: it compares the
# worktree's STAGED index tree against the worktree's OWN merge-base with the
# integration target, NOT against the target tree. Worker output is a staged
# index tree (see _derive_workspace_commit_evidence: stagedTree=`git write-tree`,
# parentOid=HEAD), so a normal finished worker sits at (an ancestor of) the
# target with reviewed work only in the INDEX. A target-relative predicate
# churns a merely-behind worker; a naive `rev-list <target>..HEAD` fix would
# report EMPTY for a staged-only worker and DROP its reviewed work off-main.
# --------------------------------------------------------------------------

class TestContributionRelativeNewWork:
    def _pred(self, path):
        tools = object.__new__(OrchestratorTools)
        tools._ssh_manager = None
        return tools._worktree_has_new_work(str(path), "main", ssh_host=None)

    def test_contribution_relative_merely_behind_is_not_new_work(self, tmp_path):
        # HEAD is an ancestor of an advanced target, nothing staged: no new
        # contribution. A target-relative predicate returns True here and churns.
        repo = _make_behind_repo(tmp_path / "wt", staged=False)
        assert self._pred(repo) is False

    def test_contribution_relative_staged_only_uncommitted_is_new_work(self, tmp_path):
        # THE critical case: a normal finished worker's reviewed work lives only
        # in the staged index tree (HEAD still at the merge-base, behind an
        # advanced target). It MUST register as new work — returning False would
        # drop reviewed work off-main (never-lose-work violation).
        repo = _make_behind_repo(tmp_path / "wt", staged=True)
        assert self._pred(repo) is True

    def test_contribution_relative_committed_ahead_is_new_work(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        assert self._pred(repo) is True

    def test_contribution_relative_post_recycle_integrated_is_not_new_work(self, tmp_path):
        # Post-recycle: the worker's own commit is already on the target and the
        # target advanced foreign; HEAD is an ancestor, nothing staged => nothing
        # new. Target-relative returns True (churn); contribution-relative False.
        repo = _make_recycled_integrated_repo(tmp_path / "wt")
        assert self._pred(repo) is False


# --------------------------------------------------------------------------
# I-2 Part B (R4) — a merely-behind, gates-pass, still-ALIVE idle worker is
# ff-SYNCED (not finalized: a finalize would mint an empty commit; an abandon
# would drop the dir). The non-terminal no-new-work arm issues the SAME
# workspace-manager sync the periodic sweep uses and returns action 'synced'.
# A sync failure surfaces and leaves the worker running — never abandons, never
# mints a commit. When HEAD already IS the target head there is nothing to sync
# (true no-op).
# --------------------------------------------------------------------------

class TestIdleBehindSynced:
    def test_idle_behind_synced_alive_syncs_not_finalizes(self, tmp_path):
        repo = _make_behind_repo(tmp_path / "wt", staged=False)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["action"] == "synced"
        tools._workspace_client.sync.assert_called_once()
        payload = tools._workspace_client.sync.call_args.args[0]
        assert payload == {
            "repository_path": "/repo",
            "workspace_guid": _GUID,
            "owner_session_id": _OWNER,
        }
        assert tools._workspace_client.sync.call_args.kwargs == {
            "plugin_root": "/installed",
        }
        # (e) no empty commit minted on a foreign target-advance; dir kept alive.
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_idle_behind_synced_sync_failure_surfaces_never_abandons(self, tmp_path):
        repo = _make_behind_repo(tmp_path / "wt", staged=False)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        tools._workspace_client.sync.side_effect = WorkspaceClientError("sync busy")
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["action"] == "surfaced"
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_idle_behind_synced_at_target_is_noop_not_synced(self, tmp_path):
        # HEAD already IS the target head, nothing staged: nothing to ff-sync.
        repo = _make_repo(tmp_path / "wt", new_work=False)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["action"] == "noop"
        tools._workspace_client.sync.assert_not_called()
        tools._workspace_client.finalize.assert_not_called()
        tools._workspace_client.abandon.assert_not_called()


# --------------------------------------------------------------------------
# CORE FIX — probe-first router: the NON-MUTATING status probe runs BEFORE any
# mint-capable finalize, so finalize is reachable ONLY when the worktree is
# 'not-ready' (active). Every mid-finalization state (frozen drift, paused
# rebase, already-integrated) and every probe failure/unknown state is routed
# WITHOUT minting an empty commit. This is what stops a frozen/drifted worker
# minting an empty commit every cycle.
# --------------------------------------------------------------------------

class TestProbeFirstRouter:
    def test_probe_not_ready_falls_through_to_finalize(self, tmp_path):
        # Positive control: an active ('not-ready') worker with new work is the
        # ONLY path that reaches finalize. The probe ran FIRST (status) and
        # precedes the finalize.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.return_value = {"state": "not-ready"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["action"] == "integrated"
        tools._workspace_client.finalize.assert_called_once()
        first = tools._workspace_client.reconcile.call_args_list[0].args[0]
        assert first["rebase_recovery"] == "status"
        methods = [c[0] for c in tools._workspace_client.method_calls]
        assert methods.index("reconcile") < methods.index("finalize")

    def test_probe_frozen_no_rebase_never_finalizes_tags_drift(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.return_value = {"state": "frozen-no-rebase"}
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "finalization"
        assert out["recovery"]["reconcile"]["mode"] == "drift"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_probe_rebase_paused_conflict_never_finalizes_tags_conflict(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.return_value = {
            "state": "rebase-paused-conflict",
        }
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "finalization"
        assert out["recovery"]["reconcile"]["mode"] == "conflict"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_probe_rebase_paused_clean_drives_continue_completes(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        # Router status probe => paused-clean; the driven 'continue' integrates.
        tools._workspace_client.reconcile.side_effect = [
            {"state": "rebase-paused-clean"},
            {"state": "cleaned"},
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "cleaned"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")
        first = tools._workspace_client.reconcile.call_args_list[0].args[0]
        assert first["rebase_recovery"] == "status"
        second = tools._workspace_client.reconcile.call_args_list[1].args[0]
        assert second["rebase_recovery"] == "continue"

    def test_probe_integrated_completes_and_cleans_never_finalizes(self, tmp_path):
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.side_effect = [
            {"state": "integrated"},  # router status probe
            {"state": "cleaned"},     # _trigger_integrated_cleanup plain reconcile
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "integrated"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")
        # The deferred cleanup reconcile is PLAIN (no rebase_recovery key).
        cleanup = tools._workspace_client.reconcile.call_args_list[1].args[0]
        assert "rebase_recovery" not in cleanup

    def test_probe_integrated_live_session_never_completes_but_still_cleans(
        self, tmp_path,
    ):
        # SAFETY GATE: the status probe finds 'integrated' but the worker's
        # tmux session is STILL ALIVE (a false-positive / racing probe). The
        # worker must NOT be completed, but the deferred cleanup still runs
        # and the probe status is still returned — only the completion is
        # gated on confirmed-dead.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.side_effect = [
            {"state": "integrated"},  # router status probe
            {"state": "cleaned"},     # _trigger_integrated_cleanup plain reconcile
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "integrated"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()
        cleanup = tools._workspace_client.reconcile.call_args_list[1].args[0]
        assert "rebase_recovery" not in cleanup

    def test_probe_rebase_paused_clean_live_session_never_completes(self, tmp_path):
        # Same safety gate through the _drive_continue_recovery path: the
        # continue reaches a terminal integrated state, but the session is
        # still alive, so completion must not happen even though the
        # 'cleaned' result is still returned.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=True)
        tools._workspace_client.reconcile.side_effect = [
            {"state": "rebase-paused-clean"},
            {"state": "cleaned"},
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "cleaned"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_not_called()

    def test_probe_rebase_paused_clean_dead_session_completes(self, tmp_path):
        # Confirmed-dead control for the pair above: same continue path, but
        # the session is confirmed dead, so completion DOES happen.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo), has_session=False)
        tools._workspace_client.reconcile.side_effect = [
            {"state": "rebase-paused-clean"},
            {"state": "cleaned"},
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "cleaned"
        tools._workspace_client.finalize.assert_not_called()
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")

    def test_probe_raised_none_never_finalizes_tags_probe(self, tmp_path):
        # The probe itself raised: state is unknown => NEVER finalize. Preserve
        # the assignment and surface with the 'probe' tag (distinct from the
        # mid-finalization 'finalization' states).
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.side_effect = WorkspaceClientError(
            "probe boom",
        )
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["failure_phase"] == "probe"
        assert out["action"] == "surfaced"
        assert out["assignment_preserved"] is True
        tools._workspace_client.finalize.assert_not_called()

    def test_probe_integrated_resighting_does_not_log_finalize_integrated(
        self, tmp_path,
    ):
        # This router branch (state == 'integrated' on the FIRST, non-mutating
        # status probe) is a RE-SIGHTING of an already-landed row — reached
        # every idle cycle while a .done marker persists, not a fresh
        # integrate. It must NOT log finalize_integrated; the exception/
        # paused-clean seams (_classify_finalization_failure /
        # _drive_continue_recovery) already cover a genuine integrate here,
        # and double-logging would defeat the since-marker failure count.
        repo = _make_repo(tmp_path / "wt", new_work=True)
        tools = _make_tools(_worker(repo))
        tools._workspace_client.reconcile.side_effect = [
            {"state": "integrated"},  # router status probe
            {"state": "cleaned"},     # _trigger_integrated_cleanup plain reconcile
        ]
        out = tools._finalize_and_release_worker("w1", "session ended", terminal=True)
        assert out["state"] == "integrated"
        finalize_integrated_calls = [
            c for c in tools.registry.log_event.call_args_list
            if c.args and c.args[0] == "finalize_integrated"
        ]
        assert finalize_integrated_calls == []


# --------------------------------------------------------------------------
# I1 — over-completion fix. Two invariants:
#  (I-A) A dead UNMANAGED worker (no workspace_guid) completes INSIDE the seam,
#        but ONLY when terminal AND its session is confirmed dead. A live or
#        non-terminal unmanaged worker is surfaced (preserved), never completed.
#  (C-B/I-B) kill_worker completes NOTHING itself and uses a POSITIVE predicate:
#        it marks the worker completed + logs worker_finished ONLY when the seam
#        returns a genuine success (a dict with NO failure_phase). A managed
#        worker returning a transient/preserved failure (authority/probe/abandon
#        or None) is NEVER completed.
# --------------------------------------------------------------------------

def _unmanaged_kill_tools(*, has_session, spawned_at="2026-01-01T00:00:00+00:00"):
    """Minimal tools running the REAL unmanaged carve-out + REAL kill_worker
    predicate (nothing on the finalize seam is mocked)."""
    tools = object.__new__(OrchestratorTools)
    tools.registry = MagicMock()
    tools.registry.get_worker.return_value = {
        "id": "w9", "machine": None, "tmux_session": "ic-w9",
        "workspace_guid": None, "spawned_at": spawned_at,
    }
    tools.registry.update_worker_status = MagicMock()
    tools.registry.log_event = MagicMock()
    tools.tmux = MagicMock()
    tools.tmux.list_pane_pid.return_value = "123"
    tools.tmux.has_session.return_value = has_session
    tools._ensure_ssh_manager = MagicMock()
    tools._resolve_ssh_host = MagicMock(return_value=None)
    tools._db = None
    tools._get_remaining_work_after_kill = MagicMock(return_value={})
    return tools


class TestUnmanagedSeamCompletion:
    def test_unmanaged_terminal_dead_session_completes_no_failure_phase(self, tmp_path):
        # (a) UNMANAGED + terminal + session DEAD => seam completes it and
        # returns action 'completed' with NO failure_phase key.
        repo = _make_repo(tmp_path / "wt", new_work=False)
        tools = _make_tools(_worker(repo, workspace_guid=None), has_session=False)
        out = tools._finalize_and_release_worker("w1", "killed", terminal=True)
        assert out["action"] == "completed"
        assert "failure_phase" not in out
        tools.registry.update_worker_status.assert_called_once_with("w1", "completed")
        # Never reached the managed assignment/probe/finalize path.
        tools._workspace_client.finalize.assert_not_called()

    def test_unmanaged_terminal_live_session_surfaced_not_completed(self, tmp_path):
        # (b1) UNMANAGED + terminal but session ALIVE => preserved, never
        # completed (never drop a still-running worker off monitoring).
        repo = _make_repo(tmp_path / "wt", new_work=False)
        tools = _make_tools(_worker(repo, workspace_guid=None), has_session=True)
        out = tools._finalize_and_release_worker("w1", "killed", terminal=True)
        assert out["failure_phase"] == "authority"
        assert out["action"] == "surfaced"
        tools.registry.update_worker_status.assert_not_called()

    def test_unmanaged_non_terminal_surfaced_not_completed(self, tmp_path):
        # (b2) UNMANAGED + non-terminal => preserved, never completed.
        repo = _make_repo(tmp_path / "wt", new_work=False)
        tools = _make_tools(_worker(repo, workspace_guid=None), has_session=False)
        out = tools._finalize_and_release_worker("w1", "idle", terminal=False)
        assert out["failure_phase"] == "authority"
        assert out["action"] == "surfaced"
        tools.registry.update_worker_status.assert_not_called()


class TestKillWorkerPositivePredicate:
    @pytest.mark.parametrize("preserved", [
        {"failure_phase": "authority", "assignment_preserved": True},
        {"failure_phase": "probe", "assignment_preserved": True},
        {"failure_phase": "abandon", "assignment_preserved": True},
        {"failure_phase": "finalization", "assignment_preserved": True},
        None,
    ])
    def test_kill_worker_managed_transient_failure_not_completed(self, preserved):
        # (c) A managed worker whose seam returns a preserved transient failure
        # (or None) is NEVER completed by kill_worker: no completed flip, no
        # worker_finished log, and the status string says NOT completed.
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
        tools._finalize_and_release_worker = MagicMock(return_value=preserved)

        result = tools.kill_worker("w9")  # no objective/evidence => grader skipped

        tools.registry.update_worker_status.assert_not_called()
        finished = [
            c for c in tools.registry.log_event.call_args_list
            if c.args and c.args[0] == "worker_finished"
        ]
        assert finished == []
        assert "marked completed" not in result["status"]

    def test_kill_worker_unmanaged_dead_completes_and_logs_finished(self):
        # (d) POSITIVE control: an UNMANAGED dead worker taken through the REAL
        # seam by kill_worker completes, logs worker_finished, and its status
        # says 'marked completed'.
        tools = _unmanaged_kill_tools(has_session=False)

        result = tools.kill_worker("w9")  # no objective/evidence => grader skipped

        tools.registry.update_worker_status.assert_called_once_with("w9", "completed")
        finished = [
            c for c in tools.registry.log_event.call_args_list
            if c.args and c.args[0] == "worker_finished"
        ]
        assert len(finished) == 1
        assert "marked completed" in result["status"]
