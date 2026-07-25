import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { WorkflowStage } from '../types.js';
import {
  handleWriteTool,
  hashPlan,
  writeToolDefinitions,
} from '../tools/write-tools.js';

const SESSION_ID = 'codex-native-thread-id';

type ExpectedNoOp = {
  success: true;
  changed: false;
  from: WorkflowStage;
  to: WorkflowStage;
  session_id: string;
};

const PLAN = {
  name: 'Original plan',
  goal: 'Exercise transition behavior',
  design_file: 'docs/plans/design.md',
  tasks: [
    {
      id: 1,
      name: 'First task',
      description: 'First task description',
      allowed_files: ['src/first.ts'],
      depends_on: [],
      steps: [{ description: 'Do first task' }],
    },
  ],
};

const REVISED_PLAN = {
  ...PLAN,
  name: 'Revised plan',
  tasks: [
    {
      id: 2,
      name: 'Replacement task',
      description: 'Replacement task description',
      allowed_files: ['src/replacement.ts'],
      depends_on: [],
      steps: [{ description: 'Do replacement task' }],
    },
  ],
};

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
    CREATE TABLE review_grades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      wave_number INTEGER NOT NULL,
      task_ids TEXT NOT NULL,
      grade TEXT NOT NULL,
      task_boundary INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE tier_up_reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      terminal_session TEXT NOT NULL,
      plan_hash TEXT NOT NULL,
      reviewer_model TEXT NOT NULL,
      verdict TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

function parseResult(result: ReturnType<typeof handleWriteTool>): Record<string, unknown> {
  return JSON.parse(result.content[0].text) as Record<string, unknown>;
}

function snapshot(db: Database.Database, sessionId: string) {
  return {
    session: db.prepare('SELECT * FROM sessions WHERE terminal_session = ?').get(sessionId),
    designs: db.prepare('SELECT * FROM registered_designs WHERE terminal_session = ? ORDER BY design_file').all(sessionId),
    tasks: db.prepare('SELECT * FROM wave_tasks WHERE terminal_session = ? ORDER BY id').all(sessionId),
    reviews: db.prepare('SELECT * FROM review_grades WHERE terminal_session = ? ORDER BY id').all(sessionId),
    tierUpReviews: db.prepare('SELECT * FROM tier_up_reviews WHERE terminal_session = ? ORDER BY id').all(sessionId),
    history: db.prepare('SELECT * FROM plan_history WHERE terminal_session = ? ORDER BY id').all(sessionId),
    audit: db.prepare('SELECT * FROM audit_log WHERE terminal_session = ? ORDER BY id').all(sessionId),
  };
}

function seedSession(db: Database.Database, stage: WorkflowStage): void {
  const planJson = JSON.stringify(PLAN);
  db.prepare(`
    INSERT INTO sessions (
      terminal_session, professional_mode, workflow_stage, active_skill,
      brainstorming_active, plan_name, plan_json, current_wave,
      review_pending, review_block_count, circuit_breaker,
      memory_search_required, testing_theatre_checked, project_hash, updated_at
    ) VALUES (?, 'on', ?, 'seed-skill', 1, ?, ?, 3, 1, 2, 1, 1, 1, 'project', '2001-02-03 04:05:06')
  `).run(SESSION_ID, stage, PLAN.name, planJson);
  db.prepare(`
    INSERT INTO registered_designs (design_file, registered_at, terminal_session, consumed)
    VALUES (?, '2001-02-03 04:05:06', ?, 1)
  `).run(PLAN.design_file, SESSION_ID);
  db.prepare(`
    INSERT INTO wave_tasks (
      terminal_session, task_id, wave_number, task_name, description,
      allowed_files, status, created_at, updated_at
    ) VALUES (?, 1, 3, 'Seed task', 'seed', '["src/seed.ts"]', 'in_progress',
              '2001-02-03 04:05:06', '2001-02-03 04:05:06')
  `).run(SESSION_ID);
  db.prepare(`
    INSERT INTO review_grades (
      terminal_session, wave_number, task_ids, grade, task_boundary, created_at
    ) VALUES (?, 3, '[1]', 'A', 1, '2001-02-03 04:05:06')
  `).run(SESSION_ID);
  db.prepare(`
    INSERT INTO tier_up_reviews (
      terminal_session, plan_hash, reviewer_model, verdict, created_at
    ) VALUES (?, ?, 'gpt-5.6-sol', 'SOLID', '2001-02-03 04:05:06')
  `).run(SESSION_ID, hashPlan(planJson));
  db.prepare(`
    INSERT INTO plan_history (
      terminal_session, plan_name, design_file, completed_tasks,
      total_tasks, retreat_reason, created_at
    ) VALUES (?, 'Earlier plan', ?, '[9]', 10, 'seed', '2001-02-03 04:05:06')
  `).run(SESSION_ID, PLAN.design_file);
  db.prepare(`
    INSERT INTO audit_log (
      terminal_session, actor, action, old_value, new_value, context, created_at
    ) VALUES (?, 'test', 'seed', 'before', 'after', 'seed', '2001-02-03 04:05:06')
  `).run(SESSION_ID);
}

