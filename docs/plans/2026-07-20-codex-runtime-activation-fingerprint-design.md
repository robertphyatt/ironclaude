# Codex Runtime Activation Fingerprint and Same-Task Restart Design

> **Created:** 2026-07-20
> **Status:** Design Complete
> **Roadmap loop:** Wave 1R — verified activation remediation before Wave 2
> **Authoritative inputs:** `docs/plans/2026-07-20-v1-1-overall-roadmap.md`, `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md`, and the verified post-install behavior recorded below
> **Requirements implemented:** MP-W12; preserve MP-W08 and MP-W11

## Summary

Roadmap Wave 1 implemented workflow-transition idempotency correctly in source, generated bundle, tests, and installed cache. Its first activation proof was invalid: without fully restarting Codex, the same task inferred runtime activation from `codex plugin list`, filesystem hashes, and identity/database diagnostics. A subsequent different-stage `mark_brainstorming` call changed the stage but returned the pre-Wave-1 response shape without required `changed: true`. The running Codex process still held the prior plugin/MCP registration even though source and installed cache were current.

This remediation separates three independent facts:

1. source bytes equal installed-cache bytes;
2. a Codex task exposes skills from the intended plugin version;
3. the running state-manager MCP process loaded the intended manifest and bundle.

Only all three prove activation. Live evidence now establishes the actual boundary: after an IronClaude installation or update, fully quit and relaunch Codex, then reopen the same native task. Codex re-resolves the current plugin catalog while preserving native task identity and goal state. A new task is recovery-only if same-task verification fails. The running MCP exposes a deterministic runtime fingerprint through the existing `run_diagnostics` tool. No in-process reload command, task-state copier, new workflow stage, or new MCP tool is introduced.

## Verified Failure and Root Cause

Current source and the installed `1.0.24+codex.20260720171005` bundle contain `workflowTransitionResult`, including `changed: true` and `changed: false`. All prescribed source/cache hashes matched. Before full application restart, this task's live `mark_brainstorming` response omitted `changed`, its injected skill locators remained bound to `1.0.24+codex.20260720062826`, and its MCP launcher repeatedly targeted that deleted cache path.

The task was repaired in place and then fully restarted. On reopening native task `019f7742-abd8-7c62-af7b-fe07189f1ffd`, Codex exposed skills from `1.0.24+codex.20260720171005`, loaded all 28 state-manager tools, preserved the same task ID and persistent roadmap goal, passed 11/11 provider-native identity diagnostics, and returned:

```json
{"success":true,"changed":true,"from":"idle","to":"brainstorming","session_id":"019f7742-abd8-7c62-af7b-fe07189f1ffd"}
```

This disproves the earlier fresh-task-only conclusion. Root cause is an application-process-bound plugin/MCP catalog, not an inability to refresh an existing task. Full Codex restart is required; creating a different task is not.

`run_diagnostics` currently proves provider-native identity, database availability, transactional probe rollback, and transport health. It reports no plugin version, plugin root, manifest hash, bundle path, or bundle hash. Therefore its 11/11 result was incapable of proving runtime activation. The invalid Task 4 conclusion came from treating installation evidence as process evidence.

The review packet also omitted the operator-approved roadmap. The reviewer received the requirements ledger, design, and plans, so it had no authoritative evidence against which to reject the same-task instruction. Multi-artifact requirements-packet/schema hardening remains assigned to Roadmap Wave 11; Wave 1R records that dependency without expanding into a review-lifecycle subsystem.

## Approaches Considered

### A. Full restart, reopen same task, and fingerprint diagnostics — selected

After every Codex plugin installation or update, fully quit/relaunch Codex and reopen the existing task. The task calls `run_diagnostics`, which reports the running plugin manifest version/root and SHA-256 of its manifest and loaded bundle. Activation gates compare those fields with intended source and installed cache, verify the native task ID is unchanged, and require one valid different-stage transition with `changed:true`.

**Pros:** preserves full native conversation and goal state; uses verified Codex restart behavior; directly distinguishes install state from process state; reuses existing diagnostics; requires no transcript handoff or state migration.

**Cons:** requires a full application restart; cannot repair the loaded catalog while the old Codex process remains alive.

**Guidance alignment:** highest. It directly follows the operator's instruction to fix and continue the current session, preserves native identity and context, avoids provider-neutral or reload infrastructure, and makes stale behavior impossible to certify.

### B. Fresh-task recovery after failed same-task verification

