# Proactive Per-Orphan Discussion Implementation Plan (Feature A)

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Give the Brain a read-only `list_surfaced_orphans` tool and rewrite its orphan rule so it proactively offers and then walks the operator through each preserved orphan one at a time with a category-driven recommendation, resolving each via the unchanged `resolve_orphan`.

**Requirements:** docs/plans/2026-09-22-proactive-orphan-discussion-requirements.md

**Architecture:** Approach A — one read-only tool chain (`WorkspaceService.listSurfacedOrphans` → `list-surfaced-orphans` CLI verb → `WorkspaceClient.list_surfaced_orphans` → orchestrator `list_surfaced_orphans` MCP tool) reading the existing workspace-manager `orphan_surface` table, plus a `brain/rules/workflow.md` rewrite. No schema changes, no change to `resolve_orphan` / `reapAmbiguousOrphans` / the daemon surfacing.

**Tech Stack:** TypeScript (better-sqlite3, vitest) for workspace-manager; Python (pytest) for Commander; Markdown for the Brain rule.

---

## Task 1: `listSurfacedOrphans` service method + test

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`

**Step 1 (RED): Add the test.** Inside the existing `describe('reapAmbiguousOrphans', ...)` block (which already defines `orphanBranch`, `orphanPath`, `createOrphanWorktree`, `commitFile`), add a nested suite. It seeds a real row-less unmerged orphan via `reapAmbiguousOrphans`, asserts `listSurfacedOrphans` returns it with the derived branch, then `keep`-mutes it and asserts it is excluded.

```ts
  describe('listSurfacedOrphans', () => {
    it('returns surfaced orphans with derived branch and excludes muted (kept) ones', () => {
      const root = repository();
      const database = initDb(join(root, 'list-surfaced.db'));
      const manager = new WorkspaceService(database);
      const guid = randomUUID();
      const worktreePath = createOrphanWorktree(root, guid);
      commitFile(worktreePath, 'unmerged.txt', 'unmerged\n');
      const tip = git(worktreePath, 'rev-parse', 'HEAD');

      const swept = manager.reapAmbiguousOrphans({ repositoryPath: root, ttlHours: 0 });
      expect(swept.preservedUnmerged).toEqual([orphanBranch(guid)]);

      const listed = manager.listSurfacedOrphans({ repositoryPath: root });
      expect(listed.orphans).toHaveLength(1);
      expect(listed.orphans[0]).toMatchObject({
        workspace_guid: guid,
        branch: orphanBranch(guid),
        tip,
        category: 'genuinely-unmerged',
      });
      expect(listed.orphans[0].id).toMatch(/^[0-9a-f]{8}$/);

      // keep-mute it: a kept orphan (muted_tip == tip) must drop out of the read.
      manager.resolveOrphan({
        repositoryPath: root,
        resolutions: [{ id: listed.orphans[0].id, action: 'keep' }],
      });
      expect(manager.listSurfacedOrphans({ repositoryPath: root }).orphans).toEqual([]);
    });
  });
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/workspace-service.test.ts
```
Expected: RED — the new test fails because `listSurfacedOrphans` does not exist on `WorkspaceService`.

**Step 2 (GREEN): Add the method.** Add a public method to the `WorkspaceService` class, immediately after `reapAmbiguousOrphans` (before `resolveOrphan`). It reads `orphan_surface` for the repo, excludes muted rows (`muted_tip === tip`), and derives `branch` via the module-level `managedBranch`.

```ts
  /**
   * Read-only enumeration of the currently-surfaced orphans for a repo (the
   * rows `reapAmbiguousOrphans` wrote to `orphan_surface`), for the Brain's
   * per-orphan walkthrough. Excludes rows the operator has `keep`-muted
   * (muted_tip === tip) so a kept orphan drops out until its tip changes.
   * Purely read-only: it never writes, and never re-runs the reaper.
   */
  listSurfacedOrphans(input: { repositoryPath: string }): {
    orphans: Array<{ id: string; workspace_guid: string; branch: string; tip: string; category: string }>;
  } {
    const repository = discoverRepository(input.repositoryPath);
    const rows = this.db.prepare(`
      SELECT workspace_guid, short_id, tip, category, muted_tip FROM orphan_surface
      WHERE repository_identity = ?
    `).all(repository.repositoryIdentity) as Array<{
      workspace_guid: string; short_id: string; tip: string; category: string; muted_tip: string | null;
    }>;
    const orphans = rows
      .filter((row) => row.muted_tip !== row.tip)
      .map((row) => ({
        id: row.short_id,
        workspace_guid: row.workspace_guid,
        branch: managedBranch(row.workspace_guid),
        tip: row.tip,
        category: row.category,
      }));
    return { orphans };
  }
