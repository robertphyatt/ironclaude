# tests/test_db.py
import sqlite3
import threading
import pytest
from ironclaude.db import init_db, persist_operator_message_acknowledgement


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
        assert "provider_role_state" in tables
        assert "provider_capability_state" in tables
        assert "directive_capability_blocks" in tables
        conn.close()

    def test_directive_capability_block_survives_reopen(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation, status) "
            "VALUES ('1', 'x', 'x', 'blocked')"
        )
        directive_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO directive_capability_blocks "
            "(directive_id, capabilities_json, denial_scope, target, reason, "
            "fingerprint, state, first_observed_at, last_observed_at, "
            "next_recheck_at, backoff_seconds, generation) "
            "VALUES (?, '[\"workspace_write\"]', 'codex_sandbox', '/repo', "
            "'denied', 'fp', 'blocked', 1, 1, 61, 60, 1)",
            (directive_id,),
        )
        conn.commit()
        conn.close()
        reopened = init_db(db_path)
        row = reopened.execute(
            "SELECT capabilities_json, generation FROM directive_capability_blocks "
            "WHERE directive_id=?", (directive_id,)
        ).fetchone()
        assert tuple(row) == ('["workspace_write"]', 1)

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
        assert {"provider_role_state", "provider_capability_state"} <= tables2
        assert tables1, "init_db must create at least one table"
        conn2.close()

    def test_brain_state_singleton(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = init_db(db_path)
        row = conn.execute("SELECT * FROM brain_state WHERE id = 1").fetchone()
        assert row is not None
        assert row[1] == 0  # session_active = 0
        conn.close()

    def test_migrates_legacy_workers_with_workspace_columns(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        legacy = sqlite3.connect(db_path)
        legacy.execute("CREATE TABLE workers (id TEXT PRIMARY KEY)")
        legacy.commit()
        legacy.close()

        conn = init_db(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(workers)")}
        assert {
            "workspace_guid",
            "workspace_repository_identity",
            "workspace_path",
            "workspace_branch",
            "workspace_base_commit",
            "workspace_integration_target",
        } <= columns
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


def test_operator_message_acknowledgement_table_and_persistence(tmp_path):
    db_path = str(tmp_path / "ack.db")
    conn = init_db(db_path)
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='operator_message_acknowledgements'"
    ).fetchone() is not None
    conn.execute(
        "INSERT INTO operator_message_acknowledgements (source_ts, reason) VALUES (?, ?)",
        ("1785731067.460039", "no action requested"),
    )
    conn.commit()
    conn.close()

    reopened = init_db(db_path)
    row = reopened.execute(
        "SELECT source_ts, reason FROM operator_message_acknowledgements WHERE source_ts=?",
        ("1785731067.460039",),
    ).fetchone()
    assert tuple(row) == ("1785731067.460039", "no action requested")
    reopened.close()


def test_persist_operator_message_acknowledgement_validation_does_not_touch_connection(tmp_path):
    conn = init_db(str(tmp_path / "validation.db"))
    for source_ts, reason in (("bad", "reason"), (1785731067.460039, "reason"),
                              ("1785731067.460039", ""), ("1785731067.460039", "   ")):
        with pytest.raises(ValueError):
            persist_operator_message_acknowledgement(conn, source_ts, reason)
    assert conn.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements"
    ).fetchone()[0] == 0
    conn.close()


def test_persist_operator_message_acknowledgement_rejects_none_connection_without_opening_path(tmp_path):
    db_path = tmp_path / "must-not-be-created.db"
    assert not db_path.exists()
    with pytest.raises(
        RuntimeError,
        match="^Database connection required for operator message acknowledgement$",
    ):
        persist_operator_message_acknowledgement(None, "1785731067.460039", "no action")
    assert not db_path.exists()


def test_persist_operator_message_acknowledgement_rejects_active_transaction(tmp_path):
    db_path = str(tmp_path / "active-transaction.db")
    conn = init_db(db_path)
    reader = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO objectives (text, status) VALUES ('pending', 'active')")

        with pytest.raises(
            RuntimeError,
            match=(
                "^Database connection already has an active transaction; operator message "
                "acknowledgement requires an idle connection\\.$"
            ),
        ):
            persist_operator_message_acknowledgement(
                conn, "1785731067.460039", "no action",
            )

        assert conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM objectives").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM operator_message_acknowledgements"
        ).fetchone()[0] == 0
        assert reader.execute("SELECT COUNT(*) FROM objectives").fetchone()[0] == 0
        assert reader.execute(
            "SELECT COUNT(*) FROM operator_message_acknowledgements"
        ).fetchone()[0] == 0

        conn.rollback()
        persist_operator_message_acknowledgement(
            conn, "1785731067.460039", "no action",
        )
        assert reader.execute(
            "SELECT COUNT(*) FROM operator_message_acknowledgements"
        ).fetchone()[0] == 1
    finally:
        reader.close()
        conn.close()


