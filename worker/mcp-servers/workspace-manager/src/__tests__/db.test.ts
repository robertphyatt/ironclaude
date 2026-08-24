import { afterEach, describe, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  acquireIntegrationLock,
  acquirePrimaryCheckoutOwnership,
  bindAssignmentOwner,
  consumeMatchingHumanIntent,
  consumeHumanIntent,
  createAssignment,
  createHumanIntent,
  deleteIntegrationRecord,
  getAssignment,
  initDb,
  issueHumanIntent,
  migrateSchema,
  reapStalePrimaryOwner,
  recordIntegration,
  releaseIntegrationLock,
  releasePrimaryCheckoutOwnership,
  transitionAssignment,
} from '../db.js';
import { resolveSessionIdentity } from '../session-identity.js';
import type { HumanIntentOperation } from '../types.js';

const REPOSITORY = 'local:/repos/ironclaude/.git';
const OWNER = '019f7742-abd8-7c62-af7b-fe07189f1ffd';
const OTHER_OWNER = '019f7cdf-023c-74e0-9ead-9c155636885d';
const GUID_ONE = '11111111-1111-4111-8111-111111111111';
const GUID_TWO = '22222222-2222-4222-8222-222222222222';

describe('workspace assignment store', () => {
  const directories: string[] = [];
  const databases: Database.Database[] = [];

  afterEach(() => {
    for (const db of databases.splice(0)) db.close();
    for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
  });

  function db(): Database.Database {
    const directory = mkdtempSync(join(tmpdir(), 'ironclaude-workspace-manager-'));
    directories.push(directory);
    const database = initDb(join(directory, 'workspace.db'));
    databases.push(database);
    return database;
  }

  /** A real on-disk directory usable as a live owner's worktree_path. */
  function liveWorktree(): string {
    const worktree = mkdtempSync(join(tmpdir(), 'ironclaude-owner-worktree-'));
    directories.push(worktree);
    return worktree;
  }

  function assignment(database: Database.Database, workspaceGuid = GUID_ONE) {
    return createAssignment(database, {
      workspaceGuid,
      repositoryIdentity: REPOSITORY,
      worktreePath: `/repos/ironclaude/.ironclaude/worktrees/${workspaceGuid}`,
      branch: `ironclaude/${workspaceGuid}`,
      baseCommit: 'a'.repeat(40),
      currentHead: 'a'.repeat(40),
      integrationTarget: 'main',
    });
  }

  it('enables WAL and applies schema migrations safely on replay', () => {
    const database = db();
    expect(database.pragma('journal_mode', { simple: true })).toBe('wal');
    assignment(database);
    migrateSchema(database);
    migrateSchema(database);
    expect(getAssignment(database, GUID_ONE)).toMatchObject({ workspace_guid: GUID_ONE });
    expect(database.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'human_intents'").get()).toBeTruthy();
  });

  it('keeps workspace GUID identity immutable and validates GUIDs', () => {
    const database = db();
    expect(() => assignment(database, 'not-a-guid')).toThrow('workspaceGuid must be a UUID');
    assignment(database);
    expect(() => database.prepare('UPDATE assignments SET workspace_guid = ? WHERE workspace_guid = ?')
      .run(GUID_TWO, GUID_ONE)).toThrow('workspace GUID is immutable');
    expect(getAssignment(database, GUID_ONE)?.workspace_guid).toBe(GUID_ONE);
  });

  it('permits one active assignment per provider root and repository', () => {
    const database = db();
    assignment(database, GUID_ONE);
    assignment(database, GUID_TWO);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    expect(() => bindAssignmentOwner(database, GUID_TWO, OWNER)).toThrow('UNIQUE constraint failed');
    bindAssignmentOwner(database, GUID_TWO, OTHER_OWNER);
    expect(getAssignment(database, GUID_TWO)?.owner_session_id).toBe(OTHER_OWNER);
  });

  it('allows only durable lifecycle transitions', () => {
    const database = db();
    assignment(database);
    expect(() => transitionAssignment(database, GUID_ONE, 'reserved', 'active')).toThrow('Invalid lifecycle transition');
    expect(transitionAssignment(database, GUID_ONE, 'reserved', 'materialized').lifecycle_status).toBe('materialized');
    transitionAssignment(database, GUID_ONE, 'materialized', 'active');
    transitionAssignment(database, GUID_ONE, 'active', 'ready_for_integration');
    expect(transitionAssignment(database, GUID_ONE, 'ready_for_integration', 'active').lifecycle_status).toBe('active');
    transitionAssignment(database, GUID_ONE, 'active', 'ready_for_integration');
    transitionAssignment(database, GUID_ONE, 'ready_for_integration', 'integrated');
    expect(transitionAssignment(database, GUID_ONE, 'integrated', 'cleaned').lifecycle_status).toBe('cleaned');
    expect(() => transitionAssignment(database, GUID_ONE, 'cleaned', 'active')).toThrow('Invalid lifecycle transition');
  });

  it('serializes primary checkout and integration ownership and records integration', () => {
    const database = db();
    assignment(database);
    // A live primary owner must survive stale-owner reaping, so this fixture owner is
    // genuinely live: active lifecycle, a worktree that exists on disk, and (below) a
    // fresh acquisition. Without this the placeholder worktree_path would read as a
    // stale (missing-worktree) owner and the exclusivity assertion below would flip.
    database.prepare("UPDATE assignments SET worktree_path = ?, lifecycle_status = 'active' WHERE workspace_guid = ?")
      .run(liveWorktree(), GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    expect(acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    }).workspace_guid).toBe(GUID_ONE);
    assignment(database, GUID_TWO);
    bindAssignmentOwner(database, GUID_TWO, OTHER_OWNER);
    expect(() => acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_TWO, ownerSessionId: OTHER_OWNER,
    })).toThrow('Primary checkout is already owned');
    releasePrimaryCheckoutOwnership(database, REPOSITORY, GUID_ONE, OWNER);

    expect(acquireIntegrationLock(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, targetRef: 'refs/heads/main', expectedTarget: 'a'.repeat(40),
    }).workspace_guid).toBe(GUID_ONE);
    expect(() => acquireIntegrationLock(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, targetRef: 'refs/heads/main', expectedTarget: 'a'.repeat(40),
    })).toThrow('Integration lock is already held');
    releaseIntegrationLock(database, REPOSITORY, GUID_ONE);
    expect(recordIntegration(database, {
      workspaceGuid: GUID_ONE, repositoryIdentity: REPOSITORY, targetRef: 'refs/heads/main', integratedCommit: 'b'.repeat(40),
    }).integrated_commit).toBe('b'.repeat(40));
  });

  it('deletes an integration record by workspace guid, and is a no-op when none exists', () => {
    const database = db();
    assignment(database);
    recordIntegration(database, {
      workspaceGuid: GUID_ONE, repositoryIdentity: REPOSITORY, targetRef: 'refs/heads/main', integratedCommit: 'c'.repeat(40),
    });
    expect(database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?').get(GUID_ONE)).toBeTruthy();
    deleteIntegrationRecord(database, GUID_ONE);
    expect(database.prepare('SELECT 1 FROM integration_records WHERE workspace_guid = ?').get(GUID_ONE)).toBeUndefined();
    expect(() => deleteIntegrationRecord(database, GUID_ONE)).not.toThrow();
  });

  it('reports whether it reaped and never removes a live owner', () => {
    const database = db();
    expect(reapStalePrimaryOwner(database, REPOSITORY)).toBe(false); // no owner row
    assignment(database, GUID_ONE);
    database.prepare("UPDATE assignments SET worktree_path = ?, lifecycle_status = 'active' WHERE workspace_guid = ?")
      .run(liveWorktree(), GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    });
    expect(reapStalePrimaryOwner(database, REPOSITORY)).toBe(false); // live: not reaped
    database.prepare("UPDATE assignments SET lifecycle_status = 'cleaned' WHERE workspace_guid = ?").run(GUID_ONE);
    expect(reapStalePrimaryOwner(database, REPOSITORY)).toBe(true); // terminal: reaped
    expect(database.prepare('SELECT 1 FROM primary_checkout_owners WHERE repository_identity = ?').get(REPOSITORY))
      .toBeUndefined();
  });

  // This server's stdout is the MCP stdio transport (index.ts's
  // StdioServerTransport); any reap-observability log MUST land on stderr
  // (console.error), never stdout (console.log), or it would corrupt the
  // protocol stream. Falsifier: routing the log through console.log instead
  // of console.error makes the console.log assertion fail.
  it('logs a reap to stderr only, never stdout', () => {
    const database = db();
    assignment(database, GUID_ONE);
    database.prepare("UPDATE assignments SET worktree_path = ?, lifecycle_status = 'active' WHERE workspace_guid = ?")
      .run(liveWorktree(), GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    });
    database.prepare("UPDATE assignments SET lifecycle_status = 'cleaned' WHERE workspace_guid = ?").run(GUID_ONE);

    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    try {
      expect(reapStalePrimaryOwner(database, REPOSITORY)).toBe(true);
      expect(errorSpy).toHaveBeenCalledWith('reapStalePrimaryOwner: reclaimed stale primary-checkout owner');
      expect(logSpy).not.toHaveBeenCalled();
    } finally {
      errorSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it('reclaims a primary owner whose assignment reached a terminal lifecycle for a different session', () => {
    const database = db();
    assignment(database, GUID_ONE);
    // Isolate terminal-ness as the sole staleness cause: worktree present, acquisition fresh.
    database.prepare("UPDATE assignments SET worktree_path = ?, lifecycle_status = 'active' WHERE workspace_guid = ?")
      .run(liveWorktree(), GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    });
    database.prepare("UPDATE assignments SET lifecycle_status = 'abandoned' WHERE workspace_guid = ?").run(GUID_ONE);

    assignment(database, GUID_TWO);
    bindAssignmentOwner(database, GUID_TWO, OTHER_OWNER);
    // Falsifier: pre-fix this throws 'Primary checkout is already owned'.
    expect(acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_TWO, ownerSessionId: OTHER_OWNER,
    }).workspace_guid).toBe(GUID_TWO);
    expect(database.prepare('SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?').get(REPOSITORY))
      .toMatchObject({ workspace_guid: GUID_TWO });
  });

  it('never reaps a live primary owner: a different session is still refused', () => {
    const database = db();
    assignment(database, GUID_ONE);
    database.prepare("UPDATE assignments SET worktree_path = ?, lifecycle_status = 'active' WHERE workspace_guid = ?")
      .run(liveWorktree(), GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    });

    assignment(database, GUID_TWO);
    bindAssignmentOwner(database, GUID_TWO, OTHER_OWNER);
    expect(() => acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_TWO, ownerSessionId: OTHER_OWNER,
    })).toThrow('Primary checkout is already owned');
    expect(database.prepare('SELECT workspace_guid FROM primary_checkout_owners WHERE repository_identity = ?').get(REPOSITORY))
      .toMatchObject({ workspace_guid: GUID_ONE });
  });

  it('reclaims a primary owner whose recorded worktree no longer exists on disk', () => {
    const database = db();
    // The fixture helper records a placeholder /repos/... worktree_path that never exists.
    assignment(database, GUID_ONE);
    database.prepare("UPDATE assignments SET lifecycle_status = 'active' WHERE workspace_guid = ?").run(GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    });
    // Active and fresh; only the missing worktree makes this row stale.
    expect(existsSync(getAssignment(database, GUID_ONE)!.worktree_path)).toBe(false);

    assignment(database, GUID_TWO);
    bindAssignmentOwner(database, GUID_TWO, OTHER_OWNER);
    expect(acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_TWO, ownerSessionId: OTHER_OWNER,
    }).workspace_guid).toBe(GUID_TWO);
  });

  it('reclaims a primary owner past the 60-minute TTL but keeps a fresh one', () => {
    const database = db();
    assignment(database, GUID_ONE);
    database.prepare("UPDATE assignments SET worktree_path = ?, lifecycle_status = 'active' WHERE workspace_guid = ?")
      .run(liveWorktree(), GUID_ONE);
    bindAssignmentOwner(database, GUID_ONE, OWNER);
    acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_ONE, ownerSessionId: OWNER,
    });

    assignment(database, GUID_TWO);
    bindAssignmentOwner(database, GUID_TWO, OTHER_OWNER);
    // Fresh owner (active, worktree present, just acquired) is not reaped.
    expect(() => acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_TWO, ownerSessionId: OTHER_OWNER,
    })).toThrow('Primary checkout is already owned');

    // Age the acquisition past the TTL (same datetime format the column stores).
    database.prepare("UPDATE primary_checkout_owners SET acquired_at = datetime('now', '-61 minutes') WHERE repository_identity = ?")
      .run(REPOSITORY);
    expect(acquirePrimaryCheckoutOwnership(database, {
      repositoryIdentity: REPOSITORY, workspaceGuid: GUID_TWO, ownerSessionId: OTHER_OWNER,
    }).workspace_guid).toBe(GUID_TWO);
  });

  it.each<HumanIntentOperation>([
    'use-primary-checkout',
    'return-to-managed-worktree',
    'commit',
    'commit-and-push',
    'push',
  ])('consumes matching %s human intent exactly once', (operation) => {
    const database = db();
    assignment(database);
    const evidence = { expectedHead: 'a'.repeat(40), targetRef: 'refs/heads/main' };
    const intent = createHumanIntent(database, {
      operation,
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: REPOSITORY,
      workspaceGuid: GUID_ONE,
      expectedEvidence: evidence,
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: `nonce-${operation}`,
    });
    const request = {
      operation,
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: REPOSITORY,
      workspaceGuid: GUID_ONE,
      expectedEvidence: evidence,
      nonce: intent.nonce,
    };
    const clock = () => new Date('2029-01-01T00:00:00.000Z');
    expect(consumeHumanIntent(database, request, clock)?.intent_id).toBe(intent.intent_id);
    expect(consumeHumanIntent(database, request, clock)).toBeUndefined();
  });

  it('issues nonce and expiry server-side, supersedes replay, and consumes without caller nonce', () => {
    const database = db();
    assignment(database);
    const input = {
      operation: 'commit' as const,
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: REPOSITORY,
      workspaceGuid: GUID_ONE,
      expectedEvidence: { stagedTree: 'a'.repeat(40) },
    };
    const clock = () => new Date('2029-01-01T00:00:00.000Z');
    const first = issueHumanIntent(database, input, clock, () => 'server-nonce-one');
    const second = issueHumanIntent(database, input, clock, () => 'server-nonce-two');

    expect(first).toEqual({ issued: true, operation: 'commit' });
    expect(second).toEqual(first);
    expect(first).not.toHaveProperty('nonce');
    expect(first).not.toHaveProperty('expectedEvidence');
    expect(database.prepare('SELECT COUNT(*) AS count FROM human_intents WHERE consumed_at IS NULL')
      .get()).toEqual({ count: 1 });
    expect(database.prepare('SELECT nonce FROM human_intents WHERE consumed_at IS NULL').get())
      .toEqual({ nonce: 'server-nonce-two' });
    expect(consumeMatchingHumanIntent(database, { ...input, humanChannel: 'claude-user-prompt' }, clock)).toBeUndefined();
    expect(consumeMatchingHumanIntent(database, { ...input, expectedEvidence: { stagedTree: 'b'.repeat(40) } }, clock)).toBeUndefined();
    expect(consumeMatchingHumanIntent(database, input, clock)?.nonce).toBe('server-nonce-two');
    expect(consumeMatchingHumanIntent(database, input, clock)).toBeUndefined();
  });

  it('does not consume an expired server-held intent', () => {
    const database = db();
    assignment(database);
    const input = {
      operation: 'push' as const,
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: REPOSITORY,
      workspaceGuid: GUID_ONE,
      expectedEvidence: { localOid: 'a'.repeat(40) },
    };
    issueHumanIntent(database, input, () => new Date('2029-01-01T00:00:00.000Z'), () => 'expiring-server-nonce');
    expect(consumeMatchingHumanIntent(database, input, () => new Date('2029-01-01T00:05:00.001Z'))).toBeUndefined();
  });

  it('rejects human-intent replay across channel, root session, evidence, expiry, and request-supplied time', () => {
    const database = db();
    assignment(database);
    const input = {
      operation: 'commit' as const,
      humanChannel: 'claude-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: REPOSITORY,
      workspaceGuid: GUID_ONE,
      expectedEvidence: { stagedTree: 'a'.repeat(40) },
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: 'cross-binding-nonce',
    };
    createHumanIntent(database, input);
    expect(consumeHumanIntent(database, {
      ...input, humanChannel: 'codex-user-prompt',
    }, () => new Date('2029-01-01T00:00:00.000Z'))).toBeUndefined();
    expect(consumeHumanIntent(database, {
      ...input,
      now: '2029-01-01T00:00:00.000Z',
    } as typeof input & { now: string }, () => new Date('2031-01-01T00:00:00.000Z'))).toBeUndefined();
    expect(consumeHumanIntent(database, {
      ...input, expectedEvidence: { stagedTree: 'b'.repeat(40) },
    }, () => new Date('2029-01-01T00:00:00.000Z'))).toBeUndefined();
    expect(consumeHumanIntent(database, {
      ...input,
    }, () => new Date('2029-01-01T00:00:00.000Z'))?.nonce).toBe(input.nonce);
  });

  it('normalizes intent expiry timestamps before durable comparison', () => {
    const database = db();
    assignment(database);
    expect(createHumanIntent(database, {
      operation: 'push',
      humanChannel: 'codex-user-prompt',
      providerRootSessionId: OWNER,
      repositoryIdentity: REPOSITORY,
      workspaceGuid: GUID_ONE,
      expectedEvidence: { localCommit: 'a'.repeat(40) },
      expiresAt: '2030-01-01T01:00:00+01:00',
      nonce: 'normalized-expiry-nonce',
    }).expires_at).toBe('2030-01-01T00:00:00.000Z');
  });

  it('separates a Claude subagent from its root so authority consumption can be fenced', () => {
    // Parity with the Codex thread_source fence. A Claude subagent SHARES the
    // root's PPID file, so identity by PPID alone cannot tell them apart and
    // requireProviderRoot() could never fire on the Claude path.
    const root = resolveSessionIdentity('claude', undefined, 'claude-ppid-root');
    expect(root.invocationThreadId).toBeNull();

    for (const meta of [
      { thread_source: 'subagent', agent_id: 'a1' },
      { threadSource: 'subagent' },
      { agent_id: 'a2' },
      { subagentId: 'a3' },
    ]) {
      const sub = resolveSessionIdentity('claude', meta, 'claude-ppid-root');
      expect(sub.sessionId).toBe('claude-ppid-root');
      expect(sub.invocationThreadId).not.toBeNull();
      expect(sub.invocationThreadId).not.toBe(sub.sessionId);
    }

    // A root turn carrying unrelated metadata must stay root.
    expect(resolveSessionIdentity('claude', { progressToken: 7 }, 'claude-ppid-root')
      .invocationThreadId).toBeNull();
  });

  it('uses the same trusted provider-root identity rules as state-manager', () => {
    const codexRoot = resolveSessionIdentity('codex', {
      threadId: OWNER,
      'x-codex-turn-metadata': {
        session_id: OWNER,
        thread_id: OWNER,
        thread_source: 'user',
      },
    });
    expect(codexRoot).toMatchObject({ sessionId: OWNER, source: 'codex_meta' });
    expect(resolveSessionIdentity('claude', undefined, 'claude-ppid-root')).toMatchObject({
      sessionId: 'claude-ppid-root', source: 'ppid_file',
    });
    expect(() => resolveSessionIdentity('codex', {
      threadId: OTHER_OWNER,
      'x-codex-turn-metadata': {
        session_id: OWNER,
        thread_id: OTHER_OWNER,
        thread_source: 'user',
      },
    })).toThrow('Codex root session_id disagrees with root threadId');
  });
});

