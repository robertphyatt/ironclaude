# Codex Brain Token-Usage Payload Parity Fix — Requirements

> **Created:** 2026-08-03
> **Authority:** Operator directives and scoped systematic-debugging findings
> **Scope mode:** Hold

## Objective

Fix the existing Commander feature so a Codex Brain reports real cumulative input, output, and total token counts in heartbeat status instead of remaining at `0 tokens (0 in + 0 out)` after app-server has reported completed usage.

## Functional Requirements

1. Consume the installed Codex app-server `thread/tokenUsage/updated` payload shape.
2. Map `params.tokenUsage.total` to Commander's provider-neutral cumulative usage fields.
3. Preserve existing flat usage parsing as a defensive compatibility fallback.
4. Preserve existing `None` behavior before any usage notification.
5. Preserve existing zero-cost behavior for subscription-billed Codex usage.
6. Preserve existing `seconds_since_last_activity` behavior and heartbeat formatting.
7. Activate the repair in the running Commander with one bounded restart after verification.

## Regression Requirements

1. A test must use the installed protocol shape, including distinct `last` and `total` values.
2. The test must fail against the current parser by returning zero counters.
3. The repaired parser must return cumulative `total` values, not last-turn values.
4. Focused Codex Brain client tests, notification tests, and the full Commander suite must pass.
5. Runtime verification must establish one Commander daemon and one Codex app-server after restart. A bounded observation may report live token-count verification pending if no completed turn occurs; it must not manufacture provider work.

## Change Boundary

Production and regression-test changes are limited to:

- `commander/src/ironclaude/codex_brain_client.py`
- `commander/tests/test_codex_brain_client.py`

No changes are authorized to heartbeat presentation, Claude accounting, provider routing or failover, authentication, model selection, Slack behavior, persistence, MCP behavior, worker behavior, compaction, partial-token estimation, or cost synthesis.

## Delivery Boundary

- No new features or generalized telemetry framework.
- No speculative refactor.
- No commit or push without separate operator authorization.
