# Workflow Transition Idempotency Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Make every explicit workflow transition use caller-side state preflight and a shared server-side same-target `changed: false` no-op without state, artifact, flag, wave, hook, timestamp, or audit side effects.

**Requirements:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md` — implement MP-W08 and preserve MP-W11.

**Design:** `docs/plans/2026-07-20-workflow-transition-idempotency-design.md`

**Architecture:** Transition-calling skills and the skill-state hook read the provider-native current session before requesting a stage change. Explicit transition tools delegate to one transactional state-machine helper that returns `changed: false` before any callback or mutation when current equals target. Compound operations retain their domain work and omit only redundant stage-transition effects.

**Tech stack:** TypeScript 5.9, Vitest, better-sqlite3, Bash hooks, pytest contract tests, Codex plugin CLI.

**Plan-construction rule:** Current source is authoritative. Signatures and assertions below are behavioral contracts, not permission to paste speculative replacements without reconciling imports, transaction behavior, and existing gates.

**Adjacent queued finding:** `worker/mcp-servers/state-manager/src/types.ts` and `PlanJsonSchema` omit the `requirements_file` required by the shipped writing/executing skills. Runtime currently accepts and preserves the unknown field. Do not expand Wave 1; add this verified contract mismatch to Wave 11 (`create_plan` hardening).

---

## Task 1: Centralize and make explicit workflow transitions idempotent

**Description:** Add focused red tests for every explicit transition, then implement one shared transactional transition path and refactor the handlers. Same-target detection must precede prerequisites, review gates, callbacks, timestamp updates, artifacts, flags, waves, and audits. Preserve compound-operation semantics and MP-W11 identity binding.

**Files:**
- Create: `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/state-machine.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/write-tools.ts`

### Step 1.1: Write the focused server-side regression suite (RED)

Create `worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts` using Vitest and an in-memory better-sqlite3 schema containing `sessions`, `registered_designs`, `wave_tasks`, `review_grades`, `tier_up_reviews`, `plan_history`, and `audit_log`.

The test contract is exact:

```ts
type ExpectedNoOp = {
  success: true;
  changed: false;
  from: WorkflowStage;
  to: WorkflowStage;
  session_id: string;
};

function snapshot(db: Database.Database, sessionId: string) {
  return {
    session: db.prepare('SELECT * FROM sessions WHERE terminal_session = ?').get(sessionId),
    designs: db.prepare('SELECT * FROM registered_designs WHERE terminal_session = ? ORDER BY design_file').all(sessionId),
    tasks: db.prepare('SELECT * FROM wave_tasks WHERE terminal_session = ? ORDER BY id').all(sessionId),
    reviews: db.prepare('SELECT * FROM review_grades WHERE terminal_session = ? ORDER BY id').all(sessionId),
    tierUpReviews: db.prepare('SELECT * FROM tier_up_reviews WHERE terminal_session = ? ORDER BY id').all(sessionId),
    history: db.prepare('SELECT * FROM plan_history WHERE terminal_session = ? ORDER BY id').all(sessionId),
    audit: db.prepare('SELECT * FROM audit_log WHERE terminal_session = ? ORDER BY id').all(sessionId),
  };
}
```

Table-driven same-target cases:

| Tool | Seed stage | Required setup |
|---|---|---|
| `mark_design_ready` | `design_ready` | Pass a new `file`; assert it is not registered or consumed. |
| `mark_plan_ready` | `plan_ready` | Seed consumed design. |
| `mark_brainstorming` | `brainstorming` | Seed flags and plan rows that must remain unchanged. |
| `mark_debugging` | `debugging` | Seed flags and plan rows that must remain unchanged. |
| `start_execution` | `executing` | Omit passing tier-up/review setup; no-op must occur before gates. |
| `mark_executing` | `executing` | Omit passing review grade; no-op must occur before gates. |
| `retreat` to brainstorming | `brainstorming` | Seed plan/design/task/flag state; none may be cleared. |
| `retreat` to debugging | `debugging` | Seed plan/design/task/flag state; none may be cleared. |

For every case, capture `before = snapshot(...)`, invoke `handleWriteTool`, parse `content[0].text`, assert the exact no-op result, capture `after`, and assert `after` deeply equals `before` including `updated_at` and audit count.

Add changed-transition cases asserting `changed: true`, exact `from`/`to`, one stage mutation, and one transition audit. Add invalid different-state cases asserting an error and identical snapshots.

Add a transactional rollback case. Install an in-memory trigger that raises `ABORT` on the transition audit insert, invoke a valid different-state transition, assert the call fails with the injected database error, and assert the full before/after snapshots remain deeply equal. This proves the stage update and audit insertion roll back together rather than testing only the successful transaction path.

Add compound-operation preservation cases:

- `create_plan` from `final_plan_prep` replaces `plan_json`, rebuilds Wave 1, remains at `final_plan_prep`, and records its domain audit.
- `reset_session` from `idle` clears plan/review/task state even though workflow stage remains `idle`.

Add a metadata test asserting `idempotentHint: true` only for the explicit transition tools covered above; do not relabel `create_plan` or `reset_session` merely because they can retain a stage.

### Step 1.2: Run the focused test and capture the expected RED evidence

Run:

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/__tests__/workflow-transition-idempotency.test.ts
```

