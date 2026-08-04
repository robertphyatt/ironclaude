# Codex Commander Runtime Parity Fix Findings

> **Recorded:** 2026-08-03
> **PM session:** `019fc109-7175-7b72-8925-f21d9347e22a`
> **Result:** Runtime parity proved; implementation staged; no commit or push

## Outcome

Commander now preserves the existing Codex Brain approval workflow and successfully starts a Codex
Sol worker after both native Codex startup gates. The live disposable worker registered as client
`codex`, model `gpt-5.6-sol`, returned the exact requested marker, and was stopped and cleaned up.
Completed directive #1456, its historical Claude worker, the Pathfinder checkout, and the two
protected user-modified files were unchanged.

Slack pin policy is bounded and exact:

- with fewer than 100 pins, add the new pin without removing any pin;
- if the target is already pinned, return success without removing or adding;
- at exactly 100 pins, remove only the oldest existing pin by Slack pin-record `created`, then add
  the new pin;
- above 100 pins, an already-pinned target remains an idempotent success; an unpinned target fails
  closed without removing or adding;
- fail closed on an invalid response shape, malformed full-capacity inventory, overcapacity, or
  removal failure when a new pin would be added. Below capacity, unselected inventory entries are
  not validated because no eviction decision depends on them.

## RED and GREEN evidence

The following are already-executed RED results from this PM session, not reconstructed failures:

- `TestOrchestratorMcpWiring::test_app_server_argv_registers_production_orchestrator` failed against
  the former two-name MCP allowlist.
- `test_codex_login_status_stderr_proves_chatgpt_authentication` failed because a successful
  stderr-only `Logged in using ChatGPT` result was classified as unauthenticated.
- Initial Slack capacity coverage produced `11 failed, 11 passed`: production did not inventory pins
  or remove the oldest pin. Two additional invalid-inventory cases then produced `2 failed, 3 passed`
  before the fail-closed shape guard was added.
- The captured ANSI/cursor-shaped trust regression failed before compact Codex-only marker matching.
- `test_wait_for_ready_codex_trust_hooks_and_marker` then failed because Commander did not select the
  distinct Hooks review option.
- The live v0.146 sequence exposed that normal `send_keys("2")` appended Enter and reopened the Hooks
  dashboard; the amended regression rejected that behavior before the raw-key correction.

GREEN evidence:

- Original MCP/auth focused suite: `22 passed`; its wave full suite: `2471 passed`.
- Final focused parity suite: `37 passed`.
- Exact hooks-gate control set: `6 passed`.
- Mutation check: removing the mandatory post-raw-key `continue` caused the main regression to fail
  with read count 3 instead of 5; restoring it returned the suite to GREEN.
- Full Commander suite before live proof: `2487 passed in 1225.20s`.
- Final full Commander suite after live proof: `2487 passed in 1241.77s`.
- Exact-boundary amendment RED: `1 failed, 1 passed`; the above-capacity case incorrectly added the
  pin before production was changed.
- Exact-boundary amendment GREEN: both new boundary tests passed, then the complete reaction test
  class passed with `26 passed`.
- Final full Commander suite after the boundary amendment: `2489 passed in 1259.80s`.
- Already-pinned overcapacity intersection: exact node `1 passed`; complete Slack reaction class
  `27 passed`; final full Commander suite `2490 passed in 1330.88s`.
- Testing-theatre review caught that the malformed full-capacity fixture initially made the
  malformed record oldest. Moving it behind a valid oldest record proved validation covers every
  eviction candidate, not only the selected minimum. Blind task review then passed Stage 1 with
  Grade A and no findings.
- `git diff --check` is clean.

## Live Commander and provider proof

Fresh process tree after the normal Commander restart:

- Commander daemon PID: `66653`
- Codex Brain app-server PID: `20816`
- Orchestrator MCP PID: `20952`

The first CLI restart attempt ran inside the filesystem sandbox, where its internal read-only `ps`
identity check was denied and therefore returned `Daemon PID 66653 no longer belongs to
ironclaude`. A narrow host process census proved PID `66653` was the expected
`python -m ironclaude.main` child of the existing `make run` process. Re-running the same guarded
CLI restart with process-table permission succeeded; Commander exec-restarted in place and logged
complete shutdown, orphan cleanup, fresh Slack Socket Mode, Brain SDK startup, and main-loop startup.

Live-source provenance is direct: `lsof` reported PID `66653` cwd as
`/Users/roberthyatt/Code/ironclaude/commander`; that daemon runs
`commander/.venv/bin/python -m ironclaude.main`; the same interpreter resolves
`ironclaude.slack_interface` to
`/Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/slack_interface.py`; and the worktree
file and staged index both had Git blob ID `575771778bc5322864a77ff0b7f0261964a45be1`. Thus the
exec-restarted process loaded the exact staged pin-boundary implementation.

Task 2 performed only the guarded daemon restart and read-only process, log, database, Git, and hash
checks before updating this findings artifact. It made no Slack API or browser call, so it performed
zero pin additions or removals and did not retest the live pin inventory. The worker database had
zero rows spawned at or after the restart cutoff `2026-08-03 19:55:12` UTC, and the post-restart
daemon log contained no pin-operation or worker-spawn event.

Exactly one of each process was present. The Codex app-server passed these seven environment variable
names to the orchestrator MCP, with no secret values placed in process arguments or findings output:

- `OPERATOR_NAME`
- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID`
- `SLACK_OPERATOR_USER_ID`
- `SLACK_USER_TOKEN`
- `SUPABASE_ANON_KEY`
- `SUPABASE_URL`

Robert's signed-in Slack command selected the worker provider as `codex`. The resulting provider state
had zero unavailable Codex-worker capability rows. The live Opus semantic-tier probe reported
`authenticated=true`, `available=true`, no reason, and model `gpt-5.6-sol`.

## Approval card, pin capacity, and linked retrieval

Linked-message retrieval succeeded through server `orchestrator`, tool
`get_messages_by_ts_range`, with `is_error=false`, result count 1, and timestamp
`1785666821.572739`. Slack message content and credentials are intentionally omitted.

Directive #1459 supplied the bounded capacity proof:

- review timestamp: `1785773771.071139`;
- inventory before adding the review: 100 fully locatable pins;
- oldest existing pin: type `message`, locator `1781552932.481639`,
  `created=1781552937.0`;
- Commander removed that oldest existing pin first and then added the new review as the 100th pin;
- new review pin record: type `message`, locator `1785773771.071139`,
  `created=1785773772.0`;
- Robert rejected the new review through the signed-in Slack UI; he did not delete any existing pin;
- the rejected review card was then absent and the resulting channel inventory contained 99 pins;
- directive #1459 status was `rejected`, with zero matching workers and zero post-cutoff dispatches.

Unit regressions separately prove minimum-`created` selection across message, file, and file-comment
pins, remove-before-add call order, below-capacity no-removal behavior, already-pinned idempotence, and
fail-closed malformed full-capacity, overcapacity, and removal behavior. A final blind review found
and repaired an earlier `>= 100` implementation mismatch so eviction now occurs only at exactly 100.
A later blind evidence review identified the pinned-plus-overcapacity intersection as undocumented.
The clarified contract and dedicated regression now prove an already-pinned target remains
idempotent at 101 items, while the separate unpinned 101-item regression fails closed. Production
behavior did not change, and no additional live Slack operation was performed.

## Disposable Codex worker proof and cleanup

The first probe identity was deduplicated by Brain against an earlier failed request and created no
worker. Its only injected path was the expected template-identical `AGENTS.md`; that file and its empty
temporary directory were safely removed. The fresh retry identity prevented reuse of that attempt.

Fresh worker record:

- worker ID: `codex-parity-probe-019fc109-r2`
- compatibility type: `claude-opus`
- client: `codex`
- model: `gpt-5.6-sol`
- native session ID: `019fc8eb-c2e5-7d50-9da2-489daa0987a9`
- tmux session: `ic-codex-parity-probe-019fc109-r2`
- exact reply: `IC_CODEX_PARITY_READY_019FC109_R2`

The captured worker log showed directory trust, the numbered Hooks review, successful raw option `2`,
the later Codex welcome marker, and the exact reply. Commander `kill_worker` reconciled the registry
row to `completed`; the tmux session is absent. The probe directory contained only injected
`AGENTS.md`, whose SHA-256 matched the standard template:
`44aa1b5e51622b421587e05a28fdc6176d9111aeeb7b61686312c78f2b07c0ca`.
That file was unlinked only after equality was proved, and the empty probe directory was removed.
No unexpected path was found or deleted.

## Preserved historical and repository state

Directive #1456 remained:

- status: `completed`
- source timestamp: `1785732204.591109`
- updated at: `2026-08-03 05:37:20`

Its historical worker remained:

- ID: `pf2e-corpus-rules-approach`
- compatibility type: `claude-opus`
- status/client/model: `completed` / `claude` / `opus`
- native session ID: `6f537255-5080-43c0-a40d-57e6dbd51862`
- spawned/finished: `2026-08-03 05:01:50` / `2026-08-03 05:26:41`

No post-cutoff Pathfinder-targeted worker spawn occurred. The five Pathfinder snapshot fields matched
exactly before and after the disposable probe:

- tracked/nonignored path manifest: `079341a795adccc2cb18b2e0b105b7b825b0ad9fd23615b3e9665c6351d65da4`
- worktree status: `0390c994273d78466072fe66b0826e20f36e9e05855559f3fd21d337d9c4792d`
- unstaged binary diff: `f20e8a0aeb3230b7e383bad9f270731bedf7cac6882807aa458a9556bd9112de`
- staged binary diff: `33d541a6125812e2274f3211d02384796b69013c2afd4c8995aeb4a69a6b5a65`
- path count: `126498`

Protected user files remained unstaged and byte-identical:

- `AGENTS.md`: `13161859a034afc49c04940ed8424eb52710a3125aaccb21e556d1ca62ea4eb5`
- `commander/config/ironclaude.json`:
  `bac970ca9d7d26bc6c11ba2d3225b0d7a237fb2bf65c22ec49af2f9e97dee5c8`

## Final staged paths

The intended final index contains exactly these 14 paths:

- `README.md`
- `commander/src/ironclaude/codex_brain_client.py`
- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/src/ironclaude/provider_capabilities.py`
- `commander/src/ironclaude/slack_interface.py`
- `commander/tests/test_codex_brain_client.py`
- `commander/tests/test_provider_capabilities.py`
- `commander/tests/test_slack_interface.py`
- `commander/tests/test_worker_adapter.py`
- `docs/plans/2026-08-02-codex-commander-runtime-parity-fix-design.md`
- `docs/plans/2026-08-02-codex-commander-runtime-parity-fix-requirements.md`
- `docs/plans/2026-08-02-codex-commander-runtime-parity-fix.md`
- `docs/plans/2026-08-02-codex-commander-runtime-parity-fix.plan.json`
- `docs/plans/2026-08-03-codex-commander-runtime-parity-fix-findings.md`

No commit was created. Nothing was pushed.
