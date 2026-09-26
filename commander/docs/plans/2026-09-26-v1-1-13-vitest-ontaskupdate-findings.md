# vitest `onTaskUpdate` RPC timeout — investigation findings

## Reproduction

Baseline (`vitest run --reporter=verbose`, unmodified `vitest.config.ts`, vitest 3.2.7) reproduced the error on the first attempt. Log: `vitest-baseline.log`.

Exact error, quoted from the log:

```
⎯⎯⎯⎯⎯⎯ Unhandled Errors ⎯⎯⎯⎯⎯⎯

Vitest caught 1 unhandled error during the test run.
This might cause false positive tests. Resolve unhandled errors to make sure your tests are not affected.

⎯⎯⎯⎯⎯⎯ Unhandled Error ⎯⎯⎯⎯⎯⎯⎯
Error: [vitest-worker]: Timeout calling "onTaskUpdate"
 ❯ Object.onTimeoutError node_modules/vitest/dist/chunks/rpc.-pEldfrD.js:53:10
 ❯ Timeout._onTimeout node_modules/vitest/dist/chunks/index.B521nVV-.js:59:62
 ❯ listOnTimeout node:internal/timers:605:17
 ❯ processTimers node:internal/timers:541:7
```

with the run summary:

```
 Test Files  12 passed (12)
      Tests  489 passed | 1 skipped (490)
     Errors  1 error
   Start at  10:40:05
   Duration  139.99s (transform 335ms, setup 0ms, collect 873ms, tests 242.50s, environment 1ms, prepare 374ms)
```

All 490 tests pass; the process still exits non-zero (`exit=1`) solely because of the one unhandled RPC error.

## Implicated test file(s)

None can be pinned down. Vitest reports unhandled RPC errors in an end-of-run "Unhandled Errors" rollup, detached from the chronological point where the timeout actually fired — the log line immediately preceding the report is the last test executed by whichever fork finished last (`src/__tests__/plan-scope.test.ts`, all sub-millisecond), which is almost certainly coincidental, not causal.

The suite's slowest individual tests (all well under the 30s `testTimeout`, spread across two fork workers over ~242s of cumulative test time) are concentrated in the git-heavy suites, consistent with the plan's hypothesis:

- `integration-core.test.ts`: several tests 3–5.3s (e.g. "revalidates exact integration-lock ownership before mutation and after fast-forward before recording" 4001ms, "preserves work on rebase conflict, target movement, primary ownership, and held integration lock" 5292ms, "reconciles a crash only from durable integration reachability and keeps integrated-local state after push failure" 4265ms)
- `git-authority.test.ts`: several tests 1–3.2s
- `git-content-merged.test.ts`: one test 2978ms