```

Run the same vitest command. Expected: GREEN — the new test passes; no other workspace-service test regresses.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/workspace-service.ts worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts
```
Expected: staged (professional mode blocks commit).

---

## Task 2: `list-surfaced-orphans` CLI verb + test

**Files:**
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts`
- Test: `worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts`

**Depends on:** Task 1 (the handler calls `service.listSurfacedOrphans`).

**Step 1 (RED): Add the test.** In `cli.test.ts`, add `listSurfacedOrphans: vi.fn().mockReturnValue({ orphans: [] })` to the `internalDependencies()` stub (next to `listSharedResources`), then add a describe block mirroring the `reap-orphans` dispatch tests.

```ts
describe('workspace-manager internal CLI: list-surfaced-orphans command (read-only orphan enumeration)', () => {
  it('exposes list-surfaced-orphans as an internal command alongside the existing set', () => {
    expect(INTERNAL_COMMAND_NAMES).toContain('list-surfaced-orphans');
  });

  it('dispatches the list-surfaced-orphans command to its handler exactly once', () => {
    const dependencies = internalDependencies();
    const result = dispatchInternalCommand('list-surfaced-orphans', { marker: 'list-surfaced-orphans' }, dependencies);
    expect(dependencies.listSurfacedOrphans).toHaveBeenCalledWith({ marker: 'list-surfaced-orphans' });
    expect(dependencies.listSurfacedOrphans).toHaveBeenCalledTimes(1);
    expect(result).toBeDefined();
  });
});
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run src/__tests__/cli.test.ts
```
Expected: RED — `INTERNAL_COMMAND_NAMES` does not contain `list-surfaced-orphans`, and `dispatchInternalCommand` throws `Unknown internal workspace command: list-surfaced-orphans`.

**Step 2 (GREEN): Register the verb in four places in `cli.ts`**, mirroring `listSharedResources` (camelCase interface key, kebab verb name):

1. In `interface InternalCommandDependencies`, add:
```ts
  listSurfacedOrphans: InternalDependency;
```
2. In `INTERNAL_COMMAND_NAMES`, add `'list-surfaced-orphans'` (append before the closing `] as const`).
3. In `dispatchInternalCommand`'s switch, add:
```ts
    case 'list-surfaced-orphans': return dependencies.listSurfacedOrphans(args);
```
4. In `createInternalCommandDependencies`'s returned object, add:
```ts
    listSurfacedOrphans: (args) => service.listSurfacedOrphans({
      repositoryPath: requiredString(args, 'repository_path'),
    }),
```

Run the same vitest command. Expected: GREEN — both new tests pass; no other cli test regresses.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/mcp-servers/workspace-manager/src/cli.ts worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts
```
Expected: staged.

---

## Task 3: Python client `list_surfaced_orphans` + test

**Files:**
- Modify: `commander/src/ironclaude/workspace_client.py`
- Test: `commander/tests/test_workspace_client.py`

**Step 1 (RED): Add the test**, mirroring `test_reap_orphans_forwards_payload_into_cli_argv`:

```py
def test_list_surfaced_orphans_forwards_payload_into_cli_argv(tmp_path: Path):
    runner = Mock(return_value=completed('{"orphans":[]}\n'))
    client = WorkspaceClient(tmp_path, runner=runner)
    payload = {"repository_path": "/r"}

    assert client.list_surfaced_orphans(payload) == {"orphans": []}
    argv = runner.call_args.args[0]
    assert argv[:3] == [
        "node",
        str(tmp_path / "mcp-servers/workspace-manager/dist/cli.js"),
        "list-surfaced-orphans",
    ]
    assert json.loads(argv[3]) == payload
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_workspace_client.py -q
```
Expected: RED — `WorkspaceClient` has no `list_surfaced_orphans`, and/or the verb is not in `_COMMANDS`.

