# Codex work validation report (2026-10-06)

**Scope.** Everything Codex did while Claude was unavailable:
- the three committed loops in `55ba8b3..a3d400b`: hook corrections, boundary safeguards and GBTW evaluation warnings;
- the staged, uncommitted Claude↔Codex worker provider handoff, about 9.7k lines.

**Sources.**
- Check results: `docs/reviews/2026-10-06-codex-work-check-results.md`.
- Independent Fable review: `docs/reviews/2026-10-06-codex-work-fable-review.md`. Fable reviewed blind to Codex's own review verdicts.
- Orchestrator verification. Every Critical and Important finding was re-checked against current source by a separate Opus verifier, with scratch reproductions for C1 and I1. The orchestrator also re-ran the two failing hook suites and checked local native logs itself.

No product code was changed by this loop.

## Verdicts

| Loop | Verdict | Summary |
|---|---|---|
| Hook corrections (committed) | HAS-FINDINGS (test only) | The behaviour is correct. Two tests depend on `rg`, which is not installed on this machine outside ChatGPT.app, so the suite fails here. |
| Boundary safeguards (committed) | CLEAN | Fable verified R9/R10. All suites pass. |
| GBTW evaluation warnings (committed) | HAS-FINDINGS (test only) | The behaviour is correct. The same `rg` test dependency affects it. |
| Worker provider handoff (staged) | HAS-FINDINGS | 2 Critical, 6 Important, 5 failing workspace-manager tests. **Do not commit or deploy as-is.** |

## Check summary (from Task 1)

| Check | Result |
|---|---|
| Hook suites (47, run individually) | 45 pass, 2 fail: `test-gbtw-continuation-judge.sh` (77/184) and `test-last-assistant-text.sh` (6/7) |
| workspace-manager vitest, full | Exit 1: 5 failed / 524 passed / 1 skipped; 1 error (the known `onTaskUpdate` RPC timeout) |
| Focused handoff/CLI vitest | Exit 0, 60/60 |
| `tsc --noEmit` | Exit 0 |
| Handoff pytest selection | Exit 0, 331 passed |
| Full Commander pytest | Exit 1: 1 failed / 3760 passed (`test_executing_plans_skill.py::test_execution_complete_restage_command_stages_from_non_root_cwd`) |

Every suite ran against the working tree, which contains the staged handoff source. The dist bundles were not rebuilt.

## Verified findings

### Committed loops

**T1 (Important, test portability): committed hook tests require `rg`.**
- **Where:** `worker/hooks/tests/test-gbtw-continuation-judge.sh` and `test-last-assistant-text.sh` (line 71) call `rg`.
- **Why they fail here:** neither `bash` nor `/opt/homebrew/bin` has `rg`. The only binaries are inside `/Applications/ChatGPT.app` and `~/.codex/plugins/...`, so the suites pass only inside Codex's environment.
- **Verified:** with Codex's `rg` prepended to `PATH`, the orchestrator re-ran both suites. They passed 184/184 and 7/7, so there is no behaviour defect.
- **Effect:** `make test-hooks` is red for Claude sessions and for any normal shell, which breaks the "one IronClaude" test parity.
- **Fix:** use `grep -qF` and `grep -q`.

**M3 (Minor, from Fable): unreadable `allowed_files` ancestry is handled inconsistently.**
- The drift hook fails open on it, while scoped-tree commits fail closed.
- The committed plan kept this behaviour on purpose. Recorded only.

**Full-suite pytest failure: not attributed to a Codex loop by this validation.**
- **The test:** `test_execution_complete_restage_command_stages_from_non_root_cwd`. It fails with an IndentationError in a python3 here-document taken from the executing-plans skill text.
- **Why it can't be placed:** the `executing-plans/SKILL.md` file changed in this range, but this loop did not bisect the failure.
- **Next step:** investigate in the fix loop below.

### Staged worker handoff

**C1 (Critical): ambiguous native-log evidence suppresses ordinary worker lifecycle permanently.**
- **Cause:** `worker_quota.read_native_quota` returns `unknown` without advancing the cursor (`worker_quota.py:262-269`, `:446-447`, compared with `:505`) for several cases:
  - an oversize line (>256 KiB);
  - a line with no trailing newline;
  - malformed JSON;
  - an unrelated Codex scope;
  - a missing log;
  - a remote (`machine`) worker.
