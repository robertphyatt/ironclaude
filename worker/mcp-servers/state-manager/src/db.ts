/**
 * Database layer for the State Manager MCP Server.
 *
 * Uses better-sqlite3 with WAL mode. Database lives at ~/.claude/ironclaude.db.
 * Provides CRUD operations for sessions, wave tasks, plan history, audit log,
 * registered designs, and subagent session tracking.
 */

import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';
import os from 'os';
import type {
  Session,
  StateChange,
  WaveTask,
  WaveTaskStatus,
  PlanHistory,
  AuditEntry,
  RegisteredDesign,
  SubagentSession,
  ReviewGradeEntry,
} from './types.js';

// --- Database path ---

function getDbPath(): string {
  if (process.env.STATE_MANAGER_DB_PATH) {
    return process.env.STATE_MANAGER_DB_PATH;
  }
  return path.join(os.homedir(), '.claude', 'ironclaude.db');
}

// --- Schema migration ---

/**
 * Detect if existing DB has old schema (professional_mode INTEGER) and migrate
 * to new schema (professional_mode TEXT). Mapping: 0 -> "undecided", 1 -> "on".
 */
export function migrateSchema(db: Database.Database): void {
  // Check if sessions table exists
  const tableExists = db.prepare(
    `SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'`
  ).get();

  if (!tableExists) {
    return; // No existing table to migrate
  }

  // Check the type of professional_mode column
  const columns = db.prepare(`PRAGMA table_info(sessions)`).all() as Array<{
    name: string;
    type: string;
  }>;

  const profModeCol = columns.find((c) => c.name === 'professional_mode');
  if (!profModeCol) {
    return; // Column doesn't exist yet, will be created by schema
  }

  // If the column type is INTEGER, we need to migrate to TEXT
  if (profModeCol.type === 'INTEGER') {
    console.error('Migrating professional_mode from INTEGER to TEXT...');

    db.exec(`
      ALTER TABLE sessions RENAME TO sessions_old;
    `);

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

    // Migrate data from old schema columns to new schema columns.
    // Old schema has: is_executing_plan, plan_file, current_task, total_tasks,
    //   allowed_files, summary, subagent_circuit_breaker
    // New schema has: workflow_stage, plan_json, current_wave, circuit_breaker
    db.exec(`
      INSERT INTO sessions (
        terminal_session, professional_mode, workflow_stage, active_skill,
        brainstorming_active, plan_name, plan_json, current_wave,
        review_pending, circuit_breaker, project_hash, updated_at
      )
      SELECT
        terminal_session,
        CASE WHEN professional_mode = 1 THEN 'on' ELSE 'undecided' END,
        CASE WHEN is_executing_plan = 1 THEN 'executing' ELSE 'idle' END,
        active_skill,
        COALESCE(brainstorming_active, 0),
        plan_name,
        NULL,
        0,
        COALESCE(review_pending, 0),
        COALESCE(subagent_circuit_breaker, 0),
        project_hash,
        COALESCE(updated_at, datetime('now'))
      FROM sessions_old;
    `);

    db.exec(`DROP TABLE sessions_old;`);

    console.error('Migration complete: professional_mode is now TEXT.');
  }

  // --- Migrate registered_designs (missing consumed column + PRIMARY KEY) ---
  const rdExists = db.prepare(
    `SELECT name FROM sqlite_master WHERE type='table' AND name='registered_designs'`
  ).get();

  if (rdExists) {
    const rdColumns = db.prepare(`PRAGMA table_info(registered_designs)`).all() as Array<{
      name: string;
      type: string;
    }>;
    const hasConsumed = rdColumns.find((c) => c.name === 'consumed');

    if (!hasConsumed) {
      console.error('Migrating registered_designs: adding consumed column + PRIMARY KEY...');
      db.exec(`ALTER TABLE registered_designs RENAME TO registered_designs_old;`);
      db.exec(`
        CREATE TABLE registered_designs (
          design_file TEXT PRIMARY KEY,
          registered_at TEXT NOT NULL DEFAULT (datetime('now')),
          terminal_session TEXT NOT NULL,
          consumed INTEGER NOT NULL DEFAULT 0
        );
      `);
      db.exec(`
        INSERT OR REPLACE INTO registered_designs (design_file, registered_at, terminal_session)
        SELECT design_file, MAX(registered_at), terminal_session
        FROM registered_designs_old GROUP BY design_file;
      `);
      db.exec(`DROP TABLE registered_designs_old;`);
      console.error('Migration complete: registered_designs now has consumed column + PRIMARY KEY.');
    }
  }

  // --- Migrate sessions (missing columns from v1.0.25+) ---
  const sessionsExists2 = db.prepare(
    `SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'`
  ).get();

  if (sessionsExists2) {
    const expectedColumns: Array<{name: string; type: string; dflt: string}> = [
      { name: 'memory_search_required', type: 'INTEGER NOT NULL', dflt: '0' },
      { name: 'testing_theatre_checked', type: 'INTEGER NOT NULL', dflt: '0' },
    ];

    const currentColumns = db.prepare(`PRAGMA table_info(sessions)`).all() as Array<{name: string}>;
    const columnNames = new Set(currentColumns.map(c => c.name));

    for (const col of expectedColumns) {
      if (!columnNames.has(col.name)) {
        db.exec(`ALTER TABLE sessions ADD COLUMN ${col.name} ${col.type} DEFAULT ${col.dflt}`);
        console.error(`Migration: added ${col.name} column to sessions table.`);
      }
    }
  }
}

