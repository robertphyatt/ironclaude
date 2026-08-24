# Wave Advancement and Reviewed Carry Recovery — Execution Plan

**Goal:** Repair reviewed-wave advancement, carry every preserved release artifact into reviewed scope, and finish the professional-mode-off release without losing evidence or creating another recovery lineage.

**Requirements:** `docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-requirements.md`

**Design:** `docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-design.md`

## Execution contract

This is the one operator-authorized recovery lineage. It receives exactly one mandatory blind plan review. If that review reports issues, record its sole verdict, use one non-blind advisor to repair this human plan and its machine plan coherently, and continue without another blind plan review. Do not retreat or create another lineage.

Execution is sequential. One Terra worker implements and verifies Task 1. The main session owns state transitions, both Codex restart boundaries, Task 2, task reviews, and the final Claude/Codex reinstall. No commit or push occurs.

Active receipt `54` is the cumulative reviewed base. Preserve all working-tree bytes, index entries, refs, review evidence, and prior test evidence. Task 1 earns A or B before the main session replaces the Codex cachebuster suffix once; preserve base `1.1.6` and reuse the resulting version through both installs. Do not reset, stash, discard, or reconstruct preserved bytes from `HEAD`.

After the sole blind review and any advisor remediation, but before `start_execution`, the main session records the final Task 2 carry bytes in a temporary oracle:

```bash
/usr/bin/python3 -c 'import hashlib,json,os,pathlib,stat; root=pathlib.Path("/Users/roberthyatt/Code/ironclaude"); paths=["README.md","CHANGELOG.md","docs/plans/2026-08-14-professional-mode-off-operator-authority.md","docs/plans/2026-08-14-professional-mode-off-operator-authority.plan.json","docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-design.md","docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-requirements.md"]; rows=[]; [(rows.append({"path":p,"sha256":hashlib.sha256((root/p).read_bytes()).hexdigest(),"mode":oct(stat.S_IMODE(os.lstat(root/p).st_mode))})) for p in paths]; target=pathlib.Path("/private/tmp/wave-advancement-reviewed-carry.json"); target.write_text(json.dumps({"paths":rows},sort_keys=True)+"\n"); print(target.read_text(),end="")'
```

**Expected:** The oracle contains exactly six immutable documentation paths with final post-review SHA-256 and mode values. Task 1 separately records the new manifest version after cachebusting. Both files are evidence, not repository artifacts.

## Task 1: Repair reviewed-wave advancement and bootstrap the repaired runtime

**Files:**

- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Add: `worker/mcp-servers/state-manager/src/__tests__/get-next-tasks-review-transition.test.ts`
- Modify generated: `worker/mcp-servers/state-manager/dist/index.js`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify: `docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-design.md`
- Modify: `docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-requirements.md`

### Step 1: Preserve boundaries and prove the defect RED

Record status, receipt `54`, current runtime identity, and the carried oracle. Add a real state-manager test that reproduces the observed sequence:

1. Wave 1 tasks are `review_passed`.
2. A task-boundary A or B exists for Wave 1.
3. The session remains `reviewing`.
4. `get_next_tasks` creates Wave 2.
5. The test expects `current_wave=2`, `workflow_stage="executing"`, `review_pending=0`, `review_block_count=0`, and reset testing-theatre state.

