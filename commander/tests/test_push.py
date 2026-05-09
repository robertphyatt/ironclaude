"""Tests for push_repo() in OrchestratorTools and push reaction handling."""
import sqlite3
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ironclaude.db import init_db
from ironclaude.main import IroncladeDaemon
from ironclaude.orchestrator_mcp import OrchestratorTools


@pytest.fixture
def db_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def mock_slack():
    slack = MagicMock()
    slack.post_message.return_value = "1234567890.123456"
    return slack


@pytest.fixture
def tools(db_conn, mock_slack, tmp_path):
    registry = MagicMock()
    tmux = MagicMock()
    config = {"push_enabled": True, "push_max_per_hour": 5}
    return OrchestratorTools(
        registry, tmux, str(tmp_path / "ledger.json"),
        db_conn=db_conn, slack_bot=mock_slack, config=config,
    )


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with a remote and a local branch."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/test/test.git"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


# ─── push_repo() tests ────────────────────────────────────────────────────────


def test_push_disabled(tools, db_conn, tmp_path):
    """push_enabled=False returns error string, no DB write."""
    tools._config["push_enabled"] = False
    result = tools.push_repo(str(tmp_path), "origin", "main")
    assert isinstance(result, str), f"Expected str, got {type(result)}: {result!r}"
    assert "push_enabled" in result.lower() or "disabled" in result.lower()
    count = db_conn.execute("SELECT COUNT(*) FROM push_requests").fetchone()[0]
    assert count == 0


def test_push_invalid_remote_chars(tools, tmp_path):
    """Remote with shell metacharacters or spaces is rejected before any git call."""
    for bad_remote in ["ori;gin", "ori|gin", "ori`gin", "ori gin", "--force", "ori&gin"]:
        result = tools.push_repo(str(tmp_path), bad_remote, "main")
        assert isinstance(result, str), f"Expected str for remote={bad_remote!r}"
        assert "invalid" in result.lower(), (
            f"Expected 'invalid' in result for remote={bad_remote!r}, got {result!r}"
        )
    count = tools._db.execute("SELECT COUNT(*) FROM push_requests").fetchone()[0]
    assert count == 0


def test_push_invalid_branch_chars(tools, tmp_path):
    """Branch with flags, spaces, or metacharacters is rejected."""
    for bad_branch in ["--force", "feat ure", "feat;ure", "feat|ure", "feat`ure"]:
        result = tools.push_repo(str(tmp_path), "origin", bad_branch)
        assert isinstance(result, str), f"Expected str for branch={bad_branch!r}"
        assert "invalid" in result.lower(), (
            f"Expected 'invalid' in result for branch={bad_branch!r}, got {result!r}"
        )


def test_push_submit_success(tools, db_conn, mock_slack, git_repo):
    """Happy path: validates repo/remote/branch, inserts row, posts Slack, returns pending dict."""
    with patch("ironclaude.orchestrator_mcp.subprocess.run") as mock_run:
        # All subprocess calls (rev-parse, remote get-url, show-ref, log, diff) succeed
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123 test commit\n", stderr="")
        result = tools.push_repo(str(git_repo), "origin", "main")

    assert isinstance(result, dict), f"Expected dict, got {result!r}"
    assert result["status"] == "pending"
    assert "id" in result
    assert "expires_at" in result

    row = db_conn.execute("SELECT * FROM push_requests WHERE id=?", (result["id"],)).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["message_ts"] == "1234567890.123456"

    mock_slack.post_message.assert_called_once()
    msg = mock_slack.post_message.call_args[0][0]
    assert "✅" in msg or "❌" in msg


def test_push_rate_limit(tools, db_conn, git_repo):
    """5 completed requests in last hour; 6th returns rate limit error, no new row inserted."""
    for i in range(5):
        db_conn.execute(
            "INSERT INTO push_requests "
            "(id, repo, remote, branch, commit_summary, diff_stats, status, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'completed', datetime('now', '+5 minutes'))",
            (f"uuid-rate-{i}", str(git_repo), "origin", "main", "log", "stats"),
        )
    db_conn.commit()

    result = tools.push_repo(str(git_repo), "origin", "main")
    assert isinstance(result, str), f"Expected str, got {result!r}"
    assert "rate" in result.lower() or "limit" in result.lower()

    new_count = db_conn.execute(
        "SELECT COUNT(*) FROM push_requests WHERE id NOT LIKE 'uuid-rate-%'"
    ).fetchone()[0]
    assert new_count == 0


# ─── reaction handler tests ───────────────────────────────────────────────────


class FakeBrain:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)


@pytest.fixture
def daemon(db_conn):
    slack = MagicMock()
    slack.post_message.return_value = "ts"
    brain = FakeBrain()
    config = {"operator_name": "TestOp"}
    return IroncladeDaemon(
        config=config,
        slack=slack,
        socket_handler=None,
        registry=MagicMock(),
        tmux_manager=MagicMock(),
        brain=brain,
        db_conn=db_conn,
    )