// --- Database initialization ---

let _db: Database.Database | null = null;

export function initDb(dbPath?: string): Database.Database {
  if (_db) return _db;
  const resolvedPath = dbPath || getDbPath();

  // Ensure directory exists
  const dbDir = path.dirname(resolvedPath);
  if (!fs.existsSync(dbDir)) {
    fs.mkdirSync(dbDir, { recursive: true });
  }

  const db = new Database(resolvedPath, { timeout: 10000 });

  // Enable WAL mode for better concurrency
  db.pragma('journal_mode = WAL');

  // Run migration before creating tables (handles existing DBs)
  migrateSchema(db);

  // Create tables
  db.exec(`
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
  `);

  // Create indexes for common queries
  db.exec(`
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
    CREATE INDEX IF NOT EXISTS idx_pending_parents_session
      ON pending_subagent_parents(parent_session);
    CREATE INDEX IF NOT EXISTS idx_review_grades_session_wave
      ON review_grades(terminal_session, wave_number, task_boundary);
  `);

  _db = db;
  return db;
}

/**
 * Get the singleton database instance. Throws if initDb() hasn't been called.
 */
export function getDb(): Database.Database {
  if (!_db) {
    throw new Error('Database not initialized. Call initDb() first.');
  }
  return _db;
}

/**
 * Run a PASSIVE WAL checkpoint. Only checkpoints if no readers are active —
 * zero risk of blocking. Called after state-changing writes to ensure
 * hook-layer sqlite3 reads see fresh data.
 */
function walCheckpoint(db: Database.Database): void {
  db.pragma('wal_checkpoint(PASSIVE)');
}

// --- State change logging ---

function logStateChange(
  sessionId: string,
  field: string,
  oldValue: string | null | undefined,
  newValue: unknown,
): StateChange | null {
  const prev = oldValue ?? 'null';
  const next = String(newValue ?? 'null');
  if (prev !== next) {
    console.error(`[STATE-CHANGE|${sessionId}] ${field}: ${prev} -> ${next}`);
    return { field, old: prev, new: next };
  }
  return null;
}

// --- Session CRUD ---

export function getSession(db: Database.Database, sessionId: string): Session | undefined {
  const stmt = db.prepare(`SELECT * FROM sessions WHERE terminal_session = ?`);
  return stmt.get(sessionId) as Session | undefined;
}

export function upsertSession(db: Database.Database, session: Partial<Session> & { terminal_session: string }): StateChange[] {
  const existing = getSession(db, session.terminal_session);
  const changes: StateChange[] = [];

  // Log state changes for tracked fields
  if ('workflow_stage' in session) {
    const change = logStateChange(session.terminal_session, 'workflow_stage', existing?.workflow_stage, session.workflow_stage);
    if (change) changes.push(change);
  }
  if ('professional_mode' in session) {
    const change = logStateChange(session.terminal_session, 'professional_mode', existing?.professional_mode, session.professional_mode);
    if (change) changes.push(change);
  }

  if (existing) {
    // Update only provided fields
    const fields: string[] = [];
    const values: unknown[] = [];

    for (const [key, value] of Object.entries(session)) {
      if (key === 'terminal_session') continue;
      fields.push(`${key} = ?`);
      values.push(value);
    }

    // Always update updated_at
    fields.push(`updated_at = datetime('now')`);
    values.push(session.terminal_session);

    const sql = `UPDATE sessions SET ${fields.join(', ')} WHERE terminal_session = ?`;
    db.prepare(sql).run(...values);
  } else {
    const columns = Object.keys(session);
    const placeholders = columns.map(() => '?');
    const values = columns.map((k) => session[k as keyof typeof session]);

    const sql = `INSERT INTO sessions (${columns.join(', ')}) VALUES (${placeholders.join(', ')})`;
    db.prepare(sql).run(...values);
  }

  walCheckpoint(db);
  return changes;
}

