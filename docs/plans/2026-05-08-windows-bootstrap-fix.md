# Windows Bootstrap Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix the bootstrap deadlock that prevents ironclaude from working on Windows by making MCP wrappers cross-platform and making session-init.sh self-bootstrapping.

**Architecture:** Two independent fixes: (1) cross-platform `ensureSqlite3()` in both MCP server wrappers using `where.exe` on Windows; (2) full schema bootstrap in `session-init.sh` before the session INSERT so hooks work even when MCP servers haven't started.

**Tech Stack:** Node.js (MCP wrappers), Bash (hooks), SQLite

---

## Task 1: Cross-platform ensureSqlite3() in state-manager wrapper

**Files:**
- Modify: `worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js:28-41`

No tests required: MCP wrapper launcher has no test framework. Verified by code review + Windows acceptance test.

**Step 1: Replace ensureSqlite3() with cross-platform version**

Edit `worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js` lines 28-41. Replace the entire `ensureSqlite3()` function with:

```javascript
function ensureSqlite3() {
  try {
    const cmd = process.platform === 'win32' ? 'where.exe sqlite3' : 'command -v sqlite3';
    execSync(cmd, { stdio: 'pipe' });
  } catch {
    log('[startup] FATAL: sqlite3 CLI not found in PATH.');
    log('[startup] IronClaude hooks require the sqlite3 command-line tool.');
    log('[startup] Install it:');
    log('[startup]   macOS:         brew install sqlite3  (usually pre-installed)');
    log('[startup]   Ubuntu/Debian: sudo apt install sqlite3');
    log('[startup]   Alpine:        apk add sqlite');
    log('[startup]   Fedora/RHEL:   sudo dnf install sqlite');
    log('[startup]   Windows:       choco install sqlite  OR  winget install SQLite.SQLite');
    process.exit(1);
  }
}
```

**Step 2: Verify the change**

Run:
```bash
head -45 worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js
```

Expected: Lines 28-42 show the new `ensureSqlite3()` with `process.platform === 'win32'` check and Windows install hint.

**Step 3: Stage changes**

Run:
```bash
git add worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js
```

Expected: Changes staged.

---

## Task 2: Cross-platform ensureSqlite3() in episodic-memory wrapper

**Files:**
- Modify: `worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js:28-41`

No tests required: MCP wrapper launcher has no test framework. Verified by code review + Windows acceptance test.

**Step 1: Replace ensureSqlite3() with cross-platform version**

Edit `worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js` lines 28-41. Replace the entire `ensureSqlite3()` function with the same cross-platform version:

```javascript
function ensureSqlite3() {
  try {
    const cmd = process.platform === 'win32' ? 'where.exe sqlite3' : 'command -v sqlite3';
    execSync(cmd, { stdio: 'pipe' });
  } catch {
    log('[startup] FATAL: sqlite3 CLI not found in PATH.');
    log('[startup] IronClaude hooks require the sqlite3 command-line tool.');
    log('[startup] Install it:');
    log('[startup]   macOS:         brew install sqlite3  (usually pre-installed)');
    log('[startup]   Ubuntu/Debian: sudo apt install sqlite3');
    log('[startup]   Alpine:        apk add sqlite');
    log('[startup]   Fedora/RHEL:   sudo dnf install sqlite');
    log('[startup]   Windows:       choco install sqlite  OR  winget install SQLite.SQLite');
    process.exit(1);
  }
}
```

**Step 2: Verify the change**

Run:
```bash
head -45 worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js
```

Expected: Lines 28-42 show the new `ensureSqlite3()` with `process.platform === 'win32'` check and Windows install hint.

**Step 3: Stage changes**

Run:
```bash
git add worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js
```

Expected: Changes staged.

---

## Task 3: Self-bootstrapping schema in session-init.sh

**Files:**
- Modify: `worker/hooks/session-init.sh:17-22`

No tests required: Shell hook has no test framework. Verified by code review + Windows acceptance test. Schema SQL verified against db.ts CREATE TABLE statements.

**Step 1: Add schema bootstrap block before session INSERT**

