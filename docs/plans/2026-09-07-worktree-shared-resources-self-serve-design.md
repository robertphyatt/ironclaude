# Worktree Shared-Resources Self-Serve Design

> **Created:** 2026-09-07
> **Status:** Design Complete (amended after blind plan review — orchestrator-tool architecture)
> **Scope mode:** selective

## Summary

IronClaude has shipped a managed-worktree data-provisioning mechanism since v1.1.5
(`worktree-shared-resources`): a per-repo config at `<git-common-dir>/info/worktree-shared-resources`
listing relative paths, symlinked primary→worktree on every allocation
(`linkSharedResources`) with matching `info/exclude` entries to keep status clean.
Two gaps make it unusable in practice: (a) it is **undiscoverable** — zero
operator/Brain/worker-facing docs (only `git.ts` comments + CHANGELOG); and (b) it
is **not self-serve** — there is no tool the Brain can call to add entries. So a
Brain that discovers a worktree needs gitignored project data cannot configure
provisioning; it hand-writes an `ln -s` script and asks the operator to run it —
violating the operator-free-worktree pillar, and stalling (a real incident cost
~6.9h and a killed worker).

This design gives the Brain a self-serve surface — a new **orchestrator** MCP tool
(`configure_shared_resources` / `list_shared_resources`) that proxies through the
Commander's existing `WorkspaceClient` → workspace-manager `cli.js` internal-command
path to the shipped git.ts primitives — configures shared resources itself
(validated, repo-scoped), relinks new entries into already-live managed worktrees so
a running worker gets the data without a respawn, documents the mechanism, and adds a
distinct generalized guardrail: never skip/override/rationalize past a required-input
gate to emit a green-but-empty run.

The fix is entirely project-agnostic: no roleplaying-agents/pf2e/vision specifics
enter IronClaude. It works for any repo's gitignored local data.

## Why the surface is the ORCHESTRATOR, not a workspace-manager public tool

The Brain's MCP servers are only `episodic-memory`, `orchestrator`, `research`,
`ollama` (`commander/src/ironclaude/brain_client.py:716-743`); its allowed tools are
`mcp__orchestrator__*` / `mcp__episodic-memory__*` (`:405-416`). The Brain has NO
workspace-manager MCP server — so a tool added to workspace-manager's
`dispatchPublicTool` (the public surface, which is also identity-bound to the caller
session) is unreachable by the Brain. Workers have the workspace-manager plugin MCP
(`worker/.mcp.json`), but the WORKER is the party that DISCOVERS the missing data,
and the Brain is the party that must FIX it operator-free.

The Brain already reaches workspace-manager exactly one way: through the Commander's
`orchestrator` MCP → `self._workspace_client` (`orchestrator_mcp.py`) →
`node dist/cli.js <internal-command> <json>` (`WorkspaceClient._COMMANDS`,
`workspace_client.py:16`) → identity-free `createInternalCommandDependencies(db)`
(`cli.ts:109`) → `WorkspaceService` → git.ts. `recover_worker_integration`
(`orchestrator_mcp.py:3922` method, `:7175` `@mcp.tool()` wrapper) is the exact
Brain-callable template: resolve `ssh_host` from the worker registry (local when no
worker), `discover_installed_plugin_root`, `_workspace_transport`, call a
`self._workspace_client.<cmd>(payload, **transport)`, return a structured dict and
never raise.

## Requirements (operator-approved; unchanged by the amendment)

- R1. The Brain can configure `worktree-shared-resources` for a repository through a
  **self-serve orchestrator MCP tool** (proxying to workspace-manager via the
  existing WorkspaceClient/cli.js internal-command path), with no operator action and
  without hand-writing symlinks or worktree-fiddling scripts.
- R2. Entries are validated exactly as the shipped mechanism validates them
  (`isSafeSharedEntry`): no globs, `..`, absolute paths, trailing-slash, or `!`/`#`
  prefixes. Only explicit paths — never an auto-scan of `.gitignore`.
- R3. Adding an entry provisions it not only on the next allocation but also into
  every currently-active **managed** worktree for that repo (relink), so a live worker
  gets the data without a respawn.