Expected: FAIL. Existing handlers either reject same-target requests, mutate rows/audits, omit `changed`/`from`/`to`, or advertise non-idempotent metadata. The two compound-operation preservation tests should remain green.

Record exact failing/passing counts in execution commentary before editing source.

### Step 1.3: Add the shared transactional transition contract

In `worker/mcp-servers/state-manager/src/state-machine.ts`, add exported types and one shared operation with this externally observable contract:

```ts
export type WorkflowTransitionOutcome =
  | { ok: true; changed: false; from: WorkflowStage; to: WorkflowStage; session_id: string }
  | { ok: true; changed: true; from: WorkflowStage; to: WorkflowStage; session_id: string }
  | { ok: false; error: string; from?: WorkflowStage; to: WorkflowStage; session_id: string };
```

The implementation must perform these operations in order inside one `db.transaction`:

1. `getSession(db, sessionId)` for the dispatcher-resolved native root identity.
2. Return the exact `changed:false` branch immediately if `session.workflow_stage === target`.
3. Build prerequisite context from the row and transition-specific read callbacks.
4. Validate through `validateWorkflowTransition()`; an explicitly proven execution-recovery path may extend validity but must be represented in the shared context and tested.
5. Run a transition-specific guard callback after equality/transition validation and before mutation.
6. Run an optional transition-specific artifact callback.
7. Call `updateSession` once with transition-specific fields plus `workflow_stage: target`.
8. Insert exactly one transition audit row.
9. Return `changed:true` with exact `from`, `to`, and `session_id`.

Do not resolve session identity inside this helper, introduce a fallback, add schema, or call a callback on the no-op/error paths.

Refactor retreat artifact preservation so it can run as the shared helper's mutation callback without separately updating workflow stage or inserting a second transition audit.

### Step 1.4: Route explicit write tools through the shared path

In `worker/mcp-servers/state-manager/src/tools/write-tools.ts`:

- Route `mark_design_ready`, `mark_plan_ready`, `mark_brainstorming`, `mark_debugging`, `start_execution`, `mark_executing`, and `retreat` through the shared helper.
- Map successful outcomes to MCP text JSON containing `success`, `changed`, `from`, `to`, and `session_id`, plus existing tool-specific fields only when relevant.
- Map invalid outcomes to the existing `err(...)` surface with current and requested stages.
- Place tier-up and review-grade gates inside the transition-specific guard callback so same-target execution requests no-op before gates.
- Preserve the existing evidence-backed `mark_executing` recovery from a stale stage only when active wave tasks prove execution; record one correction/transition audit, not two.
- Mark only the explicit transition tool definitions `idempotentHint: true`.
- For `create_plan`, omit `workflow_stage` from `updateSession` when it is already `final_plan_prep`; still replace the plan, tasks, and domain audit.
- For `reset_session`, continue clearing domain state when already `idle`; omit a redundant stage-transition claim.

No other handler behavior changes in this task.

### Step 1.5: Run focused and related state-manager tests (GREEN)

