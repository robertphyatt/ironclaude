# Direct-Human Professional-Mode Deactivation Availability Implementation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Make exact direct-human professional-mode deactivation succeed through a stable authenticated runtime or a trusted emergency closure without disposable cache-path vetoes.

**Requirements:** `docs/plans/2026-08-18-direct-human-pm-deactivation-availability-requirements.md`

**Design:** `docs/plans/2026-08-18-direct-human-pm-deactivation-availability-design.md`

**Architecture:** Register provider-root session identity while the provider runtime is known-good, deploy an atomic version-independent transition generation containing the mode hook and its native runtime closure, lease that generation for each transition, and let exact direct-human events invoke it. If stable-runtime resolution or execution fails after direct-human and existing-session authentication, the trusted hook executes a generated state-manager-owned emergency closure with the same epoch, audit, preservation, and rollback invariants. Cache-root variables remain diagnostics; they cannot veto deactivation or select executable code.

**Tech stack:** Bash hooks, TypeScript, Node.js, `better-sqlite3`, SQLite, Vitest, Python/pytest, Claude and Codex provider-native CLIs.

## Execution invariants

- Each command starts in a fresh shell. Commands use absolute paths or explicit `-C`; no step relies on prior shell variables.
- Bash may start in `commander/`; every repository command names `/Users/roberthyatt/Code/ironclaude` explicitly.
- Quote every glob. Do not suppress evidence-command stderr or truncate searches used to prove absence.
- Planning artifacts are immutable review evidence, not task write scope.
- Preserve workflow, plans, tasks, receipts, index, working bytes, refs, assignments, and Commander state during every deactivation test.
- Exactly one blind plan review is permitted for this lineage. A `HAS-ISSUES` verdict receives advisor remediation in place; it does not trigger another blind review.
- Prove each new protection with a production mutation or injected failure that makes its test fail.
- Stable runtime cleanup must preserve current, last-known-good, and actively leased generations; supersession cannot invalidate an in-flight transition.
- The emergency closure is reachable only from the exact trusted direct-human hook after existing-session authentication. It is not a public SQL, MCP, agent, or Commander surface.
- Do not run live Claude inference during implementation tests. Run each fresh-provider acceptance fixture once only in Task 4 after source review and before installation.
- Do not commit or push. The operator requested one commit only after all ten roadmap loops finish.

---

## Task 1: Add durable provider bindings, generation leases, and trusted closure

**Files:**

- Create: `worker/mcp-servers/state-manager/src/provider-runtime-bindings.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/provider-runtime-bindings.test.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/provider-runtime-bindings.mutation.test.ts`
- Create: `worker/mcp-servers/state-manager/src/provider-installation-observations.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/provider-installation-observations.test.ts`
- Create: `worker/mcp-servers/state-manager/src/stable-runtime-generations.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/stable-runtime-generations.test.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/stable-runtime-generations.mutation.test.ts`
- Create: `worker/mcp-servers/state-manager/src/provider-runtime-leases.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/provider-runtime-leases.test.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/provider-runtime-leases.mutation.test.ts`
- Create: `worker/mcp-servers/state-manager/src/trusted-deactivation-fallback.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/trusted-deactivation-fallback.test.ts`
- Create: `worker/mcp-servers/state-manager/src/__tests__/trusted-deactivation-fallback.mutation.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/db.ts`
- Modify: `worker/mcp-servers/state-manager/src/types.ts`
- Modify: `worker/mcp-servers/state-manager/src/mode-transition-hook.ts`
- Modify: `worker/mcp-servers/state-manager/src/professional-mode-epochs.ts`
- Modify: `worker/mcp-servers/state-manager/src/session-identity.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/session-identity.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.mutation.test.ts`
- Modify: `worker/mcp-servers/state-manager/dist/index.js`
- Modify: `worker/mcp-servers/state-manager/dist/mode-transition-hook.js`

### Step 1: Add RED provider-session binding tests

Create real-database tests covering:

