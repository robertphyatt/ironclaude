# Review Receipt Rebase and Operator-Free Worktree Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.
> **Execution mode:** Sequential Terra subagents for Tasks 1–5. Main session owns workflow transitions, the single blind plan review, task reviews, Tasks 6–7 release orchestration, reinstall, restart, and runtime proof.

**Goal:** Reconcile cumulative reviewed trees across safe descendant HEAD movement, unwind failed review preparation without deadlock, and then complete the approved operator-free worktree lifecycle.

**Requirements:** docs/plans/2026-08-15-review-receipt-rebase-and-preparation-recovery-requirements.md

**Architecture:** The state manager three-way merges an active reviewed tree onto an authenticated descendant HEAD in a temporary index, then uses the existing pending-receipt promotion transaction. Authenticated failures before candidate creation reopen submitted tasks atomically without a semantic grade. Original worktree lifecycle Tasks 2–7 remain unchanged.

**Tech Stack:** TypeScript, better-sqlite3, Git plumbing, Vitest, Bash hooks, Python/pytest, esbuild, Codex and Claude plugin runtimes.

**Execution invariants:** Each shell step is independent. Use literal absolute paths and git -C /Users/roberthyatt/Code/ironclaude. Bash begins in commander/; quote globs; never suppress evidence errors; distinguish empty results from command failure. No direct database edit, receipt-parent rewrite, index reset, stash, commit, or push. Plan artifacts are immutable evidence, not task write scope. Recovery receives exactly one blind plan review; repair findings through one same-lineage advisor remediation, never a second blind review. Task 1 assigns one bootstrap cachebuster; Task 6 assigns the second and final cachebuster. Task 7 reinstall remains the final source/plugin/runtime mutation.

## Immutable plan evidence

mark_plan_ready and create_plan must seal and activate these exact registered paths without adding them to any task allowed_files:

- docs/plans/2026-08-15-review-receipt-rebase-and-preparation-recovery-design.md
- docs/plans/2026-08-15-review-receipt-rebase-and-preparation-recovery-requirements.md
- docs/plans/2026-08-15-review-receipt-rebase-and-preparation-recovery.md
- docs/plans/2026-08-15-review-receipt-rebase-and-preparation-recovery.plan.json
- docs/plans/2026-08-15-operator-free-managed-worktree-lifecycle-design.md
- docs/plans/2026-08-15-operator-free-managed-worktree-lifecycle-requirements.md
- docs/plans/2026-08-15-operator-free-managed-worktree-lifecycle.md
- docs/plans/2026-08-15-operator-free-managed-worktree-lifecycle.plan.json

## Task 1: Reconcile descendant receipts and unwind failed preparation

**Depends on:** none