Run:

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/mark-executing-review-gate.test.ts src/tools/write-tools.test.ts src/tools/write-tools.tier-up.test.ts src/__tests__/session-identity.test.ts src/__tests__/tool-dispatch.test.ts src/__tests__/run-diagnostics.test.ts
```

Expected: all selected test files pass. Report exact file/test counts. Any failure in tier-up gates, recovery, revised-plan reload, identity, dispatch, or diagnostics blocks staging.

### Step 1.6: Stage Task 1 files only

Run:

```bash
git add worker/mcp-servers/state-manager/src/state-machine.ts worker/mcp-servers/state-manager/src/tools/write-tools.ts worker/mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts
```

Expected: the three Task 1 paths are staged; no commit is created.

---

## Task 2: Enforce caller preflight and suppress no-op hook effects

**Description:** Add red skill/hook contract tests, then require identity-bound current-state reads before every explicit transition. Make the skill bridge skip same-target SQL/audit work and make the post-tool logger suppress `changed:false` output.

**Depends on:** Task 1

**Files:**
- Create: `commander/tests/test_workflow_transition_preflight.py`
- Create: `worker/hooks/tests/test-workflow-transition-idempotency.sh`
- Modify: `worker/skills/brainstorming/SKILL.md`
- Modify: `worker/skills/writing-plans/SKILL.md`
- Modify: `worker/skills/executing-plans/SKILL.md`
- Modify: `worker/hooks/skill-state-bridge.sh`
- Modify: `worker/hooks/mcp-state-logger.sh`

### Step 2.1: Write skill-contract tests (RED)

Create `commander/tests/test_workflow_transition_preflight.py`. Read the three skill files from `REPO_ROOT / "worker" / "skills"` and assert:

```python
TRANSITIONS = {
    "brainstorming": ("mark_brainstorming", "mark_design_ready"),
    "writing-plans": ("mark_plan_ready",),
    "executing-plans": ("start_execution", "mark_executing", "retreat"),
}
```

For every named transition, require nearby instructions to:

- call `get_resume_state` first;
- verify returned `session_id` matches the current provider-native root task;
- compare `workflow_stage` with the target;
- skip the transition call when equal;
- stop and report identity mismatch or invalid different-state error;
- avoid blind retry.

Assert the executing skill explicitly states that stage equality never skips `create_plan`, because coherent revised plans must be reloaded.

Assert the direct skill-caller inventory is exact: no current skill directly calls `mark_debugging`; `systematic-debugging` reaches `debugging` through `skill-state-bridge.sh`. The contract test must not require or introduce a duplicate skill-side transition call.

### Step 2.2: Write the isolated hook harness (RED)

Create executable `worker/hooks/tests/test-workflow-transition-idempotency.sh`. It must:

1. Create a temporary HOME with `.claude/ironclaude.db` in WAL mode and minimal `sessions`/`audit_log` schema.
2. Set `verbose_hook_logs: true` in the temporary hook config.
3. Seed a session with stable `updated_at`, flags, and audit rows.
4. Invoke `skill-state-bridge.sh` with a structured `systematic-debugging` Skill payload whose `session_id` and mapped `debugging` target equal the seeded stage.
5. Assert the complete session row and audit table are byte-for-byte unchanged and stdout has no transition/activation message.
6. Invoke the same `systematic-debugging` payload from an allowed different stage and assert exactly one stage update and one activation audit.
7. Invoke `mcp-state-logger.sh` with both supported PostToolUse representations: a `tool_output` JSON object/string containing MCP `content[0].text` and a legacy `tool_response` wrapper.
8. Assert `changed:false` produces no transition message and `changed:true` produces exactly one transition message.
9. Send malformed object, string, and `content[0].text` response variants; assert exit code 0, no stdout transition message, and byte-for-byte unchanged session/audit snapshots.
10. Remove the temporary directory through its EXIT trap.

The harness prints one `PASS` line per case and exits nonzero on the first mismatch.

### Step 2.3: Run both focused suites and capture RED evidence

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_workflow_transition_preflight.py -q
```

Expected: FAIL because current skills call transitions unconditionally.