const V1_DDL = `
  CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')));
  CREATE TABLE assignments (workspace_guid TEXT PRIMARY KEY, repository_identity TEXT NOT NULL, worktree_path TEXT NOT NULL, branch TEXT NOT NULL, base_commit TEXT NOT NULL, current_head TEXT NOT NULL, owner_session_id TEXT, worker_id TEXT, lifecycle_status TEXT NOT NULL DEFAULT 'reserved', integration_target TEXT NOT NULL, integrated_commit TEXT, recovery_ref TEXT, disposition TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
  CREATE TABLE human_intents (intent_id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, human_channel TEXT NOT NULL, provider_root_session_id TEXT NOT NULL, repository_identity TEXT NOT NULL, workspace_guid TEXT NOT NULL REFERENCES assignments(workspace_guid), expected_evidence TEXT NOT NULL, expires_at TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE, issued_at TEXT NOT NULL DEFAULT (datetime('now')), consumed_at TEXT);
  INSERT INTO schema_migrations(version) VALUES (1);
`;

describe('human_intents FK-drop migration (v2)', () => {
  it('migrates a populated v1 DB: rows survive byte-identical, FK dropped, NOT NULL kept', () => {
    const database = new Database(':memory:');
    database.pragma('foreign_keys = ON');
    database.exec(V1_DDL);
    database.prepare("INSERT INTO assignments (workspace_guid, repository_identity, worktree_path, branch, base_commit, current_head, integration_target) VALUES ('11111111-1111-4111-8111-111111111111','/repo','/wt','ironclaude/x','c0','c0','main')").run();
    database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, consumed_at) VALUES ('commit','claude-user-prompt','sess','/repo','11111111-1111-4111-8111-111111111111','{\"a\":1}','2030-01-01T00:00:00.000Z','keep1','2029-01-01T00:00:00.000Z')").run();
    const before = database.prepare("SELECT * FROM human_intents WHERE nonce='keep1'").get();
    migrateSchema(database);
    expect(database.prepare("SELECT * FROM human_intents WHERE nonce='keep1'").get()).toEqual(before);
    expect((database.prepare('SELECT version FROM schema_migrations ORDER BY version').all() as { version: number }[]).map((v) => v.version)).toEqual([1, 2, 3]);
    expect(() => database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce) VALUES ('commit','claude-user-prompt','s2','/repo','primary:/repo','{}','2030-01-01T00:00:00.000Z','n2')").run()).not.toThrow();
    expect(() => database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce) VALUES ('commit','claude-user-prompt','s3','/repo',NULL,'{}','2030-01-01T00:00:00.000Z','n3')").run()).toThrow();
    expect(database.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='human_intents_lookup_idx'").get()).toBeTruthy();
    database.close();
  });
});

