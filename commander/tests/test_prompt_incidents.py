import sqlite3

import pytest

from ironclaude.db import init_db
from ironclaude.prompt_incidents import PromptIncidentStore
from ironclaude.tmux_manager import PromptSignal


def make_store(tmp_path, name="prompt.db"):
    return PromptIncidentStore(init_db(str(tmp_path / name)))


def semantic(
    question="Choose A?", *, kind="question", options=(), authority_text="", evidence=None
):
    rendered = evidence if evidence is not None else question
    return PromptSignal(
        kind=kind,
        question=question,
        options=tuple(options),
        authority_text=authority_text,
        source_spans=(("question", 0, len(question)),),
        evidence=rendered,
    )


def deliver(store, observation, *, delivered=True, failure_category=None, now=1.0):
    assert observation.dispatch_id is not None
    assert store.claim_dispatch(
        observation.dispatch_id, destination="brain", now=now
    ) is True
    store.record_delivery(
        observation.dispatch_id,
        delivered=delivered,
        failure_category=failure_category,
    )


def test_twenty_three_identical_observations_create_one_dispatch(tmp_path):
    store = make_store(tmp_path)
    observations = [
        store.observe("worker-1", "plan_ready", semantic("Which path?", options=(("1", "Inline"),)), now=float(index))
        for index in range(23)
    ]
    assert observations[0].action == "dispatch"
    assert all(item.action == "hold" for item in observations[1:])
    assert len({item.incident_id for item in observations}) == 1
    assert sum(item.dispatch_id is not None for item in observations) == 1


def test_prompt_incident_survives_reopen(tmp_path):
    db_path = str(tmp_path / "commander.db")
    first_conn = init_db(db_path)
    first = PromptIncidentStore(first_conn)
    claim = first.observe("worker-1", "plan_ready", semantic("Which path?", options=(("1", "Inline"),)), now=10.0)
    deliver(first, claim, now=10.0)
    first_conn.close()

    second = PromptIncidentStore(init_db(db_path))
    held = second.observe("worker-1", "executing", semantic("Which path?", options=(("1", "Inline"),), evidence="Which path?\nreviewer 45s · 50.5k tokens\n/goal active (3h)"), now=20.0)
    assert held.action == "hold"
    assert held.incident_id == claim.incident_id
    assert held.dispatch_id is None


def test_a_b_a_creates_three_episodes_and_dispatches(tmp_path):
    store = make_store(tmp_path)
    a1 = store.observe("worker-1", "plan_ready", semantic("Choose?", options=(("1", "Option A"),)), now=1.0)
    b = store.observe("worker-1", "plan_ready", semantic("Choose?", options=(("1", "Option B"),)), now=2.0)
    a2 = store.observe("worker-1", "plan_ready", semantic("Choose?", options=(("1", "Option A"),)), now=3.0)
    assert [a1.action, b.action, a2.action] == ["dispatch"] * 3
    assert len({a1.incident_id, b.incident_id, a2.incident_id}) == 3
    assert a1.fingerprint == a2.fingerprint


def test_resolved_a_can_recur_after_reopen(tmp_path):
    db_path = str(tmp_path / "recur.db")
    conn = init_db(db_path)
    first = PromptIncidentStore(conn)
    a1 = first.observe("worker-1", "executing", semantic("Approve A?", kind="approval", authority_text="Approval required"), now=1.0)
    first.resolve_worker("worker-1", now=2.0)
    conn.close()

    second = PromptIncidentStore(init_db(db_path))
    a2 = second.observe("worker-1", "executing", semantic("Approve A?", kind="approval", authority_text="Approval required"), now=3.0)
    assert a2.action == "dispatch"
    assert a2.incident_id != a1.incident_id
    assert a2.fingerprint == a1.fingerprint


def test_stage_and_evidence_are_not_identity_but_semantic_fields_are(tmp_path):
    store = make_store(tmp_path)
    base = store.observe("worker-1", "plan_ready", semantic("Choose?", options=(("1", "Option A"),), evidence="reviewer 26s · 40.3k tokens"), now=1.0)
    stage = store.observe("worker-1", "executing", semantic("Choose?", options=(("1", "Option A"),), evidence="reviewer 45s · 50.5k tokens"), now=2.0)
    worker = store.observe("worker-2", "executing", semantic("Choose?", options=(("1", "Option A"),)), now=3.0)
    option = store.observe("worker-2", "executing", semantic("Choose?", options=(("1", "Option Z"),)), now=4.0)
    authority = store.observe("worker-2", "executing", semantic("Choose?", options=(("1", "Option Z"),), authority_text="Human approval required"), now=5.0)
    assert stage.action == "hold"
    assert stage.fingerprint == base.fingerprint
    assert len({base.fingerprint, worker.fingerprint, option.fingerprint, authority.fingerprint}) == 4