Run:

```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

Expected: FAIL because same-target bridge calls still update/audit and the MCP logger ignores `changed`.

Record exact failures before editing source.

### Step 2.4: Add caller-side preflight to the workflow skills

Update each transition instruction in the three skill files with the same contract:

```text
1. Call get_resume_state for the current provider-native root task.
2. If session_id is missing or differs from the current native root task, STOP without transition.
3. If workflow_stage equals the target, continue without calling the transition tool.
4. Otherwise call the transition tool once.
5. Require changed:true for a requested different-state transition; report errors and do not retry without a fresh read.
```

Apply it before:

- debugging return and design completion in `brainstorming`;
- plan completion in `writing-plans`;
- execution start, return from review, and retreat in `executing-plans`.

Keep `create_plan` outside this skip rule and state why. Do not alter review policy, task orchestration, or unrelated prose.

### Step 2.5: Make hook paths side-effect-free on same target

In `worker/hooks/skill-state-bridge.sh`, after reading `CURRENT_STAGE` and deriving `TARGET_STAGE`, exit successfully before `db_write_or_fail`, `db_audit_log`, or `log_hook` when the stages match. Do not reset `active_skill`, `memory_search_required`, `testing_theatre_checked`, or `updated_at` on that no-op path.

In `worker/hooks/mcp-state-logger.sh`, inspect `.tool_output` first and `.tool_response` as compatibility input. Handle an object, a JSON string, and MCP `content[].text` JSON. If the parsed successful response contains `changed: false`, exit before the DB read and `log_hook`. Changed or legacy responses retain best-effort current-stage logging. Malformed response data must not mutate state or hard-fail the tool call.

Do not modify hook registration or introduce asynchronous hooks.

### Step 2.6: Run focused skill and hook tests (GREEN)

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_workflow_transition_preflight.py commander/tests/test_executing_plans_skill.py commander/tests/test_writing_plans_skill.py -q
```

Expected: all selected Commander contract tests pass with exact counts reported.

Run:

```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

Expected: every harness case prints `PASS`; exit code 0.

### Step 2.7: Stage Task 2 files only

Run:

```bash
git add commander/tests/test_workflow_transition_preflight.py worker/hooks/tests/test-workflow-transition-idempotency.sh worker/skills/brainstorming/SKILL.md worker/skills/writing-plans/SKILL.md worker/skills/executing-plans/SKILL.md worker/hooks/skill-state-bridge.sh worker/hooks/mcp-state-logger.sh
```

Expected: exactly the seven Task 2 paths are added to the existing staged work; no commit is created.

---

## Task 3: Build, verify, hot-deploy, and record Wave 1 evidence

**Description:** Rebuild generated distribution, repair the evidence-backed Codex cachebuster/version-consistency contract, run focused and complete regression gates, validate the plugin, install a cachebusted local mirror, prove source/cache parity, and record evidence. Tasks 1 and 2 own transition regression coverage; this task adds only the focused Commander contract required for its own cachebuster deployment step.

**Depends on:** Tasks 1 and 2

**Files:**
- Modify: `commander/tests/test_version_consistency.py`
- Modify: `worker/mcp-servers/state-manager/dist/index.js`
- Modify: `worker/.codex-plugin/plugin.json`
- Create: `docs/validation/2026-07-20-workflow-transition-idempotency.md`

### Step 3.1: Rebuild state-manager distribution

Run:

```bash
npm --prefix worker/mcp-servers/state-manager run build
```

Expected: TypeScript compilation and esbuild bundle succeed; `worker/mcp-servers/state-manager/dist/index.js` changes and contains the verified transition implementation.

### Step 3.2: Re-run every focused Wave 1 gate against the built source

Run:

```bash
npm --prefix worker/mcp-servers/state-manager test -- src/__tests__/workflow-transition-idempotency.test.ts src/__tests__/mark-executing-review-gate.test.ts src/tools/write-tools.test.ts src/tools/write-tools.tier-up.test.ts src/__tests__/session-identity.test.ts src/__tests__/tool-dispatch.test.ts src/__tests__/run-diagnostics.test.ts
```

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_workflow_transition_preflight.py commander/tests/test_executing_plans_skill.py commander/tests/test_writing_plans_skill.py -q
```

