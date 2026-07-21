import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { getSession, initDb, upsertSession } from '../db.js';
import type { SessionIdentity } from '../session-identity.js';
import { dispatchTool } from '../tool-dispatch.js';
import { readToolDefinitions } from '../tools/read-tools.js';
import type {
  RuntimeFingerprintCapture,
  RuntimeFingerprintExpectation,
} from '../runtime-fingerprint.js';

const identity: SessionIdentity = {
  client: 'codex',
  sessionId: 'diag-root-a',
  invocationThreadId: 'diag-child-a',
  source: 'codex_meta',
};

const dbRoot = mkdtempSync(path.join(tmpdir(), 'ironclaude-diagnostics-'));
const diagnosticsDb = initDb(path.join(dbRoot, 'state.db'));

function testDb() {
  return diagnosticsDb;
}

afterAll(() => {
  diagnosticsDb.close();
  rmSync(dbRoot, { recursive: true, force: true });
});

function auditRows(db: ReturnType<typeof initDb>) {
  return db.prepare('SELECT * FROM audit_log ORDER BY id').all();
}

const runtimeCapture: RuntimeFingerprintCapture = {
  ok: true,
  runtime: {
    plugin_name: 'ironclaude',
    plugin_version: '1.1.0+codex.test',
    plugin_root: '/tmp/ironclaude/1.1.0+codex.test',
    manifest_path: '/tmp/ironclaude/1.1.0+codex.test/.codex-plugin/plugin.json',
    manifest_sha256: 'a'.repeat(64),
    bundle_path: '/tmp/ironclaude/1.1.0+codex.test/mcp-servers/state-manager/dist/index.js',
    bundle_sha256: 'b'.repeat(64),
    client: 'codex',
  },
};

function expectedRuntime(): RuntimeFingerprintExpectation {
  if (!runtimeCapture.ok) throw new Error(runtimeCapture.error);
  const { plugin_version, plugin_root, manifest_sha256, bundle_sha256, client } = runtimeCapture.runtime;
  return { plugin_version, plugin_root, manifest_sha256, bundle_sha256, client };
}

function structuredPayload(text: string) {
  return JSON.parse(text.slice(text.lastIndexOf('\n\n') + 2)) as {
    results: Array<{ test: string; status: string; detail: string }>;
    passed: number;
    total: number;
    runtime?: typeof runtimeCapture extends { ok: true; runtime: infer T } ? T : never;
  };
}

function seededDb() {
  const db = testDb();
  upsertSession(db, {
    terminal_session: identity.sessionId,
    professional_mode: 'on',
    testing_theatre_checked: 1,
  });
  db.prepare('UPDATE sessions SET updated_at = ? WHERE terminal_session = ?')
    .run('2026-07-19 12:00:00', identity.sessionId);
  db.prepare(
    'INSERT INTO audit_log (terminal_session, actor, action, old_value, new_value, context) VALUES (?, ?, ?, ?, ?, ?)',
  ).run(identity.sessionId, 'test', 'existing', null, 'kept', 'fixture');
  return db;
}

