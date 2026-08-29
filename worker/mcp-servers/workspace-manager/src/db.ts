import Database from 'better-sqlite3';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type {
  AcquireIntegrationLockInput,
  AcquirePrimaryCheckoutOwnershipInput,
  Assignment,
  AssignmentLifecycle,
  CreateAssignmentInput,
  CreateHumanIntentInput,
  ConsumeHumanIntentInput,
  ConsumeMatchingHumanIntentInput,
  HumanIntent,
  HumanIntentReceipt,
  IssueHumanIntentInput,
  IntegrationLock,
  IntegrationRecord,
  PrimaryCheckoutOwnership,
  RecordIntegrationInput,
} from './types.js';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const TRANSITIONS: Readonly<Record<AssignmentLifecycle, readonly AssignmentLifecycle[]>> = {
  reserved: ['materialized', 'abandoned'],
  materialized: ['active', 'abandoned'],
  active: ['ready_for_integration', 'abandoned'],
  ready_for_integration: ['active', 'integrated', 'abandoned'],
  integrated: ['cleaned', 'active'],
  abandoned: ['cleaned'],
  cleaned: [],
};

function getDbPath(): string {
  if (process.env.WORKSPACE_MANAGER_DB_PATH) return process.env.WORKSPACE_MANAGER_DB_PATH;
  return path.join(os.homedir(), '.claude', 'ironclaude-workspaces.db');
}

function requiredText(value: string, label: string): string {
  if (value.length === 0) throw new Error(`${label} must not be empty`);
  return value;
}

function requiredUuid(value: string, label: string): string {
  if (!UUID_PATTERN.test(value)) throw new Error(`${label} must be a UUID`);
  return value;
}

const WORKSPACE_SENTINEL_PATTERN = /^primary:.+$/;
function requiredIntentWorkspaceRef(value: string, label: string): string {
  if (!UUID_PATTERN.test(value) && !WORKSPACE_SENTINEL_PATTERN.test(value)) {
    throw new Error(`${label} must be a UUID or a primary-checkout sentinel`);
  }
  return value;
}

