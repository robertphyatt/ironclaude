# Codex Commander Runtime Parity Fix Design

> **Created:** 2026-08-02
> **Status:** Design Complete
> **Scope mode:** hold

## Summary

Commander successfully selects Codex for its Brain, but three independent launch/probe defects leave
Codex unable to match the existing Claude workflow. First, the Codex app-server forwards only the
Supabase variables to its orchestrator MCP child, so that child creates no `SlackBot`. Directive
submission therefore writes a `pending_confirmation` row while skipping the standalone Slack review,
pin, reaction update, and linked-message retrieval. Directive #1456 demonstrated this with a null
`interpretation_ts`.

Second, the current Codex CLI returns a successful `codex login status` message on stderr. The
capability probe checks stdout only, falsely records Codex Opus as `unsupported_auth_mode`, and treats
that result as a hard quarantine. Worker routing then falls through to Claude. The live
`pf2e-corpus-rules-approach` worker demonstrated this: the requested Opus semantic tier should have
resolved to Codex Sol, but the registry records client `claude`, model `opus`, and the worker is paused
at Claude's weekly-limit chooser.

Third, a live disposable Codex worker probe reached Codex's trust screen but never accepted it.
`_wait_for_ready()` inspects a tmux pipe log after ANSI removal and searches for the spaced phrase
`do you trust`. Codex renders that TUI with cursor positioning, so the real cleaned bytes contain
`Doyoutrustthecontents...`; the existing idealized plain-text unit test cannot reproduce the runtime
seam. The readiness timeout then falls into the much longer professional-mode identity wait before a
worker can be registered.

The first repaired rollout then exposed a second native Codex startup gate immediately after directory
trust: `Hooks need review`, with `Trust all and continue` as option 2. Commander accepted directory
trust exactly once but had no handler for this distinct screen, so the worker still could not reach
readiness. Because IronClaude workers require the deployed hooks, continuing without trusting them is
not parity. The minimal follow-up recognizes only this exact compact hooks-review screen and selects
option 2 exactly once.

The next live rollout exposed a narrower interaction bug in that handler. `send_keys(..., "2")`
sends the numeric shortcut and then appends Enter. Codex activates option 2 on the shortcut itself,
so the trailing Enter lands on the newly revealed welcome screen and reopens the Hooks dashboard.
Because the pipe log is cumulative, the welcome marker remains visible behind the later Hooks screen
and the old membership-only readiness test returns true prematurely. The correction must send only
the raw `2` shortcut and accept readiness only when its latest occurrence follows the latest hooks
screen occurrence.

The live approval-card probe also established a fourth failure at Slack's 100-pin channel limit:
`pin_message()` calls `pins.add` without first making capacity, so the standalone review is posted but
left unpinned with `too_many_pins`. Operator policy now requires every new pin at capacity to evict the
oldest existing pin before adding the new one.

This design fixes only those four proven parity failures. Directive #1456 and its original
Claude-native worker have since both become `completed` without the worker passing the usage-limit
chooser. They remain immutable historical evidence; live routing acceptance uses an isolated,
disposable Codex probe instead of reviving or replacing that completed work.

## Architecture

### 1. Forward the existing Slack/operator environment contract

Extend `CodexBrainClient._orchestrator_mcp_overrides()`'s existing
`mcp_servers.orchestrator.env_vars` allowlist. Retain `SUPABASE_URL` and `SUPABASE_ANON_KEY`, then add
the five variables already consumed by `orchestrator_mcp.py`:

- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID`
- `SLACK_USER_TOKEN`
- `SLACK_OPERATOR_USER_ID`
- `OPERATOR_NAME`

Only variable names remain in app-server arguments. Values continue through the inherited process
environment and must never appear in argv or diagnostic output. No new environment abstraction or
configuration file is introduced.

### 2. Accept the Codex CLI's successful stderr auth response

Keep the existing `codex login status` probe and its exit-code contract. For return code zero, search
the combined stdout and stderr text for the existing case-insensitive `chatgpt` marker. This accepts
the current CLI response without weakening authentication policy: API-key and unknown successful auth
modes remain `unsupported_auth_mode`, while nonzero results remain `not_authenticated`.

No provider ordering, sticky-selection, fallback, tier mapping, or quarantine policy changes. Once the
false hard quarantine is cleared through the existing provider command, the legacy `claude-opus`
compatibility label continues to mean semantic tier Opus and resolves under the selected Codex client
to `gpt-5.6-sol`.

### 3. Normalize only Codex startup and readiness markers for tmux pipe logs

Keep the existing `read_log_tail()` polling path, one-shot trust dismissal, timeouts, and Claude
branch. Inside the Codex branch only, remove whitespace from the lowercased terminal text and match
the exact compact markers `doyoutrustthecontentsofthisdirectory` and `>_openaicodex`. This accounts for spaces supplied by TUI
cursor movement without changing shared ANSI parsing, remote tmux interfaces, or readiness policy.

Treat directory trust and hooks review as separate one-shot gates. After directory trust has been
accepted, match the hooks screen only when the compact output contains the ordered bounded screen
shape `hooksneedreview.{0,500}(?<!\d)2\.trustallandcontinue`, then send raw key `2` once through the
existing tmux raw-key interface, with no appended Enter. Continue polling after that action. Accept
the compact readiness marker only when its last occurrence follows the last hooks-review marker in
the cumulative log. This binds exact option 2 to the same prompt while tolerating cursor-rendered
explanatory text and prevents a Hooks dashboard layered over the welcome screen from passing. Do not
choose the `Continue without trusting` path.

The regression must begin with real ANSI/cursor-shaped byte patterns rather than pre-cleaned
approximations, prove one Enter for repeated directory-trust output, prove one raw `2` with no Enter
for repeated hooks-review output, reject a welcome marker followed by the reopened Hooks dashboard,
and then prove a later fresh compact Codex readiness marker was actually consumed through exact read
count or scripted-output exhaustion. Negative cases prevent unrelated separated fragments from
triggering either dismissal.

### 4. Make pin capacity before adding a new pin

Keep pin-capacity behavior inside the existing `SlackBot.pin_message()` seam so every caller receives
the same bounded policy. Before adding a pin, call `pins.list` once. If the target message timestamp is
already present, return success without removing anything. If fewer than 100 pins exist, call
`pins.add` as today without validating entries that will not be selected or removed. At exactly 100
pins, require every inventory item to have a numeric pin-record `created` value and a type-specific
removal locator, select the earliest item, remove it, then add the requested timestamp. A count above
100 is outside Slack's bounded channel state and, when the target is not already pinned, must fail
closed without removing or adding a pin. Already-pinned detection remains first because that path
does not add a new pin and must stay idempotent at every inventory count.

Slack inventories may contain `message`, `file`, or `file_comment` items. Map those respectively to
`pins.remove` arguments `timestamp=message.ts`, `file=file.id`, and
`file_comment=comment.id`. Break equal-`created` ties deterministically by item type and locator. If
inventory retrieval, response shape, complete candidate construction, selection, or removal fails,
log a sanitized warning and return false without calling `pins.add`. Preserve the existing
`already_pinned` success handling for race tolerance. No pin history, queue, retry service, or
rollback mechanism is added.

Add the existing Slack endpoints' required `pins:read` and `pins:write` bot scopes to setup
documentation. Current live acceptance must call `pins.list` successfully before any capacity action,
which directly verifies the installed token's read permission; successful oldest removal and new add
verify write permission.

## Components

### Production source

- `commander/src/ironclaude/codex_brain_client.py`
  - Extend the existing orchestrator MCP environment-name allowlist.
- `commander/src/ironclaude/provider_capabilities.py`
  - Recognize the successful ChatGPT marker across stdout and stderr.
- `commander/src/ironclaude/orchestrator_mcp.py`
  - Recognize Codex trust/readiness markers after terminal cursor spacing is lost.
- `commander/src/ironclaude/slack_interface.py`
  - Inventory pins and evict the oldest message/file/file-comment pin record at the 100-pin limit
    before adding a new pin.
- `README.md`
  - Document the `pins:read` and `pins:write` bot scopes required by existing pin operations.

### Focused tests

- `commander/tests/test_codex_brain_client.py`
  - Require the complete explicit MCP environment-name allowlist.
  - Prove representative Slack secret values remain absent from argv.
- `commander/tests/test_provider_capabilities.py`
  - Add the current successful stderr response as a RED regression case.
  - Preserve rejection tests for API-key and unknown auth modes.
- `commander/tests/test_worker_adapter.py`
  - Replace idealized readiness coverage with raw ANSI/cursor-shaped Codex output.
  - Prove one-shot directory trust, one-shot hooks trust, compact readiness, and negative fragments.
- `commander/tests/test_slack_interface.py`
  - Prove no eviction below capacity, no eviction for an already-pinned target, oldest-by-`created`
    type-specific eviction before addition at capacity, and fail-closed inventory/shape/removal
    behavior.

### Operational verification

- Restart Commander so the Codex app-server and orchestrator MCP receive the new configuration.
- Re-select Codex for the worker role through `/ironclaude provider worker codex`, which atomically
  clears the false Codex-worker capability quarantine using existing behavior.
- Preserve completed directive #1456 and `pf2e-corpus-rules-approach` unchanged. Record that its log
  never advanced beyond Claude's usage-limit chooser.
- Exercise linked-message retrieval through the live Codex Brain's existing
  `get_messages_by_ts_range` tool and require the requested Slack timestamp in the returned metadata.
- Reuse rejected standalone approval card #1459 with nonce `IC-PARITY-019FC109-DIRECTIVE`; require its
  non-null review timestamp, 100-pin replacement evidence, operator rejection, and zero matching
  worker rows or post-cutoff spawn events. Do not submit another capacity diagnostic.
- Spawn one nonce-labeled `claude-opus` compatibility worker in a dedicated temporary directory with
  a no-write readiness objective. Commander is expected to inject exactly its standard `AGENTS.md`
  bootstrap before launch; hash that file and require the worker to create no additional paths.
  Require registry/process evidence for client `codex`, model `gpt-5.6-sol`, and the exact readiness
  marker, then stop it through existing `kill_worker` behavior.
- Hash the Pathfinder checkout before and after live verification and require exact equality. No
  runtime probe targets `/Users/roberthyatt/Code/roleplaying-agents`.

## Data Flow

### Directive confirmation

```text
Commander .env
  -> Codex app-server inherited environment
  -> explicit orchestrator MCP env_vars allowlist
  -> orchestrator_mcp.py creates SlackBot
  -> submit_directive posts standalone review
  -> interpretation_ts stored
  -> pin inventory checked
  -> at 100 pins, oldest pin record removed
  -> review pinned and source reaction updated