- exact session/client/runtime registration;
- same-generation idempotent replay;
- atomic supersession by a verified newer generation;
- atomic acquisition, heartbeat, idempotent release, and bounded expiry of a generation lease;
- cleanup exclusion for current, last-known-good, and actively leased generations;
- stale, deleted, and symlinked installation observations;
- forged client, other session, other provider, manifest mismatch, bundle mismatch, and generation rollback;
- concurrent sessions and concurrent registration;
- concurrent supersession/cleanup while an earlier generation is leased;
- a shared complete preservation fixture covering workflow, plan, tasks, reviews,
  receipts, all non-mode session fields, index tree, working bytes, commits, refs,
  assignments, and Commander state for primary success, fallback success, replay,
  every injected failure, and rollback;
- exactly one closed epoch and unchanged preserved state after stale-root deactivation;
- exact emergency closure parity with the primary epoch/mode/audit transaction.

The binding contract must expose this shape:

```ts
export interface ProviderRuntimeBinding {
  terminalSession: string;
  client: 'claude' | 'codex';
  generation: string;
  createdAt: string;
}
```

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/provider-runtime-bindings.test.ts src/__tests__/provider-runtime-bindings.mutation.test.ts src/__tests__/provider-installation-observations.test.ts src/__tests__/stable-runtime-generations.test.ts src/__tests__/stable-runtime-generations.mutation.test.ts src/__tests__/provider-runtime-leases.test.ts src/__tests__/provider-runtime-leases.mutation.test.ts src/__tests__/trusted-deactivation-fallback.test.ts src/__tests__/trusted-deactivation-fallback.mutation.test.ts src/__tests__/professional-mode-epochs.test.ts src/__tests__/professional-mode-epochs.mutation.test.ts src/__tests__/session-identity.test.ts
```

Expected: nonzero exit. New binding tests fail because the module/schema and stable-session resolver do not exist. Record the exact failures before implementation.

### Step 2: Implement the binding ledger and resolver

Add a state-manager-owned schema with one current generation per exact session/client and retained superseded evidence:

```sql
CREATE TABLE IF NOT EXISTS provider_runtime_bindings (
  binding_id TEXT PRIMARY KEY,
  terminal_session TEXT NOT NULL,
  client TEXT NOT NULL CHECK (client IN ('claude','codex')),
  generation TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(terminal_session, client, generation)
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_provider_runtime_current
  ON provider_runtime_bindings(terminal_session, client);

CREATE TABLE IF NOT EXISTS provider_installation_observations (
  observation_id TEXT PRIMARY KEY,
  binding_id TEXT NOT NULL,
  installed_root TEXT,
  canonical_root TEXT,
  installed_manifest_sha256 TEXT,
  installed_bundle_sha256 TEXT,
  relation TEXT NOT NULL CHECK (relation IN
    ('current','superseded','alias','missing','unreadable','cross_client',
     'unrelated','digest_mismatch')),
  observed_at TEXT NOT NULL,
  FOREIGN KEY(binding_id) REFERENCES provider_runtime_bindings(binding_id)
);

CREATE TABLE IF NOT EXISTS stable_runtime_generations (
  generation TEXT PRIMARY KEY,
  stable_root TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  transition_bundle_sha256 TEXT NOT NULL,
  native_runtime_sha256 TEXT NOT NULL,
  publication_state TEXT NOT NULL CHECK
    (publication_state IN ('prepared','committed','retired')),
  prepared_at TEXT NOT NULL,
  committed_at TEXT,
  retired_at TEXT
);

CREATE TABLE IF NOT EXISTS stable_runtime_heads (
  client TEXT PRIMARY KEY CHECK (client IN ('claude','codex')),
  current_generation TEXT NOT NULL,
  last_known_good_generation TEXT NOT NULL,
  revision INTEGER NOT NULL,
  FOREIGN KEY(current_generation) REFERENCES stable_runtime_generations(generation),
  FOREIGN KEY(last_known_good_generation) REFERENCES stable_runtime_generations(generation)
);

CREATE TABLE IF NOT EXISTS provider_runtime_leases (
  lease_id TEXT PRIMARY KEY,
  terminal_session TEXT NOT NULL,
  client TEXT NOT NULL CHECK (client IN ('claude','codex')),
  generation TEXT NOT NULL,
  binding_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  fencing_token INTEGER NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  released_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('active','released','expired')),
  UNIQUE(terminal_session, client, event_id)
);

