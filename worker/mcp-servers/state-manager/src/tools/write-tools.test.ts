import { describe, it, expect, beforeEach } from 'vitest';
import Database from 'better-sqlite3';
import { handleWriteTool } from './write-tools.js';

const SESSION_ID = 'test-session-record-review';

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
    CREATE TABLE review_grades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number INTEGER NOT NULL,
      task_ids TEXT NOT NULL,
      grade TEXT NOT NULL,
      task_boundary INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

function setupExecutingSession(db: Database.Database): void {
  db.prepare(`
    INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave, professional_mode)
    VALUES (?, 'executing', 1, 1, 'on')
  `).run(SESSION_ID);
  db.prepare(`
    INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
    VALUES (?, 1, 1, 'Test Task', 'submitted')
  `).run(SESSION_ID);
}

function getReviewPending(db: Database.Database): number {
  const row = db.prepare(
    `SELECT review_pending FROM sessions WHERE terminal_session = ?`,
  ).get(SESSION_ID) as { review_pending: number };
  return row.review_pending;
}

describe('record_review_verdict — review_pending flag clearing', () => {
  let db: Database.Database;

  beforeEach(() => {
    db = createTestDb();
    setupExecutingSession(db);
  });

  it('clears review_pending=0 when grade=A and task_boundary=true', () => {
    const result = handleWriteTool(
      'record_review_verdict',
      { grade: 'A', task_boundary: true },
      db,
      SESSION_ID,
    );
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(getReviewPending(db)).toBe(0);
  });

  it('clears review_pending=0 when grade=B and task_boundary=true', () => {
    const result = handleWriteTool(
      'record_review_verdict',
      { grade: 'B', task_boundary: true },
      db,
      SESSION_ID,
    );
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(getReviewPending(db)).toBe(0);
  });

  it('does NOT clear review_pending when grade=D (failing grade)', () => {
    const result = handleWriteTool(
      'record_review_verdict',
      { grade: 'D', task_boundary: true },
      db,
      SESSION_ID,
    );
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(getReviewPending(db)).toBe(1);
  });

  it('does NOT clear review_pending when task_boundary=false (partial wave)', () => {
    const result = handleWriteTool(
      'record_review_verdict',
      { grade: 'A', task_boundary: false },
      db,
      SESSION_ID,
    );
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(getReviewPending(db)).toBe(1);
  });
});
