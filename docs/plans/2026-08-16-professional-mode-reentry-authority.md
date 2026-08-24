# Professional-Mode Re-entry Authority Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Make professional-mode re-entry supersede stale authority safely and permit only exact, operator-approved recovery Git sequences.

**Requirements:** `docs/plans/2026-08-16-professional-mode-reentry-authority-requirements.md`

**Architecture:** Workspace-manager owns read-only recovery inspection, trusted operator guidance, MCP elicitation, single-use command capabilities, and argv-only Git execution. State-manager owns authority epochs and independently finalizes a new PM-on baseline from durable workspace recovery evidence. Hooks capture direct-human events; skills orchestrate both servers without exposing generic Git authority.

**Tech stack:** TypeScript, better-sqlite3, MCP SDK 1.22 elicitation, Bash hooks, Git plumbing, Vitest, pytest, esbuild.

## Execution invariants

- Every command is independent. Use literal absolute paths; shell state never carries between steps.
- Bash may start in `commander/`; every Git command uses `git -C /Users/roberthyatt/Code/ironclaude`.
- Quote globs under zsh. Do not suppress evidence-command stderr or truncate absence/completeness checks.
- Planning artifacts are immutable review evidence, not task write scope. Stage ignored `docs/plans` artifacts only with exact `git add -f --` paths.
- PM-on retains two disjoint mutation lanes: existing task-scoped `git add` for review-candidate staging, and the trusted recovery executor consuming one exact capability. Staging grants no recovery, ref, worktree, commit, or push authority; recovery is the only new general Git-mutation authority.
- No task commits or pushes. No operator is asked to run Git or worktree commands.
- The PM-off bootstrap replaces the Codex cachebuster once to deploy Task 5 into this exact session. Task 4 replaces it once more after all product source is final. Its reinstall is the final source/plugin/runtime mutation; only read-only runtime proof and workflow evidence follow.

## Execution bootstrap prerequisite

Under exact PM off, implement and verify Task 5's two lifecycle repairs, append the
failure path to the roadmap, rebuild state-manager, replace the Codex cachebuster
once, validate the plugin, and reinstall into this exact provider-root session.
No session replacement, blind review, database edit, reset, clean, stash, commit,
or push is permitted.

After restart, prove the installed root, version, state-manager bundle hash, and
terminal session. Stage only the four immutable plan artifacts, invoke the
post-advisor reseal once, reload this corrected plan in the same lineage, record
the already-completed non-blind advisor remediation for the corrected hash, and
reactivate professional mode. Task 5 is the first execution task and reviews the
bootstrap source, generated bundle, manifest, tests, and roadmap note. Any proof
mismatch stops before reseal or activation; it never produces Git handback.

---

## Task 5: Repair post-advisor reseal and stale candidate authority

**Runs first. Depends on:** none

**Files:**
- Modify: `worker/mcp-servers/state-manager/src/plan-artifacts.ts`
- Modify: `worker/mcp-servers/state-manager/src/review-receipts.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/mode-transition-hook.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/reentry-recovery.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/reentry-recovery.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/hooks/state-activator.sh`
- Modify: `worker/hooks/tests/test-git-authority-activation.sh`
- Modify: `worker/hooks/tests/test-professional-mode-deactivation.sh`
- Modify generated: `worker/mcp-servers/state-manager/dist/index.js`
- Modify: `worker/.codex-plugin/plugin.json`
- Add: `docs/plans/2026-07-20-v1-1-overall-roadmap.md`
- Modify evidence: `docs/plans/2026-08-16-professional-mode-reentry-authority-design.md`
- Modify evidence: `docs/plans/2026-08-16-professional-mode-reentry-authority-requirements.md`
- Modify evidence: `docs/plans/2026-08-16-professional-mode-reentry-authority.md`
- Modify evidence: `docs/plans/2026-08-16-professional-mode-reentry-authority.plan.json`