function canonicalIsoTimestamp(value: string, label: string): string {
  if (value.length === 0 || Number.isNaN(Date.parse(value))) {
    throw new Error(`${label} must be an ISO timestamp`);
  }
  return new Date(value).toISOString();
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('expectedEvidence must be JSON serializable');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`;
  }
  throw new Error('expectedEvidence must be JSON serializable');
}

/** Re-runnable, transactional schema bootstrap for durable workspace state. */
export function migrateSchema(db: Database.Database): void {
  db.transaction(() => {
    db.exec(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS assignments (
        workspace_guid TEXT PRIMARY KEY,
        repository_identity TEXT NOT NULL,
        worktree_path TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_commit TEXT NOT NULL,
        current_head TEXT NOT NULL,
        owner_session_id TEXT,
        worker_id TEXT,
        lifecycle_status TEXT NOT NULL DEFAULT 'reserved'
          CHECK (lifecycle_status IN ('reserved', 'materialized', 'active', 'ready_for_integration', 'integrated', 'abandoned', 'cleaned')),
        integration_target TEXT NOT NULL,
        integrated_commit TEXT,
        recovery_ref TEXT,
        disposition TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS primary_checkout_owners (
        repository_identity TEXT PRIMARY KEY,
        workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid),
        owner_session_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS integration_locks (
        repository_identity TEXT PRIMARY KEY,
        workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid),
        target_ref TEXT NOT NULL,
        expected_target TEXT NOT NULL,
        acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS integration_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_guid TEXT NOT NULL UNIQUE REFERENCES assignments(workspace_guid),
        repository_identity TEXT NOT NULL,
        target_ref TEXT NOT NULL,
        integrated_commit TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
      );

      CREATE TABLE IF NOT EXISTS human_intents (
        intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push')),
        human_channel TEXT NOT NULL,
        provider_root_session_id TEXT NOT NULL,
        repository_identity TEXT NOT NULL,
        workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid),
        expected_evidence TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        nonce TEXT NOT NULL UNIQUE,
        issued_at TEXT NOT NULL DEFAULT (datetime('now')),
        consumed_at TEXT
      );

      CREATE UNIQUE INDEX IF NOT EXISTS active_assignment_owner_repository
        ON assignments(owner_session_id, repository_identity)
        WHERE owner_session_id IS NOT NULL
          AND lifecycle_status NOT IN ('integrated', 'abandoned', 'cleaned');
      CREATE INDEX IF NOT EXISTS assignments_repository_idx ON assignments(repository_identity);
      CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
        ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);

      CREATE TRIGGER IF NOT EXISTS prevent_workspace_guid_mutation
      BEFORE UPDATE OF workspace_guid ON assignments
      WHEN NEW.workspace_guid <> OLD.workspace_guid
      BEGIN
        SELECT RAISE(ABORT, 'workspace GUID is immutable');
      END;

      INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
    `);
  })();

  if (!db.prepare('SELECT 1 FROM schema_migrations WHERE version = 2').get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE human_intents_v2 (
          intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push')),
          human_channel TEXT NOT NULL,
          provider_root_session_id TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          workspace_guid TEXT NOT NULL,
          expected_evidence TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          nonce TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at TEXT
        );
        INSERT INTO human_intents_v2 (intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at)
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents WHERE workspace_guid IS NOT NULL;
        DROP TABLE human_intents;
        ALTER TABLE human_intents_v2 RENAME TO human_intents;
        CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
          ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
      `);
    })();
  }

  if (!db.prepare('SELECT 1 FROM schema_migrations WHERE version = 3').get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE human_intents_v3 (
          intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push', 'reconcile', 'close-out')),
          human_channel TEXT NOT NULL,
          provider_root_session_id TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          workspace_guid TEXT NOT NULL,
          expected_evidence TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          nonce TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at TEXT
        );
        INSERT INTO human_intents_v3 (intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at)
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents;
        DROP TABLE human_intents;
        ALTER TABLE human_intents_v3 RENAME TO human_intents;
        CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
          ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);
      `);
    })();
  }

  if (!db.prepare('SELECT 1 FROM schema_migrations WHERE version = 4').get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE IF NOT EXISTS preserved_work (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          workspace_guid TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          owner_session_id TEXT,
          kind TEXT NOT NULL CHECK (kind IN ('pending-push', 'recovery')),
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS preserved_work_repo_idx ON preserved_work(repository_identity);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);
      `);
    })();
  }

  if (!db.prepare('SELECT 1 FROM schema_migrations WHERE version = 5').get()) {
    db.transaction(() => {
      db.exec(`
        CREATE TABLE human_intents_v5 (
          intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
          operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push', 'reconcile', 'close-out', 'confirm-resolution')),
          human_channel TEXT NOT NULL,
          provider_root_session_id TEXT NOT NULL,
          repository_identity TEXT NOT NULL,
          workspace_guid TEXT NOT NULL,
          expected_evidence TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          nonce TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL DEFAULT (datetime('now')),
          consumed_at TEXT
        );
        INSERT INTO human_intents_v5 (intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at)
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents;
        DROP TABLE human_intents;
        ALTER TABLE human_intents_v5 RENAME TO human_intents;
        CREATE INDEX IF NOT EXISTS human_intents_lookup_idx
          ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
        INSERT OR IGNORE INTO schema_migrations(version) VALUES (5);
      `);
    })();
  }
}

export interface PreservedWorkRow {
  id: number;
  workspace_guid: string;
  repository_identity: string;
  owner_session_id: string | null;
  kind: 'pending-push' | 'recovery';
  payload: string;
}

/**
 * Durably records a carried push obligation or a Case-B recovery snapshot in a table that
 * survives `reuseTerminalAssignment` NULLing the assignments row (C2). Idempotent on an
 * unresolved (workspace_guid, kind, payload) triple so a crash-retry never double-lists.
 */
export function insertPreservedWork(
  db: Database.Database,
  input: { workspaceGuid: string; repositoryIdentity: string; ownerSessionId: string | null; kind: 'pending-push' | 'recovery'; payload: string },
): void {
  const existing = db.prepare(
    'SELECT 1 FROM preserved_work WHERE workspace_guid = ? AND kind = ? AND payload = ? AND resolved_at IS NULL',
  ).get(input.workspaceGuid, input.kind, input.payload);
  if (existing) return;
  db.prepare(
    'INSERT INTO preserved_work (workspace_guid, repository_identity, owner_session_id, kind, payload) VALUES (?, ?, ?, ?, ?)',
  ).run(input.workspaceGuid, input.repositoryIdentity, input.ownerSessionId, input.kind, input.payload);
}

/** Unresolved preserved-work rows for a repository + owning session, oldest first. */
export function listUnresolvedPreservedWork(
  db: Database.Database,
  repositoryIdentity: string,
  ownerSessionId: string | null,
): PreservedWorkRow[] {
  return db.prepare(
    'SELECT id, workspace_guid, repository_identity, owner_session_id, kind, payload FROM preserved_work'
    + ' WHERE repository_identity = ? AND owner_session_id IS ? AND resolved_at IS NULL ORDER BY created_at ASC, id ASC',
  ).all(repositoryIdentity, ownerSessionId) as PreservedWorkRow[];
}

/**
 * Marks matching unresolved preserved-work rows resolved. Scoped by kind (and optionally
 * workspace_guid); an optional predicate does the fine-grained match (e.g. remote + ancestor)
 * so a drain resolves the durable table row directly, not only the assignments-row disposition.
 */
export function resolvePreservedWork(
  db: Database.Database,
  criteria: { workspaceGuid?: string; kind: 'pending-push' | 'recovery'; predicate?: (row: PreservedWorkRow) => boolean },
): void {
  const rows = (criteria.workspaceGuid
    ? db.prepare(
      'SELECT id, workspace_guid, repository_identity, owner_session_id, kind, payload FROM preserved_work'
      + ' WHERE workspace_guid = ? AND kind = ? AND resolved_at IS NULL',
    ).all(criteria.workspaceGuid, criteria.kind)
    : db.prepare(
      'SELECT id, workspace_guid, repository_identity, owner_session_id, kind, payload FROM preserved_work'
      + ' WHERE kind = ? AND resolved_at IS NULL',
    ).all(criteria.kind)) as PreservedWorkRow[];
  const stmt = db.prepare("UPDATE preserved_work SET resolved_at = datetime('now') WHERE id = ?");
  for (const row of rows) {
    if (!criteria.predicate || criteria.predicate(row)) stmt.run(row.id);
  }
}

export function initDb(dbPath?: string): Database.Database {
  const resolvedPath = dbPath || getDbPath();
  if (resolvedPath !== ':memory:') fs.mkdirSync(path.dirname(resolvedPath), { recursive: true });
  const db = new Database(resolvedPath, { timeout: 10000 });
  db.pragma('foreign_keys = ON');
  db.pragma('journal_mode = WAL');
  migrateSchema(db);
  return db;
}

export function getAssignment(db: Database.Database, workspaceGuid: string): Assignment | undefined {
  return db.prepare('SELECT * FROM assignments WHERE workspace_guid = ?').get(workspaceGuid) as Assignment | undefined;
}

export function createAssignment(db: Database.Database, input: CreateAssignmentInput): Assignment {
  const workspaceGuid = requiredUuid(input.workspaceGuid, 'workspaceGuid');
  const ownerSessionId = input.ownerSessionId == null ? null : requiredText(input.ownerSessionId, 'ownerSessionId');
  db.prepare(`
    INSERT INTO assignments (
      workspace_guid, repository_identity, worktree_path, branch, base_commit, current_head,
      owner_session_id, worker_id, integration_target
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    workspaceGuid,
    requiredText(input.repositoryIdentity, 'repositoryIdentity'),
    requiredText(input.worktreePath, 'worktreePath'),
    requiredText(input.branch, 'branch'),
    requiredText(input.baseCommit, 'baseCommit'),
    requiredText(input.currentHead, 'currentHead'),
    ownerSessionId,
    input.workerId == null ? null : requiredText(input.workerId, 'workerId'),
    requiredText(input.integrationTarget, 'integrationTarget'),
  );
  return getAssignment(db, workspaceGuid)!;
}

