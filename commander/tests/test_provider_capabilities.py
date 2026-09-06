import subprocess

from ironclaude.db import init_db
from ironclaude.provider_capabilities import (
    CapabilityProbe,
    CapabilityRegistry,
    ClientCapability,
    ProbeResult,
)
from ironclaude.provider_config import parse_provider_config
from ironclaude.provider_state import ProviderState


class FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, host, argv):
        self.calls.append((host, tuple(argv)))
        return self.results.pop(0)


def dual_config(base_config):
    raw = base_config()
    raw["clients"]["codex"]["enabled"] = True
    raw["roles"]["worker"]["clients"] = ["claude", "codex"]
    return parse_provider_config(raw)


def test_disabled_client_is_not_probed(base_config):
    executor = FakeExecutor([])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: None)
    result = probe.probe_local(
        parse_provider_config(base_config()), "codex", "worker", "sonnet"
    )
    assert result.configured is False
    assert executor.calls == []


def test_role_and_tier_must_be_explicitly_supported(base_config):
    probe = CapabilityProbe(executor=FakeExecutor([]), executable_lookup=lambda _: "/bin/codex")
    cfg = dual_config(base_config)
    brain = probe.probe_local(cfg, "codex", "brain", "sonnet")
    assert (brain.configured, brain.supported) == (False, True)
    unsupported = probe.probe_local(cfg, "codex", "worker", "nonexistent")
    assert (unsupported.configured, unsupported.supported) == (True, False)


def test_missing_executable_is_unusable(base_config):
    probe = CapabilityProbe(executor=FakeExecutor([]), executable_lookup=lambda _: None)
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert result.installed is False
    assert result.available is False
    assert result.usable is False
    assert result.reason == "missing_executable"


def test_codex_login_status_proves_chatgpt_authentication(base_config):
    executor = FakeExecutor([ProbeResult(0, "Logged in using ChatGPT", "")])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: "/bin/codex")
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert executor.calls == [("local", ("/bin/codex", "login", "status"))]
    assert result.authenticated is True
    assert result.available is True
    assert result.usable is True


def test_codex_login_status_stderr_proves_chatgpt_authentication(base_config):
    executor = FakeExecutor([ProbeResult(0, "", "Logged in using ChatGPT")])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: "/bin/codex")
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert executor.calls == [("local", ("/bin/codex", "login", "status"))]
    assert result.authenticated is True
    assert result.available is True
    assert result.usable is True


def test_other_successful_codex_auth_mode_is_rejected(base_config):
    executor = FakeExecutor([ProbeResult(0, "Logged in using API key", "")])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: "/bin/codex")
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert result.authenticated is False
    assert result.available is False
    assert result.reason == "unsupported_auth_mode"


def test_codex_login_failure_is_unusable(base_config):
    executor = FakeExecutor([ProbeResult(1, "", "Not logged in")])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: "/bin/codex")
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert result.authenticated is False
    assert result.available is False
    assert result.reason == "not_authenticated"


def test_claude_auth_is_unknown_until_runtime_observation(base_config):
    probe = CapabilityProbe(executor=FakeExecutor([]), executable_lookup=lambda _: "/bin/claude")
    result = probe.probe_local(
        parse_provider_config(base_config()), "claude", "worker", "sonnet"
    )
    assert result.installed is True
    assert result.authenticated is None
    assert result.available is True
    assert result.usable is True


def test_remote_host_must_explicitly_enable_client(base_config):
    executor = FakeExecutor([])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: None)
    result = probe.probe_remote(
        dual_config(base_config),
        {"claude": {"enabled": True, "path": "/opt/claude"}},
        "codex", "worker", "sonnet", "ssh-host",
    )
    assert result.configured is False
    assert executor.calls == []


def test_remote_missing_executable_is_distinct_from_auth_failure(base_config):
    executor = FakeExecutor([ProbeResult(127, "", "command not found")])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: None)
    result = probe.probe_remote(
        dual_config(base_config),
        {"codex": {"enabled": True, "path": "/missing/codex"}},
        "codex", "worker", "sonnet", "ssh-host",
    )
    assert executor.calls == [("ssh-host", ("/missing/codex", "--version"))]
    assert result.installed is False
    assert result.reason == "missing_executable"


