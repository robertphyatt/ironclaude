import json
import sqlite3
from types import SimpleNamespace
import uuid

import pytest
import psutil

from ironclaude.db import init_db
from ironclaude.worker_registry import WorkerRegistry


def _registry(tmp_path, name="registry.db"):
    return WorkerRegistry(init_db(str(tmp_path / name)))


def _worker(registry, *, machine="local", client="claude"):
    objective_id = registry.create_objective("keep work unfinished")
    task_id = registry.create_task(objective_id, "continue task")
    registry.register_worker(
        "w1", "claude-opus", "ic-w1", machine=machine, repo="/repo",
        task_id=task_id, client=client, model="claude-opus" if client == "claude" else "gpt-6-sol",
        native_session_id=str(uuid.uuid4()),
    )
    registry.set_worker_workspace("w1", {
        "workspace_guid": "guid", "repository_identity": "repo-id",
        "worktree_path": "/worktree", "branch": "branch", "base_commit": "base",
        "integration_target": "main",
    })


def _snapshot(registry):
    return registry.get_worker("w1")


@pytest.mark.parametrize("source_client,target_client", [("claude", "codex"), ("codex", "claude")])
def test_completed_target_runtime_proves_effective_tier_only_for_exact_successor(tmp_path, source_client, target_client):
    registry = _registry(tmp_path)
    _worker(registry, machine=None, client=source_client)
    registry._conn.execute("UPDATE workers SET type='claude-fable' WHERE id='w1'")
    registry._conn.commit()
    source = registry.get_worker("w1")
    assert registry.effective_worker_tier(source) == "fable"
    registry.claim_worker_handoff(source, generation=1)
    target = {"client": target_client, "model": "fallback-opus", "native_session_id": str(uuid.uuid4()),
              "tmux_session": "ic-w1-h1", "machine": None}
    evidence = {"target_tier": "opus", "target_runtime": target, "assignment_finished": True}
    registry._conn.execute("UPDATE worker_handoffs SET phase='completed',completed_at=datetime('now'),evidence_json=? WHERE worker_id='w1'",
                           (json.dumps(evidence),))
    registry._conn.execute("UPDATE workers SET status='running',client=?,model=?,native_session_id=?,tmux_session=?,machine=? WHERE id='w1'",
                           (target["client"], target["model"], target["native_session_id"], target["tmux_session"], target["machine"]))
    registry._conn.commit()
    current = registry.get_worker("w1")
    assert registry.effective_worker_tier(current) == "opus"
    assert registry.effective_worker_tier({**current, "native_session_id": str(uuid.uuid4())}) is None
    registry._conn.execute("UPDATE workers SET native_session_id=? WHERE id='w1'", (str(uuid.uuid4()),))
    registry._conn.commit()
    assert registry.effective_worker_tier(registry.get_worker("w1")) is None
    registry._conn.execute("UPDATE workers SET native_session_id=? WHERE id='w1'", (target["native_session_id"],))
    registry._conn.execute("UPDATE worker_handoffs SET evidence_json=? WHERE worker_id='w1'",
                           (json.dumps({**evidence, "target_tier": "unknown"}),))
    registry._conn.commit()
    assert registry.effective_worker_tier(registry.get_worker("w1")) is None


def test_claim_holds_worker_and_rejects_completion_without_partial_stamp(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    claim = registry.claim_worker_handoff(_snapshot(registry), generation=1)
    assert claim["phase"] == "claimed"
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    with pytest.raises(RuntimeError, match="handoff"):
        registry.update_worker_status("w1", "completed")
    worker = registry.get_worker("w1")
    assert worker["status"] == "handoff_pending"
    assert worker["finished_at"] is None


@pytest.mark.parametrize("source_client,target_client", [("claude", "codex"), ("codex", "claude")])
def test_local_none_machine_claim_and_runtime_replacement_both_directions(tmp_path, source_client, target_client):
    registry = _registry(tmp_path)
    _worker(registry, machine=None, client=source_client)
    source = _snapshot(registry)
    assert registry.claim_worker_handoff(source, generation=1)["phase"] == "claimed"
    for prior, next_phase in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                              ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, next_phase)
    target = registry.replace_worker_runtime(
        "w1", 1, "owner_transferred", client=target_client, model="target-model",
        native_session_id=str(uuid.uuid4()), tmux_session="target", machine=None,
    )
    assert target["machine"] is None
    assert target["client"] == target_client


