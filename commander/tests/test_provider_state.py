import sqlite3

import pytest

from ironclaude.db import init_db
from ironclaude.provider_state import ProviderState, WORKER_USAGE_ACCOUNT_TIER


def test_native_usage_quarantine_expires_at_reset_time_and_requarantines(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account = ("local", "claude", "worker", WORKER_USAGE_ACCOUNT_TIER)
    worker = ("local", "claude", "worker", "opus")
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:1",
                           usage_cursor=10, usage_event_key="e1",
                           usage_event_timestamp="2026-10-06T12:00:00Z", usage_expires_at=1_000.0)
    assert not state.is_available(*worker, now=999.0)
    assert state.is_available(*worker, now=1_000.0)
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:2",
                           usage_cursor=20, usage_event_key="e2",
                           usage_event_timestamp="2026-10-06T13:00:00Z", usage_expires_at=5_000.0)
    assert not state.is_available(*worker, now=1_001.0)
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:0",
                           usage_cursor=5, usage_event_key="e0",
                           usage_event_timestamp="2026-10-06T11:00:00Z", usage_expires_at=99_999.0)
    assert state.is_available(*worker, now=5_000.0)


def test_usage_quarantine_without_reset_uses_default(tmp_path, monkeypatch):
    import ironclaude.provider_state as provider_state_module
    monkeypatch.setattr(provider_state_module.time, "time", lambda: 10_000.0)
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account = ("local", "codex", "worker", WORKER_USAGE_ACCOUNT_TIER)
    state.mark_unavailable(*account, "usage", "native_usage_exhausted", usage_generation="g:f:1",
                           usage_cursor=10, usage_event_key="e1",
                           usage_event_timestamp="2026-10-06T12:00:00Z")
    assert not state.is_available("local", "codex", "worker", "opus", now=10_000.0 + 17_999)
    assert state.is_available("local", "codex", "worker", "opus", now=10_000.0 + 18_000)