### Step 1: Prove RED lifecycle failures

Add real-Git/database tests for one post-advisor reseal, exact replay, stale
prior-lineage receipt supersession, missing advisor/artifact/task bindings,
injected audit rollback, index/ref preservation, and ordinary same-lineage unwind.
Add post-F reseal success/replay/refusal coverage and same-`HEAD` branch-switch
refusal for no-candidate unwind.
Run the new cases against the old implementation and require failure.

### Step 2: Implement the bounded lifecycle repair

Permit one server-authenticated post-advisor reseal under the R0 predicates.
Permit one additional same-lineage reseal only after an exact task-boundary F
reopens the whole current wave; transition to `final_plan_prep` for corrected
artifact reload and reuse it idempotently. Bind authenticated failure unwind to
the exact branch. Treat authenticated historical receipt paths as cumulative
reviewed authority while current task scope gates only new or changed paths.
Before candidate creation, validate one stale prior-lineage receipt and defer its
retirement until the exact pending candidate and locked index are ready. Retire
authority and promote the candidate in one transaction; retain the stale ref and
all historical evidence. Expose only the superseded receipt ID in the result.

### Step 3: Record the failure path

Append a corrective roadmap wave describing the exact sequence that forced PM
deactivation and the permanent acceptance gate proving future recovery stays
inside PM-on surfaces.

### Step 4: Verify and stage Task 5

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/plan-artifacts.test.ts src/__tests__/review-receipts.test.ts src/tools/write-tools.test.ts src/tools/write-tools.tier-up.test.ts src/__tests__/workflow-transition-idempotency.test.ts
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/plan-artifacts.ts worker/mcp-servers/state-manager/src/review-receipts.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.test.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/dist/index.js worker/.codex-plugin/plugin.json docs/plans/2026-07-20-v1-1-overall-roadmap.md docs/plans/2026-08-16-professional-mode-reentry-authority-design.md docs/plans/2026-08-16-professional-mode-reentry-authority-requirements.md docs/plans/2026-08-16-professional-mode-reentry-authority.md docs/plans/2026-08-16-professional-mode-reentry-authority.plan.json
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: 132 or more focused tests pass, typecheck/build exit zero, and only
Task 5 implementation paths plus receipt-owned planning evidence are staged.

---

## Task 1: Build server-held recovery guidance and Git capabilities

**Depends on:** Task 5

**Files:**
- Create: `worker/mcp-servers/workspace-manager/src/reentry-recovery.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/reentry-recovery.test.ts`
- Create: `worker/mcp-servers/workspace-manager/src/__tests__/reentry-recovery.mutation.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/types.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/db.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/git.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/hook-intent.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/index.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts`
- Modify: `worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts`

### Step 1: Add RED real-Git and MCP elicitation tests

Add isolated database and temporary-repository tests for:

- complete dirty inventory: staged, unstaged, untracked, deleted, renamed, executable, symlink, conflict, and ignored entries;
- server-generated options containing ordered Git argv, effects, path/ref scope, reversibility, recommendation, and rationale;
- durable disclosure records whose timestamp/order precedes any authorizing event;
- rejection of generic approval, command-bearing approval predating disclosure, stale disclosure ID, revised argv, mismatched disposition, and changed observed state;
- acceptance of exact post-disclosure direct-human selection captured only from `UserPromptSubmit` and bound to provider root, client, repository, checkout, epoch, disclosure ID, and observed Git state;
- ambiguous guidance invoking `server.server.elicitInput(...)` only when client capabilities include `elicitation`;
- unsupported elicitation returning a typed no-mutation infrastructure blocker;
- one-shot capability expiry, replay, state drift, argument reordering, path/ref widening, client/session/repository/checkout substitution, unlisted commit/push, and unlisted fallback rejection;
- argv-only execution, effect verification, approved compensation, partial failure, and idempotent execution receipt replay.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/reentry-recovery.test.ts src/__tests__/reentry-recovery.mutation.test.ts src/__tests__/db.test.ts src/__tests__/git.test.ts src/__tests__/tool-dispatch.test.ts
```

Expected: non-zero after RED tests are added; named cases expose absent recovery tables, elicitation route, capability checks, and trusted executor.

### Step 2: Implement recovery persistence and inspection

Add durable rows for option disclosures, operator guidance, recovery capabilities, command/effect bindings, and execution receipts. Implement canonical repository/checkout discovery, branch/HEAD/index/ref snapshots, exact entry inventory, assignment-generation binding, and typed classifications. Public callers may supply only repository, epoch/re-entry identifier, and a closed server-issued disclosure/disposition identifier; they may not supply argv, paths, refs, effects, nonce, or expiry.

### Step 3: Implement trusted guidance selection and execution

Generate and persist every mutating argv vector, effect, and recommendation before authority can be minted. Extend the trusted hook helper to accept only a post-disclosure direct-human selection that exactly matches the server-held disclosure ID and observed state. For all other state, use MCP SDK elicitation after checking `server.getClientCapabilities()?.elicitation`; bind accepted content directly to the same server-held option. Revalidate all bindings before mutation, execute with `spawnSync('git', ['-C', checkout, ...argv])` and no shell, verify each effect, and persist a one-shot execution receipt. Fail closed when elicitation is unsupported, declined, cancelled, malformed, stale, or mismatched.

### Step 4: Run GREEN and mutation tests

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/reentry-recovery.test.ts src/__tests__/reentry-recovery.mutation.test.ts src/__tests__/db.test.ts src/__tests__/git.test.ts src/__tests__/tool-dispatch.test.ts
```

Expected: selected tests pass; executable mutations weakening binding equality, command order, one-shot consumption, or effect verification produce targeted failures before restoration.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npx tsc --noEmit
```

Expected: exit zero.

### Step 5: Stage exact Task 1 paths

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/workspace-manager/src/reentry-recovery.ts worker/mcp-servers/workspace-manager/src/__tests__/reentry-recovery.test.ts worker/mcp-servers/workspace-manager/src/__tests__/reentry-recovery.mutation.test.ts worker/mcp-servers/workspace-manager/src/types.ts worker/mcp-servers/workspace-manager/src/db.ts worker/mcp-servers/workspace-manager/src/git.ts worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/__tests__/db.test.ts worker/mcp-servers/workspace-manager/src/__tests__/git.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts
```

Expected: exit zero.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero.

---

## Task 2: Add authority epochs and transactional PM re-entry

**Files:**
- Create: `worker/mcp-servers/state-manager/src/professional-mode-epochs.ts`
- Create: `worker/mcp-servers/state-manager/src/mode-transition-hook.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.test.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.mutation.test.ts`
- Create generated: `worker/mcp-servers/state-manager/dist/mode-transition-hook.js`
- Modify: `worker/mcp-servers/state-manager/package.json`
- Modify: `worker/mcp-servers/state-manager/src/types.ts`
- Modify: `worker/mcp-servers/state-manager/src/db.ts`
- Modify: `worker/mcp-servers/state-manager/src/state-machine.ts`
- Modify: `worker/mcp-servers/state-manager/src/review-receipts.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/read-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tool-dispatch.ts`
- Modify: `worker/mcp-servers/state-manager/src/index.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/pm-deactivation-audit.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`

### Step 1: Add RED epoch and finalization tests

Use isolated state/workspace databases and real repositories to cover:

- `on -> off` always succeeds while recording complete or explicitly uncertain epoch-close evidence;
- exact-on is no-op and never alters active workflow authority;
- `off -> on` cannot use the ordinary mode setter and remains off during inspection/guidance/execution;
- clean unchanged and clean off-period commit movement;
- current incident: operator-authorized PM-off commit overlaps dormant active receipt without receipt-tree merge or retrospective grade;
- same-session bootstrap: Task 5 preserves review history and stale receipt ref while preparing and reviewing its exact replacement-lineage candidate successfully;
- exact workspace execution-receipt authentication from the configured workspace database;
- atomic stale-pending retirement, dormant-active supersession, plan interruption/history preservation, review-flag cleanup, new baseline, and mode-on transition;
- transaction rollback, no fabricated grade, preserved refs/history/index/worktree, and idempotent finalization.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/professional-mode-epochs.test.ts src/__tests__/professional-mode-epochs.mutation.test.ts src/__tests__/pm-deactivation-audit.test.ts src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/tool-dispatch.test.ts src/tools/write-tools.test.ts
```

Expected: non-zero after RED tests are added; named cases expose missing epoch storage, off-to-on gate, workspace receipt authentication, and atomic supersession.

### Step 2: Implement epoch ledger and hook helper

Add authority-epoch schema and typed repository observations. `mode-transition-hook.ts` must accept only a trusted UserPromptSubmit payload, capture all readable evidence, record explicit uncertainty for failures, and set mode off without retiring receipts or mutating Git. Add a dedicated esbuild script for `dist/mode-transition-hook.js`.

### Step 3: Implement read-only inspection and state finalization

Add read/write tools for re-entry inspection and finalization. Inspection is mutation-free. Finalization opens the workspace database read-only, authenticates the exact execution/no-mutation receipt, independently re-observes repository state, and performs one state transaction. Preserve receipt refs and historical rows; mark old authority superseded/retired for enforcement, never delete it. Ordinary `set_professional_mode(on)` must return a typed re-entry-required result when current mode is off.

### Step 4: Run GREEN, mutation, and type checks

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/professional-mode-epochs.test.ts src/__tests__/professional-mode-epochs.mutation.test.ts src/__tests__/pm-deactivation-audit.test.ts src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/tool-dispatch.test.ts src/tools/write-tools.test.ts
```

Expected: selected tests pass; mutations weakening epoch, workspace-receipt, or transaction checks fail before restoration.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx tsc --noEmit
```

Expected: exit zero.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run bundle:mode-transition-hook
```

Expected: exit zero and `dist/mode-transition-hook.js` is rebuilt from current source.

### Step 5: Stage exact Task 2 paths

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/src/professional-mode-epochs.ts worker/mcp-servers/state-manager/src/mode-transition-hook.ts worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.test.ts worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.mutation.test.ts worker/mcp-servers/state-manager/dist/mode-transition-hook.js worker/mcp-servers/state-manager/package.json worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/state-machine.ts worker/mcp-servers/state-manager/src/review-receipts.ts worker/mcp-servers/state-manager/src/tools/read-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tool-dispatch.ts worker/mcp-servers/state-manager/src/index.ts worker/mcp-servers/state-manager/src/__tests__/pm-deactivation-audit.test.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/state-manager/src/tools/write-tools.test.ts
```

Expected: exit zero.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero.

---

## Task 3: Wire trusted hooks, activation behavior, and client parity

**Files:**
- Create: `commander/scripts/live_pm_reentry_provider_response.py`
- Create: `commander/tests/test_live_pm_reentry_provider_response.py`
- Create: `commander/tests/test_professional_mode_reentry_skill.py`
- Modify: `worker/hooks/state-activator.sh`
- Modify: `worker/hooks/session-init.sh`
- Modify: `worker/hooks/professional-mode-guard.sh`
- Modify: `worker/hooks/tests/test-professional-mode-deactivation.sh`
- Modify: `worker/hooks/tests/test-professional-mode-off-authority.sh`
- Modify: `worker/hooks/tests/test-bash-readonly-guard.sh`
- Modify: `worker/hooks/tests/test-git-authority-activation.sh`
- Modify: `worker/skills/activate-professional-mode/SKILL.md`
- Modify: `worker/skills/deactivate-professional-mode/SKILL.md`
- Modify: `commander/tests/test_activation_client_parity.py`
- Modify: `commander/tests/test_deactivation_client_parity.py`

### Step 1: Add RED hook, skill, and provider-harness tests

Cover exact Claude slash and Codex dollar/Markdown invocation forms. Assert:

- deactivation calls the trusted mode-transition helper and remains available on missing/unreadable repository evidence;
- activation from off follows inspect -> workspace resolve/elicit/execute -> state finalize and reports success only after mode becomes on;
- exact post-disclosure operator selection does not reconfirm;
- generic, pre-disclosure, stale, or mismatched guidance invokes elicitation and cannot mint authority;
- ambiguous state renders a structured dialogue with exact commands, effects, recommendation, and rationale;
- decline/cancel/unsupported client leaves mode off and preserves all state;
- no output asks the operator to run Git;
- existing task-scoped `git add` staging remains admitted but cannot mint or consume a recovery capability;
- PM-on Bash/raw Git mutation remains blocked, including argv equivalent to an approved sequence;
- commit, push, ref movement, reset, clean, stash, and worktree mutation remain blocked outside the trusted executor;
- exact-off raw Git contract remains unchanged.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```

