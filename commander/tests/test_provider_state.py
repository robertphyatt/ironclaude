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