- **Effect:** `_native_handoff_gate` returns True on `unknown` (`main.py:1817-1840`). `check_workers` (`main.py:4823`) and `_confirm_and_kill_stuck_worker` (`:4498`) then skip `.done`, dead-session, idle and check-in handling for that worker indefinitely.
- **Reproduced by the verifier:**
  - a 300 KiB line gives `unknown incomplete_native_record cursor=0`, and the result repeats on every read;
  - a missing file gives `unknown unreadable_native_log`.
- **How common:** Fable counted 468 Claude and 267 Codex local logs containing a line over 256 KiB.
- **Wider than Fable said:** every remote worker is affected.
- **Requirement:** violates R8.
- **The tests lock this behaviour in:** `test_worker_handoff_daemon.py` asserts the gate is True.

**C2 (Critical, narrower than stated): some paused handoffs can never return to `running`.**
- **What Fable overstated:** the `opposite_capability_unavailable` reason at phase `claimed` is retried every cycle (`worker_handoff.py:390-445`).
- **What is confirmed:** retries only resume forward.
- **Pauses that strand the worker:**
  - **Permanent reasons:** `native_quota_evidence_changed` (the source quota resets, which is the normal case after a 5-hour window), `uncertain_external_effect`, `handoff_effect_or_proof_failed`, and any pause after phase `claimed`.
  - **Where it ends up:** the worker is left in `handoff_pending`, and the workspace hold stays in phase `begun`.
  - **What gets blocked:** kill, commit, send, finalize and sync.
  - **Why nothing undoes it:**
    - `worker_registry.py:598` is the only write from `handoff_pending` back to `running`, and only a completed handoff reaches it.
    - `handoff.ts` only moves forward.
    - No MCP tool or CLI command offers abort or release.
- **Recovery:** requires hand-editing two SQLite databases.
- **Requirements:** violates R1, R6 and R8.
- **The tests lock this behaviour in:** `test_worker_handoff.py:547-552`.

**I1 (Important): the Claude five-hour limit is never recognised.**
- **Cause:** `CLAUDE_RATE_LIMIT_TYPES = frozenset({"seven_day"})` (`worker_quota.py:17`, used at `:62`).
- **The design does not ask for this:** it says "known nonempty rateLimitType/reset" (`2026-10-05-worker-provider-handoff-design.md:50`).
- **Real occurrences:** the orchestrator counted local Claude session logs. 11 contain `"rateLimitType":"five_hour"` and 12 contain `seven_day`.
- **Reproduced:** a `five_hour` record yields `unknown`, which then triggers C1.

**I2 (Important, needs a crash or restart mid-chain): bootstrap process leases are in-memory per controller.**
- **Where:** `worker_handoff_process.py:26`, `:51`, `:111`. A controller is built fresh for each call (`orchestrator_mcp.py:857-867`).
- **Why the normal path works:** a single call carries `target_ready` through owner transfer, so the lease survives.
- **When it fails:** after a daemon restart between `target_ready` and `owner_transferred`, the next controller has no lease and `assert_quiescent` raises.
- **Result:** the error is mapped to the non-retryable `handoff_effect_or_proof_failed`, after the source has already been stopped. The worker then ends up stranded as in C2.

**I3 (Important): a dead-owner `effects_started` worker operation is never reconciled.**
- **The gap:** only `claimed` rows are reaped (`worker_registry.py:262-265`).
- **When it happens:** the daemon marks `effects_started` at the start of every per-worker body (`main.py:4831`). A hard crash (SIGKILL, OOM or a supervisor kill) inside that body blocks every later operation on the worker with "unreconciled worker operation blocks handoff".
- **No recovery:** there is no startup sweep and no operator tool.

**I4 (Important): usage quarantine cannot be cleared once a client has no running worker.**
- **Where:** `recover_usage_quarantine` (`provider_state.py:276-313`). Its only caller is `main.py:1851`, which never passes `operator_authorized`. That caller runs only for a running worker of the quarantined client.
- **Effect:** after the last Claude worker hands off to Codex, Claude is excluded from worker spawns on that host permanently.

**I5 (Important, transient alone): Brain worker operations collide with the daemon's lease.**
- **Where:** the orchestrator MCP is a separate process with its own owner token (`orchestrator_mcp.py:8218`, `worker_registry.py:22`). Eleven wrapped methods (`:7317-7326`) fail immediately with the misleading "unreconciled worker operation blocks handoff" during the daemon's per-worker window. They neither wait nor retry.
- **Severity:** permanent only together with I3.

