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
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

logger = logging.getLogger("ironclaude.fable_availability")

_STATE_PATH = Path(
    os.environ.get(
        "IRONCLAUDE_FABLE_STATE_PATH",
        str(Path.home() / ".ironclaude" / "state" / "fable_unavailable.json"),
    )
)

# Per-category recheck windows. The recheck follows WHY Fable was blocked:
_MODEL_TTL = 86400        # 24h — Fable-specific outage; Opus is a genuine fallback.
_USAGE_REPROBE = 1800     # 30m — usage limit with no extractable reset; re-probe (never a flat 5h).
_UNKNOWN_REPROBE = 3600   # 1h  — unclassified/transient overload; downgrade but re-probe soon.
_USAGE_MAX = 6 * 3600     # hard upper bound on any usage_limit window (guards a bad parse/skew).

# Usage-limit phrases: present so the usage_limit machinery/tests work, but NOTE no live
# detector currently forwards usage-limit text to mark_fable_unavailable (deferred — needs a
# captured real usage-limit error string; the brain_client detectors only match model outages).
_USAGE_PHRASES = ("5-hour limit", "5 hour limit", "usage limit", "rate limit",
                  "too many requests", "quota")
# Strong model-outage ANCHORS — unambiguous Fable-specific outage signals, ALIGNED to the
# real detectors in brain_client.py (_is_model_unavailable / _MODEL_UNAVAILABLE_TEXT_PHRASES).
# Checked BEFORE usage phrases so a model-outage message that incidentally also mentions
# "rate limit"/"quota" still classifies model_unavailable (a real account usage-limit never
# says "selected model"). — hardening for adversarial review M1.
_MODEL_ANCHORS = ("selected model", "issue with the selected model", "may not exist",
                  "may not have access", "not have access")
# Weaker model phrases — checked AFTER usage phrases.
_MODEL_PHRASES = ("not available", "access denied", "currently unavailable", "no access")
_USAGE_CODE_RE = re.compile(r"\b429\b")
_MODEL_CODE_RE = re.compile(r"\b403\b")
_RESET_RE = re.compile(r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def _now() -> float:
    """Indirection so tests can pin the clock without freezing the flock deadline loop."""
    return time.time()


def classify_reason(reason: str) -> str:
    """Classify a block reason into a recheck category.

    Returns "usage_limit" | "model_unavailable" | "unknown". Case-insensitive;
    fail-safe to "unknown". Usage is checked before model so an account-wide
    throttle is never mistaken for a Fable-specific outage.
    """
    r = (reason or "").lower()
    if any(p in r for p in _MODEL_ANCHORS):
        return "model_unavailable"   # unambiguous outage anchor wins over any usage words
    if any(p in r for p in _USAGE_PHRASES) or _USAGE_CODE_RE.search(r):
        return "usage_limit"
    if any(p in r for p in _MODEL_PHRASES) or _MODEL_CODE_RE.search(r):
        return "model_unavailable"
    return "unknown"


def parse_reset_time(message: str, now: float) -> float | None:
    """Parse 'resets <time>' from a usage-limit message.

    Returns the epoch of the NEXT occurrence of that clock time at/after ``now``
    (local tz), or None if no reset time is present. Callers clamp the result to
    a sane window (see mark_fable_unavailable).
    """
    if not message:
        return None
    m = _RESET_RE.search(message)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if not (0 <= minute < 60):
        return None
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24):
        return None
    base = datetime.fromtimestamp(now)
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate.timestamp()


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
        return bool(payload["unavailable_until"] > _now())   # _now() so mark's clock stays consistent
    except Exception as exc:
        logger.debug("is_fable_unavailable: treating as available after error: %s", exc)
        return False


def fable_block_category() -> str | None:
    """Category of the CURRENT block, or None if Fable is available/unreadable.

    Returns "usage_limit" | "model_unavailable" | "unknown" when an active flag
    exists, else None. A flag missing the "category" key (legacy) reads as
    "unknown". Fail-safe: any read/parse error -> None (no-downgrade side).
    """
    try:
        payload = json.loads(_STATE_PATH.read_text())
        if payload["unavailable_until"] > _now():
            return payload.get("category", "unknown")
        return None
    except Exception as exc:
        logger.debug("fable_block_category: available/unreadable after error: %s", exc)
        return None


