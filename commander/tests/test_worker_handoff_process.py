import json
import os
import signal

import pytest

from ironclaude.worker_handoff_process import NativeProcessAdapter, ProcessProofError


class _Ps:
    class Error(Exception):
        pass

    class NoSuchProcess(Error):
        pass

    class AccessDenied(Error):
        pass

    def __init__(self, processes):
        self.processes = {proc.pid: proc for proc in processes}

    def Process(self, pid):
        if pid == os.getpid():
            return _Proc(pid, ["pytest"], "/private/tmp")
        if pid not in self.processes or self.processes[pid].gone:
            raise self.NoSuchProcess(pid)
        return self.processes[pid]

    def process_iter(self):
        return [proc for proc in self.processes.values() if not proc.gone]


class _Proc:
    def __init__(self, pid, cmdline, cwd, *, created=1.0, parent=None, uid=501):
        self.pid, self._cmdline, self._cwd = pid, cmdline, str(cwd)
        self._created, self.parent, self._uid, self.gone = created, parent, uid, False
        self.signals = []

    def children(self, recursive=True):
        result = [proc for proc in PROCS if proc.parent is self and not proc.gone]
        if recursive:
            for proc in list(result):
                result.extend(proc.children())
        return result

    def parents(self):
        return []

    def name(self):
        return "zsh"

    def cmdline(self):
        return self._cmdline

    def create_time(self):
        return self._created

    def ppid(self):
        return self.parent.pid if self.parent else 1

    def cwd(self):
        return self._cwd

    def uids(self):
        return type("U", (), {"real": self._uid})()

    def open_files(self):
        return []

    def send_signal(self, value):
        self.signals.append(value)
        self.gone = True


class _Tmux:
    def __init__(self, pid):
        self.pid = pid

    def list_pane_pid(self, name, ssh_host=None):
        return self.pid


PROCS = []


def _adapter(tmp_path, client="claude"):
    global PROCS
    root = _Proc(10001, ["zsh"], tmp_path)
    child = _Proc(10002, [client] + (["--no-daemon"] if client == "codex" else []), tmp_path, parent=root)
    PROCS = [root, child]
    return NativeProcessAdapter(_Tmux(root.pid), psutil_module=_Ps(PROCS)), root, child


