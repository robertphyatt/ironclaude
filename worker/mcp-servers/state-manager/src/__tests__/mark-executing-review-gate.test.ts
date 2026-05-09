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
      review_block_count INTEGER NOT NULL DEFAULT 0,
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

describe('review_block_count resets', () => {
  let db: Database.Database;

  beforeEach(() => {
    db = createTestDb();
  });

  it('submit_task resets review_block_count to 0', () => {
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_block_count, current_wave)
       VALUES ('test-rbc', 'executing', 3, 1)`,
    ).run();
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
       VALUES ('test-rbc', 1, 1, 'Test Task', 'in_progress')`,
    ).run();

    const result = handleWriteTool('submit_task', { task_id: 1 }, db, 'test-rbc');
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();

    const row = db.prepare(
      'SELECT review_block_count FROM sessions WHERE terminal_session = ?',
    ).get('test-rbc') as { review_block_count: number };
    expect(row.review_block_count).toBe(0);
  });

  it('record_review_verdict with passing grade resets review_block_count to 0', () => {
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_block_count, current_wave, review_pending)
       VALUES ('test-rbc', 'reviewing', 4, 1, 1)`,
    ).run();
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
       VALUES ('test-rbc', 1, 1, 'Test Task', 'submitted')`,
    ).run();

    const result = handleWriteTool(
      'record_review_verdict',
      { grade: 'A', task_boundary: true },
      db,
      'test-rbc',
    );
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();

    const row = db.prepare(
      'SELECT review_block_count FROM sessions WHERE terminal_session = ?',
    ).get('test-rbc') as { review_block_count: number };
    expect(row.review_block_count).toBe(0);
  });

  it('record_review_verdict with failing grade does not reset review_block_count', () => {
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_block_count, current_wave, review_pending)
       VALUES ('test-rbc', 'reviewing', 4, 1, 1)`,
    ).run();
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
       VALUES ('test-rbc', 1, 1, 'Test Task', 'submitted')`,
    ).run();

    const result = handleWriteTool(
      'record_review_verdict',
      { grade: 'C', task_boundary: true },
      db,
      'test-rbc',
    );
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();

    const row = db.prepare(
      'SELECT review_block_count FROM sessions WHERE terminal_session = ?',
    ).get('test-rbc') as { review_block_count: number };
    expect(row.review_block_count).toBe(4);
  });

  it('mark_executing resets review_block_count to 0', () => {
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_block_count, current_wave, review_pending)
       VALUES ('test-rbc', 'reviewing', 2, 1, 1)`,
    ).run();
    db.prepare(
      `INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
       VALUES ('test-rbc', 1, '[]', 'A', 1)`,
    ).run();

    const result = handleWriteTool('mark_executing', {}, db, 'test-rbc');
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.workflow_stage).toBe('executing');

    const row = db.prepare(
      'SELECT review_block_count FROM sessions WHERE terminal_session = ?',
    ).get('test-rbc') as { review_block_count: number };
    expect(row.review_block_count).toBe(0);
  });
});