def test_remote_executable_probe_failure_is_distinct(base_config):
    executor = FakeExecutor([ProbeResult(2, "", "permission denied")])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: None)
    result = probe.probe_remote(
        dual_config(base_config),
        {"codex": {"enabled": True, "path": "/opt/codex"}},
        "codex", "worker", "sonnet", "ssh-host",
    )
    assert result.installed is False
    assert result.reason == "executable_probe_failed"


def test_remote_executor_oserror_is_classified(base_config):
    def missing_remote_executable(host, argv):
        raise FileNotFoundError(argv[0])

    probe = CapabilityProbe(
        executor=missing_remote_executable, executable_lookup=lambda _: None
    )
    result = probe.probe_remote(
        dual_config(base_config),
        {"codex": {"enabled": True, "path": "/missing/codex"}},
        "codex", "worker", "sonnet", "ssh-host",
    )
    assert result.installed is False
    assert result.available is False
    assert result.reason == "executable_error"


def test_probe_timeout_becomes_unavailable_capability(base_config):
    def timeout_executor(host, argv):
        raise subprocess.TimeoutExpired(argv, 10)

    probe = CapabilityProbe(executor=timeout_executor, executable_lookup=lambda _: "/bin/codex")
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert result.usable is False
    assert result.reason == "probe_timeout"


def test_local_executor_oserror_becomes_unavailable_capability(base_config):
    def missing_after_lookup(host, argv):
        raise FileNotFoundError(argv[0])

    probe = CapabilityProbe(
        executor=missing_after_lookup, executable_lookup=lambda _: "/bin/codex"
    )
    result = probe.probe_local(dual_config(base_config), "codex", "worker", "sonnet")
    assert result.installed is False
    assert result.reason == "executable_error"


def test_remote_executor_uses_same_client_contract(base_config):
    executor = FakeExecutor([
        ProbeResult(0, "codex-cli 1.2.3", ""),
        ProbeResult(0, "Logged in using ChatGPT", ""),
    ])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: None)
    result = probe.probe_remote(
        dual_config(base_config),
        {"codex": {"enabled": True, "path": "/opt/codex"}},
        "codex", "worker", "sonnet", "ssh-host",
    )
    assert executor.calls == [
        ("ssh-host", ("/opt/codex", "--version")),
        ("ssh-host", ("/opt/codex", "login", "status")),
    ]
    assert result.host == "ssh-host"
    assert (result.role, result.tier) == ("worker", "sonnet")
    assert result.usable is True


def test_successful_probe_does_not_clear_usage_limit_quarantine(
    tmp_path, base_config
):
    executor = FakeExecutor([
        ProbeResult(0, "Logged in using ChatGPT", ""),
        ProbeResult(0, "Logged in using ChatGPT", ""),
    ])
    probe = CapabilityProbe(executor=executor, executable_lookup=lambda _: "/bin/codex")
    state = ProviderState(init_db(str(tmp_path / "commander.db")))
    registry = CapabilityRegistry(state)
    state.set_current_client("worker", "claude")

    registry.record(probe.probe_local(
        dual_config(base_config), "codex", "worker", "sonnet"
    ))
    assert registry.get("local", "codex", "worker", "sonnet").usable is True

    state.mark_unavailable(
        "local", "codex", "worker", "sonnet", "usage_limit", "limited"
    )
    registry.record(probe.probe_local(
        dual_config(base_config), "codex", "worker", "sonnet"
    ))
    assert registry.get("local", "codex", "worker", "sonnet").usable is False
    assert state.unavailable_reason("local", "codex", "worker", "sonnet") == {
        "category": "usage_limit", "reason": "limited",
    }

    state.mark_available("local", "codex", "worker", "sonnet")
    assert registry.get("local", "codex", "worker", "sonnet").usable is True
    assert state.get_current_client("worker") == "claude"