```

Brain commentary remains on its independent existing threaded path.

### Worker selection

```text
spawn_worker(worker_type=claude-opus compatibility label)
  -> semantic tier opus
  -> codex login status returns rc=0 plus ChatGPT marker on stderr
  -> capability available
  -> selected worker client codex
  -> model gpt-5.6-sol
  -> compact Codex trust marker recognized and accepted once
  -> exact hooks-review marker recognized and raw option 2 accepted once without trailing Enter
  -> readiness marker occurs after the latest hooks-review marker
  -> fresh Codex worker process/session
```

## Error Handling

- Missing parent Slack variables: live acceptance fails with variable names only; no secret values are
  printed and no argv-secret fallback is added.
- Codex auth result with nonzero exit: existing `not_authenticated` result remains unchanged.
- Successful auth output without `chatgpt`: existing `unsupported_auth_mode` result remains unchanged.
- Stale Codex quarantine: clear it only through the existing explicit provider-selection command; do
  not edit SQLite directly.
- Commander restart failure: stop and report existing lifecycle diagnostics rather than testing stale
  children.
- Completed historical work: do not resume, kill again, revise, or replace directive #1456 or its
  Claude-native worker.
- Diagnostic dispatch: use a unique nonce and require no matching worker description or post-cutoff
  `worker_spawned` event after rejection.
- Probe isolation: create a dedicated empty temporary directory outside both repositories; allow and
  hash only Commander's expected `AGENTS.md` bootstrap, require no other path, and require the probe's
  exact readiness marker before cleanup. Each live attempt must use a fresh worker ID, marker, and
  temporary path so Brain one-shot deduplication cannot substitute an earlier failed attempt.
- Codex hooks review: require prior directory-trust acceptance and the ordered bounded compact
  hooks-screen shape; send raw option 2 once with no trailing Enter even when cumulative pipe logs
  repeat the screen; require readiness newer than the latest hooks marker. Do not generalize prompt
  selection or disable hooks.
- Slack pin inventory failure or missing/non-list `items`: return false without removing a pin or
  attempting a capacity-blind add.
- Malformed full-capacity inventory, including any item lacking `created` or its type-specific
  locator, or an inventory count above 100 with an unpinned target: return false without removing a
  pin or adding the new pin. An already-pinned target returns success before these new-pin checks.
- Oldest-pin removal failure: return false without adding the new pin.
- Concurrent already-pinned race: preserve the existing `already_pinned` success result.
- Pathfinder preservation: compare status, tracked and nonignored working-file manifest, unstaged
  binary diff, and staged binary diff hashes before and after runtime verification.
- Slack Socket Mode `BAD_LENGTH` reconnects, notification durability, and generic directive behavior
  are unchanged and outside scope.

## Testing Strategy

Implementation follows RED before GREEN.

1. Add a failing Codex Brain wiring assertion for all seven forwarded environment names and sentinel
   secrecy.
2. Add a failing capability-probe test for `ProbeResult(0, "", "Logged in using ChatGPT")`.
3. Add RED pin-capacity regressions for no eviction below capacity, already-pinned idempotence,
   oldest-by-`created` message/file/file-comment eviction ordering, inventory exception/shape failure,
   and fail-closed malformed-at-capacity, over-capacity-unpinned, over-capacity-already-pinned, and
   removal behavior.
4. Add a RED regression using real ANSI/cursor-shaped directory-trust and hooks-review output,
   including raw-key versus auto-Enter behavior, a reopened Hooks dashboard after a welcome marker,
   one-shot, ordering, distance-bound, and negative coverage.
5. Make the minimum four production changes.
6. Run focused test classes/files, then the full Commander suite with pytest cache disabled.
7. Verify the pre-existing dirty files `AGENTS.md` and `commander/config/ironclaude.json` are byte-for-byte
   unchanged and no unrelated path is staged.
8. Restart Commander and verify one daemon, one Codex Brain app-server, and one orchestrator MCP.
9. Inspect only live MCP environment variable names; require all seven names and expose no values.
10. Run a live Codex auth probe and require rc=0 plus a usable Codex-worker capability after explicit
   worker-provider reselection.
11. Reuse the successful nonce-labeled linked-message rollout already produced during the first
    runtime pass; do not repeat linked-message retrieval.
12. Reuse #1459's already-captured capacity evidence: before-fix `too_many_pins`, non-null review
    timestamp `1785773771.071139`, the review as newest of exactly 100 fully locatable pins, Robert's
    rejection, 99 pins with card absent after cleanup, and zero nonce-matching dispatch. Do not submit
    another capacity diagnostic; unit tests provide exact oldest-selection and call-order evidence.
13. Spawn a disposable compatibility-label worker in the isolated temporary directory. Require client
    `codex`, model `gpt-5.6-sol`, a fresh native UUID, a live tmux session, and the exact readiness
    marker; then stop it through existing Commander behavior.
14. Require exactly the expected bootstrap `AGENTS.md` before worker output, no additional path after
    readiness, and removal of the temporary directory after kill.
15. Require identical Pathfinder status and content/diff hashes before and after the runtime probe,
    plus no post-cutoff spawn targeting the Pathfinder repository.

Process existence, DB insertion alone, Brain chatter, compatibility labels, or mocked unit tests are not
accepted as final runtime proof.

## Explicitly Excluded

- Brain chatter/thread routing changes
- Provider router or fallback-policy redesign
- Provider-neutral worker-label migration
- Slack TLS/Socket Mode repair
- Slack outbox or retry infrastructure
- Generic directive persistence, confirmation, or dispatch changes
- Pin history, queueing, retry service, or rollback infrastructure
- Claude client behavior changes
- Direct database repair
- Any Pathfinder corpus implementation or repository mutation by this fix
- Revival, replacement, or status mutation of completed directive #1456 or its original worker

## Implementation Notes

The target checkout already has unrelated user changes in `AGENTS.md` and
`commander/config/ironclaude.json`. Preserve them exactly. Product changes are limited to the eight
source/test files plus `README.md` listed above; professional-mode design and plan artifacts are the
only additional repository files.