**Step 2 (GREEN):** In `workspace_client.py`, add `"list-surfaced-orphans"` to the `_COMMANDS` frozenset, and add the one-line method next to `list_shared_resources`:

```py
    def list_surfaced_orphans(self, payload: dict[str, Any], **transport: Any) -> dict[str, Any]:
        return self._invoke("list-surfaced-orphans", payload, **transport)
```

Run the same pytest command. Expected: GREEN.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/workspace_client.py commander/tests/test_workspace_client.py
```
Expected: staged.

---

## Task 4: Orchestrator `list_surfaced_orphans` tool + test

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Test: `commander/tests/test_orchestrator_mcp.py`

**Depends on:** Task 3 (the tool calls `self._workspace_client.list_surfaced_orphans`).

**Step 1 (RED): Add the test**, mirroring `TestResolveOrphan` but WITHOUT the `_live_worker_worktree_paths` patch (a read tool needs no protected_paths):

```py
class TestListSurfacedOrphans:
    """Brain self-serve read tool enumerating surfaced orphans for the
    per-orphan walkthrough; delegates to workspace-manager's
    list-surfaced-orphans verb, no protected_paths (read-only)."""

    def _tools(self):
        tools = object.__new__(OrchestratorTools)
        tools.registry = MagicMock()
        tools._db = init_db(":memory:")
        tools.tmux = MagicMock()
        tools._workspace_client = MagicMock()
        tools._workspace_client.discover_installed_plugin_root.return_value = "/installed/claude"
        tools._ensure_ssh_manager = MagicMock()
        tools._resolve_ssh_host = MagicMock(return_value=None)
        return tools

    def test_list_surfaced_orphans_forwards_repository_path(self):
        tools = self._tools()
        expected = {"orphans": [{"id": "ab12cd34", "workspace_guid": "g1",
                                 "branch": "ironclaude/g1", "tip": "abc", "category": "genuinely-unmerged"}]}
        tools._workspace_client.list_surfaced_orphans.return_value = expected

        result = tools.list_surfaced_orphans("/repo")

        assert result == expected
        call = tools._workspace_client.list_surfaced_orphans.call_args
        assert call.args[0] == {"repository_path": "/repo"}
        assert call.kwargs == {"plugin_root": "/installed/claude"}

    def test_list_surfaced_orphans_never_raises_on_workspace_error(self):
        tools = self._tools()
        tools._workspace_client.list_surfaced_orphans.side_effect = RuntimeError("boom")

        result = tools.list_surfaced_orphans("/repo")

        assert result["error"] == "boom"
        assert result["failure_phase"] == "list_surfaced_orphans"
        assert result["repository_path"] == "/repo"
```

Run:
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_orchestrator_mcp.py -q -k ListSurfacedOrphans
```
Expected: RED — `OrchestratorTools` has no `list_surfaced_orphans`.

**Step 2 (GREEN):** In `orchestrator_mcp.py`, add the `OrchestratorTools.list_surfaced_orphans` method mirroring `list_shared_resources` (resolve transport via `_shared_resources_transport`, call the client, wrap errors — never raise):

```py
    def list_surfaced_orphans(
        self, repository_path: str, worker_id: str | None = None,
    ) -> dict:
        """Read the currently-surfaced orphans for a repo, for the Brain's
        per-orphan walkthrough.

        Returns {orphans: [{id, workspace_guid, branch, tip, category}]} (kept
        /muted orphans excluded) or a structured error dict; never raises.
        worker_id resolves transport from the worker registry when given; omit
        it for a Brain-local repository.
        """
        transport = self._shared_resources_transport(repository_path, worker_id)
        if "error" in transport:
            return transport
        try:
            return self._workspace_client.list_surfaced_orphans(
                {"repository_path": repository_path},
                **transport,
            )
        except Exception as exc:
            return {
                "error": str(exc),
                "failure_phase": "list_surfaced_orphans",
                "repository_path": repository_path,
            }
```