describe('human-intent workspace-ref predicate: UUID or primary-checkout sentinel', () => {
  it('accepts a primary-checkout sentinel for createHumanIntent, rejects invalid refs, and does not widen createAssignment', () => {
    const database = initDb(':memory:');
    expect(() => createHumanIntent(database, {
      operation: 'commit',
      humanChannel: 'claude-user-prompt',
      providerRootSessionId: 'sess-1',
      repositoryIdentity: '/repo',
      workspaceGuid: 'primary:/repo',
      expectedEvidence: {},
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: 'sentinel-nonce',
    })).not.toThrow();

    expect(() => createHumanIntent(database, {
      operation: 'commit',
      humanChannel: 'claude-user-prompt',
      providerRootSessionId: 'sess-2',
      repositoryIdentity: '/repo',
      workspaceGuid: 'not-a-uuid',
      expectedEvidence: {},
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: 'bad-nonce-1',
    })).toThrow();

    expect(() => createHumanIntent(database, {
      operation: 'commit',
      humanChannel: 'claude-user-prompt',
      providerRootSessionId: 'sess-3',
      repositoryIdentity: '/repo',
      workspaceGuid: 'primary:',
      expectedEvidence: {},
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: 'bad-nonce-2',
    })).toThrow();

    expect(() => createAssignment(database, {
      workspaceGuid: 'primary:/x',
      repositoryIdentity: '/repo',
      worktreePath: '/wt',
      branch: 'ironclaude/x',
      baseCommit: 'a'.repeat(40),
      currentHead: 'a'.repeat(40),
      integrationTarget: 'main',
    })).toThrow();

    database.close();
  });
});

