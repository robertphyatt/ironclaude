# Adversarial Review v1.0.8 Security Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 8 findings from the adversarial security review of IronClaude v1.0.8 (SQL injection, path traversal, XSS, dead batch grading, asymmetric audit trail, connection leak, two DRY violations).

**Architecture:** Seven tasks across two files (`orchestrator_mcp.py`, `wiki_server.py`). Tasks 1 and 3 are independent (different files) and run in Wave 1. Tasks 2→4→7→5→6 sequence through `orchestrator_mcp.py` to avoid concurrent edits. BUG-02 and BUG-03 are fixed in-place (Task 4) before DRY-01 extracts the shared method (Task 7), so bug fixes survive a DRY-01 code review rejection.

**Tech Stack:** Python 3.11, sqlite3, pytest, pathlib, re, html (stdlib)

---

## Task 1: SEC-01 — UUID Validation in `_activate_pm_remote`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (lines 1258–1278)
- Test: `commander/tests/test_orchestrator_mcp.py` (add new `TestActivatePmRemote` class at end of file)

**Step 1: Write RED test**

Add this class at the end of `commander/tests/test_orchestrator_mcp.py` (after the last class):

```python
class TestActivatePmRemote:
    def test_rejects_non_uuid_session_id(self, tools, mock_tmux):
        """_activate_pm_remote returns error if session UUID fails UUID format check."""
        # 36-char string that passes the len==36 check but contains SQL injection characters
        malicious = "a' OR 'x'='x'; INSERT INTO evil;!"
        assert len(malicious) == 36
        mock_tmux.list_pane_pid.return_value = "12345"
        mock_tmux.read_file.return_value = malicious
        result = tools._activate_pm_remote("ic-w1", "remote-host")
        assert isinstance(result, str)
        assert "invalid" in result.lower()
        mock_tmux.run_sqlite_query.assert_not_called()

    def test_accepts_valid_uuid(self, tools, mock_tmux):
        """_activate_pm_remote succeeds when session UUID matches UUID format."""
        mock_tmux.list_pane_pid.return_value = "12345"
        mock_tmux.read_file.return_value = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_tmux.run_sqlite_query.return_value = ""
        result = tools._activate_pm_remote("ic-w1", "remote-host")
        assert result is None
        mock_tmux.run_sqlite_query.assert_called_once()
```

**Step 2: Run tests — verify RED**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestActivatePmRemote -v
```

Expected: FAIL on both tests (`test_rejects_non_uuid_session_id` — returns None instead of error; `test_accepts_valid_uuid` — also returns None but `run_sqlite_query` called regardless).

**Step 3: Implement UUID validation**

In `commander/src/ironclaude/orchestrator_mcp.py`, find `_activate_pm_remote` at line ~1258. After the `if session_uuid is None:` block (after line 1261), insert UUID validation before the `db_path = "~/.claude/ironclaude.db"` line:

```python
        # Validate UUID format before interpolating into SQL.
        # TODO: replace with parameterized query when run_sqlite_query supports
        #       bind params over SSH — this regex is a forced workaround for the
        #       CLI sqlite3 interface which cannot use ? placeholders.
        if not re.fullmatch(
            r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
            session_uuid,
        ):
            reason = f"invalid session UUID format: {session_uuid!r}"
            logger.warning(reason)
            return reason

        db_path = "~/.claude/ironclaude.db"
```

The existing code at lines 1263–1270 stays unchanged after this insertion.

**Step 4: Run tests — verify GREEN**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestActivatePmRemote -v
```

Expected: PASS on both tests.

