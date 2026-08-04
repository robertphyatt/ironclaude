# Commander Acknowledgment Transaction and Audit Repair Findings

> **Date:** 2026-08-03
> **Status:** Complete

## Scope

Parity repair only. No new classifier, connection, retry, transport, directive,
worker, provider, pin, approval, or Slack workflow was added. `AGENTS.md` and
`commander/config/ironclaude.json` remained operator-owned and unstaged.

## Root Cause

`persist_operator_message_acknowledgement()` called `commit()` or `rollback()` on
its injected shared SQLite connection without first establishing transaction
ownership. A diagnostic reproduced the defect: an unrelated pending objective made
`conn.in_transaction` true; acknowledgment persistence changed it to false; and a
second connection could read both writes.

`/audit` also emitted duplicate disposition totals: the legacy mapped/unmapped
labels repeated the canonical directive/unresolved counts.

## Repair

- Added one fail-closed `conn.in_transaction` precondition before any acknowledgment
  helper SQL.
- Preserved existing validation, immutable-row lookup, insert, commit, rollback,
  concurrency convergence, and database-trigger behavior.
- Deleted only the duplicate `Mapped to directives` and `Unmapped` summary lines.
- Retained the canonical `Directives`, `Acknowledged`, and `Unresolved` totals and
  every detailed audit section.

## TDD Evidence

Exact RED command exercised one real-SQLite transaction test and two audit tests:

- `3 failed in 0.33s`;
- transaction test failed because no `RuntimeError` was raised; and
- both audit tests failed because the forbidden duplicate label remained.

After the minimum production repair, the exact GREEN command produced:

- `3 passed in 0.26s` from the delegated execution; and
- independent main-context rerun: `3 passed in 0.27s`.

Focused helper/audit/MCP/transport regression produced:

- delegated execution: `38 passed, 845 deselected in 1.15s`;
- independent main-context rerun: `38 passed, 845 deselected in 1.10s`.

The transaction test proves, before rollback, that the caller connection still sees
its unrelated pending objective and sees no acknowledgment, while an independent
reader sees neither write. After caller rollback, normal clean-connection
acknowledgment succeeds and becomes visible to the independent reader.

## Full Regression Evidence

- Combined seven-module Commander regression: `1221 passed in 143.99s`.
- Full Commander suite: `2558 passed, 1 skipped in 267.06s`.

## Restart and Retained Runtime Evidence

Identity-checked CLI restart reported:

- `Restart signal sent to daemon PID 66653`.

Fresh startup evidence appeared at `2026-08-03 20:31:41–42 MDT`:

- Slack Bolt running;
- Brain SDK client started; and
- Commander daemon starting.

Post-restart process inspection showed exactly one Commander daemon, PID `66653`,
and one attached Codex Brain app-server, PID `80274`.

Read-only Commander database verification retained:

- acknowledgment `1785805709.575399`, created `2026-08-04 01:08:59`;
- acknowledgment `1785807711.015959`, created `2026-08-04 01:42:21`; and
- directive `1461`, source `1785808112.894889`, status `in_progress`, approval-card
  timestamp `1785808181.347549`.

No new operator message, directive, worker, pin, provider action, or Slack approval
was created for this repair loop.

## Protected Scope

Final expected protected hashes:

- `AGENTS.md`: `13161859a034afc49c04940ed8424eb52710a3125aaccb21e556d1ca62ea4eb5`
- `commander/config/ironclaude.json`: `bac970ca9d7d26bc6c11ba2d3225b0d7a237fb2bf65c22ec49af2f9e97dee5c8`

No commit was created. Nothing was pushed.
