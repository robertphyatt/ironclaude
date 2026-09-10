# Worktree Shared-Resources Self-Serve Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Give the Brain a self-serve ORCHESTRATOR MCP tool (`configure_shared_resources` / `list_shared_resources`) that proxies through the Commander's WorkspaceClient → workspace-manager cli.js internal-command path to the shipped git.ts shared-resource primitives, relinks new entries into already-live managed worktrees, documents the mechanism (Brain rules + worker templates), and adds a distinct generalized guardrail against faking past a missing-declared-input gate — so the Brain never hand-symlinks or stalls waiting on the operator.

**Requirements:** `docs/plans/2026-09-07-worktree-shared-resources-self-serve-design.md`

**Architecture:** The Brain has NO workspace-manager MCP (only episodic-memory/orchestrator/research/ollama). The self-serve surface is an ORCHESTRATOR tool, modeled on `recover_worker_integration` (orchestrator_mcp.py:3922/7175): resolve transport (`discover_installed_plugin_root` + `_workspace_transport`), call a new `WorkspaceClient` method → `node dist/cli.js <internal-command> <json>` → identity-free `createInternalCommandDependencies(db)` → `WorkspaceService` → git.ts `addSharedResourceEntries` + relink live **managed** worktrees (enumerated from the assignments DB).

**Tech Stack:** TypeScript (workspace-manager) + vitest; Python (commander) + pytest; markdown docs. No project-specific (pf2e) logic anywhere.

## Execution invariants (reviewer checks commands against these)

- Bash cwd is NOT stable; each command is self-contained. TS build/test: `cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && ...` (test=`npm test`; build=`npm run build` → `dist/{index,cli,hook-intent}.js`). Python test: `cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest ...`. Stage with `git -C /Users/roberthyatt/Code/ironclaude add …`; `docs/` gitignored (`add -f`).
- `SHARED_RESOURCE_CONFIG`/`isSafeSharedEntry` are PRIVATE in git.ts — `addSharedResourceEntries` lives IN git.ts to reuse them.
- Reused (verified current source): git.ts `readSharedResourceConfig`(:187), `isSafeSharedEntry`(:197,private), `linkSharedResources`(:227, returns void today — Task 1 makes it return its `linked` array), `ensureExcludeEntries`(:177), `discoverRepository`(:84); cli.ts `INTERNAL_COMMAND_NAMES`(:30)+`dispatchInternalCommand`(:97-104)+`createInternalCommandDependencies`(:109); `workspace_client.py` `_COMMANDS`(:16)+thin methods(:216-237)+`_invoke`(:179); orchestrator `recover_worker_integration`(:3922/7175), `discover_installed_plugin_root`, `_workspace_transport`(:2831). Assignments table: `assignments(repository_identity, worktree_path, lifecycle_status)`; live set = `lifecycle_status NOT IN ('integrated','abandoned','cleaned')` (db.ts:99/149).
- Test structures: `tool-dispatch.test.ts` `INTERNAL_COMMANDS`(:40)+`internalDependencies()`(:60-71)+`toEqual(INTERNAL_COMMAND_NAMES)`(:105); `test_workspace_client.py` hasattr set(:65-67)+exact-command loop(:76); orchestrator tool assert = `_tool_manager.get_tool("<name>").fn`.
- Scope=selective: R1-R6. Non-goals: entry-removal tool, read-only link mode, auto-scan, orphan reaper. The workspace-manager PUBLIC tool surface (`dispatchPublicTool`) is NOT used.

---

## Task 1: git.ts — `addSharedResourceEntries` helper + `linkSharedResources` returns planted set

> **RE-RUN NOTE:** this task's code (`git.ts` + `git.test.ts`) is ALREADY STAGED from a prior execution (this plan re-runs after an execution-order retreat). Its RED step is **verification-only** — run the test command and expect **PASS**, then stage. No new code changes in this task.

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`

**Step 1 (verification-only, NOT RED):** In `git.test.ts` import `addSharedResourceEntries`, `readSharedResourceConfig`, `linkSharedResources`. Assert: (a) valid entries appended + returned as `added`; (b) re-add dedupes (`skipped`); (c) invalid (`../x`,`/abs`,`a*`,`x/`,`!x`,`#x`) → `rejected`, NOT written; (d) absent config → created; (e) `linkSharedResources` RETURNS the planted `string[]` (a linked entry present, a source-absent entry absent from the returned array). Use a tmp git-common-dir with `info/` for the config tests; a tmp primary+worktree dir pair for the link test.