**Step 5: Run full suite to check regressions**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py -x -q 2>&1 | tail -20
```

Expected: same pass count as before (no regressions).

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 2: SEC-02 — Path Traversal Guard in `wiki_write` and `wiki_delete`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (lines 2461–2510)
- Test: `commander/tests/test_orchestrator_mcp.py` (add to `TestWikiTools` class)

**Step 1: Write RED tests**

Add these two tests inside the `TestWikiTools` class (after the last test in that class, before the next class definition):

```python
    def test_wiki_write_rejects_path_traversal(self, wiki_tools, tmp_path):
        """wiki_write returns an error string for path traversal page names."""
        import os
        result = wiki_tools.wiki_write("../etc/passwd", "Evil", "bad content")
        assert "traversal" in result.lower()
        # Verify no file was written outside wiki dir
        assert not (tmp_path / "etc" / "passwd.md").exists()

    def test_wiki_delete_rejects_path_traversal(self, wiki_tools, tmp_path):
        """wiki_delete returns an error string for path traversal page names."""
        result = wiki_tools.wiki_delete("../etc/passwd")
        assert "traversal" in result.lower()
```

**Step 2: Run tests — verify RED**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_write_rejects_path_traversal tests/test_orchestrator_mcp.py::TestWikiTools::test_wiki_delete_rejects_path_traversal -v
```

Expected: FAIL — `wiki_write` currently creates the file rather than returning an error.

**Step 3: Add path traversal guard to `wiki_write`**

In `commander/src/ironclaude/orchestrator_mcp.py`, in the `wiki_write` method. The current code at line ~2466:

```python
        page_path = os.path.join(wiki_dir, f"{page}.md")
        is_update = os.path.exists(page_path)
```

Replace with:

```python
        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
            return f"Path traversal rejected: {page}"
        is_update = os.path.exists(page_path)
```

**Step 4: Add path traversal guard to `wiki_delete`**

In `wiki_delete`, the current code at line ~2494:

```python
        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not os.path.exists(page_path):
```

Replace with:

```python
        page_path = os.path.join(wiki_dir, f"{page}.md")
        if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
            return f"Path traversal rejected: {page}"
        if not os.path.exists(page_path):
```

**Step 5: Run tests — verify GREEN**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestWikiTools -v
```

Expected: all TestWikiTools tests pass including the two new ones.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 3: SEC-03 — XSS Escaping in `wiki_server.py`

**Files:**
- Modify: `tools/wiki_server.py`
- Create: `commander/tests/test_wiki_server.py`

**Step 1: Create RED test file**

Create `commander/tests/test_wiki_server.py` with:

```python
"""Tests for tools/wiki_server.py — XSS escaping in HTML output."""
from __future__ import annotations

import sys
from pathlib import Path

# tools/ is not on the pytest pythonpath — add it explicitly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from wiki_server import _parse_md  # noqa: E402


def test_parse_md_escapes_xss_in_title(tmp_path):
    """Title from YAML frontmatter is HTML-escaped before being placed in <title>."""
    md_file = tmp_path / "test.md"
    md_file.write_text("---\ntitle: <script>alert(1)</script>\n---\n\nContent\n")
    title, _ = _parse_md(md_file)
    assert "<script>" not in title
    assert "&lt;script&gt;" in title


def test_parse_md_preserves_html_body(tmp_path):
    """Body is pre-rendered HTML from the markdown library — must not be double-escaped."""
    md_file = tmp_path / "test.md"
    md_file.write_text("# Hello\n\n**bold**\n")
    _, body = _parse_md(md_file)
    assert "<h1>" in body
    assert "&lt;h1&gt;" not in body
```

**Step 2: Run test — verify RED**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_wiki_server.py -v
```

Expected: `test_parse_md_escapes_xss_in_title` FAILS (title returned unescaped). `test_parse_md_preserves_html_body` passes.

**Step 3: Add `import html` to wiki_server.py**

In `tools/wiki_server.py`, the current imports are:

```python
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import markdown as mdlib
```

Add `import html` after `import re`:

```python
import html
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import markdown as mdlib
```

**Step 4: Escape title in `_parse_md`**

In `tools/wiki_server.py`, the `_parse_md` function ends with:

```python
    md = mdlib.Markdown(extensions=["tables", "fenced_code"])
    return title, md.convert(body)
```

Replace with:

```python
    md = mdlib.Markdown(extensions=["tables", "fenced_code"])
    return html.escape(title), md.convert(body)
```

**Step 5: Escape page stems in `_send_404`**

In `tools/wiki_server.py`, the `_send_404` method contains:

```python
        pages = sorted(p.stem for p in WIKI_DIR.glob("*.md") if p.stem != "index")
        items = "\n".join(f'<li><a href="/wiki/{p}.md">{p}</a></li>' for p in pages)
```

Replace with:

```python
        pages = sorted(p.stem for p in WIKI_DIR.glob("*.md") if p.stem != "index")
        items = "\n".join(
            f'<li><a href="/wiki/{html.escape(p)}.md">{html.escape(p)}</a></li>'
            for p in pages
        )
```

**Step 6: Run tests — verify GREEN**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_wiki_server.py -v
```

Expected: both tests PASS.

**Step 7: Stage changes**

```bash
git add tools/wiki_server.py commander/tests/test_wiki_server.py
```

---

## Task 4: BUG-02 + BUG-03 — Audit Trail and Connection Safety (In-Place)

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (lines 1021–1192)
- Test: `commander/tests/test_orchestrator_mcp.py` (add tests + update fixture in `TestActivatePmViaSqlite`)

These fixes land in the existing `_activate_pm_via_sqlite` and `_deactivate_pm_via_sqlite` methods without structural consolidation. Task 7 (DRY-01) handles the extraction separately so these fixes survive a DRY revert.

**Step 1: Write RED test — audit trail (BUG-02)**

In `commander/tests/test_orchestrator_mcp.py`, inside `TestActivatePmViaSqlite`, add the `AUDIT_LOG_SCHEMA` class constant and `test_activate_writes_audit_log`:

```python
    AUDIT_LOG_SCHEMA = """
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            terminal_session TEXT,
            actor TEXT,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            context TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """

    def test_activate_writes_audit_log(self, tools, tmp_path):
        """_activate_pm_via_sqlite writes actor='daemon:pm_activate' to audit_log."""
        pid, uuid = "12345", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        claude_dir = self._setup_claude_dir(tmp_path, pid, uuid)
        db_path = claude_dir / "ironclaude.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(self.AUDIT_LOG_SCHEMA)
        conn.commit()
        conn.close()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = self._mock_tmux_run(pid)
            tools._activate_pm_via_sqlite("ic-w1", timeout=2, _claude_dir=claude_dir)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT actor, action FROM audit_log WHERE terminal_session=?", (uuid,)
        ).fetchone()
        conn.close()
        assert row is not None, "audit_log must have a row after activation"
        assert row[0] == "daemon:pm_activate"
        assert row[1] == "professional_mode_on"
```

**Step 2: Write RED test — connection safety (BUG-03)**

Also inside `TestActivatePmViaSqlite`, add:

```python
    def test_connection_closed_on_sqlite_error(self, tools, tmp_path):
        """DB connection is closed via finally even when sqlite3.Error is raised."""
        pid, uuid = "12345", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        claude_dir = self._setup_claude_dir(tmp_path, pid, uuid, create_db=False)
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.Error("forced error")
        with patch("subprocess.run") as mock_run, \
             patch("ironclaude.orchestrator_mcp.sqlite3.connect", return_value=mock_conn):
            mock_run.return_value = self._mock_tmux_run(pid)
            result = tools._activate_pm_via_sqlite("ic-w1", timeout=2, _claude_dir=claude_dir)
        assert isinstance(result, str)
        assert "sqlite" in result.lower()
        mock_conn.close.assert_called_once()
```

**Step 3: Run RED tests**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestActivatePmViaSqlite::test_activate_writes_audit_log tests/test_orchestrator_mcp.py::TestActivatePmViaSqlite::test_connection_closed_on_sqlite_error -v
```

Expected: both FAIL (`test_activate_writes_audit_log` — no audit row; `test_connection_closed_on_sqlite_error` — `close` not called on error path).

**Step 4: Fix BUG-03 in `_activate_pm_via_sqlite` — add `try/finally`**

