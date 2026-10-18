"""Controller guards and native persistence proof at durable boundaries."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
from types import SimpleNamespace
import uuid
from unittest.mock import MagicMock
import runpy

import pytest

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon
from ironclaude.orchestrator_mcp import OrchestratorTools
from ironclaude.worker_handoff import HandoffHold, InstalledNativeRuntime, WorkerHandoffController
from ironclaude.worker_handoff_process import NativeProcessAdapter
from ironclaude.worker_pm_handoff import snapshot_pm_state
from ironclaude.worker_registry import WorkerRegistry
from ironclaude.workspace_client import WorkspaceClient, WorkspaceClientError
from ironclaude.workspace_handoff_authority import WorkspaceHandoffAuthority
from ironclaude.provider_capabilities import CapabilityRegistry, ClientCapability
from ironclaude.provider_config import parse_provider_config
from ironclaude.provider_router import ProviderRouter
from ironclaude.provider_state import ProviderState
from ironclaude.worker_quota import read_native_quota

SCHEMA = runpy.run_path(str(Path(__file__).with_name("test_worker_pm_handoff.py")))["SCHEMA"]


def _registry(tmp_path):
    registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    objective = registry.create_objective("unfinished")
    task = registry.create_task(objective, "continue")
    source = str(uuid.uuid4())
    registry.register_worker("w1", "claude-opus", "ic-w1", task_id=task,
                             repo=str(tmp_path), description="original objective",
                             client="claude", model="claude-opus", native_session_id=source)
    registry.set_worker_workspace("w1", {
        "workspace_guid": str(uuid.uuid4()), "repository_identity": "repo-id",
        "worktree_path": str(tmp_path / "worktree"), "branch": "branch",
        "base_commit": "base", "integration_target": "main",
    })
    return registry


def test_held_worker_blocks_delayed_send_before_grading_or_tmux(tmp_path):
    registry = _registry(tmp_path)
    tools = OrchestratorTools.__new__(OrchestratorTools)
    tools.registry = registry
    tools.tmux = MagicMock()
    tools._ssh_manager = None
    tools._call_local_grader = MagicMock()
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=1)
    with pytest.raises(RuntimeError, match="handoff"):
        tools.send_to_worker("w1", "continue")
    tools._call_local_grader.assert_not_called()
    tools.tmux.send_keys.assert_not_called()


def test_abort_handoff_is_reachable_through_wrapped_recovery_tool(tmp_path):
    registry = _registry(tmp_path)
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=1)
    registry.pause_worker_handoff("w1", 1, "uncertain_external_effect")
    tools = OrchestratorTools.__new__(OrchestratorTools)
    tools.registry = registry
    tools.tmux = MagicMock()
    tools._ssh_manager = None
    tools._workspace_client = MagicMock()
    with pytest.raises(RuntimeError, match="active handoff blocks worker operation"):
        tools.recover_worker_integration("w1", "status")
    result = tools.recover_worker_integration("w1", "abort_handoff")
    assert result["status"] == "released", result
    assert registry.get_worker("w1")["status"] == "running"
    tools._workspace_client.release_handoff.assert_not_called()


def test_uncertain_bootstrap_is_held_without_relaunch(tmp_path):
    registry = _registry(tmp_path)
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=1)
    registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    registry.record_worker_handoff_evidence("w1", 1, "source_stopped", {
        "bootstrap_intent": "ic-w1-h1-bootstrap",
    })
    tools = MagicMock()
    tools.registry = registry
    runtime = MagicMock()
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=runtime,
                                         authority=MagicMock(), pm_db_path=tmp_path / "pm.db")
    assert controller.reconcile("w1")["reason"] == "uncertain_external_effect"
    runtime.bootstrap.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"


def test_stale_hook_uuid_file_cannot_bind_reused_source_pid(tmp_path):
    native = str(uuid.uuid4())
    path = tmp_path / "ironclaude-session-12345.id"
    path.write_text(native)
    os.utime(path, (100, 100))
    tools = MagicMock()
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock(), session_id_dir=tmp_path)
    snapshot = {"pane_pid":12345, "processes":[{"pid":12345,"ppid":1,"creation_time":200}],
                "producer_pids":[12345]}
    with pytest.raises(HandoffHold, match="binding"):
        controller._verify_hook_binding(snapshot, native, "claude")
    os.utime(path, (201, 201))
    controller._verify_hook_binding(snapshot, native, "claude")


@pytest.mark.parametrize("changed", [
    "workspace_guid", "generation", "repository_identity", "worker_id",
    "source_owner_session_id", "worktree_path", "branch", "base_commit",
    "current_head", "integration_target", "target_owner_session_id",
])
def test_handoff_readback_rejects_changed_scope_field(changed):
    target = str(uuid.uuid4())
    scope = {"workspace_guid":str(uuid.uuid4()), "generation":2,
             "repository_identity":"repository", "worker_id":"w1",
             "source_owner_session_id":str(uuid.uuid4()),
             "expected_worktree_path":"/managed/tree", "expected_branch":"ironclaude/branch",
             "expected_base_commit":"base", "expected_current_head":"head",
             "expected_integration_target":"main"}
    state = {"protocol_version":1, "phase":"transferred", "target_owner_session_id":target,
             "workspace_guid":scope["workspace_guid"], "generation":2,
             "repository_identity":scope["repository_identity"], "worker_id":"w1",
             "source_owner_session_id":scope["source_owner_session_id"],
             "worktree_path":scope["expected_worktree_path"], "branch":scope["expected_branch"],
             "base_commit":"base", "current_head":"head", "integration_target":"main"}
    evidence = {"scope":scope, "target_id":target}
    WorkerHandoffController._validate_scope_readback(evidence, state)
    state[changed] = "foreign" if changed != "generation" else 3
    with pytest.raises(HandoffHold, match="readback"):
        WorkerHandoffController._validate_scope_readback(evidence, state)


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_native_ack_requires_saved_identity_model_and_completed_turn(tmp_path, client):
    native_id = str(uuid.uuid4())
    cwd = str(tmp_path / "worktree")
    model = "claude-sonnet-4-5" if client == "claude" else "gpt-6-sol"
    if client == "claude":
        path = tmp_path / ".claude/projects/hash" / f"{native_id}.jsonl"
        records = [
            {"sessionId": native_id, "cwd": cwd, "type": "user"},
            {"sessionId": native_id, "cwd": cwd, "type": "assistant", "message": {
                "model": model, "stop_reason": "end_turn", "usage": {"output_tokens": 2},
                "content": [{"type": "text", "text": "HANDOFF_BOOTSTRAP_READY"}],
            }},
        ]
    else:
        path = tmp_path / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": native_id, "cwd": cwd}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
            {"type": "turn_context", "payload": {"model": model, "turn_id": "t1"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "HANDOFF_BOOTSTRAP_READY"}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}},
        ]
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    driver = InstalledNativeRuntime(MagicMock())
    # The matcher uses the native home layout; patch Path.home only for this fixture.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert driver._saved_ack(client, native_id, cwd, model, 0)
        assert not driver._saved_ack(client, native_id, cwd, "wrong-model", 0)


@pytest.mark.parametrize("mutation", ["foreign_cwd", "later_api_error", "ack_api_error", "later_assistant", "missing_stop", "noninteger_usage"])
def test_claude_saved_ack_requires_current_bound_clean_assistant(tmp_path, mutation):
    native_id, cwd, model = str(uuid.uuid4()), str(tmp_path / "worktree"), "claude-opus-4-5"
    path = tmp_path / ".claude/projects/hash" / f"{native_id}.jsonl"
    ack = {"sessionId": native_id, "cwd": cwd, "type": "assistant", "message": {
        "model": model, "stop_reason": "end_turn", "usage": {"output_tokens": 2},
        "content": [{"type": "text", "text": "HANDOFF_BOOTSTRAP_READY"}]}}
    rows = [{"sessionId": native_id, "cwd": cwd, "type": "user"}, ack]
    if mutation == "foreign_cwd":
        ack["cwd"] = str(tmp_path / "foreign")
    elif mutation == "later_api_error":
        rows.append({"sessionId": native_id, "cwd": cwd, "type": "assistant",
                     "isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429})
    elif mutation == "ack_api_error":
        ack.update(isApiErrorMessage=True, error="rate_limit", apiErrorStatus=429)
    elif mutation == "later_assistant":
        rows.append({"sessionId": native_id, "cwd": cwd, "type": "assistant", "message": {
            "model": model, "stop_reason": "end_turn", "usage": {"output_tokens": 2},
            "content": [{"type": "text", "text": "other reply"}]}})
    elif mutation == "missing_stop":
        del ack["message"]["stop_reason"]
    elif mutation == "noninteger_usage":
        ack["message"]["usage"]["output_tokens"] = "2"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert InstalledNativeRuntime(MagicMock())._saved_ack("claude", native_id, cwd, model, 0) is None


@pytest.mark.parametrize("mutation", ["wrong_model", "error_complete", "wrong_turn", "quoted_marker", "aborted"])
def test_codex_saved_ack_rejects_noncompleted_or_unowned_turn(tmp_path, mutation):
    native_id, model = str(uuid.uuid4()), "gpt-6-sol"
    cwd = str(tmp_path / "worktree")
    path = tmp_path / ".codex/sessions" / f"rollout-{native_id}.jsonl"
    records = [
        {"type":"session_meta", "payload":{"id":native_id,"cwd":cwd}},
        {"type":"event_msg", "payload":{"type":"task_started","turn_id":"t1"}},
        {"type":"turn_context", "payload":{"turn_id":"t1","model":model}},
        {"type":"response_item", "payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"HANDOFF_BOOTSTRAP_READY"}]}},
        {"type":"event_msg", "payload":{"type":"task_complete","turn_id":"t1"}},
    ]
    if mutation == "wrong_model": records[2]["payload"]["model"] = "gpt-6-astra"
    if mutation == "error_complete": records[4]["payload"]["error"] = {"code":"usage_limit_exceeded"}
    if mutation == "wrong_turn": records[4]["payload"]["turn_id"] = "old"
    if mutation == "quoted_marker":
        records[3]["payload"]["role"] = "user"
        records[3]["payload"]["content"][0]["text"] = 'Please quote "HANDOFF_BOOTSTRAP_READY"'
    if mutation == "aborted": records[4]["payload"]["type"] = "turn_aborted"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert not InstalledNativeRuntime(MagicMock())._saved_ack("codex", native_id, cwd, model, 0)


@pytest.mark.parametrize("failure", ["unbound_error", "bound_error", "generic_error", "wrong_turn_ack", "after_complete_error"])
def test_codex_saved_ack_rejects_separate_native_error_and_foreign_response(tmp_path, failure):
    native_id, model = str(uuid.uuid4()), "gpt-6-sol"
    cwd = str(tmp_path / "worktree")
    path = tmp_path / ".codex/sessions" / f"rollout-{native_id}.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": native_id, "cwd": cwd}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "turn_context", "payload": {"turn_id": "t1", "model": model}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "HANDOFF_BOOTSTRAP_READY"}]}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}},
    ]
    if failure == "wrong_turn_ack":
        rows[3]["payload"]["turn_id"] = "other"
    else:
        error = {"type": "event_msg", "payload": {"type": "error", "error": {"code": "usage_limit_exceeded"}}}
        if failure == "bound_error":
            error["payload"]["turn_id"] = "t1"
        if failure == "generic_error":
            error["payload"] = {"type": "error", "message": "native failure"}
        rows.insert(5 if failure == "after_complete_error" else 4, error)
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert InstalledNativeRuntime(MagicMock())._saved_ack("codex", native_id, cwd, model, 0) is None


# These fixtures deliberately retain production storage boundaries.  Native CLI,
# tmux, and process enumeration are the only simulated boundaries.
class _HandoffProc:
    def __init__(self, pid, argv, cwd, parent=None):
        self.pid, self.argv, self.cwd_value, self.parent = pid, argv, str(cwd), parent
        self.created, self.gone, self.signals = float(pid), False, []

    def children(self, recursive=True):
        direct = [p for p in self._pool if p.parent is self and not p.gone]
        return direct + [child for p in direct for child in p.children()] if recursive else direct

    def parents(self): return []
    def name(self): return "zsh"
    def cmdline(self): return self.argv
    def create_time(self): return self.created
    def ppid(self): return self.parent.pid if self.parent else 1
    def cwd(self): return self.cwd_value
    def uids(self): return SimpleNamespace(real=os.getuid())
    def open_files(self): return []
    def send_signal(self, signal): self.signals.append(signal); self.gone = True


class _HandoffPs:
    class Error(Exception): pass
    class NoSuchProcess(Error): pass
    class AccessDenied(Error): pass
    def __init__(self): self.processes = {}; self.controller = _HandoffProc(os.getpid(), ["pytest"], "/private/tmp")
    def Process(self, pid):
        if pid == os.getpid(): return self.controller
        item = self.processes.get(pid)
        if item is None or item.gone: raise self.NoSuchProcess(pid)
        return item
    def process_iter(self): return [p for p in self.processes.values() if not p.gone]


class _HandoffTmux:
    def __init__(self): self.panes, self.sent = {}, []
    def list_pane_pid(self, name, ssh_host=None): return self.panes[name].pid
    def send_keys(self, name, text): self.sent.append((name, text)); return True


class _NativeRuntime:
    def __init__(self, ps, tmux, home, pm_db): self.ps, self.tmux, self.home, self.pm_db, self.next_pid = ps, tmux, Path(home), Path(pm_db), 41000
    def preflight(self, client): pass
    def _spawn(self, name, client, path, *, readonly):
        root = _HandoffProc(self.next_pid, ["zsh"], path); self.next_pid += 1
        argv = (["codex", "--no-daemon", "--sandbox", "read-only"] if client == "codex" else ["claude", "--permission-mode", "plan", "--tools", ""]) if readonly else (["codex", "--no-daemon", "--dangerously-bypass-approvals-and-sandbox"] if client == "codex" else ["claude", "--dangerously-skip-permissions"])
        child = _HandoffProc(self.next_pid, argv, path, root); self.next_pid += 1
        root._pool = child._pool = list(self.ps.processes.values()) + [root, child]
        self.ps.processes.update({root.pid: root, child.pid: child}); self.tmux.panes[name] = root
        return root, child
    def _ack(self, client, native_id, path, model):
        if client == "claude":
            log = self.home / ".claude/projects/target" / f"{native_id}.jsonl"
            records = [{"sessionId":native_id,"cwd":str(path),"type":"user"}, {"sessionId":native_id,"cwd":str(path),"type":"assistant","message":{"model":model,"stop_reason":"end_turn","usage":{"output_tokens":1},"content":[{"type":"text","text":"HANDOFF_BOOTSTRAP_READY"}]}}]
        else:
            log = self.home / ".codex/sessions" / f"rollout-{native_id}.jsonl"
            records = [{"type":"session_meta","payload":{"id":native_id,"cwd":str(path)}}, {"type":"event_msg","payload":{"type":"task_started","turn_id":"t1"}}, {"type":"turn_context","payload":{"model":model,"turn_id":"t1"}}, {"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"HANDOFF_BOOTSTRAP_READY"}]}}, {"type":"event_msg","payload":{"type":"task_complete","turn_id":"t1"}}]
        log.parent.mkdir(parents=True, exist_ok=True); log.write_text("".join(json.dumps(x)+"\n" for x in records))
    def bootstrap(self, name, client, model, path, worker_id):
        native = str(uuid.uuid4()); self._spawn(name, client, path, readonly=True); self._ack(client, native, path, model)
        db = sqlite3.connect(self.pm_db); db.execute("INSERT INTO sessions (terminal_session,professional_mode,workflow_stage,project_hash) VALUES (?,?,?,?)", (native, "on", "idle", "project")); db.commit(); db.close()
        return {"client":client,"model":model,"native_session_id":native,"persisted":True,"ack_complete":True,"observed_model":model}
    def resume(self, name, client, model, native_id, path, worker_id): self._spawn(name, client, path, readonly=False); return {"native_session_id":native_id,"ready":True}
    def inspect_resumed(self, name, client, native_id): return name in self.tmux.panes


def _native_runner(workspace_db):
    root = Path(__file__).parents[2] / "worker/mcp-servers/workspace-manager"
    vite = root / "node_modules/.bin/vite-node"
    source = root / "src/cli.ts"
    def run(argv, **kwargs):
        return subprocess.run([str(vite), "--script", str(source), argv[2], argv[3]], capture_output=True, text=True, check=False, env={**os.environ, "WORKSPACE_MANAGER_DB_PATH":str(workspace_db)})
    return run, root.parent.parent


def _write_quota(home, client, native_id, worktree):
    timestamp = "2026-10-05T12:00:00Z"
    if client == "claude":
        path = Path(home) / ".claude/projects/source" / f"{native_id}.jsonl"
        rows = [{"session_id":native_id,"sessionId":native_id,"cwd":str(worktree),"timestamp":timestamp,"type":"assistant","uuid":"turn","error":"rate_limit","isApiErrorMessage":True,"apiErrorStatus":429,"quotaLimits":{"status":"rejected","rateLimitType":"seven_day","resetsAt":1,"unifiedRateLimitFallbackAvailable":False,"overageStatus":"rejected","isUsingOverage":False},"message":{"role":"assistant","model":"<synthetic>","usage":{"output_tokens":1}}}]
    else:
        path = Path(home) / ".codex/sessions" / f"rollout-{native_id}.jsonl"
        rows = [{"type":"session_meta","payload":{"id":native_id,"cwd":str(worktree)}}, {"type":"event_msg","timestamp":timestamp,"payload":{"type":"task_started","turn_id":"t"}}, {"type":"event_msg","timestamp":timestamp,"payload":{"type":"token_count","rate_limits":{"limit_id":"codex","rate_limit_reached_type":"rate_limit_reached"},"info":{"total_token_usage":{"output_tokens":7}}}}]
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("".join(json.dumps(row)+"\n" for row in rows));
    return path


def _scope(worker, head="head"):
    return {"workspace_guid": worker["workspace_guid"], "generation": 1,
            "repository_identity": worker["workspace_repository_identity"], "worker_id": worker["id"],
            "source_owner_session_id": worker["native_session_id"],
            "expected_worktree_path": worker["workspace_path"], "expected_branch": worker["workspace_branch"],
            "expected_base_commit": worker["workspace_base_commit"], "expected_current_head": head,
            "expected_integration_target": worker["workspace_integration_target"]}


def _paused_after_stop(registry, phase, reason, **evidence):
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1)
    order = ("claimed", "source_stopped", "target_ready", "pm_prepared", "owner_transferred",
             "runtime_registered", "continuation_dispatched")
    for old, new in zip(order, order[1:]):
        if old == phase:
            break
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, phase, {
        "scope": _scope(worker), "transport": {}, "capability_token": "token", **evidence})
    registry.pause_worker_handoff("w1", 1, reason)


def _readback(scope, phase, target=None):
    return {"protocol_version": 1, "present": True, "phase": phase, "target_owner_session_id": target,
            "workspace_guid": scope["workspace_guid"], "generation": scope["generation"],
            "repository_identity": scope["repository_identity"], "worker_id": scope["worker_id"],
            "source_owner_session_id": scope["source_owner_session_id"],
            "worktree_path": scope["expected_worktree_path"], "branch": scope["expected_branch"],
            "base_commit": scope["expected_base_commit"], "current_head": scope["expected_current_head"],
            "integration_target": scope["expected_integration_target"]}


def _bootstrap_tree(worktree, name="ic-w1-h1-bootstrap"):
    ps, tmux = _HandoffPs(), _HandoffTmux()
    root = _HandoffProc(41000, ["zsh"], worktree)
    child = _HandoffProc(41001, ["claude", "--permission-mode", "plan", "--tools", ""], worktree, root)
    root._pool = child._pool = [root, child]
    ps.processes = {root.pid: root, child.pid: child}
    tmux.panes[name] = root
    snapshot = NativeProcessAdapter(tmux, psutil_module=ps).snapshot(name, str(worktree), "claude")
    return ps, tmux, json.loads(json.dumps(snapshot)), root, child


def _released_workspace():
    workspace = MagicMock()
    workspace.release_handoff.return_value = {"protocol_version": 1, "present": True, "phase": "finished"}
    return workspace


def _pm_db(path, source):
    db = sqlite3.connect(path); db.executescript(SCHEMA)
    plan = json.dumps({"name":"handoff","goal":"continue","design_file":"docs/design.md","tasks":[{"id":i,"allowed_files":[]} for i in range(1,8)]})
    db.execute("INSERT INTO sessions (terminal_session,professional_mode,workflow_stage,plan_name,plan_json,current_wave,project_hash) VALUES (?,?,?,?,?,?,?)", (source,"on","executing","handoff",plan,1,"project"))
    for task, status in enumerate(["review_passed"] * 5 + ["in_progress", "pending"], 1): db.execute("INSERT INTO wave_tasks (terminal_session,task_id,wave_number,task_name,status) VALUES (?,?,?,?,?)", (source,task,1,f"Task {task}",status))
    db.execute("INSERT INTO registered_designs (design_file,terminal_session,consumed) VALUES (?,?,1)", ("docs/design.md",source)); db.commit(); db.close()


@pytest.mark.parametrize("reset_at,expected", [
    # Event 2026-10-05T12:00:00Z = 1791201600. A reset 21 h later is valid; a reset
    # three hours before the event is not later than it, so it counts as absent (H9 validity).
    # A reset ten years after the event (1791201600 + 10*365*86400 = 2106561600) is capped at the
    # event plus 7 days (1791201600 + 7*86400 = 1791806400), not now plus 7 days: time.time is
    # pinned to 1_000_000 here (design 2026-10-18 v120-security-fixes 2).
    (1791277200, 1791277200.0), (None, 1_000_060.0), (1791190800, 1_000_060.0),
    (2106561600, 1791806400.0),
])
def test_source_quarantine_expires_at_native_reset_or_configured_default(tmp_path, monkeypatch, reset_at, expected):
    import ironclaude.worker_handoff as handoff_module
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    state = ProviderState(registry._conn)
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(),
                            _ssh_manager=None, _provider_router=lambda: (None, None, state, None),
                            _config={"worker_usage_quarantine_default_seconds": 60})
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock())
    monkeypatch.setattr(handoff_module.time, "time", lambda: 1_000_000.0)
    quota = {"state": "exhausted", "event_key": "quota-event", "file_identity": "1:2", "cursor": 10,
             "event_timestamp": "2026-10-05T12:00:00+00:00", "reset_at": reset_at}
    controller._quarantine_validated_source(worker, 1, quota)
    row = registry._conn.execute(
        "SELECT usage_expires_at FROM provider_capability_state WHERE client='claude' AND tier=?",
        (state.worker_usage_scope_tier("worker", "opus"),)).fetchone()
    assert row[0] == expected


def test_paused_retry_releases_when_source_quota_evidence_changes(tmp_path):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    verdict = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                workspace_path=worker["workspace_path"], generation="1")
    scope = _scope(worker)
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "quota_verdict": verdict, "scope": scope, "transport": {},
        "capability_token": "token", "grant_proved": True})
    registry.pause_worker_handoff("w1", 1, "opposite_capability_unavailable")
    workspace = _released_workspace()
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace,
                            _ssh_manager=None,
                            _provider_router=lambda: (None, None, ProviderState(registry._conn), None))
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock(), native_log_root=home,
                                         pm_db_path=tmp_path / "missing-pm.db")
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held"
    workspace.release_handoff.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    success = {"session_id": worker["native_session_id"], "sessionId": worker["native_session_id"],
               "cwd": worker["workspace_path"], "timestamp": "2026-10-05T12:00:01Z",
               "type": "assistant", "uuid": "recovered",
               "message": {"role": "assistant", "model": "claude-opus", "stop_reason": "end_turn",
                           "usage": {"output_tokens": 2}}}
    with path.open("a") as stream:
        stream.write(json.dumps(success) + "\n")
    released = controller.retry_paused_preflight("w1")
    assert released["status"] == "released" and released["phase"] == "released", released
    assert registry.get_worker("w1")["status"] == "running"
    workspace.release_handoff.assert_called_once_with({
        "workspace_guid": scope["workspace_guid"], "generation": 1, "capability_token": "token",
        "source_owner_session_id": worker["native_session_id"]})


@pytest.mark.parametrize("session_alive", [True, False])
def test_prestop_retry_releases_when_source_session_is_gone(tmp_path, session_alive):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    verdict = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                workspace_path=worker["workspace_path"], generation="1")
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "quota_verdict": verdict, "scope": _scope(worker), "transport": {},
        "capability_token": "token", "grant_proved": True})
    registry.pause_worker_handoff("w1", 1, "opposite_capability_unavailable")
    workspace = _released_workspace()
    tmux = MagicMock()
    tmux.has_session.return_value = session_alive
    tools = SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None,
                            _provider_router=lambda: (None, None, ProviderState(registry._conn), None))
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(),
                                         authority=MagicMock(), native_log_root=home,
                                         pm_db_path=tmp_path / "missing-pm.db")
    result = controller.retry_paused_preflight("w1")
    tmux.has_session.assert_called_with("ic-w1")
    if session_alive:
        assert result["status"] == "held"
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        workspace.release_handoff.assert_not_called()
    else:
        assert result["status"] == "released"
        assert registry.get_worker("w1")["status"] == "running"
        workspace.release_handoff.assert_called_once()


@pytest.mark.parametrize("reason", ["native_quota_evidence_changed", "source_process_changed_before_stop"])
def test_prestop_pause_with_nonretryable_reason_releases_automatically(tmp_path, reason):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "scope": _scope(worker), "transport": {}, "capability_token": "token"})
    registry.pause_worker_handoff("w1", 1, reason)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    assert controller.retry_paused_preflight("w1")["status"] == "released"
    assert registry.get_worker("w1")["status"] == "running"
    assert json.loads(registry.get_worker_handoff("w1")["evidence_json"])["disposition"] == "released"
    workspace.release_handoff.assert_called_once()


def test_prestop_release_refused_once_target_recorded(tmp_path):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1, initial_evidence={
        "scope": _scope(worker), "transport": {}, "capability_token": "token",
        "target_id": str(uuid.uuid4())})
    registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held"
    assert held["release_refused"] == "handoff_target_or_source_stop_recorded"
    workspace.release_handoff.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"


@pytest.mark.parametrize("target_owner,source_owner_matches,accepted", [
    (None, True, True), (str(uuid.uuid4()), True, True),
    (str(uuid.uuid4()), False, False), (None, False, False),
])
def test_released_generation_lets_next_handoff_preflight_proceed(tmp_path, target_owner, source_owner_matches, accepted):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    worktree = Path(worker["workspace_path"])
    worktree.mkdir(parents=True)
    for argv in (["git", "init", "-b", "main", str(worktree)],
                 ["git", "-C", str(worktree), "config", "user.email", "test@example.com"],
                 ["git", "-C", str(worktree), "config", "user.name", "Test"]):
        subprocess.run(argv, check=True, capture_output=True)
    (worktree / "AGENTS.md").write_text("rules\n"); (worktree / "CLAUDE.md").write_text("rules\n")
    subprocess.run(["git", "-C", str(worktree), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "base"], check=True, capture_output=True)
    workspace = MagicMock()
    workspace.discover_installed_plugin_root.return_value = "/plugin"
    workspace.handoff_status.return_value = {
        "protocol_version": 1, "present": True, "phase": "finished", "generation": 1,
        "target_owner_session_id": target_owner,
        "source_owner_session_id": worker["native_session_id"] if source_owner_matches else str(uuid.uuid4())}
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    if accepted:
        scope, _transport, _head = controller._source_preflight(worker, 2)
        assert scope["generation"] == 2
    else:
        with pytest.raises(HandoffHold, match="workspace_handoff_protocol_unavailable"):
            controller._source_preflight(worker, 2)


def test_post_stop_permanent_failure_releases_for_preservation_not_completion(tmp_path):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "pm_readback_failed")
    workspace = _released_workspace()
    runtime = MagicMock()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=runtime, authority=MagicMock())
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True
    assert result["reason"] == "pm_readback_failed"
    assert registry.get_worker("w1")["status"] == "running"
    row = registry.get_worker_handoff("w1")
    assert row["phase"] == "released" and row["completed_at"] is not None
    evidence = json.loads(row["evidence_json"])
    assert evidence["disposition"] == "released" and evidence["released_from_phase"] == "source_stopped"
    workspace.release_handoff.assert_called_once()
    runtime.bootstrap.assert_not_called()


def test_post_stop_preservation_stops_live_bootstrap_then_releases(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    _paused_after_stop(registry, "target_ready", "pm_prepare_failed",
                       target_id=str(uuid.uuid4()), bootstrap_process=bootstrap)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    released = controller.retry_paused_preflight("w1")
    assert released["status"] == "released" and released["preserve"] is True
    assert root.gone is True and child.gone is True
    assert registry.get_worker("w1")["status"] == "running"
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["bootstrap_stopped"] is True and evidence["bootstrap_stop_intent"] is False
    workspace.release_handoff.assert_called_once()


def test_post_stop_preservation_holds_when_bootstrap_stop_is_unprovable(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    _paused_after_stop(registry, "target_ready", "pm_prepare_failed",
                       target_id=str(uuid.uuid4()), bootstrap_process=bootstrap)
    original = ps.Process
    ps.Process = lambda pid: (_ for _ in ()).throw(ps.AccessDenied(pid)) if pid == 41001 else original(pid)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held"
    assert held["preserve_refused"] == "target_bootstrap_stop_unproved"
    assert registry.get_worker_handoff("w1")["pause_reason"] == "pm_prepare_failed"
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    workspace.release_handoff.assert_not_called()


def test_authorized_but_untransferred_handoff_stops_bootstrap_and_releases_as_source(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "pm_prepared", "owner_transfer_unproved", target_id=target_id,
                       authorize_proved=True, bootstrap_process=bootstrap)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(registry.get_worker("w1")), "begun", target_id)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    released = controller.retry_paused_preflight("w1")
    assert released["status"] == "released" and released["preserve"] is True
    assert root.gone is True and child.gone is True
    assert registry.get_worker("w1")["status"] == "running"
    workspace.release_handoff.assert_called_once()


@pytest.mark.parametrize("via", ["retry", "abort"])
@pytest.mark.parametrize("reason", ["owner_transfer_unproved", "uncertain_external_effect"])
def test_landed_transfer_advances_to_post_transfer_instead_of_releasing(tmp_path, reason, via):
    registry = _registry(tmp_path)
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "pm_prepared", reason, target_id=target_id,
                       authorize_proved=True, transfer_intent=True)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(registry.get_worker("w1")), "transferred", target_id)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    result = (controller.retry_paused_preflight("w1") if via == "retry"
              else controller.abort_handoff("w1"))
    assert "transfer_landed" in result.values()
    row = registry.get_worker_handoff("w1")
    assert (row["phase"], row["pause_reason"], row["completed_at"]) == ("owner_transferred", None, None)
    assert json.loads(row["evidence_json"])["transfer_intent"] is False
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    workspace.release_handoff.assert_not_called()


def test_previously_released_workspace_row_is_not_a_landed_transfer(tmp_path):
    registry = _registry(tmp_path)
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "pm_prepared", "owner_transfer_unproved", target_id=target_id,
                       authorize_proved=True)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(registry.get_worker("w1")), "finished", target_id)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True
    assert registry.get_worker_handoff("w1")["phase"] == "released"
    assert registry.get_worker("w1")["status"] == "running"
    workspace.release_handoff.assert_called_once()


def test_post_stop_retry_never_rereads_the_stopped_source_log(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "opposite_capability_unavailable")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the post-stop retry must not read the stopped source's native log")

    monkeypatch.setattr("ironclaude.worker_handoff.read_native_quota", forbidden)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    controller._validate_quota = forbidden
    controller._target = MagicMock(return_value=(SimpleNamespace(
        client="codex", model="gpt-5.6-sol", effective_tier="opus"), "opus"))
    controller._start_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    assert controller._target.call_args.kwargs == {"allow_source_client": True}
    controller._start_target.assert_called_once()
    assert registry.get_worker_handoff("w1")["pause_reason"] is None


def test_uncertain_external_effect_stays_paused_without_release(tmp_path):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "uncertain_external_effect")
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    for _ in range(2):
        assert controller.retry_paused_preflight("w1")["status"] == "held"
    workspace.release_handoff.assert_not_called()
    assert registry.get_worker("w1")["status"] == "handoff_pending"


@pytest.mark.parametrize("bootstrap_state", ["alive", "unproved", "gone", "gone_worktree_removed"])
def test_abort_handoff_requires_recorded_bootstrap_proved_gone(tmp_path, bootstrap_state):
    import shutil
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    _paused_after_stop(registry, "target_ready", "uncertain_external_effect",
                       target_id=str(uuid.uuid4()), bootstrap_process=bootstrap)
    if bootstrap_state.startswith("gone"):
        root.gone = child.gone = True
        if bootstrap_state == "gone_worktree_removed":
            shutil.rmtree(worktree)
    elif bootstrap_state == "unproved":
        # Root exited; the child's identity cannot be read, so it is not proved gone.
        root.gone = True
        original = ps.Process
        ps.Process = lambda pid: (_ for _ in ()).throw(ps.AccessDenied(pid)) if pid == 41001 else original(pid)
    workspace = _released_workspace()
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    result = controller.abort_handoff("w1")
    if bootstrap_state.startswith("gone"):
        assert result["status"] == "released"
        assert registry.get_worker("w1")["status"] == "running"
        workspace.release_handoff.assert_called_once()
    else:
        assert result["status"] == "held"
        assert result["abort_refused"] == "target_bootstrap_alive_or_unproved"
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        workspace.release_handoff.assert_not_called()


def test_post_stop_retry_accepts_source_client_once_its_quarantine_expires(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "source_stopped", "opposite_capability_unavailable")
    raw = {"clients": {"claude": {"enabled": True, "path": "claude", "models": {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}},
                       "codex": {"enabled": True, "path": "codex", "models": {"haiku": "gpt-5.6-luna", "sonnet": "gpt-5.6-terra", "opus": "gpt-5.6-sol", "fable": "gpt-6-astra"}}},
           "roles": {role: {"preferred": "claude", "clients": ["claude", "codex"]} for role in ("brain", "worker", "grader", "advisor")},
           "shadow_mode": False}
    config = parse_provider_config(raw)
    state = ProviderState(registry._conn)
    caps = CapabilityRegistry(state)

    class Probe:
        def probe_local(self, config, client, role, tier):
            ready = client == "claude"
            return ClientCapability("local", client, role, tier, ready, ready, ready, ready, ready)

    monkeypatch.setattr("ironclaude.provider_capabilities.CapabilityProbe", Probe)
    account = ("local", "claude", "worker", state.worker_usage_scope_tier("worker", "opus"))
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:1",
                           usage_cursor=1, usage_event_key="quota",
                           usage_event_timestamp="2026-10-05T12:00:00Z", usage_expires_at=4_000_000_000.0)
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None,
                            _provider_router=lambda: (ProviderRouter(config, state, caps), config, state, caps))
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    controller._start_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    held = controller.retry_paused_preflight("w1")
    assert held["status"] == "held" and held["reason"] == "opposite_capability_unavailable"
    controller._start_target.assert_not_called()
    registry._conn.execute("UPDATE provider_capability_state SET usage_expires_at=1 WHERE client='claude' AND tier=?",
                           (account[3],))
    registry._conn.commit()
    source = json.loads(registry.get_worker_handoff("w1")["snapshot_json"])
    with pytest.raises(HandoffHold, match="opposite_capability_unavailable"):
        controller._target(source)
    controller.retry_paused_preflight("w1")
    row = registry.get_worker_handoff("w1")
    assert row["pause_reason"] is None
    assert json.loads(row["evidence_json"])["target_client"] == "claude"
    controller._start_target.assert_called_once()


def test_restart_after_target_ready_rebuilds_bootstrap_lease_and_transfers(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    worktree = Path(source["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, bootstrap, root, child = _bootstrap_tree(worktree)
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared")):
        registry.advance_worker_handoff("w1", 1, old, new)
    target_id = str(uuid.uuid4())
    registry.record_worker_handoff_evidence("w1", 1, "pm_prepared", {
        "scope": _scope(source), "transport": {}, "capability_token": "token",
        "authorize_proved": True, "target_id": target_id, "bootstrap_process": bootstrap})
    workspace = MagicMock()
    workspace.transfer_handoff.return_value = {"protocol_version": 1, "phase": "transferred",
                                               "target_owner_session_id": target_id}
    restarted = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(),
        authority=MagicMock(), pm_db_path=tmp_path / "pm.db")
    restarted._resume_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    restarted.reconcile("w1")
    row = registry.get_worker_handoff("w1")
    assert row["phase"] == "owner_transferred" and row["pause_reason"] is None
    workspace.transfer_handoff.assert_called_once()
    restarted._resume_target.assert_called_once()
    assert root.gone is False and child.gone is False


def test_stale_resume_session_is_stopped_then_resumed_forward(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, _session_tree, root, child = _bootstrap_tree(worktree, name="ic-w1-h1")
    tmux.has_session = lambda name, ssh_host=None: name in tmux.panes and not tmux.panes[name].gone
    _paused_after_stop(registry, "owner_transferred", "target_resume_session_already_exists",
                       target_id=str(uuid.uuid4()), target_client="claude", target_model="claude-opus",
                       resume_intent="ic-w1-h1", resume_started_at=123.0, bootstrap_stopped=True)
    runtime = MagicMock()
    runtime.inspect_resumed.return_value = False
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=MagicMock(), _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=runtime, authority=MagicMock())
    controller._resume_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    assert root.gone is True and child.gone is True
    row = registry.get_worker_handoff("w1")
    evidence = json.loads(row["evidence_json"])
    assert row["pause_reason"] is None and evidence["resume_intent"] is False
    assert evidence["stale_resume_stop_intent"] is False
    assert evidence["stale_resume_process"]["session_name"] == "ic-w1-h1"
    assert evidence["stale_resume_stop_proof"]["stopped"] is True
    controller._resume_target.assert_called_once()


def test_pending_stale_resume_stop_intent_self_heals_on_reconcile(tmp_path):
    registry = _registry(tmp_path)
    worktree = Path(registry.get_worker("w1")["workspace_path"])
    worktree.mkdir(parents=True)
    ps, tmux, stale, root, child = _bootstrap_tree(worktree, name="ic-w1-h1")
    root.gone = child.gone = True
    source = registry.get_worker("w1")
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "scope": _scope(source), "transport": {}, "capability_token": "token",
        "target_id": str(uuid.uuid4()), "target_client": "claude", "target_model": "claude-opus",
        "resume_intent": "ic-w1-h1", "resume_started_at": 123.0, "bootstrap_stopped": True,
        "stale_resume_stop_intent": True, "stale_resume_process": stale})
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=MagicMock(), _ssh_manager=None),
        process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=MagicMock(), authority=MagicMock())
    controller._resume_target = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.reconcile("w1")
    row = registry.get_worker_handoff("w1")
    evidence = json.loads(row["evidence_json"])
    assert evidence["stale_resume_stop_intent"] is False and evidence["resume_intent"] is False
    assert evidence["stale_resume_stop_proof"]["stopped"] is True
    assert row["pause_reason"] is None
    controller._resume_target.assert_called_once()


def test_undelivered_continuation_dispatch_is_retried_forward(tmp_path):
    registry = _registry(tmp_path)
    _paused_after_stop(registry, "runtime_registered", "continuation_dispatch_unproved",
                       target_id=str(uuid.uuid4()), target_session="ic-w1-h1", dispatch_intent=True)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    controller._dispatch = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    controller.retry_paused_preflight("w1")
    row = registry.get_worker_handoff("w1")
    assert row["pause_reason"] is None
    assert json.loads(row["evidence_json"])["dispatch_intent"] is False
    controller._dispatch.assert_called_once()


def test_permanent_failure_after_transfer_preserves_as_the_target(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    target_id = str(uuid.uuid4())
    _paused_after_stop(registry, "owner_transferred", "target_runtime_changed", target_id=target_id,
                       target_client="codex", target_model="gpt-5.6-sol", bootstrap_stopped=True)
    workspace = _released_workspace()
    workspace.handoff_status.return_value = _readback(_scope(source), "transferred", target_id)
    workspace.finish_handoff_after_target.return_value = {"protocol_version": 1, "phase": "finished"}
    tmux = MagicMock()
    tmux.has_session.return_value = False
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True
    worker = registry.get_worker("w1")
    assert (worker["status"], worker["client"], worker["native_session_id"], worker["tmux_session"]) == (
        "running", "codex", target_id, "ic-w1-h1")
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["disposition"] == "released_to_target" and evidence["assignment_finished"] is True
    workspace.finish_handoff_after_target.assert_called_once_with({**_scope(source), "capability_token": "token"})
    workspace.finish_handoff.assert_not_called()
    workspace.release_handoff.assert_not_called()


def test_finish_retry_uses_after_target_finish_then_completes(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    target_id = str(uuid.uuid4())
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-5.6-sol",
                                    native_session_id=target_id, tmux_session="ic-w1-h1", machine=None)
    for old, new in (("owner_transferred", "runtime_registered"), ("runtime_registered", "continuation_dispatched")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, "continuation_dispatched", {
        "scope": _scope(source), "transport": {}, "capability_token": "token", "target_id": target_id,
        "finish_intent": True})
    registry.pause_worker_handoff("w1", 1, "assignment_finish_unproved")
    workspace = MagicMock()
    workspace.handoff_status.side_effect = [_readback(_scope(source), "transferred", target_id),
                                            _readback(_scope(source), "finished", target_id)]
    workspace.finish_handoff_after_target.return_value = {"protocol_version": 1, "phase": "finished"}
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    assert controller.retry_paused_preflight("w1")["status"] == "completed"
    assert registry.get_worker("w1")["status"] == "running"
    workspace.finish_handoff_after_target.assert_called_once()
    workspace.finish_handoff.assert_not_called()


def test_target_commit_after_transfer_then_permanent_failure_finishes_and_preserves(tmp_path):
    primary = tmp_path / "primary"; primary.mkdir()
    for argv in (["git", "init", "-b", "main", str(primary)],
                 ["git", "-C", str(primary), "config", "user.email", "test@example.com"],
                 ["git", "-C", str(primary), "config", "user.name", "Test"]):
        subprocess.run(argv, check=True, capture_output=True)
    (primary / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "base"], check=True, capture_output=True)
    workspace_db = tmp_path / "workspace.db"
    runner, plugin_root = _native_runner(workspace_db)
    workspace = WorkspaceClient(plugin_root, runner=runner)
    source_id, target_id = str(uuid.uuid4()), str(uuid.uuid4())
    reserved = workspace.allocate({"repository_path": str(primary), "workspace_guid": str(uuid.uuid4()),
                                   "worker_id": "w1", "integration_target": "main"})
    assignment = workspace.bind({"repository_path": str(primary), "repository_identity": reserved["repository_identity"],
                                 "workspace_guid": reserved["workspace_guid"], "worker_id": "w1",
                                 "owner_session_id": source_id, "expected_lifecycle": "active",
                                 "expected_worktree_path": reserved["worktree_path"],
                                 "expected_branch": reserved["branch"], "expected_base_commit": reserved["base_commit"],
                                 "expected_current_head": reserved["current_head"]})
    worktree = Path(assignment["worktree_path"])
    registry = WorkerRegistry(init_db(str(tmp_path / "commander.db")))
    registry.register_worker("w1", "claude-opus", "ic-w1", repo=str(primary), description="original objective",
                             client="claude", model="opus", native_session_id=source_id)
    registry.set_worker_workspace("w1", assignment)
    worker = registry.get_worker("w1")
    head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    scope = {**_scope(worker, head), "repository_path": str(primary)}
    authority = WorkspaceHandoffAuthority(workspace_db)
    grant = authority.grant(workspace_guid=scope["workspace_guid"], generation=1,
                            repository_identity=scope["repository_identity"], worker_id="w1",
                            worktree_path=scope["expected_worktree_path"], branch=scope["expected_branch"],
                            base_commit=scope["expected_base_commit"], current_head=head,
                            integration_target=scope["expected_integration_target"],
                            source_owner_session_id=source_id)
    payload = {**scope, "capability_token": grant.capability_token}
    workspace.begin_handoff(payload)
    authority.authorize_target(workspace_guid=scope["workspace_guid"], generation=1,
                               capability_token=grant.capability_token, source_owner_session_id=source_id,
                               target_owner_session_id=target_id)
    workspace.transfer_handoff({**payload, "target_owner_session_id": target_id})
    # The target commits after the transfer, so the granted HEAD is no longer the live HEAD.
    (worktree / "target.txt").write_text("target work\n")
    subprocess.run(["git", "-C", str(worktree), "add", "target.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "target work"], check=True, capture_output=True)
    with pytest.raises(WorkspaceClientError, match="live Git evidence"):
        workspace.finish_handoff(payload)
    registry.claim_worker_handoff(worker, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "scope": scope, "transport": {}, "capability_token": grant.capability_token, "target_id": target_id,
        "target_client": "codex", "target_model": "gpt-5.6-sol", "target_tier": "opus", "bootstrap_stopped": True})
    registry.pause_worker_handoff("w1", 1, "target_runtime_changed")
    tmux = MagicMock()
    tmux.has_session.return_value = False
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None),
        process=MagicMock(), runtime=MagicMock(), authority=authority)
    result = controller.retry_paused_preflight("w1")
    assert result["status"] == "released" and result["preserve"] is True, result
    assert workspace.handoff_status({"workspace_guid": scope["workspace_guid"]})["phase"] == "finished"
    current = registry.get_worker("w1")
    assert (current["status"], current["client"], current["native_session_id"]) == ("running", "codex", target_id)
    assert json.loads(registry.get_worker_handoff("w1")["evidence_json"])["disposition"] == "released_to_target"


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
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    pm = tmp_path / "pm.db"
    _pm_db(pm, worker["native_session_id"])
    db = sqlite3.connect(pm)
    db.execute("UPDATE sessions SET workflow_stage='plan_interrupted' WHERE terminal_session=?",
               (worker["native_session_id"],))
    db.commit(); db.close()
    state = MagicMock()
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(),
                            _ssh_manager=None, _provider_router=lambda: (None, None, state, None))
    process, runtime = MagicMock(), MagicMock()
    controller = WorkerHandoffController(tools, process=process, runtime=runtime,
                                         authority=MagicMock(), pm_db_path=pm)
    controller._target = lambda _worker: (SimpleNamespace(client="codex", model="gpt-6-sol"), "opus")
    controller._preflight = lambda _worker, _handle, _generation: ({}, {}, "head")
    verdict = {"state":"exhausted", "client":"claude", "native_session_id":worker["native_session_id"],
               "generation":"1", "event_key":"native-event", "cursor":12, "file_identity":"1:2"}
    controller._validate_quota = lambda _worker, _generation, proposed: proposed
    result = controller.handoff("w1", verdict)
    assert result == {"status":"rejected", "worker_id":"w1", "reason":"operator_paused_pm"}
    assert registry.get_worker_handoff("w1") is None
    assert registry.get_worker("w1")["status"] == "running"
    state.mark_unavailable.assert_called_once()
    process.snapshot.assert_not_called()
    process.stop_and_prove.assert_not_called()
    runtime.bootstrap.assert_not_called()


def test_pretransfer_failure_stops_only_verified_readonly_bootstrap(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    registry.claim_worker_handoff(source, generation=1)
    for before, after in (("claimed", "source_stopped"), ("source_stopped", "target_ready")):
        registry.advance_worker_handoff("w1", 1, before, after)
    scope = {"workspace_guid":source["workspace_guid"], "generation":1,
             "repository_identity":source["workspace_repository_identity"], "worker_id":"w1",
             "source_owner_session_id":source["native_session_id"],
             "expected_worktree_path":source["workspace_path"], "expected_branch":source["workspace_branch"],
             "expected_base_commit":source["workspace_base_commit"],
             "expected_current_head":"head", "expected_integration_target":source["workspace_integration_target"]}
    target_id = str(uuid.uuid4())
    snapshot = {"session_name":"ic-w1-h1-bootstrap", "processes":[{"pid":12345}]}
    registry.record_worker_handoff_evidence("w1", 1, "target_ready", {
        "scope":scope, "transport":{}, "target_id":target_id, "bootstrap_process":snapshot})
    workspace = MagicMock()
    workspace.handoff_status.return_value = {"protocol_version":1, "phase":"begun",
        "target_owner_session_id":None, **{
            "workspace_guid":scope["workspace_guid"], "generation":1,
            "repository_identity":scope["repository_identity"], "worker_id":"w1",
            "source_owner_session_id":scope["source_owner_session_id"],
            "worktree_path":scope["expected_worktree_path"], "branch":scope["expected_branch"],
            "base_commit":scope["expected_base_commit"], "current_head":"head",
            "integration_target":scope["expected_integration_target"]}}
    process = MagicMock()
    process.stop_and_prove.return_value = {"stopped":True, "source_pids":[12345]}
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=workspace,
                            _ssh_manager=None)
    controller = WorkerHandoffController(tools, process=process, runtime=MagicMock(),
                                         authority=MagicMock(), pm_db_path=tmp_path / "pm.db")
    controller._prepare_pm = lambda *_args: (_ for _ in ()).throw(HandoffHold("pm_prepare_failed"))
    held = controller.reconcile("w1")
    assert held["reason"] == "pm_prepare_failed"
    assert held["phase"] == "target_ready"
    process.stop_and_prove.assert_called_once_with(snapshot)
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["cleanup_intent"] is False and evidence["bootstrap_cleanup_proof"]["stopped"] is True


@pytest.mark.parametrize(
    "source_client,target_client,with_registry_task,opposite_available,scenario",
    [(source, target, task, available, "normal")
     for source, target in (("claude", "codex"), ("codex", "claude"))
     for task in (True, False) for available in (True, False)] + [
         ("claude", "codex", False, True, "late_recovery"),
         ("codex", "claude", False, True, "preclaim_rejection"),
         ("claude", "codex", True, False, "restart_before_hold"),
         ("claude", "codex", True, True, "fresh_quota_before_stop"),
         ("claude", "codex", False, True, "fable_fallback"),
         ("codex", "claude", False, True, "fable_fallback"),
     ],
)
def test_full_storage_backed_native_handoff_both_directions(tmp_path, monkeypatch, source_client, target_client, with_registry_task, opposite_available, scenario):
    primary = tmp_path / "primary"; primary.mkdir()
    for argv in (["git","init","-b","main",primary],["git","-C",primary,"config","user.email","test@example.com"],["git","-C",primary,"config","user.name","Test"]): subprocess.run(argv, check=True, capture_output=True)
    (primary / "tracked.txt").write_text("base\n"); (primary / "AGENTS.md").write_text("rules\n"); (primary / "CLAUDE.md").write_text("rules\n")
    subprocess.run(["git","-C",primary,"add","."], check=True); subprocess.run(["git","-C",primary,"commit","-m","base"], check=True, capture_output=True)
    source_id = str(uuid.uuid4()); workspace_db = tmp_path / "workspace.db"; runner, plugin_root = _native_runner(workspace_db)
    workspace = WorkspaceClient(plugin_root, runner=runner)
    reserved = workspace.allocate({"repository_path":str(primary),"workspace_guid":str(uuid.uuid4()),"worker_id":"w1","integration_target":"main"})
    assignment = workspace.bind({"repository_path":str(primary),"repository_identity":reserved["repository_identity"],"workspace_guid":reserved["workspace_guid"],"worker_id":"w1","owner_session_id":source_id,"expected_lifecycle":"active","expected_worktree_path":reserved["worktree_path"],"expected_branch":reserved["branch"],"expected_base_commit":reserved["base_commit"],"expected_current_head":reserved["current_head"]})
    worktree = Path(assignment["worktree_path"]); (worktree / "tracked.txt").write_text("staged\n"); subprocess.run(["git","-C",worktree,"add","tracked.txt"], check=True); (worktree / "tracked.txt").write_text("unstaged\n"); (worktree / "untracked.txt").write_text("u\n"); (worktree / "shared-link").symlink_to("untracked.txt")
    before = subprocess.run(["git","-C",worktree,"status","--porcelain=v1","--untracked-files=all"],capture_output=True,text=True,check=True).stdout
    registry = WorkerRegistry(init_db(str(tmp_path / "commander.db"))); objective = registry.create_objective("unfinished"); task = registry.create_task(objective,"continue")
    worker_type = "claude-fable" if scenario == "fable_fallback" else "claude-opus"
    source_model = (("fable" if source_client == "claude" else "gpt-6-astra")
                    if scenario == "fable_fallback" else
                    ("opus" if source_client == "claude" else "gpt-5.6-sol"))
    registry.register_worker("w1", worker_type, "ic-w1", task_id=task if with_registry_task else None, repo=str(primary), description="original objective", client=source_client, model=source_model, native_session_id=source_id)
    registry.set_worker_workspace("w1", assignment)
    home = tmp_path / "home"; quota_path = _write_quota(home, source_client, source_id, worktree); quota = read_native_quota(quota_path, client=source_client, native_session_id=source_id, workspace_path=str(worktree), generation="1")
    assert quota["state"] == "exhausted", quota
    pm = tmp_path / "pm.db"; _pm_db(pm, source_id)
    ps, tmux = _HandoffPs(), _HandoffTmux(); root = _HandoffProc(40000,["zsh"],worktree); argv = [source_client] + (["--no-daemon"] if source_client == "codex" else []); child = _HandoffProc(40001,argv,worktree,root); root._pool = child._pool = [root,child]; ps.processes = {root.pid:root,child.pid:child}; tmux.panes["ic-w1"] = root
    hooks = tmp_path / "hooks"; hooks.mkdir(); (hooks / f"ironclaude-session-{child.pid if source_client == 'codex' else root.pid}.id").write_text(source_id)
    raw = {"clients":{"claude":{"enabled":True,"path":"claude","models":{"haiku":"haiku","sonnet":"sonnet","opus":"opus","fable":"fable"}},"codex":{"enabled":True,"path":"codex","models":{"haiku":"gpt-5.6-luna","sonnet":"gpt-5.6-terra","opus":"gpt-5.6-sol","fable":"gpt-6-astra"}}},"roles":{role:{"preferred":"claude","clients":["claude","codex"]} for role in ("brain","worker","grader","advisor")},"shadow_mode":False}
    state = ProviderState(registry._conn); caps = CapabilityRegistry(state)
    class Probe:
        def probe_local(self, config, client, role, tier):
            ready = (tier == "opus") if scenario == "fable_fallback" else (opposite_available or client == source_client)
            return ClientCapability("local",client,role,tier,ready,ready,ready,ready,ready)
    monkeypatch.setattr("ironclaude.provider_capabilities.CapabilityProbe", Probe)
    monkeypatch.setattr(workspace, "discover_installed_plugin_root", lambda client: str(plugin_root))
    tools = SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace, _ssh_manager=None, _provider_router=lambda: (ProviderRouter(parse_provider_config(raw),state,caps),parse_provider_config(raw),state,caps))
    controller = WorkerHandoffController(tools, process=NativeProcessAdapter(tmux, psutil_module=ps), runtime=_NativeRuntime(ps,tmux,home,pm), authority=WorkspaceHandoffAuthority(workspace_db), pm_db_path=pm, native_log_root=home, session_id_dir=hooks)
    late_success = scenario == "late_recovery"
    if late_success:
        def source_recovers_during_target_preflight(_client):
            success = {"session_id":source_id, "sessionId":source_id, "cwd":str(worktree),
                       "timestamp":"2026-10-05T12:00:01Z", "type":"assistant", "uuid":"recovered",
                       "message":{"role":"assistant", "model":"claude-opus", "stop_reason":"end_turn",
                                  "usage":{"output_tokens":2}}}
            with quota_path.open("a") as stream:
                stream.write(json.dumps(success) + "\n")
        controller.runtime.preflight = source_recovers_during_target_preflight
    if scenario == "fresh_quota_before_stop":
        def source_reexhausts_during_target_preflight(_client):
            assert state.recover_usage_quarantine(
                "local", source_client, "worker",
                state.worker_usage_scope_tier("worker", "opus"),
                native_generation=f"{uuid.uuid4()}:other-file:1", native_cursor=1,
                native_event_key="other-worker-success", native_state="available",
                native_event_timestamp="2026-10-05T12:02:00Z")
            assert state.is_available("local", source_client, "worker", "opus")
            fresh = json.loads(quota_path.read_text().strip())
            fresh["timestamp"] = "2026-10-05T12:03:00Z"
            fresh["uuid"] = "fresh-quota"
            with quota_path.open("a") as stream:
                stream.write(json.dumps(fresh) + "\n")
        controller.runtime.preflight = source_reexhausts_during_target_preflight
    # The daemon reads native evidence and calls the real private facade; only
    # provider/process boundaries use the test runtime above.
    result_box = {}
    monkeypatch.setattr("ironclaude.worker_handoff.WorkerHandoffController", lambda _tools: controller)
    def handoff(worker_id, verdict):
        result_box["result"] = OrchestratorTools.handoff_worker(tools, worker_id, verdict)
        return result_box["result"]
    tools.handoff_worker = handoff
    daemon = IroncladeDaemon.__new__(IroncladeDaemon)
    daemon.registry = registry
    daemon._native_log_root = home
    daemon._get_orchestrator = lambda: tools
    daemon.slack = MagicMock(); daemon.brain = MagicMock()
    daemon._worker_idle_since = {}; daemon._prompt_store = lambda: None
    tools.retry_worker_handoff_preflight = lambda worker_id: OrchestratorTools.retry_worker_handoff_preflight(tools, worker_id)
    if scenario == "restart_before_hold":
        # Crash after the atomic claim and before any workspace effect. A new
        # controller must install the source hold before considering a target.
        source_worker = registry.get_worker("w1")
        scope, transport, head = controller._source_preflight(source_worker, 1)
        source_process = controller.process.snapshot("ic-w1", str(worktree), source_client)
        prepared = {"quota_verdict": quota, "scope": scope, "transport": transport,
                    "pm_snapshot": snapshot_pm_state(pm, source_id, "project"),
                    "source_process": source_process, "source_head": head,
                    "continuation_objective": "original objective"}
        registry.claim_worker_handoff(source_worker, generation=1,
                                      reconcile_nonce="crashed", initial_evidence=prepared)
        row = registry.get_worker_handoff("w1")
        evidence = json.loads(row["evidence_json"])
        assert evidence["scope"] == scope and evidence["quota_verdict"]["state"] == "exhausted"
        evidence["reconcile_owner"] = {"nonce": "crashed", "pid": 2147483647, "started": 0}
        registry._conn.execute("UPDATE worker_handoffs SET evidence_json=? WHERE worker_id='w1'",
                               (json.dumps(evidence),))
        registry._conn.commit()
        restarted = WorkerHandoffController(
            tools, process=NativeProcessAdapter(tmux, psutil_module=ps),
            runtime=_NativeRuntime(ps, tmux, home, pm),
            authority=WorkspaceHandoffAuthority(workspace_db), pm_db_path=pm,
            native_log_root=home, session_id_dir=hooks,
        )
        pending = restarted.reconcile("w1")
        assert pending["status"] == "held" and pending["reason"] == "target_preflight_pending"
        held_status = workspace.handoff_status({"workspace_guid": assignment["workspace_guid"]})
        assert held_status["phase"] == "begun" and held_status["source_owner_session_id"] == source_id
        request = {"repository_path": str(primary), "workspace_guid": assignment["workspace_guid"]}
        with pytest.raises(WorkspaceClientError, match="handoff is pending"):
            workspace.sync({**request, "owner_session_id": source_id})
        with pytest.raises(WorkspaceClientError, match="handoff is pending"):
            workspace.reap(request)
        assert tmux.panes["ic-w1"] is root
        return
    if scenario == "preclaim_rejection":
        (worktree / "CLAUDE.md").unlink()
        for _ in range(2):
            daemon._maybe_start_handoff(registry.get_worker("w1"))
        assert daemon._handoff_suppresses(registry.get_worker("w1")) is False
        assert result_box["result"] == {"status": "rejected", "worker_id": "w1",
                                        "reason": "workspace_instructions_missing"}
        assert registry.get_worker_handoff("w1") is None
        trusted = registry.get_native_quota_evidence("w1")["verdict"]
        assert trusted["state"] == "exhausted" and trusted["reader_state"] == quota["reader_state"]
        assert trusted["cursor"] == quota["cursor"]
        assert daemon.slack.post_message.call_count == 1
        assert "Workspace assignment hold is unproved" in daemon.slack.post_message.call_args.args[0]
        assert tmux.panes["ic-w1"] is root
        assert workspace.handoff_status({"workspace_guid":assignment["workspace_guid"]})["present"] is False
        return
    daemon._maybe_start_handoff(registry.get_worker("w1"))
    result = result_box["result"]
    if late_success:
        assert result["status"] == "held" and result["reason"] == "native_quota_evidence_changed", result
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        assert tmux.panes["ic-w1"] is root and child.gone is False
        assert controller.runtime.next_pid == 41000
        assert workspace.handoff_status({"workspace_guid":assignment["workspace_guid"]})["phase"] == "begun"
        released = tools.retry_worker_handoff_preflight("w1")
        assert released["status"] == "released" and released["phase"] == "released", released
        assert registry.get_worker("w1")["status"] == "running"
        assert tmux.panes["ic-w1"] is root and child.gone is False
        released_status = workspace.handoff_status({"workspace_guid": assignment["workspace_guid"]})
        assert released_status["phase"] == "finished" and released_status["target_owner_session_id"] is None
        assert registry.effective_worker_tier(registry.get_worker("w1")) == "opus"
        return
    if not opposite_available:
        assert result["status"] == "held" and result["reason"] == "opposite_capability_unavailable", result
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        assert registry.get_worker_handoff("w1")["generation"] == 1
        held_status = workspace.handoff_status({"workspace_guid": assignment["workspace_guid"]})
        assert held_status["phase"] == "begun" and held_status["generation"] == 1
        assert held_status["source_owner_session_id"] == source_id
        request = {"repository_path": str(primary), "workspace_guid": assignment["workspace_guid"]}
        with pytest.raises(WorkspaceClientError, match="handoff is pending"):
            workspace.sync({**request, "owner_session_id": source_id})
        with pytest.raises(WorkspaceClientError, match="handoff is pending"):
            workspace.reap(request)
        for _ in range(2):
            daemon.check_workers()
        assert registry.get_worker_handoff("w1")["generation"] == 1
        assert registry.get_worker_handoff("w1")["pause_reason"] == "opposite_capability_unavailable"
        assert daemon.slack.post_message.call_count == 1
        assert daemon.brain.send_message.call_count == 1
        assert tmux.panes["ic-w1"] is root
        return
    assert result["status"] == "completed", result
    worker = registry.get_worker("w1"); assert (worker["client"], worker["native_session_id"] != source_id, worker["status"]) == (target_client, True, "running")
    if scenario == "fresh_quota_before_stop":
        assert not state.is_available("local", source_client, "worker", "opus")
        incident = state.unavailable_reason("local", source_client, "worker", "opus")
        assert incident["reason"] == "native_usage_exhausted"
        stamp = registry._conn.execute(
            "SELECT usage_event_timestamp FROM provider_capability_state "
            "WHERE host='local' AND client=? AND role='worker' AND tier=?",
            (source_client, state.worker_usage_scope_tier("worker", "opus")),
        ).fetchone()[0]
        assert stamp == "2026-10-05T12:03:00+00:00"
        target_pane = tmux.panes[worker["tmux_session"]]
        (hooks / f"ironclaude-session-{target_pane.children()[0].pid}.id").write_text(
            worker["native_session_id"])
        target_quota = _write_quota(home, target_client, worker["native_session_id"], worktree)
        assert read_native_quota(target_quota, client=target_client,
            native_session_id=worker["native_session_id"], workspace_path=str(worktree),
            generation="2")["state"] == "exhausted"
        daemon._maybe_start_handoff(worker)
        assert daemon._handoff_suppresses(registry.get_worker("w1")) is False
        assert result_box["result"]["status"] == "held", result_box["result"]
        assert result_box["result"]["reason"] == "opposite_capability_unavailable"
        return
    if scenario == "fable_fallback":
        assert worker["type"] == "claude-fable"
        assert registry.effective_worker_tier(worker) == "opus"
        completed = registry.get_worker_handoff("w1")
        assert json.loads(completed["evidence_json"])["target_tier"] == "opus"
        target_pane = tmux.panes[worker["tmux_session"]]
        target_producer = target_pane.children()[0] if target_client == "codex" else target_pane
        (hooks / f"ironclaude-session-{target_producer.pid}.id").write_text(worker["native_session_id"])
        assert not state.is_available("local", source_client, "worker", "fable")
        assert not state.is_available("local", source_client, "worker", "opus")
        target_quota = _write_quota(home, target_client, worker["native_session_id"], worktree)
        if target_client == "claude":
            native_log = home / ".claude/projects/target" / f"{worker['native_session_id']}.jsonl"
            native_log.write_text(target_quota.read_text())
            target_quota.unlink()
            target_quota = native_log
        packet = read_native_quota(target_quota, client=target_client,
            native_session_id=worker["native_session_id"], workspace_path=str(worktree), generation="2")
        assert packet["state"] == "exhausted"
        daemon._maybe_start_handoff(worker)
        assert state.unavailable_reason("local", target_client, "worker", "opus") is not None
        account_state = state.unavailable_reason(
            "local", target_client, "worker", state.worker_usage_scope_tier("worker", "fable"))
        assert account_state is not None and account_state["category"] == "usage"
        assert not state.is_available("local", target_client, "worker", "fable")
        assert result_box["result"]["status"] == "held", result_box["result"]
        assert result_box["result"]["reason"] == "opposite_capability_unavailable"
        assert registry.get_worker("w1")["status"] == "handoff_pending"
        return
    assert worker["task_id"] == (task if with_registry_task else None)
    assert workspace.handoff_status({"workspace_guid":assignment["workspace_guid"]})["target_owner_session_id"] == worker["native_session_id"]
    assert subprocess.run(["git","-C",worktree,"status","--porcelain=v1","--untracked-files=all"],capture_output=True,text=True,check=True).stdout == before
    db = sqlite3.connect(pm); assert [r[0] for r in db.execute("SELECT status FROM wave_tasks WHERE terminal_session=? ORDER BY task_id",(worker["native_session_id"],))] == ["review_passed"] * 5 + ["pending", "pending"]


def test_lost_dispatch_response_stays_held_without_resending(tmp_path):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    registry.claim_worker_handoff(worker, generation=1)
    for previous, phase in zip(("claimed", "source_stopped", "target_ready", "pm_prepared", "owner_transferred"), ("source_stopped", "target_ready", "pm_prepared", "owner_transferred", "runtime_registered")):
        registry.advance_worker_handoff("w1", 1, previous, phase)
    registry.record_worker_handoff_evidence("w1", 1, "runtime_registered", {"dispatch_intent": True})
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None)
    controller = WorkerHandoffController(tools, process=MagicMock(), runtime=MagicMock(), authority=MagicMock(), pm_db_path=tmp_path / "pm.db")
    held = controller.reconcile("w1")
    assert held == {"status":"held", "worker_id":"w1", "generation":1, "phase":"runtime_registered", "reason":"uncertain_external_effect"}
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    controller.reconcile("w1")
    tools.tmux.send_keys.assert_not_called()


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_resume_readback_requires_ready_before_runtime_registration(tmp_path, client):
    registry = _registry(tmp_path)
    if client == "claude":
        registry.set_worker_provider("w1", "codex", "gpt-5.6-sol")
    source = registry.get_worker("w1")
    registry.claim_worker_handoff(source, generation=1)
    for old, new in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                     ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, old, new)
    target_id = str(uuid.uuid4())
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "resume_intent": "ic-w1-h1", "resume_started_at": 123.0,
        "target_client": client, "target_id": target_id, "target_model": "target-model",
    })
    tmux = MagicMock()
    tmux.has_session.return_value = True
    workspace = MagicMock()
    tools = SimpleNamespace(registry=registry, tmux=tmux, _workspace_client=workspace,
                            _ssh_manager=None, _config={})
    tools._wait_for_ready = MagicMock(return_value=False)
    tools._read_pm_state_via_sqlite = MagicMock(return_value={
        "session_uuid": target_id, "professional_mode": "on"})
    runtime = InstalledNativeRuntime(tools)
    runtime._fresh_claude_binding = MagicMock(return_value=True)
    controller = WorkerHandoffController(tools, runtime=runtime, process=MagicMock(),
                                         authority=MagicMock())
    held = controller.reconcile("w1")
    assert held["status"] == "held" and held["phase"] == "owner_transferred", held
    assert held["reason"] == "uncertain_external_effect"
    assert registry.get_worker("w1")["native_session_id"] == source["native_session_id"]
    tmux.send_keys.assert_not_called()
    workspace.finish_handoff.assert_not_called()


def test_paused_preeffect_retry_requarantines_newer_exhaustion(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    verdict = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                workspace_path=worker["workspace_path"], generation="1")
    state = ProviderState(registry._conn)
    pm = tmp_path / "pm.db"
    _pm_db(pm, worker["native_session_id"])
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(),
                            _ssh_manager=None, _provider_router=lambda: (None, None, state, None))
    process = MagicMock()
    controller = WorkerHandoffController(tools, process=process, runtime=MagicMock(),
                                         authority=MagicMock(), native_log_root=home, pm_db_path=pm)
    controller._source_preflight = MagicMock(return_value=({"worker_id": "w1"}, {}, "head"))
    controller._begin_source_hold = MagicMock()
    process.snapshot.return_value = {"processes": [{"pid": 100}], "pane_pid": 100}
    controller._verify_hook_binding = MagicMock()
    controller._target = MagicMock(side_effect=HandoffHold("opposite_capability_unavailable"))
    held = controller.handoff("w1", verdict)
    assert held["status"] == "held" and held["phase"] == "claimed", held
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    key = ("local", "claude", "worker", "opus")
    assert not state.is_available(*key)
    assert state.recover_usage_quarantine(
        "local", "claude", "worker", state.worker_usage_scope_tier("worker", "opus"),
        native_generation=f"{uuid.uuid4()}:other-file:1", native_cursor=1,
        native_event_key="other-worker-success", native_state="available",
        native_event_timestamp="2026-10-05T12:02:00Z")
    assert state.is_available(*key)
    newer_quota = json.loads(path.read_text().strip())
    newer_quota["timestamp"] = "2026-10-05T12:03:00Z"
    newer_quota["uuid"] = "fresh-quota"
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "user", "session_id": worker["native_session_id"],
                                 "sessionId": worker["native_session_id"],
                                 "cwd": worker["workspace_path"],
                                 "timestamp": "2026-10-05T12:00:01Z",
                                 "message": {"role": "user"}}) + "\n")
        stream.write(json.dumps(newer_quota) + "\n")
    controller._target = MagicMock(return_value=(SimpleNamespace(
        client="codex", model="gpt-5.6-sol", effective_tier="opus"), "opus"))
    controller._preflight = MagicMock(return_value=({"worker_id": "w1"}, {}, "head"))
    monkeypatch.setattr("ironclaude.worker_handoff._project_hash", lambda *_args: "project")
    monkeypatch.setattr("ironclaude.worker_handoff.snapshot_pm_state", lambda *_args: {
        "session": {"workflow_stage": "executing", "plan_json": json.dumps({"goal": "continue"})}})
    process.snapshot.return_value = {"processes": [{"pid": 100}], "pane_pid": 100}
    controller._verify_hook_binding = MagicMock()
    controller.reconcile = MagicMock(return_value={"status": "pending", "worker_id": "w1"})
    assert controller.retry_paused_preflight("w1")["status"] == "pending"
    evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
    assert evidence["quota_verdict"]["state"] == "exhausted"
    assert evidence["quota_verdict"]["cursor"] > verdict["cursor"]
    assert evidence["quota_verdict"]["event_timestamp"] == "2026-10-05T12:03:00+00:00"
    assert not state.is_available(*key)
    assert state.unavailable_reason(*key)["reason"] == "native_usage_exhausted"
    stamp = registry._conn.execute(
        "SELECT usage_event_timestamp FROM provider_capability_state "
        "WHERE host='local' AND client='claude' AND role='worker' AND tier=?",
        (state.worker_usage_scope_tier("worker", "opus"),),
    ).fetchone()[0]
    assert stamp == "2026-10-05T12:03:00+00:00"
    assert registry.get_worker_handoff("w1")["pause_reason"] is None
    controller.reconcile.assert_called_once_with("w1")


@pytest.mark.parametrize("phase,method,intent", [
    ("source_stopped", "_start_target", "bootstrap_intent"),
    ("owner_transferred", "_resume_target", "resume_intent"),
    ("runtime_registered", "_dispatch", "dispatch_intent"),
])
def test_two_connections_cannot_run_same_phase_effect_concurrently(tmp_path, phase, method, intent):
    setup = _registry(tmp_path)
    setup.claim_worker_handoff(setup.get_worker("w1"), generation=1)
    phases = ("claimed", "source_stopped", "target_ready", "pm_prepared",
              "owner_transferred", "runtime_registered")
    for old, new in zip(phases, phases[1:]):
        if old == phase:
            break
        setup.advance_worker_handoff("w1", 1, old, new)

    db_path = str(tmp_path / "commander.db")
    first = WorkerRegistry(sqlite3.connect(db_path, check_same_thread=False, timeout=5))
    second = WorkerRegistry(sqlite3.connect(db_path, check_same_thread=False, timeout=5))
    c1 = WorkerHandoffController(SimpleNamespace(registry=first, _workspace_client=MagicMock()),
                                 process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    c2 = WorkerHandoffController(SimpleNamespace(registry=second, _workspace_client=MagicMock()),
                                 process=MagicMock(), runtime=MagicMock(), authority=MagicMock())
    entered, release = threading.Event(), threading.Event()
    calls = []

    def first_effect(worker_id, generation, _evidence):
        calls.append("first")
        entered.set()
        assert release.wait(5)
        first.record_worker_handoff_evidence(worker_id, generation, phase, {intent: "effect-started"})
        return c1._status(worker_id)

    setattr(c1, method, first_effect)
    setattr(c2, method, lambda *_args: calls.append("second"))
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(c1.reconcile("w1")))
    thread.start()
    try:
        assert entered.wait(5)
        blocked = c2.reconcile("w1")
        assert blocked["reason"] == "handoff_reconcile_owned"
        assert calls == ["first"]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert outcome[0]["phase"] == phase
    assert c2.reconcile("w1")["reason"] == "uncertain_external_effect"
    assert calls == ["first"]


def test_inaccessible_prior_reconcile_owner_cannot_authorize_new_effect(tmp_path, monkeypatch):
    import psutil
    registry = _registry(tmp_path)
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=1)
    registry.record_worker_handoff_evidence("w1", 1, "claimed", {
        "reconcile_owner": {"nonce": "old", "pid": 987654321, "started": 1.0},
    })
    actual_process = psutil.Process

    def inaccessible(pid):
        if pid == 987654321:
            raise psutil.AccessDenied(pid)
        return actual_process(pid)

    monkeypatch.setattr("ironclaude.worker_registry.psutil.Process", inaccessible)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, _workspace_client=MagicMock()),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock(),
    )
    controller._stop_source = MagicMock()

    result = controller.reconcile("w1")

    assert result["reason"] == "handoff_reconcile_owner_unproved"
    controller._stop_source.assert_not_called()


def test_dead_prior_owner_reconciles_intent_without_replaying_effect(tmp_path, monkeypatch):
    import psutil
    registry = _registry(tmp_path)
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=1)
    registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    registry.record_worker_handoff_evidence("w1", 1, "source_stopped", {
        "bootstrap_intent": "ic-w1-h1-bootstrap",
        "reconcile_owner": {"nonce": "dead", "pid": 987654321, "started": 1.0},
    })
    actual_process = psutil.Process

    def dead_owner(pid):
        if pid == 987654321:
            raise psutil.NoSuchProcess(pid)
        return actual_process(pid)

    monkeypatch.setattr("ironclaude.worker_registry.psutil.Process", dead_owner)
    controller = WorkerHandoffController(
        SimpleNamespace(registry=registry, _workspace_client=MagicMock()),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock(),
    )
    controller._start_target = MagicMock()

    result = controller.reconcile("w1")

    assert result["reason"] == "uncertain_external_effect"
    controller._start_target.assert_not_called()


def test_initial_claim_owns_gap_before_first_evidence_write(tmp_path):
    setup = _registry(tmp_path)
    nonce = "initial-owner"
    setup.claim_worker_handoff(setup.get_worker("w1"), generation=1,
                               reconcile_nonce=nonce)
    second = WorkerRegistry(sqlite3.connect(str(tmp_path / "commander.db"), timeout=5))
    rival = WorkerHandoffController(
        SimpleNamespace(registry=second, _workspace_client=MagicMock()),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock(),
    )
    rival._stop_source = MagicMock()

    blocked = rival.reconcile("w1")

    assert blocked["reason"] == "handoff_reconcile_owned"
    rival._stop_source.assert_not_called()
    starter = WorkerHandoffController(
        SimpleNamespace(registry=setup, _workspace_client=MagicMock()),
        process=MagicMock(), runtime=MagicMock(), authority=MagicMock(),
    )
    with starter._reconcile_scope("w1", 1, preclaimed_nonce=nonce) as denial:
        assert denial is None
        setup.record_worker_handoff_evidence("w1", 1, "claimed", {"init_started": True})
    assert json.loads(setup.get_worker_handoff("w1")["evidence_json"])["init_started"] is True


def test_claim_persists_prepared_source_packet_and_owner_atomically(tmp_path):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    packet = {"quota_verdict": {"state": "exhausted", "native_session_id": source["native_session_id"]},
              "scope": {"expected_worktree_path": source["workspace_path"]},
              "source_process": {"session_name": source["tmux_session"], "processes": [{"pid": 123}]}}

    row = registry.claim_worker_handoff(source, generation=1,
                                        reconcile_nonce="first-owner", initial_evidence=packet)

    saved = json.loads(row["evidence_json"])
    assert all(saved[key] == value for key, value in packet.items())
    assert saved["reconcile_owner"]["nonce"] == "first-owner"
    assert saved["reconcile_owner"]["pid"] > 0
    assert registry.get_worker("w1")["status"] == "handoff_pending"


@pytest.mark.parametrize("change", ["same_exhaustion", "available", "new_file", "new_generation", "new_native"])
def test_quota_refresh_preserves_rejection_only_for_same_exhausted_source(tmp_path, change):
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    verdict = {"state": "exhausted", "client": "claude",
               "native_session_id": source["native_session_id"], "generation": "1",
               "file_identity": "file-one", "cursor": 10, "event_key": "quota-one",
               "reader_state": {"last": 10}}
    registry.record_native_quota_verdict("w1", 1, verdict, snapshot=source)
    assert registry.record_native_handoff_rejection("w1", 1, "workspace_instructions_missing",
                                                     snapshot=source)
    assert not registry.record_native_handoff_rejection("w1", 1, "workspace_instructions_missing",
                                                        snapshot=source)
    fresh = {**verdict, "cursor": 12, "reader_state": {"last": 12}}
    generation = 1
    if change == "available":
        fresh["state"] = "available"
    elif change == "new_file":
        fresh["file_identity"] = "file-two"
    elif change == "new_generation":
        generation = 2
        fresh["generation"] = "2"
    elif change == "new_native":
        native_id = str(uuid.uuid4())
        registry._conn.execute("UPDATE workers SET native_session_id=? WHERE id='w1'", (native_id,))
        registry._conn.commit()
        source = registry.get_worker("w1")
        fresh["native_session_id"] = native_id
    registry.record_native_quota_verdict("w1", generation, fresh, snapshot=source)
    saved = registry.get_native_quota_evidence("w1")["verdict"]
    assert saved["cursor"] == 12 and saved["reader_state"] == {"last": 12}
    assert (saved.get("handoff_rejection") == "workspace_instructions_missing") == (change == "same_exhaustion")


def test_controller_reuses_authenticated_daemon_eof_without_rescanning(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    quota_line = path.read_text()
    rows = [{"type": "user", "session_id": worker["native_session_id"],
             "sessionId": worker["native_session_id"], "cwd": worker["workspace_path"],
             "timestamp": "2026-10-05T11:59:59Z", "message": {"role": "user"}} for _ in range(3)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + quota_line)
    verdict = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                workspace_path=worker["workspace_path"], generation="1")
    registry.record_native_quota_verdict("w1", 1, verdict, snapshot=worker)
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None)
    controller = WorkerHandoffController(tools, native_log_root=home)
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    import ironclaude.worker_handoff as handoff_module
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(handoff_module.time, "monotonic", lambda: next(clock))
    assert controller._validate_quota(worker, 1, {"event_key": verdict["event_key"],
        "cursor": verdict["cursor"], "file_identity": verdict["file_identity"],
        "state": "exhausted", "reader_state": {"latest_state": "exhausted"}})["state"] == "exhausted"
    replacement = path.with_suffix(".replacement")
    replacement.write_text(path.read_text())
    os.replace(replacement, path)
    with pytest.raises(HandoffHold, match="native_quota_evidence_changed"):
        controller._validate_quota(worker, 1, verdict)


def test_controller_scan_progress_survives_budget_and_reconstruction(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    import ironclaude.worker_handoff as handoff_module
    registry = _registry(tmp_path)
    worker = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", worker["native_session_id"], Path(worker["workspace_path"]))
    quota_line = path.read_text()
    rows = [{"type": "user", "session_id": worker["native_session_id"],
             "sessionId": worker["native_session_id"], "cwd": worker["workspace_path"],
             "timestamp": "2026-10-05T11:59:59Z", "message": {"role": "user"}} for _ in range(3)]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows) + quota_line)
    proposed = read_native_quota(path, client="claude", native_session_id=worker["native_session_id"],
                                 workspace_path=worker["workspace_path"], generation="1")
    assert proposed["state"] == "exhausted"
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(handoff_module.time, "monotonic", lambda: next(clock))
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None)
    cursors = []
    for _ in range(4):
        controller = WorkerHandoffController(tools, native_log_root=home)
        with pytest.raises(HandoffHold, match="scan_budget"):
            controller._validate_quota(worker, 1, proposed, require_same_event=False)
        saved = registry.get_native_quota_evidence("w1")["verdict"]
        assert saved["state"] == "unknown" and saved["reason"] == "native_quota_log_scan_budget_exceeded"
        cursors.append(saved["cursor"])
    assert cursors == sorted(set(cursors))
    final = WorkerHandoffController(tools, native_log_root=home)._validate_quota(
        worker, 1, proposed, require_same_event=False)
    assert final["state"] == "exhausted" and final["cursor"] == proposed["cursor"]


@pytest.mark.parametrize("paused", [False, True])
def test_claimed_controller_scan_progress_survives_reconstruction(tmp_path, monkeypatch, paused):
    import ironclaude.worker_quota as quota_reader
    import ironclaude.worker_handoff as handoff_module
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", source["native_session_id"], Path(source["workspace_path"]))
    proposed = read_native_quota(path, client="claude", native_session_id=source["native_session_id"],
                                 workspace_path=source["workspace_path"], generation="1")
    registry.claim_worker_handoff(source, generation=1, initial_evidence={"quota_verdict": proposed})
    if paused:
        registry.pause_worker_handoff("w1", 1, "target_unavailable")
    rows = [{"type": "user", "session_id": source["native_session_id"],
             "sessionId": source["native_session_id"], "cwd": source["workspace_path"],
             "timestamp": "2026-10-05T12:00:01Z", "message": {"role": "user"}} for _ in range(3)]
    with path.open("a") as stream:
        stream.write("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    clock = iter(range(0, 1000, 16))
    monkeypatch.setattr(handoff_module.time, "monotonic", lambda: next(clock))
    tools = SimpleNamespace(registry=registry, tmux=MagicMock(), _workspace_client=MagicMock(), _ssh_manager=None)
    cursors = []
    for _ in range(3):
        controller = WorkerHandoffController(tools, native_log_root=home)
        with controller._reconcile_scope("w1", 1, allow_paused=paused) as denial:
            assert denial is None
            with pytest.raises(HandoffHold, match="scan_budget"):
                controller._validate_quota(source, 1, proposed, require_same_event=False)
        evidence = json.loads(registry.get_worker_handoff("w1")["evidence_json"])
        assert evidence["quota_verdict"] == proposed
        cursors.append(evidence["quota_progress"]["cursor"])
    assert cursors == sorted(set(cursors))
    controller = WorkerHandoffController(tools, native_log_root=home)
    with controller._reconcile_scope("w1", 1, allow_paused=paused) as denial:
        assert denial is None
        assert controller._validate_quota(source, 1, proposed, require_same_event=False)["state"] == "exhausted"


def test_claimed_scan_progress_rejects_foreign_owner_source_and_file(tmp_path, monkeypatch):
    import ironclaude.worker_quota as quota_reader
    registry = _registry(tmp_path)
    source = registry.get_worker("w1")
    home = tmp_path / "home"
    path = _write_quota(home, "claude", source["native_session_id"], Path(source["workspace_path"]))
    proposed = read_native_quota(path, client="claude", native_session_id=source["native_session_id"],
                                 workspace_path=source["workspace_path"], generation="1")
    registry.claim_worker_handoff(source, generation=1, initial_evidence={"quota_verdict": proposed})
    row = {"type": "user", "session_id": source["native_session_id"],
           "sessionId": source["native_session_id"], "cwd": source["workspace_path"],
           "timestamp": "2026-10-05T12:00:01Z", "message": {"role": "user"}}
    with path.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    monkeypatch.setattr(quota_reader, "MAX_RECORDS", 1)
    progress = read_native_quota(path, client="claude", native_session_id=source["native_session_id"],
                                 workspace_path=source["workspace_path"], generation="1",
                                 cursor=proposed["cursor"], reader_state=proposed["reader_state"],
                                 expected_file_identity=proposed["file_identity"])
    progress.update(state="unknown", reason="native_quota_log_scan_budget_exceeded")
    controller = WorkerHandoffController(SimpleNamespace(registry=registry, tmux=MagicMock(),
        _workspace_client=MagicMock(), _ssh_manager=None), native_log_root=home)
    with controller._reconcile_scope("w1", 1) as denial:
        assert denial is None
        nonce = json.loads(registry.get_worker_handoff("w1")["evidence_json"])["reconcile_owner"]["nonce"]
        current = registry.get_worker("w1")
        with pytest.raises(RuntimeError, match="owner"):
            registry.record_worker_handoff_quota_progress("w1", 1, progress, snapshot=current,
                                                          owner_nonce="foreign", expected_pause_reason=None)
        with pytest.raises(RuntimeError, match="runtime snapshot changed"):
            registry.record_worker_handoff_quota_progress("w1", 1, progress,
                snapshot={**current, "native_session_id": str(uuid.uuid4())}, owner_nonce=nonce,
                expected_pause_reason=None)
        with pytest.raises(RuntimeError, match="foreign"):
            registry.record_worker_handoff_quota_progress("w1", 1,
                {**progress, "file_identity": "other"}, snapshot=current, owner_nonce=nonce,
                expected_pause_reason=None)
        registry.pause_worker_handoff("w1", 1, "operator_hold")
        with pytest.raises(RuntimeError, match="phase changed"):
            registry.record_worker_handoff_quota_progress("w1", 1, progress,
                snapshot=current, owner_nonce=nonce, expected_pause_reason=None)
    assert "quota_progress" not in json.loads(registry.get_worker_handoff("w1")["evidence_json"])