Expected: non-zero after RED assertions are added; trusted helper and epoch assertions are absent.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_professional_mode_reentry_skill.py tests/test_activation_client_parity.py tests/test_deactivation_client_parity.py tests/test_live_pm_reentry_provider_response.py -q
```

Expected: non-zero after RED tests are added; activation skill and provider harness lack the re-entry protocol.

### Step 2: Wire hook and schema parity

Replace deactivation's direct SQL mode flip with the trusted state hook helper. Record only post-disclosure activation selections through workspace-manager's trusted hook helper without issuing broad Git intent. Add epoch/recovery tables to session-init bootstrap schema. Keep exact-off early bypass and existing task-scoped staging lane intact; do not add a generic guard-level Git command allowlist.

### Step 3: Update activation and deactivation skills

Activation from `off` must run the provider-native re-entry protocol and remain off until finalization. It may pass only a typed disposition selected from server-generated options; it never supplies argv/paths/refs. If server requests elicitation, the MCP client owns the operator dialogue. On unsupported elicitation, report the exact infrastructure blocker. Deactivation reports epoch closure/uncertainty without weakening PM-off authority.

### Step 4: Implement deterministic provider-native acceptance harness

The harness creates disposable Git repositories and isolated DBs, then runs fresh Claude Sonnet and Codex sessions. Use one fixture per session, Claude timeout 540 seconds, Codex timeout 180 seconds, no silent retries. Verify explicit-guidance and ambiguous-dialogue fixtures, exact listed commands/effects, no redundant confirmation, no operator shell handback, and final mode/state evidence.

### Step 5: Run GREEN parity suites

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```

Expected: exit zero.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-off-authority.sh
```

Expected: exit zero.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-bash-readonly-guard.sh
```

Expected: exit zero.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh
```

Expected: exit zero.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_professional_mode_reentry_skill.py tests/test_activation_client_parity.py tests/test_deactivation_client_parity.py tests/test_live_pm_reentry_provider_response.py -q
```

Expected: selected tests pass.

Run:

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh
```

Expected: exit zero.

### Step 6: Stage exact Task 3 paths

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/hooks/state-activator.sh worker/hooks/session-init.sh worker/hooks/professional-mode-guard.sh worker/hooks/tests/test-professional-mode-deactivation.sh worker/hooks/tests/test-professional-mode-off-authority.sh worker/hooks/tests/test-bash-readonly-guard.sh worker/hooks/tests/test-git-authority-activation.sh worker/skills/activate-professional-mode/SKILL.md worker/skills/deactivate-professional-mode/SKILL.md commander/scripts/live_pm_reentry_provider_response.py commander/tests/test_live_pm_reentry_provider_response.py commander/tests/test_professional_mode_reentry_skill.py commander/tests/test_activation_client_parity.py commander/tests/test_deactivation_client_parity.py
```