Run:

```bash
bash worker/hooks/tests/test-workflow-transition-idempotency.sh
```

Expected: all focused gates pass with exact counts and no skipped required case.

### Step 3.3: Repair and prove the Codex cachebuster version contract

Use the captured full-suite RED evidence as the starting failure: `test_version_sources_match` rejected `1.0.24+codex.20260720062826` while the other release declarations were `1.0.24` (`1 failed, 1989 passed`).

In `commander/tests/test_version_consistency.py`, first add focused parameterized cases for a pure helper that:

- returns the unchanged release version when no `+` metadata exists;
- strips one valid `+codex.<lowercase-alphanumeric-or-hyphen-token>` suffix and returns the release prefix;
- rejects empty, repeated, non-Codex, or malformed metadata rather than hiding arbitrary drift.

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_version_consistency.py -q
```

Expected RED: new focused cases fail because the normalizer does not exist or the raw Codex manifest version is still compared.

Implement the smallest helper and use it only for `worker/.codex-plugin/plugin.json`; keep every other declared version exact. Run the same command again.

Expected GREEN: all version-consistency cases pass, including the currently cachebusted source manifest and invalid-metadata rejection.

### Step 3.4: Run complete regression suites

Run:

```bash
npm --prefix worker/mcp-servers/state-manager test
```

Expected: every state-manager test file and test passes.

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests -q
```

Expected: complete Commander suite passes. This is a long-running release gate; do not substitute a narrow suite.

Run:

```bash
git diff --check
git diff --cached --check
```

Expected: neither unstaged nor staged changes contain whitespace errors.

### Step 3.5: Validate and cachebust the local plugin

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py worker
```

Expected: `Plugin validation passed`.

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py worker
```

Expected: base version remains `1.0.24` and one new `+codex.<timestamp>` suffix replaces the prior suffix.

Run validation again:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py worker
```

Expected: `Plugin validation passed` for the cachebusted manifest.

Run the focused final-state version contract again:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_version_consistency.py -q
```

Expected: all version-consistency tests pass against the final cachebusted source manifest.

### Step 3.6: Confirm the local marketplace and reinstall

Run:

```bash
codex plugin marketplace list
```

Expected: marketplace `ironclaude` root is `/Users/roberthyatt/Code/ironclaude`.

Run:

```bash
codex plugin add ironclaude@ironclaude
```

Expected: plugin installs successfully from the local marketplace and reports the new cache path.

### Step 3.7: Prove source/cache parity

Run this deterministic source/cache comparison. It derives the installed path only from the cachebusted source manifest and stops on a missing directory, missing file, or byte mismatch:

```bash
set -euo pipefail
IRONCLAUDE_W1_VERSION=$(node -p "require('./worker/.codex-plugin/plugin.json').version")
IRONCLAUDE_W1_CACHE="/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/$IRONCLAUDE_W1_VERSION"
test -d "$IRONCLAUDE_W1_CACHE"
for IRONCLAUDE_W1_REL in '.codex-plugin/plugin.json' 'mcp-servers/state-manager/src/state-machine.ts' 'mcp-servers/state-manager/src/tools/write-tools.ts' 'mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts' 'mcp-servers/state-manager/dist/index.js' 'skills/brainstorming/SKILL.md' 'skills/writing-plans/SKILL.md' 'skills/executing-plans/SKILL.md' 'hooks/skill-state-bridge.sh' 'hooks/mcp-state-logger.sh' 'hooks/tests/test-workflow-transition-idempotency.sh'; do
  test -f "worker/$IRONCLAUDE_W1_REL"
  test -f "$IRONCLAUDE_W1_CACHE/$IRONCLAUDE_W1_REL"
  cmp -s "worker/$IRONCLAUDE_W1_REL" "$IRONCLAUDE_W1_CACHE/$IRONCLAUDE_W1_REL"
  echo "MATCH $IRONCLAUDE_W1_REL $(shasum -a 256 "worker/$IRONCLAUDE_W1_REL" | awk '{print $1}')"
done
```