def test_production_telemetry_changes_do_not_change_fingerprint(tmp_path):
    store = make_store(tmp_path)
    first = store.observe(
        "worker-1", "reviewing", semantic("Continue review?", evidence="reviewer 26s · 40.3k tokens\n/goal active (2h)"), now=1.0
    )
    second = store.observe(
        "worker-1", "executing", semantic("Continue review?", evidence="reviewer 45s · 50.5k tokens\n/goal active (3h)"), now=2.0
    )
    assert second.action == "hold"
    assert second.fingerprint == first.fingerprint


def test_claim_is_at_most_once(tmp_path):
    store = make_store(tmp_path)
    observation = store.observe("worker-1", "plan_ready", semantic(), now=1.0)
    assert store.claim_dispatch(observation.dispatch_id, destination="brain", now=2.0)
    assert not store.claim_dispatch(observation.dispatch_id, destination="brain", now=3.0)


def test_capability_recovery_rearms_only_held_capability_dispatch(tmp_path):
    store = make_store(tmp_path)
    held = store.observe("held", "plan_ready", semantic("Choose A?"), now=1.0)
    deliver(
        store,
        held,
        delivered=False,
        failure_category="capability_blocked",
        now=2.0,
    )
    delivered = store.observe("delivered", "plan_ready", semantic("Choose B?"), now=3.0)
    deliver(store, delivered, now=4.0)
    unrelated = store.observe("unrelated", "plan_ready", semantic("Choose C?"), now=5.0)
    deliver(store, unrelated, delivered=False, failure_category="tool_failure", now=6.0)

    recovery = store.rearm_capability_recovery("capability-fp-1", now=7.0)
    assert [item.incident_id for item in recovery] == [held.incident_id]
    assert recovery[0].dispatch_id is not None
    assert store.rearm_capability_recovery("capability-fp-1", now=8.0) == []


def test_operator_guidance_rearms_once_per_authenticated_source_ts(tmp_path):
    store = make_store(tmp_path)
    initial = store.observe("worker-1", "plan_ready", semantic(), now=1.0)
    deliver(store, initial, now=2.0)
    first = store.rearm_from_operator_guidance(
        "worker-1", "1700000000.000100", now=3.0
    )
    pending_replay = store.rearm_from_operator_guidance(
        "worker-1", "1700000000.000100", now=4.0
    )
    assert pending_replay is not None
    assert pending_replay.dispatch_id == first.dispatch_id
    deliver(store, pending_replay, now=4.5)
    replay = store.rearm_from_operator_guidance(
        "worker-1", "1700000000.000100", now=4.75
    )
    second = store.rearm_from_operator_guidance(
        "worker-1", "1700000001.000200", now=5.0
    )
    assert first is not None and first.dispatch_id is not None
    assert replay is None
    assert second is not None and second.dispatch_id != first.dispatch_id
    assert store.rearm_from_operator_guidance(
        "missing-worker", "1700000002.000300", now=6.0
    ) is None


def test_active_incident_exposes_pending_initial_dispatch_for_recovery(tmp_path):
    store = make_store(tmp_path)
    initial = store.observe("worker-1", "plan_ready", semantic(), now=1.0)

    active = store.active_for_worker("worker-1")

    assert active["dispatch_id"] == initial.dispatch_id
    assert active["dispatch_state"] == "pending"
    assert active["dispatch_reason"] == "initial"


@pytest.mark.parametrize("source_ts", ["", "not-a-ts", "1.2.3", "1700000000"])
def test_operator_guidance_rejects_non_slack_identity(tmp_path, source_ts):
    store = make_store(tmp_path)
    store.observe("worker-1", "plan_ready", semantic(), now=1.0)
    with pytest.raises(ValueError, match="Slack timestamp"):
        store.rearm_from_operator_guidance("worker-1", source_ts, now=2.0)


def test_resolved_episode_cannot_rearm(tmp_path):
    store = make_store(tmp_path)
    store.observe("worker-1", "plan_ready", semantic(), now=1.0)
    store.resolve_worker("worker-1", now=2.0)
    assert store.rearm_from_operator_guidance(
        "worker-1", "1700000000.000100", now=3.0
    ) is None
    assert store.rearm_capability_recovery("fp", now=4.0) == []


def test_failed_persistence_does_not_return_dispatch_claim(tmp_path):
    conn = init_db(str(tmp_path / "failure.db"))
    conn.execute(
        "CREATE TRIGGER reject_prompt_dispatch BEFORE INSERT ON worker_prompt_dispatches "
        "BEGIN SELECT RAISE(ABORT, 'forced dispatch failure'); END"
    )
    conn.commit()
    store = PromptIncidentStore(conn)
    with pytest.raises(sqlite3.IntegrityError, match="forced dispatch failure"):
        store.observe("worker-1", "plan_ready", semantic(), now=1.0)
    assert conn.execute("SELECT COUNT(*) FROM worker_prompt_incidents").fetchone()[0] == 0
