"""Availability flag for the Fable model tier.

Tracks a time-bounded "Fable is unavailable" flag on disk so callers across
process boundaries (daemon, workers, advisor) can agree to fall back to
Opus without a live RPC. Every read path is fail-safe: any error (missing
file, permission denied, corrupt JSON, missing key) is treated as "Fable is
available" so a bad state file can never spuriously suppress Fable or take
down the daemon.

``mark_fable_unavailable`` and ``clear_fable_unavailable`` return a
``Literal`` outcome rather than a bare bool so callers can distinguish
"nothing changed because it was already in that state" (dedup — no alert)
from "the on-disk write itself failed" (Fable is still down and the
operator must be told regardless — alert anyway). The read-check-write in
``mark_fable_unavailable`` is additionally serialized across processes with
a short, fail-safe ``fcntl.flock`` so two daemons racing to detect the same
Fable outage don't both observe "not yet flagged" and both fire a
duplicate alert.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

logger = logging.getLogger("ironclaude.fable_availability")

_STATE_PATH = Path(
    os.environ.get(
        "IRONCLAUDE_FABLE_STATE_PATH",
        str(Path.home() / ".ironclaude" / "state" / "fable_unavailable.json"),
    )
)
_UNAVAILABLE_TTL_SECONDS = 86400  # 24h


@contextlib.contextmanager
def _acquire_lock(timeout_seconds: float = 0.2):
    """Best-effort cross-process lock guarding the mark_ read-check-write.

    Non-blocking flock retried every 10ms up to ``timeout_seconds``. If the
    lock can't be acquired in time, log at debug and proceed unlocked —
    this must never block the daemon, only reduce the odds of a race.
    """
    lock_path = _STATE_PATH.parent / ".mark.lock"
    fh = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")
        deadline = time.time() + timeout_seconds
        acquired = False
        while time.time() < deadline:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.01)
        if not acquired:
            logger.debug("_acquire_lock: timed out after %.3fs, proceeding unlocked", timeout_seconds)
    except Exception as exc:
        logger.debug("_acquire_lock: failed to set up lock, proceeding unlocked: %s", exc)

    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            fh.close()


def is_fable_unavailable() -> bool:
    """Return True if the on-disk flag says Fable is currently unavailable.

    Fail-safe: any error while reading/parsing is treated as "available"
    (returns False) and logged at debug level.
    """
    try:
        payload = json.loads(_STATE_PATH.read_text())
        return bool(payload["unavailable_until"] > time.time())
    except Exception as exc:
        logger.debug("is_fable_unavailable: treating as available after error: %s", exc)
        return False


def mark_fable_unavailable(reason: str = "") -> Literal["transition", "already_flagged", "write_failed"]:
    """Mark Fable unavailable for _UNAVAILABLE_TTL_SECONDS.

    Returns:
        "transition" — state moved from available to unavailable and the
            write succeeded; callers should alert (once).
        "already_flagged" — Fable was already unavailable at entry; no write
            attempted; this is dedup, not a failure — callers must not alert.
        "write_failed" — Fable was not yet flagged, but the on-disk write
            raised; Fable is still down so callers must alert anyway even
            though the persisted flag didn't take.

    The read-check-write is serialized across processes with a short,
    fail-safe flock (see _acquire_lock) so two callers racing to detect the
    same outage don't both observe "not yet flagged" and both alert. Writes
    are atomic (write to a .tmp sibling, then os.replace).
    """
    with _acquire_lock():
        was_unavailable = is_fable_unavailable()
        if was_unavailable:
            return "already_flagged"
        now = time.time()
        payload = {
            "unavailable_until": now + _UNAVAILABLE_TTL_SECONDS,
            "reason": reason,
            "marked_at": now,
        }
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _STATE_PATH.with_suffix(_STATE_PATH.suffix + ".tmp")
        try:
            try:
                tmp_path.write_text(json.dumps(payload))
                os.replace(tmp_path, _STATE_PATH)
            except Exception as exc:
                logger.warning("mark_fable_unavailable: write failed: %s", exc)
                return "write_failed"
        finally:
            # If os.replace succeeded, tmp_path is already gone (rename moved it).
            # If write_text or os.replace failed, tmp_path may still exist — clean up.
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass  # cleanup best-effort; don't mask the primary result
        return "transition"


def clear_fable_unavailable() -> Literal["removed", "not_present", "remove_failed"]:
    """Remove the on-disk flag.

    Returns:
        "removed" — a file existed and was removed.
        "not_present" — no file existed; nothing to do.
        "remove_failed" — a file existed but removal raised.
    """
    try:
        existed = _STATE_PATH.exists()
        if not existed:
            return "not_present"
        _STATE_PATH.unlink()
        return "removed"
    except Exception as exc:
        logger.warning("clear_fable_unavailable: remove failed: %s", exc)
        return "remove_failed"


def resolve_worker_type(worker_type: str) -> str:
    """Redirect claude-fable to claude-opus when Fable is unavailable. Never raises."""
    try:
        if worker_type == "claude-fable" and is_fable_unavailable():
            return "claude-opus"
    except Exception as exc:
        logger.debug("resolve_worker_type: passthrough after error: %s", exc)
    return worker_type


def resolve_advisor_model(model):
    """Redirect the 'fable' advisor model to 'opus' when Fable is unavailable."""
    try:
        if model == "fable" and is_fable_unavailable():
            return "opus"
    except Exception as exc:
        logger.debug("resolve_advisor_model: passthrough after error: %s", exc)
    return model
