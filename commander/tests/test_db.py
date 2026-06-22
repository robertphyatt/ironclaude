# tests/test_db.py
import sqlite3
import pytest
from ironclaude.db import init_db


class TestInitDb:
    def test_creates_all_tables(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "brain_state" in tables
        assert "events" in tables
        assert "objectives" in tables
        assert "tasks" in tables
        assert "workers" in tables
        conn.close()

    def test_wal_mode_enabled(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_busy_timeout_set(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000
        conn.close()

    def test_idempotent(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn1 = init_db(db_path)
        tables1 = {
            r[0] for r in conn1.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        conn1.close()
        conn2 = init_db(db_path)
        tables2 = {
            r[0] for r in conn2.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        # Re-initializing must be idempotent: the same table set, no duplicates,
        # no losses. Asserting the set equality (rather than a hardcoded count)
        # keeps this robust as the schema grows.
        assert tables2 == tables1
        assert tables1, "init_db must create at least one table"
        conn2.close()

    def test_brain_state_singleton(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        row = conn.execute("SELECT * FROM brain_state WHERE id = 1").fetchone()
        assert row is not None
        assert row[1] == 0  # session_active = 0
        conn.close()
