"""Process identity proofs used before a native worker handoff."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import time
from typing import Any

import psutil


class ProcessProofError(RuntimeError):
    """Process state cannot prove a source worker has stopped."""


class NativeProcessAdapter:
    """Stop only a recorded native-client process tree after identity checks."""

    def __init__(self, tmux, ssh_manager=None, *, psutil_module=psutil):
        self._tmux = tmux
        self._ssh_manager = ssh_manager
        self._psutil = psutil_module
        self._source_identities: set[tuple[int, float]] = set()
        self._readonly_leases: list[dict] = []

    def snapshot(self, session_name, workspace_path, client, *, ssh_host=None) -> dict:
        workspace = self._workspace(workspace_path)
        self._remote_guard(ssh_host)
        if client not in {"claude", "codex"}:
            raise ProcessProofError("unsupported native client")
        pane_pid = self._tmux.list_pane_pid(session_name, ssh_host=ssh_host)
        if not pane_pid:
            raise ProcessProofError("tmux pane PID unavailable; tmux disappearance is not process proof")
        try:
            root = self._psutil.Process(int(pane_pid))
        except (ValueError, self._psutil.NoSuchProcess) as exc:
            raise ProcessProofError("tmux pane process unavailable") from exc
        self._refuse_unsafe_root(root)
        processes = self._tree(root)
        producer = [proc for proc in processes if self._is_producer(proc, client)]
        if client == "codex" and not producer:
            raise ProcessProofError("Codex source needs a standalone --no-daemon producer")
        if client == "claude" and not producer:
            raise ProcessProofError("Claude source needs a native producer in pane tree")
        identities = [self._identity(proc) for proc in processes]
        self.assert_quiescent(workspace, allowed_pids={item["pid"] for item in identities}, ssh_host=ssh_host, _source_lease=True)
        readonly = any(self._is_readonly_bootstrap(proc) for proc in producer)
        if readonly:
            self._readonly_leases.append({"client": client, "pane_pid": int(pane_pid), "identities": {(item["pid"], item["creation_time"]) for item in identities}})
        else:
            self._source_identities.update((item["pid"], item["creation_time"]) for item in identities)
        return {
            "session_name": str(session_name),
            "workspace_path": str(workspace),
            "client": client,
            "pane_pid": int(pane_pid),
            "processes": identities,
            "producer_pids": [proc.pid for proc in producer],
            "readonly_bootstrap": readonly,
            "ssh_host": ssh_host,
        }

    def stop_and_prove(self, snapshot, *, ssh_host=None) -> dict:
        self._validate_snapshot(snapshot, ssh_host)
        workspace = self._workspace(snapshot["workspace_path"])
        self._assert_snapshot_identities_current(snapshot)
        self._assert_tree_unchanged(snapshot)
        self.assert_quiescent(workspace, allowed_pids=(), ssh_host=ssh_host, _ignore={item["pid"] for item in snapshot["processes"]}, _source_lease=True)
        for item in reversed(snapshot["processes"]):
            proc = self._same_process(item)
            if proc is not None:
                proc.send_signal(signal.SIGTERM)
        for _ in range(3):
            if all(self._same_process(item) is None for item in snapshot["processes"]):
                return self.verify_stopped(snapshot, ssh_host=ssh_host)
            time.sleep(0.05)
        for item in reversed(snapshot["processes"]):
            proc = self._same_process(item)
            if proc is not None:
                proc.send_signal(signal.SIGKILL)
        for _ in range(20):
            if all(self._same_process(item) is None for item in snapshot["processes"]):
                break
            time.sleep(0.05)
        return self.verify_stopped(snapshot, ssh_host=ssh_host)

    def proved_absent(self, snapshot) -> bool:
        """H7b: prove every recorded process gone by PID plus start time, without the worktree.

        Used only when the worktree directory no longer exists, so verify_stopped's
        workspace resolution cannot run. AccessDenied or an unreadable identity raises.
        """
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("processes"), list) or not snapshot["processes"]:
            raise ProcessProofError("invalid process snapshot")
        required = {"pid", "creation_time", "ppid", "cwd", "uid", "cmdline"}
        for row in snapshot["processes"]:
            if (not isinstance(row, dict) or set(row) != required or not isinstance(row["pid"], int)
                    or not isinstance(row["creation_time"], (int, float))):
                raise ProcessProofError("invalid process identity snapshot")
        self._remote_guard(snapshot.get("ssh_host"))
        return all(self._same_process(item) is None for item in snapshot["processes"])

    def verify_stopped(self, snapshot, *, ssh_host=None) -> dict:
        self._validate_snapshot(snapshot, ssh_host)
        live = [item["pid"] for item in snapshot["processes"] if self._same_process(item) is not None]
        if live:
            raise ProcessProofError("recorded source processes remain: " + ",".join(map(str, live)))
        self._clear_absent_source_identities()
        quiescent = self.assert_quiescent(snapshot["workspace_path"], ssh_host=ssh_host)
        return {"stopped": True, "source_pids": [item["pid"] for item in snapshot["processes"]], "quiescent": quiescent}

    def adopt_readonly_bootstrap(self, snapshot, *, ssh_host=None) -> None:
        """Rebuild a read-only bootstrap lease from persisted identity after a restart (H8).

        Re-proves that every recorded PID still has its recorded start time, that the
        pane tree is unchanged, and that its producer is still the read-only bootstrap.
        """
        self._validate_snapshot(snapshot, ssh_host)
        if snapshot.get("readonly_bootstrap") is not True:
            raise ProcessProofError("recorded bootstrap is not read-only")
        if any(self._same_process(item) is None for item in snapshot["processes"]):
            raise ProcessProofError("recorded bootstrap process exited or PID was reused")
        identities = {(item["pid"], item["creation_time"]) for item in snapshot["processes"]}
        lease = {"client": snapshot["client"], "pane_pid": snapshot["pane_pid"], "identities": identities}
        if lease not in self._readonly_leases:
            self._readonly_leases.append(lease)
        if not self._readonly_identity_is_live(next(iter(identities))):
            self._readonly_leases.remove(lease)
            raise ProcessProofError("recorded bootstrap no longer proves read-only ownership")

    def assert_quiescent(self, workspace_path, *, allowed_pids=(), ssh_host=None, _ignore=frozenset(), _source_lease=False) -> dict:
        workspace = self._workspace(workspace_path)
        self._remote_guard(ssh_host)
        allowed = {int(pid) for pid in allowed_pids} | set(_ignore)
        if not _source_lease:
            for pid in allowed:
                try:
                    proc = self._psutil.Process(pid)
                except self._psutil.NoSuchProcess:
                    continue
                identity = (pid, proc.create_time())
                if identity in self._source_identities:
                    raise ProcessProofError("source process cannot be allowed bootstrap process")
                if not self._readonly_identity_is_live(identity):
                    raise ProcessProofError("allowed bootstrap process lacks readonly native ownership proof")
        associated: list[int] = []
        for proc in self._all_processes():
            pid = proc.pid
            if pid in allowed:
                continue
            try:
                if self._associated(proc, workspace):
                    associated.append(pid)
            except self._psutil.AccessDenied:
                uid = self._uid(proc)
                if uid == os.getuid():
                    raise ProcessProofError(f"current-user process {pid} cannot be inspected")
                if uid is None:
                    raise ProcessProofError(f"process {pid} identity cannot be inspected")
        if associated:
            raise ProcessProofError("unowned workspace processes: " + ",".join(map(str, associated)))
        return {"quiescent": True, "workspace_path": str(workspace), "allowed_pids": sorted(allowed)}

    def _validate_snapshot(self, snapshot: dict, ssh_host: str | None) -> None:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("processes"), list):
            raise ProcessProofError("invalid process snapshot")
        if snapshot.get("client") not in {"claude", "codex"} or not isinstance(snapshot.get("pane_pid"), int):
            raise ProcessProofError("invalid process snapshot")
        required = {"pid", "creation_time", "ppid", "cwd", "uid", "cmdline"}
        for row in snapshot["processes"]:
            if not isinstance(row, dict) or set(row) != required or not isinstance(row["pid"], int) or not isinstance(row["creation_time"], (int, float)) or not isinstance(row["ppid"], int) or not isinstance(row["cwd"], str) or (row["uid"] is not None and not isinstance(row["uid"], int)) or not isinstance(row["cmdline"], list) or not all(isinstance(arg, str) for arg in row["cmdline"]):
                raise ProcessProofError("invalid process identity snapshot")
        if snapshot.get("ssh_host") != ssh_host:
            raise ProcessProofError("snapshot host differs from requested host")
        self._remote_guard(ssh_host)
        self._workspace(snapshot.get("workspace_path"))
        root = next((row for row in snapshot["processes"] if row["pid"] == snapshot["pane_pid"]), None)
        if root is None:
            raise ProcessProofError("snapshot has no pane process")
        live = self._same_process(root)
        if live is not None:
            self._refuse_unsafe_root(live)

    def _remote_guard(self, ssh_host: str | None) -> None:
        if not ssh_host:
            return
        if self._ssh_manager is None:
            raise ProcessProofError("remote process proof unsupported without SSH argv transport")
        try:
            args = self._ssh_manager.get_ssh_args(ssh_host)
        except Exception as exc:
            raise ProcessProofError("remote SSH argv transport unavailable") from exc
        if not isinstance(args, (list, tuple)) or not args:
            raise ProcessProofError("remote SSH argv transport unavailable")
        raise ProcessProofError("remote process proof unsupported; refusing local PID proof")

    def _workspace(self, value) -> Path:
        if not isinstance(value, (str, os.PathLike)):
            raise ProcessProofError("workspace path required")
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ProcessProofError("workspace path is not a directory")
        return path

    def _tree(self, root):
        try:
            return [root, *root.children(recursive=True)]
        except self._psutil.Error as exc:
            raise ProcessProofError("cannot inspect pane process tree") from exc

    def _all_processes(self):
        try:
            return list(self._psutil.process_iter())
        except self._psutil.Error as exc:
            raise ProcessProofError("cannot enumerate processes") from exc

    def _refuse_unsafe_root(self, root) -> None:
        own = os.getpid()
        ancestors = {own}
        try:
            ancestors.update(proc.pid for proc in self._psutil.Process(own).parents())
        except self._psutil.Error as exc:
            raise ProcessProofError("cannot inspect controller ancestors") from exc
        if root.pid in ancestors:
            raise ProcessProofError("refusing controller or ancestor process")
        name = (root.name() or "").lower()
        cmdline = " ".join(root.cmdline()).lower()
        if name == "tmux" or "tmux: server" in cmdline or "codex app" in cmdline:
            raise ProcessProofError("refusing shared tmux server or Codex application process")

    def _is_producer(self, proc, client: str) -> bool:
        argv = list(proc.cmdline())
        entry = self._native_entry(argv, client)
        if client == "codex":
            return entry == "codex" and "--no-daemon" in argv
        return entry == "claude"

    def _is_readonly_bootstrap(self, proc) -> bool:
        argv = list(proc.cmdline())
        if self._native_entry(argv, "codex") == "codex":
            return ("--no-daemon" in argv and any(argv[index:index + 2] == ["--sandbox", "read-only"] for index in range(len(argv))) and "--dangerously-bypass-approvals-and-sandbox" not in argv)
        tools_disabled = "--tools=" in argv or any(argv[index:index + 2] == ["--tools", ""] for index in range(len(argv)))
        return self._native_entry(argv, "claude") == "claude" and any(argv[index:index + 2] == ["--permission-mode", "plan"] for index in range(len(argv))) and tools_disabled

    @staticmethod
    def _native_entry(argv, client: str) -> str | None:
        if not argv:
            return None
        executable = Path(argv[0]).name.lower()
        if executable == client:
            return client
        if executable in {"node", "nodejs"} and len(argv) > 1:
            entry = str(argv[1])
            if client == "claude" and Path(entry).name == "cli.js" and "/@anthropic-ai/claude-code/" in entry:
                return client
        return None

    def _identity(self, proc) -> dict:
        try:
            return {"pid": proc.pid, "creation_time": proc.create_time(), "ppid": proc.ppid(), "cwd": proc.cwd(), "uid": self._uid(proc), "cmdline": list(proc.cmdline())}
        except self._psutil.Error as exc:
            raise ProcessProofError(f"cannot snapshot process {proc.pid}") from exc

    def _same_process(self, item):
        try:
            proc = self._psutil.Process(int(item["pid"]))
            if proc.create_time() != item["creation_time"]:
                return None
            return proc
        except self._psutil.NoSuchProcess:
            return None
        except self._psutil.Error as exc:
            raise ProcessProofError(f"cannot prove process {item.get('pid')} identity") from exc

    def _assert_snapshot_identities_current(self, snapshot: dict) -> None:
        for item in snapshot["processes"]:
            try:
                proc = self._psutil.Process(int(item["pid"]))
            except self._psutil.NoSuchProcess:
                continue
            except self._psutil.Error as exc:
                raise ProcessProofError(f"cannot prove process {item['pid']} identity") from exc
            try:
                if proc.create_time() != item["creation_time"]:
                    raise ProcessProofError(f"PID {item['pid']} was reused")
            except self._psutil.Error as exc:
                raise ProcessProofError(f"cannot prove process {item['pid']} identity") from exc

    def _assert_tree_unchanged(self, snapshot: dict) -> None:
        root_row = next((item for item in snapshot["processes"] if item["pid"] == snapshot["pane_pid"]), None)
        if root_row is None:
            raise ProcessProofError("snapshot has no pane process")
        root = self._same_process(root_row)
        if root is None:
            raise ProcessProofError("pane process changed or exited before stop proof")
        current = {proc.pid for proc in self._tree(root)}
        recorded = {item["pid"] for item in snapshot["processes"]}
        if current != recorded:
            raise ProcessProofError("pane process topology changed after snapshot")

    def _clear_absent_source_identities(self) -> None:
        retained = set()
        for pid, created in self._source_identities:
            try:
                proc = self._psutil.Process(pid)
            except self._psutil.NoSuchProcess:
                continue
            if proc.create_time() == created:
                retained.add((pid, created))
        self._source_identities = retained

    def _readonly_identity_is_live(self, identity: tuple[int, float]) -> bool:
        for lease in self._readonly_leases:
            if identity not in lease["identities"]:
                continue
            try:
                root = self._psutil.Process(lease["pane_pid"])
            except self._psutil.NoSuchProcess:
                continue
            tree = self._tree(root)
            current = {(proc.pid, proc.create_time()) for proc in tree}
            if current != lease["identities"]:
                continue
            if any(self._is_readonly_bootstrap(proc) for proc in tree if self._is_producer(proc, lease["client"])):
                return True
        return False

    def _associated(self, proc, workspace: Path) -> bool:
        try:
            cwd = proc.cwd()
            if cwd and self._under_workspace(cwd, workspace):
                return True
            for opened in proc.open_files():
                if self._under_workspace(opened.path, workspace):
                    return True
            return any(str(workspace) in part for part in proc.cmdline())
        except self._psutil.NoSuchProcess:
            return False

    def _uid(self, proc):
        try:
            return proc.uids().real
        except (AttributeError, self._psutil.AccessDenied):
            return None

    @staticmethod
    def _under_workspace(value, workspace: Path) -> bool:
        try:
            Path(value).resolve().relative_to(workspace)
            return True
        except (ValueError, OSError):
            return False