Expected: exit zero.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero.

---

## Task 4: Document, build, verify, cachebust, and release

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `Makefile`
- Modify: `commander/scripts/live_pm_reentry_provider_response.py`
- Modify: `commander/tests/test_live_pm_reentry_provider_response.py`
- Modify: `docs/plans/2026-07-20-v1-1-overall-roadmap.md`
- Modify: `worker/mcp-servers/state-manager/src/plan-artifacts.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/professional-mode-epochs.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/read-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/tool-dispatch.ts`
- Modify: `worker/mcp-servers/state-manager/src/index.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts`
- Modify: `docs/plans/2026-08-16-professional-mode-reentry-authority.md`
- Modify: `docs/plans/2026-08-16-professional-mode-reentry-authority.plan.json`
- Modify: `worker/.codex-plugin/plugin.json`
- Modify generated: `worker/mcp-servers/state-manager/dist/index.js`
- Modify generated: `worker/mcp-servers/state-manager/dist/mode-transition-hook.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/index.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/cli.js`
- Modify generated: `worker/mcp-servers/workspace-manager/dist/hook-intent.js`

### Step 0: Repair failed inactive-receipt replacement after the authenticated Task 4 F

The first post-F reseal created inactive receipt 22. `create_plan` correctly
rejected that plan because it changed non-reopened Task 3; the corrected plan
restores Task 3 exactly and widens only reopened Task 4, but the immutable
inactive receipt blocks a second reseal. Add RED/GREEN real-Git coverage and a
narrow authenticated replacement transaction. It must bind the existing
inactive receipt to the latest exact post-F reseal audit, unchanged session,
repository, checkout, lineage, active receipt and `HEAD`; retain the old ref as
retired audit evidence; create exactly one corrected inactive receipt; roll back
row/ref/audit changes on failure; and replay idempotently. Record this exact
receipt-22 variant in roadmap Wave 19. No manual database/ref edit, new lineage,
blind plan review, reset, clean, stash, commit, or push.

### Step 0a: Repair the fresh-Claude permission boundary after the authenticated Task 4 F

Receipt 85 recorded a real Task 4 Stage 1 failure: the authenticated Claude
fixture loaded both MCP servers, but the non-interactive `claude -p` process ran
in default permission mode and denied `inspect_authority_reentry` before MCP
execution. Preserve lineage 31 and all earlier reviewed tasks. Do not run another
blind plan review or replace the existing cachebuster.

Add a falsifying test that requires the Claude command to include the supported
non-interactive permission pair `--permission-mode bypassPermissions`, while the
Codex command remains unchanged. Run the focused test before implementation and
require failure:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_live_pm_reentry_provider_response.py -q
```

Add only that Claude permission pair to the dedicated provider harness command,
preserving project-only settings, explicit MCP configuration, strict MCP mode,
timeouts, one fresh session per fixture, and independent Git/SQLite/trace
verification. Rerun the same command and require success.

### Step 0b: Repair assignment-generation binding after the authenticated Task 4 F

Task-boundary review proved that public recovery guidance, capability execution,
automatic no-mutation recovery, trusted hook selection, and authority-epoch
observation all used the provisional literal `none`. Replace that placeholder
with one server-derived deterministic generation for the exact provider-root
session, repository, checkout, and active assignment. An authenticated absence
may use the explicit `none` sentinel; missing, malformed, ambiguous, or changed
workspace evidence must never silently become `none`. Deactivation remains
available by recording explicit uncertainty, while re-entry fails closed until
the generation is authenticated.

Add RED/GREEN real-database and hook coverage proving active-assignment
generation is non-`none` and identical across epoch, disclosure, capability,
receipt, and finalization; absence is authenticated; assignment creation,
replacement, owner/lifecycle change, and generation drift reject before Git or
state mutation; caller/hook payload cannot override server derivation; and
existing primary-checkout no-mutation recovery remains idempotent. Preserve the
same lineage and blind-review history; record only advisor remediation after the
corrected artifact reseal.

### Step 1: Update documentation

Document authority epochs, clean PM-off commit re-entry, explicit/ambiguous recovery guidance, exact command/effect disclosure, one-shot capability limits, no operator Git handback, and unchanged PM-on Git/commit/push blocking.

### Step 2: Complete the repository suite and run focused pre-release verification

Add `test-state-manager` to `.PHONY`, implement it as `cd worker/mcp-servers/state-manager && npm test`, and make the root `test` target depend on it alongside hooks, workspace-manager, and Commander.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm test -- --run src/__tests__/reentry-recovery.test.ts src/__tests__/reentry-recovery.mutation.test.ts src/__tests__/db.test.ts src/__tests__/git.test.ts src/__tests__/tool-dispatch.test.ts
```