CREATE TABLE IF NOT EXISTS provider_runtime_fences (
  terminal_session TEXT NOT NULL,
  client TEXT NOT NULL CHECK (client IN ('claude','codex')),
  generation TEXT NOT NULL,
  next_token INTEGER NOT NULL,
  PRIMARY KEY(terminal_session, client, generation)
);
```

Implement immutable generation preparation, fsync/self-test, binding/head commit, and
startup reconciliation. SQLite committed heads are authoritative; filesystem
`current` is reconstructible. A committed binding can reference only complete,
digest-valid generation bytes. Installation observations append independently so
same-generation Claude replacements retain every root/digest observation.

Implement fenced lease acquisition and release inside explicit transactions. Every
heartbeat and final transition validates the current token. Cleanup expires only
bounded leases, excludes current/last-known-good/active-token generations, and
cannot let a resumed stale holder commit.

Implement one canonical trusted-deactivation transaction contract shared by the
primary bridge and generated emergency closure. Both accept only the existing
session and server-derived delivery identity after the trusted hook check. Run one
backend-neutral conformance suite against both implementations and compare result,
epoch, mode, audit, rollback, and complete preserved-state snapshots.

Add replay-safe migration for pre-feature sessions. Durable client attachment is
registered when provider-native startup knows it. Missing/conflicting attachment is
recorded as uncertainty, never inferred from cache roots, and never vetoes trusted
human deactivation of an existing session.

### Step 3: Route trusted deactivation through the exact binding

Extend `closeProfessionalModeFromTrustedHook` so the bridge supplies a verified
binding/generation when available and explicit uncertainty otherwise. Preserve the
existing exact `UserPromptSubmit` plus `invocationSource='human'` checks. Record
stale/missing/cross-client root and client observations without letting them select
authority or code.

Do not add a public MCP off transition or general raw SQL fallback. The generated emergency closure is embedded into the trusted hook and may run only after exact direct-human provenance and existing-session authentication when stable-runtime resolution or execution fails.

### Step 4: Prove GREEN and mutation sensitivity

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/provider-runtime-bindings.test.ts src/__tests__/provider-runtime-bindings.mutation.test.ts src/__tests__/provider-installation-observations.test.ts src/__tests__/stable-runtime-generations.test.ts src/__tests__/stable-runtime-generations.mutation.test.ts src/__tests__/provider-runtime-leases.test.ts src/__tests__/provider-runtime-leases.mutation.test.ts src/__tests__/trusted-deactivation-fallback.test.ts src/__tests__/trusted-deactivation-fallback.mutation.test.ts src/__tests__/professional-mode-epochs.test.ts src/__tests__/professional-mode-epochs.mutation.test.ts src/__tests__/pm-deactivation-audit.test.ts src/__tests__/session-identity.test.ts && npx tsc --noEmit
```

Expected: exit zero. The summary names binding, lease, fallback, epoch, and audit tests. Temporarily remove one session check, digest check, lease exclusion, fallback provenance check, and transaction guard in separate mutations and require the corresponding test to fail, then restore production code.