/**
 * Reset-and-reuses a spent same-GUID row IN PLACE so a re-allocated provider
 * root reclaims its own retired workspace GUID instead of colliding on the
 * assignments PRIMARY KEY. Scoped hard to a terminal cleaned/abandoned row: the
 * WHERE clause refuses an integrated (or any non-terminal) row, and callers gate
 * this behind a proven-gone worktree so no live work is ever overwritten. Clears
 * the prior lifecycle's integration record and recovery/disposition proofs, then
 * re-arms the row at `reserved` for a fresh materialization.
 */
export function reuseTerminalAssignment(db: Database.Database, input: CreateAssignmentInput): Assignment {
  const workspaceGuid = requiredUuid(input.workspaceGuid, 'workspaceGuid');
  const ownerSessionId = input.ownerSessionId == null ? null : requiredText(input.ownerSessionId, 'ownerSessionId');
  db.transaction(() => {
    db.prepare('DELETE FROM integration_records WHERE workspace_guid = ?').run(workspaceGuid);
    const result = db.prepare(`
      UPDATE assignments
      SET repository_identity = ?, worktree_path = ?, branch = ?, base_commit = ?, current_head = ?,
          owner_session_id = ?, worker_id = ?, integration_target = ?,
          integrated_commit = NULL, recovery_ref = NULL, disposition = NULL,
          lifecycle_status = 'reserved', updated_at = datetime('now')
      WHERE workspace_guid = ? AND lifecycle_status = 'cleaned'
    `).run(
      requiredText(input.repositoryIdentity, 'repositoryIdentity'),
      requiredText(input.worktreePath, 'worktreePath'),
      requiredText(input.branch, 'branch'),
      requiredText(input.baseCommit, 'baseCommit'),
      requiredText(input.currentHead, 'currentHead'),
      ownerSessionId,
      input.workerId == null ? null : requiredText(input.workerId, 'workerId'),
      requiredText(input.integrationTarget, 'integrationTarget'),
      workspaceGuid,
    );
    if (result.changes !== 1) throw new Error('Terminal assignment reuse target changed concurrently');
  })();
  return getAssignment(db, workspaceGuid)!;
}