**Files:**
- Modify or verify: `worker/mcp-servers/state-manager/src/plan-artifacts.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.test.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.mutation.test.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/db.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/types.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/state-machine.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/review-receipts.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/tools/write-tools.test.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`
- Modify or verify: `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- Modify or verify: `worker/mcp-servers/state-manager/dist/index.js`
- Modify or verify: `worker/.codex-plugin/plugin.json`
- Modify or verify: `worker/skills/brainstorming/SKILL.md`
- Modify or verify: `worker/skills/writing-plans/SKILL.md`
- Modify or verify: `worker/skills/executing-plans/SKILL.md`
- Modify or verify: `commander/tests/test_brainstorming_skill.py`
- Modify or verify: `commander/tests/test_writing_plans_skill.py`
- Modify or verify: `commander/tests/test_executing_plans_skill.py`
- Modify or verify: `commander/tests/test_review_receipt_skill.py`
- Modify or verify: `worker/hooks/test-guard-security.sh`

### Step 1: Reconfirm preserved live evidence

Read the trusted state database and Git objects without mutation. Require active receipt 57 for the provider-root session, parent 7ba7894e01089dfbbbc946b3ea93a3935e876e81, tree cb6e3d09ec9665793699b88f003afece78a6778b, receipt ref identity, live HEAD f07e3e7e75c7352f40686578daa4e75f3ccef698, and branch refs/heads/main. Compare parent-to-HEAD and parent-to-receipt path sets.

~~~
sqlite3 -readonly -header -column /Users/roberthyatt/.claude/ironclaude.db "SELECT id,status,parent_oid,tree_oid,receipt_ref,checkout_mode,workspace_guid FROM review_receipts WHERE terminal_session='019fc5e5-fc72-7493-b785-bee8cda62b1b' AND id=57;"
~~~

~~~
git -C /Users/roberthyatt/Code/ironclaude show -s --format='%H%n%P%n%T%n%D' HEAD && git -C /Users/roberthyatt/Code/ironclaude diff --name-only 7ba7894e01089dfbbbc946b3ea93a3935e876e81 HEAD
~~~

Expected: HEAD changes exactly commander/src/ironclaude/main.py and commander/tests/test_daemon.py; neither overlaps the 83 active-receipt paths; every active-receipt path remains staged; current Task 1 paths remain preserved. Any drift stops before implementation and reports exact OIDs and paths.

### Step 2: Write RED real-Git and state-transition tests

Extend worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts and worker/mcp-servers/state-manager/src/tools/write-tools.test.ts. Cover:

- clean descendant HEAD reconciliation with exact pending parent and composed tree
- identical overlaps
- content, delete/modify, executable-mode, symlink, and rename conflicts
- non-descendant, branch, repository, checkout, workspace, ref, and object drift
- authenticated no-candidate failure reopening submitted tasks
- exact flag clearing, one infrastructure audit, zero review grades
- exact index bytes, working bytes, receipt rows, refs, and objects preserved
- existing-pending, unauthenticated, cleanup-uncertain, and concurrent-index negative controls
- repeated unwind idempotency

Run:

~~~
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/plan-artifacts.test.ts src/__tests__/plan-artifacts.mutation.test.ts src/tools/write-tools.test.ts src/tools/write-tools.tier-up.test.ts
~~~

Expected: new descendant-reconciliation tests fail at Active review receipt parent does not match HEAD; new unwind tests fail because submitted state remains stranded. Existing cases stay green.

### Step 3: Implement isolated three-way receipt reconciliation

In worker/mcp-servers/state-manager/src/review-receipts.ts, replace the parent-equality rejection with a helper that:

1. fully verifies active receipt identity and Git object;
2. preserves equal-parent behavior;
3. requires merge-base --is-ancestor for a changed HEAD;
4. creates a separate temporary index;
5. runs a three-tree read with receipt parent as base, live HEAD as current side, and active tree as reviewed side;
6. inspects unmerged stages and throws a typed authenticated preparation error containing exact conflict paths and OIDs;
7. writes a reconciled baseline tree only when no unmerged entries exist.

Use that tree as candidate baseline and live HEAD as pending parent. Do not modify real index, working tree, active receipt row, or active ref during reconciliation. Leave unexpected-staged-path rejection, index locking, strict pending reuse, and compensation intact.

### Step 4: Implement atomic pre-candidate failure unwind

In worker/mcp-servers/state-manager/src/tools/write-tools.ts, distinguish authenticated safe-to-unwind preparation failures from unauthenticated or cleanup-uncertain failures. When no pending receipt exists and the same lineage/wave still has submitted tasks, use one transaction to:

- update those tasks to in_progress;
- set workflow_stage=executing;
- clear review_pending, review_block_count, active_skill, and testing_theatre_checked;
- insert one infrastructure-failure audit record with receipt and HEAD evidence.

Return an explicit response that preparation failed and tasks reopened. Record no A-F grade. Preserve an existing pending receipt and make repeated unwind a no-op.

### Step 5: Run GREEN and mutation-sensitive verification

Run:

~~~
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/plan-artifacts.test.ts src/__tests__/plan-artifacts.mutation.test.ts src/tools/write-tools.test.ts src/tools/write-tools.tier-up.test.ts
~~~

~~~
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
~~~

Temporarily weaken descendant ancestry and safe-unwind gating one at a time. The named tests must fail, and apply_patch must restore source immediately.

Expected: all selected tests and typecheck pass after restoration; each deliberate mutation produces a targeted failure.

### Step 6: Run affected and full regression suites once

Run:

~~~
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/test-guard-security.sh
~~~

~~~
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_brainstorming_skill.py tests/test_writing_plans_skill.py tests/test_executing_plans_skill.py tests/test_review_receipt_skill.py -q
~~~

~~~
make -C /Users/roberthyatt/Code/ironclaude test
~~~

Expected: every command exits zero. The full suite runs once unless a deterministic source repair justifies one bounded repeat.

### Step 7: Build, cachebust, validate, and record bootstrap runtime

Run:

~~~
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
~~~

~~~
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
~~~

~~~
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
~~~

~~~
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_version_consistency.py -q
~~~

Write /private/tmp/review-receipt-recovery-bootstrap-runtime.json from the measured source manifest version and SHA-256 hashes of the manifest, state-manager bundle, workspace-manager server bundle, CLI bundle, and hook-intent bundle. The expected Codex plugin root is the cache directory named by that measured version. Read the file back and compare every field to source bytes before installation.

~~~
/usr/bin/python3 -c 'import hashlib,json,pathlib; root=pathlib.Path("/Users/roberthyatt/Code/ironclaude/worker"); manifest=root/".codex-plugin/plugin.json"; version=json.loads(manifest.read_text())["version"]; cache=pathlib.Path("/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude")/version; sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); data={"client":"codex","plugin_root":str(cache),"plugin_version":version,"manifest_sha256":sha(manifest),"state_manager_bundle_sha256":sha(root/"mcp-servers/state-manager/dist/index.js"),"workspace_manager_bundle_sha256":sha(root/"mcp-servers/workspace-manager/dist/index.js"),"workspace_manager_cli_sha256":sha(root/"mcp-servers/workspace-manager/dist/cli.js"),"workspace_manager_hook_intent_sha256":sha(root/"mcp-servers/workspace-manager/dist/hook-intent.js")}; pathlib.Path("/private/tmp/review-receipt-recovery-bootstrap-runtime.json").write_text(json.dumps(data,sort_keys=True)+"\n")'
~~~

Read back and verify the exact oracle before installation:

~~~
/usr/bin/python3 -c 'import hashlib,json,pathlib; root=pathlib.Path("/Users/roberthyatt/Code/ironclaude/worker"); manifest=root/".codex-plugin/plugin.json"; version=json.loads(manifest.read_text())["version"]; cache=pathlib.Path("/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude")/version; sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); expected={"client":"codex","plugin_root":str(cache),"plugin_version":version,"manifest_sha256":sha(manifest),"state_manager_bundle_sha256":sha(root/"mcp-servers/state-manager/dist/index.js"),"workspace_manager_bundle_sha256":sha(root/"mcp-servers/workspace-manager/dist/index.js"),"workspace_manager_cli_sha256":sha(root/"mcp-servers/workspace-manager/dist/cli.js"),"workspace_manager_hook_intent_sha256":sha(root/"mcp-servers/workspace-manager/dist/hook-intent.js")}; p=pathlib.Path("/private/tmp/review-receipt-recovery-bootstrap-runtime.json"); raw=p.read_bytes(); assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), "runtime oracle must end with exactly one LF"; actual=json.loads(raw); assert actual == expected, f"runtime oracle drift: {actual}"'
~~~

Expected: build, validation, and version tests pass; base remains 1.1.6; exactly one new Task 1 Codex suffix replaces the old suffix; oracle parses as JSON, ends with exactly one LF, and equals every recomputed source field.

### Step 8: Stage only Task 1 implementation and validate

Run:

~~~
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/src/plan-artifacts.ts worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.test.ts worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.mutation.test.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/state-machine.ts worker/mcp-servers/state-manager/src/review-receipts.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.test.ts worker/mcp-servers/state-manager/src/tools/write-tools.tier-up.test.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/mcp-servers/state-manager/dist/index.js worker/.codex-plugin/plugin.json worker/skills/brainstorming/SKILL.md worker/skills/writing-plans/SKILL.md worker/skills/executing-plans/SKILL.md commander/tests/test_brainstorming_skill.py commander/tests/test_writing_plans_skill.py commander/tests/test_executing_plans_skill.py commander/tests/test_review_receipt_skill.py worker/hooks/test-guard-security.sh
~~~

~~~
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
~~~

Expected: exit zero. Immutable plan evidence is supplied by its server-held receipt and remains absent from Task 1 allowed_files. No unrelated working-tree path is modified by Task 1.

### Step 9: Install the bootstrap and review Task 1

Verify /private/tmp/restart_codex.py has SHA-256 d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f. Launch the verified delayed restart helper, then install the Codex bootstrap:

~~~
/usr/bin/python3 -c 'import hashlib,pathlib; p=pathlib.Path("/private/tmp/restart_codex.py"); expected="d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f"; actual=hashlib.sha256(p.read_bytes()).hexdigest(); assert actual == expected, f"restart helper hash mismatch: {actual}"'
~~~

~~~
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3","/private/tmp/restart_codex.py"],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)'
~~~

~~~
codex plugin add ironclaude@ironclaude --json
~~~

After restart, pass the exact object from /private/tmp/review-receipt-recovery-bootstrap-runtime.json to run_diagnostics. Verify the same provider-root session, exact installed root/version/hashes, current lineage, active Task 1, active receipt 57, and immutable plan-evidence receipt. Submit Task 1 and load ironclaude:code-review --task-boundary.

Expected: preparation reconciles receipt 57 onto descendant HEAD, the candidate contains active reviewed bytes plus plan evidence and Task 1, and Task 1 receives A/B. No source edit, rebuild, cachebuster, or reinstall occurs after submission.

---

## Task 2: Unify checkout authority and allocate PM-on worktrees automatically

**Depends on:** Task 1

**Files:**
- Create: `worker/mcp-servers/workspace-manager/src/effective-checkout.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/effective-checkout.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/git-authority.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/hooks/workspace-path-adapter.sh`
- Modify: `worker/hooks/state-activator.sh`
- Modify: `worker/hooks/session-init.sh`
- Modify: `worker/skills/activate-professional-mode/SKILL.md`
- Modify: `worker/skills/use-managed-worktree/SKILL.md`
- Modify: `commander/src/ironclaude/templates/worker_agents.md`
- Modify: `commander/src/ironclaude/templates/worker_claude_md.md`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git-authority.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/hooks/tests/test-managed-worktree-guard.sh`
- Modify: `worker/hooks/tests/test-git-authority-activation.sh`
- Modify: `commander/tests/test_workspace_activation_skill.py`
- Modify: `commander/tests/test_activation_client_parity.py`
- Modify: `commander/tests/test_worker_claude_md_template.py`