### Step 5: Build and stage Task 1

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build && npm run bundle:mode-transition-hook
```

Expected: exit zero; both tracked bundles contain provider-runtime binding markers.

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/src/provider-runtime-bindings.ts worker/mcp-servers/state-manager/src/__tests__/provider-runtime-bindings.test.ts worker/mcp-servers/state-manager/src/__tests__/provider-runtime-bindings.mutation.test.ts worker/mcp-servers/state-manager/src/provider-installation-observations.ts worker/mcp-servers/state-manager/src/__tests__/provider-installation-observations.test.ts worker/mcp-servers/state-manager/src/stable-runtime-generations.ts worker/mcp-servers/state-manager/src/__tests__/stable-runtime-generations.test.ts worker/mcp-servers/state-manager/src/__tests__/stable-runtime-generations.mutation.test.ts worker/mcp-servers/state-manager/src/provider-runtime-leases.ts worker/mcp-servers/state-manager/src/__tests__/provider-runtime-leases.test.ts worker/mcp-servers/state-manager/src/__tests__/provider-runtime-leases.mutation.test.ts worker/mcp-servers/state-manager/src/trusted-deactivation-fallback.ts worker/mcp-servers/state-manager/src/__tests__/trusted-deactivation-fallback.test.ts worker/mcp-servers/state-manager/src/__tests__/trusted-deactivation-fallback.mutation.test.ts worker/mcp-servers/state-manager/src/db.ts worker/mcp-servers/state-manager/src/types.ts worker/mcp-servers/state-manager/src/mode-transition-hook.ts worker/mcp-servers/state-manager/src/professional-mode-epochs.ts worker/mcp-servers/state-manager/src/session-identity.ts worker/mcp-servers/state-manager/src/__tests__/session-identity.test.ts worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.test.ts worker/mcp-servers/state-manager/src/__tests__/professional-mode-epochs.mutation.test.ts worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/state-manager/dist/mode-transition-hook.js && git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero; cached diff check passes and no Task 1 path remains unstaged.

---

## Task 2: Deploy a leased stable transition generation and connect trusted hooks

**Files:**

- Create: `worker/mcp-servers/state-manager/cli/stable-transition-runtime.js`
- Create: `worker/mcp-servers/state-manager/scripts/build-trusted-deactivation-fallback.mjs`
- Create: `worker/mcp-servers/state-manager/src/__tests__/stable-transition-runtime.test.ts`
- Modify: `worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js`
- Modify: `worker/mcp-servers/state-manager/package.json`
- Modify: `worker/mcp-servers/state-manager/src/runtime-fingerprint.ts`
- Modify: `worker/mcp-servers/state-manager/src/tools/read-tools.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/runtime-fingerprint.test.ts`
- Modify: `worker/mcp-servers/state-manager/src/__tests__/run-diagnostics.test.ts`
- Modify: `worker/mcp-servers/state-manager/dist/index.js`
- Modify: `worker/mcp-servers/state-manager/dist/mode-transition-hook.js`
- Create: `worker/mcp-servers/state-manager/dist/trusted-deactivation-fallback.js`
- Modify: `worker/hooks/state-activator.sh`
- Modify: `worker/hooks/session-init.sh`
- Modify: `worker/hooks/tests/test-professional-mode-deactivation.sh`
- Modify: `worker/hooks/tests/test-git-authority-activation.sh`
- Modify: `worker/hooks/tests/test-professional-mode-off-authority.sh`
- Modify: `Makefile`
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/tests/test_main_validate.py`

### Step 1: Add RED stable-generation and stale-root tests

Add real filesystem/runtime tests proving:

- a generation contains `mode-transition-hook.js`, `better-sqlite3`, `bindings`, and `file-uri-to-path` runtime closure plus a manifest digest for every executable/dependency byte;
- generation publication validates all bytes before atomically changing `current`;
- a referenced prior generation remains usable after plugin-cache deletion;
- a leased prior generation remains usable during concurrent publication and cleanup;
- partial generation, digest drift, path escape, and newest-cache substitution fail before publication;
- existing direct-human session binding chooses the stable generation when `PLUGIN_ROOT` names a deleted cache directory;
- forged root hints and different sessions cannot choose runtime or client;
- exact slash, dollar, and Markdown forms still deactivate; near-miss prompts remain inert.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/stable-transition-runtime.test.ts src/__tests__/runtime-fingerprint.test.ts src/__tests__/run-diagnostics.test.ts src/__tests__/provider-runtime-leases.test.ts src/__tests__/trusted-deactivation-fallback.test.ts
```

Expected: nonzero exit because the stable-generation deployer and diagnostics do not exist.

Run:

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh
```

Expected before test correction: exit zero with 43 passes, including the incorrect assertion that malformed/stale roots preserve PM on. Record this as the testing-theatre baseline, then replace that expectation with the availability matrix.

