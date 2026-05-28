# Adversarial Review v1.0.8 Security Fixes Design

> **Created:** 2026-05-22
> **Status:** Design Complete

## Summary

Fix all 8 findings from the adversarial security review of IronClaude v1.0.8 (commit 3718b5d).
Covers 3 security issues (SQL injection, path traversal, XSS), 3 bug fixes (dead batch grading,
asymmetric audit trail, connection leak), and 2 DRY violations. DRY-01 subsumes BUG-02 and BUG-03
because the refactoring naturally includes audit logging and safe connection handling in the shared method.

## Architecture

Findings group into 4 implementation waves:

| Wave | Findings | Files Touched |
|------|----------|---------------|
| 1 | SEC-01, SEC-02, SEC-03 | orchestrator_mcp.py, wiki_server.py |
| 2 | DRY-01 + BUG-02 + BUG-03 | orchestrator_mcp.py |
| 3 | BUG-01 | orchestrator_mcp.py |
| 4 | DRY-02 | orchestrator_mcp.py, main.py |

Security fixes land first. The PM SQLite refactor (Wave 2) is a single atomic change that
consolidates two duplicate methods while correcting both the audit trail and connection leak.

## Components

### SEC-01 — UUID Validation in `_activate_pm_remote`

**File:** `commander/src/ironclaude/orchestrator_mcp.py` ~line 1258

After reading the session ID file and before constructing the SQL query, validate
`session_uuid` against a strict UUID regex:

```
re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', session_uuid)
```

If validation fails: log warning, return error reason string. Do NOT execute the query.
This limits session_uuid to hex digits and hyphens — safe for f-string SQL since no SQL metacharacters are possible.

### SEC-02 — Path Traversal Guard in `wiki_write` and `wiki_delete`

**File:** `commander/src/ironclaude/orchestrator_mcp.py` ~lines 2466, 2494

After computing `page_path = os.path.join(wiki_dir, f"{page}.md")`, add:

```python
if not Path(page_path).resolve().is_relative_to(Path(wiki_dir).resolve()):
    return f"Path traversal rejected: {page}"
```

Insert this guard before any filesystem operations. Return the error string directly
(consistent with existing wiki_write/wiki_delete return conventions).

### SEC-03 — XSS Escaping in `tools/wiki_server.py`

**File:** `tools/wiki_server.py`

1. Add `import html` at top of file.
2. In `_parse_md`: wrap returned title: `return html.escape(title), md.convert(body)`. The
   `body` is already rendered HTML by the `markdown` library — do NOT escape it.
3. In `WikiHandler._send_404` (line ~102): escape page stems before injecting into link elements:
   ```python
   items = "\n".join(f'<li><a href="/wiki/{html.escape(p)}.md">{html.escape(p)}</a></li>' for p in pages)
   ```

### DRY-01 + BUG-02 + BUG-03 — Extract `_set_pm_via_sqlite`

**File:** `commander/src/ironclaude/orchestrator_mcp.py` ~lines 1021-1192

Extract shared private method:

```python
def _set_pm_via_sqlite(
    self, session_name: str, value: str,
    timeout: int = 30, _claude_dir: Path | None = None
) -> str | None:
```

Internal structure:
- Step 1: Get pane PID via tmux (same as both current methods)
- Step 2: Poll for session ID file (same as both current methods)
- Step 3: Write to DB using `try/finally: conn.close()` to ensure no leaks
  - `INSERT OR IGNORE` then `UPDATE` with `value`
  - After first `conn.commit()`: INSERT into `audit_log`
    - `actor`: `"daemon:pm_activate"` when `value == 'on'`, else `"daemon:pm_deactivate"`
    - `action`: `"professional_mode_on"` when `value == 'on'`, else `"professional_mode_off"`
  - Second `conn.commit()`

Connection safety pattern:
```python
conn = sqlite3.connect(str(db_path), timeout=5)
try:
    conn.execute("PRAGMA journal_mode=WAL")
    # ... INSERT/UPDATE sessions, INSERT audit_log ...
    conn.commit()
finally:
    conn.close()
```

Existing thin wrappers become:
```python
def _activate_pm_via_sqlite(self, session_name, timeout=30, _claude_dir=None):
    return self._set_pm_via_sqlite(session_name, 'on', timeout, _claude_dir)

def _deactivate_pm_via_sqlite(self, session_name, timeout=30, _claude_dir=None):
    return self._set_pm_via_sqlite(session_name, 'off', timeout, _claude_dir)
```

This preserves existing call sites and test API without changes.

### BUG-01 — Batch Grading in `_call_grader`

**File:** `commander/src/ironclaude/orchestrator_mcp.py` ~lines 587-627, 1510

Add `batch: bool = False` parameter to `_call_grader`. When `batch=True`, after finding the
post-delimiter content, attempt array extraction before single-object extraction:

1. Search for outermost array with `re.search(r'\[.*\]', post_delimiter, re.DOTALL)`
2. If found: attempt `json.loads()` on the match
3. Validate result is a list of dicts with `grade`/`approved`/`feedback` keys
4. If valid: clear grader context and return the list
5. If any step fails: fall through to existing single-object extraction

In `spawn_workers` at line 1510, change:
```python
grade_results = self._call_grader(system_prompt, user_prompt)
```
to:
```python
grade_results = self._call_grader(system_prompt, user_prompt, batch=True)
```

Existing single-call sites pass no `batch` argument — behavior unchanged.

### DRY-02 — WORKER_COMMANDS Single Definition

**File:** `commander/src/ironclaude/main.py` ~lines 49-51

Remove duplicate `WORKER_COMMANDS` dict from `main.py`. Replace with:
```python
from ironclaude.orchestrator_mcp import WORKER_COMMANDS
```

The canonical definition stays in `orchestrator_mcp.py` lines 90-92.

## Data Flow

No data flow changes. All fixes are input validation, output escaping, connection lifecycle,
and code consolidation. No new tables, columns, or API shapes.

## Error Handling

- SEC-01: Invalid UUID → return reason string (consistent with existing failure pattern in `_activate_pm_remote`)
- SEC-02: Traversal detected → return `"Path traversal rejected: {page}"` string (consistent with wiki_write/wiki_delete existing return convention)
- SEC-03: No new error handling — `html.escape` is side-effect-free
- BUG-01: If array parse fails, fall through to existing single-object path; `spawn_workers` fallback to individual grading is unchanged
- DRY-01: `sqlite3.Error` in shared method → `finally: conn.close()` then return reason string

## Testing Strategy

### Existing tests that must continue to pass
- `test_deactivate_pm_via_sqlite` (line 791) — calls `_deactivate_pm_via_sqlite` directly; still works via wrapper
- `test_batch_grades_all_in_one_call`, `test_batch_grader_fallback`, etc. — mock `_call_grader`, unaffected
- All `test_wiki_write_*` and `test_wiki_delete_*` tests

### New tests to add (all in `commander/tests/test_orchestrator_mcp.py` unless noted)

**SEC-01:**
- `test_activate_pm_remote_rejects_sql_injection_uuid` — session_uuid contains `'; DROP TABLE sessions; --`, verify returns error reason, no tmux sqlite call made
- `test_activate_pm_remote_accepts_valid_uuid` — valid UUID passes validation (mock rest of method)

**SEC-02:**
- `test_wiki_write_rejects_path_traversal` — `wiki_write("../etc/passwd", ...)` returns `"Path traversal rejected: ../etc/passwd"`, no file written
- `test_wiki_delete_rejects_path_traversal` — `wiki_delete("../etc/passwd")` returns traversal rejection, no file deleted

**SEC-03 (new file: `commander/tests/test_wiki_server.py`):**
- `test_parse_md_escapes_xss_in_title` — frontmatter `title: <script>alert(1)</script>` → escaped in returned title
- `test_send_404_escapes_xss_in_page_stems` — filesystem page named `<script>` → stems escaped in 404 HTML

**DRY-01 + BUG-02:**
- `test_activate_pm_via_sqlite_writes_audit_log` — after activation, `audit_log` has row with `actor='daemon:pm_activate'`, `action='professional_mode_on'`
- `test_set_pm_via_sqlite_closes_connection_on_error` — force `sqlite3.Error` after connect, verify no connection leak (use `mock_connect` to track close calls)

**BUG-01:**
- `test_call_grader_batch_returns_list` — mock grader response containing `[{...}, {...}]` JSON array, verify `_call_grader(batch=True)` returns a list
- `test_call_grader_non_batch_returns_dict` — same mock, `_call_grader(batch=False)` returns single dict

**DRY-02:**
- `test_worker_commands_imported_in_main` — verify `from ironclaude.main import WORKER_COMMANDS` works and is the same object as `from ironclaude.orchestrator_mcp import WORKER_COMMANDS`

## Implementation Notes

- `Path.is_relative_to()` requires Python 3.9+. The venv is Python 3.11 — confirmed safe.
- `with sqlite3.connect() as conn:` manages transactions only (not lifecycle) in Python's sqlite3 module. Use `try/finally: conn.close()` instead.
- `html.escape()` escapes `<`, `>`, `&`, `"`, `'` — sufficient for both `<title>` context and `href`/text content in `_send_404`.
- The batch array regex `r'\[.*\]'` with `re.DOTALL` is greedy and will match the outermost `[...]`. If multiple arrays appear, it matches the widest span — this is acceptable since the grader response should have only one JSON array.
- After DRY-02, `main.py`'s `WORKER_COMMANDS` import must use the package-relative form `from ironclaude.orchestrator_mcp import WORKER_COMMANDS` (not relative import `from .orchestrator_mcp`) since `main.py` is the entry point.