Expected: selected tests pass.

Run the complete state-manager suite directly before the composite repository run:

```bash
make -C /Users/roberthyatt/Code/ironclaude test-state-manager
```

Expected: exit zero from the complete state-manager Vitest suite.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm test -- --run src/__tests__/professional-mode-epochs.test.ts src/__tests__/professional-mode-epochs.mutation.test.ts src/__tests__/pm-deactivation-audit.test.ts src/__tests__/review-receipts.test.ts src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/tool-dispatch.test.ts src/tools/write-tools.test.ts
```

Expected: selected tests pass.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_professional_mode_reentry_skill.py tests/test_activation_client_parity.py tests/test_deactivation_client_parity.py tests/test_live_pm_reentry_provider_response.py tests/test_version_consistency.py -q
```

Expected: selected tests pass.

### Step 3: Run one justified post-F complete repository suite

Run:

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

Expected: exit zero. Capture the terminal exit; do not infer success from partial
output. The earlier pre-F run remains baseline evidence; this one repetition is
required because the reviewed provider harness changed.

### Step 4: Preserve the final recovery Codex cachebuster

Require `worker/.codex-plugin/plugin.json` to remain exactly
`1.1.6+codex.20260818023851`. The bounded PM-off receipt-lifecycle repair
replaced the stale prior suffix once after all source edits were final. Do not
run the cachebuster helper again in this lineage.

### Step 5: Build final tracked bundles

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build
```

Expected: exit zero; `dist/index.js` contains current epoch/re-entry markers.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run bundle:mode-transition-hook
```

Expected: exit zero; `dist/mode-transition-hook.js` contains the trusted deactivation path.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager && npm run build
```

Expected: exit zero; `dist/index.js`, `dist/cli.js`, and `dist/hook-intent.js` contain current recovery markers.

### Step 6: Validate plugin and versions

Run:

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker
```

Expected: `Plugin validation passed: /Users/roberthyatt/Code/ironclaude/worker`.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python -m pytest tests/test_version_consistency.py -q
```

Expected: exit zero.

### Step 7: Run fresh provider behavior acceptance before installation

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/python scripts/live_pm_reentry_provider_response.py --client all --source-root /Users/roberthyatt/Code/ironclaude
```

Expected: Claude and Codex pass every explicit-guidance and ambiguous-dialogue fixture; unsupported elicitation is not accepted as success.

### Step 8: Record exact runtime oracle and stage release candidate

