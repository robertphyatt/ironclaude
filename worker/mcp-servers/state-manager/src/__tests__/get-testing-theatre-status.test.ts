/**
 * get-testing-theatre-status.test.ts
 *
 * Verifies that get_testing_theatre_status returns the correct
 * testing_theatre_checked value from the sessions table.
 */

import { describe, it, expect } from 'vitest';
import Database from 'better-sqlite3';
import { handleReadTool } from '../tools/read-tools.js';

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
      circuit_breaker INTEGER NOT NULL DEFAULT 0,
      memory_search_required INTEGER NOT NULL DEFAULT 0,
      testing_theatre_checked INTEGER NOT NULL DEFAULT 0,
      project_hash TEXT,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  return db;
}

describe('get_testing_theatre_status', () => {
  it('returns testing_theatre_checked=0 when flag is unset', () => {
    const db = createTestDb();
    db.prepare(`INSERT INTO sessions (terminal_session) VALUES (?)`).run('test-session-1');

    const result = handleReadTool('get_testing_theatre_status', {}, db, 'test-session-1');
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.testing_theatre_checked).toBe(0);
    expect(parsed.session_id).toBe('test-session-1');
  });

  it('returns testing_theatre_checked=1 when flag is set', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, testing_theatre_checked) VALUES (?, ?)`
    ).run('test-session-2', 1);

    const result = handleReadTool('get_testing_theatre_status', {}, db, 'test-session-2');
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.testing_theatre_checked).toBe(1);
    expect(parsed.session_id).toBe('test-session-2');
  });

  it('returns error object when session not found', () => {
    const db = createTestDb();

    const result = handleReadTool('get_testing_theatre_status', {}, db, 'nonexistent-session');
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.error).toBeDefined();
    expect(parsed.session_id).toBe('nonexistent-session');
  });
});