### Step 2: Implement stable-generation deployment

Create a deployer that copies the verified transition bundle and minimal native runtime closure into a temporary generation under `~/.claude/ironclaude-runtime/generations/<digest>`, hashes every file, writes a canonical manifest, executes a real module-load self-test, then atomically swaps `current`. Retain the current, last-known-good, and every actively leased generation.

Add a build step that derives the hook-embedded emergency closure from the canonical state-manager transaction contract and verifies its digest/parity. It must require exact direct-human provenance and an existing authenticated session, write the same epoch/mode/audit fields transactionally, preserve every other state surface, and remain unreachable from MCP, Commander, and agent paths.

`mcp-server-wrapper.js`, `session-init.sh`, Commander startup deployment, and `make deploy-hooks` must call the same deployer contract. Provider startup must deploy it; Commander restart must not be required. Tests redirect `HOME` to temporary roots.

### Step 3: Replace cache-path execution in `state-activator.sh`

Keep the exact prompt and direct-human provenance matcher byte-for-byte unless tests require a narrowly justified correction. Replace `find_mode_transition_hook` and `resolve_trusted_mode_client` deactivation authority with the stable manifest/current-generation resolver and exact session binding. Pass root hints only as diagnostics.

If repository observation fails, pass explicit uncertainty and continue. If stable-runtime resolution, verification, lease acquisition, load, or execution fails after direct-human and existing-session authentication, invoke the embedded emergency closure and audit degraded-runtime evidence. If the session is missing, fail without creating state.

### Step 4: Add diagnostics and Commander deployment parity

Extend runtime fingerprint/diagnostics with stable generation, manifest hash, transition bundle hash, and native runtime closure hash. Make Commander deploy the same atomic runtime rather than only `*.sh`. Remove newest-cache selection as executable authority while retaining best-effort shell-hook mirroring.

### Step 5: Prove GREEN and failure sensitivity

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npx vitest run src/__tests__/stable-transition-runtime.test.ts src/__tests__/runtime-fingerprint.test.ts src/__tests__/run-diagnostics.test.ts src/__tests__/provider-runtime-bindings.test.ts src/__tests__/provider-runtime-leases.test.ts src/__tests__/trusted-deactivation-fallback.test.ts src/__tests__/professional-mode-epochs.test.ts && npx tsc --noEmit
```

Expected: exit zero; every listed file appears in the terminal summary.

Run:

```bash
bash -n /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh && bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-deactivation.sh && bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-git-authority-activation.sh && bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-professional-mode-off-authority.sh && cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python -m pytest commander/tests/test_main_validate.py::TestDeployWorkerHooks commander/tests/test_deactivation_client_parity.py -q
```

Expected: exit zero. Current related baselines are 43 deactivation passes, 135 Git-authority passes, and 11 Commander/parity passes; corrected summaries may grow but must have zero failures. Inject a partial generation and a changed manifest after validation; each must fail before current-generation swap or mode mutation. Hold a lease while publishing and cleaning a successor generation; the leased generation remains usable. Break the fallback provenance and transaction guards separately; each negative test fails before restoration.

### Step 6: Build and stage Task 2

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build && npm run bundle:mode-transition-hook && npm run build:trusted-deactivation-fallback
```

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/mcp-servers/state-manager/cli/stable-transition-runtime.js worker/mcp-servers/state-manager/scripts/build-trusted-deactivation-fallback.mjs worker/mcp-servers/state-manager/src/__tests__/stable-transition-runtime.test.ts worker/mcp-servers/state-manager/cli/mcp-server-wrapper.js worker/mcp-servers/state-manager/package.json worker/mcp-servers/state-manager/src/runtime-fingerprint.ts worker/mcp-servers/state-manager/src/tools/read-tools.ts worker/mcp-servers/state-manager/src/__tests__/runtime-fingerprint.test.ts worker/mcp-servers/state-manager/src/__tests__/run-diagnostics.test.ts worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/state-manager/dist/mode-transition-hook.js && git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/dist/trusted-deactivation-fallback.js && git -C /Users/roberthyatt/Code/ironclaude add -- worker/hooks/state-activator.sh worker/hooks/session-init.sh worker/hooks/tests/test-professional-mode-deactivation.sh worker/hooks/tests/test-git-authority-activation.sh worker/hooks/tests/test-professional-mode-off-authority.sh Makefile commander/src/ironclaude/main.py commander/tests/test_main_validate.py && git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero; cached diff check passes and no Task 2 path remains unstaged.

