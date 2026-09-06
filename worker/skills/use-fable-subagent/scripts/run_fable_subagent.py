#!/usr/bin/env python3
"""Run one verified, report-only Claude Fable consultation for Codex."""

# IRONCLAUDE_LLM_PATH: actual_fable_subagent; destination AI.

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence


DEFAULT_TIMEOUT_SECONDS = 900
MAX_OUTPUT_BYTES = 1_000_000
MAX_ERROR_CHARS = 2_000
_SANITIZED_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)


class FableSubagentError(RuntimeError):
    """A bounded Fable consultation could not be verified."""


def build_argv(claude_executable: str = "claude") -> list[str]:
    return [
        claude_executable,
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


def sanitized_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    for key in _SANITIZED_VARIABLES:
        env.pop(key, None)
    env["CLAUDE_CODE_EFFORT_LEVEL"] = "high"
    return env


def _bounded_detail(detail: object) -> str:
    return str(detail).replace("\x00", "")[:MAX_ERROR_CHARS]


def _is_fable_model(model: str) -> bool:
    lowered = model.strip().lower()
    return lowered == "fable" or lowered.startswith("claude-fable-")


def parse_verified_report(
    stdout: str, max_output_bytes: int = MAX_OUTPUT_BYTES
) -> str:
    if len(stdout.encode("utf-8")) > max_output_bytes:
        raise FableSubagentError("Claude output exceeded output limit")

    models: set[str] = set()
    report: str | None = None
    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise FableSubagentError(
                f"invalid stream JSON on line {line_number}: {_bounded_detail(exc)}"
            ) from exc
        if not isinstance(event, dict):
            raise FableSubagentError(f"invalid stream JSON event on line {line_number}")
        if event.get("type") == "assistant":
            message = event.get("message")
            model = message.get("model") if isinstance(message, dict) else None
            if isinstance(model, str) and model.strip():
                models.add(model.strip())
        if event.get("type") == "result" and event.get("subtype") == "success":
            candidate = event.get("result")
            if isinstance(candidate, str) and candidate:
                report = candidate

    if not models:
        raise FableSubagentError("effective Fable identity was not reported")
    if len(models) != 1:
        raise FableSubagentError(
            f"mixed effective model identities: {_bounded_detail(sorted(models))}"
        )
    model = next(iter(models))
    if not _is_fable_model(model):
        raise FableSubagentError(f"non-Fable effective model identity: {model}")
    if report is None:
        raise FableSubagentError("successful Fable report was not returned")
    return report


def _safe_unregister(selector: selectors.BaseSelector, stream: object) -> None:
    try:
        selector.unregister(stream)
    except (KeyError, ValueError):
        pass


def _safe_close(stream: object) -> None:
    try:
        stream.close()  # type: ignore[attr-defined]
    except OSError:
        pass


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def _terminate_drain_and_reap(
    proc: subprocess.Popen[bytes],
    selector: selectors.BaseSelector,
    stdout: bytearray,
    stderr: bytearray,
    max_output_bytes: int,
) -> None:
    """Kill whole consultation, retain at most cap bytes, and reap direct child."""
    _kill_process_group(proc)

    if proc.stdin is not None:
        _safe_unregister(selector, proc.stdin)
        _safe_close(proc.stdin)

    drain_deadline = time.monotonic() + 1.0
    while selector.get_map() and time.monotonic() < drain_deadline:
        events = selector.select(timeout=0.05)
        if not events and proc.poll() is not None:
            break
        for key, _ in events:
            stream = key.fileobj
            destination = stdout if key.data == "stdout" else stderr
            retained = len(stdout) + len(stderr)
            allowance = max(0, max_output_bytes - retained)
            if allowance == 0:
                _safe_unregister(selector, stream)
                _safe_close(stream)
                continue
            try:
                chunk = os.read(stream.fileno(), min(65_536, allowance))
            except (BlockingIOError, OSError):
                continue
            if chunk:
                destination.extend(chunk)
            else:
                _safe_unregister(selector, stream)
                _safe_close(stream)

    for key in list(selector.get_map().values()):
        _safe_unregister(selector, key.fileobj)
        _safe_close(key.fileobj)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()


def run_fable_subagent(
    prompt: str,
    *,
    process_factory=subprocess.Popen,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> str:
    if not isinstance(prompt, str) or not prompt:
        raise FableSubagentError("prompt must be nonempty text")
    if timeout_seconds <= 0:
        raise FableSubagentError("timeout must be positive")
    if max_output_bytes <= 0:
        raise FableSubagentError("output limit must be positive")

    prompt_bytes = prompt.encode("utf-8")
    stdout = bytearray()
    stderr = bytearray()

    with tempfile.TemporaryDirectory(prefix="ironclaude-fable-") as private_cwd:
        try:
            proc = process_factory(
                build_argv(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=private_cwd,
                env=sanitized_env(),
                text=False,
                bufsize=0,
                start_new_session=True,
            )
        except OSError as exc:
            raise FableSubagentError(
                f"could not start Claude: {_bounded_detail(exc)}"
            ) from exc

        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            _kill_process_group(proc)
            proc.wait()
            raise FableSubagentError("Claude process did not expose required pipes")

        selector = selectors.DefaultSelector()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        if prompt_bytes:
            selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            proc.stdin.close()

        prompt_offset = 0
        deadline = time.monotonic() + timeout_seconds
        failure: str | None = None
        try:
            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    failure = "Claude Fable consultation timed out"
                    break
                events = selector.select(timeout=min(remaining_time, 0.1))
                if not events:
                    if proc.poll() is not None:
                        # EOF readiness normally follows; bounded retry avoids a spin.
                        continue
                    continue

                for key, _ in events:
                    stream = key.fileobj
                    if key.data == "stdin":
                        try:
                            count = os.write(stream.fileno(), prompt_bytes[prompt_offset:])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            count = len(prompt_bytes) - prompt_offset
                        prompt_offset += count
                        if prompt_offset >= len(prompt_bytes):
                            _safe_unregister(selector, stream)
                            _safe_close(stream)
                        continue

                    retained = len(stdout) + len(stderr)
                    allowance = max_output_bytes - retained
                    try:
                        chunk = os.read(stream.fileno(), min(65_536, allowance + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        _safe_unregister(selector, stream)
                        _safe_close(stream)
                        continue
                    if len(chunk) > allowance:
                        destination = stdout if key.data == "stdout" else stderr
                        destination.extend(chunk[: max(0, allowance)])
                        failure = "Claude Fable consultation exceeded output limit"
                        break
                    destination = stdout if key.data == "stdout" else stderr
                    destination.extend(chunk)
                if failure is not None:
                    break

            if failure is not None:
                _terminate_drain_and_reap(
                    proc, selector, stdout, stderr, max_output_bytes
                )
                raise FableSubagentError(failure)

            remaining_time = max(0.0, deadline - time.monotonic())
            try:
                returncode = proc.wait(timeout=remaining_time)
            except subprocess.TimeoutExpired as exc:
                _terminate_drain_and_reap(
                    proc, selector, stdout, stderr, max_output_bytes
                )
                raise FableSubagentError("Claude Fable consultation timed out") from exc
        finally:
            selector.close()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                _safe_close(stream)

    try:
        stdout_text = stdout.decode("utf-8", errors="strict")
        stderr_text = stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FableSubagentError(
            f"Claude returned invalid UTF-8: {_bounded_detail(exc)}"
        ) from exc
    if returncode != 0:
        detail = _bounded_detail(stderr_text) or "no stderr"
        raise FableSubagentError(f"Claude exited with status {returncode}: {detail}")
    return parse_verified_report(stdout_text, max_output_bytes=max_output_bytes)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        prompt = args.prompt_file.read_text(encoding="utf-8")
        report = run_fable_subagent(prompt, timeout_seconds=args.timeout_seconds)
    except (OSError, UnicodeError, FableSubagentError) as exc:
        print(f"FABLE_SUBAGENT_ERROR: {_bounded_detail(exc)}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