Also add RED cases for terminal completion, missing or informational grades, C/D/F, wrong-wave or malformed evidence, duplicate calls, the standard `mark_executing` ordering, and injected transaction failure.

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/get-next-tasks-review-transition.test.ts
```

**Expected:** Nonzero exit on the new advancement assertions while existing fixtures initialize successfully.

### Step 2: Implement one atomic transition

Update only `get_next_tasks` and narrow helpers needed by it:

- When called from `reviewing`, validate a task-boundary A or B for the completed current wave before computing a successor.
- In one SQLite transaction, create or reuse successor task rows, advance `current_wave`, enter `executing`, clear `review_pending` and `review_block_count`, reset review-scoped testing state, and write the audit record.
- When no successor remains, use the same reviewed-wave proof and transaction to enter `execution_complete`.
- Refuse missing, informational, C/D/F, malformed, or wrong-wave evidence before mutation.
- Preserve the existing `mark_executing` then `get_next_tasks` path.
- Make repeated calls return existing state without duplicate tasks, a second grade, or extra transition evidence.
- Do not modify receipts.

Do not add a schema migration, amendment API, generic transition framework, or unrelated state-manager refactor.

### Step 3: Run GREEN, rollback, and mutation-sensitive proof

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/get-next-tasks-review-transition.test.ts src/__tests__/mark-executing-review-gate.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/tools/write-tools.test.ts
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
```

**Expected:** Every test passes. The rollback test proves session, task, grade, receipt, and audit rows remain unchanged after an injected failure. Temporarily weaken the same-wave grade predicate and the atomic stage update one at a time; require the focused test to fail, then restore the production source with `apply_patch` and rerun GREEN.

### Step 4: Certify final source before runtime mutation

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

**Expected:** The complete repository suite exits zero once. Any source defect is repaired within Task 1's allowed source, test, or generated-bundle paths, then the affected focused test and this one justified full-suite repetition pass before Task 1 submission. The cachebuster remains `1.1.6+codex.20260815051924` throughout source repair and review.

### Step 5: Build, stage, submit, and review Task 1 before cachebusting

Build the final reviewed source and record its bundle hash before submission:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
shasum -a 256 /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager/dist/index.js > /private/tmp/wave-advancement-reviewed-bundle.sha256
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/__tests__/get-next-tasks-review-transition.test.ts docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-design.md docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-requirements.md worker/.codex-plugin/plugin.json
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/dist/index.js
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

**Expected:** Build and cached-diff checks pass. Task 1 candidate starts from receipt `54` and includes exactly its source, test, generated bundle, pre-cachebuster manifest, recovery design, and recovery requirements. Main submits Task 1 and loads `$ironclaude:code-review --task-boundary`. C, D, or F reopens exactly Task 1; repair, rebuild, resubmission, and task re-review all finish before cachebusting. A or B seals the candidate atop receipt `54`.

### Step 6: Cachebust once and bootstrap the reviewed runtime

After Task 1 earns A or B, main calls `mark_executing` with the existing runtime and defers `get_next_tasks`. It then replaces the Codex suffix exactly once, rebuilds unchanged reviewed source, and requires the rebuilt bundle hash to match `/private/tmp/wave-advancement-reviewed-bundle.sha256`.

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
shasum -a 256 -c /private/tmp/wave-advancement-reviewed-bundle.sha256
/usr/bin/python3 -c 'import hashlib,json,pathlib; root=pathlib.Path("/Users/roberthyatt/Code/ironclaude/worker"); version=json.loads((root/".codex-plugin/plugin.json").read_text())["version"]; digest=lambda p: hashlib.sha256((root/p).read_bytes()).hexdigest(); expected={"client":"codex","manifest_sha256":digest(".codex-plugin/plugin.json"),"plugin_root":f"/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/{version}","plugin_version":version,"state_manager_bundle_sha256":digest("mcp-servers/state-manager/dist/index.js"),"workspace_manager_bundle_sha256":digest("mcp-servers/workspace-manager/dist/index.js"),"workspace_manager_cli_sha256":digest("mcp-servers/workspace-manager/dist/cli.js"),"workspace_manager_hook_intent_sha256":digest("mcp-servers/workspace-manager/dist/hook-intent.js")}; target=pathlib.Path("/private/tmp/wave-advancement-bootstrap-runtime.json"); target.write_text(json.dumps(expected,sort_keys=True)+"\n"); print(target.read_text(),end="")'
shasum -a 256 /private/tmp/restart_codex.py
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3", "/private/tmp/restart_codex.py"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'
codex plugin add ironclaude@ironclaude --json
```

**Expected:** Base remains `1.1.6`; one fresh suffix replaces `20260815051924`; the rebuilt state-manager bundle is byte-identical to the reviewed bundle; helper hash is `d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f`; the exact fresh Codex version installs and the same native task resumes.

After restart, main passes the exact bootstrap object to `run_diagnostics`, requires activation match, the same provider-root session and plan, Task 1 `review_passed`, and `workflow_stage="executing"`, then calls repaired `get_next_tasks`. Task 2 must appear pending with `workflow_stage="executing"`. The direct `reviewing` to `get_next_tasks` route remains required test coverage; live recovery intentionally uses standard `mark_executing` ordering.

## Task 2: Carry exact artifacts, certify, and finish the release

**Depends on:** Task 1

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/plans/2026-08-14-professional-mode-off-operator-authority.md`
- Modify: `docs/plans/2026-08-14-professional-mode-off-operator-authority.plan.json`
- Modify: `docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-design.md`
- Modify: `docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-requirements.md`
- Modify: `worker/.codex-plugin/plugin.json`