def test_release_returns_prestop_pause_to_running_with_released_disposition(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    with pytest.raises(RuntimeError, match="pause changed"):
        registry.release_worker_handoff("w1", 1, "other_reason")
    row = registry.release_worker_handoff("w1", 1, "native_quota_evidence_changed")
    assert row["phase"] == "released" and row["completed_at"]
    assert json.loads(row["evidence_json"])["disposition"] == "released"
    worker = registry.get_worker("w1")
    assert worker["status"] == "running"
    assert registry.effective_worker_tier(worker) == "opus"
    registry.update_worker_status("w1", "completed")
    assert registry.get_worker("w1")["status"] == "completed"


@pytest.mark.parametrize("recorded", ["target_id", "stop_intent", "source_stop_proof", "bootstrap_intent", "phase"])
def test_prestop_release_refused_once_target_or_stop_recorded(tmp_path, recorded):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    if recorded == "phase":
        registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    else:
        registry.record_worker_handoff_evidence("w1", 1, "claimed", {recorded: "recorded"})
    registry.pause_worker_handoff("w1", 1, "native_quota_evidence_changed")
    with pytest.raises(RuntimeError, match="release refused"):
        registry.release_worker_handoff("w1", 1, "native_quota_evidence_changed")
    assert registry.get_worker("w1")["status"] == "handoff_pending"
    assert registry.get_worker_handoff("w1")["completed_at"] is None


def test_post_stop_release_after_transfer_adopts_target_runtime(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    target_id = str(uuid.uuid4())
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-6-sol",
                                    native_session_id=target_id, tmux_session="ic-w1-h1", machine=None)
    registry.record_worker_handoff_evidence("w1", 1, "owner_transferred", {
        "target_tier": "sonnet", "assignment_finished": True})
    registry.pause_worker_handoff("w1", 1, "target_runtime_changed")
    row = registry.release_worker_handoff("w1", 1, "target_runtime_changed", after_stop=True,
                                          disposition="released_to_target")
    assert json.loads(row["evidence_json"])["disposition"] == "released_to_target"
    worker = registry.get_worker("w1")
    assert (worker["status"], worker["client"], worker["native_session_id"]) == ("running", "codex", target_id)
    # The adopted target runtime keeps its proven tier, not the source type's tier (opus).
    assert registry.effective_worker_tier(worker) == "sonnet"


def test_post_transfer_release_refuses_an_unexpected_runtime(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt-6-sol",
                                    native_session_id=str(uuid.uuid4()), tmux_session="ic-w1-h1", machine=None)
    registry._conn.execute("UPDATE workers SET model='foreign' WHERE id='w1'")
    registry._conn.commit()
    registry.pause_worker_handoff("w1", 1, "target_runtime_changed")
    with pytest.raises(RuntimeError, match="runtime changed"):
        registry.release_worker_handoff("w1", 1, "target_runtime_changed", after_stop=True,
                                        disposition="released_to_target")
    assert registry.get_worker("w1")["status"] == "handoff_pending"


def test_runtime_replacement_allows_same_client_only_with_new_native_session(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None)
    source = _snapshot(registry)
    registry.claim_worker_handoff(source, generation=1)
    for prior, nxt in (("claimed", "source_stopped"), ("source_stopped", "target_ready"),
                       ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, nxt)
    with pytest.raises(RuntimeError, match="identity invalid"):
        registry.replace_worker_runtime("w1", 1, "owner_transferred", client="claude", model="claude-opus",
                                        native_session_id=source["native_session_id"],
                                        tmux_session="ic-w1-h1", machine=None)
    with pytest.raises(RuntimeError, match="identity invalid"):
        registry.replace_worker_runtime("w1", 1, "owner_transferred", client="claude", model="claude-opus",
                                        native_session_id=str(uuid.uuid4()),
                                        tmux_session="ic-w1-h1", machine="other-host")
    successor = str(uuid.uuid4())
    worker = registry.replace_worker_runtime("w1", 1, "owner_transferred", client="claude",
                                             model="claude-opus", native_session_id=successor,
                                             tmux_session="ic-w1-h1", machine=None)
    assert (worker["client"], worker["native_session_id"]) == ("claude", successor)


def test_claim_rejects_unknown_concrete_client(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry, machine=None, client="unknown")
    with pytest.raises(RuntimeError, match="runtime or workspace incomplete"):
        registry.claim_worker_handoff(_snapshot(registry), generation=1)


def test_claim_rejects_stale_snapshot_and_active_operation(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    stale = _snapshot(registry)
    registry.set_worker_provider("w1", "claude", "new-model")
    with pytest.raises(RuntimeError, match="snapshot"):
        registry.claim_worker_handoff(stale, generation=1)
    with registry.worker_operation(_snapshot(registry), operation="finalize"):
        with pytest.raises(RuntimeError, match="operation"):
            registry.claim_worker_handoff(_snapshot(registry), generation=1)


def test_operation_lease_survives_restart_and_requires_exact_runtime(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    snapshot = _snapshot(first)
    lease = first.acquire_worker_operation(snapshot, operation="send")
    second = _registry(tmp_path)
    with pytest.raises(RuntimeError, match="operation"):
        second.acquire_worker_operation(_snapshot(second), operation="kill")
    with pytest.raises(RuntimeError, match="operation"):
        second.claim_worker_handoff(_snapshot(second), generation=1)
    with pytest.raises(RuntimeError, match="lease missing"):
        second.release_worker_operation("w1", lease["nonce"], {**_snapshot(second), "model": "changed"})
    assert second.claim_worker_operation_recovery_warning(_snapshot(second)) is None


def test_dead_owner_claimed_lease_recovers_only_exact_unchanged_source(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    prior = first.acquire_worker_operation(source, operation="send")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    assert second.claim_worker_operation_recovery_warning(_snapshot(second)) is None
    with pytest.raises(RuntimeError, match="snapshot"):
        second.acquire_worker_operation({**_snapshot(second), "model": "foreign"}, operation="send")
    replacement = second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    assert replacement["nonce"] != prior["nonce"]
    assert second.claim_worker_operation_recovery_warning(_snapshot(second)) is None
    with pytest.raises(RuntimeError, match="missing"):
        first.release_worker_operation("w1", prior["nonce"], source)
    second.mark_worker_operation_effects_started(replacement, _snapshot(second))
    second.release_worker_operation("w1", replacement["nonce"], _snapshot(second))


def test_handoff_claim_recovers_dead_owner_claimed_operation_atomically(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    old = first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    first._conn.commit()
    restarted = _registry(tmp_path)
    claimed = restarted.claim_worker_handoff(_snapshot(restarted), generation=1)
    assert claimed["phase"] == "claimed"
    assert restarted.get_worker("w1")["status"] == "handoff_pending"
    assert restarted._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None
    with pytest.raises(RuntimeError, match="missing"):
        first.release_worker_operation("w1", old["nonce"], source)


@pytest.mark.parametrize("phase,changed", [
    ("effects_started", False), ("claimed", True), ("effects_started", True),
])
def test_handoff_claim_reaps_dead_owner_operation_in_any_phase(tmp_path, phase, changed):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    if phase == "effects_started":
        first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    if changed:
        first._conn.execute("UPDATE workers SET model='changed' WHERE id='w1'")
    first._conn.commit()
    restarted = _registry(tmp_path)
    assert restarted.claim_worker_handoff(_snapshot(restarted), generation=1)["phase"] == "claimed"
    assert restarted._conn.execute(
        "SELECT 1 FROM worker_operations WHERE worker_id='w1'").fetchone() is None
    events = restarted._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped'").fetchall()
    assert [json.loads(row["details"])["phase"] for row in events] == [phase]
    assert json.loads(events[0]["details"])["nonce"] == lease["nonce"]


@pytest.mark.parametrize("phase,owner,changed", [
    ("claimed", "live", False),
    ("claimed", "unproved", False),
    ("effects_started", "live", False),
    ("effects_started", "unproved", False),
])
def test_handoff_claim_refuses_uncertain_or_foreign_operation(tmp_path, phase, owner, changed):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    if phase == "effects_started":
        first.mark_worker_operation_effects_started(lease, source)
    if owner == "dead":
        first._conn.execute(
            "UPDATE worker_operations SET owner_pid=2147483647,owner_started=0 WHERE worker_id='w1'")
    elif owner == "unproved":
        first._conn.execute(
            "UPDATE worker_operations SET owner_pid=NULL,owner_started=NULL WHERE worker_id='w1'")
    if changed:
        first._conn.execute("UPDATE workers SET model='changed' WHERE id='w1'")
    first._conn.commit()
    restarted = _registry(tmp_path)
    with pytest.raises(RuntimeError, match="unreconciled"):
        restarted.claim_worker_handoff(_snapshot(restarted), generation=1)
    assert restarted.get_worker("w1")["status"] == "running"
    assert restarted.get_worker_handoff("w1") is None
    assert restarted._conn.execute(
        "SELECT nonce FROM worker_operations WHERE worker_id='w1'").fetchone()["nonce"] == lease["nonce"]


def test_dead_owner_started_lease_is_reaped_with_event_and_no_warning(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    assert second.claim_worker_operation_recovery_warning(_snapshot(second)) is None
    replacement = second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    assert replacement["nonce"] != lease["nonce"]
    details = second._conn.execute(
        "SELECT details FROM events WHERE event_type='worker_operation_reaped' AND worker_id='w1'"
    ).fetchone()["details"]
    assert json.loads(details) == {"nonce": lease["nonce"], "operation": "worker_mutation",
                                   "phase": "effects_started", "owner_pid": 2147483647,
                                   "owner_started": 0}
    with pytest.raises(RuntimeError, match="missing"):
        first.release_worker_operation("w1", lease["nonce"], source)


def test_same_pid_with_different_start_time_is_dead_and_reaped(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute("UPDATE worker_operations SET owner_started=1.0 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    replacement = second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    assert replacement["nonce"] != lease["nonce"]


def test_reap_dead_worker_operations_keeps_live_and_unproved_leases(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    for worker_id in ("w2", "w3"):
        registry.register_worker(worker_id, "claude-opus", f"ic-{worker_id}", client="claude",
                                 model="claude-opus", native_session_id=str(uuid.uuid4()))
    for worker_id in ("w1", "w2", "w3"):
        snapshot = registry.get_worker(worker_id)
        lease = registry.acquire_worker_operation(snapshot, operation="worker_mutation")
        registry.mark_worker_operation_effects_started(lease, snapshot)
    registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    registry._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w3'")
    registry._conn.commit()
    assert _registry(tmp_path).reap_dead_worker_operations() == 1
    remaining = [row["worker_id"] for row in registry._conn.execute(
        "SELECT worker_id FROM worker_operations ORDER BY worker_id").fetchall()]
    assert remaining == ["w2", "w3"]


def test_unproved_owner_identity_warns_but_never_recovers_claim(tmp_path, monkeypatch):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=1 WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    import ironclaude.worker_registry as registry_module
    original_process = registry_module.psutil.Process

    def inaccessible_owner(pid):
        if pid == 2147483647:
            raise psutil.AccessDenied(pid)
        return original_process(pid)

    monkeypatch.setattr(registry_module.psutil, "Process", inaccessible_owner)
    with pytest.raises(RuntimeError, match="unreconciled"):
        second.acquire_worker_operation(_snapshot(second), operation="worker_mutation")
    blocker = second.claim_worker_operation_recovery_warning(_snapshot(second))
    assert blocker["nonce"] == lease["nonce"]
    assert blocker["disposition"] == "owner_identity_unproved"
    assert second.claim_worker_operation_recovery_warning(_snapshot(second)) is None
    assert second._conn.execute(
        "SELECT phase FROM worker_operations WHERE worker_id='w1'").fetchone()["phase"] == "claimed"


def test_foreign_snapshot_cannot_claim_operation_recovery_warning(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first.mark_worker_operation_effects_started(lease, source)
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    with pytest.raises(RuntimeError, match="snapshot"):
        second.claim_worker_operation_recovery_warning({**_snapshot(second), "model": "foreign"})
    assert second.claim_worker_operation_recovery_warning(_snapshot(second))["nonce"] == lease["nonce"]


def test_changed_current_runtime_unproved_owner_warns_old_lease_scope_but_cannot_recover(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    lease = first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w1'")
    first._conn.execute("UPDATE workers SET model='successor-model' WHERE id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    current = _snapshot(second)
    with pytest.raises(RuntimeError, match="unreconciled"):
        second.acquire_worker_operation(current, operation="worker_mutation")
    blocker = second.claim_worker_operation_recovery_warning(current)
    assert blocker["nonce"] == lease["nonce"]
    assert blocker["snapshot"]["model"] == source["model"]
    assert blocker["disposition"] == "runtime_scope_changed"
    assert second.claim_worker_operation_recovery_warning(current) is None


def test_dead_owner_lease_with_changed_runtime_is_reaped(tmp_path):
    first = _registry(tmp_path)
    _worker(first)
    source = _snapshot(first)
    first.acquire_worker_operation(source, operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=2147483647, owner_started=0 WHERE worker_id='w1'")
    first._conn.execute("UPDATE workers SET model='successor-model' WHERE id='w1'")
    first._conn.commit()
    second = _registry(tmp_path)
    current = _snapshot(second)
    assert second.claim_worker_operation_recovery_warning(current) is None
    assert second.acquire_worker_operation(current, operation="worker_mutation")["worker_id"] == "w1"


def _brain_wait_clock(monkeypatch, on_sleep=None):
    import ironclaude.worker_registry as registry_module
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds
        if on_sleep is not None:
            on_sleep(len(sleeps))

    monkeypatch.setattr(registry_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(registry_module.time, "monotonic", lambda: clock["now"])
    return clock, sleeps


def test_brain_wrapper_waits_for_live_daemon_lease_then_proceeds(tmp_path, monkeypatch):
    from ironclaude.orchestrator_mcp import _hold_worker_operation
    daemon_registry = _registry(tmp_path)
    _worker(daemon_registry)
    lease = daemon_registry.acquire_worker_operation(_snapshot(daemon_registry), operation="worker_mutation")
    brain_registry = _registry(tmp_path)

    def daemon_finishes(count):
        if count == 3:
            daemon_registry.release_worker_operation("w1", lease["nonce"], _snapshot(daemon_registry))

    clock, sleeps = _brain_wait_clock(monkeypatch, daemon_finishes)
    calls = []
    guarded = _hold_worker_operation(lambda self, worker_id: calls.append(worker_id) or "done")
    assert guarded(SimpleNamespace(registry=brain_registry), "w1") == "done"
    assert calls == ["w1"]
    assert sleeps == [0.2, 0.2, 0.2]


def test_brain_wrapper_reports_busy_after_five_seconds(tmp_path, monkeypatch):
    from ironclaude.orchestrator_mcp import _hold_worker_operation
    daemon_registry = _registry(tmp_path)
    _worker(daemon_registry)
    daemon_registry.acquire_worker_operation(_snapshot(daemon_registry), operation="worker_mutation")
    brain_registry = _registry(tmp_path)
    clock, sleeps = _brain_wait_clock(monkeypatch)
    calls = []
    guarded = _hold_worker_operation(lambda self, worker_id: calls.append(worker_id))
    with pytest.raises(RuntimeError, match=r"worker w1 is busy \(daemon cycle in progress\); retry shortly"):
        guarded(SimpleNamespace(registry=brain_registry), "w1")
    assert calls == []
    assert set(sleeps) == {0.2}
    assert 5.0 - 1e-6 <= clock["now"] <= 5.2 + 1e-6


def test_brain_wrapper_does_not_wait_on_unproved_owner(tmp_path, monkeypatch):
    from ironclaude.orchestrator_mcp import _hold_worker_operation
    first = _registry(tmp_path)
    _worker(first)
    first.acquire_worker_operation(_snapshot(first), operation="worker_mutation")
    first._conn.execute(
        "UPDATE worker_operations SET owner_pid=NULL, owner_started=NULL WHERE worker_id='w1'")
    first._conn.commit()
    clock, sleeps = _brain_wait_clock(monkeypatch)
    guarded = _hold_worker_operation(lambda self, worker_id: "done")
    with pytest.raises(RuntimeError, match="unreconciled"):
        guarded(SimpleNamespace(registry=_registry(tmp_path)), "w1")
    assert sleeps == []


def test_legacy_operation_lease_migrates_as_effects_started(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE worker_operations (worker_id TEXT PRIMARY KEY, nonce TEXT UNIQUE NOT NULL, "
                 "operation TEXT NOT NULL, snapshot_json TEXT NOT NULL, owner_token TEXT NOT NULL, "
                 "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.commit(); conn.close()
    registry = WorkerRegistry(init_db(str(path)))
    _worker(registry)
    source = _snapshot(registry)
    registry._conn.execute(
        "INSERT INTO worker_operations(worker_id,nonce,operation,snapshot_json,owner_token) "
        "VALUES (?,?,?,?,?)",
        ("w1", "legacy-nonce", "worker_mutation", json.dumps(registry._snapshot(source)), "old-owner"),
    )
    registry._conn.commit()
    row = registry._conn.execute("SELECT phase,owner_pid FROM worker_operations WHERE worker_id='w1'").fetchone()
    assert row["phase"] == "effects_started" and row["owner_pid"] is None
    restarted = _registry(tmp_path, "legacy.db")
    with pytest.raises(RuntimeError, match="unreconciled"):
        restarted.acquire_worker_operation(_snapshot(restarted), operation="worker_mutation")
    with pytest.raises(RuntimeError, match="unreconciled"):
        restarted.claim_worker_handoff(_snapshot(restarted), generation=1)
    assert restarted.claim_worker_operation_recovery_warning(_snapshot(restarted))["nonce"] == "legacy-nonce"
    assert restarted.claim_worker_operation_recovery_warning(_snapshot(restarted)) is None


def test_claim_locks_other_connection_before_snapshot_check(tmp_path, monkeypatch):
    first = _registry(tmp_path)
    _worker(first)
    second = _registry(tmp_path)
    second._conn.execute("PRAGMA busy_timeout=0")
    original = first._exact_worker

    def competing_write(snapshot):
        worker = original(snapshot)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second._conn.execute("UPDATE workers SET model='raced' WHERE id='w1'")
        return worker

    monkeypatch.setattr(first, "_exact_worker", competing_write)
    assert first.claim_worker_handoff(_snapshot(first), generation=1)["phase"] == "claimed"
    assert first.get_worker("w1")["model"] == "claude-opus"


def test_status_check_locks_competing_claim_before_terminal_write(tmp_path, monkeypatch):
    first = _registry(tmp_path)
    _worker(first)
    second = _registry(tmp_path)
    second._conn.execute("PRAGMA busy_timeout=0")
    source = _snapshot(second)
    original = first._active_handoff

    def competing_claim(worker_id):
        held = original(worker_id)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.claim_worker_handoff(source, generation=1)
        return held

    monkeypatch.setattr(first, "_active_handoff", competing_claim)
    first.update_worker_status("w1", "completed")
    assert first.get_worker("w1")["status"] == "completed"
    assert first.get_worker_handoff("w1") is None


def test_held_worker_blocks_every_status_and_job_completion(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    source = _snapshot(registry)
    registry.claim_worker_handoff(source, generation=1)
    registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    registry.pause_worker_handoff("w1", 1, "unverified process")
    handoff = registry.get_worker_handoff("w1")
    assert handoff["phase"] == "source_stopped"
    assert handoff["pause_reason"] == "unverified process"
    for status in ("running", "completed", "failed", "killed"):
        with pytest.raises(RuntimeError, match="handoff"):
            registry.update_worker_status("w1", status)
    with pytest.raises(RuntimeError, match="handoff"):
        registry.update_task_status(source["task_id"], "completed")
    with pytest.raises(RuntimeError, match="handoff"):
        registry.complete_objective(source["objective"]["id"])
    assert registry.get_worker("w1")["finished_at"] is None
    assert registry.get_worker("w1")["task"]["completed_at"] is None


def test_replacement_requires_full_phase_and_preserves_history(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    source = _snapshot(registry)
    registry.claim_worker_handoff(source, generation=1)
    registry.advance_worker_handoff("w1", 1, "claimed", "source_stopped")
    with pytest.raises(ValueError, match="owner_transferred"):
        registry.replace_worker_runtime("w1", 1, "source_stopped", client="codex", model="gpt", native_session_id=str(uuid.uuid4()), tmux_session="target", machine="local")
    for prior, next_phase in (("source_stopped", "target_ready"), ("target_ready", "pm_prepared"), ("pm_prepared", "owner_transferred")):
        registry.advance_worker_handoff("w1", 1, prior, next_phase)
    with pytest.raises(RuntimeError, match="snapshot"):
        registry.record_native_quota_verdict("w1", 1, {"cursor": 1, "generation": 1,
            "client": source["client"], "native_session_id": source["native_session_id"]},
            snapshot={**source, "model": "stale"})
    target_id = str(uuid.uuid4())
    target = registry.replace_worker_runtime("w1", 1, "owner_transferred", client="codex", model="gpt", native_session_id=target_id, tmux_session="target", machine="local")
    assert target["task"] == source["task"]
    assert target["objective"] == source["objective"]
    registry.advance_worker_handoff("w1", 1, "owner_transferred", "runtime_registered")
    registry.advance_worker_handoff("w1", 1, "runtime_registered", "continuation_dispatched")
    registry.record_worker_handoff_evidence("w1", 1, "continuation_dispatched", {"assignment_finished": True})
    registry.complete_worker_handoff("w1", 1, "continuation_dispatched")
    assert registry.get_worker("w1")["status"] == "running"
    registry.claim_worker_handoff(registry.get_worker("w1"), generation=2)
    assert len(registry._conn.execute("SELECT * FROM worker_handoffs WHERE worker_id='w1'").fetchall()) == 2


def test_quota_evidence_cannot_cross_native_identity_or_regress_cursor(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    source = _snapshot(registry)
    verdict = {"native_session_id": source["native_session_id"], "client": source["client"],
               "generation": "1", "cursor": 20, "event_key": "event-20", "state": "exhausted"}
    registry.record_native_quota_verdict("w1", 1, verdict, snapshot=source)
    with pytest.raises(ValueError, match="identity"):
        registry.record_native_quota_verdict("w1", 1, {**verdict, "native_session_id": str(uuid.uuid4())}, snapshot=source)
    with pytest.raises(RuntimeError, match="stale"):
        registry.record_native_quota_verdict("w1", 1, {**verdict, "cursor": 10}, snapshot=source)
    registry.claim_worker_handoff(source, generation=1)
    with pytest.raises(RuntimeError, match="snapshot"):
        registry.record_native_quota_verdict("w1", 1, verdict, snapshot=source)


def test_runtime_replacement_preserves_assignment_and_rejects_old_generation(tmp_path):
    registry = _registry(tmp_path)
    _worker(registry)
    claim = registry.claim_worker_handoff(_snapshot(registry), generation=1)
    for prior, next_phase in zip(registry._PHASES, registry._PHASES[1:]):
        if next_phase == "runtime_registered":
            break
        registry.advance_worker_handoff("w1", 1, prior, next_phase)
    worker = registry.replace_worker_runtime(
        "w1", 1, "owner_transferred", client="codex", model="gpt-6-sol",
        native_session_id=str(uuid.uuid4()), tmux_session="ic-w1-g1", machine="local",
    )
    assert worker["workspace_guid"] == "guid"
    assert worker["type"] == "claude-opus"
    assert worker["status"] == "handoff_pending"
    with pytest.raises(RuntimeError, match="generation"):
        registry.replace_worker_runtime("w1", 2, "owner_transferred", client="codex", model="x", native_session_id=str(uuid.uuid4()), tmux_session="x", machine="local")
