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


def test_init_db_creates_shadow_concordance_table(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_concordance'"
    )
    assert cur.fetchone() is not None


def test_init_db_creates_shadow_concordance_worker_id_index(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_shadow_concordance_worker_id'"
    )
    assert cur.fetchone() is not None


def test_init_db_creates_shadow_concordance_created_at_index(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_shadow_concordance_created_at'"
    )
    assert cur.fetchone() is not None


def test_init_db_creates_directives_planned_columns(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    cursor = conn.execute("PRAGMA table_info(directives)")
    columns = [row[1] for row in cursor.fetchall()]
    for expected in (
        "planned_worker_type",
        "planned_use_goal",
        "planned_prompt",
        "planned_worker_type_reason",
        "planned_use_goal_reason",
        "planned_prompt_reason",
        "superseded_by",
    ):
        assert expected in columns
    conn.close()


def test_init_db_creates_superseded_by_index(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_directives_superseded_by'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_sets_row_factory(tmp_path):
    """Q-2 regression: init_db must return a connection with sqlite3.Row set
    deterministically (not relying on WorkerRegistry.__init__'s side effect),
    so dict(row) and row["col"] access work on any conn it returns, while
    integer indexing (row[0]) keeps working too."""
    conn = init_db(str(tmp_path / "rf.db"))
    assert conn.row_factory is sqlite3.Row
    conn.execute(
        "INSERT INTO directives (source_ts, source_text, interpretation) "
        "VALUES ('ts', 'src', 'interp')"
    )
    conn.commit()
    row = conn.execute("SELECT id, interpretation FROM directives LIMIT 1").fetchone()
    # Both access styles must work on a Row.
    assert row[0] == 1
    assert row["interpretation"] == "interp"
    assert dict(row) == {"id": 1, "interpretation": "interp"}
    conn.close()


def test_migration_idempotent_on_existing_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    cursor = conn2.execute("PRAGMA table_info(directives)")
    columns = [row[1] for row in cursor.fetchall()]
    planned_columns = [
        "planned_worker_type",
        "planned_use_goal",
        "planned_prompt",
        "planned_worker_type_reason",
        "planned_use_goal_reason",
        "planned_prompt_reason",
        "superseded_by",
    ]
    for expected in planned_columns:
        assert columns.count(expected) == 1
    conn2.close()


class TestShadowConcordanceCheck:
    """DB-03: shadow_concordance.concordance column enforces the A/B/C/F enum
    at the SQLite CHECK layer, not just at the application layer."""

    def _seed_row(self, conn, concordance_value):
        conn.execute(
            "INSERT INTO shadow_concordance"
            " (context, worker_id, opus_grade, opus_approved, shadow_grade, shadow_approved,"
            " concordance, confidence_in_disagreement, test_mode)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("plan_review", "w1", "A", 1, "A", 1, concordance_value, "low", 0),
        )
        conn.commit()

    def test_accepts_all_valid_enum_values(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        for value in ("A", "B", "C", "F"):
            self._seed_row(conn, value)
        cur = conn.execute("SELECT COUNT(*) FROM shadow_concordance")
        assert cur.fetchone()[0] == 4
        conn.close()

    def test_rejects_invalid_enum_value(self, tmp_path):
        conn = init_db(str(tmp_path / "test.db"))
        with pytest.raises(sqlite3.IntegrityError):
            self._seed_row(conn, "bogus")
        conn.close()


def test_init_db_migrates_index_after_column_add(tmp_path):
    """Regression: CREATE INDEX on superseded_by must run AFTER the
    ADD-COLUMN migration, so upgrades against pre-existing DBs don't
    crash at CREATE INDEX. This test simulates an old DB that has a
    `directives` table without the superseded_by column, then calls
    init_db() and asserts no OperationalError."""
    import sqlite3
    db_path = str(tmp_path / "old.db")
    # Simulate an "old" DB with the pre-thinking-face `directives` schema.
    old = sqlite3.connect(db_path)
    old.execute(
        "CREATE TABLE directives (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "source_ts TEXT, source_text TEXT, interpretation TEXT, "
        "status TEXT DEFAULT 'pending_confirmation', "
        "created_at TEXT DEFAULT (datetime('now')), "
        "updated_at TEXT DEFAULT (datetime('now')))"
    )
    old.commit()
    old.close()

    from ironclaude.db import init_db
    # Must NOT raise. Under the bug this raises OperationalError at
    # `CREATE INDEX ... ON directives(superseded_by)`.
    conn = init_db(db_path)

    # After init_db the column and the index must exist.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(directives)").fetchall()]
    assert "superseded_by" in cols, f"superseded_by not added: {cols}"
    idx_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='directives'"
    ).fetchall()
    idx_names = [r[0] for r in idx_rows]
    assert "idx_directives_superseded_by" in idx_names, f"index missing: {idx_names}"
