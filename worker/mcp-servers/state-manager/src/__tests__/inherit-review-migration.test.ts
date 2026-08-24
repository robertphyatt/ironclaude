import { describe, it, expect } from 'vitest';
import Database from 'better-sqlite3';
import { migrateSchema } from '../db.js';

describe('inherit_review migration', () => {
  it('adds inherit_review to a pre-existing sessions table with default 0', () => {
    const db = new Database(':memory:');
    // professional_mode is REQUIRED here: migrateSchema early-returns when it is
    // absent (db.ts), and it must be TEXT (INTEGER triggers a rename-migration path
    // that expects old-schema columns and throws).
    db.exec(`CREATE TABLE sessions (
      terminal_session TEXT PRIMARY KEY,
      professional_mode TEXT NOT NULL DEFAULT 'undecided',
      workflow_stage TEXT NOT NULL DEFAULT 'idle',
      plan_lineage INTEGER NOT NULL DEFAULT 0
    );`);
    db.prepare(`INSERT INTO sessions (terminal_session) VALUES ('s1')`).run();

    migrateSchema(db);

    const cols = db.prepare(`PRAGMA table_info(sessions)`).all() as Array<{ name: string }>;
    expect(cols.some((c) => c.name === 'inherit_review')).toBe(true);
    const row = db.prepare(`SELECT inherit_review FROM sessions WHERE terminal_session = 's1'`).get() as { inherit_review: number };
    expect(row.inherit_review).toBe(0);
  });
});