---

## Task 3: Add provider-native acceptance, runtime oracle, and documentation

**Files:**

- Create: `commander/scripts/live_pm_deactivation_provider_response.py`
- Create: `commander/tests/test_live_pm_deactivation_provider_response.py`
- Create: `commander/scripts/build_pm_deactivation_runtime_oracle.py`
- Create: `commander/tests/test_pm_deactivation_runtime_oracle.py`
- Create: `commander/scripts/verify_installed_pm_deactivation_runtime.py`
- Create: `commander/tests/test_verify_installed_pm_deactivation_runtime.py`
- Create: `commander/scripts/reinstall_pm_deactivation_runtime.py`
- Create: `commander/tests/test_reinstall_pm_deactivation_runtime.py`
- Modify: `worker/skills/deactivate-professional-mode/SKILL.md`
- Modify: `commander/tests/test_deactivation_client_parity.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/plans/2026-07-20-v1-1-overall-roadmap.md`

### Step 1: Add RED provider-harness contracts

Create one fresh native session per fixture with isolated state DB, real Git repository, stable runtime generation, and installed-root fixture. The harness must independently inspect DB, stable manifest, tool trace, and Git state; provider JSON is not proof.

Fixtures:

- normal Claude install;
- normal Codex install;
- Codex cachebuster replacement with deleted prior root;
- Claude same-version byte replacement;
- legitimate symlink alias;
- restart and concurrent direct sessions;
- forged/cross-client root hint;
- subagent and automation events.
- stable-runtime resolution, verification, lease, load, and execution failures that must use the emergency closure;
- in-flight leased generation superseded while deactivation completes.

Claude uses Sonnet with a 540-second timeout; Codex uses a 180-second timeout. No fixture retries. The harness must not consume Claude usage during Task 3; unit-test its command construction and verifiers only.

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python -m pytest commander/tests/test_live_pm_deactivation_provider_response.py commander/tests/test_pm_deactivation_runtime_oracle.py commander/tests/test_verify_installed_pm_deactivation_runtime.py commander/tests/test_reinstall_pm_deactivation_runtime.py -q
```

Expected: nonzero exit before the harness exists, then RED assertions until all independent evidence checks are implemented.

### Step 2: Implement independent acceptance verification

Require each positive fixture to prove:

- exact session changed from on to off;
- exactly one closed epoch with exact client/session/runtime generation;
- stale-root diagnostics recorded where applicable;
- workflow, plan, tasks, receipts, index tree, working tree, refs, and assignments unchanged;
- no restart, Git mutation, state edit, path repair, or operator handback.

Negative fixtures must prove mode/epochs/audit unchanged. Reject missing tool trace, self-reported success, unexpected retries, duplicate non-idempotent transitions, or changed Git state.

Implement `build_pm_deactivation_runtime_oracle.py` with closed arguments. `--source-root` plus `--output` writes canonical expected source/plugin/runtime hashes; `--source-root` plus `--verify` independently recomputes and compares them. The schema includes both provider manifests, state-manager and mode-transition bundles, stable-generation manifest, transition bundle, native closure, fallback payload, and cachebuster. Empty, duplicate, missing, or post-install-derived fields fail. Seal the oracle's own SHA-256 in a separate custody file before installation.

Implement `verify_installed_pm_deactivation_runtime.py` to execute one real installed-provider deactivation fixture and independently emit an oracle-bound receipt containing installed root, manifest and bundle hashes, stable generation, session/client evidence or explicit client uncertainty, delivery ID, epoch, mode before/after, audit row, preservation hashes, command exit, and retry count. Reject stale, self-reported, duplicate, incomplete, or wrong-oracle evidence.

Implement `reinstall_pm_deactivation_runtime.py` as the sole release coordinator. Preflight credentials, disk, marketplace/source identity, known-good installed versions, restart-helper digest, oracle custody digest, and recovery inputs before any mutation. Execute Claude reinstall with exact stop/recovery rules, then Codex plugin add, verified app restart, readiness wait, and phase receipt. If the current Codex process cannot survive restart, launch this one coordinator externally and perform no subsequent command in the old process; the fresh task resumes from its receipt. Tests inject every phase failure and prove no second-provider mutation after a failed first-provider phase.

### Step 3: Update human-facing contracts

Document the stable deactivation bridge, emergency closure, generation leases, three-state reporting, cache replacement behavior, and unchanged human-only boundary. Add the operator's deferred backlog rule that an already-approved bounded action must not trigger a redundant permission request unless destination, sensitivity, destructive effect, or scope materially changes. Keep Wave 25 in progress; Task 4 will write a pre-install conditional completion gate tied to its A/B receipt.

### Step 4: Prove GREEN and stage Task 3

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python -m pytest commander/tests/test_live_pm_deactivation_provider_response.py commander/tests/test_pm_deactivation_runtime_oracle.py commander/tests/test_verify_installed_pm_deactivation_runtime.py commander/tests/test_reinstall_pm_deactivation_runtime.py commander/tests/test_deactivation_client_parity.py commander/tests/test_version_consistency.py -q
```