export function bindAssignmentOwner(db: Database.Database, workspaceGuid: string, ownerSessionId: string): Assignment {
  const existing = getAssignment(db, workspaceGuid);
  if (!existing) throw new Error('Assignment not found');
  const owner = requiredText(ownerSessionId, 'ownerSessionId');
  if (existing.owner_session_id === owner) return existing;
  if (existing.owner_session_id !== null) throw new Error('Assignment owner is already bound');
  const result = db.prepare(`
    UPDATE assignments SET owner_session_id = ?, updated_at = datetime('now')
    WHERE workspace_guid = ? AND owner_session_id IS NULL
  `).run(owner, workspaceGuid);
  if (result.changes !== 1) throw new Error('Assignment owner binding changed concurrently');
  return getAssignment(db, workspaceGuid)!;
}

export function transitionAssignment(
  db: Database.Database,
  workspaceGuid: string,
  expectedStatus: AssignmentLifecycle,
  nextStatus: AssignmentLifecycle,
): Assignment {
  if (!TRANSITIONS[expectedStatus].includes(nextStatus)) {
    throw new Error(`Invalid lifecycle transition: ${expectedStatus} -> ${nextStatus}`);
  }
  const result = db.prepare(`
    UPDATE assignments SET lifecycle_status = ?, updated_at = datetime('now')
    WHERE workspace_guid = ? AND lifecycle_status = ?
  `).run(nextStatus, workspaceGuid, expectedStatus);
  if (result.changes !== 1) throw new Error('Assignment lifecycle state changed concurrently or assignment was not found');
  return getAssignment(db, workspaceGuid)!;
}

/** A dead/timed-out primary owner is reclaimable after this bounded idle window. */
const PRIMARY_OWNER_TTL_MINUTES = 60;