### Step 1: Write RED allocation and parity tests

Cover automatic PM-on direct/Commander allocation, exact repository/workspace/session ownership, monotonic generation, digest/expiry, stale or foreign evidence, transactional primary/managed switching, cross-component root parity, and exact PM-off bypass.

Expected: new cases fail because activation remains opt-in and consumers derive checkout authority independently.

### Step 2: Implement shared snapshot and automatic allocation

Add generation migration and `EffectiveCheckoutSnapshot`. Resolve exact repository/workspace/session ownership. PM-on activation/resume allocates or recovers a managed assignment before writes. PM-off returns before any assignment lookup or redirection.

### Step 3: Route consumers and verify switching

Route WorkspaceService, Git authority, status, CLI, hooks, and both Commander templates through the same snapshot contract. Build `dist/cli.js` before hook tests. Primary/managed transitions compare generation, update transactionally, and report success only after manager/guard/Git-authority parity.

### Step 4: Run GREEN and stage

Run the focused workspace, hook, activation, and template suites; run `npx tsc --noEmit`, `npm run bundle:cli`, and Bash syntax checks. Stage exact Task 2 paths and validate cached diff.

Expected: every command exits zero; weakening allocation, exact ownership, generation, PM-off bypass, or template parity fails a named test.

---

## Task 3: Reconcile stale and dirty assignments without operator mechanics

