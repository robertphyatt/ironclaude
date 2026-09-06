"""Persistent routing state stored in Commander's existing SQLite database."""
from __future__ import annotations

import sqlite3

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

MAX_BRAIN_CAPABILITY_PROBE_BACKOFF = 900.0


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

    def is_available(self, host: str, client: str, role: str, tier: str) -> bool:
        row = self._conn.execute(
            """
            SELECT available FROM provider_capability_state
            WHERE host=? AND client=? AND role=? AND tier=?
            """,
            (host, client, role, tier),
        ).fetchone()
        return row is None or bool(row[0])

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
                        WHEN excluded.available=0 THEN 0
                        WHEN provider_capability_state.reason IN ({transient})
                            THEN 1
                        ELSE provider_capability_state.available END,
                    category=CASE
                        WHEN excluded.available=0 THEN excluded.category
                        WHEN provider_capability_state.reason IN ({transient})
                            THEN NULL
                        ELSE provider_capability_state.category END,
                    reason=CASE
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
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO provider_capability_state
                    (host, client, role, tier, available, category, reason)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(host, client, role, tier) DO UPDATE SET
                    available=0,
                    category=excluded.category,
                    reason=excluded.reason,
                    updated_at=datetime('now')
                """,
                (host, client, role, tier, category, reason),
            )

    def mark_available(self, host: str, client: str, role: str, tier: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE provider_capability_state
                SET available=1, category=NULL, reason=NULL, updated_at=datetime('now')
                WHERE host=? AND client=? AND role=? AND tier=?
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
        if not row:
            return None
        return {"category": row[0], "reason": row[1]}

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
