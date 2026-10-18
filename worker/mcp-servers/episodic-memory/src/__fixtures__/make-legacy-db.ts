/** Build an index in the pre-lean shape: old exchanges columns, tool_calls + its indexes, vec0. */
import Database from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';

export const LEGACY_ROWS: Array<[string, string, string]> = [
  ['k1', '-Users-alice-Code-app', '2026-10-01T00:00:00.000Z'],
  ['k2', '-Users-alice-Code-app', '2026-10-02T00:00:00.000Z'],
  ['k3', '-Users-alice--ironclaude-brain', '2026-10-03T00:00:00.000Z'],
  ['k4', '-Users-alice-Code-app', '2026-10-04T00:00:00.000Z'],
  ['m1', '-Users-alice--ironclaude-grader', '2026-10-05T00:00:00.000Z'],
  ['m2', '-private-tmp', '2026-10-06T00:00:00.000Z'],
  ['w1', '-Users-alice-Code-rp-knowledge-base-programmatically-control-claude-work-chunk-CHUNK-1', '2026-10-07T00:00:00.000Z'],
];

export function legacyVector(index: number): Buffer {
  const v = new Float32Array(384);
  v[index] = 1;
  v[383] = 0.5;
  return Buffer.from(v.buffer);
}

export function makeLegacyDb(file: string, archiveRoot = '/archive'): void {
  const db = new Database(file);
  sqliteVec.load(db);
  db.pragma('journal_mode = WAL');
  db.exec(`CREATE TABLE exchanges (id TEXT PRIMARY KEY, project TEXT NOT NULL, timestamp TEXT NOT NULL, user_message TEXT NOT NULL, assistant_message TEXT NOT NULL, archive_path TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, embedding BLOB, last_indexed INTEGER, parent_uuid TEXT, is_sidechain BOOLEAN DEFAULT 0, session_id TEXT, cwd TEXT, git_branch TEXT, claude_version TEXT, thinking_level TEXT, thinking_disabled BOOLEAN, thinking_triggers TEXT)`);
  db.exec(`CREATE TABLE tool_calls (id TEXT PRIMARY KEY, exchange_id TEXT NOT NULL, tool_name TEXT NOT NULL, tool_input TEXT, tool_result TEXT, is_error BOOLEAN DEFAULT 0, timestamp TEXT NOT NULL, FOREIGN KEY (exchange_id) REFERENCES exchanges(id))`);
  db.exec(`CREATE INDEX idx_tool_name ON tool_calls(tool_name)`);
  db.exec(`CREATE INDEX idx_tool_exchange ON tool_calls(exchange_id)`);
  db.exec(`CREATE VIRTUAL TABLE vec_exchanges USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[384])`);
  const ins = db.prepare(`INSERT INTO exchanges (id, project, timestamp, user_message, assistant_message, archive_path, line_start, line_end) VALUES (?, ?, ?, ?, ?, ?, 1, 2)`);
  const vec = db.prepare(`INSERT INTO vec_exchanges (id, embedding) VALUES (?, ?)`);
  const tool = db.prepare(`INSERT INTO tool_calls VALUES (?, ?, 'Write', ?, NULL, 0, ?)`);
  LEGACY_ROWS.forEach(([id, project, ts], i) => {
    ins.run(id, project, ts, `user ${id}`, `assistant ${id}`, `${archiveRoot}/${project}/${id}.jsonl`);
    vec.run(id, legacyVector(i));
    for (let t = 0; t < 20; t++) tool.run(`${id}-t${t}`, id, 'x'.repeat(4000), ts);
  });
  db.close();
}