def test_legacy_usage_row_without_expiry_expires_after_default(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    state._conn.execute(
        "INSERT INTO provider_capability_state (host, client, role, tier, available, category, reason, updated_at) "
        "VALUES ('local', 'claude', 'worker', ?, 0, 'usage', 'legacy quota', '2000-01-01 00:00:00')",
        (WORKER_USAGE_ACCOUNT_TIER,))
    state._conn.commit()
    assert state.is_available("local", "claude", "worker", "opus")
    state._conn.execute("UPDATE provider_capability_state SET updated_at=datetime('now')")
    state._conn.commit()
    assert not state.is_available("local", "claude", "worker", "opus")


def test_expiry_never_reopens_non_usage_unavailability(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    state.mark_unavailable("local", "claude", "worker", "opus", "auth", "probe failed")
    assert not state.is_available("local", "claude", "worker", "opus", now=10**12)


def test_usage_quarantine_survives_failed_probe_and_auth_success(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    state.mark_unavailable("local", "claude", "worker", "opus", "usage", "native quota")
    state.record_capability("local", "claude", "worker", "opus", configured=True, supported=True, installed=True, authenticated=False, available=False, reason="probe_timeout")
    state.record_capability("local", "claude", "worker", "opus", configured=True, supported=True, installed=True, authenticated=True, available=True, reason=None)
    assert state.unavailable_reason("local", "claude", "worker", "opus") == {"category": "usage", "reason": "native quota"}


def test_usage_quarantine_requires_newer_native_recovery_or_operator(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    key = ("local", "claude", "worker", "opus")
    state.mark_unavailable(*key, "usage", "native quota", usage_generation="session-1", usage_cursor=10, usage_event_key="event-10")
    state.mark_unavailable(*key, "auth", "probe failed")
    state.mark_available(*key)
    assert state.unavailable_reason(*key) == {"category": "usage", "reason": "native quota"}
    assert not state.recover_usage_quarantine(*key, native_generation="session-1", native_cursor=10, native_event_key="event-10", native_state="available")
    assert not state.recover_usage_quarantine(*key, native_generation="session-2", native_cursor=11, native_event_key="event-11", native_state="available")
    assert state.recover_usage_quarantine(*key, native_generation="session-1", native_cursor=11, native_event_key="event-11", native_state="available")
    assert state.is_available(*key)


def test_cross_generation_recovery_persists_watermark_and_rejects_late_quota(tmp_path):
    db_path = str(tmp_path / "state.db")
    key = ("local", "claude", "worker", "opus")
    state = ProviderState(init_db(db_path))
    state.mark_unavailable(
        *key, "usage", "quota at T2", usage_generation="native-old",
        usage_cursor=10, usage_event_key="quota-T2",
        usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    state._conn.close()

    state = ProviderState(init_db(db_path))
    assert state.recover_usage_quarantine(
        *key, native_generation="native-fresh", native_cursor=1,
        native_event_key="success-T3", native_state="available",
        native_event_timestamp="2026-10-06T12:00:03+00:00",
    )
    state.mark_unavailable(
        *key, "usage", "late quota at T1", usage_generation="native-late",
        usage_cursor=1, usage_event_key="quota-T1",
        usage_event_timestamp="2026-10-06T12:00:01Z",
    )
    assert state.unavailable_reason(*key) is None
    state.mark_unavailable(
        *key, "usage", "quota at T4", usage_generation="native-new",
        usage_cursor=1, usage_event_key="quota-T4",
        usage_event_timestamp="2026-10-06T12:00:04Z",
    )
    assert state.unavailable_reason(*key) == {"category": "usage", "reason": "quota at T4"}


def test_healthy_native_success_persists_watermark_before_any_quarantine(tmp_path):
    db_path = str(tmp_path / "state.db")
    first = ProviderState(init_db(db_path))
    key = ("local", "claude", "worker", first.worker_usage_scope_tier("worker", "opus"))
    assert not first.recover_usage_quarantine(
        *key, native_generation="native-success", native_cursor=1,
        native_event_key="success-T3", native_state="available",
        native_event_timestamp="2026-10-06T12:00:03Z",
    )
    first._conn.close()

    second = ProviderState(init_db(db_path))
    second.mark_unavailable(
        *key, "usage", "late quota at T2", usage_generation="native-late",
        usage_cursor=1, usage_event_key="quota-T2",
        usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    assert second.unavailable_reason(*key) is None
    assert second.is_available("local", "claude", "worker", "fable")
    second.mark_unavailable(
        *key, "usage", "fresh quota at T4", usage_generation="native-new",
        usage_cursor=1, usage_event_key="quota-T4",
        usage_event_timestamp="2026-10-06T12:00:04Z",
    )
    assert second.unavailable_reason(*key) == {"category": "usage", "reason": "fresh quota at T4"}
    assert not second.is_available("local", "claude", "worker", "fable")


def test_native_success_watermark_preserves_nonusage_unavailability(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    key = ("local", "claude", "worker", "opus")
    state.mark_unavailable(*key, "auth", "login required")

    assert not state.recover_usage_quarantine(
        *key, native_generation="native-success", native_cursor=1,
        native_event_key="success-T3", native_state="available",
        native_event_timestamp="2026-10-06T12:00:03Z",
    )

    assert state.unavailable_reason(*key) == {"category": "auth", "reason": "login required"}
    assert state._conn.execute(
        "SELECT usage_event_timestamp FROM provider_capability_state "
        "WHERE host=? AND client=? AND role=? AND tier=?", key,
    ).fetchone()[0] == "2026-10-06T12:00:03+00:00"


def test_worker_usage_account_scope_blocks_tier_fallback_and_requires_fresh_native_recovery(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account_tier = state.worker_usage_scope_tier("worker", "fable")
    state.mark_unavailable(
        "host-a", "claude", "worker", account_tier, "usage", "account quota",
        usage_generation="native-old", usage_cursor=10, usage_event_key="quota-T2",
        usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    assert not state.is_available("host-a", "claude", "worker", "fable")
    assert not state.is_available("host-a", "claude", "worker", "opus")
    assert state.is_available("host-a", "codex", "worker", "opus")
    assert state.is_available("host-b", "claude", "worker", "opus")
    assert state.is_available("host-a", "claude", "brain", "opus")
    state.record_capability(
        "host-a", "claude", "worker", "opus", configured=True, supported=True,
        installed=True, authenticated=True, available=True, reason=None,
    )
    assert not state.is_available("host-a", "claude", "worker", "opus")
    recovery = dict(native_generation="native-fresh", native_cursor=1,
                    native_event_key="success-T3", native_state="available")
    assert not state.recover_usage_quarantine(
        "host-a", "claude", "worker", account_tier, **recovery,
        native_event_timestamp="2026-10-06T12:00:02Z",
    )
    assert not state.recover_usage_quarantine(
        "host-a", "claude", "worker", account_tier, **recovery,
    )
    assert state.recover_usage_quarantine(
        "host-a", "claude", "worker", account_tier, **recovery,
        native_event_timestamp="2026-10-06T12:00:03Z",
    )
    assert state.is_available("host-a", "claude", "worker", "fable")
    assert state.is_available("host-a", "claude", "worker", "opus")


def test_worker_usage_account_same_generation_rejects_older_timestamp(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account_tier = state.worker_usage_scope_tier("worker", "opus")
    state.mark_unavailable(
        "local", "claude", "worker", account_tier, "usage", "account quota",
        usage_generation="native-1", usage_cursor=10, usage_event_key="quota-T2",
        usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    assert not state.recover_usage_quarantine(
        "local", "claude", "worker", account_tier, native_generation="native-1",
        native_cursor=11, native_event_key="success-missing", native_state="available",
    )
    assert not state.recover_usage_quarantine(
        "local", "claude", "worker", account_tier, native_generation="native-1",
        native_cursor=11, native_event_key="success-T1", native_state="available",
        native_event_timestamp="2026-10-06T12:00:01Z",
    )
    assert state.recover_usage_quarantine(
        "local", "claude", "worker", account_tier, native_generation="native-1",
        native_cursor=11, native_event_key="success-T2", native_state="available",
        native_event_timestamp="2026-10-06T12:00:02Z",
    )


def test_operator_recovery_can_clear_worker_usage_account_scope(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    account_tier = state.worker_usage_scope_tier("worker", "opus")
    state.mark_unavailable(
        "local", "claude", "worker", account_tier, "usage", "account quota",
        usage_generation="native-1", usage_cursor=1, usage_event_key="quota",
        usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    assert state.recover_usage_quarantine(
        "local", "claude", "worker", account_tier, operator_authorized=True
    )
    assert state.is_available("local", "claude", "worker", "fable")


def test_cross_generation_recovery_requires_strict_timestamp_and_exact_scope(tmp_path):
    state = ProviderState(init_db(str(tmp_path / "state.db")))
    key = ("local", "claude", "worker", "opus")
    state.mark_unavailable(
        *key, "usage", "quota", usage_generation="native-old", usage_cursor=1,
        usage_event_key="quota", usage_event_timestamp="2026-10-06T12:00:02Z",
    )
    common = dict(native_generation="native-new", native_cursor=1,
                  native_event_key="success", native_state="available")
    assert not state.recover_usage_quarantine(
        *key, **common, native_event_timestamp="2026-10-06T12:00:02Z"
    )
    assert not state.recover_usage_quarantine(*key, **common)
    with pytest.raises(ValueError, match="timezone-aware"):
        state.recover_usage_quarantine(*key, **common, native_event_timestamp="not-a-timestamp")
    assert not state.recover_usage_quarantine(
        "other-host", "claude", "worker", "opus", **common,
        native_event_timestamp="2026-10-06T12:00:03Z",
    )
    assert state.unavailable_reason(*key) == {"category": "usage", "reason": "quota"}


def test_init_db_migrates_usage_event_timestamp(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE provider_capability_state (
            host TEXT NOT NULL, client TEXT NOT NULL, role TEXT NOT NULL, tier TEXT NOT NULL,
            PRIMARY KEY (host, client, role, tier))"""
    )
    conn.commit()
    conn.close()

    migrated = init_db(str(db_path))
    columns = {row[1] for row in migrated.execute("PRAGMA table_info(provider_capability_state)")}
    assert "usage_event_timestamp" in columns


def make_state(tmp_path):
    return ProviderState(init_db(str(tmp_path / "commander.db")))


def test_current_client_defaults_without_mutation(tmp_path):
    state = make_state(tmp_path)
    assert state.current_client("brain", "claude") == "claude"
    assert state.get_current_client("brain") is None


def test_current_client_is_sticky_per_role(tmp_path):
    state = make_state(tmp_path)
    state.set_current_client("worker", "codex")
    assert state.current_client("worker", "claude") == "codex"
    assert state.current_client("brain", "claude") == "claude"


def test_repeated_state_updates_are_idempotent(tmp_path):
    state = make_state(tmp_path)
    state.set_current_client("worker", "codex")
    state.set_current_client("worker", "codex")
    role_count = state._conn.execute(
        "SELECT COUNT(*) FROM provider_role_state WHERE role='worker'"
    ).fetchone()[0]
    assert role_count == 1

    state.mark_unavailable("host-a", "codex", "worker", "sonnet", "client_crash", "first")
    state.mark_unavailable("host-a", "codex", "worker", "sonnet", "client_crash", "latest")
    cap_count = state._conn.execute(
        """SELECT COUNT(*) FROM provider_capability_state
           WHERE host='host-a' AND client='codex' AND role='worker' AND tier='sonnet'"""
    ).fetchone()[0]
    assert cap_count == 1
    assert state.unavailable_reason("host-a", "codex", "worker", "sonnet")["reason"] == "latest"


def test_capability_defaults_available(tmp_path):
    state = make_state(tmp_path)
    assert state.is_available("local", "claude", "brain", "sonnet") is True


def test_unavailability_is_scoped_to_host_client_role_and_tier(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "host-a", "claude", "worker", "sonnet", "usage_limit", "resets later"
    )
    assert state.is_available("host-a", "claude", "worker", "sonnet") is False
    assert state.is_available("host-a", "claude", "worker", "opus") is True
    assert state.is_available("host-b", "claude", "worker", "sonnet") is True


def test_mark_available_requires_explicit_recovery(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "local", "codex", "grader", "opus", "expired_auth", "login required"
    )
    state.mark_available("local", "codex", "grader", "opus")
    assert state.is_available("local", "codex", "grader", "opus") is True


def test_recovery_does_not_change_sticky_client(tmp_path):
    state = make_state(tmp_path)
    state.set_current_client("brain", "codex")
    state.mark_unavailable(
        "local", "claude", "brain", "sonnet", "usage_limit", "limit"
    )
    state.mark_available("local", "claude", "brain", "sonnet")
    assert state.get_current_client("brain") == "codex"


def test_reason_round_trips(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "client_crash", "exit 1"
    )
    assert state.unavailable_reason("local", "codex", "worker", "sonnet") == {
        "category": "client_crash",
        "reason": "exit 1",
    }


def test_brain_capability_block_claims_one_notification_per_fingerprint(tmp_path):
    state = make_state(tmp_path)
    first = state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="destination-conflict",
        fingerprint="fp-a",
        now=10.0,
        initial_backoff=30.0,
    )
    assert first["notification_state"] == "pending"
    assert first["next_probe_at"] == 40.0
    assert state.claim_brain_capability_notification(
        tier="sonnet", fingerprint="fp-a"
    ) is True
    assert state.claim_brain_capability_notification(
        tier="sonnet", fingerprint="fp-a"
    ) is False

    repeated = state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="destination-conflict",
        fingerprint="fp-a",
        now=20.0,
        initial_backoff=30.0,
    )
    assert repeated["notification_state"] == "claimed"

    changed = state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="source-missing",
        fingerprint="fp-b",
        now=30.0,
        initial_backoff=30.0,
    )
    assert changed["notification_state"] == "pending"
    assert state.claim_brain_capability_notification(
        tier="sonnet", fingerprint="fp-b"
    ) is True


def test_brain_capability_recheck_and_recovery_preserve_sticky_provider(tmp_path):
    state = make_state(tmp_path)
    state.set_current_client("brain", "codex")
    state.record_brain_capability_block(
        tier="opus",
        category="command_bridge",
        reason="destination-conflict",
        fingerprint="fp-live",
        now=100.0,
        initial_backoff=15.0,
    )
    assert state.brain_capability_recheck_due(tier="opus", now=114.9) is False
    assert state.brain_capability_recheck_due(tier="opus", now=115.0) is True

    state.record_brain_capability_recovery(tier="opus")

    assert state.get_current_client("brain") == "codex"
    assert state.is_available("local", "codex", "brain", "opus") is True
    row = state._conn.execute(
        "SELECT capability_fingerprint, notification_state, next_probe_at "
        "FROM provider_capability_state "
        "WHERE host='local' AND client='codex' AND role='brain' AND tier='opus'"
    ).fetchone()
    assert tuple(row) == (None, "pending", None)


def test_brain_capability_backoff_saturates_and_survives_reopen(tmp_path):
    db_path = str(tmp_path / "bounded.db")
    conn = init_db(db_path)
    state = ProviderState(conn)
    state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="missing",
        fingerprint="fp-bounded",
        now=10.0,
        initial_backoff=600.0,
    )
    saturated = state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="missing",
        fingerprint="fp-bounded",
        now=20.0,
        initial_backoff=600.0,
    )
    assert saturated["probe_backoff_seconds"] == 900.0
    assert saturated["next_probe_at"] == 920.0
    conn.close()

    reopened = ProviderState(init_db(db_path))
    persisted = reopened.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="missing",
        fingerprint="fp-bounded",
        now=30.0,
        initial_backoff=600.0,
    )
    assert persisted["probe_backoff_seconds"] == 900.0
    assert persisted["next_probe_at"] == 930.0


def test_brain_capability_backoff_clamps_legacy_oversized_value(tmp_path):
    state = make_state(tmp_path)
    state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="missing",
        fingerprint="fp-legacy",
        now=1.0,
        initial_backoff=30.0,
    )
    state._conn.execute(
        "UPDATE provider_capability_state SET probe_backoff_seconds=? "
        "WHERE host='local' AND client='codex' AND role='brain' AND tier='sonnet'",
        (1e30,),
    )
    state._conn.commit()

    clamped = state.record_brain_capability_block(
        tier="sonnet",
        category="command_bridge",
        reason="missing",
        fingerprint="fp-legacy",
        now=2.0,
        initial_backoff=30.0,
    )
    assert clamped["probe_backoff_seconds"] == 900.0
    assert clamped["next_probe_at"] == 902.0


def test_capability_observation_preserves_quarantine_until_explicit_recovery(tmp_path):
    state = make_state(tmp_path)
    state.set_current_client("worker", "codex")
    state.record_capability(
        "host-a", "claude", "worker", "sonnet",
        configured=True, supported=True, installed=True,
        authenticated=True, reason="manual_quarantine", available=False,
    )
    failed = state.capability_observation("host-a", "claude", "worker", "sonnet")
    assert failed == {
        "configured": True, "supported": True, "installed": True,
        "authenticated": True, "available": False, "reason": "manual_quarantine",
    }

    state.record_capability(
        "host-a", "claude", "worker", "sonnet",
        configured=True, supported=True, installed=True,
        authenticated=None, reason=None, available=True,
    )
    still_quarantined = state.capability_observation(
        "host-a", "claude", "worker", "sonnet"
    )
    assert still_quarantined["available"] is False
    assert state.unavailable_reason("host-a", "claude", "worker", "sonnet") == {
        "category": "probe_failure", "reason": "manual_quarantine",
    }
    state.mark_available("host-a", "claude", "worker", "sonnet")
    recovered = state.capability_observation("host-a", "claude", "worker", "sonnet")
    assert recovered["available"] is True
    assert recovered["reason"] is None
    assert state.unavailable_reason("host-a", "claude", "worker", "sonnet") is None
    assert state.get_current_client("worker") == "codex"


def test_explicit_cutover_reset_removes_only_target_client_role_capabilities(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "host-a", "codex", "worker", "sonnet", "usage_limit", "worker limit"
    )
    state.mark_unavailable(
        "host-a", "codex", "grader", "opus", "usage_limit", "grader limit"
    )
    state.mark_unavailable(
        "host-b", "claude", "worker", "sonnet", "usage_limit", "claude limit"
    )

    state.set_current_client("worker", "codex", reset_capabilities=True)

    assert state.get_current_client("worker") == "codex"
    assert state.capability_observation(
        "host-a", "codex", "worker", "sonnet"
    ) is None
    assert state.is_available("host-a", "codex", "grader", "opus") is False
    assert state.is_available("host-b", "claude", "worker", "sonnet") is False


def test_explicit_cutover_reset_rolls_back_delete_when_sticky_upsert_fails(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "usage_limit", "keep on rollback"
    )
    state._conn.execute(
        """
        CREATE TRIGGER reject_provider_role_insert
        BEFORE INSERT ON provider_role_state
        BEGIN
            SELECT RAISE(ABORT, 'forced sticky failure');
        END
        """
    )
    state._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced sticky failure"):
        state.set_current_client("worker", "codex", reset_capabilities=True)

    assert state.is_available("local", "codex", "worker", "sonnet") is False
    assert state.get_current_client("worker") is None


def test_ordinary_sticky_write_preserves_provider_quarantine(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "usage_limit", "still limited"
    )

    state.set_current_client("worker", "codex")

    assert state.is_available("local", "codex", "worker", "sonnet") is False


def _record_probe(state, *, reason, available):
    state.record_capability(
        "host-a", "codex", "worker", "sonnet",
        configured=True, supported=True, installed=True,
        authenticated=None if available else False,
        reason=reason, available=available,
    )


@pytest.mark.parametrize(
    "reason", ["probe_timeout", "not_authenticated", "executable_probe_failed"]
)
def test_transient_quarantine_clears_on_successful_probe(tmp_path, reason):
    state = make_state(tmp_path)
    _record_probe(state, reason=reason, available=False)
    assert state.is_available("host-a", "codex", "worker", "sonnet") is False

    _record_probe(state, reason=None, available=True)

    assert state.is_available("host-a", "codex", "worker", "sonnet") is True
    observed = state.capability_observation("host-a", "codex", "worker", "sonnet")
    assert observed["reason"] is None
    assert state.unavailable_reason("host-a", "codex", "worker", "sonnet") is None


@pytest.mark.parametrize(
    "reason",
    [
        "not_configured",
        "unsupported",
        "missing_executable",
        "unsupported_auth_mode",
        "executable_error",
    ],
)
def test_hard_quarantine_survives_successful_probe(tmp_path, reason):
    state = make_state(tmp_path)
    _record_probe(state, reason=reason, available=False)

    _record_probe(state, reason=None, available=True)

    assert state.is_available("host-a", "codex", "worker", "sonnet") is False
    assert state.unavailable_reason("host-a", "codex", "worker", "sonnet") == {
        "category": "probe_failure",
        "reason": reason,
    }


def test_unknown_reason_survives_successful_probe(tmp_path):
    state = make_state(tmp_path)
    _record_probe(state, reason="some_future_reason", available=False)

    _record_probe(state, reason=None, available=True)

    assert state.is_available("host-a", "codex", "worker", "sonnet") is False


def test_failed_probe_still_forces_unavailable_over_available_row(tmp_path):
    state = make_state(tmp_path)
    _record_probe(state, reason=None, available=True)

    _record_probe(state, reason="probe_timeout", available=False)

    assert state.is_available("host-a", "codex", "worker", "sonnet") is False


def test_cleared_transient_row_leaves_unavailable_capabilities(tmp_path):
    state = make_state(tmp_path)
    _record_probe(state, reason="probe_timeout", available=False)
    assert state.unavailable_capabilities("codex", "worker") != []

    _record_probe(state, reason=None, available=True)

    assert state.unavailable_capabilities("codex", "worker") == []


def test_transient_reason_set_is_exact():
    from ironclaude.provider_state import TRANSIENT_UNAVAILABLE_REASONS

    assert tuple(TRANSIENT_UNAVAILABLE_REASONS) == (
        "probe_timeout",
        "not_authenticated",
        "executable_probe_failed",
    )


def test_unavailable_capabilities_reports_selected_client_role_only(tmp_path):
    state = make_state(tmp_path)
    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "usage_limit", "worker limit"
    )
    state.mark_unavailable(
        "remote-a", "codex", "worker", "opus", "expired_auth", "login"
    )
    state.mark_unavailable(
        "local", "codex", "grader", "opus", "usage_limit", "grader limit"
    )

    assert state.unavailable_capabilities("codex", "worker") == [
        {
            "host": "local",
            "tier": "sonnet",
            "category": "usage_limit",
            "reason": "worker limit",
        },
        {
            "host": "remote-a",
            "tier": "opus",
            "category": "expired_auth",
            "reason": "login",
        },
    ]
