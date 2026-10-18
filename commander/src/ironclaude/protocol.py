# src/ic/protocol.py
"""File-based communication protocol between daemon and brain."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger("ironclaude.protocol")

_SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+\Z')


def validate_safe_id(value: str) -> None:
    """Raise ValueError if value contains characters unsafe for filesystem paths."""
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"Unsafe ID rejected: {value!r}")


_counter = 0
_counter_lock = threading.Lock()


def write_decision(decisions_dir: str, decision: dict) -> str:
    """Write a decision file. Returns the file path."""
    global _counter
    Path(decisions_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    with _counter_lock:
        _counter += 1
        seq = _counter
    filename = f"{int(time.time() * 1000)}_{seq}.json"
    path = os.path.join(decisions_dir, filename)
    with open(path, "w") as f:
        json.dump(decision, f)
    return path


def read_pending_decisions(decisions_dir: str) -> list[dict]:
    """Read and delete all pending decision files. Returns list of decisions."""
    Path(decisions_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    current_uid = os.getuid()
    decisions = []
    for filename in sorted(os.listdir(decisions_dir)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(decisions_dir, filename)
        try:
            file_stat = os.stat(path)
            if file_stat.st_uid != current_uid:
                logger.warning(f"Skipping decision {filename}: not owned by current user")
                continue
            with open(path) as f:
                decisions.append(json.load(f))
            os.remove(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read decision {filename}: {e}")
    return decisions


def extract_ledger_json(body: str) -> dict:
    """Extract ledger JSON from the ## Data code fence in a wiki page body."""
    parts = body.split("## Data")
    if len(parts) < 2:
        return {"objective": None, "tasks": []}
    data_section = parts[1]
    fence_start = data_section.find("```")
    if fence_start == -1:
        return {"objective": None, "tasks": []}
    after_fence = data_section[fence_start + 3:]
    first_newline = after_fence.find("\n")
    if first_newline == -1:
        return {"objective": None, "tasks": []}
    json_content = after_fence[first_newline + 1:]
    fence_end = json_content.find("```")
    if fence_end == -1:
        return {"objective": None, "tasks": []}
    try:
        return json.loads(json_content[:fence_end].strip())
    except (json.JSONDecodeError, ValueError):
        return {"objective": None, "tasks": []}


def read_task_ledger(path: str) -> dict | None:
    """Read the task ledger. A .md path is the wiki ledger page (its ## Data JSON
    block); anything else is the legacy JSON file. Returns None if missing or
    unparseable."""
    try:
        with open(path) as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    if path.endswith(".md"):
        data = extract_ledger_json(raw)
        if not isinstance(data, dict):
            return None
        return data if (data.get("objective") or data.get("tasks")) else None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def write_worker_spec(specs_dir: str, spec: dict) -> str:
    """Write a worker spec file. Returns the file path."""
    validate_safe_id(spec["id"])
    Path(specs_dir).mkdir(parents=True, exist_ok=True)
    worker_id = spec["id"]
    path = os.path.join(specs_dir, f"{worker_id}.json")
    with open(path, "w") as f:
        json.dump(spec, f)
    return path


def read_worker_spec(specs_dir: str, worker_id: str) -> dict | None:
    """Read a worker spec file. Returns None if not found."""
    validate_safe_id(worker_id)
    path = os.path.join(specs_dir, f"{worker_id}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