@pytest.fixture
def push_row(db_conn, git_repo):
    """Insert a pending push request row with valid TTL."""
    push_id = "test-push-id-0001"
    db_conn.execute(
        "INSERT INTO push_requests"
        " (id, repo, remote, branch, commit_summary, diff_stats, status, message_ts, expires_at)"
        " VALUES (?, ?, 'origin', 'main', 'log', 'stats', 'pending', 'msg-ts-001',"
        " datetime('now', '+5 minutes'))",
        (push_id, str(git_repo)),
    )
    db_conn.commit()
    return push_id


def test_confirm_executes(daemon, db_conn, git_repo, push_row):
    """white_check_mark reaction runs git push and marks completed."""
    with patch("ironclaude.main.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = daemon._handle_push_reaction("white_check_mark", "msg-ts-001")

    assert result is True
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == ["git", "push", "origin", "main"]
    row = db_conn.execute("SELECT status FROM push_requests WHERE id=?", (push_row,)).fetchone()
    assert row["status"] == "completed"


def test_reject(daemon, db_conn, push_row):
    """x reaction marks rejected without running git push."""
    with patch("ironclaude.main.subprocess.run") as mock_run:
        result = daemon._handle_push_reaction("x", "msg-ts-001")

    assert result is True
    mock_run.assert_not_called()
    row = db_conn.execute("SELECT status FROM push_requests WHERE id=?", (push_row,)).fetchone()
    assert row["status"] == "rejected"


def test_confirm_expired(daemon, db_conn):
    """white_check_mark on an expired push marks it expired, no git push."""
    push_id = "test-expired-001"
    db_conn.execute(
        "INSERT INTO push_requests"
        " (id, repo, remote, branch, commit_summary, diff_stats, status, message_ts, expires_at)"
        " VALUES (?, '/tmp', 'origin', 'main', 'log', 'stats', 'pending', 'msg-ts-exp',"
        " datetime('now', '-1 minute'))",
        (push_id,),
    )
    db_conn.commit()

    with patch("ironclaude.main.subprocess.run") as mock_run:
        result = daemon._handle_push_reaction("white_check_mark", "msg-ts-exp")

    assert result is True
    mock_run.assert_not_called()
    row = db_conn.execute("SELECT status FROM push_requests WHERE id=?", (push_id,)).fetchone()
    assert row["status"] == "expired"


def test_already_consumed(daemon, db_conn):
    """Reaction on a non-pending row returns False — already handled."""
    push_id = "test-consumed-001"
    db_conn.execute(
        "INSERT INTO push_requests"
        " (id, repo, remote, branch, commit_summary, diff_stats, status, message_ts, expires_at)"
        " VALUES (?, '/tmp', 'origin', 'main', 'log', 'stats', 'completed', 'msg-ts-done',"
        " datetime('now', '+5 minutes'))",
        (push_id,),
    )
    db_conn.commit()

    result = daemon._handle_push_reaction("white_check_mark", "msg-ts-done")
    assert result is False


def test_git_failure(daemon, db_conn, git_repo, push_row):
    """git push failure marks status as failed."""
    with patch("ironclaude.main.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="authentication failed")
        result = daemon._handle_push_reaction("white_check_mark", "msg-ts-001")

    assert result is True
    row = db_conn.execute("SELECT status FROM push_requests WHERE id=?", (push_row,)).fetchone()
    assert row["status"] == "failed"


def test_unrelated_emoji_ignored(daemon, db_conn, push_row):
    """Reactions with emoji other than white_check_mark/x return False."""
    result = daemon._handle_push_reaction("thumbsup", "msg-ts-001")
    assert result is False
    row = db_conn.execute("SELECT status FROM push_requests WHERE id=?", (push_row,)).fetchone()
    assert row["status"] == "pending"


def test_sweep_expires_pending(daemon, db_conn):
    """_sweep_expired_push_requests marks overdue pending rows as expired."""
    db_conn.execute(
        "INSERT INTO push_requests"
        " (id, repo, remote, branch, commit_summary, diff_stats, status, expires_at)"
        " VALUES ('old-1', '/tmp', 'origin', 'main', 'log', 'stats', 'pending',"
        " datetime('now', '-10 minutes'))"
    )
    db_conn.execute(
        "INSERT INTO push_requests"
        " (id, repo, remote, branch, commit_summary, diff_stats, status, expires_at)"
        " VALUES ('still-valid', '/tmp', 'origin', 'main', 'log', 'stats', 'pending',"
        " datetime('now', '+5 minutes'))"
    )
    db_conn.commit()

    daemon._sweep_expired_push_requests()

    old_row = db_conn.execute("SELECT status FROM push_requests WHERE id='old-1'").fetchone()
    valid_row = db_conn.execute(
        "SELECT status FROM push_requests WHERE id='still-valid'"
    ).fetchone()
    assert old_row["status"] == "expired"
    assert valid_row["status"] == "pending"