### Step 1: Verify and stage the exact reviewed carry

Compare the six documentation paths with `/private/tmp/wave-advancement-reviewed-carry.json`. Require identical SHA-256 and mode values before staging. Confirm the manifest version equals `plugin_version` in `/private/tmp/wave-advancement-bootstrap-runtime.json`.

```bash
/usr/bin/python3 -c 'import hashlib,json,os,pathlib,stat; root=pathlib.Path("/Users/roberthyatt/Code/ironclaude"); proof=json.loads(pathlib.Path("/private/tmp/wave-advancement-reviewed-carry.json").read_text())["paths"]; bad=[r for r in proof if hashlib.sha256((root/r["path"]).read_bytes()).hexdigest()!=r["sha256"] or oct(stat.S_IMODE(os.lstat(root/r["path"]).st_mode))!=r["mode"]]; not bad or (_ for _ in ()).throw(SystemExit(json.dumps(bad,sort_keys=True))); print("CARRY_WORKTREE_MATCH")'
/usr/bin/python3 -c 'import json,pathlib; p=pathlib.Path("/Users/roberthyatt/Code/ironclaude/worker/.codex-plugin/plugin.json"); expected=json.loads(pathlib.Path("/private/tmp/wave-advancement-bootstrap-runtime.json").read_text())["plugin_version"]; actual=json.loads(p.read_text())["version"]; actual==expected or (_ for _ in ()).throw(SystemExit(f"{actual} != {expected}")); print(actual)'
git -C /Users/roberthyatt/Code/ironclaude add -f -- README.md CHANGELOG.md docs/plans/2026-08-14-professional-mode-off-operator-authority.md docs/plans/2026-08-14-professional-mode-off-operator-authority.plan.json docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-design.md docs/plans/2026-08-15-wave-advancement-and-reviewed-carry-recovery-requirements.md worker/.codex-plugin/plugin.json
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

**Expected:** The six documentation paths match the post-review oracle byte-for-byte and mode-for-mode; the manifest matches Task 1's recorded bootstrap version. The candidate contains Task 1's reviewed tree plus the seven declared Task 2 paths; no unrelated entry is added.

### Step 2: Run release certification without source mutation

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-off-authority.sh
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /Users/roberthyatt/Code/ironclaude/commander/scripts/live_pm_off_provider_response.py --client all --source-root /Users/roberthyatt/Code/ironclaude
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_professional_mode_off_contract.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_live_pm_off_provider_response.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_git_authority_skill_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_activation_client_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_deactivation_client_parity.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_worker_claude_md_template.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_brain_client.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_codex_brain_client.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_human_authority_refusal.py /Users/roberthyatt/Code/ironclaude/commander/tests/test_push.py -q
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && shasum -a 256 -c /private/tmp/wave-advancement-reviewed-bundle.sha256
```