In `_activate_pm_via_sqlite` (lines 1078–1101), replace the try/except block:

```python
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                " VALUES (?, 'on')",
                (session_uuid,),
            )
            conn.execute(
                "UPDATE sessions SET professional_mode='on', updated_at=datetime('now')"
                " WHERE terminal_session=?",
                (session_uuid,),
            )
            conn.commit()
            conn.close()
            logger.info(
                f"PM activated via SQLite for {session_name} "
                f"(session {session_uuid[:8]}...)"
            )
            return None
        except sqlite3.Error as e:
            reason = f"sqlite error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason
```

Replace with:

```python
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                " VALUES (?, 'on')",
                (session_uuid,),
            )
            conn.execute(
                "UPDATE sessions SET professional_mode='on', updated_at=datetime('now')"
                " WHERE terminal_session=?",
                (session_uuid,),
            )
            conn.commit()
            conn.execute(
                "INSERT INTO audit_log"
                " (terminal_session, actor, action, old_value, new_value, context)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_uuid,
                    "daemon:pm_activate",
                    "professional_mode_on",
                    None,
                    "on",
                    f"PM activated via _activate_pm_via_sqlite for {session_name}",
                ),
            )
            conn.commit()
        except sqlite3.Error as e:
            reason = f"sqlite error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason
        finally:
            conn.close()
        logger.info(
            f"PM activated via SQLite for {session_name} "
            f"(session {session_uuid[:8]}...)"
        )
        return None
```

**Step 5: Fix BUG-03 in `_deactivate_pm_via_sqlite` — move `conn` outside try**

In `_deactivate_pm_via_sqlite` (lines 1155–1192), find the existing try block and restructure it identically: move `conn = sqlite3.connect(...)` before the `try:` and add `finally: conn.close()`. The audit_log INSERT is already present — no change needed to that.

Current structure in `_deactivate_pm_via_sqlite`:
```python
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            ...
            conn.close()
            ...
            return None
        except sqlite3.Error as e:
            ...
            return reason
```

Replace with:
```python
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                " VALUES (?, 'off')",
                (session_uuid,),
            )
            conn.execute(
                "UPDATE sessions SET professional_mode='off', updated_at=datetime('now')"
                " WHERE terminal_session=?",
                (session_uuid,),
            )
            conn.commit()
            conn.execute(
                "INSERT INTO audit_log"
                " (terminal_session, actor, action, old_value, new_value, context)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_uuid,
                    "daemon:pm_deactivate",
                    "professional_mode_off",
                    None,
                    "off",
                    f"PM deactivated via _deactivate_pm_via_sqlite for {session_name}",
                ),
            )
            conn.commit()
        except sqlite3.Error as e:
            reason = f"sqlite error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason
        finally:
            conn.close()
        logger.info(
            f"PM deactivated via SQLite for {session_name} "
            f"(session {session_uuid[:8]}...)"
        )
        return None
```

**Step 6: Update `_setup_claude_dir` fixture to create `audit_log`**

In `TestActivatePmViaSqlite._setup_claude_dir`, add `conn.execute(self.AUDIT_LOG_SCHEMA)` after `conn.execute(self.SESSIONS_SCHEMA)` so existing tests don't fail on the new audit_log INSERT:

```python
    def _setup_claude_dir(self, tmp_path, pid, session_uuid, create_db=True, prefill_row=None):
        """Create a temp ~/.claude dir with session file and optional DB."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / f"ironclaude-session-{pid}.id").write_text(session_uuid)
        if create_db:
            db_path = claude_dir / "ironclaude.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(self.SESSIONS_SCHEMA)
            conn.execute(self.AUDIT_LOG_SCHEMA)
            if prefill_row:
                conn.execute(
                    "INSERT INTO sessions (terminal_session, professional_mode) VALUES (?, ?)",
                    (session_uuid, prefill_row),
                )
            conn.commit()
            conn.close()
        return claude_dir
```