export function updateSession(
  db: Database.Database,
  sessionId: string,
  fields: Partial<Omit<Session, 'terminal_session'>>
): StateChange[] {
  const changes: StateChange[] = [];

  // Log state changes for tracked fields
  if ('workflow_stage' in fields || 'professional_mode' in fields) {
    const existing = getSession(db, sessionId);
    if ('workflow_stage' in fields) {
      const change = logStateChange(sessionId, 'workflow_stage', existing?.workflow_stage, fields.workflow_stage);
      if (change) changes.push(change);
    }
    if ('professional_mode' in fields) {
      const change = logStateChange(sessionId, 'professional_mode', existing?.professional_mode, fields.professional_mode);
      if (change) changes.push(change);
    }
  }

  const setClauses: string[] = [];
  const values: unknown[] = [];

  for (const [key, value] of Object.entries(fields)) {
    setClauses.push(`${key} = ?`);
    values.push(value);
  }

  // Always update updated_at
  setClauses.push(`updated_at = datetime('now')`);
  values.push(sessionId);

  const sql = `UPDATE sessions SET ${setClauses.join(', ')} WHERE terminal_session = ?`;
  db.prepare(sql).run(...values);

  walCheckpoint(db);
  return changes;
}

// --- Wave Task CRUD ---

export function getWaveTasks(
  db: Database.Database,
  sessionId: string,
  waveNumber?: number
): WaveTask[] {
  if (waveNumber !== undefined) {
    const stmt = db.prepare(
      `SELECT * FROM wave_tasks WHERE terminal_session = ? AND wave_number = ? ORDER BY task_id`
    );
    return stmt.all(sessionId, waveNumber) as WaveTask[];
  }

  const stmt = db.prepare(
    `SELECT * FROM wave_tasks WHERE terminal_session = ? ORDER BY wave_number, task_id`
  );
  return stmt.all(sessionId) as WaveTask[];
}