def test_snapshot_records_full_owned_tree(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    snapshot = adapter.snapshot("ic-1", tmp_path, "claude")
    assert snapshot["producer_pids"] == [child.pid]
    assert {row["pid"] for row in snapshot["processes"]} == {root.pid, child.pid}


def test_codex_requires_standalone_no_daemon_producer(tmp_path):
    adapter, root, child = _adapter(tmp_path, "codex")
    child._cmdline = ["codex"]
    with pytest.raises(ProcessProofError, match="--no-daemon"):
        adapter.snapshot("ic-1", tmp_path, "codex")


def test_text_mention_does_not_spoof_native_cli(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    child._cmdline = ["python", "echo claude"]
    with pytest.raises(ProcessProofError, match="native producer"):
        adapter.snapshot("ic-1", tmp_path, "claude")


def test_pid_reuse_is_not_signalled(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    snapshot = adapter.snapshot("ic-1", tmp_path, "claude")
    child._created = 9.0
    with pytest.raises(ProcessProofError, match="reused"):
        adapter.stop_and_prove(snapshot)
    assert child.signals == []
    assert root.signals == []


def test_unowned_reparented_workspace_process_blocks(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    rogue = _Proc(10003, ["python"], tmp_path)
    PROCS.append(rogue)
    adapter._psutil.processes[rogue.pid] = rogue
    with pytest.raises(ProcessProofError, match="unowned workspace"):
        adapter.snapshot("ic-1", tmp_path, "claude")


def test_disappeared_pane_is_not_stop_proof(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    root.gone = True
    with pytest.raises(ProcessProofError, match="pane process unavailable"):
        adapter.snapshot("ic-1", tmp_path, "claude")


def test_controller_pid_cannot_be_source_root(tmp_path, monkeypatch):
    adapter, root, child = _adapter(tmp_path)
    monkeypatch.setattr("ironclaude.worker_handoff_process.os.getpid", lambda: root.pid)
    with pytest.raises(ProcessProofError, match="controller"):
        adapter.snapshot("ic-1", tmp_path, "claude")


def test_remote_refuses_local_proof(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    with pytest.raises(ProcessProofError, match="unsupported"):
        adapter.snapshot("ic-1", tmp_path, "claude", ssh_host="remote")


def test_inaccessible_current_user_process_blocks_quiescence(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    root._cwd = child._cwd = "/private/tmp"
    inaccessible = _Proc(10003, ["writer"], tmp_path, uid=os.getuid())
    inaccessible.cwd = lambda: (_ for _ in ()).throw(adapter._psutil.AccessDenied())
    PROCS.append(inaccessible)
    adapter._psutil.processes[inaccessible.pid] = inaccessible
    with pytest.raises(ProcessProofError, match="cannot be inspected"):
        adapter.assert_quiescent(tmp_path)


def test_new_workspace_child_after_snapshot_blocks_before_kill(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    snapshot = adapter.snapshot("ic-1", tmp_path, "claude")
    newcomer = _Proc(10003, ["writer"], tmp_path, parent=root)
    PROCS.append(newcomer)
    adapter._psutil.processes[newcomer.pid] = newcomer
    with pytest.raises(ProcessProofError, match="topology changed"):
        adapter.stop_and_prove(snapshot)
    assert root.signals == []
    assert child.signals == []


def test_source_pid_cannot_be_allowed_as_bootstrap(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    adapter.snapshot("ic-1", tmp_path, "claude")
    with pytest.raises(ProcessProofError, match="source process"):
        adapter.assert_quiescent(tmp_path, allowed_pids=[child.pid])


def test_actual_readonly_bootstrap_identity_is_allowed(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    child._cmdline = ["claude", "--permission-mode", "plan", "--tools", ""]
    snapshot = adapter.snapshot("ic-1", tmp_path, "claude")
    assert snapshot["readonly_bootstrap"] is True
    assert adapter.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])["quiescent"]


def test_codex_readonly_snapshot_allows_full_owned_tree(tmp_path):
    adapter, root, child = _adapter(tmp_path, "codex")
    child._cmdline = ["codex", "--no-daemon", "--sandbox", "read-only"]
    snapshot = adapter.snapshot("ic-1", tmp_path, "codex")
    assert snapshot["readonly_bootstrap"] is True
    assert adapter.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])["quiescent"]


def test_proved_absent_accepts_gone_tree_without_worktree(tmp_path):
    import shutil
    worktree = tmp_path / "wt"
    worktree.mkdir()
    adapter, root, child = _adapter(worktree)
    snapshot = adapter.snapshot("ic-1-h1-bootstrap", worktree, "claude")
    root.gone = child.gone = True
    shutil.rmtree(worktree)
    with pytest.raises(FileNotFoundError):
        adapter.verify_stopped(snapshot)
    assert adapter.proved_absent(snapshot) is True


def test_proved_absent_refuses_live_or_unreadable_identity(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    snapshot = adapter.snapshot("ic-1-h1-bootstrap", tmp_path, "claude")
    assert adapter.proved_absent(snapshot) is False
    root.gone = True
    original = adapter._psutil.Process
    adapter._psutil.Process = lambda pid: ((_ for _ in ()).throw(adapter._psutil.AccessDenied(pid))
                                           if pid == child.pid else original(pid))
    with pytest.raises(ProcessProofError):
        adapter.proved_absent(snapshot)


def test_restarted_adapter_adopts_recorded_readonly_bootstrap(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    child._cmdline = ["claude", "--permission-mode", "plan", "--tools", ""]
    snapshot = json.loads(json.dumps(adapter.snapshot("ic-1-h1-bootstrap", tmp_path, "claude")))
    restarted = NativeProcessAdapter(_Tmux(root.pid), psutil_module=adapter._psutil)
    with pytest.raises(ProcessProofError, match="readonly native ownership"):
        restarted.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])
    restarted.adopt_readonly_bootstrap(snapshot)
    assert restarted.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])["quiescent"]


@pytest.mark.parametrize("change", ["reused_pid", "writable_producer", "exited"])
def test_restarted_adapter_refuses_unproved_bootstrap_identity(tmp_path, change):
    adapter, root, child = _adapter(tmp_path)
    child._cmdline = ["claude", "--permission-mode", "plan", "--tools", ""]
    snapshot = json.loads(json.dumps(adapter.snapshot("ic-1-h1-bootstrap", tmp_path, "claude")))
    restarted = NativeProcessAdapter(_Tmux(root.pid), psutil_module=adapter._psutil)
    if change == "reused_pid":
        child._created = 9.0
    elif change == "writable_producer":
        snapshot["readonly_bootstrap"] = False
    else:
        child.gone = True
    with pytest.raises(ProcessProofError):
        restarted.adopt_readonly_bootstrap(snapshot)
    with pytest.raises(ProcessProofError):
        restarted.assert_quiescent(tmp_path, allowed_pids=[root.pid, child.pid])


def test_stop_signals_only_recorded_owned_tree_then_proves(tmp_path):
    adapter, root, child = _adapter(tmp_path)
    snapshot = adapter.snapshot("ic-1", tmp_path, "claude")
    proof = adapter.stop_and_prove(snapshot)
    assert proof["stopped"] is True
    assert root.signals == [signal.SIGTERM]
    assert child.signals == [signal.SIGTERM]
