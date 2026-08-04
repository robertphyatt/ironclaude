# Codex Brain Token-Usage Payload Parity Fix — Design

> **Created:** 2026-08-03
> **Status:** Design Complete
> **Scope:** Restore existing Codex/Claude heartbeat token-accounting parity; no new telemetry or provider behavior
> **Requirements:** `docs/plans/2026-08-03-codex-brain-token-usage-payload-fix-requirements.md`

## Problem

While the Codex Brain is active, Commander heartbeats can remain:

```text
🧠 Brain: 0 tokens (0 in + 0 out) — turn in progress (...)
```

The Commander process and Codex app-server are healthy and continue producing Brain responses. The zero counters are therefore not evidence of an idle or failed Brain.

## Root Cause

Codex app-server emits `thread/tokenUsage/updated` with this relevant structure:

```text
params.tokenUsage.total.inputTokens
params.tokenUsage.total.outputTokens
params.tokenUsage.total.totalTokens
```

`CodexBrainClient._handle_event()` recognizes the correct event and stores `params.tokenUsage`, but `get_token_usage()` reads token counters directly from that outer object. The counters are one level lower under `total`, so the getter returns zero for every counter.

The existing regression test uses a synthetic flat `params.usage` payload. It verifies the event name but cannot detect drift from the installed app-server protocol. An exact schema-shaped reproduction currently returns zero despite nonzero cumulative counters.

Historical Commander behavior establishes two constraints:

- The heartbeat contract is accumulated usage since Brain restart. Codex `tokenUsage.total`, not `tokenUsage.last`, supplies that cumulative value.
- A legitimate active first turn may remain at zero until the provider reports completed usage. Existing `seconds_since_last_activity` formatting already distinguishes that case and remains unchanged.

## Selected Repair

Normalize the stored payload in `CodexBrainClient.get_token_usage()`:

1. If the stored usage object contains a dictionary-valued `total`, read counters from that nested cumulative breakdown.
2. Otherwise, retain the existing flat-object parsing as a defensive compatibility fallback.
3. Continue deriving total from input plus output only when the selected breakdown omits its explicit total.
4. Continue reporting Codex cost as zero when app-server supplies no cost.

This keeps event dispatch simple and localizes provider-protocol translation to the existing adapter method whose purpose is returning Commander’s provider-neutral usage shape.

## Files and Boundaries

Modify only:

- `commander/src/ironclaude/codex_brain_client.py`
- `commander/tests/test_codex_brain_client.py`

Do not modify:

- heartbeat formatting or duration logic;
- `main.py` heartbeat wiring;
- Claude usage accounting;
- provider routing, failover, authentication, or model selection;
- database persistence, Slack behavior, MCP behavior, worker behavior, or compaction;
- cost synthesis or partial-token estimation.

## Error Handling

Existing fail-soft behavior remains:

- No stored usage event returns `None`.
- A missing or malformed nested `total` falls back to the existing flat parsing path.
- Missing individual counters remain zero.
- Activity age continues to come from the already maintained `_last_activity` timestamp.

No new logging, retries, state, or user-visible status variants are introduced.

## Verification

### Regression test

Replace the synthetic flat event fixture with the installed protocol shape, including required `threadId`, `turnId`, and distinct `last` and `total` breakdowns. Assert that:

- returned input, output, and total counters equal the cumulative `total` values;
- values do not accidentally come from `last`;
- existing activity and cost fields retain their contract.

The test must fail before the parser change by returning zeros and pass afterward with the cumulative counts.

### Focused and regression suites

Run:

- `commander/tests/test_codex_brain_client.py`
- `commander/tests/test_notifications.py`
- the full Commander test suite

### Running Commander activation

After code and tests pass, restart the existing Commander once so its Codex Brain loads the repaired adapter. Verify one Commander daemon and one Codex app-server remain active, and inspect subsequent runtime evidence for a schema-shaped token-usage notification producing nonzero cumulative counters. If no completed Codex turn occurs during the bounded observation window, report live counter verification as pending rather than generating synthetic work or changing behavior.

## Rejected Alternatives

- **Change heartbeat formatting:** rejected because the formatter accurately displays the adapter values; masking zeros would leave accounting broken.
- **Normalize in event dispatch:** valid but adds event-specific branching to the dispatcher when the existing getter is already the provider-neutral mapping boundary.
- **Estimate live or partial tokens:** rejected as a new telemetry feature and inconsistent with prior accumulated-turn semantics.
- **Remove flat fallback:** rejected because it is unnecessary for the repair and would broaden behavior change.
