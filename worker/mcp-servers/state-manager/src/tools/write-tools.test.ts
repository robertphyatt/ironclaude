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

describe('claim_task — workflow_stage guard', () => {
  let db: Database.Database;
  const SESSION = 'test-session-claim-task';

  beforeEach(() => {
    db = createTestDb();
  });

  it('rejects claim_task when workflow_stage is idle', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode)
      VALUES (?, 'idle', 1, 'on')
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'pending')
    `).run(SESSION);

    const result = handleWriteTool('claim_task', { task_id: 1 }, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain('workflow_stage must be');
    expect(parsed.error).toContain('idle');
  });

  it('rejects claim_task when workflow_stage is brainstorming', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode)
      VALUES (?, 'brainstorming', 1, 'on')
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'pending')
    `).run(SESSION);

    const result = handleWriteTool('claim_task', { task_id: 1 }, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain('workflow_stage must be');
  });

  it('allows claim_task when workflow_stage is executing', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode)
      VALUES (?, 'executing', 1, 'on')
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'pending')
    `).run(SESSION);

    const result = handleWriteTool('claim_task', { task_id: 1 }, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.status).toBe('in_progress');
  });

  it('allows claim_task when workflow_stage is reviewing', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode)
      VALUES (?, 'reviewing', 1, 'on')
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'pending')
    `).run(SESSION);

    const result = handleWriteTool('claim_task', { task_id: 1 }, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.status).toBe('in_progress');
  });
});

describe('mark_executing — state recovery', () => {
  let db: Database.Database;
  const SESSION = 'test-session-mark-executing';

  beforeEach(() => {
    db = createTestDb();
  });

  it('recovers when workflow_stage is idle but active wave_tasks exist', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode, review_pending)
      VALUES (?, 'idle', 1, 'on', 0)
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'pending')
    `).run(SESSION);
    db.prepare(`
      INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
      VALUES (?, 1, '1', 'A', 1)
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.workflow_stage).toBe('executing');
    expect(parsed.recovered).toBe(true);

    const auditEntry = db.prepare(
      `SELECT * FROM audit_log WHERE terminal_session = ? AND action = 'workflow_stage_recovery'`,
    ).get(SESSION) as { actor: string; old_value: string; new_value: string } | undefined;
    expect(auditEntry).toBeDefined();
    expect(auditEntry!.actor).toBe('system:state-correction');
    expect(auditEntry!.old_value).toBe('idle');
    expect(auditEntry!.new_value).toBe('executing');
  });

  it('rejects when workflow_stage is idle and no active wave_tasks exist', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode, review_pending)
      VALUES (?, 'idle', 1, 'on', 0)
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain("workflow_stage must be 'reviewing'");
    expect(parsed.error).toContain('idle');
  });

  it('normal path still works: reviewing with passing review', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode, review_pending)
      VALUES (?, 'reviewing', 1, 'on', 0)
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'review_passed')
    `).run(SESSION);
    db.prepare(`
      INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
      VALUES (?, 1, '1', 'A', 1)
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.workflow_stage).toBe('executing');
    expect(parsed.recovered).toBeUndefined();
  });

  it('recovery still requires passing review grade', () => {
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode, review_pending)
      VALUES (?, 'idle', 1, 'on', 0)
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'pending')
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain('no passing review verdict');
  });
});

describe('mark_executing — task_boundary review gate', () => {
  let db: Database.Database;
  const SESSION = 'test-session-mark-executing-tb';

  beforeEach(() => {
    db = createTestDb();
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, current_wave, professional_mode, review_pending)
      VALUES (?, 'reviewing', 1, 'on', 0)
    `).run(SESSION);
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'review_passed')
    `).run(SESSION);
  });

  it('REJECTS when the only passing grade is informational (task_boundary=0)', () => {
    // A standalone/informational A recorded earlier must NOT satisfy the
    // return-to-executing gate — only a task-boundary review counts.
    db.prepare(`
      INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
      VALUES (?, 1, '1', 'A', 0)
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain('no passing review verdict');
    // Gate rejected: workflow_stage must remain 'reviewing'
    const stage = (db.prepare(
      `SELECT workflow_stage FROM sessions WHERE terminal_session = ?`,
    ).get(SESSION) as { workflow_stage: string }).workflow_stage;
    expect(stage).toBe('reviewing');
  });

  it('ACCEPTS when a passing grade has task_boundary=1', () => {
    db.prepare(`
      INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
      VALUES (?, 1, '1', 'A', 1)
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.workflow_stage).toBe('executing');
  });

  it('REJECTS informational A even when a failing task-boundary grade also exists', () => {
    // Informational A (task_boundary=0) plus a task-boundary C (not A/B):
    // no passing task-boundary grade exists, so the gate must reject.
    db.prepare(`
      INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
      VALUES (?, 1, '1', 'A', 0)
    `).run(SESSION);
    db.prepare(`
      INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
      VALUES (?, 1, '1', 'C', 1)
    `).run(SESSION);

    const result = handleWriteTool('mark_executing', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeDefined();
    expect(parsed.error).toContain('no passing review verdict');
  });
});

describe('clear_stale_review_pending — stale flag detection', () => {
  let db: Database.Database;
  const SESSION = 'test-session-clear-stale';

  beforeEach(() => {
    db = createTestDb();
    db.prepare(`
      INSERT INTO sessions (terminal_session, workflow_stage, review_pending, review_block_count, current_wave, professional_mode)
      VALUES (?, 'executing', 1, 3, 1, 'on')
    `).run(SESSION);
  });

  it('clears stale review_pending when no submitted tasks in current wave', () => {
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'review_passed')
    `).run(SESSION);

    const result = handleWriteTool('clear_stale_review_pending', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.cleared).toBe(true);
    expect(parsed.wave).toBe(1);

    const row = db.prepare(
      `SELECT review_pending, review_block_count FROM sessions WHERE terminal_session = ?`,
    ).get(SESSION) as { review_pending: number; review_block_count: number };
    expect(row.review_pending).toBe(0);
    expect(row.review_block_count).toBe(0);
  });

  it('does not clear when submitted task exists in current wave', () => {
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'submitted')
    `).run(SESSION);

    const result = handleWriteTool('clear_stale_review_pending', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.cleared).toBe(false);
    expect(parsed.reason).toContain('still submitted');

    const row = db.prepare(
      `SELECT review_pending FROM sessions WHERE terminal_session = ?`,
    ).get(SESSION) as { review_pending: number };
    expect(row.review_pending).toBe(1);
  });

  it('does not clear when review_pending is already 0', () => {
    db.prepare(`UPDATE sessions SET review_pending = 0 WHERE terminal_session = ?`).run(SESSION);

    const result = handleWriteTool('clear_stale_review_pending', {}, db, SESSION);
    const parsed = JSON.parse(result.content[0].text);
    expect(parsed.error).toBeUndefined();
    expect(parsed.cleared).toBe(false);
    expect(parsed.reason).toContain('already 0');
  });

  it('writes audit log entry when clearing stale flag', () => {
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, status)
      VALUES (?, 1, 1, 'Test Task', 'review_passed')
    `).run(SESSION);

    handleWriteTool('clear_stale_review_pending', {}, db, SESSION);

    const entry = db.prepare(
      `SELECT * FROM audit_log WHERE terminal_session = ? AND action = 'clear_stale_review_pending'`,
    ).get(SESSION) as { actor: string; old_value: string; new_value: string } | undefined;
    expect(entry).toBeDefined();
    expect(entry!.actor).toBe('system:stale-flag-heal');
    expect(entry!.old_value).toBe('1');
    expect(entry!.new_value).toBe('0');
  });
});