type TransitionCase = {
  name: string;
  tool: string;
  stage: WorkflowStage;
  target: WorkflowStage;
  args: Record<string, unknown>;
  removeGateEvidence?: 'tier-up' | 'review-grade';
};

const SAME_TARGET_CASES: TransitionCase[] = [
  { name: 'mark_design_ready', tool: 'mark_design_ready', stage: 'design_ready', target: 'design_ready', args: { file: 'docs/plans/must-not-register.md' } },
  { name: 'mark_plan_ready', tool: 'mark_plan_ready', stage: 'plan_ready', target: 'plan_ready', args: {} },
  { name: 'mark_brainstorming', tool: 'mark_brainstorming', stage: 'brainstorming', target: 'brainstorming', args: {} },
  { name: 'mark_debugging', tool: 'mark_debugging', stage: 'debugging', target: 'debugging', args: {} },
  { name: 'start_execution', tool: 'start_execution', stage: 'executing', target: 'executing', args: {}, removeGateEvidence: 'tier-up' },
  { name: 'mark_executing', tool: 'mark_executing', stage: 'executing', target: 'executing', args: {}, removeGateEvidence: 'review-grade' },
  { name: 'retreat to brainstorming', tool: 'retreat', stage: 'brainstorming', target: 'brainstorming', args: { to: 'brainstorming', reason: 'same stage' } },
  { name: 'retreat to debugging', tool: 'retreat', stage: 'debugging', target: 'debugging', args: { to: 'debugging', reason: 'same stage' } },
];