**Depends on:** Task 2

**Files:**
- Create: `worker/mcp-servers/workspace-manager/src/lifecycle-reconciler.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/lifecycle-reconciler.test.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/lifecycle-reconciler.mutation.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/integration.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/integration-core.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/integration-recovery.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`

### Step 1: Write RED reconciliation and mutation tests

Cover every freshness point, clean fast-forward, clean rebase, every dirty entry class, dirty-behind and dirty-local-commit preservation, recovery refs, ignored resources, conflict abort, lock/ref/generation races, crash replay, clean-checkout recreation, and metadata healing. A dedicated executable mutation suite changes dependency seams or temporary copies and restores automatically; it must reject weakened CAS, recovery-ref, Git-before-DB, and rollback behavior.

Expected: dirty branches do not preserve before every synchronization path, and dirty local commits still dead-end.

### Step 2: Implement inventory and private preservation

Build sorted non-ignored inventory; create an exact temporary-index recovery commit and generation-fenced ref; verify tree and reachability before checkout rebuild or synchronization.

### Step 3: Implement and wire reconciliation

Return structured current, fast-forwarded, rebased, preserved-and-synchronized, and conflict outcomes. Recreate a clean managed checkout after verified preservation. Use reconciler from activation/resume, public sync, return-to-managed, Commander CLI, and pre-finalization while preserving integration-lock, review, effect, target-CAS, and primary-overlap gates.