/**
 * Reaps a stale primary-checkout owner row so a dead or timed-out session can no
 * longer deadlock commit/finalize/use_primary_checkout with "Primary checkout is
 * already owned". The current owner row is STALE when its owning assignment is
 * missing, is terminal (integrated/abandoned/cleaned), its recorded worktree no
 * longer exists on disk, or the acquisition is older than the bounded TTL. A LIVE
 * owner (active/ready assignment, worktree present, fresh acquisition) is never
 * reaped — exclusivity is the hard invariant. Returns whether a row was deleted.
 *
 * Race safety without a transaction wrapper (which would risk nesting inside a
 * caller's transaction): the DELETE is keyed to the exact row identity just read
 * (repository, workspace, owner, acquired_at). Two concurrent claimants that both
 * observe the same stale row each delete only that row; the repository_identity
 * PRIMARY KEY then admits exactly one re-INSERT, and a loser's retry re-conflicts
 * and re-reaps or throws. A row re-acquired after a reap carries a fresh
 * acquired_at (so the TTL case's keyed DELETE cannot match it) or a live
 * assignment (so it is not stale to begin with); a same-second re-acquire by the
 * same workspace+owner is unreachable here because re-acquisition requires an
 * intervening release+INSERT that this keyed DELETE has not yet performed.
 */
export function reapStalePrimaryOwner(db: Database.Database, repositoryIdentity: string): boolean {
  const owner = db.prepare('SELECT * FROM primary_checkout_owners WHERE repository_identity = ?')
    .get(repositoryIdentity) as PrimaryCheckoutOwnership | undefined;
  if (!owner) return false;
  const assignment = getAssignment(db, owner.workspace_guid);
  const terminal = assignment !== undefined
    && (assignment.lifecycle_status === 'integrated'
      || assignment.lifecycle_status === 'abandoned'
      || assignment.lifecycle_status === 'cleaned');
  const worktreeMissing = assignment !== undefined && !fs.existsSync(assignment.worktree_path);
  const ttlExpired = db.prepare(
    "SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ? AND acquired_at < datetime('now', ?)",
  ).get(repositoryIdentity, `-${PRIMARY_OWNER_TTL_MINUTES} minutes`) !== undefined;
  const stale = assignment === undefined || terminal || worktreeMissing || ttlExpired;
  if (!stale) return false;
  const result = db.prepare(`
    DELETE FROM primary_checkout_owners
    WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ? AND acquired_at = ?
  `).run(repositoryIdentity, owner.workspace_guid, owner.owner_session_id, owner.acquired_at);
  const reclaimed = result.changes === 1;
  // Observability only, protocol-clean: this server's stdout is the MCP stdio
  // transport (see index.ts's StdioServerTransport), so any log must go to
  // stderr via console.error, never console.log/stdout.
  if (reclaimed) {
    console.error('reapStalePrimaryOwner: reclaimed stale primary-checkout owner');
  }
  return reclaimed;
}

export function acquirePrimaryCheckoutOwnership(
  db: Database.Database,
  input: AcquirePrimaryCheckoutOwnershipInput,
): PrimaryCheckoutOwnership {
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== input.repositoryIdentity || assignment.owner_session_id !== input.ownerSessionId) {
    throw new Error('Primary checkout ownership does not match assignment binding');
  }
  try {
    db.prepare(`
      INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
      VALUES (?, ?, ?)
    `).run(input.repositoryIdentity, input.workspaceGuid, input.ownerSessionId);
  } catch (error) {
    const owner = db.prepare('SELECT * FROM primary_checkout_owners WHERE repository_identity = ?')
      .get(input.repositoryIdentity) as PrimaryCheckoutOwnership | undefined;
    if (owner?.workspace_guid === input.workspaceGuid && owner.owner_session_id === input.ownerSessionId) return owner;
    // A dead/timed-out owner is reclaimable: reap it and retry the INSERT exactly
    // once. A second concurrent claimant that won the re-INSERT still holds the
    // repository_identity PRIMARY KEY, so this retry re-conflicts and throws.
    if (reapStalePrimaryOwner(db, input.repositoryIdentity)) {
      try {
        db.prepare(`
          INSERT INTO primary_checkout_owners (repository_identity, workspace_guid, owner_session_id)
          VALUES (?, ?, ?)
        `).run(input.repositoryIdentity, input.workspaceGuid, input.ownerSessionId);
        return db.prepare('SELECT * FROM primary_checkout_owners WHERE repository_identity = ?')
          .get(input.repositoryIdentity) as PrimaryCheckoutOwnership;
      } catch {
        throw new Error('Primary checkout is already owned');
      }
    }
    throw new Error('Primary checkout is already owned');
  }
  return db.prepare('SELECT * FROM primary_checkout_owners WHERE repository_identity = ?')
    .get(input.repositoryIdentity) as PrimaryCheckoutOwnership;
}

