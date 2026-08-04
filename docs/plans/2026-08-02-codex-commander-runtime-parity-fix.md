# Codex Commander Runtime Parity Fix Plan

> **Created:** 2026-08-02
> **Revised:** 2026-08-03 after live pin-capacity and worker-startup evidence
> **Scope:** Four minimal parity fixes; no generalized infrastructure

## Objective

Retain the already-verified Slack MCP environment and stderr-auth fixes, add the operator-required
oldest-pin eviction at Slack's 100-pin limit, repair the live Codex trust/readiness seam, then prove
the complete path without touching completed directive #1456 or Pathfinder.

## Preserved completed work

Already staged and verified:

- `commander/src/ironclaude/codex_brain_client.py`
- `commander/src/ironclaude/provider_capabilities.py`
- `commander/tests/test_codex_brain_client.py`
- `commander/tests/test_provider_capabilities.py`

Evidence: exact two-test RED, focused `22 passed`, full Commander `2471 passed`, live seven-name MCP
environment, usable Codex Opus capability, linked Slack timestamp retrieval, directive #1458 approval
card and rejection, and zero diagnostic dispatch. Preserve these changes; do not broaden them.

## Task 1: Enforce bounded Slack pin capacity

Allowed files:

- `commander/src/ironclaude/slack_interface.py`
- `commander/tests/test_slack_interface.py`
- `README.md`

### Step 1: Add RED capacity-policy tests

Update existing simple pin fixtures to return `{"items": []}` from `pins.list`. Add tests that prove:

1. Below capacity, `pins.list` precedes `pins.add` and no removal occurs.
2. At 100 pins, the minimum numeric pin-record `created` item is removed before the new pin is added,
   independent of response order and message timestamp. Parameterize the oldest item across
   `message`, `file`, and `file_comment` and assert the matching `pins.remove` argument.
3. If the target timestamp is already in a 100-pin inventory, return true without remove or add.
4. `pins.list` exceptions, missing `items`, and non-list `items` return false without remove or add.
5. A full inventory where any item lacks usable `created` or its type-specific locator returns false
   without remove or add.
6. Removal failure returns false without adding the new pin.
7. An inventory above 100 items with an unpinned target fails closed without remove or add, while an
   already-pinned target remains an idempotent success at any count. Malformed entries below capacity
   are not selected or removed and therefore do not block the normal add.

Run the exact class before production edits:

```bash
commander/.venv/bin/python -m pytest -p no:cacheprovider \
  commander/tests/test_slack_interface.py::TestSlackBotReactions -q
```

Expected RED: new capacity cases fail because production never calls `pins.list` or `pins.remove`.

### Step 2: Implement the minimum policy

In `SlackBot.pin_message()` only:

- call `pins_list(channel=...)` once;
- read `items` as a list;
- return true if any item's `message.ts` equals the target;
- if length is below 100, retain the existing add behavior;
- at exactly 100, construct all removal candidates, mapping `message` to `timestamp=message.ts`,
  `file` to `file=file.id`, and `file_comment` to `file_comment=comment.id`;
- require every full-inventory item to have numeric `created` and a locator, then select the minimum
  `(created, type, locator)`, call `pins_remove`, and only then call `pins_add`;
- for an unpinned target above 100, or on inventory, full-capacity malformed-selection, or removal
  failure, log a sanitized warning and return false without remove or add;
- retain `already_pinned` as success around the final add.

Add `pins:read` and `pins:write` to README's bot-scope list. Do not add retries, rollback, history,
queueing, or directive-specific logic.

### Step 3: Run GREEN and task verification

Run each command as a separate fail-fast step:

```bash
commander/.venv/bin/python -m pytest -p no:cacheprovider commander/tests/test_slack_interface.py::TestSlackBotReactions -q
commander/.venv/bin/python -m pytest -p no:cacheprovider commander/tests/test_slack_interface.py -q
commander/.venv/bin/python -m pytest -p no:cacheprovider commander/tests -q
git diff --check -- README.md commander/src/ironclaude/slack_interface.py commander/tests/test_slack_interface.py
git add -- README.md commander/src/ironclaude/slack_interface.py commander/tests/test_slack_interface.py
```

Then programmatically compare `git diff --cached --name-only` with the exact expected index: four PM
artifacts, four preserved production/test files, and these three Task 1 files. Fail on any missing or
extra path.

### Rollout correction: enforce the exact count boundary

Final blind review reproduced an unplanned `>100` behavior because production used `len(items) >= 100`.
The operator contract is exact: below 100, add without deletion; at exactly 100, remove the oldest
existing pin before adding; above 100, fail closed. Add one over-capacity regression asserting no
remove or add, plus one under-capacity malformed-entry regression asserting the entry is not selected
and the new pin is added without deletion. Replace `>= 100` with separate `> 100` fail-closed and
`== 100` eviction branches. Do not validate under-capacity entries for eviction and do not add retry,
rollback, history, queueing, or directive-specific behavior.