**Step 2: Verify (code staged — verification-only).**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- git.test
```
Expected: PASS — `git.test` suite green (`addSharedResourceEntries` + `linkSharedResources` planted-set tests already implemented).

**Step 3 (GREEN):** Add `addSharedResourceEntries(repositoryIdentity, entries): {added, skipped, rejected, entries}` near `readSharedResourceConfig`, reusing `isSafeSharedEntry`/`SHARED_RESOURCE_CONFIG`/existing `mkdirSync`/`readFileSync`/`writeFileSync`/`existsSync`; only write when `added.length>0`, preserving the trailing-newline invariant. Change `linkSharedResources`' signature to `: string[]` and `return linked;` at its end (the array it already accumulates at git.ts:233) — non-breaking (caller at workspace-service.ts:259 ignores the return).

**Step 4: Run GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- git.test
```
Expected: passed.

**Step 5: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts
```

---

## Task 2: cli.ts internal commands + WorkspaceService handlers + bundle

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts` (its hardcoded `internalDependencies()` mock needs the two new `InternalCommandDependencies` keys or `tsc` fails at build — audited as the only other hardcoded internal-deps mock; `integration-cases.ts` uses the real factory; `PublicToolDependencies` is untouched)
- Modify: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Task 1.

**Step 1 (RED — this task has GENUINELY NEW work, so RED really fails):** In `workspace-service.test.ts` (real `git init` + `ensureSessionWorktree` harness) assert `configureSharedResources({repository_path, entries})`: appends valid entries + returns `{added, skipped, rejected, entries, relinked}`; a live managed worktree gets symlink + `/entry` in `info/exclude` and appears in `relinked` with the planted entry when the primary source EXISTS; a source-ABSENT entry is written to config but NOT in `relinked` (no dangling link). Negative bounding (I2): an operator worktree OUTSIDE `.ironclaude/worktrees/` (no assignments row) and an orphaned managed dir with no live assignments row are NOT relinked.
> **N1 (must-fix, would-have-caught bug):** `listSharedResources` returns an OBJECT `{entries: string[]}`, NOT a bare array — the Commander's `WorkspaceClient._decode` rejects any non-object JSON (`workspace_client.py:58`), so a bare-array return makes the orchestrator `list_shared_resources` tool return `{"error": ...}` on **every** real call. Assert the handler returns `{entries: [...]}` matching the config.
> **N3 (falsifiability):** add a discriminating test — a MANAGED assignments row `UPDATE`d to `lifecycle_status='integrated'` (harness already does a direct `UPDATE`) with `worktree_path` still on disk is ABSENT from `relinked` and gets NO symlink. This is the ONLY test that fails if the `lifecycle_status NOT IN ('integrated','abandoned','cleaned')` clause is deleted.
>
> In `tool-dispatch.test.ts` add `configure-shared-resources` + `list-shared-resources` to `INTERNAL_COMMANDS` (:40) and both mock keys to `internalDependencies()` (:60-71) — `configureSharedResources` returns an object, `listSharedResources` returns `{entries: []}`. Also add the same two mock keys to `cli.test.ts`'s hardcoded `internalDependencies()` (~:19-30) (`listSharedResources` → `{entries: []}`), or `tsc` fails at build on the missing `InternalCommandDependencies` properties.

**Step 2: Run RED.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- workspace-service tool-dispatch
```
Expected: FAIL — handlers + internal commands not defined; `INTERNAL_COMMAND_NAMES` ≠ updated `INTERNAL_COMMANDS`.

**Step 3 (GREEN — workspace-service.ts):** add `configureSharedResources` (discoverRepository → `addSharedResourceEntries` → enumerate live managed worktrees via `SELECT worktree_path FROM assignments WHERE repository_identity = ? AND lifecycle_status NOT IN ('integrated','abandoned','cleaned')`, keep rows whose `worktree_path` exists on disk, call `linkSharedResources(primaryCheckoutPath, worktreePath, repositoryIdentity, added)` capturing the returned planted set → `relinked[worktreePath]=planted`; return `{added, skipped, rejected, entries, relinked}`) and `listSharedResources` RETURNING an object `{entries: readSharedResourceConfig(...)}` (type `{entries: string[]}`) — **not** a bare array, so `WorkspaceClient._decode` (requires object) accepts it. Keep the `lifecycle_status NOT IN (...)` clause (N3 test guards it).

**Step 4 (GREEN — cli.ts):** add `'configure-shared-resources'`, `'list-shared-resources'` to `INTERNAL_COMMAND_NAMES` (:30), `dispatchInternalCommand` cases (:97-104), and the `InternalCommandDependencies` type + `createInternalCommandDependencies` (:109) wiring to the two service handlers.

**Step 5: Run GREEN.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- workspace-service tool-dispatch
```
Expected: passed.