// The existing FK-drop v2 test's V1_DDL declares human_intents.workspace_guid NOT NULL,
// so it cannot reproduce the live 2026-08-21 crash. That crash was on an OLDER on-disk
// human_intents whose workspace_guid predated the NOT NULL constraint (CREATE TABLE IF NOT
// EXISTS never re-tightened it); the v2 migration's INSERT..SELECT then hit the new NOT NULL
// and threw on every cli.js call. This fixture reproduces the nullable shape.
const LEGACY_NULLABLE_HUMAN_INTENTS_DDL = `
  CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')));
  CREATE TABLE human_intents (intent_id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, human_channel TEXT NOT NULL, provider_root_session_id TEXT NOT NULL, repository_identity TEXT NOT NULL, workspace_guid TEXT, expected_evidence TEXT NOT NULL, expires_at TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE, issued_at TEXT NOT NULL DEFAULT (datetime('now')), consumed_at TEXT);
  INSERT INTO schema_migrations(version) VALUES (1);
`;

describe('human_intents v2 migration tolerates a legacy NULL workspace_guid row', () => {
  it('drops NULL-guid rows and migrates without crashing', () => {
    const database = new Database(':memory:');
    database.pragma('foreign_keys = ON');
    database.exec(LEGACY_NULLABLE_HUMAN_INTENTS_DDL);
    database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, consumed_at) VALUES ('commit','claude-user-prompt','sess','/repo','11111111-1111-4111-8111-111111111111','{}','2030-01-01T00:00:00.000Z','keep-valid','2029-01-01T00:00:00.000Z')").run();
    database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, consumed_at) VALUES ('commit','claude-user-prompt','sess','/repo',NULL,'{}','2030-01-01T00:00:00.000Z','dead-null','2029-01-01T00:00:00.000Z')").run();
    // Pre-fix: migrateSchema throws NOT NULL constraint failed on the NULL row -> RED.
    migrateSchema(database);
    expect((database.prepare('SELECT version FROM schema_migrations ORDER BY version').all() as { version: number }[]).map((v) => v.version)).toEqual([1, 2, 3]);
    expect(database.prepare("SELECT nonce FROM human_intents WHERE nonce='keep-valid'").get()).toEqual({ nonce: 'keep-valid' });
    expect(database.prepare('SELECT COUNT(*) AS c FROM human_intents WHERE workspace_guid IS NULL').get()).toEqual({ c: 0 });
    // Positive control: the recreated v2 table dropped the FK - the sentinel inserts cleanly.
    expect(() => database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce) VALUES ('commit','claude-user-prompt','s2','/repo','primary:/repo','{}','2030-01-01T00:00:00.000Z','sentinel')").run()).not.toThrow();
    database.close();
  });
});