export function releasePrimaryCheckoutOwnership(
  db: Database.Database,
  repositoryIdentity: string,
  workspaceGuid: string,
  ownerSessionId: string,
): void {
  const result = db.prepare(`
    DELETE FROM primary_checkout_owners
    WHERE repository_identity = ? AND workspace_guid = ? AND owner_session_id = ?
  `).run(repositoryIdentity, workspaceGuid, ownerSessionId);
  if (result.changes !== 1) throw new Error('Primary checkout ownership was not held by this assignment');
}

export function acquireIntegrationLock(db: Database.Database, input: AcquireIntegrationLockInput): IntegrationLock {
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== input.repositoryIdentity) {
    throw new Error('Integration lock does not match assignment repository');
  }
  try {
    db.prepare(`
      INSERT INTO integration_locks (repository_identity, workspace_guid, target_ref, expected_target)
      VALUES (?, ?, ?, ?)
    `).run(input.repositoryIdentity, input.workspaceGuid, requiredText(input.targetRef, 'targetRef'), requiredText(input.expectedTarget, 'expectedTarget'));
  } catch {
    throw new Error('Integration lock is already held');
  }
  return db.prepare('SELECT * FROM integration_locks WHERE repository_identity = ?')
    .get(input.repositoryIdentity) as IntegrationLock;
}

export function releaseIntegrationLock(db: Database.Database, repositoryIdentity: string, workspaceGuid: string): void {
  const result = db.prepare(`
    DELETE FROM integration_locks WHERE repository_identity = ? AND workspace_guid = ?
  `).run(repositoryIdentity, workspaceGuid);
  if (result.changes !== 1) throw new Error('Integration lock was not held by this assignment');
}

export function deleteIntegrationRecord(db: Database.Database, workspaceGuid: string): void {
  db.prepare('DELETE FROM integration_records WHERE workspace_guid = ?').run(workspaceGuid);
}

export function recordIntegration(db: Database.Database, input: RecordIntegrationInput): IntegrationRecord {
  const assignment = getAssignment(db, input.workspaceGuid);
  if (!assignment || assignment.repository_identity !== input.repositoryIdentity) {
    throw new Error('Integration record does not match assignment repository');
  }
  const result = db.prepare(`
    INSERT INTO integration_records (workspace_guid, repository_identity, target_ref, integrated_commit)
    VALUES (?, ?, ?, ?)
  `).run(input.workspaceGuid, input.repositoryIdentity, requiredText(input.targetRef, 'targetRef'), requiredText(input.integratedCommit, 'integratedCommit'));
  return db.prepare('SELECT * FROM integration_records WHERE id = ?').get(result.lastInsertRowid) as IntegrationRecord;
}