**Expected:** Every command exits zero. Task 1's full repository suite and reviewed bundle hash remain the source certification. If any command now exposes a defect requiring a path outside Task 2's seven allowed files, stop before installation and report the exact blocker; do not edit undeclared source, retreat, create another lineage, or run another blind plan review. Task 2 may repair only its declared documentation or manifest paths after C, D, or F.

### Step 3: Validate fixed versions and regenerate final runtime evidence

Do not run the cachebuster helper again. Validate Task 1's manifest and final built bundles, then overwrite the pre-install oracle with current final bytes. Record cache installation roots separately from active runtime roots. For Claude, derive the active root from the canonical local-path marketplace's `worker/` directory when present; otherwise use the user-cache root. Codex always uses its cache root.

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python -m pytest /Users/roberthyatt/Code/ironclaude/commander/tests/test_version_consistency.py -q
/usr/bin/python3 -c 'import hashlib,json,pathlib; source=pathlib.Path("/Users/roberthyatt/Code/ironclaude/worker"); shared=["mcp-servers/state-manager/dist/index.js","mcp-servers/workspace-manager/dist/index.js","mcp-servers/workspace-manager/dist/cli.js","mcp-servers/workspace-manager/dist/hook-intent.js"]; manifests={"claude":".claude-plugin/plugin.json","codex":".codex-plugin/plugin.json"}; homes={"claude":".claude","codex":".codex"}; versions={c:json.loads((source/m).read_text())["version"] for c,m in manifests.items()}; installed={c:pathlib.Path("/Users/roberthyatt")/homes[c]/"plugins/cache/ironclaude/ironclaude"/versions[c] for c in manifests}; markets=json.loads(pathlib.Path("/Users/roberthyatt/.claude/plugins/known_marketplaces.json").read_text()); local=pathlib.Path(markets.get("ironclaude",{}).get("installLocation",markets.get("ironclaude",{}).get("source",{}).get("path","")))/"worker"; active={"claude":local.resolve() if local.is_dir() else installed["claude"],"codex":installed["codex"]}; digest=lambda relative: hashlib.sha256((source/relative).read_bytes()).hexdigest(); runtimes={c:{"client":c,"plugin_root":str(active[c]),"plugin_version":versions[c],"manifest_sha256":digest(manifests[c]),"state_manager_bundle_sha256":digest(shared[0]),"workspace_manager_bundle_sha256":digest(shared[1]),"workspace_manager_cli_sha256":digest(shared[2]),"workspace_manager_hook_intent_sha256":digest(shared[3])} for c in manifests}; inventory=[{"client":c,"root":str(installed[c]),"relative_path":r,"sha256":digest(r)} for c,m in manifests.items() for r in [m,*shared]]; target=pathlib.Path("/private/tmp/pm-off-operator-authority-expected-runtime.json"); target.write_text(json.dumps({"expected_runtime":runtimes,"installed_roots":{c:str(p) for c,p in installed.items()},"inventory":inventory},sort_keys=True)+"\n"); print(target.read_text(),end="")'
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

**Expected:** Plugin validation and version tests pass. Claude remains base `1.1.6`; Codex remains Task 1's exact fresh cachebuster. The printed oracle contains eight diagnostic fields per active runtime, separate installed roots, and ten exact installed-file inventory entries. Claude's active root is either its cache or the canonical local-marketplace `worker/` root.

### Step 4: Reinstall as the final source, plugin, and runtime mutation

Main performs this step. No subagent may reinstall or restart.

```bash
shasum -a 256 /private/tmp/restart_codex.py
claude plugin uninstall ironclaude@ironclaude --scope user --keep-data --yes
claude plugin install ironclaude@ironclaude --scope user
claude plugin details ironclaude@ironclaude
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3", "/private/tmp/restart_codex.py"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'
codex plugin add ironclaude@ironclaude --json
```

**Expected:** Helper hash matches the approved value. Claude installs base `1.1.6`; Codex installs the unchanged cachebuster. `codex plugin add` is the final source, build, plugin-installation, and runtime mutation.

