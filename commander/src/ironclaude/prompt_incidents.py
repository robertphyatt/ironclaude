"""Durable Commander prompt incidents and at-most-once dispatch generations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
import time
from typing import Literal

from ironclaude.tmux_manager import PromptSignal


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1a\x1c-\x1f\x7f]")
_TIMESTAMP_RE = re.compile(
    r"^\s*(?:\d{4}-\d{2}-\d{2}[ T])?\d{2}:\d{2}:\d{2}(?:[,.]\d+)?\s+"
)
_SLACK_TIMESTAMP_RE = re.compile(r"^[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class PromptObservation:
    action: Literal["dispatch", "hold"]
    incident_id: int
    dispatch_id: int | None
    fingerprint: str
    evidence: str


class PromptIncidentStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @staticmethod
    def _normalize_evidence(evidence: str) -> str:
        cleaned = _ANSI_RE.sub("", evidence)
        normalized = []
        for line in cleaned.splitlines():
            line = _CONTROL_RE.sub("", line)
            line = _TIMESTAMP_RE.sub("", line).rstrip()
            normalized.append(line)
        return "\n".join(normalized).strip()[-8192:]

    @classmethod
    def _fingerprint(cls, worker_id: str, signal: PromptSignal) -> tuple[str, str]:
        if not isinstance(signal, PromptSignal):
            raise TypeError("PromptIncidentStore.observe requires a validated PromptSignal")
        normalized = cls._normalize_evidence(signal.evidence)
        canonical = json.dumps(
            {
                "worker_id": worker_id,
                "kind": signal.kind,
                "question": signal.question,
                "options": signal.options,
                "authority_text": signal.authority_text,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), normalized

    def _create_dispatch(
        self,
        incident_id: int,
        *,
        reason: str,
        identity: str,
        now: float,
    ) -> int:
        generation = self._conn.execute(
            "SELECT COALESCE(MAX(generation), 0) + 1 "
            "FROM worker_prompt_dispatches WHERE incident_id=?",
            (incident_id,),
        ).fetchone()[0]
        cursor = self._conn.execute(
            """
            INSERT INTO worker_prompt_dispatches
                (incident_id, generation, reason, identity, state, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (incident_id, generation, reason, identity, float(now)),
        )
        return int(cursor.lastrowid)

    def observe(
        self, worker_id: str, stage: str, signal: PromptSignal, *, now: float
    ) -> PromptObservation:
        fingerprint, normalized = self._fingerprint(worker_id, signal)
        with self._conn:
            active = self._conn.execute(
                """
                SELECT id, fingerprint, evidence FROM worker_prompt_incidents
                WHERE worker_id=? AND status='active'
                """,
                (worker_id,),
            ).fetchone()
            if active is not None and active[1] == fingerprint:
                self._conn.execute(
                    "UPDATE worker_prompt_incidents "
                    "SET stage=?, evidence=?, last_observed_at=? WHERE id=?",
                    (stage, normalized, float(now), active[0]),
                )
                return PromptObservation(
                    "hold", int(active[0]), None, fingerprint, normalized
                )
            if active is not None:
                self._conn.execute(
                    """
                    UPDATE worker_prompt_incidents
                    SET status='superseded', resolved_at=? WHERE id=?
                    """,
                    (float(now), active[0]),
                )
            cursor = self._conn.execute(
                """
                INSERT INTO worker_prompt_incidents
                    (worker_id, stage, fingerprint, evidence, status,
                     first_observed_at, last_observed_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (worker_id, stage, fingerprint, normalized, float(now), float(now)),
            )
            incident_id = int(cursor.lastrowid)
            dispatch_id = self._create_dispatch(
                incident_id,
                reason="initial",
                identity="initial",
                now=now,
            )
        return PromptObservation(
            "dispatch", incident_id, dispatch_id, fingerprint, normalized
        )

    def claim_dispatch(
        self, dispatch_id: int | None, *, destination: str, now: float
    ) -> bool:
        if dispatch_id is None:
            return False
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE worker_prompt_dispatches
                SET state='claimed', destination=?, claimed_at=?
                WHERE id=? AND state='pending'
                """,
                (destination, float(now), dispatch_id),
            )
        return cursor.rowcount == 1

    def record_delivery(
        self,
        dispatch_id: int,
        *,
        delivered: bool,
        failure_category: str | None = None,
    ) -> None:
        state = (
            "delivered"
            if delivered
            else "held"
            if failure_category == "capability_blocked"
            else "failed"
        )
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE worker_prompt_dispatches
                SET state=?, failure_category=?, completed_at=?
                WHERE id=? AND state='claimed'
                """,
                (state, failure_category, time.time(), dispatch_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("prompt dispatch must be claimed before delivery")

    def _observation_for_dispatch(
        self, incident_id: int, dispatch_id: int
    ) -> PromptObservation:
        row = self._conn.execute(
            "SELECT fingerprint, evidence FROM worker_prompt_incidents WHERE id=?",
            (incident_id,),
        ).fetchone()
        return PromptObservation(
            "dispatch", incident_id, dispatch_id, row[0], row[1]
        )

    def rearm_capability_recovery(
        self, capability_fingerprint: str, *, now: float
    ) -> list[PromptObservation]:
        incidents = self._conn.execute(
            """
            SELECT incident.id
            FROM worker_prompt_incidents AS incident
            JOIN worker_prompt_dispatches AS dispatch
              ON dispatch.incident_id=incident.id
            WHERE incident.status='active'
              AND dispatch.generation=(
                  SELECT MAX(latest.generation)
                  FROM worker_prompt_dispatches AS latest
                  WHERE latest.incident_id=incident.id
              )
              AND dispatch.state='held'
              AND dispatch.failure_category='capability_blocked'
            ORDER BY incident.id
            """
        ).fetchall()
        observations = []
        with self._conn:
            for row in incidents:
                incident_id = int(row[0])
                try:
                    dispatch_id = self._create_dispatch(
                        incident_id,
                        reason="capability_recovery",
                        identity=capability_fingerprint,
                        now=now,
                    )
                except sqlite3.IntegrityError:
                    continue
                observations.append(
                    self._observation_for_dispatch(incident_id, dispatch_id)
                )
        return observations

    def rearm_from_operator_guidance(
        self, worker_id: str, source_ts: str, *, now: float
    ) -> PromptObservation | None:
        if not isinstance(source_ts, str) or not _SLACK_TIMESTAMP_RE.fullmatch(source_ts):
            raise ValueError("source_ts must be an exact Slack timestamp string")
        row = self._conn.execute(
            """
            SELECT id FROM worker_prompt_incidents
            WHERE worker_id=? AND status='active'
            """,
            (worker_id,),
        ).fetchone()
        if row is None:
            return None
        incident_id = int(row[0])
        try:
            with self._conn:
                dispatch_id = self._create_dispatch(
                    incident_id,
                    reason="operator_guidance",
                    identity=source_ts,
                    now=now,
                )
        except sqlite3.IntegrityError:
            pending = self._conn.execute(
                """
                SELECT id FROM worker_prompt_dispatches
                WHERE incident_id=? AND reason='operator_guidance'
                  AND identity=? AND state='pending'
                """,
                (incident_id, source_ts),
            ).fetchone()
            if pending is None:
                return None
            dispatch_id = int(pending[0])
        return self._observation_for_dispatch(incident_id, dispatch_id)

    def active_for_worker(self, worker_id: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT incident.*, dispatch.id AS dispatch_id,
                   dispatch.generation, dispatch.state AS dispatch_state,
                   dispatch.failure_category,
                   dispatch.reason AS dispatch_reason
            FROM worker_prompt_incidents AS incident
            LEFT JOIN worker_prompt_dispatches AS dispatch
              ON dispatch.incident_id=incident.id
             AND dispatch.generation=(
                 SELECT MAX(latest.generation)
                 FROM worker_prompt_dispatches AS latest
                 WHERE latest.incident_id=incident.id
             )
            WHERE incident.worker_id=? AND incident.status='active'
            """,
            (worker_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def active_incidents(self) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT incident.*, dispatch.id AS dispatch_id,
                   dispatch.generation, dispatch.state AS dispatch_state,
                   dispatch.failure_category,
                   dispatch.reason AS dispatch_reason
            FROM worker_prompt_incidents AS incident
            LEFT JOIN worker_prompt_dispatches AS dispatch
              ON dispatch.incident_id=incident.id
             AND dispatch.generation=(
                 SELECT MAX(latest.generation)
                 FROM worker_prompt_dispatches AS latest
                 WHERE latest.incident_id=incident.id
             )
            WHERE incident.status='active'
            ORDER BY incident.worker_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def resolve_worker(self, worker_id: str, *, now: float) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE worker_prompt_incidents
                SET status='resolved', resolved_at=?
                WHERE worker_id=? AND status='active'
                """,
                (float(now), worker_id),
            )
        return cursor.rowcount == 1