These are all synchronous `spawnSync` git call sequences (per the codebase's git-authority/finalization-coordinator design), matching the plan's hypothesis about the mechanism, but none individually is long enough to directly explain a 60s RPC timeout, so causation is not established.

## Root cause (UNVERIFIED except where noted)

**Verified by source inspection:** the "Timeout calling onTaskUpdate" error comes from `birpc`'s bundled RPC layer inside vitest (`node_modules/vitest/dist/chunks/index.B521nVV-.js`), which defines:

```js
const DEFAULT_TIMEOUT = 6e4; // 60000ms
...
const { post, on, off, eventNames, serialize, deserialize, resolver, bind, timeout = DEFAULT_TIMEOUT } = options;
```

For the `forks` pool, the RPC options are built by `createForksRpcOptions()` (`node_modules/vitest/dist/chunks/utils.CAioKnHs.js`), which supplies `serialize`, `deserialize`, `post`, and `on`, but **no `timeout` field**. `onTaskUpdate` is a real RPC call (not one of the fire-and-forget `eventNames`: `onUserConsoleLog`, `onCollected`, `onCancel`), so it is subject to the fixed 60-second `DEFAULT_TIMEOUT`.

Confirmed there is no exposed vitest config knob for this: `rg -n "rpcTimeout|RPC_TIMEOUT" node_modules/vitest/dist` and `rg -n "rpc" node_modules/vitest/dist/node.d.ts node_modules/vitest/dist/config.d.ts` both return nothing. `testTimeout` (per-test timeout) is a separate, unrelated setting and does not reach this code path.

**UNVERIFIED (best-supported hypothesis, mechanism only):** with `pool: 'forks'` and up to two concurrent fork workers each running long sequences of synchronous `spawnSync` git subprocesses, the worker process's event loop is blocked for extended stretches. The `onTaskUpdate` call is sent from a worker to the main process over `child_process` IPC and awaits an acknowledgment; if the combination of (a) the sending worker's own event-loop blocking and (b) main-process/IPC-channel backpressure across two busy forks pushes the round trip past the fixed 60s window, the call times out. This is plausible given the observed test durations and pool shape, but the exact triggering call was not isolated (see previous section) and no experiment in this task proved the mechanism directly (only that the error is real and that the RPC timeout is hardcoded).

## Change made: none

Two config-only experiments were tried and both failed to clear the error, so `vitest.config.ts` is unchanged from its original content:

1. **`poolOptions.forks.singleFork: true`** (replacing `maxForks`/`minForks`) — collapses to a single fork, eliminating any cross-fork IPC contention. Result: the error still occurred, and additionally the run **aborted after the first test file** (`integration-core.test.ts` only: `Test Files 1 passed (12)`, `Tests 96 passed (96)`, exit 1) instead of completing all 12 files — worse than baseline. Log: `vitest-experiment-singlefork.log`.
2. **`fileParallelism: false`** (kept `maxForks: 2`/`minForks: 1`) — forces files to run one at a time. Result: all 12 files completed with the same pass counts as baseline (`Test Files 12 passed (12)`, `Tests 489 passed | 1 skipped (490)`), but the `onTaskUpdate` timeout still occurred. Log: `vitest-experiment-fileparallelism.log`.

No other documented vitest option was found (via source and typings inspection) that changes the birpc call timeout for the `forks` pool. Per the task's instructions, since no config-only change removes the error, `vitest.config.ts` was reverted to its original content (`git diff` on the file is empty) and no further verification runs were performed.

## Run summaries

| Run | Log | Config change | `Test Files` | `Tests` | `onTaskUpdate` error present |
|---|---|---|---|---|---|
| Baseline | `vitest-baseline.log` | none | `12 passed (12)` | `489 passed \| 1 skipped (490)` | yes |
| Experiment 1 | `vitest-experiment-singlefork.log` | `poolOptions.forks.singleFork: true` | `1 passed (12)` (run aborted early) | `96 passed (96)` | yes |
| Experiment 2 | `vitest-experiment-fileparallelism.log` | `fileParallelism: false` | `12 passed (12)` | `489 passed \| 1 skipped (490)` | yes |

All logs are in the scratchpad directory: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/`.

## Recommendation for future work

Since no config-only fix exists, resolving this (if ever prioritized) would require either: reducing the number of long synchronous `spawnSync` sequences per test/worker (e.g. batching or using async `spawn` in the git-authority/finalization-coordinator test helpers), or filing/tracking a vitest issue requesting a configurable RPC timeout for the `forks`/`threads` pools. Neither is in scope for this task.

## Bisect (remainder loop)

Each of the 12 test files was run standalone (no other file in the same vitest invocation) three times, to check whether any single file reproduces the `onTaskUpdate` timeout on its own, independent of running the full 12-file suite together. Logs: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/bisect/`.

### Results table

| File | Run 1 exit | Run 2 exit | Run 3 exit | Error present |
|---|---|---|---|---|
| `integration-core` | 1 | 1 | 1 | **yes (3/3)** |
| `workspace-service` | 0 | 0 | 0 | no |
| `integration-recovery` | 0 | 0 | 0 | no |
| `git-authority` | 0 | 0 | 0 | no |
| `git-content-merged` | 0 | 0 | 0 | no |
| `cli` | 0 | 0 | 0 | no |
| `db` | 0 | 0 | 0 | no |
| `git` | 0 | 0 | 0 | no |
| `parent-death-exit` | 0 | 0 | 0 | no |
| `plan-scope` | 0 | 0 | 0 | no |
| `scoped-tree` | 0 | 0 | 0 | no |
| `tool-dispatch` | 0 | 0 | 0 | no |

All 36 exit lines were produced (3 + 6 + 27), matching the plan's expectation. `rg -l -F 'Timeout calling "onTaskUpdate"'` over the bisect directory returned exactly three logs, all `integration-core`: `integration-core-run1.log`, `integration-core-run2.log`, `integration-core-run3.log`. No other file's log contains the string, in any run.

### Reproducing runs — quoted summary lines

`integration-core-run1.log`:
```
 Test Files  1 passed (1)
      Tests  96 passed (96)
     Errors  1 error
   Duration  101.95s (transform 139ms, setup 0ms, collect 215ms, tests 101.58s, environment 0ms, prepare 34ms)
```

`integration-core-run2.log`:
```
 Test Files  1 passed (1)
      Tests  96 passed (96)
     Errors  1 error
   Duration  111.08s (transform 168ms, setup 0ms, collect 253ms, tests 110.66s, environment 0ms, prepare 37ms)
```

`integration-core-run3.log`:
```
 Test Files  1 passed (1)
      Tests  96 passed (96)
     Errors  1 error
   Duration  100.08s (transform 141ms, setup 0ms, collect 214ms, tests 99.71s, environment 0ms, prepare 35ms)
```

All three runs show the identical shape: every test in the file passes (96/96), the file's own `Duration` is ~100–111s (well past the 60s birpc `DEFAULT_TIMEOUT` documented in the Root Cause section above), and vitest reports exactly one unhandled `Error: [vitest-worker]: Timeout calling "onTaskUpdate"` from `node_modules/vitest/dist/chunks/rpc.-pEldfrD.js:53:10` after the test run completes. No other test file's standalone run exceeded, or came close to, 60s.

### Narrowing (Step 3)

Exactly one file (`integration-core`) reproduced, so its two top-level `describe` blocks (from `rg -n "^describe\(" src/__tests__/integration-core.test.ts`: `finalizePrimaryUnassignedCommit` at line 35, `finalizePrimaryUnassignedPush` at line 124) were run individually with `-t`, one log each (2 of the 10-run narrowing budget used):

| Filter | Log | Exit | Tests | Duration | Error present |
|---|---|---|---|---|---|
| `finalizePrimaryUnassignedCommit` | `integration-core-t1.log` | 0 | `3 passed \| 93 skipped (96)` | 2.07s | no |
| `finalizePrimaryUnassignedPush` | `integration-core-t2.log` | 0 | `11 passed \| 85 skipped (96)` | 8.77s | no |

Neither describe block reproduces the timeout in isolation. Both finish in single-digit seconds, far under the 60s window.

**Coverage caveat (orchestrator correction):** these two `-t` filters cover only **14 of the file's 96 tests** (3 + 11). The remaining 82 tests sit outside the two top-level `describe` blocks; they are presumably generated or top-level cases, not verified. Those 82 were never run separately, so this narrowing does **not** show that the timeout is independent of any specific test. It shows only that the two named `describe` blocks alone do not trigger it.

### Conclusion

- **VERIFIED — culprit file:** `integration-core.test.ts`. It reproduces the error 3/3 in complete isolation, with 96/96 tests passing and a whole-file duration of about 100–111s. None of the other 11 files reproduced it in any of their 33 isolated runs. The trigger is therefore contained in this one file and does not require a cross-file interaction.
- **UNVERIFIED — mechanism:** "the file's cumulative runtime crosses the 60s window" is a hypothesis, not a finding. birpc's `DEFAULT_TIMEOUT` applies per RPC call, not per file. A single `onTaskUpdate` call must go more than 60s without an acknowledgment, so total file duration alone does not explain it. Two candidate causes remain, and this bisect does not separate them:
  - one or more of the 82 unnarrowed tests blocks the worker's event loop, or starves IPC, for over 60s in aggregate between task updates;
  - a slower accumulation effect.

**Proposed scope for the follow-up fix loop:**
1. Finish the narrowing: bisect the 82 tests outside the two `describe` blocks, for example by line ranges with `-t` or by temporarily splitting the file in a scratch copy, to find the stall.
2. Fix the root cause in the test code or its helpers. Likely candidates are long synchronous `spawnSync` git sequences that block the event loop.
3. Restore a strict exit-0 vitest gate.

Config-only fixes are already ruled out.