### Step 4: Run GREEN and stage

Build `dist/cli.js` before any CLI consumer runs. Run focused workspace and executable mutation suites plus `npx tsc --noEmit`. Stage exact Task 3 paths, including the generated CLI and mutation suite, and validate cached diff.

---

## Task 4: Add authorized discard and plan-scoped adoption

**Depends on:** Task 3

**Files:**
- Create: `worker/mcp-servers/workspace-manager/src/task-scope.ts`
- Create: `worker/mcp-servers/workspace-manager/src/managed-adoption.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/managed-adoption.test.ts`
- Create: `worker/skills/discard-managed-worktree/SKILL.md`
- Create: `worker/skills/discard-managed-worktree/test-scenarios/01-without-skill.md`
- Create: `worker/skills/discard-managed-worktree/test-scenarios/02-with-skill.md`
- Create: `worker/skills/discard-managed-worktree/test-scenarios/03-edge-cases.md`
- Create: `commander/tests/test_discard_managed_worktree_skill.py`
- Create: `commander/scripts/live_worktree_disposition_provider_response.py`
- Create: `commander/tests/test_live_worktree_disposition_provider_response.py`
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/workspace-service.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/lifecycle-reconciler.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/cli.ts`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/cli.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/hooks/state-activator.sh`
- Modify: `worker/hooks/tests/test-git-authority-activation.sh`
- Modify: `worker/hooks/tests/test-managed-worktree-guard.sh`
- Modify: `worker/skills/use-managed-worktree/SKILL.md`
- Modify: `worker/skills/executing-plans/SKILL.md`
- Modify: `commander/tests/test_use_managed_worktree_skill.py`
- Modify: `commander/tests/test_git_authority_skill_parity.py`

### Step 1: Write RED authority, adoption, and skill tests

Cover preview, issuance, exact bindings, replay/expiry/drift, ignored resources, authenticated task scope, malformed paths, primary-index preservation, temporary-index races, every Git entry type, overlap, and rendered provider parity. Apply `ironclaude:writing-skills` pressure scenarios without repository mutation.

Expected: discard intent, managed cleanup, and adoption do not exist; baseline pressure asks for operator mechanics.

### Step 2: Implement discard authority and managed cleanup