**W1 (Important, found by the orchestrator, verified): workspace mutation claims are never reclaimed after a crash.**
- **Where:** `withAssignmentMutation` (`handoff.ts:90-96`) inserts a `workspace_mutations` row and deletes it only in `finally`.
- **Nothing reads the claim's owner details:** `process_identity` and `acquired_at` are never read.
- **No cleanup anywhere:** neither the TS side nor Commander (`workspace_handoff_authority.py:65-67`) nor any startup path clears stale rows.
- **Effect:** a process crash mid-finalization or mid-sync permanently blocks every later finalize, sync, cleanup and reap on that assignment ("Assignment mutation is already claimed"), and also blocks handoff begin and grant. This conflicts with the standing rule that worktree management never needs operator intervention.
- **The tests lock this behaviour in:** `handoff.test.ts:72` asserts that a dead-process row blocks, and nothing reclaims it.

**V1 (Important): the staged handoff leaves the full workspace-manager suite red.**
- **What breaks:**
  - Migration 7 breaks 3 `db.test.ts` migration tests, which expect `[1..6]`.
  - The 4 new internal CLI commands break the 13-command list in `tool-dispatch.test.ts`. The new names do not include any forbidden command.
  - The `withAssignmentMutation` wrapper on `finalizeCommanderLocalCommit` opens a DB transaction before validating the input. The forged-authority test now gets `db.transaction is not a function` instead of "Commander finalization input is malformed".
- **How Codex missed this:** its required selection ran only `handoff.test.ts` and `cli.test.ts`.
- **What this means:**
  - The first two are stale tests.
  - The third is a real ordering change: malformed private-CLI input is now rejected only after a mutation claim, which is also exposed to W1.

**Minor (from Fable):**
- M1: the successor's `TRON_WORKER_ID` export does nothing (behaviour unchanged).
- M2: `transferHandoff` marks source intents *consumed* rather than revoked (`handoff.ts:78`).

**Test-quality note (from Fable, not separately verified):**
- Most `test_worker_handoff.py` controller tests use `process=MagicMock()`. Their process-stop proof checks cannot fail.
- The process adapter stub marks a process gone on the first signal, so the SIGKILL escalation and wait loops are untested.

Fable's 8 observations stay unverified and are not counted as findings. Examples:
- macOS `open_files()` AccessDenied;
- full-dict snapshot equality;
- the synchronous handoff blocking the slow lane;
- Codex bootstrap MCP loading.

## Recommendations

| Loop | Recommendation |
|---|---|
| Hook corrections | **Keep** (already committed). Small fix loop **L-T1**: replace `rg` with `grep` in the two tests. Fold this into the next hook-touching loop or run it alone. |
| Boundary safeguards | **Keep.** No action. |
| GBTW warnings | **Keep.** Covered by L-T1. |
| Full-pytest restage-command failure | **Fix loop L-T2:** debug and fix the IndentationError in the executing-plans restage snippet. |
| Worker handoff (staged) | **Do not commit yet.** Run fix loop **L-H1 "handoff recovery and evidence fixes"**, covering C1, C2, I1–I5, W1 and V1, then run a fresh final Fable review before committing. Key design questions for the operator: (a) should `unknown` evidence fall back to ordinary lifecycle after a bound, or after skipping the bad record? (b) what is the explicit abort/release primitive, and is it an operator tool, automatic after a quota reset, or both? (c) should stale-lease reaping key on dead-owner proof (pid + create_time) for both `worker_operations` and `workspace_mutations`? |
| Backlog | M1, M2, the MagicMock-heavy controller tests, and Fable's observations. |

## Deploy prerequisites

1. **Do not restart Commander while the staged handoff source is in the working tree.**
   - **Why:** `commander/` runs from source, so a restart activates the `_native_handoff_gate` (C1/I1) for every managed worker immediately.
   - **Why the bundles don't make it safe:** the workspace bundles lack the handoff commands, so on a real exhaustion the handoff fails closed. C1 still applies regardless.
2. Before any deploy of the handoff:
   - rebuild the workspace-manager dist bundles (`dist/cli.js` and `dist/index.js` currently have 0 occurrences of `begin-handoff`);
   - reinstall both plugin caches;
   - restart.
3. **Refresh the Claude plugin cache now.** It is behind `59b5ca7`/`a3d400b` (scoped-tree, workspace dist, three skills), independent of the handoff.