The exact compared plugin paths are:

- `.codex-plugin/plugin.json`
- `mcp-servers/state-manager/src/state-machine.ts`
- `mcp-servers/state-manager/src/tools/write-tools.ts`
- `mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts`
- `mcp-servers/state-manager/dist/index.js`
- `skills/brainstorming/SKILL.md`
- `skills/writing-plans/SKILL.md`
- `skills/executing-plans/SKILL.md`
- `hooks/skill-state-bridge.sh`
- `hooks/mcp-state-logger.sh`
- `hooks/tests/test-workflow-transition-idempotency.sh`

Expected: every source/installed pair reports `MATCH`. Any missing or mismatched file blocks completion.

### Step 3.8: Write deployment evidence

Create `docs/validation/2026-07-20-workflow-transition-idempotency.md` containing:

- requirement/design/plan paths;
- confirmed root cause;
- RED and GREEN focused counts;
- full state-manager and Commander counts;
- captured version-consistency RED, focused GREEN counts, and post-cachebuster final-state GREEN count;
- build and plugin-validation results;
- installed version and cache path;
- one hash row per compared file;
- explicit evidence that same-target calls leave session/artifact/flag/wave/hook/audit snapshots unchanged;
- MP-W11 identity regression results;
- restart-pending status and same-task resume instruction;
- complete MP-001 through MP-C07 future-loop mapping and explicit release-blocking status from the approved design ledger;
- the queued Wave 11 `requirements_file` schema/type mismatch;
- statement that no commit or push occurred.

### Step 3.9: Stage generated/deployment artifacts and stop at restart boundary

Run:

```bash
git add commander/tests/test_version_consistency.py worker/mcp-servers/state-manager/dist/index.js worker/.codex-plugin/plugin.json
```

Run:

```bash
git add -f docs/validation/2026-07-20-workflow-transition-idempotency.md
```

Expected: Task 3 artifacts are staged. Do not commit or push. Submit Task 3 for its normal review gate; after a passing review, leave Task 4 pending.

Report that source and installed cache are complete, then require a full Codex quit/restart. Reopen this same Codex task so its native task identity and durable plan remain bound; do not start a new task. Do not claim live runtime activation or Roadmap Wave 1 completion before Task 4 passes.

---

## Task 4: Resume after restart and prove live runtime activation

**Description:** After Task 3 passes review, fully quit and relaunch Codex, reopen this same task, verify the cachebusted plugin is the loaded runtime, prove native session/plan continuity, and finalize the durable Wave 1 evidence. This task changes documentation only.

**Depends on:** Task 3

**Files:**
- Modify: `docs/validation/2026-07-20-workflow-transition-idempotency.md`

### Step 4.1: Cross the explicit process boundary without changing tasks

After Task 3 passes review, fully quit Codex, relaunch it, and reopen this same task. Re-invoke `ironclaude:executing-plans`; it must call `get_resume_state` before any transition and resume Task 4 rather than creating or reloading a different plan.

Expected: native `session_id` still equals this task's Codex thread ID; professional mode is on; plan name is `Workflow Transition Idempotency`; Tasks 1-3 are review-passed; Task 4 is pending/in progress; workflow stage is the valid execution-resume stage. Any missing, ambiguous, or mismatched identity blocks completion.

### Step 4.2: Verify loaded plugin version and source/cache bytes

Run:

```bash
codex plugin list
```

Expected: `ironclaude@ironclaude` reports the exact cachebusted version in `worker/.codex-plugin/plugin.json` and the configured marketplace remains `/Users/roberthyatt/Code/ironclaude`.

Re-run the exact Step 3.6 source/cache comparison command after restart:

```bash
set -euo pipefail
IRONCLAUDE_W1_VERSION=$(node -p "require('./worker/.codex-plugin/plugin.json').version")
IRONCLAUDE_W1_CACHE="/Users/roberthyatt/.codex/plugins/cache/ironclaude/ironclaude/$IRONCLAUDE_W1_VERSION"
test -d "$IRONCLAUDE_W1_CACHE"
for IRONCLAUDE_W1_REL in '.codex-plugin/plugin.json' 'mcp-servers/state-manager/src/state-machine.ts' 'mcp-servers/state-manager/src/tools/write-tools.ts' 'mcp-servers/state-manager/src/__tests__/workflow-transition-idempotency.test.ts' 'mcp-servers/state-manager/dist/index.js' 'skills/brainstorming/SKILL.md' 'skills/writing-plans/SKILL.md' 'skills/executing-plans/SKILL.md' 'hooks/skill-state-bridge.sh' 'hooks/mcp-state-logger.sh' 'hooks/tests/test-workflow-transition-idempotency.sh'; do
  test -f "worker/$IRONCLAUDE_W1_REL"
  test -f "$IRONCLAUDE_W1_CACHE/$IRONCLAUDE_W1_REL"
  cmp -s "worker/$IRONCLAUDE_W1_REL" "$IRONCLAUDE_W1_CACHE/$IRONCLAUDE_W1_REL"
  echo "MATCH $IRONCLAUDE_W1_REL $(shasum -a 256 "worker/$IRONCLAUDE_W1_REL" | awk '{print $1}')"
done
```

Expected: all eleven pairs report `MATCH` from the cache directory named by that exact version.

### Step 4.3: Prove live native-session diagnostics

Call `run_diagnostics({})` through the reloaded state manager.

Expected: every diagnostic passes; both hook and MCP resolution bind to this task's native Codex session ID; no fallback, cross-client identity, collision, or live-state mutation is reported.

### Step 4.4: Finalize and stage activation evidence

Update `docs/validation/2026-07-20-workflow-transition-idempotency.md` with:

- restart/reopen timestamp and same-task native session ID;
- loaded plugin version and cache path from Step 4.2;
- every post-restart parity hash;
- complete `run_diagnostics` pass count and identity result;
- `runtime-active: verified` replacing `restart-pending`;
- confirmation that no commit or push occurred.

Run:

```bash
git add -f docs/validation/2026-07-20-workflow-transition-idempotency.md
```

Expected: final activation evidence is staged. Submit Task 4 for the normal review gate. Only a passing review completes Roadmap Wave 1 and permits Wave 2 brainstorming.

---

## Requirements → Design → Plan Parity Audit

| Requirement/design invariant | Task and evidence |
|---|---|
| Every caller reads state first and skips same target | Task 2 skill contracts and hook harness |
| `mark_debugging` coverage follows real source topology | Task 1 server test plus Task 2 systematic-debugging bridge harness; no invented direct skill caller |
| Server returns `changed:false`, `from`, and `to` | Task 1 table-driven tests and shared helper |
| No session/timestamp/artifact/flag/wave/hook/audit side effects | Task 1 database snapshots plus Task 2 hook snapshots |
| Invalid different-state transitions remain errors | Task 1 invalid-transition tests |
| Valid transitions mutate/audit exactly once | Task 1 changed-transition tests |
| Compound domain operations retain behavior | Task 1 revised-plan reload and idle-reset tests |
| Shared state-machine path | Task 1 helper and handler refactor |
| MP-W11 native identity remains exact | Task 1 related tests and Task 3 full regression |
| Built and installed behavior matches source | Task 3 build, validation, reinstall, and deterministic SHA-256 evidence; Task 4 post-restart recheck |
| Codex cachebuster preserves release-version consistency | Task 3 focused valid/invalid suffix tests, full Commander suite, and post-cachebuster rerun |
| Transaction failure is atomic | Task 1 audit-trigger fault injection and full snapshot equality |
| Malformed hook responses are side-effect-free | Task 2 malformed payload matrix and full hook snapshots |
| Restart activates the exact verified runtime without losing identity | Task 4 same-task resume, plugin version/parity, and diagnostics |
| No scope drift into later roadmap waves | Explicit file list, adjacent finding queued for Wave 11, no provider/Slack/schema work |
| Complete active ledger remains traceable and release-blocking | Design ledger plus Task 3 validation evidence; later requirements remain in named roadmap loops |

Human and machine plans must contain the same four tasks, dependency chain `1 -> 2 -> 3 -> 4`, exact allowed-file lists, ordered commands, expected results, restart boundary, and acceptance gates.