- R4 (distinct task). A generalized behavioral guardrail: when a plan step/gate
  depends on a declared input that is absent, the agent surfaces a real blocker or
  produces the input through the workflow — never skips/overrides/rationalizes past
  the gate to emit a green-but-empty result. Project-agnostic.
- R5. Discoverability: the mechanism + the new orchestrator tool are documented where
  the Brain and workers will find them (README, brain rules naming the ORCHESTRATOR
  tool, `use-managed-worktree` skill, worker templates telling the worker to report
  the missing path to the Brain — never hand-symlink), so the fallback never recurs.
- R6. No project-specific logic (roleplaying-agents/pf2e/vision) anywhere in IronClaude.

## Root cause (evidence, verified against current source)

- Mechanism born in `docs/plans/2026-08-07-managed-worktree-usability-fixes-design.md`
  #9 (lines 169-185): explicit per-repo config, symlink at allocation, "absent file ⇒
  no linking", "never an auto-scan of `.gitignore`". Self-serve was never designed.
- **The Brain cannot reach workspace-manager's public tools.** `brain_client.py:716-743`
  (MCP servers), `:405-416` (allowed-tool prefixes). Public surface is identity-bound
  (`index.ts:287` `createPublicToolDependencies(db, identity)`; `list_active_assignments`
  filters `owner_session_id = identity.sessionId`, `index.ts:344-352`). Commander's only
  workspace-manager path is the internal cli.js commands (`workspace_client.py:16`,
  `:188` `if command not in _COMMANDS`).
- Discoverability gap: 0 hits for `worktree-shared-resources` in `README.md`,
  `worker/skills/`, `commander/src/brain/rules/`, `commander/src/ironclaude/templates/`.
- Shipped functions (git.ts): `SHARED_RESOURCE_CONFIG` (:136, private),
  `isSafeSharedEntry` (:197, private), `readSharedResourceConfig` (:187, exported),
  `linkSharedResources` (:227, exported — skips source-absent + already-present;
  currently returns `void` though it accumulates a `linked` array at :233),
  `ensureExcludeEntries` (:177), `discoverRepository` (:84), `listWorktrees` (:53).
- Bridge templates: `WorkspaceClient._COMMANDS`+thin methods (`workspace_client.py:16,216-237`);
  cli.ts `INTERNAL_COMMAND_NAMES`+`dispatchInternalCommand`+`createInternalCommandDependencies`
  (`cli.ts:30,97-104,109`); orchestrator `recover_worker_integration` (`orchestrator_mcp.py:3922,7175`).
- Deferred relink slice: `docs/plans/2026-08-19-worktree-reaper-leak-design.md:127`.

## Architecture

Approach B (self-serve tool), corrected to the orchestrator path. Six tasks,
dependency-ordered. TS layers (Tasks 1-2) then the Python bridge + orchestrator tool
(Task 3), then docs (4-5), then the distinct guardrail (6).

### Task 1 — git.ts: `addSharedResourceEntries` helper + `linkSharedResources` returns linked set

- Add `addSharedResourceEntries(repositoryIdentity, entries)` in git.ts (reuses the
  module-private `isSafeSharedEntry`/`SHARED_RESOURCE_CONFIG`): validate, dedupe against
  `readSharedResourceConfig`, append new valid entries; return
  `{added, skipped, rejected, entries}`.
- Change `linkSharedResources` to **return the `string[]` it already accumulates**
  (the `linked` array at git.ts:233) instead of `void` — non-breaking (current caller
  `workspace-service.ts:259` ignores the return). This makes the relink handler report
  what was actually planted (I4), distinguishing a source-absent skip from a real link.

### Task 2 — cli.ts internal commands + WorkspaceService handlers

- WorkspaceService: `configureSharedResources({repository_path, entries})` →
  `discoverRepository` → `addSharedResourceEntries` → for each live **managed** worktree
  (enumerated from the assignments DB, see below) call `linkSharedResources` (returns
  planted entries) + rely on its `ensureExcludeEntries`; return
  `{added, skipped, rejected, entries, relinked: {<worktreePath>: <plantedEntries>}}`.
  `listSharedResources({repository_path})` → `readSharedResourceConfig`.
