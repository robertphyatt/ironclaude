"""Hermetic tests for repository-owned Codex restart helper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "worker/scripts/restart-codex.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("restart_codex", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper():
    return _load_module()


def test_dry_run_reports_reviewed_paths_without_spawning(helper, monkeypatch, capsys):
    monkeypatch.setattr(
        helper.subprocess,
        "Popen",
        lambda *_a, **_k: pytest.fail("dry-run spawned a process"),
    )
    assert helper.main(["--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["script"] == str(SCRIPT.resolve())
    assert payload["sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert payload["quit_argv"] == [
        "/usr/bin/pkill",
        "-9",
        "-a",
        "-f",
        r"^/Applications/ChatGPT\.app/Contents/MacOS/ChatGPT( |$)",
    ]
    assert payload["probe_argv"] == [
        "/usr/bin/pgrep",
        "-a",
        "-f",
        r"^/Applications/ChatGPT\.app/Contents/MacOS/ChatGPT( |$)",
    ]
    assert payload["launch_argv"] == ["/usr/bin/open", "/Applications/ChatGPT.app"]


def test_probe_pattern_is_valid_posix_ere_and_matches_exact_app_executable(helper):
    pattern = helper.PROBE_ARGV[-1]
    assert helper.QUIT_ARGV[-1] == pattern
    valid = subprocess.run(
        ["/usr/bin/grep", "-E", pattern],
        input="/Applications/ChatGPT.app/Contents/MacOS/ChatGPT\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr
    invalid = subprocess.run(
        ["/usr/bin/grep", "-E", pattern],
        input="/tmp/ChatGPT\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid.returncode == 1, invalid.stderr


def test_schedule_captures_immutable_private_snapshot_and_detaches_it(
    helper, tmp_path
):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return SimpleNamespace(pid=4321)

    result = helper.schedule_restart(
        script_path=SCRIPT,
        popen_factory=fake_popen,
        snapshot_root=tmp_path,
    )
    digest = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    snapshot = Path(result["snapshot"])
    assert result == {
        "pid": 4321,
        "sha256": digest,
        "source": str(SCRIPT.resolve()),
        "snapshot": str(snapshot),
    }
    assert snapshot.read_bytes() == SCRIPT.read_bytes()
    assert snapshot.stat().st_mode & 0o777 == 0o500
    assert snapshot.parent.stat().st_mode & 0o777 == 0o700
    assert captured["argv"] == [
        sys.executable,
        str(snapshot),
        "--perform",
        "--expected-sha256",
        digest,
    ]
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["start_new_session"] is True
    assert captured["close_fds"] is True
    assert captured["cwd"] == "/"
    helper.cleanup_snapshot(snapshot, snapshot_root=tmp_path)
    assert not snapshot.parent.exists()


def test_snapshot_bytes_remain_bound_when_source_path_changes(helper, tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(SCRIPT.read_bytes())
    source.chmod(0o644)
    launch = {}

    def fake_popen(argv, **_kwargs):
        launch["snapshot"] = Path(argv[1])
        return SimpleNamespace(pid=9)

    result = helper.schedule_restart(
        script_path=source,
        popen_factory=fake_popen,
        snapshot_root=tmp_path,
    )
    source.write_text("substituted", encoding="utf-8")
    snapshot = launch["snapshot"]
    assert helper.sha256_file(snapshot) == result["sha256"]
    assert snapshot.read_bytes() != source.read_bytes()
    helper.cleanup_snapshot(snapshot, snapshot_root=tmp_path)


def test_schedule_rejects_symlink_and_group_writable_source(helper, tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(SCRIPT.read_bytes())
    source.chmod(0o664)
    with pytest.raises(RuntimeError, match="writable"):
        helper.schedule_restart(
            script_path=source,
            popen_factory=lambda *_a, **_k: None,
            snapshot_root=tmp_path,
        )
    source.chmod(0o644)
    link = tmp_path / "link.py"
    link.symlink_to(source)
    with pytest.raises(RuntimeError, match="symlink"):
        helper.schedule_restart(
            script_path=link,
            popen_factory=lambda *_a, **_k: None,
            snapshot_root=tmp_path,
        )


def test_schedule_failure_removes_only_created_snapshot(helper, tmp_path):
    def fail_spawn(*_args, **_kwargs):
        raise OSError("spawn blocked")

    with pytest.raises(OSError, match="spawn blocked"):
        helper.schedule_restart(
            script_path=SCRIPT,
            popen_factory=fail_spawn,
            snapshot_root=tmp_path,
        )
    assert list(tmp_path.glob("ironclaude-restart-*")) == []


def test_perform_refuses_digest_mismatch_before_sleep_or_force_quit(
    helper, tmp_path
):
    calls = []
    result = helper.perform_restart(
        "0" * 64,
        script_path=SCRIPT,
        runner=lambda *a, **k: calls.append((a, k)),
        sleeper=lambda value: calls.append(("sleep", value)),
        log_path=tmp_path / "restart.log",
    )
    assert result == helper.EXIT_DIGEST_MISMATCH
    assert calls == []
    assert "digest mismatch" in (tmp_path / "restart.log").read_text()


def test_perform_rechecks_digest_immediately_before_quit(helper, tmp_path):
    calls = []
    digests = iter(["a" * 64, "b" * 64])
    helper.sha256_file = lambda _path: next(digests)
    result = helper.perform_restart(
        "a" * 64,
        script_path=SCRIPT,
        runner=lambda *a, **k: calls.append((a, k)),
        sleeper=lambda value: calls.append(("sleep", value)),
        log_path=tmp_path / "restart.log",
    )
    assert result == helper.EXIT_DIGEST_MISMATCH
    assert calls == [("sleep", helper.START_DELAY_SECONDS)]
    assert "pre-quit digest mismatch" in (tmp_path / "restart.log").read_text()


def test_perform_uses_exact_commands_proves_exit_and_startup(helper, tmp_path):
    calls = []
    probes = iter([1, 1, 0])  # quit complete; first launch poll absent; second present

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv == helper.PROBE_ARGV:
            return SimpleNamespace(returncode=next(probes))
        return SimpleNamespace(returncode=0)

    sleeps = []
    digest = helper.sha256_file(SCRIPT)
    result = helper.perform_restart(
        digest,
        script_path=SCRIPT,
        runner=runner,
        sleeper=sleeps.append,
        poll_attempts=3,
        log_path=tmp_path / "restart.log",
    )
    assert result == 0
    assert [call[0] for call in calls] == [
        helper.QUIT_ARGV,
        helper.PROBE_ARGV,
        helper.LAUNCH_ARGV,
        helper.PROBE_ARGV,
        helper.PROBE_ARGV,
    ]
    for _argv, kwargs in calls:
        assert kwargs["check"] is False
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == helper.COMMAND_TIMEOUT_SECONDS
    assert sleeps == [
        helper.START_DELAY_SECONDS,
        helper.POLL_INTERVAL_SECONDS,
    ]
    log = (tmp_path / "restart.log").read_text()
    assert "restart requested" in log
    assert "quit complete" in log
    assert "relaunch complete" in log


def test_quit_timeout_is_distinct_and_never_launches(helper, tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    result = helper.perform_restart(
        helper.sha256_file(SCRIPT),
        script_path=SCRIPT,
        runner=runner,
        sleeper=lambda _value: None,
        poll_attempts=2,
        log_path=tmp_path / "restart.log",
    )
    assert result == helper.EXIT_QUIT_TIMEOUT
    assert helper.LAUNCH_ARGV not in calls
    assert "quit timeout" in (tmp_path / "restart.log").read_text()


def test_force_quit_no_match_still_verifies_exit_before_launch(helper, tmp_path):
    calls = []
    probes = iter([1, 0])

    def runner(argv, **_kwargs):
        calls.append(argv)
        if argv == helper.QUIT_ARGV:
            return SimpleNamespace(returncode=1)
        if argv == helper.PROBE_ARGV:
            return SimpleNamespace(returncode=next(probes))
        return SimpleNamespace(returncode=0)

    assert helper.perform_restart(
        helper.sha256_file(SCRIPT), script_path=SCRIPT, runner=runner,
        sleeper=lambda _value: None, poll_attempts=2,
        log_path=tmp_path / "restart.log",
    ) == 0
    assert calls == [helper.QUIT_ARGV, helper.PROBE_ARGV,
                     helper.LAUNCH_ARGV, helper.PROBE_ARGV]


def test_force_quit_no_signal_never_launches_while_app_remains(helper, tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1 if argv == helper.QUIT_ARGV else 0)

    assert helper.perform_restart(
        helper.sha256_file(SCRIPT), script_path=SCRIPT, runner=runner,
        sleeper=lambda _value: None, poll_attempts=2,
        log_path=tmp_path / "restart.log",
    ) == helper.EXIT_QUIT_TIMEOUT
    assert helper.LAUNCH_ARGV not in calls


@pytest.mark.parametrize("returncode", [2, 3])
def test_force_quit_command_error_never_launches(helper, tmp_path, returncode):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=returncode)

    assert helper.perform_restart(
        helper.sha256_file(SCRIPT), script_path=SCRIPT, runner=runner,
        sleeper=lambda _value: None,
        log_path=tmp_path / "restart.log",
    ) == helper.EXIT_COMMAND_FAILURE
    assert calls == [helper.QUIT_ARGV]
    assert f"pkill returned status {returncode}" in (
        tmp_path / "restart.log"
    ).read_text()


def test_relaunch_timeout_is_distinct_without_false_success(helper, tmp_path):
    calls = []
    probe_codes = iter([1, 1, 1])

    def runner(argv, **_kwargs):
        calls.append(argv)
        if argv == helper.PROBE_ARGV:
            return SimpleNamespace(returncode=next(probe_codes))
        return SimpleNamespace(returncode=0)

    result = helper.perform_restart(
        helper.sha256_file(SCRIPT),
        script_path=SCRIPT,
        runner=runner,
        sleeper=lambda _value: None,
        poll_attempts=2,
        log_path=tmp_path / "restart.log",
    )
    assert result == helper.EXIT_RELAUNCH_TIMEOUT
    assert helper.LAUNCH_ARGV in calls
    log = (tmp_path / "restart.log").read_text()
    assert "relaunch timeout" in log
    assert "relaunch complete" not in log


def test_command_failure_has_distinct_bounded_diagnostic(helper, tmp_path):
    def runner(_argv, **_kwargs):
        raise subprocess.TimeoutExpired(["command"], 5)

    result = helper.perform_restart(
        helper.sha256_file(SCRIPT),
        script_path=SCRIPT,
        runner=runner,
        sleeper=lambda _value: None,
        log_path=tmp_path / "restart.log",
    )
    assert result == helper.EXIT_COMMAND_FAILURE
    log = (tmp_path / "restart.log").read_text()
    assert "command failure" in log
    assert len(max(log.splitlines(), key=len)) <= helper.MAX_LOG_LINE_CHARS + 40


def test_secure_log_rejects_symlink_and_creates_owner_only_file(helper, tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve", encoding="utf-8")
    link = tmp_path / "restart.log"
    link.symlink_to(target)
    helper._record("must not follow", log_path=link)
    assert target.read_text(encoding="utf-8") == "preserve"

    log = tmp_path / "owned.log"
    helper._record("safe", log_path=log)
    assert "safe" in log.read_text(encoding="utf-8")
    assert log.stat().st_mode & 0o777 == 0o600


def test_malformed_digest_and_unexpected_probe_status_fail_without_false_success(
    helper, tmp_path
):
    calls = []
    assert helper.perform_restart(
        "NOT-A-DIGEST",
        script_path=SCRIPT,
        runner=lambda *a, **k: calls.append((a, k)),
        sleeper=lambda value: calls.append(("sleep", value)),
        log_path=tmp_path / "malformed.log",
    ) == helper.EXIT_DIGEST_MISMATCH
    assert calls == []

    def runner(argv, **_kwargs):
        if argv == helper.PROBE_ARGV:
            return SimpleNamespace(returncode=7)
        return SimpleNamespace(returncode=0)

    assert helper.perform_restart(
        helper.sha256_file(SCRIPT),
        script_path=SCRIPT,
        runner=runner,
        sleeper=lambda _value: None,
        log_path=tmp_path / "probe.log",
    ) == helper.EXIT_COMMAND_FAILURE
    assert "pgrep returned status 7" in (tmp_path / "probe.log").read_text()


def test_private_perform_mode_requires_digest(helper, capsys):
    assert helper.main(["--perform"]) == helper.EXIT_DIGEST_MISMATCH
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires expected digest" in captured.err
