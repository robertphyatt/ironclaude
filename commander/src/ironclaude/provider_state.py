"""Persistent routing state stored in Commander's existing SQLite database."""
from __future__ import annotations

import sqlite3


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

    def set_current_client(self, role: str, client: str) -> None:
        if client not in ("claude", "codex"):
            raise ValueError(f"unsupported client: {client}")
        with self._conn:
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
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO provider_capability_state
                    (host, client, role, tier, configured, supported, installed,
                     authenticated, available, category, reason, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(host, client, role, tier) DO UPDATE SET
                    configured=excluded.configured,
                    supported=excluded.supported,
                    installed=excluded.installed,
                    authenticated=excluded.authenticated,
                    available=CASE WHEN excluded.available=0
                        THEN 0 ELSE provider_capability_state.available END,
                    category=CASE WHEN excluded.available=0
                        THEN excluded.category ELSE provider_capability_state.category END,
                    reason=CASE WHEN excluded.available=0
                        THEN excluded.reason ELSE provider_capability_state.reason END,
                    observed_at=datetime('now'),
                    updated_at=datetime('now')
                """,
                (
                    host, client, role, tier, int(configured), int(supported),
                    int(installed), auth_value, int(available),
                    None if available else "probe_failure", reason,
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
