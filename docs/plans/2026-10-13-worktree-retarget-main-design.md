# Retarget the roleplaying-agents music worktree to main

> **Created:** 2026-10-13
> **Status:** Design Complete

Loop: full — a database write (one row in the workspace-manager DB). One task, no code change.

The operator said "Do these now" on 2026-10-13, so routine approval prompts are delegated. Predicted answer: Yes, invoke writing-plans (delegated).

Episodic memory has no record of anyone editing this DB by hand (searched 2026-10-13), so the schema and connection settings were read straight from the code.

## Requirements

The operator gave an explicit go on 2026-10-13 ("yes do the sql retarget"). This is step 1 of Option B in `docs/reviews/2026-10-12-worktree-unblock-findings.md`.

- R1. Record the row's current `integration_target` before changing it. The findings doc measured `portrait-gen-structural-sclera-gate`.
- R2. Change exactly one row: `UPDATE assignments SET integration_target='main' WHERE workspace_guid='019f7d26-9faa-7e71-b7cf-ee2a2334b9f4';`. Use bare `main`, because `integrationTargetRef` (`integration.ts:213-215`) adds `refs/heads/` itself.
- R3. Guard the update so that it changes nothing unless the row is still `active` and still targets sclera-gate. Prove it changed exactly one row with `changes()`.
- R4. Read the row back afterwards and confirm that `integration_target='main'` and that every other column is unchanged.
- R5. Do not touch any other row, table or file. That includes the 4 `d1501-*` assignments that also target sclera-gate.
- R6. Record the before and after values in the findings doc, as §8.

The operator also decided that `AGENTS.md` will be included in `/commit`. That step, and every later step of Option B, runs in the roleplaying-agents Codex session and is out of scope for this loop.

## Design

- **Writing while the server is running.** The DB is `~/.claude/ironclaude-workspaces.db`. It runs in WAL mode, and the server opens it with a 10 s busy timeout (`db.ts:398-400`). The `sqlite3` CLI writes safely alongside the running MCP servers if it sets `.timeout 10000`.
- **Server cache.** None. The server reads the row fresh on every call (`getAssignment`, `db.ts:405-407`).
- **Triggers.** The only trigger on `assignments` is `prevent_workspace_guid_mutation` (`db.ts:154-159`). This update leaves `workspace_guid` alone, so the trigger does not fire.
- **Re-activation.** It cannot quietly undo the retarget. The rewrite that resets `integration_target` (`reuseTerminalAssignment`, `db.ts:440-451`) only touches rows in `cleaned` status, and this row is `active`.

## Error handling

- **Guard matches no row.** If the guard's `changes()` returns 0, the row moved since it was measured. Stop and report it; do not widen the WHERE clause.
- **Database busy.** If `sqlite3` reports busy after the 10 s timeout, stop and report the stderr.

## Testing

No tests are required: this is an operational one-row data change, not code. Verification:
- the before and after `SELECT`;
- `changes()=1`;
- a full-row comparison, before against after, showing that only `integration_target` and possibly `updated_at` differ. This update does not set `updated_at`, so it should not change either.