def test_persist_operator_message_acknowledgement_rolls_back_persistence_errors(tmp_path):
    class CommitFailingConnection:
        def __init__(self, inner):
            self.inner = inner
            self.rollbacks = 0

        def execute(self, *args):
            return self.inner.execute(*args)

        @property
        def in_transaction(self):
            return self.inner.in_transaction

        def commit(self):
            raise sqlite3.OperationalError("commit failure")

        def rollback(self):
            self.rollbacks += 1
            self.inner.rollback()

    inner = init_db(str(tmp_path / "rollback.db"))
    conn = CommitFailingConnection(inner)
    with pytest.raises(sqlite3.OperationalError, match="commit failure"):
        persist_operator_message_acknowledgement(conn, "1785731067.460039", "no action")
    assert conn.rollbacks == 1
    assert inner.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements"
    ).fetchone()[0] == 0
    inner.close()


def test_persist_operator_message_acknowledgement_is_immutable_with_plain_rows(tmp_path):
    conn = init_db(str(tmp_path / "immutable.db"))
    conn.row_factory = None
    source_ts = "1785731067.460039"
    first = persist_operator_message_acknowledgement(conn, source_ts, "first reason")
    repeated = persist_operator_message_acknowledgement(conn, source_ts, "second reason")
    assert repeated == first
    assert first["source_ts"] == source_ts
    assert first["reason"] == "first reason"
    assert conn.execute(
        "SELECT reason FROM operator_message_acknowledgements WHERE source_ts=?", (source_ts,)
    ).fetchone()[0] == "first reason"
    conn.close()


def test_persist_operator_message_acknowledgement_concurrent_calls_converge(tmp_path):
    db_path = str(tmp_path / "ack-race.db")
    init_db(db_path).close()
    source_ts = "1785731067.460039"
    barrier = threading.Barrier(2)
    outcomes = []
    errors = []

    def acknowledge(reason):
        conn = init_db(db_path)
        try:
            barrier.wait()
            outcomes.append(persist_operator_message_acknowledgement(conn, source_ts, reason))
        except Exception as exc:
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=acknowledge, args=(reason,)) for reason in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(outcomes) == 2
    assert outcomes[0] == outcomes[1]
    conn = init_db(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements WHERE source_ts=?", (source_ts,)
    ).fetchone()[0] == 1
    conn.close()


def test_operator_message_disposition_exclusivity_both_insert_orders(tmp_path):
    conn = init_db(str(tmp_path / "exclusive.db"))
    source_ts = "1785731067.460039"
    conn.execute(
        "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, 'x', 'x')",
        (source_ts,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO operator_message_acknowledgements (source_ts, reason) VALUES (?, 'no')",
            (source_ts,),
        )
    conn.rollback()
    conn.execute(
        "INSERT INTO operator_message_acknowledgements (source_ts, reason) VALUES (?, 'no')",
        ("1785731067.460040",),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, 'x', 'x')",
            ("1785731067.460040",),
        )
    conn.close()


def test_operator_message_disposition_exclusivity_two_connections_race(tmp_path):
    db_path = str(tmp_path / "race.db")
    init_db(db_path).close()
    source_ts = "1785731067.460039"
    barrier = threading.Barrier(2)
    outcomes = []

    def insert_disposition(kind):
        conn = init_db(db_path)
        try:
            barrier.wait()
            if kind == "ack":
                persist_operator_message_acknowledgement(conn, source_ts, "no")
            else:
                conn.execute(
                    "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, 'x', 'x')",
                    (source_ts,),
                )
            conn.commit()
            outcomes.append("inserted")
        except sqlite3.IntegrityError:
            outcomes.append("rejected")
        except sqlite3.Error:
            outcomes.append("error")
        finally:
            conn.close()

    threads = [
        threading.Thread(target=insert_disposition, args=(kind,))
        for kind in ("ack", "directive")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["inserted", "rejected"]
    conn = init_db(db_path)
    directive_count = conn.execute(
        "SELECT COUNT(*) FROM directives WHERE source_ts=?", (source_ts,)
    ).fetchone()[0]
    acknowledgement_count = conn.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements WHERE source_ts=?",
        (source_ts,),
    ).fetchone()[0]
    assert directive_count in (0, 1)
    assert acknowledgement_count in (0, 1)
    assert directive_count + acknowledgement_count == 1
    conn.close()


def test_multiple_directives_can_still_share_unacknowledged_source_ts(tmp_path):
    conn = init_db(str(tmp_path / "shared.db"))
    source_ts = "1785731067.460039"
    for interpretation in ("first", "second"):
        conn.execute(
            "INSERT INTO directives (source_ts, source_text, interpretation) VALUES (?, 'x', ?)",
            (source_ts, interpretation),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM directives WHERE source_ts=?", (source_ts,)
    ).fetchone()[0] == 2
    conn.close()
