# Commander Acknowledgment Transaction and Audit Repair Design

> **Created:** 2026-08-03
> **Status:** Design Complete
> **Scope mode:** Reduction

## Summary

Repair two defects found while closing the existing Commander actionable-work
convergence loop. This is parity repair only: it adds no classifier, workflow,
transport, retry, connection, or operator-facing feature.

First, `persist_operator_message_acknowledgement()` currently calls `commit()` or
`rollback()` on its injected shared SQLite connection without proving that it owns
the active transaction. A caller's unrelated pending write is therefore committed
alongside the acknowledgment. The helper must fail closed before doing any work when
the connection is already inside a transaction.

Second, `/audit` currently prints duplicate category totals. It must report the
three already-designed disposition categories once each: `Directives`,
`Acknowledged`, and `Unresolved`.

## Confirmed Evidence

A diagnostic using the production helper created an unrelated uncommitted objective
on the shared connection, then persisted an acknowledgment. Before the helper,
`conn.in_transaction` was `True`; afterward it was `False`, and a second connection
could read both the unrelated objective and acknowledgment. The helper therefore
committed work it did not own.

Current `/audit` construction emits five totals:

- `Mapped to directives` and `Directives` contain the same count;
- `Unmapped` and `Unresolved` contain the same count; and
- `Acknowledged` contains the third disposition count.

The approved convergence design defines only three categories: directive,
acknowledged, and unresolved.

## Architecture

### Transaction ownership guard

Keep the existing authoritative-connection-only helper and its existing SQL,
immutability, race convergence, trigger behavior, and commit-before-Slack contract.
Before its first lookup or write, require `conn.in_transaction` to be false. If it is
true, raise a deterministic `RuntimeError` and leave the caller's transaction
untouched.

Both existing marked-reply transports already catch helper failures and skip Slack
delivery/reaction. The existing MCP acknowledgment operation already surfaces helper
errors. No caller-specific handling is added.

This guard must run before the existing-row lookup. Returning a row observed through
an already-active caller transaction would not establish the helper's required
standalone durable disposition boundary.

### Audit category cleanup

Delete the redundant `Mapped to directives` and `Unmapped` summary lines. Retain the
existing detailed sections and the three canonical summary totals:

- `Directives`;
- `Acknowledged`;
- `Unresolved`.

No audit query, classification, search window, detailed row, or Slack behavior
changes.

## Rejected Alternatives

### Open a dedicated SQLite connection

Rejected. The approved architecture requires persistence through the injected
authoritative connection, and callers do not provide a database path. Adding a
connection would widen ownership and lifecycle scope.

### Use a savepoint inside an existing transaction

Rejected. Releasing a savepoint does not commit the outer transaction, so an
independent connection cannot observe the acknowledgment before Slack delivery. It
cannot satisfy the existing durability requirement.

### Commit or roll back the caller transaction selectively

Rejected. SQLite cannot commit only the acknowledgment while leaving unrelated work
pending on the same connection. Silent transaction ownership remains the defect.

## Error Handling

- `conn is None`, malformed timestamps, and blank reasons retain current failures.
- An already-active transaction raises before any lookup, insert, commit, or rollback.
- Both Slack transports continue to fail closed on this error.
- A clean connection retains existing immutable-row and concurrent-insert behavior.
- `/audit` database or Slack search failures retain current messages.

## Testing Strategy

Add one real-SQLite RED test proving an unrelated pending write is neither committed
nor rolled back when acknowledgment persistence is attempted. At the failure point:

- the helper raises the exact transaction-ownership error;
- the original connection remains in its transaction;
- a second connection cannot see the unrelated write;
- no acknowledgment exists; and
- after the caller rolls back, the helper succeeds normally.

Update the existing `/audit` tests to require the three canonical totals and reject
the two duplicate legacy labels. These assertions must exercise `_handle_audit()`,
not a formatting helper mock.

Run focused DB, daemon, and transport tests, then the full Commander suite. Restart
the sole Commander once and verify healthy Codex Brain topology. Re-query the prior
accepted non-actionable message and actionable directive evidence; no new operator
message or Slack approval is required because normal clean-connection behavior is
unchanged.

## Scope Boundaries

- No changes to acknowledgment schema or triggers.
- No new database connection, transaction abstraction, retry, or classifier.
- No changes to reply markers, Slack delivery, directives, workers, providers, pins,
  or approval cards.
- No new audit fields or commands.
- No changes to `AGENTS.md` or `commander/config/ironclaude.json`.
- No commit and no push.
