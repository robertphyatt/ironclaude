import sqlite3

import pytest

from ironclaude.db import init_db
from ironclaude.provider_state import ProviderState


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
