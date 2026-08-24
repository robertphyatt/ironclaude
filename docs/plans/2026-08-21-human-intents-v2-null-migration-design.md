# human_intents v2 migration NULL-guid tolerance (Loop 0) Design

> **Created:** 2026-08-21
> **Status:** Design Complete
> **Scope mode:** selective (single defensive SQL fix + seeded migration test)
> **Epic context:** Loop 0 of the human-controlled commit/push epic (Fable-coached decomposition:
> Loop 0 migration fix → Loop 1 unassigned-primary human /push → Loop 2 /commit-and-push → Loop 3
> managed-worktree gaps → Loop 4 interactive conflict flow). Loop 0 retires a DB landmine FIRST
> because every subsequent loop redeploys the workspace-manager and re-walks this migration on
> other machines' DBs.

## Summary

The workspace-manager schema v2 migration (`worker/mcp-servers/workspace-manager/src/db.ts:167-188`)
recreates `human_intents` with `workspace_guid TEXT NOT NULL` — dropping v1's
`REFERENCES assignments(workspace_guid)` foreign key so the Loop 2 sentinel `primary:<repoIdentity>`
(not an assignment row) is storable. It copies every existing row via a bare
`INSERT INTO human_intents_v2 (...) SELECT ... FROM human_intents`. If any legacy row has a NULL
`workspace_guid` — which the live DB had, because the on-disk `human_intents` was created under an
older schema and `CREATE TABLE IF NOT EXISTS` never re-tightened the column — the INSERT violates the
new NOT NULL, the migration transaction rolls back, and the error re-throws on **every** `cli.js`
invocation, breaking ALL workspace-manager operations (get_workspace_status, assignments, commit,
etc.). Proven live 2026-08-21; worked around by manually deleting 3 dead NULL-guid rows
(`project_human_intents_v2_migration_null_crash`).

**Fix:** make the copy NULL-tolerant — `... SELECT ... FROM human_intents WHERE workspace_guid IS NOT NULL`.
The dropped rows are expired, single-use human-intent auth receipts (5-minute TTL, db.ts:473); they
carry no work and any NULL-guid receipt is unusable by construction (consumption exact-matches a
non-NULL guid). "Never lose work" applies to commits/branches, not to transient auth receipts, so
dropping them is safe and correct — no side-table preservation (YAGNI).

## Architecture

Single-line change inside the existing v2 migration transaction (db.ts:181-182). Only the
`INSERT…SELECT` gains a `WHERE workspace_guid IS NOT NULL` filter. The migration remains gated on
`schema_migrations version = 2` absence (db.ts:165), runs once, and is idempotent. No new table, no
new column, no FK reintroduced (reintroducing `REFERENCES assignments(workspace_guid)` would re-break
the sentinel — explicitly NOT done). The filter targets `workspace_guid` only: every other v2
NOT NULL column (`operation`, `human_channel`, `provider_root_session_id`, `repository_identity`,
`expected_evidence`, `expires_at`, `nonce`) has been NOT NULL since v1, so no legacy NULL can exist
there — filtering them would be speculative (no evidence, YAGNI). `workspace_guid` is the only column
whose constraint history changed (FK→plain-NOT-NULL) and thus the only one exposed to older-schema
NULL drift.

## Components

- `worker/mcp-servers/workspace-manager/src/db.ts` — the v2 migration `INSERT…SELECT` (db.ts:181-182).
  Single edit: add `WHERE workspace_guid IS NOT NULL`.
- `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts` — add a seeded-NULL-row migration
  regression test (see Testing Strategy).

## Data Flow

`initDb` → `migrateSchema(db)` → v1 block (`CREATE TABLE IF NOT EXISTS`, no-op on an existing
old-schema table) → v2 block: if version 2 absent, create `human_intents_v2`, copy
`WHERE workspace_guid IS NOT NULL` (NULL-guid rows skipped), drop old table, rename, index, record
version 2. A DB with a NULL-guid legacy row now migrates cleanly instead of crashing every CLI call.

## Error Handling

- NULL-guid legacy rows: filtered out (dropped), migration succeeds. This is the fix.
- No NULL-guid rows (clean DB): the `WHERE` is a no-op; all rows copy exactly as before — no
  behavior change on healthy DBs.
- Migration still runs inside `db.transaction(...)`, so any genuine unexpected failure still rolls
  back atomically (unchanged).

## Testing Strategy

`worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts` (vitest, in-memory
`better-sqlite3`). RED→GREEN regression test that reproduces the live crash:
1. Open an in-memory DB. Manually create a v1-shaped `human_intents` table WITHOUT the NOT NULL on
   `workspace_guid` (simulating the older on-disk schema that permitted NULL) plus `schema_migrations`
   seeded at version 1. Insert two rows: one valid (non-NULL guid) and one with NULL `workspace_guid`.
   (Also create the other v1 tables the v1 block references, or let `CREATE TABLE IF NOT EXISTS`
   create them — verified against the actual migrateSchema flow during writing-plans.)
2. Call `migrateSchema(db)`.
3. Assert (all fail on the CURRENT code because migrateSchema throws before any assertion):
   - `migrateSchema` does NOT throw;
   - `schema_migrations` contains version 2;
   - the valid row survives in `human_intents` (matched by `nonce`);
   - the NULL-guid row is gone (0 rows where `workspace_guid IS NULL`);
   - `human_intents` accepts the sentinel `primary:<repo>` shape (the FK is gone) — a positive
     control that the v2 table is the recreated one.

Falsifiability: on the unpatched migration, step 2 throws (NOT NULL violation), so the test errors
out — RED. With the `WHERE` filter, all assertions hold — GREEN. Then run the full workspace-manager
vitest suite for no regression.

## Implementation Notes

- **Deploy (post-commit, LOCAL, human):** the commander resolves the workspace-manager from the
  CLAUDE plugin cache (`~/.claude/plugins/cache/ironclaude/ironclaude/<latest>/mcp-servers/workspace-manager/`)
  via version-match (`workspace_client.py:73-88`), NOT the repo; `make deploy-hooks` does NOT touch
  it. So the built `dist/` must be refreshed in the repo AND the claude cache (and the codex cache if
  codex uses workspace-manager) — verify with the marker-grep pattern
  (`project_verify_what_ran`, `project_local_deploy_facts`). The MCP server holds its dist in memory
  until relaunch, so the running commander needs a workspace-manager restart to pick it up.
- **Build step:** workspace-manager is TypeScript — the plan must `npm run build` (or the repo's build
  command, verified during writing-plans) so `dist/` reflects the `src/` change before deploy.
- Scope stays selective: no FK reintroduction, no change to any other migration, no change to intent
  issuance/consumption logic.
- The autosync-epic non-goal amendment (remote push / conflict resolution, human path) belongs to
  Loop 1/2, NOT Loop 0.