export function upsertWaveTask(db: Database.Database, task: Omit<WaveTask, 'id' | 'created_at' | 'updated_at'>): void {
  // Check if task already exists for this session + task_id
  const existing = db.prepare(
    `SELECT id FROM wave_tasks WHERE terminal_session = ? AND task_id = ?`
  ).get(task.terminal_session, task.task_id) as { id: number } | undefined;

  if (existing) {
    db.prepare(`
      UPDATE wave_tasks
      SET wave_number = ?, task_name = ?, description = ?, allowed_files = ?, status = ?, updated_at = datetime('now')
      WHERE id = ?
    `).run(task.wave_number, task.task_name, task.description, task.allowed_files, task.status, existing.id);
  } else {
    db.prepare(`
      INSERT INTO wave_tasks (terminal_session, task_id, wave_number, task_name, description, allowed_files, status)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(
      task.terminal_session,
      task.task_id,
      task.wave_number,
      task.task_name,
      task.description,
      task.allowed_files,
      task.status
    );
  }
  walCheckpoint(db);
}

export function updateWaveTaskStatus(db: Database.Database, rowId: number, status: WaveTaskStatus): void {
  db.prepare(
    `UPDATE wave_tasks SET status = ?, updated_at = datetime('now') WHERE id = ?`
  ).run(status, rowId);
  walCheckpoint(db);
}

// --- Plan History CRUD ---

export function insertPlanHistory(
  db: Database.Database,
  entry: Omit<PlanHistory, 'id' | 'created_at'>
): void {
  db.prepare(`
    INSERT INTO plan_history (terminal_session, plan_name, design_file, completed_tasks, total_tasks, retreat_reason)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(
    entry.terminal_session,
    entry.plan_name,
    entry.design_file,
    entry.completed_tasks,
    entry.total_tasks,
    entry.retreat_reason
  );
}

export function getPlanHistory(db: Database.Database, designFile: string): PlanHistory[] {
  const stmt = db.prepare(
    `SELECT * FROM plan_history WHERE design_file = ? ORDER BY created_at DESC`
  );
  return stmt.all(designFile) as PlanHistory[];
}

// --- Audit Log CRUD ---

export function insertAuditLog(
  db: Database.Database,
  entry: Omit<AuditEntry, 'id' | 'created_at'>
): void {
  db.prepare(`
    INSERT INTO audit_log (terminal_session, actor, action, old_value, new_value, context)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(
    entry.terminal_session,
    entry.actor,
    entry.action,
    entry.old_value,
    entry.new_value,
    entry.context
  );
}

// --- Registered Designs CRUD ---

export function getDesign(db: Database.Database, file: string): RegisteredDesign | undefined {
  const stmt = db.prepare(`SELECT * FROM registered_designs WHERE design_file = ?`);
  return stmt.get(file) as RegisteredDesign | undefined;
}

export function registerDesign(db: Database.Database, file: string, sessionId: string): void {
  db.prepare(`
    INSERT OR REPLACE INTO registered_designs (design_file, terminal_session, consumed)
    VALUES (?, ?, 0)
  `).run(file, sessionId);
  walCheckpoint(db);
}

export function consumeDesign(db: Database.Database, file: string): void {
  db.prepare(`UPDATE registered_designs SET consumed = 1 WHERE design_file = ?`).run(file);
  walCheckpoint(db);
}

export function unconsumeDesign(db: Database.Database, file: string): void {
  db.prepare(`UPDATE registered_designs SET consumed = 0 WHERE design_file = ?`).run(file);
}

export function isDesignConsumed(db: Database.Database, file: string): boolean {
  const row = db.prepare(
    `SELECT consumed FROM registered_designs WHERE design_file = ?`
  ).get(file) as { consumed: number } | undefined;
  return row ? row.consumed === 1 : false;
}

// --- Subagent session helpers ---

export function enqueueSubagent(db: Database.Database, parent: string, taskNum: number | null): void {
  db.prepare(`
    INSERT INTO pending_subagent_parents (parent_session, task_number)
    VALUES (?, ?)
  `).run(parent, taskNum);
}

export function dequeueSubagent(db: Database.Database): { id: number; parent_session: string; task_number: number | null } | undefined {
  const row = db.prepare(
    `SELECT * FROM pending_subagent_parents ORDER BY id ASC LIMIT 1`
  ).get() as { id: number; parent_session: string; task_number: number | null } | undefined;

  if (row) {
    db.prepare(`DELETE FROM pending_subagent_parents WHERE id = ?`).run(row.id);
  }

  return row;
}

export function linkSubagent(db: Database.Database, child: string, parent: string, taskNum?: number | null): void {
  db.prepare(`
    INSERT OR REPLACE INTO subagent_sessions (child_session, parent_session, task_number)
    VALUES (?, ?, ?)
  `).run(child, parent, taskNum ?? null);
}

export function getParentSession(db: Database.Database, child: string): SubagentSession | undefined {
  const stmt = db.prepare(`SELECT * FROM subagent_sessions WHERE child_session = ?`);
  return stmt.get(child) as SubagentSession | undefined;
}

// --- Review Grades CRUD ---

export function insertReviewGrade(
  db: Database.Database,
  sessionId: string,
  waveNumber: number,
  taskIds: number[],
  grade: string,
  taskBoundary: boolean,
): void {
  db.prepare(`
    INSERT INTO review_grades (terminal_session, wave_number, task_ids, grade, task_boundary)
    VALUES (?, ?, ?, ?, ?)
  `).run(sessionId, waveNumber, JSON.stringify(taskIds), grade, taskBoundary ? 1 : 0);
  walCheckpoint(db);
}

export function getLatestReviewGrade(
  db: Database.Database,
  sessionId: string,
  waveNumber: number,
): ReviewGradeEntry | undefined {
  return db.prepare(`
    SELECT * FROM review_grades
    WHERE terminal_session = ? AND wave_number = ? AND task_boundary = 1
    ORDER BY created_at DESC
    LIMIT 1
  `).get(sessionId, waveNumber) as ReviewGradeEntry | undefined;
}

export function clearReviewGrades(db: Database.Database, sessionId: string): void {
  db.prepare(`DELETE FROM review_grades WHERE terminal_session = ?`).run(sessionId);
  walCheckpoint(db);
}
