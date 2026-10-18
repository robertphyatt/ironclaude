import Database from 'better-sqlite3';
import { ConversationExchange } from './types.js';
import path from 'path';
import fs from 'fs';
import * as sqliteVec from 'sqlite-vec';
import { getDbPath } from './paths.js';

export function migrateSchema(db: Database.Database): void {
  const columns = db.prepare(`SELECT name FROM pragma_table_info('exchanges')`).all() as Array<{ name: string }>;
  const columnNames = new Set(columns.map(c => c.name));

  const migrations: Array<{ name: string; sql: string }> = [
    { name: 'last_indexed', sql: 'ALTER TABLE exchanges ADD COLUMN last_indexed INTEGER' },
    { name: 'parent_uuid', sql: 'ALTER TABLE exchanges ADD COLUMN parent_uuid TEXT' },
    { name: 'is_sidechain', sql: 'ALTER TABLE exchanges ADD COLUMN is_sidechain BOOLEAN DEFAULT 0' },
    { name: 'session_id', sql: 'ALTER TABLE exchanges ADD COLUMN session_id TEXT' },
    { name: 'cwd', sql: 'ALTER TABLE exchanges ADD COLUMN cwd TEXT' },
    { name: 'git_branch', sql: 'ALTER TABLE exchanges ADD COLUMN git_branch TEXT' },
    { name: 'claude_version', sql: 'ALTER TABLE exchanges ADD COLUMN claude_version TEXT' },
    { name: 'thinking_level', sql: 'ALTER TABLE exchanges ADD COLUMN thinking_level TEXT' },
    { name: 'thinking_disabled', sql: 'ALTER TABLE exchanges ADD COLUMN thinking_disabled BOOLEAN' },
    { name: 'thinking_triggers', sql: 'ALTER TABLE exchanges ADD COLUMN thinking_triggers TEXT' },
    { name: 'entrypoint', sql: 'ALTER TABLE exchanges ADD COLUMN entrypoint TEXT' },
    { name: 'session_class', sql: 'ALTER TABLE exchanges ADD COLUMN session_class TEXT' },
  ];

  let migrated = false;
  for (const migration of migrations) {
    if (!columnNames.has(migration.name)) {
      console.log(`Migrating schema: adding ${migration.name} column...`);
      db.prepare(migration.sql).run();
      migrated = true;
    }
  }

  if (migrated) {
    console.log('Migration complete.');
  }
}

/**
 * idx_session_class and idx_archive_path. Building them scans every exchanges row, so
 * initDatabase builds them only for a brand-new file; on an existing index `maintain`
 * builds them under the sync lock (concurrent MCP starts must not race a long build).
 */
export function ensureProvenanceIndexes(db: Database.Database): void {
  db.exec(`CREATE INDEX IF NOT EXISTS idx_session_class ON exchanges(session_class)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_archive_path ON exchanges(archive_path)`);
}

/**
 * Every table and index the index needs. There is deliberately no tool_calls table:
 * it was write-only (nothing in src/ read it) and held ~82% of a 42 GB index. Tool
 * names still reach the embedding text through the callers of insertExchange.
 */
export function applySchema(db: Database.Database, opts: { provenanceIndexes?: boolean } = {}): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS exchanges (
      id TEXT PRIMARY KEY,
      project TEXT NOT NULL,
      timestamp TEXT NOT NULL,
      user_message TEXT NOT NULL,
      assistant_message TEXT NOT NULL,
      archive_path TEXT NOT NULL,
      line_start INTEGER NOT NULL,
      line_end INTEGER NOT NULL,
      embedding BLOB,
      last_indexed INTEGER,
      parent_uuid TEXT,
      is_sidechain BOOLEAN DEFAULT 0,
      session_id TEXT,
      cwd TEXT,
      git_branch TEXT,
      claude_version TEXT,
      thinking_level TEXT,
      thinking_disabled BOOLEAN,
      thinking_triggers TEXT,
      entrypoint TEXT,
      session_class TEXT
    )
  `);

  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS vec_exchanges USING vec0(
      id TEXT PRIMARY KEY,
      embedding FLOAT[384]
    )
  `);

  db.exec(`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`);

  // Run migrations before indexes so every indexed column exists.
  migrateSchema(db);

  db.exec(`CREATE INDEX IF NOT EXISTS idx_timestamp ON exchanges(timestamp DESC)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_session_id ON exchanges(session_id)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_project ON exchanges(project)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_sidechain ON exchanges(is_sidechain)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_git_branch ON exchanges(git_branch)`);
  if (opts.provenanceIndexes) ensureProvenanceIndexes(db);
}