**Step 6: Build bundle.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: tsc clean + 3 dist bundles written; no errors.

**Step 7: Full TS suite.**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test
```
Expected: 0 failed.

**Step 8: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/cli.ts worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

---

## Task 3: Python bridge (WorkspaceClient) + orchestrator MCP tools

> **RE-RUN NOTE:** the main code (`workspace_client.py` + `orchestrator_mcp.py`) and its tests are ALREADY STAGED from a prior execution; those steps are verification-only (tests already pass). The ONE genuinely new addition here is a `WorkspaceClient._decode` negative test (Step 1).

**Files:**
- Modify: `commander/src/ironclaude/workspace_client.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Test: `commander/tests/test_workspace_client.py`
- Test: `commander/tests/test_orchestrator_mcp.py`

**Depends on:** Task 2.

**Step 1 (workspace_client tests):** two SEPARATE functions are already staged — `test_exposes_shared_resource_methods` (hasattr both) and `test_shared_resource_methods_forward_hyphenated_internal_commands` (each forwards its exact hyphenated internal command + payload). **NEW work (would have caught N1):** add a `_decode` negative test — a `completed()` result whose stdout is a JSON ARRAY (e.g. `completed('["data/x"]\n')`) makes `WorkspaceClient` raise `WorkspaceClientError('... JSON response must be an object')`. This proves the boundary the real cli.js list path crosses (why `listSharedResources` MUST return an object).

**Step 2 (orchestrator tests — already staged, verify):** `test_orchestrator_mcp.py` class `TestConfigureSharedResources` (5 tests): registration (`_tool_manager.get_tool("configure_shared_resources").fn` / `list_shared_resources`), local configure/list forward exact payload + installed transport, never-raise on `WorkspaceClientError`, and worker-not-found does-not-discover. Verify present; no new change.

**Step 3: Verify workspace_client tests (staged code + new `_decode` negative test; verification, not RED).**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_workspace_client.py -k "shared_resource or exact_internal or lifecycle_methods or decode" -q
```
Expected: PASS — shared-resource method tests + exact-internal-command + no-push-lifecycle + `_decode` negative test all green. (`_decode` already rejects arrays, so the new negative test passes against current code.)

**Step 4 (workspace_client.py — already staged, verify):** `'configure-shared-resources'`, `'list-shared-resources'` in `_COMMANDS`; two thin methods `configure_shared_resources(payload, **transport)` / `list_shared_resources(payload, **transport)` calling `self._invoke('configure-shared-resources'|'list-shared-resources', payload, **transport)`. Verify present; no new change.

**Step 5 (orchestrator_mcp.py — already staged, verify):** `configure_shared_resources(self, repository_path, entries, worker_id=None)` + `list_shared_resources(self, repository_path, worker_id=None)` methods (+ `_shared_resources_transport` helper) near `~:3927-4025`, modeled on `recover_worker_integration` (staged `~:4027`): resolve ssh_host from registry when `worker_id` else local; `discover_installed_plugin_root`; `_workspace_transport`; call `self._workspace_client.<method>(payload, **transport)`; return structured dict, never raise. `@mcp.tool()` wrappers near `~:7295-7339` (mirror staged `recover_worker_integration` wrapper `~:7280`). Verify present; no new change.

**Step 6: Run GREEN (both suites).**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_workspace_client.py -q
```
Expected: 0 failed.
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -k "ConfigureSharedResources" -q
```
Expected: PASS — 5 passed (registration + local configure/list payload+transport + never-raise + worker-not-found). (`-k` matches the class name; `configure_shared`/`list_shared` would select nothing.)

**Step 7: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/workspace_client.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_workspace_client.py commander/tests/test_orchestrator_mcp.py
```

---

