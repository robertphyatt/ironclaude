import { afterEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { getBlindTierUpReviewForLineage, initDb, migrateSchema } from '../db.js';

describe('plan lineage schema migration', () => {
  const directories: string[] = [];

  afterEach(() => {
    for (const directory of directories.splice(0)) {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it('adds lineage columns without deleting duplicate historical review rows', () => {
    const directory = mkdtempSync(join(tmpdir(), 'ironclaude-lineage-migration-'));
    directories.push(directory);
    const db = new Database(join(directory, 'state.db'));
    db.exec(`
      CREATE TABLE sessions (
        terminal_session TEXT PRIMARY KEY,
        professional_mode TEXT NOT NULL DEFAULT 'undecided',
        workflow_stage TEXT NOT NULL DEFAULT 'idle'
      );
      CREATE TABLE tier_up_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        terminal_session TEXT NOT NULL,
        plan_hash TEXT NOT NULL,
        reviewer_model TEXT NOT NULL,
        verdict TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      INSERT INTO sessions (terminal_session, professional_mode, workflow_stage)
      VALUES ('legacy', 'on', 'final_plan_prep');
      INSERT INTO tier_up_reviews (terminal_session, plan_hash, reviewer_model, verdict)
      VALUES
        ('legacy', 'same-plan', 'gpt-5.6-sol', 'HAS-ISSUES'),
        ('legacy', 'same-plan', 'gpt-5.6-sol', 'SOLID');
    `);

    migrateSchema(db);

    const sessionColumns = db.prepare('PRAGMA table_info(sessions)').all() as Array<{ name: string; dflt_value: string | null }>;
    const reviewColumns = db.prepare('PRAGMA table_info(tier_up_reviews)').all() as Array<{ name: string; dflt_value: string | null }>;
    expect(sessionColumns.find((column) => column.name === 'plan_lineage')).toMatchObject({ dflt_value: '0' });
    expect(reviewColumns.find((column) => column.name === 'plan_lineage')).toMatchObject({ dflt_value: '0' });
    expect(db.prepare('SELECT id, plan_lineage FROM tier_up_reviews ORDER BY id').all()).toEqual([
      { id: 1, plan_lineage: 0 },
      { id: 2, plan_lineage: 0 },
    ]);
    expect(db.prepare("SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_duplicate_blind_tier_up_review'").get())
      .toBeTruthy();

    expect(() => db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('legacy', 0, 'another-plan', 'gpt-5.6-sol', 'SOLID')
    `).run()).toThrow('plan lineage already has a blind review');
    db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('legacy', 1, 'new-lineage-first', 'gpt-5.6-sol', 'SOLID')
    `).run();
    expect(() => db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('legacy', 1, 'new-lineage-second', 'gpt-5.6-sol', 'HAS-ISSUES')
    `).run()).toThrow('plan lineage already has a blind review');
    db.close();

    const reopened = new Database(join(directory, 'state.db'));
    expect(() => reopened.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('legacy', 0, 'after-reopen', 'gpt-5.6-sol', 'top-tier-self')
    `).run()).toThrow('plan lineage already has a blind review');
    reopened.close();
  });

  it.each([
    ['HAS-ISSUES', 'SOLID'],
    ['SOLID', 'HAS-ISSUES'],
  ])('keeps %s as canonical after migration and reopen before %s', (first, second) => {
    const directory = mkdtempSync(join(tmpdir(), 'ironclaude-lineage-canonical-'));
    directories.push(directory);
    const filename = join(directory, 'state.db');
    const db = new Database(filename);
    db.exec(`
      CREATE TABLE sessions (
        terminal_session TEXT PRIMARY KEY,
        professional_mode TEXT NOT NULL DEFAULT 'undecided',
        workflow_stage TEXT NOT NULL DEFAULT 'idle'
      );
      CREATE TABLE tier_up_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        terminal_session TEXT NOT NULL,
        plan_hash TEXT NOT NULL,
        reviewer_model TEXT NOT NULL,
        verdict TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );
      INSERT INTO sessions (terminal_session, professional_mode, workflow_stage)
      VALUES ('legacy-order', 'on', 'final_plan_prep');
    `);
    db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_hash, reviewer_model, verdict)
      VALUES ('legacy-order', 'same-plan', 'gpt-5.6-sol', ?),
             ('legacy-order', 'same-plan', 'gpt-5.6-sol', ?)
    `).run(first, second);
    migrateSchema(db);
    db.close();

    const reopened = new Database(filename);
    expect(getBlindTierUpReviewForLineage(reopened, 'legacy-order', 0)?.verdict).toBe(first);
    reopened.close();
  });

  it('fresh initialization installs a durable trigger that rejects a second blind row', () => {
    const directory = mkdtempSync(join(tmpdir(), 'ironclaude-lineage-fresh-'));
    directories.push(directory);
    const filename = join(directory, 'state.db');
    const db = initDb(filename);
    db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('fresh', 0, 'first', 'gpt-5.6-sol', 'SOLID')
    `).run();
    expect(db.prepare("SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_duplicate_blind_tier_up_review'").get())
      .toBeTruthy();
    expect(() => db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('fresh', 0, 'second', 'gpt-5.6-sol', 'HAS-ISSUES')
    `).run()).toThrow('plan lineage already has a blind review');
    db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('fresh', 1, 'new-lineage-first', 'gpt-5.6-sol', 'SOLID')
    `).run();
    expect(() => db.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('fresh', 1, 'new-lineage-second', 'gpt-5.6-sol', 'HAS-ISSUES')
    `).run()).toThrow('plan lineage already has a blind review');
    db.close();

    const reopened = new Database(filename);
    expect(() => reopened.prepare(`
      INSERT INTO tier_up_reviews (terminal_session, plan_lineage, plan_hash, reviewer_model, verdict)
      VALUES ('fresh', 0, 'third', 'gpt-5.6-sol', 'top-tier-self')
    `).run()).toThrow('plan lineage already has a blind review');
    reopened.close();
  });
});