let _db: Database.Database | null = null;

/** Close and forget the cached handle (used after the slim swaps db.sqlite, and by tests). */
export function resetDatabaseCache(): void {
  if (_db && _db.open) _db.close();
  _db = null;
}

export function initDatabase(): Database.Database {
  // Reuse the cached handle only if it is still open. better-sqlite3 exposes a
  // `.open` boolean that flips to false after `.close()`. A closed handle would
  // throw "database is not open" on the next query, so drop it and re-open.
  if (_db && _db.open) return _db;
  _db = null;
  const dbPath = getDbPath();

  const dbDir = path.dirname(dbPath);
  if (!fs.existsSync(dbDir)) {
    fs.mkdirSync(dbDir, { recursive: true });
  }

  const isNewFile = !fs.existsSync(dbPath);
  const db = new Database(dbPath);
  sqliteVec.load(db);
  db.pragma('journal_mode = WAL');
  applySchema(db, { provenanceIndexes: isNewFile });

  _db = db;
  return db;
}

export function tableExists(db: Database.Database, name: string): boolean {
  return !!db.prepare(`SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?`).get(name);
}

export function getMeta(db: Database.Database, key: string): string | null {
  if (!tableExists(db, 'meta')) return null;
  const row = db.prepare(`SELECT value FROM meta WHERE key = ?`).get(key) as { value: string } | undefined;
  return row ? row.value : null;
}

export function setMeta(db: Database.Database, key: string, value: string): void {
  db.exec(`CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)`);
  db.prepare(`INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value`)
    .run(key, value);
}

export function insertExchange(
  db: Database.Database,
  exchange: ConversationExchange,
  embedding: number[],
  _toolNames?: string[]
): void {
  const now = Date.now();

  // One transaction, so an exchanges row never exists without its vec_exchanges row.
  const txn = db.transaction(() => {
    db.prepare(`
      INSERT OR REPLACE INTO exchanges
      (id, project, timestamp, user_message, assistant_message, archive_path, line_start, line_end, last_indexed,
       parent_uuid, is_sidechain, session_id, cwd, git_branch, claude_version,
       thinking_level, thinking_disabled, thinking_triggers, entrypoint, session_class)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      exchange.id,
      exchange.project,
      exchange.timestamp,
      exchange.userMessage,
      exchange.assistantMessage,
      exchange.archivePath,
      exchange.lineStart,
      exchange.lineEnd,
      now,
      exchange.parentUuid || null,
      exchange.isSidechain ? 1 : 0,
      exchange.sessionId || null,
      exchange.cwd || null,
      exchange.gitBranch || null,
      exchange.claudeVersion || null,
      exchange.thinkingLevel || null,
      exchange.thinkingDisabled ? 1 : 0,
      exchange.thinkingTriggers || null,
      exchange.entrypoint || null,
      exchange.sessionClass || null
    );

    // Virtual tables don't support REPLACE: delete, then insert.
    db.prepare(`DELETE FROM vec_exchanges WHERE id = ?`).run(exchange.id);
    db.prepare(`INSERT INTO vec_exchanges (id, embedding) VALUES (?, ?)`)
      .run(exchange.id, Buffer.from(new Float32Array(embedding).buffer));
  });

  txn();
}

export function getAllExchanges(db: Database.Database): Array<{ id: string; archivePath: string }> {
  const stmt = db.prepare(`SELECT id, archive_path as archivePath FROM exchanges`);
  return stmt.all() as Array<{ id: string; archivePath: string }>;
}

export function getFileLastIndexed(db: Database.Database, archivePath: string): number | null {
  const stmt = db.prepare(`
    SELECT MAX(last_indexed) as lastIndexed
    FROM exchanges
    WHERE archive_path = ?
  `);
  const row = stmt.get(archivePath) as { lastIndexed: number | null };
  return row.lastIndexed;
}

export function deleteExchange(db: Database.Database, id: string): void {
  db.prepare(`DELETE FROM vec_exchanges WHERE id = ?`).run(id);
  db.prepare(`DELETE FROM exchanges WHERE id = ?`).run(id);
}
