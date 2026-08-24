# human_intents v2 migration NULL-guid tolerance (Loop 0) Requirements (operator-approved)

> **Created:** 2026-08-21
> **Source:** operator-approved decomposition of the human-controlled commit/push epic
> (Fable-coached). Operator chose "Loop 0 then Loop 1, ff-only, no envelope." Loop 0 retires the
> DB migration landmine FIRST because every subsequent loop redeploys the workspace-manager and
> re-runs this migration on other machines' DBs. Root cause proven live 2026-08-21
> (`project_human_intents_v2_migration_null_crash`). Human commits, no push.

## Problem

`worker/mcp-servers/workspace-manager/src/db.ts:167-188` (schema v2 migration) recreates
`human_intents` with `workspace_guid TEXT NOT NULL` (dropping v1's
`REFERENCES assignments(workspace_guid)` FK so the sentinel `primary:<repoIdentity>` is storable) and
copies every row via `INSERT INTO human_intents_v2 (...) SELECT ... FROM human_intents` (db.ts:181-182).
A legacy row with a NULL `workspace_guid` — present because the on-disk `human_intents` was created
under an older schema and `CREATE TABLE IF NOT EXISTS` never re-tightened the column — violates the
new NOT NULL, rolls back the migration transaction, and re-throws on **every** `cli.js` invocation,
breaking ALL workspace-manager operations. Worked around live by deleting 3 dead NULL-guid rows; the
durable fix is a NULL-tolerant migration.

## Approved scope

- **R1 — NULL-tolerant copy.** Add `WHERE workspace_guid IS NOT NULL` to the v2 migration's
  `INSERT…SELECT` (db.ts:182). NULL-guid rows are dropped (not copied). They are expired, single-use
  human-intent auth receipts (5-minute TTL) carrying no work and unusable by construction (a NULL guid
  can never match a consumption request); "never lose work" applies to commits/branches, not transient
  auth receipts, so dropping them is correct. No side-table preservation (YAGNI).
- **R2 — no other change.** Filter `workspace_guid` ONLY (the sole column whose constraint history
  changed FK→plain-NOT-NULL and thus the only one exposed to older-schema NULL drift; every other v2
  NOT NULL column has been NOT NULL since v1, so no legacy NULL can exist there). Do NOT reintroduce
  the FK, do NOT touch any other migration, intent issuance, or consumption logic.
- **R3 — falsifiable regression test.** Add a test in
  `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts` that reproduces the crash: seed an
  in-memory DB with a legacy **nullable** `human_intents` table (the older shape the existing `V1_DDL`
  at db.test.ts:457-462 does NOT reproduce — its `human_intents` is `NOT NULL`), insert one valid row
  and one NULL-guid row, run `migrateSchema`, and assert: it does NOT throw; `schema_migrations` =
  [1,2]; the valid row survives (by nonce); zero rows remain with NULL `workspace_guid`; the recreated
  v2 table accepts the sentinel `primary:<repo>` (FK-dropped positive control). RED on the current
  code (migrateSchema throws), GREEN after R1.
- **R4 — no regression.** The full workspace-manager vitest suite passes, including the existing v2
  test (db.test.ts:465-479) which asserts a populated v1 row survives byte-identical — with the
  `WHERE` filter, its non-NULL row still copies unchanged. Staged set is exactly the two source files:
  `worker/mcp-servers/workspace-manager/src/db.ts` and
  `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`.
- **R5 — staging only (PM on).** `git add` both source files (repo-root-anchored `git -C`).
  Professional mode blocks commit; the human commits. No push.

## Deploy (post-commit, LOCAL — not a plan task)

`worker/mcp-servers/*/dist/` is gitignored (commit is src only). After the human commit:
`npm run build` (`tsc && esbuild bundle`) in the workspace-manager package, then refresh the built
`dist/` into the CLAUDE plugin cache the commander resolves workspace-manager from
(`~/.claude/plugins/cache/ironclaude/ironclaude/<latest>/mcp-servers/workspace-manager/`, per
`workspace_client.py:73-88`; NOT touched by `make deploy-hooks`), and restart the commander's
workspace-manager MCP so it loads the new dist (MCP servers hold dist in memory until relaunch).
Verify with the marker-grep pattern (`project_verify_what_ran`, `project_local_deploy_facts`).

## Non-goals

- Loop 1+ (the human push lanes). Separate loops.
- FK reintroduction; any change to intent issuance/consumption; filtering columns other than
  `workspace_guid`.
- The autosync-epic non-goal amendment (remote push / conflict resolution) — that belongs to Loop 1/2.
