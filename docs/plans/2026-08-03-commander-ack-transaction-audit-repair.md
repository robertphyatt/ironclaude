# Commander Acknowledgment Transaction and Audit Repair Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Prevent acknowledgment persistence from owning an existing caller transaction and remove duplicate `/audit` disposition totals without changing normal Commander behavior.

**Requirements:** `docs/plans/2026-08-03-commander-actionable-work-convergence-requirements.md`

**Architecture:** Keep the injected authoritative SQLite connection and existing commit-before-Slack flow. Require that connection to be transaction-idle before the helper reads or writes, then retain existing immutable/race behavior. Delete only the two redundant audit summary lines.

**Tech Stack:** Python 3, `sqlite3`, pytest, Slack Bolt Commander daemon.

## Execution Invariants

- Professional mode remains active. Use sequential `gpt-5.6-terra` execution for Task 1; main context owns Task 2 runtime/state work and all reviews.
- Each command is independent. Use absolute repo paths or `git -C /Users/roberthyatt/Code/ironclaude`.
- Shell cwd for Commander commands is `/Users/roberthyatt/Code/ironclaude/commander`.
- `docs/` is ignored; stage workflow artifacts with `git add -f`.
- Protect `AGENTS.md` at SHA-256 `13161859a034afc49c04940ed8424eb52710a3125aaccb21e556d1ca62ea4eb5`.
- Protect `commander/config/ironclaude.json` at SHA-256 `bac970ca9d7d26bc6c11ba2d3225b0d7a237fb2bf65c22ec49af2f9e97dee5c8`.
- Do not commit or push.
- No new operator Slack message, directive, worker, pin, provider action, classifier, connection, retry, or feature.

---

## Task 1: Enforce transaction ownership and canonical audit totals

**Execution:** One focused sequential `gpt-5.6-terra` worker. Main context retains state transitions and task-boundary review.

**Files:**
- Modify: `commander/src/ironclaude/db.py`
- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/tests/test_db.py`
- Modify: `commander/tests/test_daemon.py`

### Step 1: Write RED transaction and audit tests

In `commander/tests/test_db.py`, add a real-SQLite test with this behavior:

```python
def test_persist_operator_message_acknowledgement_rejects_active_transaction(tmp_path):
    db_path = str(tmp_path / "active-transaction.db")
    conn = init_db(db_path)
    reader = sqlite3.connect(db_path)
    source_ts = "1785808181.347549"
    conn.execute(
        "INSERT INTO objectives (text, status) VALUES (?, ?)",
        ("unrelated pending write", "active"),
    )
    assert conn.in_transaction

    with pytest.raises(
        RuntimeError,
        match="^Database connection already has an active transaction; "
              "operator message acknowledgement requires an idle connection\\.$",
    ):
        persist_operator_message_acknowledgement(conn, source_ts, "status only")

    assert conn.in_transaction
    assert conn.execute(
        "SELECT COUNT(*) FROM objectives WHERE text=?", ("unrelated pending write",)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements WHERE source_ts=?",
        (source_ts,),
    ).fetchone()[0] == 0
    assert reader.execute(
        "SELECT COUNT(*) FROM objectives WHERE text=?", ("unrelated pending write",)
    ).fetchone()[0] == 0
    assert reader.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements WHERE source_ts=?",
        (source_ts,),
    ).fetchone()[0] == 0

    conn.rollback()
    persisted = persist_operator_message_acknowledgement(conn, source_ts, "status only")
    assert persisted["source_ts"] == source_ts
    assert reader.execute(
        "SELECT COUNT(*) FROM operator_message_acknowledgements WHERE source_ts=?",
        (source_ts,),
    ).fetchone()[0] == 1
    reader.close()
    conn.close()
```

Expose `in_transaction` from the existing `CommitFailingConnection` test double:

```python
@property
def in_transaction(self):
    return self.inner.in_transaction
```

In `commander/tests/test_daemon.py`, update `TestHandleAudit.test_audit_all_mapped`
and `test_audit_all_unmapped` to assert the canonical totals and the absence of
duplicate labels:

```python
assert "Directives: 1" in msg
assert "Acknowledged: 0" in msg
assert "Unresolved: 0" in msg
assert "Mapped to directives:" not in msg
assert "Unmapped:" not in msg
```

Use `0/0/1` for the all-unmapped case.

### Step 2: Run RED tests

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py::test_persist_operator_message_acknowledgement_rejects_active_transaction tests/test_daemon.py::TestHandleAudit::test_audit_all_mapped tests/test_daemon.py::TestHandleAudit::test_audit_all_unmapped
```

Expected: FAIL. The transaction test reports no `RuntimeError`; both audit tests
find one of the forbidden duplicate labels. Record exact output.

### Step 3: Implement the minimum production repair

In `commander/src/ironclaude/db.py`, immediately after existing argument validation
and before `result_from` or any SQL, add:

```python
if conn.in_transaction:
    raise RuntimeError(
        "Database connection already has an active transaction; "
        "operator message acknowledgement requires an idle connection."
    )
```

Do not change the helper's existing lookup, insert, commit, rollback, race, or result
logic.

In `commander/src/ironclaude/main.py`, delete only:

```python
lines.append(f"• Mapped to directives: {len(mapped)}")
lines.append(f"• Unmapped: {len(unresolved)}")
```

Retain `Directives`, `Acknowledged`, and `Unresolved` totals and all detailed audit
sections unchanged.

### Step 4: Run GREEN tests

Run the exact Step 2 command.

Expected: PASS, exit 0. Removing the transaction guard or restoring either duplicate
audit line makes a named test fail.

### Step 5: Run focused parity regression

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py tests/test_daemon.py tests/test_orchestrator_mcp.py -k 'operator_message_acknowledgement or audit or transport_acknowledgement or TestAcknowledgeOperatorMessage'
```

Expected: PASS, exit 0; record measured count. This catches helper delegation,
commit-before-post, fail-closed transport, audit classification, and race regressions.

### Step 6: Stage only Task 1 files

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/db.py commander/src/ironclaude/main.py commander/tests/test_db.py commander/tests/test_daemon.py
git -C /Users/roberthyatt/Code/ironclaude diff --staged --check
```

Expected: four Task 1 files staged; diff check exits 0. Do not commit or push.

---

## Task 2: Full regression, restart, and bounded findings

**Execution:** Main context only. No implementation delegation; this task owns workflow state, process identity, runtime verification, findings, and final staging.

**Files:**
- Create: `docs/plans/2026-08-03-commander-ack-transaction-audit-repair-findings.md`

### Step 1: Verify protected scope before regression

Run:

```bash
shasum -a 256 /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/commander/config/ironclaude.json
git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: protected hashes match execution invariants and both protected files remain
unstaged.

### Step 2: Run combined Commander regression

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py tests/test_orchestrator_mcp.py tests/test_enforcement.py tests/test_main_validate.py tests/test_slack_interface.py tests/test_codex_brain_client.py tests/test_daemon.py
```

Expected: PASS, exit 0; record measured count. Do not predict the count before
execution.

### Step 3: Run full Commander regression

Run:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/
```

Expected: PASS, exit 0; record measured pass/skip counts. Do not predict counts before
execution.

### Step 4: Restart the verified sole Commander

Run outside a sandbox that blocks process inspection:

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/ironclaude restart
```

Expected: identity-checked restart signals the sole daemon; no duplicate starts.

### Step 5: Verify topology and retained disposition evidence

Run:

```bash
ps -axo pid,ppid,lstart,command | rg 'ironclaude\.main|codex app-server'
tail -n 300 /tmp/ironclaude-daemon.log | rg 'Brain SDK client started|Brain Codex client started|IronClaude Commander daemon starting|Bolt app is running'
sqlite3 /Users/roberthyatt/Code/ironclaude/commander/data/db/ironclaude.db "SELECT source_ts,reason,created_at FROM operator_message_acknowledgements WHERE source_ts IN ('1785805709.575399','1785807711.015959') ORDER BY source_ts;"
sqlite3 /Users/roberthyatt/Code/ironclaude/commander/data/db/ironclaude.db "SELECT id,source_ts,status,interpretation_ts FROM directives WHERE source_ts='1785808112.894889' ORDER BY id;"
```

Expected: exactly one Commander and one attached Codex Brain app-server; fresh healthy
startup; both prior acknowledgment rows remain; the operator-confirmed actionable
source retains its directive row. No new Slack message or directive is created.

### Step 6: Write bounded findings

Create `docs/plans/2026-08-03-commander-ack-transaction-audit-repair-findings.md`
containing root cause, RED/GREEN output, focused/full counts, restart topology, retained
DB evidence, protected hashes, and explicit no-commit/no-push statement. Do not copy
Slack message text, credentials, or unrelated diagnostics.

### Step 7: Stage workflow artifacts and run diff check

Run:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- docs/plans/2026-08-03-commander-ack-transaction-audit-repair-design.md docs/plans/2026-08-03-commander-ack-transaction-audit-repair.md docs/plans/2026-08-03-commander-ack-transaction-audit-repair.plan.json docs/plans/2026-08-03-commander-ack-transaction-audit-repair-findings.md
git -C /Users/roberthyatt/Code/ironclaude diff --staged --check
```

Expected: repair source/tests and four repair workflow artifacts staged; diff check
exits 0; no commit or push.

### Step 8: Final protected-scope verification

Run:

```bash
shasum -a 256 /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/commander/config/ironclaude.json
git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: protected hashes unchanged; protected files remain unstaged; no files beyond
existing convergence work and this repair loop are staged. Do not commit or push.