Add the thin MCP wrapper next to the `resolve_orphan`/`list_shared_resources` wrappers, mirroring `list_shared_resources`:

```py
    @mcp.tool()
    def list_surfaced_orphans(repository_path: str, worker_id: str = "") -> str:
        """Read the currently-surfaced orphaned worktrees for a repository, for
        a per-orphan review.

        Returns JSON {orphans: [{id, workspace_guid, branch, tip, category}]}
        (operator-kept/muted orphans excluded), or a structured error dict. Use
        the returned ids with resolve_orphan to act on the operator's decision.

        Args:
            repository_path: Absolute path to the git repository (primary checkout).
            worker_id: Resolves the worker's provider + host; omit for a
                Brain-local repository.
        """
        return json.dumps(
            tools.list_surfaced_orphans(repository_path, worker_id or None)
        )
```

Run the same pytest command. Expected: GREEN.

**Step 3: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_mcp.py
```
Expected: staged.

---

## Task 5: Rewrite the Brain orphan rule (workflow.md)

**Files:**
- Modify: `commander/src/brain/rules/workflow.md`

**No tests required:** Brain guidance prose — there is no behavioral harness for `workflow.md` content; it is validated by review. (Consistent with how the existing orphan rule is maintained.)

**Step 1: Replace the "Resolving Orphaned Worktrees (operator-consented cleanup)" section.** Replace the entire current section (heading `### Resolving Orphaned Worktrees (operator-consented cleanup)` through the end of its `**Rules:**` list, immediately before `### Never Fake Past a Missing-Input Gate`) with the following. It flips passive-wait to proactive-offer + operator-trigger + strictly-sequential walkthrough, and preserves every `resolve_orphan` rail verbatim.

