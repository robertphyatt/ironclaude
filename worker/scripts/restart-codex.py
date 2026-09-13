#!/usr/bin/env python3
"""Self-verifying detached restart helper for the ChatGPT/Codex desktop app."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import Any


LOG_PATH = Path("/private/tmp/ironclaude-restart-codex.log")
MAX_LOG_BYTES = 64_000
MAX_LOG_LINE_CHARS = 1_000
MAX_SOURCE_BYTES = 1_000_000
START_DELAY_SECONDS = 2.0
POLL_INTERVAL_SECONDS = 0.25
POLL_ATTEMPTS = 120
COMMAND_TIMEOUT_SECONDS = 5.0

CHATGPT_EXECUTABLE_PATTERN = (
    r"^/Applications/ChatGPT\.app/Contents/MacOS/ChatGPT( |$)"
)
QUIT_ARGV = [
    "/usr/bin/pkill",
    "-9",
    "-a",  # Include the scheduling app's ancestor process on macOS.
    "-f",
    CHATGPT_EXECUTABLE_PATTERN,
]
PROBE_ARGV = [
    "/usr/bin/pgrep",
    "-a",  # macOS otherwise excludes the scheduling app's ancestor process.
    "-f",
    CHATGPT_EXECUTABLE_PATTERN,
]
LAUNCH_ARGV = ["/usr/bin/open", "/Applications/ChatGPT.app"]

EXIT_DIGEST_MISMATCH = 20
EXIT_COMMAND_FAILURE = 21
EXIT_QUIT_TIMEOUT = 22
EXIT_RELAUNCH_TIMEOUT = 23
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(message: object, *, log_path: Path = LOG_PATH) -> None:
    safe = str(message).replace("\n", " ").replace("\r", " ")[:MAX_LOG_LINE_CHARS]
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = -1
    try:
        fd = os.open(log_path, flags, 0o600)
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            return
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        if metadata.st_size >= MAX_LOG_BYTES:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
        timestamp = datetime.now(timezone.utc).isoformat()
        os.write(fd, f"{timestamp} {safe}\n".encode("utf-8"))
    except OSError:
        # Restart correctness must not depend on diagnostic-file availability.
        pass
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


def _read_trusted_source(path: Path) -> tuple[Path, bytes]:
    source = Path(os.path.abspath(path))
    if source.is_symlink():
        raise RuntimeError("restart helper source must not be a symlink")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise RuntimeError(f"restart helper source could not be opened: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("restart helper source must be a regular file")
        if metadata.st_uid != os.getuid():
            raise RuntimeError("restart helper source must be owned by current user")
        if metadata.st_mode & 0o022:
            raise RuntimeError("restart helper source must not be group/world writable")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, MAX_SOURCE_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise RuntimeError("restart helper source exceeds size limit")
            chunks.append(chunk)
        return source, b"".join(chunks)
    finally:
        os.close(fd)


def _write_private_snapshot(source_bytes: bytes, *, snapshot_root: Path) -> Path:
    root = snapshot_root.resolve(strict=True)
    directory = Path(tempfile.mkdtemp(prefix="ironclaude-restart-", dir=root))
    os.chmod(directory, 0o700)
    snapshot = directory / "restart-codex.py"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(snapshot, flags, 0o500)
    try:
        offset = 0
        while offset < len(source_bytes):
            offset += os.write(fd, source_bytes[offset:])
        os.fsync(fd)
        os.fchmod(fd, 0o500)
    finally:
        os.close(fd)
    return snapshot


def cleanup_snapshot(
    snapshot: Path, *, snapshot_root: Path = Path("/private/tmp")
) -> None:
    root = snapshot_root.resolve(strict=True)
    candidate = Path(os.path.abspath(snapshot))
    directory = candidate.parent
    if (
        directory.parent != root
        or not directory.name.startswith("ironclaude-restart-")
        or candidate.name != "restart-codex.py"
        or candidate.is_symlink()
    ):
        raise RuntimeError("refusing unsafe restart snapshot cleanup")
    metadata = candidate.stat(follow_symlinks=False)
    directory_metadata = directory.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != os.getuid()
    ):
        raise RuntimeError("refusing unowned restart snapshot cleanup")
    candidate.unlink()
    directory.rmdir()


def _run_command(
    runner: Callable[..., Any], argv: list[str]
) -> int:
    result = runner(
        argv,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    return int(result.returncode)


def _chatgpt_running(runner: Callable[..., Any]) -> bool:
    returncode = _run_command(runner, PROBE_ARGV)
    if returncode == 0:
        return True
    if returncode == 1:
        return False
    raise RuntimeError(f"pgrep returned status {returncode}")


def schedule_restart(
    *,
    script_path: Path | None = None,
    popen_factory=subprocess.Popen,
    snapshot_root: Path = Path("/private/tmp"),
) -> dict[str, object]:
    source, source_bytes = _read_trusted_source(
        Path(__file__) if script_path is None else Path(script_path)
    )
    digest = hashlib.sha256(source_bytes).hexdigest()
    snapshot = _write_private_snapshot(source_bytes, snapshot_root=snapshot_root)
    if sha256_file(snapshot) != digest:
        cleanup_snapshot(snapshot, snapshot_root=snapshot_root)
        raise RuntimeError("restart helper snapshot verification failed")
    argv = [
        sys.executable,
        str(snapshot),
        "--perform",
        "--expected-sha256",
        digest,
    ]
    try:
        child = popen_factory(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            close_fds=True,
            start_new_session=True,
        )
    except BaseException:
        cleanup_snapshot(snapshot, snapshot_root=snapshot_root)
        raise
    return {
        "pid": int(child.pid),
        "sha256": digest,
        "source": str(source),
        "snapshot": str(snapshot),
    }


def perform_restart(
    expected_sha256: str,
    *,
    script_path: Path | None = None,
    runner=subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    poll_attempts: int = POLL_ATTEMPTS,
    log_path: Path = LOG_PATH,
) -> int:
    source = (Path(__file__) if script_path is None else Path(script_path)).resolve()
    if not _SHA256_RE.fullmatch(expected_sha256):
        _record("digest mismatch: malformed expected sha256", log_path=log_path)
        return EXIT_DIGEST_MISMATCH
    try:
        if sha256_file(source) != expected_sha256:
            _record("digest mismatch: source differs from scheduled bytes", log_path=log_path)
            return EXIT_DIGEST_MISMATCH

        sleeper(START_DELAY_SECONDS)

        # Close the schedule-to-mutation race immediately before force termination.
        if sha256_file(source) != expected_sha256:
            _record("pre-quit digest mismatch: restart refused", log_path=log_path)
            return EXIT_DIGEST_MISMATCH

        _record("restart requested", log_path=log_path)
        terminate_returncode = _run_command(runner, QUIT_ARGV)
        if terminate_returncode not in {0, 1}:
            raise RuntimeError(
                f"pkill returned status {terminate_returncode}"
            )

        for attempt in range(poll_attempts):
            if not _chatgpt_running(runner):
                break
            if attempt + 1 < poll_attempts:
                sleeper(POLL_INTERVAL_SECONDS)
        else:
            _record("quit timeout", log_path=log_path)
            return EXIT_QUIT_TIMEOUT
        _record("quit complete", log_path=log_path)

        if _run_command(runner, LAUNCH_ARGV) != 0:
            raise RuntimeError("open launch returned nonzero")

        for attempt in range(poll_attempts):
            if _chatgpt_running(runner):
                _record("relaunch complete", log_path=log_path)
                return 0
            if attempt + 1 < poll_attempts:
                sleeper(POLL_INTERVAL_SECONDS)
        _record("relaunch timeout", log_path=log_path)
        return EXIT_RELAUNCH_TIMEOUT
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        _record(f"command failure: {exc}", log_path=log_path)
        return EXIT_COMMAND_FAILURE


def _dry_run(script_path: Path) -> dict[str, object]:
    source = script_path.resolve(strict=True)
    return {
        "mode": "dry-run",
        "script": str(source),
        "sha256": sha256_file(source),
        "quit_argv": QUIT_ARGV,
        "probe_argv": PROBE_ARGV,
        "launch_argv": LAUNCH_ARGV,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--schedule", action="store_true")
    modes.add_argument("--perform", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--expected-sha256", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source = Path(__file__)
    try:
        if args.dry_run:
            print(json.dumps(_dry_run(source), sort_keys=True))
            return 0
        if args.schedule:
            print(json.dumps(schedule_restart(script_path=source), sort_keys=True))
            return 0
        if args.expected_sha256 is None:
            print("restart-codex: --perform requires expected digest", file=sys.stderr)
            return EXIT_DIGEST_MISMATCH
        try:
            return perform_restart(args.expected_sha256, script_path=source)
        finally:
            if source.parent.name.startswith("ironclaude-restart-"):
                try:
                    cleanup_snapshot(source)
                except (OSError, RuntimeError) as exc:
                    _record(f"snapshot cleanup failure: {exc}")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"restart-codex: {str(exc)[:MAX_LOG_LINE_CHARS]}", file=sys.stderr)
        return EXIT_COMMAND_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
