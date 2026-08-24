import { describe, it, expect } from 'vitest';
import Database from 'better-sqlite3';
import type { WorkflowStage } from '../types.js';
import { handleWriteTool } from '../tools/write-tools.js';

const SESSION_ID = 'codex-native-thread-id';

// Fresh in-memory schema per test (initDb is a cached singleton; unsuitable for
// isolated cases). Mirrors the real schema; sessions carries inherit_review.
function makeDb(): Database.Database {
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
      plan_lineage INTEGER NOT NULL DEFAULT 0,
      inherit_review INTEGER NOT NULL DEFAULT 0,
      circuit_breaker INTEGER NOT NULL DEFAULT 0,
      memory_search_required INTEGER NOT NULL DEFAULT 0,
      testing_theatre_checked INTEGER NOT NULL DEFAULT 0,
      project_hash TEXT,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE registered_designs (
      design_file TEXT PRIMARY KEY,
      registered_at TEXT NOT NULL DEFAULT (datetime('now')),
      terminal_session TEXT NOT NULL,
      consumed INTEGER NOT NULL DEFAULT 0
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
    CREATE TABLE plan_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      plan_name TEXT NOT NULL,
      design_file TEXT NOT NULL,
      completed_tasks TEXT,
      total_tasks INTEGER NOT NULL,
      retreat_reason TEXT,
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

function seed(db: Database.Database, stage: WorkflowStage, planLineage: number): void {
  db.prepare(`
    INSERT INTO sessions (terminal_session, professional_mode, workflow_stage, plan_name, plan_json, plan_lineage)
    VALUES (?, 'on', ?, 'Plan', '{"name":"Plan","design_file":"docs/plans/seed-design.md","tasks":[]}', ?)
  `).run(SESSION_ID, stage, planLineage);
}

function call(db: Database.Database, tool: string, args: Record<string, unknown>): void {
  handleWriteTool(tool, args, db, SESSION_ID);
}

function row(db: Database.Database): { plan_lineage: number; inherit_review: number; workflow_stage: string } {
  return db.prepare('SELECT plan_lineage, inherit_review, workflow_stage FROM sessions WHERE terminal_session = ?')
    .get(SESSION_ID) as { plan_lineage: number; inherit_review: number; workflow_stage: string };
}

describe('effort-scoped review budget', () => {
  it('(a) retreat executing->brainstorming then mark_design_ready keeps plan_lineage (inherits)', () => {
    const db = makeDb();
    seed(db, 'executing', 5);
    call(db, 'retreat', { to: 'brainstorming', reason: 'in-effort fix' });
    expect(row(db).inherit_review).toBe(1); // classified from the mid-effort FROM-stage
    call(db, 'mark_design_ready', { file: 'docs/plans/a-design.md' });
    const r = row(db);
    expect(r.workflow_stage).toBe('design_ready');
    expect(r.plan_lineage).toBe(5); // inherited: NOT incremented
    expect(r.inherit_review).toBe(0); // one-shot cleared
  });

  it('(b) execution_complete->brainstorming then mark_design_ready increments (new effort)', () => {
    const db = makeDb();
    seed(db, 'execution_complete', 5);
    call(db, 'mark_brainstorming', {});
    expect(row(db).inherit_review).toBe(0);
    call(db, 'mark_design_ready', { file: 'docs/plans/b-design.md' });
    expect(row(db).plan_lineage).toBe(6); // new effort: incremented
  });

  it('(c) mark_brainstorming from executing sets inherit_review=1', () => {
    const db = makeDb();
    seed(db, 'executing', 5);
    call(db, 'mark_brainstorming', {});
    expect(row(db).inherit_review).toBe(1);
  });

  it('(d) mid-effort -> debugging -> brainstorming inherits (two-hop hole closed)', () => {
    const db = makeDb();
    seed(db, 'executing', 5);
    call(db, 'mark_debugging', {}); // executing->debugging: FROM=executing (RETREAT_SOURCE) -> 1
    expect(row(db).inherit_review).toBe(1);
    call(db, 'mark_brainstorming', {}); // debugging->brainstorming: FROM=debugging -> unchanged
    expect(row(db).inherit_review).toBe(1);
    call(db, 'mark_design_ready', { file: 'docs/plans/d-design.md' });
    expect(row(db).plan_lineage).toBe(5); // inherited
  });

  it('(e) execution_complete->brainstorming->debugging->brainstorming increments (new effort preserved)', () => {
    const db = makeDb();
    seed(db, 'execution_complete', 5);
    call(db, 'mark_brainstorming', {}); // FROM=execution_complete -> 0
    call(db, 'mark_debugging', {}); // FROM=brainstorming -> unchanged (0)
    call(db, 'mark_brainstorming', {}); // FROM=debugging -> unchanged (0)
    expect(row(db).inherit_review).toBe(0);
    call(db, 'mark_design_ready', { file: 'docs/plans/e-design.md' });
    expect(row(db).plan_lineage).toBe(6); // still a new effort: incremented
  });
});