describe('run_diagnostics', () => {
  it('is advertised as read-only now that its probe rolls back', () => {
    const definition = readToolDefinitions.find((tool) => tool.name === 'run_diagnostics');
    expect(definition?.annotations.readOnlyHint).toBe(true);
  });

  it('reports explicit Codex context without changing session or audit state', () => {
    const db = testDb();
    upsertSession(db, {
      terminal_session: identity.sessionId,
      professional_mode: 'on',
      testing_theatre_checked: 1,
    });
    upsertSession(db, {
      terminal_session: 'diag-root-b',
      professional_mode: 'off',
      testing_theatre_checked: 0,
    });
    db.prepare('UPDATE sessions SET updated_at = ? WHERE terminal_session = ?')
      .run('2026-07-19 12:00:00', identity.sessionId);
    db.prepare(
      'INSERT INTO audit_log (terminal_session, actor, action, old_value, new_value, context) VALUES (?, ?, ?, ?, ?, ?)',
    ).run(identity.sessionId, 'test', 'existing', null, 'kept', 'fixture');

    const beforeA = getSession(db, identity.sessionId);
    const beforeB = getSession(db, 'diag-root-b');
    const beforeAudit = auditRows(db);

    const result = dispatchTool('run_diagnostics', {}, db, identity, runtimeCapture);
    const text = result.content[0].text;
    const payload = structuredPayload(text);

    expect(text).toContain('client=codex');
    expect(text).toContain(`session=${identity.sessionId}`);
    expect(text).toContain('source=codex_meta');
    expect(text).not.toContain('PPID file exists');
    expect(payload.runtime).toEqual(runtimeCapture.runtime);
    expect(payload.results).toContainEqual(expect.objectContaining({
      test: 'Runtime fingerprint',
      status: 'PASS',
    }));
    expect(payload.passed).toBe(12);
    expect(payload.total).toBe(12);
    expect(getSession(db, identity.sessionId)).toEqual(beforeA);
    expect(getSession(db, 'diag-root-b')).toEqual(beforeB);
    expect(auditRows(db)).toEqual(beforeAudit);
    expect(auditRows(db).some((row) => (row as { action: string }).action === 'diag_test')).toBe(false);
  });

  it('verifies an exact expected runtime as check 13 without mutation', () => {
    const db = seededDb();
    const beforeSession = getSession(db, identity.sessionId);
    const beforeAudit = auditRows(db);
    const result = dispatchTool(
      'run_diagnostics',
      { expected_runtime: expectedRuntime() },
      db,
      identity,
      runtimeCapture,
    );
    const payload = structuredPayload(result.content[0].text);
    expect(payload.results).toContainEqual(expect.objectContaining({
      test: 'Runtime activation match',
      status: 'PASS',
    }));
    expect(payload.passed).toBe(13);
    expect(payload.total).toBe(13);
    expect(getSession(db, identity.sessionId)).toEqual(beforeSession);
    expect(auditRows(db)).toEqual(beforeAudit);
  });

  it('preserves the fixed 12-check shape when a transactional operation fails', () => {
    const db = seededDb();
    const beforeSession = getSession(db, identity.sessionId);
    const beforeAudit = auditRows(db);
    db.exec(`
      CREATE TRIGGER fail_diagnostics_update
      BEFORE UPDATE OF updated_at ON sessions
      WHEN NEW.updated_at LIKE '%T%'
      BEGIN
        SELECT RAISE(FAIL, 'diagnostic update blocked');
      END;
    `);
    try {
      const result = dispatchTool('run_diagnostics', {}, db, identity, runtimeCapture);
      const payload = structuredPayload(result.content[0].text);
      expect(payload.total).toBe(12);
      expect(payload.results.filter((entry) => [
        'Write test (updated_at)',
        'Read-back verification',
        'Audit log writable',
      ].includes(entry.test))).toEqual([
        expect.objectContaining({ test: 'Write test (updated_at)', status: 'FAIL' }),
        expect.objectContaining({ test: 'Read-back verification', status: 'FAIL' }),
        expect.objectContaining({ test: 'Audit log writable', status: 'PASS' }),
      ]);
      expect(payload.results.some((entry) => entry.test === 'Transactional persistence probe')).toBe(false);
      expect(getSession(db, identity.sessionId)).toEqual(beforeSession);
      expect(auditRows(db)).toEqual(beforeAudit);
    } finally {
      db.exec('DROP TRIGGER fail_diagnostics_update');
    }
  });

  it('fails closed when startup capture is missing or failed', () => {
    for (const capture of [undefined, { ok: false, error: 'startup fixture failed' } as const]) {
      const db = seededDb();
      const beforeSession = getSession(db, identity.sessionId);
      const beforeAudit = auditRows(db);
      const result = dispatchTool('run_diagnostics', { expected_runtime: expectedRuntime() }, db, identity, capture);
      const payload = structuredPayload(result.content[0].text);
      expect(payload.runtime).toBeUndefined();
      expect(payload.results).toContainEqual(expect.objectContaining({ test: 'Runtime fingerprint', status: 'FAIL' }));
      expect(payload.results).toContainEqual(expect.objectContaining({ test: 'Runtime activation match', status: 'FAIL' }));
      expect(getSession(db, identity.sessionId)).toEqual(beforeSession);
      expect(auditRows(db)).toEqual(beforeAudit);
    }
  });

  it.each([
    ['plugin_version', 'stale'],
    ['plugin_root', '/stale'],
    ['manifest_sha256', '0'.repeat(64)],
    ['bundle_sha256', '1'.repeat(64)],
    ['client', 'claude'],
  ] as const)('rejects expected_runtime mismatch for %s without mutation', (field, value) => {
    const db = seededDb();
    const beforeSession = getSession(db, identity.sessionId);
    const beforeAudit = auditRows(db);
    const expected = { ...expectedRuntime(), [field]: value };
    const result = dispatchTool('run_diagnostics', { expected_runtime: expected }, db, identity, runtimeCapture);
    const payload = structuredPayload(result.content[0].text);
    expect(payload.results).toContainEqual(expect.objectContaining({
      test: 'Runtime activation match',
      status: 'FAIL',
      detail: expect.stringContaining(field),
    }));
    expect(getSession(db, identity.sessionId)).toEqual(beforeSession);
    expect(auditRows(db)).toEqual(beforeAudit);
  });
});
