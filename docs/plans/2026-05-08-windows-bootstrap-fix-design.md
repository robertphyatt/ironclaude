# Windows Bootstrap Fix Design

> **Created:** 2026-05-08
> **Status:** Design Complete

## Summary

Fix a bootstrap deadlock that prevents ironclaude from working on Windows. Two independent issues combine to block all tool calls on fresh Windows installs: (1) MCP server wrappers use `command -v sqlite3` which fails in cmd.exe (Node.js default shell on Windows), preventing schema creation; (2) hooks hard-fail because the database has no tables or session rows, since the MCP servers never started. The fix makes the MCP wrappers cross-platform and makes `session-init.sh` self-bootstrapping so hooks work even before MCP servers finish their first-run build.

## Architecture

Two independent, additive changes:

1. **Cross-platform `ensureSqlite3()`** in both MCP server wrappers (`state-manager` and `episodic-memory`). Replace `execSync('command -v sqlite3')` with a platform-conditional check: `where.exe sqlite3` on Windows, `command -v sqlite3` elsewhere. Add Windows install hints to the error message.

2. **Self-bootstrapping schema in `session-init.sh`**. Before the session INSERT, run `PRAGMA journal_mode=wal` + all 8 `CREATE TABLE IF NOT EXISTS` statements + all indexes from `db.ts`. This is idempotent — a no-op when the MCP server has already created the schema. The `migrateSchema()` logic in `db.ts` is unmodified and continues to handle column additions on existing databases.

## Components

| File | Change |
|------|--------|
| `worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js` | Replace `ensureSqlite3()` with cross-platform check (lines 28-41) |
| `worker/mcp-servers/episodic-memory/cli/mcp-server-wrapper.js` | Same cross-platform `ensureSqlite3()` fix (lines 28-41) |
| `worker/hooks/session-init.sh` | Add schema bootstrap block before line 20 (~60 lines) |

`db.ts` is NOT modified. Its `CREATE TABLE IF NOT EXISTS` logic finds pre-existing tables and is a no-op. The migration logic checks column types before migrating, so current-version tables created by the bootstrap don't trigger migrations.

## Error Handling

- **`ensureSqlite3()` failure:** Same `process.exit(1)` behavior with improved error message including Windows install options (`choco install sqlite`, `winget install sqlite.sqlite`).
- **Schema bootstrap failure:** Silent fail (`2>/dev/null || true`). If sqlite3 isn't available in Git Bash PATH, hooks fall through to existing `db_read_or_fail` behavior — no regression.
- **Schema drift:** Cross-reference comments in both `session-init.sh` and `db.ts` remind maintainers to keep schemas in sync. Future column additions are handled by `migrateSchema()` ALTER TABLE logic. New tables must be added to both files.

## Testing Strategy

No automated tests — wrappers and hooks have no test framework. Verification:
1. Code review confirms SQL matches `db.ts` table definitions exactly
2. Idempotency verified by `CREATE TABLE IF NOT EXISTS` semantics
3. Windows acceptance test: deployed Claude instance on Windows will verify after v1.0.7 publish