If the reopened task still exposes stale skill locators, lacks runtime fingerprint fields, resolves a different native identity, or fails the `changed:true` probe, stop and create a new task with the roadmap path, prior task ID, original objective, and raw native conversation reference.

**Pros:** provides a fail-closed recovery path when Codex cannot refresh the old task on a particular release or failure mode.

**Cons:** loses direct continuity and requires an explicit handoff; must not be used as the default after same-task restart has been proven.

**Guidance alignment:** acceptable only as recovery. It honors fail-closed activation without overriding operator preference for the existing task.

### C. Force in-process plugin/MCP reload

Add IronClaude commands that attempt to restart or replace Codex-owned tool and MCP bindings without quitting Codex.

**Pros:** could avoid the application restart if Codex exposed a supported reload primitive.

**Cons:** no supported primitive is present; IronClaude cannot rebuild Codex-owned skill/tool registration in the running process; implementation would depend on undocumented internals and still need a fingerprint.

**Guidance alignment:** poor. It adds speculative machinery and conflicts with YAGNI.

## Architecture

### Startup-captured runtime fingerprint helper

Add one pure helper module under the state-manager source tree. During server startup—before accepting any MCP requests—it receives the bundle module URL, resolves the following values, and stores the result in immutable process memory:

- plugin root;
- provider-active manifest path and parsed `name`/`version`: `.codex-plugin/plugin.json` for Codex, `.claude-plugin/plugin.json` for Claude;
- `mcp-servers/state-manager/dist/index.js` path;
- SHA-256 of the manifest bytes;
- SHA-256 of the bundle bytes;
- configured client from `IRONCLAUDE_CLIENT`.

Path derivation starts from `import.meta.url`, not `cwd`, environment guesses, the marketplace source path, or the newest cache directory. In the bundle and source-test layouts, walking from the module directory to the plugin root is deterministic. Tests inject temporary fixture paths rather than relying on the developer's live cache.

The manifest and bundle hashes are captured once at process startup, not reread when `run_diagnostics` is called. This distinction is mandatory: call-time hashes prove current disk bytes but can falsely certify a process that loaded older JavaScript before those files were replaced. Startup-captured hashes prove which files existed when this MCP process loaded, while the separate source/cache parity gate proves current installation bytes.

Missing files, malformed JSON, wrong plugin name, empty version, or unreadable bundle are explicit failures. `parseIronClaudeClient` remains the sole client-validation boundary and rejects unsupported values before fingerprint capture. The helper never substitutes the other provider's manifest, searches other cache versions, or falls back to source; any fallback would hide the exact stale-runtime or cross-client configuration condition this loop must detect.

### Existing `run_diagnostics` extension

`run_diagnostics` retains all current identity/database/rollback checks and adds a `Runtime fingerprint` check. Its structured JSON gains a required `runtime` object containing:

```json
{
  "plugin_name": "ironclaude",
  "plugin_version": "1.0.24+codex.<cachebuster>",
  "plugin_root": "/absolute/path/to/running/plugin",
  "manifest_path": "/absolute/path/to/.codex-plugin/plugin.json",
  "manifest_sha256": "<hex>",
  "bundle_path": "/absolute/path/to/dist/index.js",
  "bundle_sha256": "<hex>",
  "client": "codex"
}
```

The example above is the Codex shape; Claude reports `.claude-plugin/plugin.json` and `client: "claude"`. If startup fingerprint resolution fails, the server preserves the captured failure and the diagnostic check is `FAIL`; no fabricated partial runtime object is returned. `run_diagnostics` never recomputes or repairs the fingerprint. An old server has neither the check nor `runtime`; activation verification treats either absence as stale, never as backward-compatible success.

For executable activation verification, `run_diagnostics` accepts one optional `expected_runtime` object with exact `plugin_version`, `plugin_root`, `manifest_sha256`, `bundle_sha256`, and `client`. When provided, the same pure fingerprint module compares the immutable startup capture with every expected field and adds a `Runtime activation match` PASS/FAIL result. Missing capture, missing expected fields, or any mismatch fails explicitly and keeps the next PM loop blocked. When omitted, diagnostics remain a read-only 12-check health report; when provided and matched, they report 13/13. This extends the existing diagnostic surface rather than adding a tool or separate verifier subsystem.

### Activation protocol

After source build, cachebuster, validation, local reinstall, and source/cache hash parity:

1. Stop at a durable between-loop boundary; preserve current task and goal state.
2. Record current native task ID and intended cachebuster/hash evidence.
3. Fully quit and relaunch Codex.
4. Reopen the same Codex task.
5. Verify the native task ID is unchanged and the injected skill locator names the intended cachebuster.
6. Call `run_diagnostics` with the intended installed cache values in `expected_runtime` and require runtime version, root, manifest hash, bundle hash, client, and provider-native task identity to match.
7. Exercise one valid different-stage transition and require `changed:true`; preflight still prohibits a same-target probe.
8. Only if steps 4-7 fail, create a new task using the minimal native handoff and repeat fingerprint verification there.

Only then may validation say `runtime-active: verified` and the next roadmap PM loop begin.

## Documentation and Durable Evidence

Wave 1R corrects `docs/validation/2026-07-20-workflow-transition-idempotency.md`: it must explicitly state that the pre-restart Task 4 result was disproven, preserve successful source/test/install evidence, and record same-task post-restart proof separately.

The overall roadmap gains a named Wave 1R row and makes full Codex restart plus same-task verification explicit between any hot-deploy loop and its successor. `README.md` replaces its unconditional new-task requirement with same-task reopen as default and new-task recovery after verification failure; filesystem parity and `codex plugin list` remain insufficient without runtime fingerprint.

The executing-plans skill adds a narrow plugin-hot-deploy rule: if a plan installs or updates IronClaude itself, completion pauses across a full Codex restart, reopens the current task, and verifies `run_diagnostics.runtime` before continuing. This does not affect ordinary application deployments.

Wave 11 remains responsible for making every operator-approved requirements artifact, including the roadmap, an explicit blind-review input through validated plan schema and skill guidance. Wave 1R records the current contradiction as regression evidence for that later loop rather than adding another review subsystem now.

## Error Handling

- Missing or malformed runtime fingerprint: activation fails closed; do not start the next roadmap wave.
- Runtime version differs from source manifest: report both exact versions and stop.
- Runtime bundle hash differs from installed cache: report both exact paths/hashes and stop.
- Runtime plugin root points at a prior/deleted cache: report stale binding, fully restart Codex, and reopen the same task for another verification read.
- Same-task post-restart verification fails: stop; create a new task using the minimal continuation prompt with roadmap path and prior-task reference. Do not copy or mutate completed workflow rows.
- Different-stage transition lacks `changed: true`: runtime activation fails even if all hashes match.

No automatic retries, cache scanning, fallback to the newest version, silent acceptance of missing fields, or manual database mutation is allowed.

## Testing Strategy

### Unit and contract tests

- Pure fingerprint helper tests use temporary dual-manifest plugin layouts and assert exact provider-active paths, version, client, and SHA-256 values for both Codex and Claude.
- Startup-capture tests mutate manifest/bundle fixtures after initialization and prove diagnostics retain original startup hashes rather than reporting replacement disk bytes.
- Negative cases cover each provider's missing active manifest, malformed JSON, wrong plugin name, empty version, unsupported client, missing bundle, and unreadable inputs; the other provider's valid manifest must never rescue a failure.
- `run_diagnostics` tests require the new runtime check/object while preserving provider-native identity and byte-for-byte session/audit rollback.
- Pure verifier and dispatch tests pass missing/stale capture plus each mismatched expected field and require an explicit `Runtime activation match` failure; a complete match returns PASS without state mutation.
- Commander documentation/skill contracts require full-restart/same-task language after Codex install/update, retain explicit fresh-task recovery, and forbid certifying activation from `codex plugin list` or filesystem parity alone.

### Regression gates

- Focused fingerprint and diagnostic suites.
- Existing session-identity, dispatch, transition-idempotency, and diagnostic tests.
- Complete state-manager and Commander suites.
- Plugin validation before and after cachebusting.
- Exact source/cache parity for plugin-bundled fingerprint source/test, diagnostic source/test, generated bundle, Codex manifest, and executing skill.
- Repository evidence and contract tests for README, roadmap, and validation artifacts; these repository-only files are not falsely compared with installed plugin cache.

### Live activation gate

The final gate must execute after reinstall, full Codex restart, and reopening the same native task. It records:

- native task ID before and after restart, which must match;
- injected skill cachebuster;
- `run_diagnostics` exact pass count;
- runtime plugin version/root and both hashes;
- provider-native identity source;
- a valid different-stage transition returning `changed: true`;
- no commit and no push.

## YAGNI Boundary

Wave 1R adds no in-process reload command, cross-task plan migration, task cloning, process supervisor, cache discovery algorithm, compatibility-copy mechanism, new MCP tool, new database table, new workflow stage, provider router, or Commander feature. It extends one diagnostic surface, corrects operational guidance and evidence, and establishes the smallest verified full-restart/same-task activation boundary.