- Managed-worktree enumeration (I2): the internal handler has `this.db` — query
  `assignments WHERE repository_identity = ? AND lifecycle_status NOT IN
  ('integrated','abandoned','cleaned')`, keep rows whose `worktree_path` exists on disk.
  Do NOT copy `list_active_assignments` (its `owner_session_id` filter would relink only
  one worktree, violating R3). Do NOT use bare `listWorktrees` minus primary/bare (admits
  operator-created + orphaned worktrees). (Ground the exact assignments table/columns +
  lifecycle values against `db.ts`/`workspace-service.ts` at planning.)
- cli.ts: add `configure-shared-resources`, `list-shared-resources` to
  `INTERNAL_COMMAND_NAMES` + `dispatchInternalCommand` switch + the
  `InternalCommandDependencies` type and `createInternalCommandDependencies`.
- Rebuild bundle (`npm run build` → `dist/{index,cli,hook-intent}.js`).

### Task 3 — Python bridge + orchestrator MCP tools

- `WorkspaceClient` (`workspace_client.py`): add `configure-shared-resources`,
  `list-shared-resources` to `_COMMANDS`; add two thin methods calling
  `self._invoke(command, payload, **transport)`.
- `orchestrator_mcp.py`: add `configure_shared_resources(repository_path, entries, worker_id=None)`
  and `list_shared_resources(repository_path, worker_id=None)` methods + `@mcp.tool()`
  wrappers, modeled on `recover_worker_integration` (ssh_host from registry when
  `worker_id` given else local; `discover_installed_plugin_root`; `_workspace_transport`;
  return structured dict, never raise). The Brain's tool guard already admits
  `mcp__orchestrator__*` — no `GATED_TOOLS` entry needed.

### Task 4 — Discoverability docs

README (mechanism, self-serve via the orchestrator tool, explicit-entries-only
security, write-through-symlink caution), brain rules (call the orchestrator
`configure_shared_resources`; never hand `ln -s` / never ask the operator),
`use-managed-worktree` skill, CHANGELOG. **Include a deploy note**: the Brain runtime
loads workspace-manager's `cli.js` from the plugin cache (a copy), so the tool takes
effect after a plugin-cache refresh (marketplace reinstall) — not on `npm run build`
alone.

### Task 5 — Worker templates (R5 audience)

`commander/src/ironclaude/templates/worker_agents.md` + `worker_claude_md.md`: when
gitignored data is absent from the worktree, report the path to the Brain as a blocker
— never hand-symlink or fiddle with the worktree. (Complements the Brain-rules half.)

### Task 6 (distinct) — never-fake-past-a-gate guardrail

Generalized rule in brain rules + executing-plans discipline: a plan step/gate whose
declared input is absent must surface a real blocker or produce it via the workflow —
never skip/override/rationalize to fake green. Detection cue: a required-input check
that "passes" only because it was disabled/skipped. No runtime code; kept distinct so
it reviews/reverts independently.

## Components

1. `worker/mcp-servers/workspace-manager/src/git.ts` — `addSharedResourceEntries`;
   `linkSharedResources` return type.
2. `worker/mcp-servers/workspace-manager/src/workspace-service.ts` — the two handlers +
   managed-worktree enumeration.
3. `worker/mcp-servers/workspace-manager/src/cli.ts` — two internal commands.
4. `worker/mcp-servers/workspace-manager/src/index.ts` — only if a shared type lives
   there; the PUBLIC tool surface is NOT used (verify at planning whether any index.ts
   edit is needed; likely none).
5. workspace-manager tests: `__tests__/git.test.ts`, `__tests__/workspace-service.test.ts`,
   `__tests__/tool-dispatch.test.ts` (INTERNAL list `INTERNAL_COMMANDS` + `internalDependencies()`
   mock keys — I1).
6. workspace-manager bundle: `dist/{index,cli,hook-intent}.js`.
7. `commander/src/ironclaude/workspace_client.py` + `commander/tests/test_workspace_client.py`
   (hardcoded command tuples — add the two).