Run the two new RED nodes before production edits, then the complete reaction class, the full Commander
suite, and diff checks. Restart Commander through the normal CLI after GREEN so the corrected source is
active. Refresh only the process PIDs, final test totals, exact policy wording, and review outcome in
the existing findings; reuse the already-completed exact-100 live evidence without creating another pin.

### Rollout correction: clarify already-pinned overcapacity

The operator rule applies when adding a new pin. Therefore already-pinned detection remains an
idempotent success before capacity handling, while an unpinned target above 100 fails closed. Add one
intersection regression with 101 items containing the target, asserting success after `pins.list`
with no remove or add. Do not change production behavior or perform another live Slack operation.

## Task 2: Fix Codex trust and readiness recognition

Allowed files:

- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/tests/test_worker_adapter.py`

Depends on Task 1 review completion.

### Step 1: Add RED coverage at the real sanitizer seam

1. Import `_strip_ansi` from `ironclaude.tmux_manager`.
2. Let `_FakeTmux` pass scripted raw output through `_strip_ansi`, matching production
   `read_log_tail()` behavior.
3. Replace idealized Codex trust coverage with captured-shape bytes containing CSI cursor movement.
4. Poll the same cumulative trust screen twice, then a cursor-shaped Codex ready marker.
5. Assert readiness and `tmux.sent == [""]`, proving exactly one Enter.
6. Add an unrelated-fragment negative case that reaches readiness without sending Enter.

```bash
commander/.venv/bin/python -m pytest -p no:cacheprovider \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_trust_and_marker \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_does_not_match_unrelated_fragments -q
```

Expected RED: the real-shape trust case fails with the current spaced marker; negative case passes.

### Step 2: Implement Codex-only compact markers

Inside `_wait_for_ready()`'s Codex branch only, remove whitespace from lowercased terminal text and
match exact compact markers:

- `doyoutrustthecontentsofthisdirectory`
- `>_openaicodex`

Preserve polling, timeouts, one-shot dismissal, shared ANSI handling, tmux interfaces, remote behavior,
and Claude's branch.

### Rollout correction: handle the distinct Codex hooks-review gate

Live execution after Task 2 proved directory trust now succeeds, then Codex stops at a second captured
screen: `Hooks need review` with `Trust all and continue` as option 2. Add
`test_wait_for_ready_codex_trust_hooks_and_marker`, scripting repeated ANSI/cursor-shaped hooks-review
output between directory trust and readiness. Require `tmux.sent == [""]` and
`tmux.raw_sent == [["2"]]`. Add a negative
`test_wait_for_ready_codex_does_not_match_unrelated_hooks_fragments` where complete hooks phrases are
reversed or separated beyond the bound.

Inside the existing Codex branch, add a separate one-shot flag. Require directory trust already
dismissed and the ordered bounded compact screen regex
`hooksneedreview.{0,500}(?<!\d)2\.trustallandcontinue`.

The live v0.146 rollout proved that calling `send_keys(..., "2")` is incorrect: the numeric shortcut
activates option 2 immediately, and `send_keys`' appended Enter reopens the Hooks dashboard on the
welcome screen. Replace that call with existing `send_raw_keys(..., ["2"])`, continue polling after
the action, and accept readiness only when the last `>_openaicodex` occurrence follows the last
`hooksneedreview` occurrence in cumulative compact output. Extend the main captured-shape regression
to record raw keys, include the reopened-dashboard ordering before the final fresh welcome marker,
require one raw `2` with no second Enter, and assert the final scripted poll was consumed through an
exact read count or output exhaustion. Preserve all other behavior. Run exact RED/GREEN nodes,
focused parity files, full Commander suite, whitespace validation, and task-boundary review before
repeating Task 3.

### Step 3: Run GREEN and task verification

Run each command as a separate fail-fast step:

```bash
commander/.venv/bin/python -m pytest -p no:cacheprovider \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_trust_hooks_and_marker \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_requires_directory_trust_before_hooks \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_requires_exact_hooks_option_two \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_does_not_match_unrelated_hooks_fragments \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_codex_does_not_match_unrelated_fragments \
  commander/tests/test_worker_adapter.py::test_wait_for_ready_claude_unchanged -q