**Step 7: Run all TestActivatePmViaSqlite — verify GREEN**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestActivatePmViaSqlite -v
```

Expected: all tests pass including two new ones.

**Step 8: Run deactivate test — verify no regression**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py -k "deactivate_pm" -v
```

Expected: PASS.

**Step 9: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 7: DRY-01 — Extract `_set_pm_via_sqlite`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (lines 1021–1192, now already fixed)
- Test: `commander/tests/test_orchestrator_mcp.py` (no new tests — behavior unchanged, existing tests verify correctness)

**Step 1: Verify baseline**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestActivatePmViaSqlite -v 2>&1 | tail -10
```

Expected: all pass. This confirms Task 4 landed cleanly before touching the same region.

**Step 2: Replace both methods with shared `_set_pm_via_sqlite` + thin wrappers**

In `commander/src/ironclaude/orchestrator_mcp.py`, replace the entire `_activate_pm_via_sqlite` method and `_deactivate_pm_via_sqlite` method (lines 1021–1192, post-Task-4 content) with:

```python
    def _set_pm_via_sqlite(
        self, session_name: str, value: str,
        timeout: int = 30, _claude_dir: Path | None = None,
    ) -> str | None:
        """Shared implementation for activate/deactivate PM via direct SQLite write.

        Args:
            session_name: tmux session name (e.g. 'ic-w1')
            value: 'on' or 'off'
            timeout: seconds to wait for session ID file to appear
            _claude_dir: override ~/.claude path (for testing)

        Returns None on success, or a failure reason string on any failure.
        """
        claude_dir = _claude_dir if _claude_dir is not None else Path("~/.claude").expanduser()

        # Step 1: Get pane PID via tmux
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", session_name, "-F", "#{pane_pid}"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                reason = f"tmux list-panes failed: {result.stderr.strip()}"
                logger.warning(f"{reason} for {session_name}")
                return reason
            pane_pid = result.stdout.strip()
            if not pane_pid.isdigit():
                reason = f"invalid pane PID: {pane_pid}"
                logger.warning(f"{reason} for {session_name}")
                return reason
        except Exception as e:
            reason = f"pane PID error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason

        # Step 2: Poll for session ID file
        session_id_file = claude_dir / f"ironclaude-session-{pane_pid}.id"
        deadline = time.time() + timeout
        session_uuid = None
        while time.time() < deadline:
            if session_id_file.exists():
                candidate = session_id_file.read_text().strip()
                if len(candidate) == 36:
                    session_uuid = candidate
                    break
            time.sleep(1)

        if session_uuid is None:
            reason = f"session ID file timeout after {timeout}s"
            logger.warning(f"{reason} for {session_name}")
            return reason

        # Step 3: Write PM value and audit log to DB
        actor = "daemon:pm_activate" if value == "on" else "daemon:pm_deactivate"
        action = "professional_mode_on" if value == "on" else "professional_mode_off"
        db_path = claude_dir / "ironclaude.db"
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO sessions (terminal_session, professional_mode)"
                " VALUES (?, ?)",
                (session_uuid, value),
            )
            conn.execute(
                "UPDATE sessions SET professional_mode=?, updated_at=datetime('now')"
                " WHERE terminal_session=?",
                (value, session_uuid),
            )
            conn.commit()
            conn.execute(
                "INSERT INTO audit_log"
                " (terminal_session, actor, action, old_value, new_value, context)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_uuid,
                    actor,
                    action,
                    None,
                    value,
                    f"PM set to '{value}' via _set_pm_via_sqlite for {session_name}",
                ),
            )
            conn.commit()
        except sqlite3.Error as e:
            reason = f"sqlite error: {e}"
            logger.warning(f"{reason} for {session_name}")
            return reason
        finally:
            conn.close()

        logger.info(
            f"PM set to '{value}' via SQLite for {session_name} "
            f"(session {session_uuid[:8]}...)"
        )
        return None

    def _activate_pm_via_sqlite(
        self, session_name: str, timeout: int = 30, _claude_dir: Path | None = None
    ) -> str | None:
        """Activate professional mode by writing directly to ironclaude.db."""
        return self._set_pm_via_sqlite(session_name, "on", timeout, _claude_dir)

    def _deactivate_pm_via_sqlite(
        self, session_name: str, timeout: int = 30, _claude_dir: Path | None = None
    ) -> str | None:
        """Deactivate professional mode by writing 'off' to ironclaude.db."""
        return self._set_pm_via_sqlite(session_name, "off", timeout, _claude_dir)