Edit `worker/hooks/session-init.sh`. Insert the following block BETWEEN line 15 (`PROJECT_HASH=$(echo "$PWD" | portable_md5)`) and line 17 (`# Ensure session row exists via direct sqlite3`):

```bash
# ═══ Schema bootstrap (self-bootstrapping for first-run / MCP-not-yet-started) ═══
# Creates all tables + WAL mode so hooks work even before MCP servers finish building.
# Idempotent: CREATE TABLE IF NOT EXISTS is a no-op when MCP server already created schema.
# MAINTENANCE: Keep in sync with worker/mcp-servers/state-manager/src/db.ts initDb().
if command -v sqlite3 &>/dev/null; then
  sqlite3 "$DB_PATH" "
    PRAGMA journal_mode=wal;

    CREATE TABLE IF NOT EXISTS sessions (
      terminal_session TEXT PRIMARY KEY,
      professional_mode TEXT NOT NULL DEFAULT 'undecided',
      workflow_stage TEXT NOT NULL DEFAULT 'idle',
      active_skill TEXT,
      brainstorming_active INTEGER NOT NULL DEFAULT 0,
      plan_name TEXT,
      plan_json TEXT,
      current_wave INTEGER NOT NULL DEFAULT 0,
      review_pending INTEGER NOT NULL DEFAULT 0,
      review_block_count INTEGER NOT NULL DEFAULT 0,
      circuit_breaker INTEGER NOT NULL DEFAULT 0,
      memory_search_required INTEGER NOT NULL DEFAULT 0,
      testing_theatre_checked INTEGER NOT NULL DEFAULT 0,
      project_hash TEXT,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS wave_tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      task_id INTEGER NOT NULL,
      wave_number INTEGER NOT NULL,
      task_name TEXT NOT NULL,
      description TEXT,
      allowed_files TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS plan_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      plan_name TEXT NOT NULL,
      design_file TEXT NOT NULL,
      completed_tasks TEXT,
      total_tasks INTEGER NOT NULL,
      retreat_reason TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      actor TEXT NOT NULL,
      action TEXT NOT NULL,
      old_value TEXT,
      new_value TEXT,
      context TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS registered_designs (
      design_file TEXT PRIMARY KEY,
      registered_at TEXT NOT NULL DEFAULT (datetime('now')),
      terminal_session TEXT NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS subagent_sessions (
      child_session TEXT PRIMARY KEY,
      parent_session TEXT NOT NULL,
      task_number INTEGER,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS pending_subagent_parents (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      parent_session TEXT NOT NULL,
      task_number INTEGER,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS review_grades (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number      INTEGER NOT NULL,
      task_ids         TEXT NOT NULL,
      grade            TEXT NOT NULL,
      task_boundary    INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_wave_tasks_session
      ON wave_tasks(terminal_session);
    CREATE INDEX IF NOT EXISTS idx_wave_tasks_session_wave
      ON wave_tasks(terminal_session, wave_number);
    CREATE INDEX IF NOT EXISTS idx_plan_history_design
      ON plan_history(design_file);
    CREATE INDEX IF NOT EXISTS idx_audit_log_session
      ON audit_log(terminal_session);
    CREATE INDEX IF NOT EXISTS idx_audit_log_created
      ON audit_log(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_subagent_parent
      ON subagent_sessions(parent_session);
  " 2>/dev/null || true
fi
```

**Step 2: Add cross-reference comment to db.ts**

Edit `worker/mcp-servers/state-manager/src/db.ts`. Add a comment before the `db.exec(` call at line 199:

```typescript
  // MAINTENANCE: Schema is also bootstrapped in worker/hooks/session-init.sh
  // for first-run scenarios where hooks fire before MCP servers finish building.
  // Keep both in sync when adding new tables.
  db.exec(`
```

**Step 3: Verify session-init.sh changes**

Run:
```bash
head -130 worker/hooks/session-init.sh
```

Expected: Schema bootstrap block appears between `PROJECT_HASH` line and `# Ensure session row exists` comment, with all 8 CREATE TABLE statements and indexes.

**Step 4: Stage changes**

Run:
```bash
git add worker/hooks/session-init.sh worker/mcp-servers/state-manager/src/db.ts
```

Expected: Changes staged.
