# human_intents v2 migration NULL-guid tolerance (Loop 0) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the workspace-manager v2 migration NULL-tolerant so a legacy NULL-guid `human_intents`
row no longer crashes every workspace-manager call.

**Requirements:** docs/plans/2026-08-21-human-intents-v2-null-migration-requirements.md

**Design:** docs/plans/2026-08-21-human-intents-v2-null-migration-design.md

**Architecture:** One-line defensive fix — add `WHERE workspace_guid IS NOT NULL` to the v2 migration
`INSERT…SELECT` (db.ts:182), dropping dead NULL-guid auth receipts instead of crashing on the new
NOT NULL. Guarded by a new regression test that reproduces the older nullable on-disk schema the
existing v2 test does not cover.

**Tech Stack:** TypeScript, better-sqlite3, vitest (`vitest run`). Tests run against `src` (vitest
resolves `../db.js` → `db.ts`), so no build is needed for the TDD cycle; the build+deploy is a
post-commit step.

**Execution invariants (author + reviewer check against these):** shell state does not persist
between steps (literal absolute paths); Bash cwd is `commander/`, so run the package script via
`npm --prefix <abs workspace-manager dir>`; `docs/` is gitignored (`git add -f` for docs, plain
`git add` for the source files under version control); the vitest pass/fail is the measured oracle for
the RED→GREEN steps (no predicted magic values); an empty result must be distinguishable from a
failed command; `worker/mcp-servers/*/dist/` is gitignored so the commit is source-only.

---

## Task 1: Make the v2 migration NULL-tolerant (TDD)

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts:182`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`

This task modifies executable code → TDD (RED → GREEN → stage).

**Step 1 (RED — add the legacy-nullable regression test).** Append to
`worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts` (after the existing
`human_intents FK-drop migration (v2)` describe block, near line 480). The existing `V1_DDL` fixture
makes `human_intents.workspace_guid` `NOT NULL`, so it cannot hold a NULL row; this fixture uses the
older **nullable** shape that the live crash actually hit:

```typescript
// The live 2026-08-21 crash was on an OLDER on-disk human_intents whose workspace_guid
// predated the NOT NULL constraint (CREATE TABLE IF NOT EXISTS never re-tightened it). The
// v2 migration's INSERT..SELECT then hit the new NOT NULL and threw on every cli.js call.
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
    // Pre-fix: migrateSchema throws NOT NULL constraint failed on the NULL row → RED.
    migrateSchema(database);
    expect((database.prepare('SELECT version FROM schema_migrations ORDER BY version').all() as { version: number }[]).map((v) => v.version)).toEqual([1, 2]);
    expect(database.prepare("SELECT nonce FROM human_intents WHERE nonce='keep-valid'").get()).toEqual({ nonce: 'keep-valid' });
    expect(database.prepare('SELECT COUNT(*) AS c FROM human_intents WHERE workspace_guid IS NULL').get()).toEqual({ c: 0 });
    // Positive control: the recreated v2 table dropped the FK — the sentinel inserts cleanly.
    expect(() => database.prepare("INSERT INTO human_intents (operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce) VALUES ('commit','claude-user-prompt','s2','/repo','primary:/repo','{}','2030-01-01T00:00:00.000Z','sentinel')").run()).not.toThrow();
    database.close();
  });
});
```

Run:
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test
```
Expected: the run ends non-zero (RED). The new test FAILS because `migrateSchema` throws a NOT NULL
constraint error copying the NULL-guid row into `human_intents_v2`. Every pre-existing test still
passes (they have no NULL rows). This proves the new check can fail.

**Step 2 (GREEN — make the copy NULL-tolerant).** In
`worker/mcp-servers/workspace-manager/src/db.ts`, change the v2 migration copy at line 182 from:
```typescript
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents;
```
to:
```typescript
          SELECT intent_id, operation, human_channel, provider_root_session_id, repository_identity, workspace_guid, expected_evidence, expires_at, nonce, issued_at, consumed_at FROM human_intents WHERE workspace_guid IS NOT NULL;
```
Only the trailing `WHERE workspace_guid IS NOT NULL` is added. No other line changes; the FK stays
dropped; no other migration is touched.

**Step 3 (GREEN — verify).** Run:
```bash
npm --prefix /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager test
```
Expected: exit 0, 0 failures. The new test passes (migration completes, valid row survives, NULL row
dropped, sentinel accepted) and every pre-existing test still passes — including the existing v2 test
(db.test.ts:465-479) whose non-NULL row still copies byte-identically under the `WHERE` filter.

**Step 4: Stage changes.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/db.ts worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts
```
Expected: both files staged (professional mode blocks commit — the human commits). Do NOT stage
anything else; plan docs stay unstaged until after Step 5's check.

**Step 5 (no-regression evidence — bounded to the two source files).** With only those two files
staged, run the UNFILTERED staged diff (no pathspec, so a stray third staged file appears and fails
the check — falsifiable):
```bash
git -C /Users/roberthyatt/Code/ironclaude diff --staged --name-only
```
Expected: exactly the two lines `worker/mcp-servers/workspace-manager/src/db.ts` and
`worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts` and nothing else staged — no other
source touched, so no other test suite can regress from this change.