commander/.venv/bin/python -m pytest -p no:cacheprovider commander/tests/test_worker_adapter.py commander/tests/test_codex_brain_client.py::TestOrchestratorMcpWiring commander/tests/test_provider_capabilities.py -q
commander/.venv/bin/python -m pytest -p no:cacheprovider commander/tests -q
git diff --check -- commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_adapter.py
git add -- commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_worker_adapter.py
```

Then compare the staged path set against the Task 1 expected index plus exactly these two Task 2 files;
fail on any missing or extra path.

## Task 3: Prove live pin and Codex-worker parity

Allowed file:

- `docs/plans/2026-08-03-codex-commander-runtime-parity-fix-findings.md`
- `/private/tmp/ironclaude-codex-parity-probe-019fc109-r2/AGENTS.md`

Depends on Task 2 review completion.

### Step 1: Re-establish preservation baselines

Require protected hashes:

- `AGENTS.md`: `13161859a034afc49c04940ed8424eb52710a3125aaccb21e556d1ca62ea4eb5`
- `commander/config/ironclaude.json`: `bac970ca9d7d26bc6c11ba2d3225b0d7a237fb2bf65c22ec49af2f9e97dee5c8`

Recheck #1456 and its historical worker as completed, capture a fresh maximum event ID, and record the
five-field Pathfinder snapshot. Represent any tracked missing file as `<missing>` in the manifest.
Require no leftover disposable probe directory, row, or tmux session.

Use read-only queries against `commander/data/db/ironclaude.db` selecting only IDs, statuses, clients,
models, repos, timestamps, event IDs/actions, and nonce presence booleans. Never select prompts,
message bodies, tokens, or environment values. Save the pre-probe five-field snapshot in memory and
write only its hashes/count to findings.

### Step 2: Restart and verify live runtime identity

```bash
commander/.venv/bin/python -m ironclaude.cli restart
```

Require one daemon, one descendant Codex app-server, and one non-app-server orchestrator MCP. Print
only PIDs and seven environment names. Send `provider worker codex` in Slack and require sticky Codex,
zero unavailable Codex-worker rows, and usable `gpt-5.6-sol` Opus capability.

The provider command must be sent through Robert's signed-in Slack UI. Fetch its message metadata and
require `user == SLACK_OPERATOR_USER_ID`; a bot-token or user-token simulation is forbidden.

### Step 3: Reuse the completed capacity and non-dispatch proof

Do not create a second capacity diagnostic. Reuse directive #1459 with nonce
`IC-PARITY-019FC109-DIRECTIVE`: the pre-fix channel had 100 pins and rejected #1458 with
`too_many_pins`; after restart, #1459 received standalone review timestamp `1785773771.071139`, was
the newest pin while `pins.list` returned exactly 100 fully locatable items, then Robert's signed-in
Slack UI supplied `:-1:`. Require #1459 `rejected`, its card absent from the resulting 99-pin
inventory, and zero matching worker rows or post-cutoff spawn events. Unit tests remain the
falsifiable evidence that the removed item is the minimum `created` candidate and that removal
precedes addition. Do not restore the evicted pin and do not consume another pin slot for retesting.

The durable linked-retrieval artifact is
`/Users/roberthyatt/.codex/sessions/2026/08/03/rollout-2026-08-03T08-27-42-019fc805-f964-7941-a56b-79dbb79f9873.jsonl`.
Sanitize it to server/tool/arguments/error/timestamp metadata only and require server `orchestrator`,
tool `get_messages_by_ts_range`, `is_error=false`, and timestamp `1785666821.572739`.

### Step 4: Spawn and verify the isolated Codex probe

Create `/private/tmp/ironclaude-codex-parity-probe-019fc109-r2` only after proving it is absent and empty.
Send this exact request:

`DIAGNOSTIC ONLY. Call spawn_worker once with worker_id codex-parity-probe-019fc109-r2, worker_type
claude-opus, repo /private/tmp/ironclaude-codex-parity-probe-019fc109-r2, no directive_id, and objective
"Runtime parity probe only. Do not create, modify, or delete files. Do not call tools or enter a
workflow. Reply exactly: IC_CODEX_PARITY_READY_019FC109_R2". Do not submit or revise any directive.`

Require:

- client `codex`, model `gpt-5.6-sol`, valid fresh native UUID;
- live tmux session and exact `IC_CODEX_PARITY_READY_019FC109_R2` marker;
- exactly one generated path, `AGENTS.md`, byte-identical to
  `commander/src/ironclaude/templates/worker_agents.md`.

Cleanup is unconditional. On success, kill the registered worker through existing `kill_worker`. On
any failed readiness/registration/assertion path, kill the exact tmux session if it exists; if a
registry row exists, reconcile it through existing Commander behavior. In every path, verify tmux is
absent, enumerate the temporary directory, unlink `AGENTS.md` only after its template hash matches,
leave any unexpected path untouched and report it, and remove the directory only when empty.

### Step 5: Final verification and findings

Repeat the Pathfinder snapshot and require exact equality plus zero post-cutoff Pathfinder-targeted
spawns. Recheck protected hashes and re-query #1456 plus its historical worker against the initial
completed-state tuple. Run the full Commander suite once more as a standalone command,
stage only the findings file, and compare the final index to exactly eight production/test files,
`README.md`, four PM artifacts, and findings. Write sanitized findings containing:

- RED/GREEN/full-suite results;
- exact prior RED node names
  `TestOrchestratorMcpWiring::test_app_server_argv_registers_production_orchestrator` and
  `test_codex_login_status_stderr_proves_chatgpt_authentication`, clearly labeled as already-executed
  evidence from this PM session rather than reconstructed RED;
- preserved #1456 facts;
- live runtime PIDs and environment names only;
- capability booleans/model;
- linked-retrieval metadata;
- oldest pin type/locator/created, new review timestamp, capacity count, diagnostic rejection, and
  zero dispatch;
- initial trust failure and corrected worker evidence;
- bootstrap hash and cleanup;
- Pathfinder pre/post hashes;
- protected hashes and final staged paths;
- explicit no commit/no push.

No commit or push.