export function createHumanIntent(db: Database.Database, input: CreateHumanIntentInput): HumanIntent {
  const expiresAt = canonicalIsoTimestamp(input.expiresAt, 'expiresAt');
  const result = db.prepare(`
    INSERT INTO human_intents (
      operation, human_channel, provider_root_session_id, repository_identity,
      workspace_guid, expected_evidence, expires_at, nonce
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    input.operation,
    requiredText(input.humanChannel, 'humanChannel'),
    requiredText(input.providerRootSessionId, 'providerRootSessionId'),
    requiredText(input.repositoryIdentity, 'repositoryIdentity'),
    requiredIntentWorkspaceRef(input.workspaceGuid, 'workspaceGuid'),
    canonicalJson(input.expectedEvidence),
    expiresAt,
    requiredText(input.nonce, 'nonce'),
  );
  return db.prepare('SELECT * FROM human_intents WHERE intent_id = ?').get(result.lastInsertRowid) as HumanIntent;
}

/**
 * Trusted UserPromptSubmit issuance boundary. Nonce, expiry, and evidence stay
 * in the server-owned database; the caller receives only a non-authorizing
 * receipt. A repeated exact command supersedes any still-pending equivalent
 * intent so public consumers never face an ambiguous replay set.
 */
export function issueHumanIntent(
  db: Database.Database,
  input: IssueHumanIntentInput,
  clock: () => Date = () => new Date(),
  nonceFactory: () => string = randomUUID,
): HumanIntentReceipt {
  const issuedAt = canonicalIsoTimestamp(clock().toISOString(), 'server clock');
  const expiresAt = canonicalIsoTimestamp(new Date(Date.parse(issuedAt) + 5 * 60 * 1000).toISOString(), 'expiresAt');
  return db.transaction(() => {
    db.prepare(`
      UPDATE human_intents SET consumed_at = ?
      WHERE operation = ?
        AND human_channel = ?
        AND provider_root_session_id = ?
        AND repository_identity = ?
        AND workspace_guid = ?
        AND consumed_at IS NULL
    `).run(
      issuedAt,
      input.operation,
      input.humanChannel,
      input.providerRootSessionId,
      input.repositoryIdentity,
      input.workspaceGuid,
    );
    createHumanIntent(db, {
      ...input,
      expiresAt,
      nonce: requiredText(nonceFactory(), 'nonce'),
    });
    return { issued: true as const, operation: input.operation };
  })();
}

/**
 * Atomically matches every authorization binding and marks an intent consumed.
 * The optional clock is an internal test seam; callers cannot supply a time.
 */
export function consumeHumanIntent(
  db: Database.Database,
  input: ConsumeHumanIntentInput,
  clock: () => Date = () => new Date(),
): HumanIntent | undefined {
  const evidence = canonicalJson(input.expectedEvidence);
  const now = canonicalIsoTimestamp(clock().toISOString(), 'server clock');
  return db.transaction(() => {
    const result = db.prepare(`
      UPDATE human_intents SET consumed_at = ?
      WHERE operation = ?
        AND human_channel = ?
        AND provider_root_session_id = ?
        AND repository_identity = ?
        AND workspace_guid = ?
        AND expected_evidence = ?
        AND nonce = ?
        AND consumed_at IS NULL
        AND expires_at > ?
    `).run(
      now,
      input.operation,
      input.humanChannel,
      input.providerRootSessionId,
      input.repositoryIdentity,
      input.workspaceGuid,
      evidence,
      input.nonce,
      now,
    );
    if (result.changes !== 1) return undefined;
    return db.prepare('SELECT * FROM human_intents WHERE nonce = ?').get(input.nonce) as HumanIntent;
  })();
}

/** Atomically consumes the newest exact pending intent without exposing nonce. */
export function consumeMatchingHumanIntent(
  db: Database.Database,
  input: ConsumeMatchingHumanIntentInput,
  clock: () => Date = () => new Date(),
): HumanIntent | undefined {
  const evidence = canonicalJson(input.expectedEvidence);
  const now = canonicalIsoTimestamp(clock().toISOString(), 'server clock');
  return db.transaction(() => {
    const candidate = db.prepare(`
      SELECT intent_id FROM human_intents
      WHERE operation = ?
        AND human_channel = ?
        AND provider_root_session_id = ?
        AND repository_identity = ?
        AND workspace_guid = ?
        AND expected_evidence = ?
        AND consumed_at IS NULL
        AND expires_at > ?
      ORDER BY intent_id DESC
      LIMIT 1
    `).get(
      input.operation,
      input.humanChannel,
      input.providerRootSessionId,
      input.repositoryIdentity,
      input.workspaceGuid,
      evidence,
      now,
    ) as { intent_id: number } | undefined;
    if (!candidate) return undefined;
    const result = db.prepare(`
      UPDATE human_intents SET consumed_at = ?
      WHERE intent_id = ? AND consumed_at IS NULL AND expires_at > ?
    `).run(now, candidate.intent_id, now);
    if (result.changes !== 1) return undefined;
    return db.prepare('SELECT * FROM human_intents WHERE intent_id = ?').get(candidate.intent_id) as HumanIntent;
  })();
}