Preview returns exact inventory without mutation. Trusted human intent binds all destructive evidence. Reconcile consumes once, revalidates, cleans only bound non-ignored entries, and synchronizes. Skill never asks the operator to run Git or reconfirm supplied authority.

### Step 3: Implement authenticated adoption

Resolve active task scope from state-manager. Resolve planning paths only from the active exact repository/session/lineage registration; reject stale, foreign, replaced, or unregistered plan evidence. Compare primary, managed, and base entries. Build and atomically promote temporary managed index and working bytes. Preserve primary bytes/index. Reject widening, escapes, unrelated staging, and divergent overlap.

### Step 4: Run GREEN, provider behavior, and stage

Build `dist/cli.js` and `dist/hook-intent.js` before hook, Commander, or provider consumers run. Run focused workspace/hook/Commander suites, type and syntax checks, and fresh provider probe with Claude Sonnet timeout 540 seconds and Codex timeout 180 seconds. Both must choose exact IronClaude authority, request no operator shell command, and avoid redundant confirmation. Stage exact Task 4 paths, including both generated bundles, and validate cached diff.

---

## Task 5: Make Commander orchestrate the same lifecycle

**Depends on:** Task 4

**Files:**
- Modify: `commander/src/ironclaude/workspace_client.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/src/brain/system_prompt.md`
- Modify: `commander/src/ironclaude/templates/worker_agents.md`
- Modify: `commander/src/ironclaude/templates/worker_claude_md.md`
- Modify: `commander/scripts/managed_worktree_live_acceptance.py`
- Modify: `commander/tests/test_workspace_client.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`
- Modify: `commander/tests/test_daemon.py`
- Modify: `commander/tests/test_worker_finalize_release.py`
- Modify: `commander/tests/test_worktree_reaper.py`
- Modify: `commander/tests/test_worker_worktree_authority.py`
- Modify: `commander/tests/test_managed_worktree_parity.py`
- Modify: `commander/tests/test_managed_worktree_live_acceptance.py`
- Create: `commander/tests/test_worktree_lifecycle_mutations.py`
- Modify: `commander/tests/test_brain_integration_recovery_wizard.py`
- Modify: `commander/tests/test_worker_claude_md_template.py`

### Step 1: Write RED Commander tests

Cover automatic allocation, stale/dirty pre-dispatch, preservation, adoption, ownership disagreement, finalization recovery, reaper liveness, remote transport, both templates, and absence of manual worktree/index instructions. A dedicated executable mutation suite changes isolated dependency seams and restores automatically; it must reject no-push, reaper-protection, provider-handback, and finalization-order mutations.

Expected: Commander still surfaces manual intervention and provider templates diverge.

### Step 2: Orchestrate shared lifecycle

Extend no-push WorkspaceClient with resolver, reconciliation, disposition, and adoption routes. Reconcile/adopt before dispatch and active-state finalization. Preserve review gates, locks, probe-first recovery, retry caps, and deployed-runtime use. Replace manual-help messages with structured conflict/disposition state. Reaper protects all ambiguous or error states.

### Step 3: Extend real-Git live acceptance

Exercise automatic allocation, transitions, synchronization, preservation, adoption, discard, primary overlap, no remote movement, and both installed-root client modes. Version-only invocation is not behavioral acceptance.

### Step 4: Run GREEN and stage

Run exact focused Commander and executable mutation suites; prove no-push, liveness, template parity, and no-manual-instruction mutations fail. Stage exact Task 5 paths and validate cached diff.

---

## Task 6: Build and review the final release candidate

**Depends on:** Task 5

