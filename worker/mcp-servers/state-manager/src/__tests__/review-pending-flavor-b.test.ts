import { describe, it, expect } from 'vitest';
import Database from 'better-sqlite3';
import { handleWriteTool } from '../tools/write-tools.js';

const SESSION_ID = 'test-session-flavor-b';

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
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number INTEGER NOT NULL,
      task_ids TEXT NOT NULL,
      grade TEXT NOT NULL,
      task_boundary INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  return db;
}

describe('review_pending Flavor B — clear_stale_review_pending', () => {
  it('clears review_pending when 0 submitted tasks in current wave', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave) VALUES (?, 'executing', 1, 1)`
    ).run(SESSION_ID);
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES (?, 1, 1, 'test task', 'in_progress')`
    ).run(SESSION_ID);

    const result = handleWriteTool('clear_stale_review_pending', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.cleared).toBe(true);
    expect(parsed.wave).toBe(1);

    const session = db.prepare(`SELECT review_pending, review_block_count FROM sessions WHERE terminal_session = ?`).get(SESSION_ID) as { review_pending: number; review_block_count: number };
    expect(session.review_pending).toBe(0);
    expect(session.review_block_count).toBe(0);
  });

  it('does NOT clear review_pending when submitted tasks exist', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave) VALUES (?, 'executing', 1, 1)`
    ).run(SESSION_ID);
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES (?, 1, 1, 'test task', 'submitted')`
    ).run(SESSION_ID);

    const result = handleWriteTool('clear_stale_review_pending', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.cleared).toBe(false);
    expect(parsed.reason).toContain('submitted');

    const session = db.prepare(`SELECT review_pending FROM sessions WHERE terminal_session = ?`).get(SESSION_ID) as { review_pending: number };
    expect(session.review_pending).toBe(1);
  });

  it('returns cleared=false when review_pending already 0', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave) VALUES (?, 'executing', 0, 1)`
    ).run(SESSION_ID);

    const result = handleWriteTool('clear_stale_review_pending', {}, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.cleared).toBe(false);
    expect(parsed.reason).toContain('already 0');
  });
});

describe('review_pending Flavor B — submit_task sets flag', () => {
  it('submit_task is the sole path that sets review_pending=1', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave, professional_mode) VALUES (?, 'executing', 0, 1, 'on')`
    ).run(SESSION_ID);
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES (?, 1, 1, 'test task', 'in_progress')`
    ).run(SESSION_ID);

    const result = handleWriteTool('submit_task', { task_id: 1 }, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.success).toBe(true);
    expect(parsed.review_pending).toBe(true);

    const session = db.prepare(`SELECT review_pending FROM sessions WHERE terminal_session = ?`).get(SESSION_ID) as { review_pending: number };
    expect(session.review_pending).toBe(1);
  });
});

describe('review_pending Flavor B — record_review_verdict clears flag', () => {
  it('clears review_pending on passing grade with task_boundary', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave, professional_mode) VALUES (?, 'executing', 1, 1, 'on')`
    ).run(SESSION_ID);
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES (?, 1, 1, 'test task', 'submitted')`
    ).run(SESSION_ID);

    const result = handleWriteTool('record_review_verdict', { grade: 'A', task_boundary: true }, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.success).toBe(true);
    expect(parsed.grade).toBe('A');
    expect(parsed.advanced_count).toBe(1);

    const session = db.prepare(`SELECT review_pending FROM sessions WHERE terminal_session = ?`).get(SESSION_ID) as { review_pending: number };
    expect(session.review_pending).toBe(0);
  });

  it('does NOT clear review_pending on failing grade', () => {
    const db = createTestDb();
    db.prepare(
      `INSERT INTO sessions (terminal_session, workflow_stage, review_pending, current_wave, professional_mode) VALUES (?, 'executing', 1, 1, 'on')`
    ).run(SESSION_ID);
    db.prepare(
      `INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status) VALUES (?, 1, 1, 'test task', 'submitted')`
    ).run(SESSION_ID);

    const result = handleWriteTool('record_review_verdict', { grade: 'D', task_boundary: true }, db, SESSION_ID);
    const parsed = JSON.parse(result.content[0].text);

    expect(parsed.success).toBe(true);

    const session = db.prepare(`SELECT review_pending FROM sessions WHERE terminal_session = ?`).get(SESSION_ID) as { review_pending: number };
    expect(session.review_pending).toBe(1);
  });
});