// A v2-era human_intents table: NOT NULL workspace_guid (FK already dropped by v2), and the
// v2 five-value operation CHECK. Used to prove a pre-existing v2 row survives the v3 widen.
const V2_HUMAN_INTENTS_DDL = `
  CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')));
  CREATE TABLE human_intents (intent_id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL CHECK (operation IN ('use-primary-checkout', 'return-to-managed-worktree', 'commit', 'commit-and-push', 'push')), human_channel TEXT NOT NULL, provider_root_session_id TEXT NOT NULL, repository_identity TEXT NOT NULL, workspace_guid TEXT NOT NULL, expected_evidence TEXT NOT NULL, expires_at TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE, issued_at TEXT NOT NULL DEFAULT (datetime('now')), consumed_at TEXT);
  CREATE INDEX human_intents_lookup_idx ON human_intents(operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, nonce);
  INSERT INTO schema_migrations(version) VALUES (1);
  INSERT INTO schema_migrations(version) VALUES (2);
`;

describe('human_intents operation-widen migration (v3)', () => {
  it('admits reconcile and close-out operations, still rejects an unknown operation', () => {
    const database = initDb(':memory:');
    expect(() => createHumanIntent(database, {
      operation: 'reconcile' as HumanIntentOperation,
      humanChannel: 'claude-user-prompt',
      providerRootSessionId: 'sess-1',
      repositoryIdentity: '/repo',
      workspaceGuid: GUID_ONE,
      expectedEvidence: {},
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: 'reconcile-nonce',
    })).not.toThrow();

    expect(() => createHumanIntent(database, {
      operation: 'close-out' as HumanIntentOperation,
      humanChannel: 'claude-user-prompt',
      providerRootSessionId: 'sess-2',
      repositoryIdentity: '/repo',
      workspaceGuid: GUID_ONE,
      expectedEvidence: {},
      expiresAt: '2030-01-01T00:00:00.000Z',
      nonce: 'close-out-nonce',
    })).not.toThrow();

    expect(() => database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce) VALUES ('bogus','claude-user-prompt','s3','/repo',?,'{}','2030-01-01T00:00:00.000Z','bogus-nonce')").run(GUID_ONE)).toThrow();

    database.close();
  });

  it('migrates a populated v2 DB: a pre-seeded v2-era row survives the v3 migration', () => {
    const database = new Database(':memory:');
    database.pragma('foreign_keys = ON');
    database.exec(V2_HUMAN_INTENTS_DDL);
    database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, consumed_at) VALUES ('commit','claude-user-prompt','sess','/repo','11111111-1111-4111-8111-111111111111','{}','2030-01-01T00:00:00.000Z','v2-row',NULL)").run();
    migrateSchema(database);
    expect((database.prepare('SELECT version FROM schema_migrations ORDER BY version').all() as { version: number }[]).map((v) => v.version)).toEqual([1, 2, 3]);
    expect(database.prepare("SELECT nonce FROM human_intents WHERE nonce='v2-row'").get()).toEqual({ nonce: 'v2-row' });
    expect(database.prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='human_intents_lookup_idx'").get()).toBeTruthy();
    database.close();
  });
});