Write `/private/tmp/professional-mode-reentry-expected-runtime.json` from pre-install bytes with exact client, plugin root, manifest version/hash, state-manager index/mode-hook hashes, and workspace-manager index/CLI/hook-intent hashes. Read it back and verify every field, including `workspace_manager_cli_sha256`, is nonempty and derived from the current candidate.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- README.md CHANGELOG.md Makefile commander/scripts/live_pm_reentry_provider_response.py commander/tests/test_live_pm_reentry_provider_response.py docs/plans/2026-07-20-v1-1-overall-roadmap.md worker/mcp-servers/state-manager/src/plan-artifacts.ts worker/mcp-servers/state-manager/src/__tests__/plan-artifacts.test.ts worker/mcp-servers/state-manager/src/professional-mode-epochs.ts worker/mcp-servers/state-manager/src/mode-transition-hook.ts worker/mcp-servers/state-manager/src/tools/read-tools.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/tool-dispatch.ts worker/mcp-servers/state-manager/src/index.ts worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.test.ts worker/mcp-servers/state-manager/src/__tests__/tool-dispatch.test.ts worker/mcp-servers/state-manager/src/tools/write-tools.test.ts worker/mcp-servers/state-manager/src/__tests__/review-receipts.test.ts worker/mcp-servers/workspace-manager/src/reentry-recovery.ts worker/mcp-servers/workspace-manager/src/index.ts worker/mcp-servers/workspace-manager/src/hook-intent.ts worker/mcp-servers/workspace-manager/src/__tests__/reentry-recovery.test.ts worker/mcp-servers/workspace-manager/src/__tests__/tool-dispatch.test.ts worker/hooks/state-activator.sh worker/hooks/tests/test-git-authority-activation.sh worker/hooks/tests/test-professional-mode-deactivation.sh docs/plans/2026-08-16-professional-mode-reentry-authority.md docs/plans/2026-08-16-professional-mode-reentry-authority.plan.json worker/.codex-plugin/plugin.json worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/state-manager/dist/mode-transition-hook.js worker/mcp-servers/workspace-manager/dist/index.js worker/mcp-servers/workspace-manager/dist/cli.js worker/mcp-servers/workspace-manager/dist/hook-intent.js
```

Expected: exit zero.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero.

### Step 9: Reinstall as the final source/plugin/runtime mutation

Verify the preapproved delayed restart helper:

```bash
/usr/bin/python3 -c 'import hashlib,pathlib; p=pathlib.Path("/private/tmp/restart_codex.py"); expected="d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f"; actual=hashlib.sha256(p.read_bytes()).hexdigest(); assert actual == expected, f"restart helper hash mismatch: {actual}"'
```

Expected: exit zero.

Reinstall Claude before the terminal Codex mutation:

```bash
claude plugin uninstall ironclaude@ironclaude --scope user --keep-data --yes
```

Expected: exit zero.

```bash
claude plugin install ironclaude@ironclaude --scope user
```

Expected: Claude installs base `1.1.6`.

```bash
claude plugin details ironclaude@ironclaude
```

Expected: inventory lists updated activation/deactivation skills and both MCP servers.

Launch the verified delayed restart helper:

```bash
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/usr/bin/python3","/private/tmp/restart_codex.py"],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)'
```

Expected: exit zero; restart scheduled.

Run the final source/plugin/runtime mutation:

```bash
codex plugin add ironclaude@ironclaude --json
```

Expected: exit zero and reported installed version equals the cachebusted manifest version. No source edit, build, cachebuster, reinstall, commit, or push follows.

### Step 10: Prove fresh installed runtime and current-deadlock recovery

In fresh Claude and Codex sessions, verify exact installed root, version, manifest and bundle hashes, including the workspace CLI hash, against `/private/tmp/professional-mode-reentry-expected-runtime.json`; run provider-native diagnostics. Resume or reproduce the original conflicting provider-root session, execute clean off-period-commit re-entry, and prove dormant receipt/plan authority is historical, current `HEAD` is the new baseline, PM is on, and a fresh candidate no longer merges the stale receipt. Then submit and review Task 4 through the installed task-boundary path.

Expected: both clients match the oracle and complete the same behavior; task-boundary review records A or B before the lineage completes.
