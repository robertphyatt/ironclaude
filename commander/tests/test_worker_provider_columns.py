import sqlite3
from ironclaude.db import init_db
from ironclaude.worker_registry import WorkerRegistry


def test_workers_has_provider_identity_columns(tmp_path):
    conn = init_db(str(tmp_path / "c.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(workers)").fetchall()}
    assert {"client", "model", "native_session_id"} <= cols


def test_set_worker_provider_persists(tmp_path):
    conn = init_db(str(tmp_path / "c.db"))
    reg = WorkerRegistry(conn)
    reg.register_worker("w1", "claude-opus", "ic-w1")
    reg.set_worker_provider("w1", client="codex", model="gpt-5.6-sol")
    row = conn.execute("SELECT client, model FROM workers WHERE id='w1'").fetchone()
    assert row["client"] == "codex" and row["model"] == "gpt-5.6-sol"


def test_migration_adds_columns_to_existing_db(tmp_path):
    p = str(tmp_path / "old.db")
    c0 = sqlite3.connect(p)
    c0.execute("CREATE TABLE workers (id TEXT PRIMARY KEY, type TEXT NOT NULL, tmux_session TEXT NOT NULL)")
    c0.commit(); c0.close()
    conn = init_db(p)  # must ALTER without error
    cols = {r[1] for r in conn.execute("PRAGMA table_info(workers)").fetchall()}
    assert {"client", "model", "native_session_id"} <= cols


def test_register_worker_persists_provider_identity_atomically(tmp_path):
    conn = init_db(str(tmp_path / "c.db"))
    reg = WorkerRegistry(conn)

    reg.register_worker(
        "w1",
        "claude-opus",
        "ic-w1",
        client="codex",
        model="gpt-5.6-sol",
        native_session_id="019fa1c0-f006-7001-8366-ed31179f9993",
    )

    row = reg.get_worker("w1")
    assert row["client"] == "codex"
    assert row["model"] == "gpt-5.6-sol"
    assert row["native_session_id"] == "019fa1c0-f006-7001-8366-ed31179f9993"
