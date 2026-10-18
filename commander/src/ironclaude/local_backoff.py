"""Local-box backoff (design 2026-10-17 v120-completeness R4c).

After a box failure (connect failure, HTTP error, empty answer, invalid JSON, a read timeout at the
box's own configured read timeout, or any other local infrastructure_error) the box is skipped for
``local_backoff_minutes`` (default 15, 0 off) and the Haiku-class fast model answers instead. The
hooks count their own budget timeout (hook_validation_budget_seconds) as a box failure. The only
exemption is a Commander read timeout under a per-call read_timeout shorter than the box's own read
timeout: it writes no marker and clears none (R4c amendment; LocalGrader.grade decides). The next
call after expiry tries the box again: a success clears the marker and a failure renews it.

One marker, shared with worker/hooks/plan-validator.sh: ``local-box-backoff.json`` holding
``{"endpoint", "until"}`` (epoch seconds) in ``$IRONCLAUDE_LOCAL_BACKOFF_DIR`` (default
``~/.ironclaude/state/local-backoff``). The directory must be a real 0700 directory owned by this
user, otherwise the marker is neither trusted nor written (the same check as the Brain gate root).
"""
from __future__ import annotations

import json
import logging
import math
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Mapping

from ironclaude import paths

logger = logging.getLogger(__name__)

DEFAULT_MINUTES = 15
MARKER_NAME = "local-box-backoff.json"
_DEFAULT_OLLAMA_URL = "http://localhost:11434"


def backoff_dir() -> Path:
    override = os.environ.get("IRONCLAUDE_LOCAL_BACKOFF_DIR")
    if override:
        return Path(override.rstrip("/") or "/")
    return paths.home() / ".ironclaude" / "state" / "local-backoff"


def dir_problem(directory: Path) -> str | None:
    """Why the marker directory is unsafe, or None (mirrors the hooks' gate_root_problem)."""
    if directory.is_symlink():
        return "is a symlink"
    if not directory.is_dir():
        return "is not a directory"
    info = directory.stat()
    if info.st_uid != os.getuid():
        return "is not owned by the current user"
    if stat.S_IMODE(info.st_mode) & 0o077:
        return "has group or other permission bits"
    return None


def backoff_minutes(cfg: Mapping, backend: str) -> int:
    """``.<backend>.local_backoff_minutes // .local_backoff_minutes // 15`` like the hook's
    _resolve_local_backoff_minutes; a non-number or a negative value means the default."""
    value = None
    block = cfg.get(backend) if isinstance(cfg, Mapping) else None
    for source in (block, cfg):
        candidate = source.get("local_backoff_minutes") if isinstance(source, Mapping) else None
        if candidate is not None and candidate is not False:
            value = candidate
            break
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return DEFAULT_MINUTES
    return int(math.floor(value))


def endpoint_for(backend: str, block: Mapping) -> str | None:
    """The configured box URL (trailing slash dropped), the marker's key; None without a box."""
    if backend == "ollama":
        url = (block or {}).get("url") or _DEFAULT_OLLAMA_URL
    elif backend == "openai":
        url = (block or {}).get("base_url")
    else:
        return None
    return url.rstrip("/") if isinstance(url, str) and url else None


def _read_marker(directory: Path) -> dict | None:
    try:
        data = json.loads((directory / MARKER_NAME).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_backed_off(endpoint: str | None, minutes: int, now: float | None = None) -> bool:
    if not endpoint or minutes <= 0:
        return False
    directory = backoff_dir()
    if dir_problem(directory):
        return False
    data = _read_marker(directory)
    until = data.get("until") if data else None
    if data is None or data.get("endpoint") != endpoint:
        return False
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        return False
    return until > (time.time() if now is None else now)


def record_failure(endpoint: str | None, minutes: int, now: float | None = None) -> None:
    if not endpoint or minutes <= 0:
        return
    directory = backoff_dir()
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
    except OSError as exc:
        logger.warning("Local-box backoff directory %s could not be created: %s", directory, exc)
        return
    problem = dir_problem(directory)
    if problem:
        logger.warning("Local-box backoff directory %s %s; marker not written", directory, problem)
        return
    until = int((time.time() if now is None else now) + minutes * 60)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".marker.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"endpoint": endpoint, "until": until}, handle)
        os.replace(tmp, directory / MARKER_NAME)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_success(endpoint: str | None) -> None:
    if not endpoint:
        return
    directory = backoff_dir()
    if dir_problem(directory):
        return
    data = _read_marker(directory)
    if data is not None and data.get("endpoint") == endpoint:
        try:
            (directory / MARKER_NAME).unlink()
        except FileNotFoundError:
            pass