8. `commander/src/ironclaude/orchestrator_mcp.py` + orchestrator tool-registration tests.
9. Docs: `README.md`, `commander/src/brain/rules/workflow.md`,
   `worker/skills/use-managed-worktree/SKILL.md`, `CHANGELOG.md`.
10. Worker templates: `commander/src/ironclaude/templates/worker_agents.md`,
    `worker_claude_md.md`.
11. Guardrail: `commander/src/brain/rules/workflow.md`, `worker/skills/executing-plans/SKILL.md`.

## Data Flow

1. A worker finds it needs gitignored project data absent from its worktree → reports
   the path to the Brain as a blocker (worker templates), never hand-symlinks.
2. Brain calls the orchestrator `configure_shared_resources({repository_path, entries, worker_id})`.
3. Orchestrator resolves transport (`discover_installed_plugin_root` + `_workspace_transport`)
   → `WorkspaceClient.configure_shared_resources` → cli.js internal command → WorkspaceService
   → `addSharedResourceEntries` (append config) → relink `added` into each live managed
   worktree (`linkSharedResources` returns planted set) → returns
   `{added, skipped, rejected, entries, relinked}`.
4. Next allocation AND current live managed worktrees have the data. Operator-free.
5. If a required input is genuinely absent from the primary (nothing to link), Task 6's
   guardrail makes the agent surface a real blocker instead of faking green.

## Error Handling

- Validation mandatory/reused (`isSafeSharedEntry`): invalid entries refused +
  reported (`rejected`), never written.
- Source-absent is not an error: `linkSharedResources` skips it (returns it as not
  planted) — configuring before data exists is safe.
- Idempotent: re-adding dedupes; re-linking an already-present path is skipped.
- Repo-scoped: binds to `repository_path`; only that repo's config + its own managed
  worktrees. The orchestrator tool never raises (structured error dict), matching
  `recover_worker_integration`.
- Truthful reporting (I4): `relinked` reports the actually-planted set per worktree, so
  the Brain never tells a worker data is present when it is not.
- Write-through hazard (noted, not solved): shared-resource symlinks are write-through
  to primary; a consumer clearing/backfilling through them mutates the primary.
  Documented as a caution; read-only-link mode is out of scope/YAGNI.

## Testing Strategy

- Task 1 (TDD, git.test.ts): `addSharedResourceEntries` valid/dedupe/rejected/absent-file;
  `linkSharedResources` returns the planted set (no existing test there — add).
- Task 2 (TDD, workspace-service.test.ts uses real `git init` + `ensureSessionWorktree`
  so symlink/exclude actually exercise): configure appends + returns; a live managed
  worktree gets symlink + `/entry` exclude + is in `relinked`; source-absent entry
  written to config but NOT linked (absent from `relinked`). Negative bounding (I2): an
  operator worktree outside `.ironclaude/worktrees/` and an orphaned managed dir with no
  DB row are NOT relinked. tool-dispatch.test.ts: add both to `INTERNAL_COMMANDS` +
  `internalDependencies()` mock (`it.each` auto-covers dispatch).
- Task 3 (TDD): `test_workspace_client.py` add both methods to its hardcoded
  hasattr/command loops; orchestrator tool-registration test asserts the two tool names
  present (membership, not exact set).
- Tasks 4/5/6: doc/rule text — non-vacuous grep markers (confirmed absent in current
  source). No runtime tests.
- Full workspace-manager vitest + affected commander pytest green; bundle rebuilt.

## Implementation Notes

- Scope=selective: R1-R6. Non-goals: entry-removal tool, read-only link mode, auto-scan,
  orphan-worktree reaper.
- Project-agnostic (R6). Re-ground every symbol/line/table against current source at
  planning (numbers here are from planning reads).
- Deploy: `npm run build` refreshes source dist; the Brain runtime loads `cli.js` from
  the plugin cache, so a plugin-cache refresh is required for the Brain to call the new
  tool — state this in the plan and docs.
- Lands as staged changes on top of v1.1.8 (`7f7f6c6`); commit/version/push are the
  operator's separate call. No push.
