import Database from 'better-sqlite3';
import os from 'node:os';
import path from 'node:path';

function stateDbPath(): string {
  return process.env.STATE_MANAGER_DB_PATH ?? path.join(os.homedir(), '.claude', 'ironclaude.db');
}

// Reads the union of a session's plan allowed_files from the state-manager DB.
// FAIL-CLOSED: throws on any read failure or empty result — callers must refuse,
// never fall back to committing the whole index (that would silently widen scope).
export function readSessionAllowedFiles(providerRootSessionId: string): string[] {
  let sdb: Database.Database;
  try {
    sdb = new Database(stateDbPath(), { readonly: true, fileMustExist: true, timeout: 10000 });
  } catch (e) {
    throw new Error(`Cannot read plan scope: state DB unreadable (${(e as Error).message})`);
  }
  try {
    const rows = sdb.prepare('SELECT allowed_files FROM wave_tasks WHERE terminal_session = ?')
      .all(providerRootSessionId) as { allowed_files: string | null }[];
    const set = new Set<string>();
    for (const r of rows) {
      if (!r.allowed_files) continue;
      const arr = JSON.parse(r.allowed_files) as unknown;
      if (Array.isArray(arr)) for (const f of arr) if (typeof f === 'string' && f.length > 0) set.add(f);
    }
    if (set.size === 0) {
      throw new Error('Cannot read plan scope: no allowed_files for this session (no active plan)');
    }
    return [...set].sort();
  } finally {
    sdb.close();
  }
}