## Task 4: Discoverability docs

**Files:**
- Modify: `README.md`
- Modify: `commander/src/brain/rules/workflow.md`
- Modify: `worker/skills/use-managed-worktree/SKILL.md`
- Modify: `CHANGELOG.md`

**Depends on:** Task 3.

**Step 1: README.md** — "Managed-worktree shared resources" subsection: mechanism, self-serve via the orchestrator `configure_shared_resources` tool (never hand-symlink / never ask the operator), explicit-entries-only security, write-through-symlink caution, and the plugin-cache-refresh deploy note.
**Step 2: commander/src/brain/rules/workflow.md** — rule: worker needs gitignored project data absent from its worktree → call `configure_shared_resources`; NEVER hand `ln -s` or ask the operator to touch a worktree (operator-free-worktree pillar).
**Step 3: worker/skills/use-managed-worktree/SKILL.md** — document configuring shared resources via the tool as part of setup.
**Step 4: CHANGELOG.md** — Unreleased/next entry (orchestrator self-serve tool + live relink + discoverability).

**Step 5: Verify markers.**
```bash
grep -rn "configure_shared_resources" /Users/roberthyatt/Code/ironclaude/README.md /Users/roberthyatt/Code/ironclaude/commander/src/brain/rules/workflow.md /Users/roberthyatt/Code/ironclaude/worker/skills/use-managed-worktree/SKILL.md
```
Expected: at least one match in each of the three files.

**Step 6: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add README.md commander/src/brain/rules/workflow.md worker/skills/use-managed-worktree/SKILL.md CHANGELOG.md
```

No tests required: documentation-only (markers verified via grep).

---

## Task 5: Worker templates (R5 audience)

**Files:**
- Modify: `commander/src/ironclaude/templates/worker_agents.md`
- Modify: `commander/src/ironclaude/templates/worker_claude_md.md`

**Depends on:** Task 4.

**Step 1:** In BOTH templates add: when gitignored project data your task needs is absent from your worktree, report the exact path to the Brain as a blocker so it can provision it (`configure_shared_resources`); NEVER hand-write `ln -s` or fiddle with the worktree yourself.

**Step 2: Verify markers.**
```bash
grep -rn "configure_shared_resources" /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/templates/worker_agents.md /Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/templates/worker_claude_md.md
```
Expected: at least one match in each file.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/templates/worker_agents.md commander/src/ironclaude/templates/worker_claude_md.md
```

No tests required: template documentation (markers verified via grep).

---

## Task 6 (distinct): Never-fake-past-a-gate guardrail

**Files:**
- Modify: `commander/src/brain/rules/workflow.md`
- Modify: `worker/skills/executing-plans/SKILL.md`

**Depends on:** Task 5. (Ordered after Tasks 4/5 to avoid a `workflow.md` edit collision.)

**Step 1: commander/src/brain/rules/workflow.md** — generalized guardrail: declared input absent → surface a real blocker or produce it via the workflow; never skip/override/rationalize past the gate to fake green; detection cue = a required-input check that "passes" only because it was disabled/skipped.
**Step 2: worker/skills/executing-plans/SKILL.md** — same discipline at the execution boundary: a task whose declared input is missing must fail/escalate, not be silently satisfied.

**Step 3: Verify markers.**
```bash
grep -rn "surface a real blocker" /Users/roberthyatt/Code/ironclaude/commander/src/brain/rules/workflow.md /Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md
```
Expected: at least one match in each file.

**Step 4: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/brain/rules/workflow.md worker/skills/executing-plans/SKILL.md
```

No tests required: behavioral rule text (markers verified via grep). Kept distinct so it reviews/reverts independently.

---

## Notes

- Project-agnostic: no roleplaying-agents/pf2e/vision anywhere. Re-ground every symbol/line/table against current source at execution.
- Deploy: `npm run build` refreshes source dist; the Brain runtime loads workspace-manager's `cli.js` from the plugin cache (a copy), so the Brain can call the new tool only after a plugin-cache refresh (marketplace reinstall) — the plan does not claim runtime pickup on build alone.
- Lands as staged changes on top of v1.1.8 (`7f7f6c6`); commit/version/push are the operator's separate call. No push.
- Bundle version `index.ts:559` = `1.1.4` (pre-existing vs package 1.1.8) — leave unless a version-consistency test requires a bump (verify at execution).