```

**Step 3: Run full TestActivatePmViaSqlite — verify all tests still pass**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestActivatePmViaSqlite -v
```

Expected: all pass (behavior identical to Task 4 output — wrappers delegate to shared method).

**Step 4: Run deactivate regression check**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py -k "deactivate_pm" -v
```

Expected: PASS.

**Step 5: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py
```

---

## Task 5: BUG-01 — Batch Grading in `_call_grader`

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (lines 537–630, ~1510)
- Test: `commander/tests/test_orchestrator_mcp.py` (add new `TestCallGraderBatch` class)

**Step 1: Write RED tests**

Add this class at the end of `commander/tests/test_orchestrator_mcp.py`:

```python
class TestCallGraderBatch:
    """Tests for _call_grader batch=True parameter."""

    def _setup_grader(self, tools):
        tools._ensure_grader = MagicMock(return_value=True)
        tools._wait_for_grader_clear = MagicMock()
        tools._grader_ready = True

    def test_batch_param_returns_list(self, tools, mock_tmux):
        """_call_grader(batch=True) returns a list when grader emits a JSON array."""
        self._setup_grader(tools)
        array_json = (
            '[{"grade":"A","approved":true,"feedback":"G1"},'
            '{"grade":"B","approved":true,"feedback":"G2"}]'
        )
        with patch("ironclaude.orchestrator_mcp.secrets.token_hex", return_value="abc12345"):
            delimiter = "GRADER_RESPONSE_abc12345"
            tools.tmux.read_log_tail.side_effect = [
                "",  # baseline
                f"{delimiter}\n{array_json}",  # poll response
            ]
            with patch("ironclaude.orchestrator_mcp.time.sleep"):
                result = tools._call_grader("sys prompt", "user prompt", batch=True)
        assert isinstance(result, list), f"Expected list, got {type(result)}: {result}"
        assert len(result) == 2
        assert result[0]["grade"] == "A"
        assert result[1]["grade"] == "B"

    def test_non_batch_still_returns_dict(self, tools, mock_tmux):
        """_call_grader with no batch param still returns a dict (backwards compat)."""
        self._setup_grader(tools)
        single_json = '{"grade":"A","approved":true,"feedback":"Good","recommended_model":"claude-sonnet"}'
        with patch("ironclaude.orchestrator_mcp.secrets.token_hex", return_value="abc12345"):
            delimiter = "GRADER_RESPONSE_abc12345"
            tools.tmux.read_log_tail.side_effect = [
                "",  # baseline
                f"{delimiter}\n{single_json}",  # poll response
            ]
            with patch("ironclaude.orchestrator_mcp.time.sleep"):
                result = tools._call_grader("sys prompt", "user prompt")
        assert isinstance(result, dict)
        assert result["grade"] == "A"
```

**Step 2: Run tests — verify RED**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestCallGraderBatch -v
```

Expected: `test_batch_param_returns_list` FAILS (TypeError: `_call_grader()` got unexpected keyword argument `batch`). `test_non_batch_still_returns_dict` passes.

**Step 3: Add `batch` parameter and array extraction to `_call_grader`**

In `commander/src/ironclaude/orchestrator_mcp.py`, the current signature at line 537:

```python
    def _call_grader(self, system_prompt: str, user_prompt: str) -> dict:
```

Replace with:

```python
    def _call_grader(self, system_prompt: str, user_prompt: str, batch: bool = False) -> dict | list:
```

Then, in the polling loop, after `post_delimiter = delta[delimiter_pos + len(delimiter):]` (line ~584), insert batch array extraction BEFORE the existing single-object regex search. Insert after the `post_delimiter` line:

```python
                # Batch mode: try JSON array extraction first
                if batch:
                    array_match = re.search(r'\[.*\]', post_delimiter, re.DOTALL)
                    if array_match:
                        try:
                            result_list = json.loads(array_match.group())
                            if (
                                isinstance(result_list, list)
                                and all(
                                    isinstance(item, dict)
                                    and "grade" in item
                                    and "approved" in item
                                    and "feedback" in item
                                    for item in result_list
                                )
                            ):
                                self.tmux.send_keys(self._grader_session, "/clear")
                                self._wait_for_grader_clear()
                                logger.debug(
                                    "Batch grader responded in %.1fs", time.time() - start_time
                                )
                                return result_list
                        except (json.JSONDecodeError, TypeError):
                            pass  # fall through to single-object extraction
```

The existing `# Look for JSON with grade/approved/feedback fields` comment and `json_match = re.search(...)` block continues unchanged after this insertion.

**Step 4: Update `spawn_workers` call to pass `batch=True`**

In `orchestrator_mcp.py` at line ~1510, find:

```python
        grade_results = self._call_grader(system_prompt, user_prompt)
```

Replace with:

```python
        grade_results = self._call_grader(system_prompt, user_prompt, batch=True)
```

**Step 5: Run tests — verify GREEN**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestCallGraderBatch -v
```

Expected: both tests PASS.

**Step 6: Verify existing batch spawn tests still pass**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestBatchSpawn -v
```

Expected: all TestBatchSpawn tests PASS (they mock `_call_grader` directly, so unchanged).

**Step 7: Stage changes**

```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 6: DRY-02 — WORKER_COMMANDS Single Definition

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py` (line ~90 region — no change needed, canonical definition stays)
- Modify: `commander/src/ironclaude/main.py` (lines 49–51)
- Test: `commander/tests/test_orchestrator_mcp.py` (add `TestWorkerCommandsImport` class)

**Step 1: Write RED test**

Add this class at the end of `commander/tests/test_orchestrator_mcp.py`:

```python
class TestWorkerCommandsImport:
    def test_worker_commands_is_same_object_in_main_and_orchestrator(self):
        """WORKER_COMMANDS in main.py is the same object as in orchestrator_mcp — not a copy."""
        from ironclaude import main, orchestrator_mcp
        assert main.WORKER_COMMANDS is orchestrator_mcp.WORKER_COMMANDS, (
            "main.WORKER_COMMANDS must be imported from orchestrator_mcp, not re-defined"
        )
```

**Step 2: Run test — verify RED**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestWorkerCommandsImport -v
```

Expected: FAIL — `main.WORKER_COMMANDS is not orchestrator_mcp.WORKER_COMMANDS` (two separate dict objects).

**Step 3: Remove duplicate definition from `main.py`**

In `commander/src/ironclaude/main.py`, the current lines 49–51:

```python
WORKER_COMMANDS = {
    "claude-sonnet": "export CLAUDE_CODE_EFFORT_LEVEL=high; exec claude --model 'sonnet' --dangerously-skip-permissions",
}
```

Replace with:

```python
from ironclaude.orchestrator_mcp import WORKER_COMMANDS
```

**Step 4: Run test — verify GREEN**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/test_orchestrator_mcp.py::TestWorkerCommandsImport -v
```

Expected: PASS.

**Step 5: Run full suite to check for import regressions**

```bash
cd ~/Code/ironclaude/commander && .venv/bin/pytest tests/ -x -q 2>&1 | tail -20
```

Expected: same pass count as before. Note: there should be no circular import since `main.py` already imports from `orchestrator_mcp` — verify by checking `main.py` imports at lines 1–50.

**Step 6: Stage changes**

```bash
git add commander/src/ironclaude/main.py commander/tests/test_orchestrator_mcp.py
```
