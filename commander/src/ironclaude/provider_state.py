"""Persistent routing state stored in Commander's existing SQLite database."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

# Probe failures that self-heal. A later successful observation clears these, and the
# local probe guard re-runs for them. Everything else (not_configured, unsupported,
# missing_executable, unsupported_auth_mode, executable_error) stays sticky and needs
# explicit operator action via `/provider <role> <client>`.
# Tuple, not set: expanded positionally into SQL bind parameters, so order matters.
TRANSIENT_UNAVAILABLE_REASONS = (
    "probe_timeout",
    "not_authenticated",
    "executable_probe_failed",
)
USAGE_QUARANTINE_CATEGORIES = ("usage", "quota", "rate_limit")
WORKER_USAGE_ACCOUNT_TIER = "__worker_usage_account__"
# H9: a usage quarantine without a native reset time expires after this default.
DEFAULT_USAGE_QUARANTINE_SECONDS = 18000

MAX_BRAIN_CAPABILITY_PROBE_BACKOFF = 900.0


def _normalized_timestamp(value: str | None) -> str | None:
    """Return a canonical UTC timestamp only for explicit timezone-aware evidence."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("native usage event timestamp must be timezone-aware")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("native usage event timestamp must be timezone-aware") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("native usage event timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


class ProviderState:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_current_client(self, role: str) -> str | None:
        row = self._conn.execute(
            "SELECT current_client FROM provider_role_state WHERE role=?",
            (role,),
        ).fetchone()
        return row[0] if row else None

    def current_client(self, role: str, default: str) -> str:
        return self.get_current_client(role) or default

    def set_current_client(
        self,
        role: str,
        client: str,
        reset_capabilities: bool = False,
    ) -> None:
        if client not in ("claude", "codex"):
            raise ValueError(f"unsupported client: {client}")
        with self._conn:
            if reset_capabilities:
                self._conn.execute(
                    """
                    DELETE FROM provider_capability_state
                    WHERE client=? AND role=?
                    """,
                    (client, role),
                )
            self._conn.execute(
                """
                INSERT INTO provider_role_state (role, current_client)
                VALUES (?, ?)
                ON CONFLICT(role) DO UPDATE SET
                    current_client=excluded.current_client,
                    updated_at=datetime('now')
                """,
                (role, client),
            )

    def unavailable_capabilities(
        self, client: str, role: str
    ) -> list[dict[str, str]]:
        rows = self._conn.execute(
            """
            SELECT host, tier, category, reason
            FROM provider_capability_state
            WHERE client=? AND role=? AND available=0
            ORDER BY host, tier
            """,
            (client, role),
        ).fetchall()
        return [
            {
                "host": row[0],
                "tier": row[1],
                "category": row[2],
                "reason": row[3],
            }
            for row in rows
        ]

    def is_available(self, host: str, client: str, role: str, tier: str, *,
                     now: float | None = None) -> bool:
        current = time.time() if now is None else now
        row = self._conn.execute(
            """
            SELECT available, category, usage_expires_at, updated_at FROM provider_capability_state
            WHERE host=? AND client=? AND role=? AND tier=?
            """,
            (host, client, role, tier),
        ).fetchone()
        if row is not None and not bool(row[0]) and not self._usage_expired(row[1], row[2], row[3], current):
            return False
        if role != "worker" or tier == WORKER_USAGE_ACCOUNT_TIER:
            return True
        account_row = self._conn.execute(
            """SELECT available, category, usage_expires_at, updated_at FROM provider_capability_state
               WHERE host=? AND client=? AND role=? AND tier=?
                 AND category IN ('usage', 'quota', 'rate_limit')""",
            (host, client, role, WORKER_USAGE_ACCOUNT_TIER),
        ).fetchone()
        return (account_row is None or bool(account_row[0])
                or self._usage_expired(account_row[1], account_row[2], account_row[3], current))

    @staticmethod
    def _usage_expired(category, expires_at, updated_at, now: float) -> bool:
        """An expired usage quarantine only re-enables trying the client (H9)."""
        if category not in USAGE_QUARANTINE_CATEGORIES:
            return False
        if expires_at is not None:
            return float(expires_at) <= now
        try:
            updated = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return updated.timestamp() + DEFAULT_USAGE_QUARANTINE_SECONDS <= now

    @staticmethod
    def worker_usage_scope_tier(role: str, tier: str) -> str:
        """Accepted account-wide native worker quota evidence spans model tiers."""
        return WORKER_USAGE_ACCOUNT_TIER if role == "worker" else tier

    def record_capability(
        self, host, client, role, tier, *, configured, supported,
        installed, authenticated, reason, available,
    ) -> None:
        auth_value = None if authenticated is None else int(authenticated)
        transient = ",".join("?" for _ in TRANSIENT_UNAVAILABLE_REASONS)
        with self._conn:
            self._conn.execute(
                f"""
                INSERT INTO provider_capability_state
                    (host, client, role, tier, configured, supported, installed,
                     authenticated, available, category, reason, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(host, client, role, tier) DO UPDATE SET
                    configured=excluded.configured,
                    supported=excluded.supported,
                    installed=excluded.installed,
                    authenticated=excluded.authenticated,
                    available=CASE
                        WHEN provider_capability_state.category IN ('usage', 'quota', 'rate_limit') THEN 0
                        WHEN excluded.available=0 THEN 0
                        WHEN provider_capability_state.reason IN ({transient})
                            THEN 1
                        ELSE provider_capability_state.available END,
                    category=CASE
                        WHEN provider_capability_state.category IN ('usage', 'quota', 'rate_limit') THEN provider_capability_state.category
                        WHEN excluded.available=0 THEN excluded.category
                        WHEN provider_capability_state.reason IN ({transient})
                            THEN NULL
                        ELSE provider_capability_state.category END,
                    reason=CASE
                        WHEN provider_capability_state.category IN ('usage', 'quota', 'rate_limit') THEN provider_capability_state.reason
                        WHEN excluded.available=0 THEN excluded.reason
                        WHEN provider_capability_state.reason IN ({transient})
                            THEN NULL
                        ELSE provider_capability_state.reason END,
                    observed_at=datetime('now'),
                    updated_at=datetime('now')
                """,
                (
                    host, client, role, tier, int(configured), int(supported),
                    int(installed), auth_value, int(available),
                    None if available else "probe_failure", reason,
                    *TRANSIENT_UNAVAILABLE_REASONS,
                    *TRANSIENT_UNAVAILABLE_REASONS,
                    *TRANSIENT_UNAVAILABLE_REASONS,
                ),
            )

    def capability_observation(self, host, client, role, tier) -> dict | None:
        row = self._conn.execute(
            """SELECT configured, supported, installed, authenticated, available, reason
               FROM provider_capability_state
               WHERE host=? AND client=? AND role=? AND tier=?""",
            (host, client, role, tier),
        ).fetchone()
        if row is None:
            return None
        return {
            "configured": bool(row[0]),
            "supported": bool(row[1]),
            "installed": bool(row[2]),
            "authenticated": None if row[3] is None else bool(row[3]),
            "available": bool(row[4]),
            "reason": row[5],
        }

    def mark_unavailable(
        self,
        host: str,
        client: str,
        role: str,
        tier: str,
        category: str,
        reason: str,
        *,
        usage_generation: str | None = None,
        usage_cursor: int | None = None,
        usage_event_key: str | None = None,
        usage_event_timestamp: str | None = None,
        usage_expires_at: float | None = None,
    ) -> None:
        usage_event_timestamp = _normalized_timestamp(usage_event_timestamp)
        expires_at = None
        if category in USAGE_QUARANTINE_CATEGORIES:
            expires_at = (float(usage_expires_at) if usage_expires_at is not None
                          else time.time() + DEFAULT_USAGE_QUARANTINE_SECONDS)
        if category in USAGE_QUARANTINE_CATEGORIES and usage_generation is not None:
            if not usage_generation or usage_cursor is None or usage_cursor < 0 or not usage_event_key:
                raise ValueError("native usage evidence requires generation, cursor, and event key")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO provider_capability_state
                    (host, client, role, tier, available, category, reason,
                     usage_generation, usage_cursor, usage_event_key, usage_event_timestamp,
                     usage_expires_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host, client, role, tier) DO UPDATE SET
                    available=CASE WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                        AND excluded.usage_generation IS NOT NULL
                                        AND provider_capability_state.usage_event_timestamp IS NOT NULL
                                        AND (excluded.usage_event_timestamp IS NULL
                                             OR excluded.usage_event_timestamp <= provider_capability_state.usage_event_timestamp)
                                   THEN provider_capability_state.available ELSE 0 END,
                    category=CASE WHEN provider_capability_state.category IN ('usage', 'quota', 'rate_limit')
                                  AND excluded.category NOT IN ('usage', 'quota', 'rate_limit')
                                  THEN provider_capability_state.category
                                  WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                       AND excluded.usage_generation IS NOT NULL
                                       AND provider_capability_state.usage_event_timestamp IS NOT NULL
                                       AND (excluded.usage_event_timestamp IS NULL
                                            OR excluded.usage_event_timestamp <= provider_capability_state.usage_event_timestamp)
                                  THEN provider_capability_state.category
                                  ELSE excluded.category END,
                    reason=CASE WHEN provider_capability_state.category IN ('usage', 'quota', 'rate_limit')
                                AND excluded.category NOT IN ('usage', 'quota', 'rate_limit')
                                THEN provider_capability_state.reason
                                WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                     AND excluded.usage_generation IS NOT NULL
                                     AND provider_capability_state.usage_event_timestamp IS NOT NULL
                                     AND (excluded.usage_event_timestamp IS NULL
                                          OR excluded.usage_event_timestamp <= provider_capability_state.usage_event_timestamp)
                                THEN provider_capability_state.reason
                                ELSE excluded.reason END,
                    usage_generation=CASE WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                                AND excluded.usage_generation IS NOT NULL
                                                AND (provider_capability_state.usage_event_timestamp IS NULL
                                                     OR (excluded.usage_event_timestamp IS NOT NULL
                                                         AND excluded.usage_event_timestamp > provider_capability_state.usage_event_timestamp))
                                          THEN excluded.usage_generation ELSE provider_capability_state.usage_generation END,
                    usage_cursor=CASE WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                            AND excluded.usage_generation IS NOT NULL
                                            AND (provider_capability_state.usage_event_timestamp IS NULL
                                                 OR (excluded.usage_event_timestamp IS NOT NULL
                                                     AND excluded.usage_event_timestamp > provider_capability_state.usage_event_timestamp))
                                      THEN excluded.usage_cursor ELSE provider_capability_state.usage_cursor END,
                    usage_event_key=CASE WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                               AND excluded.usage_generation IS NOT NULL
                                               AND (provider_capability_state.usage_event_timestamp IS NULL
                                                    OR (excluded.usage_event_timestamp IS NOT NULL
                                                        AND excluded.usage_event_timestamp > provider_capability_state.usage_event_timestamp))
                                         THEN excluded.usage_event_key ELSE provider_capability_state.usage_event_key END,
                    usage_event_timestamp=CASE WHEN excluded.category IN ('usage', 'quota', 'rate_limit')
                                                     AND excluded.usage_generation IS NOT NULL
                                                     AND (provider_capability_state.usage_event_timestamp IS NULL
                                                          OR (excluded.usage_event_timestamp IS NOT NULL
                                                              AND excluded.usage_event_timestamp > provider_capability_state.usage_event_timestamp))
                                               THEN excluded.usage_event_timestamp ELSE provider_capability_state.usage_event_timestamp END,
                    usage_expires_at=CASE WHEN excluded.category NOT IN ('usage', 'quota', 'rate_limit')
                                          THEN provider_capability_state.usage_expires_at
                                          WHEN excluded.usage_generation IS NOT NULL
                                               AND provider_capability_state.usage_event_timestamp IS NOT NULL
                                               AND (excluded.usage_event_timestamp IS NULL
                                                    OR excluded.usage_event_timestamp <= provider_capability_state.usage_event_timestamp)
                                          THEN provider_capability_state.usage_expires_at
                                          ELSE excluded.usage_expires_at END,
                    updated_at=datetime('now')
                """,
                (host, client, role, tier, category, reason, usage_generation, usage_cursor,
                 usage_event_key, usage_event_timestamp, expires_at),
            )

    def recover_usage_quarantine(
        self, host: str, client: str, role: str, tier: str, *,
        operator_authorized: bool = False,
        native_generation: str | None = None,
        native_cursor: int | None = None,
        native_event_key: str | None = None,
        native_state: str | None = None,
        native_event_timestamp: str | None = None,
    ) -> bool:
        """Clear usage hold only on explicit operator authority or newer native recovery."""
        native_event_timestamp = _normalized_timestamp(native_event_timestamp)
        if not operator_authorized and (not native_generation or native_cursor is None or not native_event_key or native_state != "available"):
            raise ValueError("newer native availability or explicit operator recovery required")
        with self._conn:
            cursor = self._conn.execute(
                """UPDATE provider_capability_state
                       SET available=1, category=NULL, reason=NULL,
                       usage_generation=NULL, usage_cursor=NULL, usage_event_key=NULL,
                       usage_expires_at=NULL,
                       usage_event_timestamp=CASE WHEN ? IS NOT NULL
                                                        AND (usage_event_timestamp IS NULL
                                                             OR ? > usage_event_timestamp)
                                                    THEN ? ELSE usage_event_timestamp END,
                       updated_at=datetime('now')
                   WHERE host=? AND client=? AND role=? AND tier=?
                     AND category IN ('usage', 'quota', 'rate_limit')
                     AND (? OR (usage_generation=? AND usage_cursor IS NOT NULL
                                AND ? > usage_cursor AND ? != usage_event_key
                                AND (tier != ? OR ? IS NOT NULL)
                                AND (? IS NULL OR usage_event_timestamp IS NULL
                                     OR ? >= usage_event_timestamp))
                              OR (usage_generation != ? AND usage_event_timestamp IS NOT NULL
                                  AND ? IS NOT NULL AND ? > usage_event_timestamp))""",
                (native_event_timestamp, native_event_timestamp, native_event_timestamp,
                 host, client, role, tier, int(operator_authorized), native_generation,
                 native_cursor, native_event_key, WORKER_USAGE_ACCOUNT_TIER,
                 native_event_timestamp, native_event_timestamp,
                 native_event_timestamp, native_generation,
                 native_event_timestamp, native_event_timestamp),
            )
            if native_state == "available" and native_event_timestamp is not None:
                self._conn.execute(
                    """INSERT INTO provider_capability_state
                           (host, client, role, tier, available, usage_event_timestamp)
                       VALUES (?, ?, ?, ?, 1, ?)
                       ON CONFLICT(host, client, role, tier) DO UPDATE SET
                           usage_event_timestamp=CASE
                               WHEN provider_capability_state.usage_event_timestamp IS NULL
                                    OR excluded.usage_event_timestamp > provider_capability_state.usage_event_timestamp
                               THEN excluded.usage_event_timestamp
                               ELSE provider_capability_state.usage_event_timestamp END,
                           updated_at=datetime('now')""",
                    (host, client, role, tier, native_event_timestamp),
                )
            return cursor.rowcount == 1

    def mark_available(self, host: str, client: str, role: str, tier: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE provider_capability_state
                SET available=1, category=NULL, reason=NULL, updated_at=datetime('now')
                WHERE host=? AND client=? AND role=? AND tier=?
                  AND (category IS NULL OR category NOT IN ('usage', 'quota', 'rate_limit'))
                """,
                (host, client, role, tier),
            )

    def unavailable_reason(
        self, host: str, client: str, role: str, tier: str
    ) -> dict[str, str] | None:
        row = self._conn.execute(
            """
            SELECT category, reason FROM provider_capability_state
            WHERE host=? AND client=? AND role=? AND tier=? AND available=0
            """,
            (host, client, role, tier),
        ).fetchone()
        if row:
            return {"category": row[0], "reason": row[1]}
        if role != "worker" or tier == WORKER_USAGE_ACCOUNT_TIER:
            return None
        account_row = self._conn.execute(
            """SELECT category, reason FROM provider_capability_state
               WHERE host=? AND client=? AND role=? AND tier=? AND available=0
                 AND category IN ('usage', 'quota', 'rate_limit')""",
            (host, client, role, WORKER_USAGE_ACCOUNT_TIER),
        ).fetchone()
        return None if not account_row else {"category": account_row[0], "reason": account_row[1]}

    def record_brain_capability_block(
        self,
        *,
        tier: str,
        category: str,
        reason: str,
        fingerprint: str,
        now: float,
        initial_backoff: float,
    ) -> dict:
        """Persist one sticky Codex-Brain capability incident."""
        existing = self._conn.execute(
            """
            SELECT capability_fingerprint, notification_state, probe_backoff_seconds
            FROM provider_capability_state
            WHERE host='local' AND client='codex' AND role='brain' AND tier=?
            """,
            (tier,),
        ).fetchone()
        same_incident = existing is not None and existing[0] == fingerprint
        notification_state = existing[1] if same_incident else "pending"
        previous_backoff = existing[2] if same_incident else None
        computed_backoff = (
            max(float(initial_backoff), float(previous_backoff) * 2.0)
            if previous_backoff is not None
            else float(initial_backoff)
        )
        backoff = min(MAX_BRAIN_CAPABILITY_PROBE_BACKOFF, computed_backoff)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO provider_capability_state
                    (host, client, role, tier, configured, supported, installed,
                     available, category, reason, capability_fingerprint,
                     notification_state, last_probe_at, next_probe_at,
                     probe_backoff_seconds, observed_at)
                VALUES ('local', 'codex', 'brain', ?, 1, 1, 0, 0, ?, ?, ?, ?, ?, ?, ?,
                        datetime('now'))
                ON CONFLICT(host, client, role, tier) DO UPDATE SET
                    available=0,
                    category=excluded.category,
                    reason=excluded.reason,
                    capability_fingerprint=excluded.capability_fingerprint,
                    notification_state=excluded.notification_state,
                    last_probe_at=excluded.last_probe_at,
                    next_probe_at=excluded.next_probe_at,
                    probe_backoff_seconds=excluded.probe_backoff_seconds,
                    observed_at=datetime('now'),
                    updated_at=datetime('now')
                """,
                (
                    tier,
                    category,
                    reason,
                    fingerprint,
                    notification_state,
                    float(now),
                    float(now) + backoff,
                    backoff,
                ),
            )
        row = self._conn.execute(
            """
            SELECT capability_fingerprint, notification_state, last_probe_at,
                   next_probe_at, probe_backoff_seconds
            FROM provider_capability_state
            WHERE host='local' AND client='codex' AND role='brain' AND tier=?
            """,
            (tier,),
        ).fetchone()
        return dict(row)

    def claim_brain_capability_notification(
        self, *, tier: str, fingerprint: str
    ) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE provider_capability_state
                SET notification_state='claimed', updated_at=datetime('now')
                WHERE host='local' AND client='codex' AND role='brain' AND tier=?
                  AND capability_fingerprint=? AND notification_state='pending'
                """,
                (tier, fingerprint),
            )
        return cursor.rowcount == 1

    def brain_capability_recheck_due(self, *, tier: str, now: float) -> bool:
        row = self._conn.execute(
            """
            SELECT next_probe_at FROM provider_capability_state
            WHERE host='local' AND client='codex' AND role='brain' AND tier=?
              AND available=0 AND next_probe_at IS NOT NULL
            """,
            (tier,),
        ).fetchone()
        return row is not None and float(row[0]) <= float(now)

    def record_brain_capability_recovery(self, *, tier: str) -> None:
        """Clear a capability incident without changing sticky Brain selection."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE provider_capability_state
                SET available=1,
                    category=NULL,
                    reason=NULL,
                    capability_fingerprint=NULL,
                    notification_state='pending',
                    next_probe_at=NULL,
                    probe_backoff_seconds=NULL,
                    updated_at=datetime('now')
                WHERE host='local' AND client='codex' AND role='brain' AND tier=?
                """,
                (tier,),
            )