```markdown
### Resolving Orphaned Worktrees (operator-consented per-orphan walkthrough)

The daemon surfaces preserved orphaned worktrees to Slack with a stable short `id`, a `category` (squash-merged / merged-on-origin / genuinely-unmerged / dirty), and a `repository_path`. You now walk {OPERATOR_NAME} through them one at a time — but you still perform NO destructive action without {OPERATOR_NAME}'s explicit per-orphan consent.

**Read the surfaced set with a read-only tool:**
- `list_surfaced_orphans(repository_path, worker_id)` returns `{orphans: [{id, workspace_guid, branch, tip, category}]}` — the currently-surfaced orphans, with operator-`keep`-muted ones already excluded. It is read-only: it never reaps, keeps, or mutates anything. `worker_id` resolves the surfacing worker's host, if any; omit it for a Brain-local repository.

**Offer proactively, on your next wake (once per set):**
1. On a normal wake, if you have surfaced orphans you have not already offered this session, call `list_surfaced_orphans`. If it returns a non-empty set, offer once: "You have N preserved orphan(s) to review — want to walk through them?" Do NOT re-nag: if {OPERATOR_NAME} does not engage, wait for them to raise it rather than re-offering every wake.
2. {OPERATOR_NAME} may also open the review at any time ("review the orphans", "let's do the orphans").

**Walk them strictly one at a time:**
3. Call `list_surfaced_orphans(repository_path, worker_id)` to read the current set.
4. For EACH orphan in turn, present its `branch`, `category`, and `tip`, plus your recommendation and a one-line reason, then WAIT for {OPERATOR_NAME}'s decision before moving on. Recommendation by category:
   - `squash-merged` / `merged-on-origin` → recommend **reap** (the work is already on the default branch).
   - `genuinely-unmerged` → recommend **keep**; offer **merge-then-reap** if {OPERATOR_NAME} wants the work landed. Never push to reap.
   - `dirty` → recommend **keep** (it holds uncommitted work); reap only if {OPERATOR_NAME} explicitly consents to discarding it, naming the `dirty` category.
5. When {OPERATOR_NAME} decides an orphan, call `resolve_orphan(repository_path, resolutions, worker_id)` for that single orphan — `resolutions` is `[{"id": "<exact surfaced id>", "action": "reap"|"keep"|"merge-then-reap", "category": "<surfaced category, optional>"}]`, and `worker_id` is the surfacing worker, if any. Currently-live worker worktrees are threaded through automatically as `protected_paths`, so a live worktree is never resolved out from under a running worker. `merge-then-reap` integrates the orphan's work into the repo's canonical default branch (`refs/remotes/origin/HEAD`, fallback `main`) — NEVER the operator's current checkout. To target a different branch, {OPERATOR_NAME} includes `integration_target` (a branch name) in that resolution, e.g. `{"id": "<id>", "action": "merge-then-reap", "integration_target": "release/x"}`. Set a resolution's `category` ONLY from a category tag or label {OPERATOR_NAME}'s own message carries or quotes from the surfaced line — e.g. they write `reap ab12cd34 [dirty]` or "reap ab12cd34 the dirty one" — NEVER infer or guess it, and omit the field when {OPERATOR_NAME} did not name one. This matters because `reap` of a worktree that has since become dirty is force-removed (discarding its uncommitted work) only when consent explicitly named the `dirty` category; relaying the surfaced category verbatim is what lets {OPERATOR_NAME} authorize — or withhold — that discard.
6. Relay the per-id outcome back to {OPERATOR_NAME}, threaded under their message, then move to the next orphan.

**Rules:**
- **Never infer, invent, or guess an id.** Use the ids EXACTLY as `list_surfaced_orphans` (or the surfaced Slack line) reports them. NEVER reap without {OPERATOR_NAME} naming that specific orphan's disposition.
- The walkthrough only reads (`list_surfaced_orphans`), presents, and relays {OPERATOR_NAME}'s explicit per-orphan decision to `resolve_orphan`. It never reaps, keeps, or merges on its own.
- `reap` and `merge-then-reap` are destructive (worktree removed / branch deleted); `keep` mutes an id until its tip changes. Treat `reap`/`merge-then-reap` with the same care as any other irreversible action.
- Outcomes to relay: `reaped`, `kept`, `refused-changed` (tip changed since surfacing, OR uncommitted work the consent did not cover — for a dirty entry re-consent naming the dirty category; otherwise re-surface), `not-surfaced` (unknown id), `skipped-live` (a live worker owns it), `reaped-worktree-only`, `merged-then-reaped`, `conflict` (merge conflict — left for manual handling), `needs-manual-merge`, `refused-dirty` (merge-then-reap refuses a worktree with uncommitted changes — commit the work first, or use `reap` to discard it), `target-moved` (the integration target advanced concurrently — retry the same resolution; the orphan is unchanged), `error`.
```

**Step 2: Stage.**
```bash
git -C /Users/roberthyatt/Code/ironclaude add commander/src/brain/rules/workflow.md
```
Expected: staged.

---

## Task 6: Rebuild dist + full-suite verification

**Files:**
- Modify (build outputs): `worker/mcp-servers/workspace-manager/dist/cli.js`, `worker/mcp-servers/workspace-manager/dist/index.js`, `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

**Depends on:** Tasks 1–5.

**No tests required:** build + full-suite verification task (it runs the suites rather than adding one).

**Step 1: Rebuild the workspace-manager bundle** (Task 1/2 changed TS source):
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```
Expected: `tsc` + bundle succeed, no error.

**Step 2: Full workspace-manager vitest:**
```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx vitest run
```
Expected: `passed | 0 failed` (the `onTaskUpdate` RPC-timeout line is benign; judge by `0 failed`).

**Step 3: Full Commander pytest:**
```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest -q
```
Expected: `0 failed`.

**Step 4: Stage the rebuilt dist (force — dist is gitignored):**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```
Expected: staged.

---

## Deploy notes (post-merge, operator-gated — not part of execution)

- workspace-manager `dist/` → copy into both plugin caches (daemon-spawned workers load from cache).
- `workflow.md` + orchestrator changes are daemon-side → a Commander restart picks them up (the Brain reloads rules at startup; daemon runs from the working tree).