Expected: exit zero; every listed file appears in the summary.

Run all focused Task 1-3 commands again, stage the exact Task 3 paths, and require `git diff --cached --check` to exit zero.

---

## Task 4: Build final bytes, verify, release, and prove the fresh runtime

**Files:**

- Modify: `worker/.codex-plugin/plugin.json`
- Modify: `worker/mcp-servers/state-manager/dist/index.js`
- Modify: `worker/mcp-servers/state-manager/dist/mode-transition-hook.js`
- Modify: `worker/mcp-servers/state-manager/dist/trusted-deactivation-fallback.js`
- Modify: `docs/plans/2026-07-20-v1-1-overall-roadmap.md`

### Step 1: Declare the authoritative completion gate, apply one cachebuster, and rebuild

Update the Wave 25 roadmap row before installation: it remains in progress unless and until Task 4 receives a task-boundary A/B receipt over the exact final candidate. This conditional gate is the durable completion authority; no roadmap write follows installation.

Run:

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py /Users/roberthyatt/Code/ironclaude/worker && cd /Users/roberthyatt/Code/ironclaude/worker/mcp-servers/state-manager && npm run build && npm run bundle:mode-transition-hook && npm run verify:trusted-deactivation-fallback
```

Expected: exit zero. No source, documentation, cachebuster, or build mutation follows this step.

### Step 2: Validate final bytes and construct the runtime oracle

Run:

```bash
/usr/bin/python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py /Users/roberthyatt/Code/ironclaude/worker && cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python -m pytest commander/tests/test_version_consistency.py commander/tests/test_pm_deactivation_runtime_oracle.py commander/tests/test_verify_installed_pm_deactivation_runtime.py commander/tests/test_reinstall_pm_deactivation_runtime.py -q && commander/.venv/bin/python commander/scripts/build_pm_deactivation_runtime_oracle.py --source-root /Users/roberthyatt/Code/ironclaude --output /private/tmp/direct-human-pm-deactivation-expected-runtime.json && commander/.venv/bin/python commander/scripts/build_pm_deactivation_runtime_oracle.py --source-root /Users/roberthyatt/Code/ironclaude --verify /private/tmp/direct-human-pm-deactivation-expected-runtime.json
```

Expected: tests and both oracle operations exit zero; oracle contains nonempty final-byte hashes for every required source/plugin/runtime component.

### Step 3: Run complete verification against final bytes

Run:

```bash
make -C /Users/roberthyatt/Code/ironclaude test
```

Expected: terminal exit zero from hooks, complete state-manager, workspace-manager, and Commander suites. Capture the final exit; partial output is not success.

### Step 4: Run each fresh provider acceptance fixture once

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python commander/scripts/live_pm_deactivation_provider_response.py --client all --source-root /Users/roberthyatt/Code/ironclaude
```