describe('explicit workflow-transition idempotency', () => {
  let db: Database.Database;
  let previousConfigPath: string | undefined;
  let temporaryConfigDirectory: string;

  beforeEach(() => {
    previousConfigPath = process.env.IRONCLAUDE_HOOKS_CONFIG_PATH;
    temporaryConfigDirectory = mkdtempSync(join(tmpdir(), 'ironclaude-transition-test-'));
    process.env.IRONCLAUDE_HOOKS_CONFIG_PATH = join(
      temporaryConfigDirectory,
      'guaranteed-missing-config.json',
    );
    db = createTestDb();
  });

  afterEach(() => {
    db.close();
    if (previousConfigPath === undefined) {
      delete process.env.IRONCLAUDE_HOOKS_CONFIG_PATH;
    } else {
      process.env.IRONCLAUDE_HOOKS_CONFIG_PATH = previousConfigPath;
    }
    rmSync(temporaryConfigDirectory, { recursive: true, force: true });
  });

  it.each(SAME_TARGET_CASES)('$name is an exact, side-effect-free same-target no-op', ({ tool, stage, target, args, removeGateEvidence }) => {
    seedSession(db, stage);
    if (removeGateEvidence === 'tier-up') {
      db.prepare('DELETE FROM tier_up_reviews WHERE terminal_session = ?').run(SESSION_ID);
    }
    if (removeGateEvidence === 'review-grade') {
      db.prepare('DELETE FROM review_grades WHERE terminal_session = ?').run(SESSION_ID);
    }
    const before = snapshot(db, SESSION_ID);

    const result = parseResult(handleWriteTool(tool, args, db, SESSION_ID));

    const expected: ExpectedNoOp = {
      success: true,
      changed: false,
      from: stage,
      to: target,
      session_id: SESSION_ID,
    };
    expect(result).toEqual(expected);
    expect(snapshot(db, SESSION_ID)).toEqual(before);
  });

  const changedCases: Array<TransitionCase & { setup?: (db: Database.Database) => void }> = [
    { name: 'mark_design_ready', tool: 'mark_design_ready', stage: 'brainstorming', target: 'design_ready', args: { file: 'docs/plans/changed-design.md' } },
    { name: 'mark_plan_ready', tool: 'mark_plan_ready', stage: 'design_ready', target: 'plan_ready', args: {} },
    { name: 'mark_brainstorming', tool: 'mark_brainstorming', stage: 'idle', target: 'brainstorming', args: {} },
    { name: 'mark_debugging', tool: 'mark_debugging', stage: 'idle', target: 'debugging', args: {} },
    { name: 'start_execution', tool: 'start_execution', stage: 'final_plan_prep', target: 'executing', args: {} },
    { name: 'mark_executing', tool: 'mark_executing', stage: 'reviewing', target: 'executing', args: {} },
    { name: 'retreat to brainstorming', tool: 'retreat', stage: 'plan_ready', target: 'brainstorming', args: { to: 'brainstorming', reason: 'requirements changed' } },
    { name: 'retreat to debugging', tool: 'retreat', stage: 'plan_ready', target: 'debugging', args: { to: 'debugging', reason: 'investigate failure' } },
  ];

  it.each(changedCases)('$name changes stage and audits exactly once', ({ tool, stage, target, args, setup }) => {
    seedSession(db, stage);
    setup?.(db);
    const beforeAuditCount = (snapshot(db, SESSION_ID).audit as unknown[]).length;

    const result = parseResult(handleWriteTool(tool, args, db, SESSION_ID));

    expect(result.error).toBeUndefined();
    expect(result).toMatchObject({
      success: true,
      changed: true,
      from: stage,
      to: target,
      session_id: SESSION_ID,
    });
    const after = snapshot(db, SESSION_ID);
    expect((after.session as { workflow_stage: string }).workflow_stage).toBe(target);
    expect((after.audit as unknown[]).length).toBe(beforeAuditCount + 1);
  });

  it.each([
    { tool: 'mark_design_ready', args: {}, stage: 'idle' as WorkflowStage },
    { tool: 'mark_plan_ready', args: {}, stage: 'idle' as WorkflowStage },
    { tool: 'start_execution', args: {}, stage: 'idle' as WorkflowStage },
    { tool: 'mark_executing', args: {}, stage: 'idle' as WorkflowStage },
  ])('$tool rejects an invalid different-state transition without mutation', ({ tool, args, stage }) => {
    seedSession(db, stage);
    if (tool === 'mark_executing') {
      db.prepare("UPDATE wave_tasks SET status = 'review_passed' WHERE terminal_session = ?").run(SESSION_ID);
    }
    const before = snapshot(db, SESSION_ID);

    const result = parseResult(handleWriteTool(tool, args, db, SESSION_ID));

    expect(result.error).toBeDefined();
    expect(snapshot(db, SESSION_ID)).toEqual(before);
  });

  it('rolls back stage and audit together when audit insertion fails', () => {
    seedSession(db, 'idle');
    db.exec(`
      CREATE TRIGGER force_transition_audit_failure
      BEFORE INSERT ON audit_log
      WHEN NEW.action = 'mark_debugging'
      BEGIN
        SELECT RAISE(ABORT, 'forced transition audit failure');
      END;
    `);
    const before = snapshot(db, SESSION_ID);

    expect(() => handleWriteTool('mark_debugging', {}, db, SESSION_ID))
      .toThrow(/forced transition audit failure/);

    expect(snapshot(db, SESSION_ID)).toEqual(before);
  });

  it('reloads revised create_plan domain state while remaining final_plan_prep', () => {
    seedSession(db, 'final_plan_prep');

    const result = parseResult(handleWriteTool('create_plan', { plan_json: REVISED_PLAN }, db, SESSION_ID));

    expect(result.error).toBeUndefined();
    const after = snapshot(db, SESSION_ID);
    expect((after.session as { workflow_stage: string }).workflow_stage).toBe('final_plan_prep');
    expect(JSON.parse((after.session as { plan_json: string }).plan_json)).toEqual(REVISED_PLAN);
    expect(after.tasks).toHaveLength(1);
    expect((after.tasks[0] as { task_id: number }).task_id).toBe(2);
    expect((after.audit as Array<{ action: string }>).at(-1)?.action).toBe('create_plan');
  });

  it('reset_session performs domain cleanup while remaining idle', () => {
    seedSession(db, 'idle');

    const result = parseResult(handleWriteTool('reset_session', {}, db, SESSION_ID));

    expect(result.error).toBeUndefined();
    const after = snapshot(db, SESSION_ID);
    expect(after.session).toMatchObject({
      workflow_stage: 'idle',
      plan_json: null,
      plan_name: null,
      current_wave: 0,
      review_pending: 0,
      circuit_breaker: 0,
    });
    expect(after.tasks).toEqual([]);
    expect(after.reviews).toEqual([]);
    expect((after.audit as Array<{ action: string }>).at(-1)?.action).toBe('reset_session');
  });

  it('advertises idempotency for explicit transitions without relabeling compound tools', () => {
    const definitions = new Map(writeToolDefinitions.map((definition) => [definition.name, definition]));
    const explicitTransitions = [
      'mark_design_ready',
      'mark_plan_ready',
      'mark_brainstorming',
      'mark_debugging',
      'start_execution',
      'mark_executing',
      'retreat',
    ];

    for (const name of explicitTransitions) {
      expect(definitions.get(name)?.annotations.idempotentHint, name).toBe(true);
    }
    expect(definitions.get('create_plan')?.annotations.idempotentHint).toBe(false);
    expect(definitions.get('reset_session')?.annotations.idempotentHint).toBe(true);
  });
});
