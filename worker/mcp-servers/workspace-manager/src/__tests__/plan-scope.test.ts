import { describe, it, expect, afterEach, vi } from 'vitest';
import Database from 'better-sqlite3';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readSessionAllowedFiles } from '../plan-scope.js';

const tempDirs: string[] = [];

function seedStateDb(rows: { session: string; allowedFiles: string }[]): string {
  const dir = mkdtempSync(join(tmpdir(), 'ironclaude-planscope-'));
  tempDirs.push(dir);
  const dbFile = join(dir, 'state.db');
  const d = new Database(dbFile);
  d.exec('CREATE TABLE wave_tasks (terminal_session TEXT, allowed_files TEXT)');
  const insert = d.prepare('INSERT INTO wave_tasks (terminal_session, allowed_files) VALUES (?, ?)');
  for (const row of rows) {
    insert.run(row.session, row.allowedFiles);
  }
  d.close();
  return dbFile;
}

afterEach(() => {
  vi.unstubAllEnvs();
  for (const dir of tempDirs.splice(0, tempDirs.length)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe('readSessionAllowedFiles', () => {
  it('returns the sorted, deduped union of allowed_files across a session\'s wave_tasks rows', () => {
    const dbFile = seedStateDb([
      { session: 'S', allowedFiles: '["a.ts","b.ts"]' },
      { session: 'S', allowedFiles: '["b.ts","c.ts"]' },
    ]);
    vi.stubEnv('STATE_MANAGER_DB_PATH', dbFile);

    expect(readSessionAllowedFiles('S')).toEqual(['a.ts', 'b.ts', 'c.ts']);
  });

  it('fails closed when the state DB does not exist', () => {
    vi.stubEnv('STATE_MANAGER_DB_PATH', join(tmpdir(), 'does-not-exist-' + Date.now() + '.db'));

    expect(() => readSessionAllowedFiles('S')).toThrow();
  });

  it('fails closed when the session has no allowed_files rows', () => {
    const dbFile = seedStateDb([
      { session: 'OTHER', allowedFiles: '["a.ts"]' },
    ]);
    vi.stubEnv('STATE_MANAGER_DB_PATH', dbFile);

    expect(() => readSessionAllowedFiles('S')).toThrow();
  });
});