**Files:**
- Modify: `Makefile`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify generated: `worker/mcp-servers/state-manager/dist/index.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

### Step 1: Update documentation

Document automatic allocation, shared checkout authority, preservation, discard, adoption, Commander orchestration, plan-artifact receipts/index escrow, unchanged PM-off behavior, and unchanged push authority.

### Step 2: Build tracked bundles

Run state-manager and workspace-manager builds as separate commands. Verify bundle markers for plan-artifact receipts, candidate composition, checkout snapshots, reconciliation, discard, and adoption.

### Step 3: Run focused verification

Run exact focused state-manager, workspace-manager, hook, Commander, provider-native, version, and plugin-validation commands separately. Every command must expose its own exit status.

### Step 4: Run complete verification once

Add `test-state-manager` and include it in `test`, so this command runs the complete state-manager suite
as well as hooks, workspace-manager, and Commander.

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

Expected: exit zero. Fix deterministic defects in owning task scope; run at most one justified full repetition. No retreat or additional blind plan review.

### Step 5: Replace the final cachebuster and rebuild final bytes

Run the cachebuster exactly once in Task 6, replacing Task 1's bootstrap suffix with the second and final suffix. Rebuild state-manager and workspace-manager separately. Validate plugin and version consistency separately. Base remains `1.1.6`.

### Step 6: Record final runtime oracle and stage release candidate

Write `/private/tmp/operator-free-worktree-expected-runtime.json` from measured source manifest, deterministic installed plugin root, and all four bundle hashes for Claude and Codex. Compare source bytes with the complete oracle before uninstall. Stage Makefile, README, CHANGELOG, manifest, and four generated bundles. Run cached-diff validation. Call `record_task_evidence` with `repository_path=/Users/roberthyatt/Code/ironclaude`, `task_id=6`, `evidence_kind=release_candidate`, and `evidence_path=/private/tmp/operator-free-worktree-expected-runtime.json`. Submit Task 6 and complete task-boundary review before installation; its A/B receipt binds the reviewed tree, exact oracle digest, and release-candidate evidence.

Expected: A/B reviewed release candidate; no source mutation remains.

---

## Task 7: Reinstall last and prove provider-native runtime behavior

**Depends on:** Task 6

**Evidence only; no source write authority:**
- `evidence_only: true`
- Compatibility sentinel: `allowed_files: ["ironclaude://evidence-only"]`; Task 1 maps this reserved non-path token to zero write authority. Future evidence-only tasks use `allowed_files: []`.
- Exact `evidence_files`: Task 6 manifest, four bundles, and `/private/tmp/operator-free-worktree-expected-runtime.json`.

### Step 1: Verify immutable release evidence

Before installation, call `verify_evidence_receipt` with `repository_path=/Users/roberthyatt/Code/ironclaude`, `source_task_id=6`, `evidence_kind=release_candidate`, and `evidence_path=/private/tmp/operator-free-worktree-expected-runtime.json`. Require exact Task 6 A/B receipt, reviewed tree, and sealed oracle digest. Do not claim installed-runtime diagnostics before the final bytes are installed. Require `/private/tmp/restart_codex.py` SHA-256 to equal `d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f` before launch.

### Step 2: Reinstall as final mutation

Uninstall/install Claude user plugin, launch verified delayed restart helper, then run `codex plugin add ironclaude@ironclaude --json` as the final source/plugin/runtime mutation.

### Step 3: Run installed-runtime proof and isolated acceptance

After restart, pass each exact client object from the sealed oracle to `run_diagnostics`; verify installed roots, versions, manifests, all four bundle hashes, session identity, and Task 7 state. Run provider-native Claude Sonnet with timeout 540 seconds and Codex with timeout 180 seconds. Acceptance may mutate only isolated disposable repositories and databases whose canonical roots are proven not to overlap source, installed roots, live databases, assignments, or real refs. PM-on must allocate managed worktrees automatically; lifecycle operations must use installed routes; neither provider may request operator worktree/index commands or redundant confirmation.

### Step 4: Submit and review Task 7

Reverify the same Task 6 receipt and typed evidence, including install receipts, diagnostics, provider/model/timeouts, sandbox roots, behavior results, and protected-state non-mutation proof. Submit Task 7 and run task-boundary review. Infrastructure failure may retry proof. A source defect stops and reports a new repair boundary; no source edit, build, cachebuster, reinstall, commit, or push follows final installation.
