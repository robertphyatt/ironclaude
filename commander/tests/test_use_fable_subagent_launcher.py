"""Hermetic tests for the Codex-to-Fable report-only launcher."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "worker/skills/use-fable-subagent/scripts/run_fable_subagent.py"
_REAL_KILLPG = os.killpg


def _load_module():
    spec = importlib.util.spec_from_file_location("run_fable_subagent", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def launcher():
    return _load_module()


def _events(report: str = "bounded report", model: str = "claude-fable-5") -> bytes:
    rows = [
        {"type": "assistant", "message": {"model": model, "content": []}},
        {"type": "result", "subtype": "success", "result": report},
    ]
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()


def _python_child(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def test_build_argv_is_restricted_high_effort_fable_without_fallback(launcher):
    argv = launcher.build_argv("/opt/bin/claude")
    assert argv == [
        "/opt/bin/claude",
        "-p",
        "--model",
        "fable",
        "--effort",
        "high",
        "--restricted",
        "--tools",
        "",
        "--permission-prompts",
        "none",
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]
    assert len(argv) == 16
    assert "--fallback-model" not in argv
    assert "--dangerously-skip-permissions" not in argv


def test_sanitized_env_removes_provider_routing_and_sets_high_effort(launcher):
    source = {
        "KEEP": "yes",
        "ANTHROPIC_API_KEY": "remove",
        "ANTHROPIC_AUTH_TOKEN": "remove",
        "ANTHROPIC_BASE_URL": "remove",
        "CLAUDE_CODE_USE_BEDROCK": "remove",
        "CLAUDE_CODE_USE_VERTEX": "remove",
    }
    result = launcher.sanitized_env(source)
    assert result["KEEP"] == "yes"
    assert result["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    for key in source:
        if key != "KEEP":
            assert key not in result


def test_parse_verified_report_accepts_only_effective_fable_identity(launcher):
    assert launcher.parse_verified_report(_events().decode()) == "bounded report"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b'{"type":"result","subtype":"success","result":"x"}\n', "identity"),
        (_events(model="claude-opus-4-1"), "non-Fable"),
        (
            _events(model="claude-fable-5")
            + b'{"type":"assistant","message":{"model":"claude-opus-4-1"}}\n',
            "mixed",
        ),
        (b"not-json\n", "JSON"),
        (b'{"type":"assistant","message":{"model":"claude-fable-5"}}\n', "report"),
    ],
)
def test_parse_verified_report_rejects_unverified_or_malformed_output(
    launcher, payload, message
):
    with pytest.raises(launcher.FableSubagentError, match=message):
        launcher.parse_verified_report(payload.decode())


def test_run_uses_empty_private_cwd_binary_pipes_and_sanitized_env(launcher):
    captured = {}

    def refusing_factory(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        captured["cwd_entries"] = list(Path(kwargs["cwd"]).iterdir())
        raise OSError("deliberate")

    with pytest.raises(launcher.FableSubagentError, match="start Claude"):
        launcher.run_fable_subagent("prompt", process_factory=refusing_factory)

    assert captured["argv"][0] == "claude"
    assert captured["stdin"] is subprocess.PIPE
    assert captured["stdout"] is subprocess.PIPE
    assert captured["stderr"] is subprocess.PIPE
    assert captured["start_new_session"] is True
    assert captured["text"] is False
    assert captured["cwd_entries"] == []
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_run_writes_prompt_while_draining_output_without_deadlock(launcher, monkeypatch):
    prefix = json.dumps({"type": "system", "padding": "x" * 200_000}) + "\n"
    child = (
        "import json,sys\n"
        f"sys.stdout.write({prefix!r}); sys.stdout.flush()\n"
        "prompt=sys.stdin.read()\n"
        "print(json.dumps({'type':'assistant','message':{'model':'claude-fable-5'}}), flush=True)\n"
        "print(json.dumps({'type':'result','subtype':'success','result':str(len(prompt))}), flush=True)\n"
    )
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    prompt = "p" * 250_000
    assert launcher.run_fable_subagent(prompt, timeout_seconds=5) == str(len(prompt))


def test_run_accepts_output_at_exact_combined_byte_limit(launcher, monkeypatch):
    payload = _events("exact")
    child = f"import sys; sys.stdin.read(); sys.stdout.buffer.write({payload!r}); sys.stdout.flush()"
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    assert launcher.run_fable_subagent("p", max_output_bytes=len(payload)) == "exact"


@pytest.mark.parametrize(("stdout_bytes", "stderr_bytes"), [(1001, 0), (0, 1001), (600, 401)])
def test_run_hard_bounds_stdout_stderr_and_combined_output(
    launcher, monkeypatch, stdout_bytes, stderr_bytes
):
    child = (
        "import os,sys,time\n"
        "sys.stdin.read()\n"
        f"os.write(1, b'x'*{stdout_bytes})\n"
        f"os.write(2, b'y'*{stderr_bytes})\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    monkeypatch.setattr(launcher.os, "killpg", _REAL_KILLPG)
    processes = []

    def tracking_factory(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        processes.append(proc)
        return proc

    with pytest.raises(launcher.FableSubagentError, match="output limit"):
        launcher.run_fable_subagent(
            "p", process_factory=tracking_factory, timeout_seconds=5, max_output_bytes=1000
        )
    assert processes[0].poll() is not None


def test_run_timeout_kills_process_group_and_reaps_child(launcher, monkeypatch):
    child = "import sys,time; sys.stdin.read(); time.sleep(30)"
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    killed = []

    def recording_killpg(pid, sig):
        killed.append((pid, sig))
        return _REAL_KILLPG(pid, sig)

    monkeypatch.setattr(launcher.os, "killpg", recording_killpg)
    processes = []

    def tracking_factory(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        processes.append(proc)
        return proc

    with pytest.raises(launcher.FableSubagentError, match="timed out"):
        launcher.run_fable_subagent(
            "p", process_factory=tracking_factory, timeout_seconds=0.1
        )
    assert killed and killed[0][0] == processes[0].pid
    assert processes[0].poll() is not None


def test_run_rejects_nonzero_exit_even_with_valid_output(launcher, monkeypatch):
    payload = _events("ignore")
    child = (
        f"import sys; sys.stdin.read(); sys.stdout.buffer.write({payload!r}); "
        "sys.stdout.flush(); raise SystemExit(7)"
    )
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    with pytest.raises(launcher.FableSubagentError, match="status 7"):
        launcher.run_fable_subagent("p", timeout_seconds=5)


def test_launcher_declares_closed_ai_communication_path():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "IRONCLAUDE_LLM_PATH: actual_fable_subagent; destination AI." in source


@pytest.mark.parametrize(
    ("prompt", "timeout", "limit", "message"),
    [
        ("", 1, 100, "prompt"),
        ("p", 0, 100, "timeout"),
        ("p", 1, 0, "output limit"),
    ],
)
def test_run_rejects_invalid_inputs_before_spawning(
    launcher, prompt, timeout, limit, message
):
    spawned = []
    with pytest.raises(launcher.FableSubagentError, match=message):
        launcher.run_fable_subagent(
            prompt,
            timeout_seconds=timeout,
            max_output_bytes=limit,
            process_factory=lambda *a, **k: spawned.append((a, k)),
        )
    assert spawned == []


def test_run_rejects_invalid_utf8(launcher, monkeypatch):
    child = "import os,sys; sys.stdin.read(); os.write(1, b'\\xff')"
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    with pytest.raises(launcher.FableSubagentError, match="invalid UTF-8"):
        launcher.run_fable_subagent("p", timeout_seconds=5)


def test_run_tolerates_child_closing_stdin_after_returning_report(launcher, monkeypatch):
    payload = _events("closed")
    child = (
        "import os,time\n"
        "os.close(0)\n"
        f"os.write(1, {payload!r})\n"
        "time.sleep(0.05)\n"
    )
    monkeypatch.setattr(launcher, "build_argv", lambda _="claude": _python_child(child))
    assert launcher.run_fable_subagent("p" * 200_000, timeout_seconds=5) == "closed"


def test_kill_process_group_falls_back_to_direct_child_kill(launcher, monkeypatch):
    class FakeProcess:
        pid = 123
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    proc = FakeProcess()
    monkeypatch.setattr(
        launcher.os, "killpg", lambda *_: (_ for _ in ()).throw(PermissionError())
    )
    launcher._kill_process_group(proc)
    assert proc.killed is True


def test_cli_prints_only_verified_report_on_success(launcher, monkeypatch, tmp_path, capsys):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("complete packet", encoding="utf-8")
    observed = {}

    def fake_run(prompt, *, timeout_seconds):
        observed.update(prompt=prompt, timeout=timeout_seconds)
        return "verified report"

    monkeypatch.setattr(launcher, "run_fable_subagent", fake_run)
    assert launcher.main(["--prompt-file", str(prompt_file), "--timeout-seconds", "7"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "verified report\n"
    assert captured.err == ""
    assert observed == {"prompt": "complete packet", "timeout": 7.0}


def test_cli_bounds_failure_diagnostic(launcher, monkeypatch, tmp_path, capsys):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("packet", encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise launcher.FableSubagentError("x" * 10_000)

    monkeypatch.setattr(launcher, "run_fable_subagent", fail)
    assert launcher.main(["--prompt-file", str(prompt_file)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("FABLE_SUBAGENT_ERROR: ")
    assert len(captured.err) <= len("FABLE_SUBAGENT_ERROR: \n") + launcher.MAX_ERROR_CHARS