def mark_fable_unavailable(
    reason: str = "",
    *,
    retry_after_seconds: float | None = None,
    reset_at: float | None = None,
) -> Literal["transition", "already_flagged", "write_failed"]:
    """Mark Fable unavailable for a REASON-AWARE recheck window.

    The recheck window follows WHY Fable was blocked (see classify_reason):
      - usage_limit: the real reset — reset_at, else the message's parsed
        `resets <time>`, else now+retry_after_seconds, else a 30-min re-probe;
        clamped to [now+60s, now+_USAGE_MAX] so a bad parse can't over-blackout.
      - model_unavailable: now + _MODEL_TTL (24h — Opus is a genuine fallback).
      - unknown: now + _UNKNOWN_REPROBE (1h re-probe, never a stuck 24h blackout).

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
        now = _now()
        category = classify_reason(reason)
        was_unavailable = is_fable_unavailable()
        if was_unavailable:
            # Dedup — EXCEPT allow escalation off a keep-Fable `usage_limit` window to a
            # downgrade category (model_unavailable/unknown): a genuine Fable outage that
            # starts during an account-wide usage limit must be able to flip the category
            # so resolve_* stops keeping Fable. (adversarial review I3)
            if not (fable_block_category() == "usage_limit" and category != "usage_limit"):
                return "already_flagged"
        if category == "usage_limit":
            if reset_at is not None:
                until = reset_at
            else:
                parsed = parse_reset_time(reason, now)
                if parsed is not None:
                    until = parsed
                elif retry_after_seconds is not None:
                    until = now + retry_after_seconds
                else:
                    until = now + _USAGE_REPROBE
            until = max(now + 60, min(until, now + _USAGE_MAX))  # clamp: never past ~6h, never <now
        elif category == "model_unavailable":
            until = now + _MODEL_TTL
        else:
            until = now + _UNKNOWN_REPROBE
        payload = {
            "unavailable_until": until,
            "category": category,
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

    Fail-open: if the file exists but cannot be unlinked (a transient disk or
    permission error — both observed in production), fall back to truncating it
    to empty. The fail-safe read path (is_fable_unavailable / fable_block_category)
    treats an empty/corrupt flag as "Fable is AVAILABLE", and truncation allocates
    no blocks, so it survives an ENOSPC that defeats unlink/atomic-replace. A flag
    we cannot delete must never pin Fable down until its window expires.

    Returns:
        "removed" — the flag was removed, or (fallback) neutralized to empty.
        "not_present" — no file existed; nothing to do.
        "remove_failed" — a file existed and neither unlink nor truncate worked.
    """
    try:
        if not _STATE_PATH.exists():
            return "not_present"
        _STATE_PATH.unlink()
        return "removed"
    except Exception as exc:
        logger.warning("clear_fable_unavailable: unlink failed (%s) — neutralizing in place", exc)
        try:
            with _STATE_PATH.open("w"):
                pass  # truncate-to-empty: read path treats empty as available
            return "removed"
        except Exception as exc2:
            logger.error("clear_fable_unavailable: could not remove or neutralize flag: %s", exc2)
            return "remove_failed"


def resolve_worker_type(worker_type: str) -> str:
    """Redirect claude-fable to claude-opus when Fable is unavailable — UNLESS the
    block is an account-wide usage_limit, in which case Opus is equally throttled so
    a possibly-reopening Fable is kept. Never raises.
    """
    try:
        if worker_type == "claude-fable" and is_fable_unavailable():
            if fable_block_category() == "usage_limit":
                return "claude-fable"  # account-wide throttle; Opus equally limited; keep Fable
            return "claude-opus"       # model outage / unknown / unreadable-category -> downgrade
    except Exception as exc:
        logger.debug("resolve_worker_type: passthrough after error: %s", exc)
    return worker_type


def resolve_advisor_model(model):
    """Redirect the 'fable' advisor model to 'opus' when Fable is unavailable — UNLESS
    the block is an account-wide usage_limit (keep Fable; Opus is equally throttled).
    """
    try:
        if model == "fable" and is_fable_unavailable():
            if fable_block_category() == "usage_limit":
                return "fable"
            return "opus"
    except Exception as exc:
        logger.debug("resolve_advisor_model: passthrough after error: %s", exc)
    return model
