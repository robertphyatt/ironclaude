/**
 * mark-executing-review-gate.test.ts
 *
 * R10 L2 fix verification: mark_executing must require a passing review verdict
 * (grade A or B) before transitioning from reviewing → executing.
 *
 * TDD approach:
 *   RED  — calls mark_executing without prior record_review_verdict, asserts error
 *   GREEN — calls record_review_verdict(grade='A') first, then mark_executing, asserts success
 */

import { describe, it, expect, beforeEach } from 'vitest';
import Database from 'better-sqlite3';
import { handleWriteTool } from '../tools/write-tools.js';

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

    CREATE TABLE wave_tasks (
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

    CREATE TABLE review_grades (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number      INTEGER NOT NULL,
      task_ids         TEXT NOT NULL,
      grade            TEXT NOT NULL,
      task_boundary    INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE registered_designs (
      design_file TEXT PRIMARY KEY,
      registered_at TEXT NOT NULL DEFAULT (datetime('now')),
      terminal_session TEXT NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0
    );
  `);
  return db;
}

const SESSION_ID = 'test-r10-l2';

describe('mark_executing requires passing review verdict (R10 L2)', () => {
  let db: Database.Database;

  beforeEach(() => {
    db = createTestDb();
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, review_pending)
      VALUES (?, 'reviewing', 1, 1)
    `).run(SESSION_ID);
  });

  it('RED: rejects mark_executing when no review verdict has been recorded', () => {
    const result = handleWriteTool('mark_executing', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toMatch(/no passing review verdict/i);
  });

  it('GREEN: allows mark_executing after record_review_verdict with grade A', () => {
    const verdictResult = handleWriteTool(
      'record_review_verdict',
      { grade: 'A', task_boundary: true },
      db,
      SESSION_ID,
    );
    const verdictParsed = JSON.parse(verdictResult.content[0].text);
    expect(verdictParsed.error).toBeUndefined();
    expect(verdictParsed.grade).toBe('A');

    const result = handleWriteTool('mark_executing', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.workflow_stage).toBe('executing');
  });
});
