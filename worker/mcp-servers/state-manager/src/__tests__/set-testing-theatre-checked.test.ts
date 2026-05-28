import { describe, it, expect } from 'vitest';
import Database from 'better-sqlite3';
import { handleWriteTool } from '../tools/write-tools.js';

const SESSION_ID = 'test-session-set-theatre';

function createTestDb(): Database.Database {
  const db = new Database(':memory:');
  db.exec(`
    CREATE TABLE sessions (
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
    CREATE TABLE audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      actor TEXT NOT NULL,
      action TEXT NOT NULL,
      old_value TEXT,
      new_value TEXT,
      context TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  return db;
}

describe('set_testing_theatre_checked', () => {
  it('sets flag from 0 to 1 and returns success', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, testing_theatre_checked) VALUES (?, 0)`
    ).run(SESSION_ID);

    const result = handleWriteTool('set_testing_theatre_checked', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.success).toBe(true);
    expect(parsed.testing_theatre_checked).toBe(1);
    expect(parsed.session_id).toBe(SESSION_ID);

    const row = db
      .prepare(`SELECT testing_theatre_checked FROM sessions WHERE terminal_session = ?`)
      .get(SESSION_ID) as { testing_theatre_checked: number };
    expect(row.testing_theatre_checked).toBe(1);
  });

  it('is idempotent: calling when flag is already 1 returns success', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, testing_theatre_checked) VALUES (?, 1)`
    ).run(SESSION_ID);

    const result = handleWriteTool('set_testing_theatre_checked', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.success).toBe(true);
    expect(parsed.testing_theatre_checked).toBe(1);
  });

  it('returns error when session not found', () => {
    const db = createTestDb();

    const result = handleWriteTool('set_testing_theatre_checked', {}, db, 'nonexistent-session');
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.error).toBeDefined();
    expect(parsed.session_id).toBe('nonexistent-session');
  });
});