### Step 5: Prove installed identity, submit, and review Task 2

After the same native Codex task resumes:

1. Read `/private/tmp/pm-off-operator-authority-expected-runtime.json`.
2. Resolve Claude's user-scope `installPath` from `~/.claude/plugins/installed_plugins.json`; require one exact installed root and version.
3. Compare every installed manifest and bundle with all ten inventory records. If Claude's active root differs because of a local-path marketplace, compare that active manifest and all four bundles with the same oracle too.
4. Run Claude `run_diagnostics` with the exact `expected_runtime.claude` object.
5. Run Codex `run_diagnostics` with the exact `expected_runtime.codex` object and require the same provider-root session and active Task 2.
6. Submit Task 2 and load `$ironclaude:code-review --task-boundary`.

```bash
/usr/bin/python3 -c 'import hashlib,json,pathlib; proof=json.loads(pathlib.Path("/private/tmp/pm-off-operator-authority-expected-runtime.json").read_text()); registry=json.loads(pathlib.Path("/Users/roberthyatt/.claude/plugins/installed_plugins.json").read_text()); wanted=proof["expected_runtime"]["claude"]; installed=proof["installed_roots"]["claude"]; matches=[row for row in registry["plugins"]["ironclaude@ironclaude"] if row.get("scope")=="user" and row.get("version")==wanted["plugin_version"] and row.get("installPath")==installed]; len(matches)==1 or (_ for _ in ()).throw(SystemExit(f"Claude registry match count: {len(matches)}")); missing=[item for item in proof["inventory"] if not (pathlib.Path(item["root"])/item["relative_path"]).is_file()]; not missing or (_ for _ in ()).throw(SystemExit(json.dumps(missing,sort_keys=True))); mismatches=[item for item in proof["inventory"] if hashlib.sha256((pathlib.Path(item["root"])/item["relative_path"]).read_bytes()).hexdigest()!=item["sha256"]]; active=pathlib.Path(wanted["plugin_root"]); active_items=[{"relative_path":r,"sha256":s} for r,s in [(".claude-plugin/plugin.json",wanted["manifest_sha256"]),("mcp-servers/state-manager/dist/index.js",wanted["state_manager_bundle_sha256"]),("mcp-servers/workspace-manager/dist/index.js",wanted["workspace_manager_bundle_sha256"]),("mcp-servers/workspace-manager/dist/cli.js",wanted["workspace_manager_cli_sha256"]),("mcp-servers/workspace-manager/dist/hook-intent.js",wanted["workspace_manager_hook_intent_sha256"])]]; active_bad=[item for item in active_items if not (active/item["relative_path"]).is_file() or hashlib.sha256((active/item["relative_path"]).read_bytes()).hexdigest()!=item["sha256"]]; not mismatches and not active_bad or (_ for _ in ()).throw(SystemExit(json.dumps({"installed":mismatches,"active":active_bad},sort_keys=True))); print("ALL_INSTALLED_AND_ACTIVE_BYTES_MATCH")'
claude -p 'Read /private/tmp/pm-off-operator-authority-expected-runtime.json. Call exactly state-manager run_diagnostics with expected_runtime equal to expected_runtime.claude. Return only that tool result. Do not call write or workflow-transition tools.' --model sonnet --output-format json
```

**Expected:** Both clients' active roots, installed roots, versions, diagnostics, and ten installed hashes match the pre-install oracle. A Claude local-marketplace source root is accepted only with separate cache and active-root byte equality. Task 2 review Grade A or B seals all seven carried paths atop Task 1's receipt and completes the plan.

Post-install activity is limited to read-only proof, diagnostics, and mandatory workflow evidence. C, D, or F must reopen exactly Task 2 before any repair attempt. A bounded repair may change only Task 2's seven declared paths, reruns affected verification, regenerates evidence, and repeats the final install; a finding requiring any other path is a blocking failure. No new lineage, blind plan review, commit, or push occurs.
