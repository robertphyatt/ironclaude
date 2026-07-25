import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import Database from 'better-sqlite3';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { handleWriteTool, getTierUpPolicy, hashPlan } from './write-tools.js';

const SESSION_ID = 'test-tier-up';
const PLAN = JSON.stringify({ name: 'P', goal: 'g', design_file: 'd-design.md', tasks: [] });
const CFG = path.join(os.tmpdir(), 'ic-tierup-test-config.json');

// dbPath defaults to ':memory:'. Pass a file path for tests that exercise
// create_plan — it runs a WAL checkpoint inside a transaction, which a raw
// :memory: db (no WAL support) rejects with "database table is locked"; a
// file+WAL db (production's setup) no-ops the PASSIVE checkpoint.
function createDb(dbPath = ':memory:'): Database.Database {
  const db = new Database(dbPath, { timeout: 10000 });
  if (dbPath !== ':memory:') db.pragma('journal_mode = WAL');
  db.exec(`
    CREATE TABLE sessions (
      terminal_session TEXT PRIMARY KEY,
      professional_mode TEXT NOT NULL DEFAULT 'undecided',
      workflow_stage TEXT NOT NULL DEFAULT 'idle',
      active_skill TEXT, brainstorming_active INTEGER NOT NULL DEFAULT 0,
      plan_name TEXT, plan_json TEXT, current_wave INTEGER NOT NULL DEFAULT 0,
      review_pending INTEGER NOT NULL DEFAULT 0, review_block_count INTEGER NOT NULL DEFAULT 0,
      circuit_breaker INTEGER NOT NULL DEFAULT 0, memory_search_required INTEGER NOT NULL DEFAULT 0,
      testing_theatre_checked INTEGER NOT NULL DEFAULT 0, project_hash TEXT,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL,
      actor TEXT NOT NULL, action TEXT NOT NULL, old_value TEXT, new_value TEXT,
      context TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE tier_up_reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL,
      plan_hash TEXT NOT NULL, reviewer_model TEXT NOT NULL, verdict TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE wave_tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL,
      task_id INTEGER NOT NULL, wave_number INTEGER NOT NULL, task_name TEXT NOT NULL, description TEXT,
      allowed_files TEXT, status TEXT NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE review_grades (
      id INTEGER PRIMARY KEY AUTOINCREMENT, terminal_session TEXT NOT NULL,
      wave_number INTEGER NOT NULL, task_ids TEXT NOT NULL, grade TEXT NOT NULL,
      task_boundary INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);
  return db;
}

function seed(db: Database.Database, stage = 'final_plan_prep', plan: string | null = PLAN): void {
  db.prepare(
    `INSERT INTO sessions (terminal_session, workflow_stage, plan_json, current_wave, professional_mode)
     VALUES (?, ?, ?, 1, 'on')`,
  ).run(SESSION_ID, stage, plan);
}

function setPolicy(p: string | null): void {
  process.env.IRONCLAUDE_HOOKS_CONFIG_PATH = CFG;
  if (p === null) { try { fs.rmSync(CFG); } catch { /* absent → fail-secure */ } }
  else fs.writeFileSync(CFG, JSON.stringify({ tier_up_review_policy: p }));
}

function parse(r: { content: Array<{ text: string }> }): any { return JSON.parse(r.content[0].text); }

describe('getTierUpPolicy — fail-secure + enum', () => {
  afterEach(() => { delete process.env.IRONCLAUDE_HOOKS_CONFIG_PATH; try { fs.rmSync(CFG); } catch {} });
  it('missing config → enforced', () => { setPolicy(null); expect(getTierUpPolicy()).toBe('enforced'); });
  it('invalid value → enforced', () => { setPolicy('bogus'); expect(getTierUpPolicy()).toBe('enforced'); });
  it('reads enforced/commander-choice/off', () => {
    setPolicy('commander-choice'); expect(getTierUpPolicy()).toBe('commander-choice');
    setPolicy('off'); expect(getTierUpPolicy()).toBe('off');
    setPolicy('enforced'); expect(getTierUpPolicy()).toBe('enforced');
  });
});

describe('hashPlan — stable + distinct', () => {
  it('same string → same hash; different string → different hash', () => {
    expect(hashPlan(PLAN)).toBe(hashPlan(PLAN));
    expect(hashPlan(PLAN)).not.toBe(hashPlan(PLAN + ' '));
  });
});

describe('submit_tier_up_review', () => {
  let db: Database.Database;
  beforeEach(() => { db = createDb(); });
  afterEach(() => { delete process.env.IRONCLAUDE_HOOKS_CONFIG_PATH; try { fs.rmSync(CFG); } catch {} });

  it('inserts a row keyed to sha256(session.plan_json)', () => {
    seed(db, 'final_plan_prep');
    const r = parse(handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'SOLID' }, db, SESSION_ID));
    expect(r.error).toBeUndefined();
    expect(r.plan_hash).toBe(hashPlan(PLAN));
    const row = db.prepare(`SELECT * FROM tier_up_reviews WHERE terminal_session=?`).get(SESSION_ID) as any;
    expect(row.plan_hash).toBe(hashPlan(PLAN));
    expect(row.reviewer_model).toBe('opus');
    expect(row.verdict).toBe('SOLID');
  });

  it('errors when workflow stage is not final_plan_prep/executing', () => {
    seed(db, 'plan_ready');
    const r = parse(handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'SOLID' }, db, SESSION_ID));
    expect(r.error).toContain('final_plan_prep');
  });

  it('rejects unknown verdicts without inserting review state', () => {
    seed(db, 'final_plan_prep');
    const r = parse(handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'proceed anyway' }, db, SESSION_ID));
    expect(r.error).toContain('verdict');
    const count = db.prepare(`SELECT COUNT(*) AS count FROM tier_up_reviews WHERE terminal_session=?`)
      .get(SESSION_ID) as { count: number };
    expect(count.count).toBe(0);
  });

  it('errors when required args missing', () => {
    seed(db, 'final_plan_prep');
    const r = parse(handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus' } as any, db, SESSION_ID));
    expect(r.error).toBeDefined();
  });
});

describe('start_execution — tier-up gate', () => {
  let db: Database.Database;
  beforeEach(() => { db = createDb(); seed(db, 'final_plan_prep'); });
  afterEach(() => { delete process.env.IRONCLAUDE_HOOKS_CONFIG_PATH; try { fs.rmSync(CFG); } catch {} });

  function stage(): string {
    return (db.prepare(`SELECT workflow_stage FROM sessions WHERE terminal_session=?`).get(SESSION_ID) as any).workflow_stage;
  }

  it('enforced + no review row → BLOCKS, stays final_plan_prep', () => {
    setPolicy('enforced');
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toContain('tier-up review');
    expect(stage()).toBe('final_plan_prep');
  });

  it('enforced + matching review row → advances to executing', () => {
    setPolicy('enforced');
    handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'SOLID' }, db, SESSION_ID);
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toBeUndefined();
    expect(stage()).toBe('executing');
  });

  it('enforced + matching HAS-ISSUES review → BLOCKS', () => {
    setPolicy('enforced');
    handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'HAS-ISSUES' }, db, SESSION_ID);
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toContain('HAS-ISSUES');
    expect(stage()).toBe('final_plan_prep');
  });

  it('enforced + matching top-tier-self review → advances', () => {
    setPolicy('enforced');
    handleWriteTool('submit_tier_up_review', { reviewer_model: 'fable', verdict: 'top-tier-self' }, db, SESSION_ID);
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toBeUndefined();
    expect(stage()).toBe('executing');
  });

  it('enforced + review row for a DIFFERENT plan (hash mismatch) → BLOCKS', () => {
    setPolicy('enforced');
    handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'SOLID' }, db, SESSION_ID);
    db.prepare(`UPDATE sessions SET plan_json=? WHERE terminal_session=?`)
      .run(JSON.stringify({ name: 'P2', goal: 'g2', design_file: 'd-design.md', tasks: [] }), SESSION_ID);
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toContain('tier-up review');
    expect(stage()).toBe('final_plan_prep');
  });

  it('commander-choice + no row → advances (no gate)', () => {
    setPolicy('commander-choice');
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toBeUndefined();
    expect(stage()).toBe('executing');
  });

  it('commander-choice + latest review HAS-ISSUES requires a current-plan pass', () => {
    setPolicy('commander-choice');
    handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'HAS-ISSUES' }, db, SESSION_ID);
    const revised = JSON.stringify({ name: 'P2', goal: 'g2', design_file: 'd-design.md', tasks: [] });
    db.prepare(`UPDATE sessions SET plan_json=? WHERE terminal_session=?`).run(revised, SESSION_ID);

    const blocked = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(blocked.error).toContain('HAS-ISSUES');
    expect(stage()).toBe('final_plan_prep');

    handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'SOLID' }, db, SESSION_ID);
    const passed = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(passed.error).toBeUndefined();
    expect(stage()).toBe('executing');
  });

  it('off + no row → advances (no gate)', () => {
    setPolicy('off');
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toBeUndefined();
    expect(stage()).toBe('executing');
  });

  it('off remains the explicit human override after HAS-ISSUES', () => {
    setPolicy('off');
    handleWriteTool('submit_tier_up_review', { reviewer_model: 'opus', verdict: 'HAS-ISSUES' }, db, SESSION_ID);
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toBeUndefined();
    expect(stage()).toBe('executing');
  });

  it('fail-secure: absent config → enforced → BLOCKS', () => {
    setPolicy(null);
    const r = parse(handleWriteTool('start_execution', {}, db, SESSION_ID));
    expect(r.error).toContain('tier-up review');
    expect(stage()).toBe('final_plan_prep');
  });
});

describe('create_plan — reload after revise (from final_plan_prep)', () => {
  it('reloads a revised plan and rebuilds wave_tasks when already in final_plan_prep', () => {
    // File+WAL db (production-faithful) — create_plan's in-transaction WAL
    // checkpoint cannot run on a raw :memory: db.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ic-createplan-'));
    const db = createDb(path.join(dir, 'test.db'));
    try {
      const planA = { name: 'A', goal: 'g', design_file: 'd-design.md',
        tasks: [{ id: 1, name: 'T1', description: 'x', allowed_files: ['a.ts'], depends_on: [], steps: [] }] };
      db.prepare(`INSERT INTO sessions (terminal_session, workflow_stage, plan_json, current_wave, professional_mode)
                  VALUES (?, 'final_plan_prep', ?, 1, 'on')`).run(SESSION_ID, JSON.stringify(planA));
      const planB = { name: 'B', goal: 'g', design_file: 'd-design.md',
        tasks: [{ id: 1, name: 'T1b', description: 'y', allowed_files: ['b.ts'], depends_on: [], steps: [] }] };
      const r = parse(handleWriteTool('create_plan', { plan_json: planB }, db, SESSION_ID));
      expect(r.error).toBeUndefined();
      const s = db.prepare(`SELECT plan_json, workflow_stage FROM sessions WHERE terminal_session=?`).get(SESSION_ID) as any;
      expect(JSON.parse(s.plan_json).name).toBe('B');
      const wt = db.prepare(`SELECT task_name FROM wave_tasks WHERE terminal_session=?`).all(SESSION_ID) as any[];
      expect(wt.map(t => t.task_name)).toEqual(['T1b']);
    } finally {
      db.close();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