Expected: every Claude and Codex positive/negative fixture passes once, with no retry and no operator handback. Stop on the first failed fixture and repair source before installation.

### Step 5: Stage the final release candidate

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- worker/.codex-plugin/plugin.json worker/mcp-servers/state-manager/dist/index.js worker/mcp-servers/state-manager/dist/mode-transition-hook.js && git -C /Users/roberthyatt/Code/ironclaude add -f -- worker/mcp-servers/state-manager/dist/trusted-deactivation-fallback.js && git -C /Users/roberthyatt/Code/ironclaude add -- docs/plans/2026-07-20-v1-1-overall-roadmap.md && git -C /Users/roberthyatt/Code/ironclaude diff --cached --check
```

Expected: exit zero and no unstaged Task 4 path remains.

### Step 6: Reinstall as the final source/plugin/runtime mutation

Run the coordinator preflight before any mutation:

```bash
cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python commander/scripts/reinstall_pm_deactivation_runtime.py --source-root /Users/roberthyatt/Code/ironclaude --restart-script /private/tmp/restart_codex.py --restart-script-sha256 d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f --oracle /private/tmp/direct-human-pm-deactivation-expected-runtime.json --preflight-only --receipt /private/tmp/wave25-install-preflight.json
```

Expected: exit zero; receipt proves package/source availability, known-good recovery identity, credentials, disk, restart-helper digest, and oracle custody before mutation.

Launch the external coordinator as the final mutation from the old Codex process, then execute no later command in that process:

```bash
/usr/bin/python3 -c 'import subprocess; subprocess.Popen(["/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python", "/Users/roberthyatt/Code/ironclaude/commander/scripts/reinstall_pm_deactivation_runtime.py", "--source-root", "/Users/roberthyatt/Code/ironclaude", "--restart-script", "/private/tmp/restart_codex.py", "--restart-script-sha256", "d09cc042bdac54f8fb87eb1a021724fe28b42b355690bdd97999fa2a8a16c38f", "--oracle", "/private/tmp/direct-human-pm-deactivation-expected-runtime.json", "--execute", "--timeout-seconds", "180", "--receipt", "/private/tmp/wave25-install-receipt.json"], start_new_session=True, stdout=open("/private/tmp/wave25-install.log", "ab"), stderr=subprocess.STDOUT)'
```

Expected: the coordinator performs Claude install/recovery handling, Codex plugin add, restart, readiness, and receipt phases in order. The fresh Codex task resumes only after restart; no old-process command races the installer. No source edit, documentation edit, build, cachebuster, reinstall, commit, or push follows.

### Step 7: Prove the loaded runtime and complete Task 4 review

In the fresh task, run:

```bash
cd /Users/roberthyatt/Code/ironclaude && commander/.venv/bin/python commander/scripts/verify_installed_pm_deactivation_runtime.py --client claude --oracle /private/tmp/direct-human-pm-deactivation-expected-runtime.json --install-receipt /private/tmp/wave25-install-receipt.json --receipt /private/tmp/wave25-claude-installed-proof.json && commander/.venv/bin/python commander/scripts/verify_installed_pm_deactivation_runtime.py --client codex --oracle /private/tmp/direct-human-pm-deactivation-expected-runtime.json --install-receipt /private/tmp/wave25-install-receipt.json --receipt /private/tmp/wave25-codex-installed-proof.json
```

Expected: both commands exit zero from independent installed-root, manifest, generation, bundle/native, session, mode/epoch/audit, preservation, and retry evidence. Submit Task 4 and run task-boundary review through the installed runtime. Only Grade A or B completes Wave 25.

### Step 8: Continue to Wave 26

After Wave 25 reaches `execution_complete`, the predeclared roadmap gate resolves to complete from its A/B receipt. Update the thread plan to mark Wave 25 complete and Wave 26 in progress. Start Wave 26 brainstorming immediately. Do not mutate repository files, commit, or push.
