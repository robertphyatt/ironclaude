# Commander Direct-Reply Acknowledgment Enforcement Continuation Plan

> **For Claude/Codex:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` in subagent-sequential mode. Use one `gpt-5.6-terra` worker at a time for Tasks 1 and 2. Main context alone owns state transitions, reviews, regression, daemon restart, live Slack verification, and findings.

**Goal:** Enforce the existing durable non-actionable disposition at both marked direct-reply transports so Codex cannot post/react successfully after omitting `acknowledge_operator_message`.

**Requirements:** `docs/plans/2026-08-03-commander-actionable-work-convergence-requirements.md`

**Design:** `docs/plans/2026-08-03-commander-actionable-work-convergence-design.md`

**Architecture:** Add one authoritative-connection-only DB persistence helper. Reuse it from the existing MCP acknowledgment tool and from both valid marked-reply paths immediately before their first Slack post. Preserve explicit reasons; otherwise use one classification-only fallback. Fail closed before Slack/reaction on DB absence, directive conflict, or persistence failure. Preserve parser, grading, chunking, Slack queue, unmarked posting, pinning, directives, workers, and provider behavior.

**Tech stack:** Python 3.11, SQLite, Slack Bolt/WebClient, pytest.

## Execution invariants

- Every shell step is independent; use literal absolute paths.
- Shell cwd is `/Users/roberthyatt/Code/ironclaude/commander`; Git commands use `git -C /Users/roberthyatt/Code/ironclaude`.
- Preserve `AGENTS.md` hash `13161859a034afc49c04940ed8424eb52710a3125aaccb21e556d1ca62ea4eb5` and `commander/config/ironclaude.json` hash `bac970ca9d7d26bc6c11ba2d3225b0d7a237fb2bf65c22ec49af2f9e97dee5c8`; both remain unstaged.
- Preserve all reviewed/staged work from earlier continuation tasks.
- Task 1 and Task 2 use separate sequential `gpt-5.6-terra` workers. Main context owns task claims/submissions, reviews, transitions, runtime, and human gates.
- RED tests must fail at the named persistence/transport seams; GREEN must exercise the same seams without helper mocks.
- `docs/` is gitignored; workflow artifacts require `git add -f`.
- No commit or push.

---

## Task 1: Centralize acknowledgment persistence

**Files:**

- Modify: `commander/src/ironclaude/db.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/tests/test_db.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`

### Step 1: Add RED shared-helper tests

In `test_db.py`, add direct tests for a new
`persist_operator_message_acknowledgement(conn, source_ts, reason)` helper:

- `None` connection, malformed/non-string timestamp, and blank/non-string reason fail without opening or creating a database;
- first insert returns the stored row;
- a repeat preserves original reason and `created_at`;
- two concurrent acknowledgment calls converge on one immutable row; and
- update the existing two-connection directive/acknowledgment race so its acknowledgment branch uses the real helper and exactly one disposition still wins.

In `TestAcknowledgeOperatorMessage`, add a delegation test proving
`OrchestratorTools.acknowledge_operator_message()` calls the shared helper and
retains its existing return/error contract.

### Step 2: Run RED

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py tests/test_orchestrator_mcp.py -k 'persist_operator_message_acknowledgement or disposition_exclusivity_two_connections_race or TestAcknowledgeOperatorMessage'
```

Expected: FAIL because the shared helper/constant do not exist and the existing MCP method still owns persistence inline.

### Step 3: Implement the shared helper and delegate the MCP tool

In `db.py`:

- define `DIRECT_REPLY_FALLBACK_REASON = "Marked direct reply classified operator message as non-actionable."`;
- define strict timestamp validation equivalent to digits-dot-digits;
- implement `persist_operator_message_acknowledgement(conn, source_ts, reason)` using only the injected connection—never a path or `sqlite3.connect()`;
- return an existing row unchanged;
- otherwise insert and commit;
- on `IntegrityError`, rollback and re-query: return a concurrently inserted acknowledgment, otherwise re-raise the directive conflict;
- on any other persistence exception, rollback and re-raise; and
- return a normal dict independent of row-factory configuration.

In `orchestrator_mcp.py`, remove its private acknowledgment validation/SQL and delegate `OrchestratorTools.acknowledge_operator_message()` to the shared helper. Do not change the FastMCP wrapper, schema, triggers, or any unrelated tool.

### Step 4: Run focused GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py tests/test_orchestrator_mcp.py -k 'persist_operator_message_acknowledgement or disposition_exclusivity_two_connections_race or TestAcknowledgeOperatorMessage'
```

Expected: PASS, exit 0. Removing shared delegation, validation, immutable repeat, concurrent convergence, rollback/re-query, or reciprocal trigger protection makes a named test fail.

### Step 5: Run Task 1 regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py tests/test_orchestrator_mcp.py
```

Expected: PASS, exit 0.

### Step 6: Stage Task 1

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/db.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_db.py commander/tests/test_orchestrator_mcp.py
```

Expected: Task 1 files added to existing staged work; protected files remain unstaged.

---

## Task 2: Enforce acknowledgment at both marked-reply transports

**Files:**

- Modify: `commander/src/ironclaude/main.py`
- Modify: `commander/src/ironclaude/orchestrator_mcp.py`
- Modify: `commander/tests/test_daemon.py`
- Modify: `commander/tests/test_main_validate.py`
- Modify: `commander/tests/test_orchestrator_mcp.py`

**Depends on:** Task 1

### Step 1: Add RED daemon-path tests

Extend `TestSolicitedReply` with real initialized DB connections and tests proving:

- absent acknowledgment is inserted with `DIRECT_REPLY_FALLBACK_REASON` and committed before `_post_brain_message()` first runs;
- a preexisting explicit reason remains unchanged;
- `None` DB, a preexisting directive for the exact timestamp, and a real persistence failure produce no Slack post or reaction;
- a real `SlackBot` whose WebClient post raises retains the exact threaded reply in its existing notification queue, leaves the fallback acknowledgment committed, and produces no check reaction; and
- polling continues to a later response after a blocked marked reply.

Update `_make_poll_daemon()` in `test_main_validate.py` to use an initialized in-memory DB so existing valid marked-reply cross-module tests exercise the new persistence seam. Add an assertion that the fallback row exists; keep malformed and marker-only behavior unchanged.

### Step 2: Add RED MCP-path tests

Extend `TestPostMessageGrader` to prove:

- approved marked content is graded first, then fallback acknowledgment is committed before the first Slack post;
- a preexisting explicit reason remains unchanged;
- rejected content creates no acknowledgment;
- `None` DB, directive conflict, and persistence failure return a structured error with no Slack post/reaction;
- genuinely unmarked content never touches acknowledgment persistence; and
- a real failing `SlackBot` queues the exact threaded reply, retains committed classification, and creates no check reaction.

Use the real helper/DB in these tests; do not mock the persistence helper.

### Step 3: Run RED

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_daemon.py tests/test_main_validate.py tests/test_orchestrator_mcp.py -k 'SolicitedReply or reply_marker or reply_reaction or TestPostMessageGrader or transport_acknowledgement'
```

Expected: new tests fail because both transports still post valid marked replies without persistence.

### Step 4: Enforce daemon-path persistence

In `main.py`, import the shared helper and fallback constant. After parsing a valid,
non-empty marked reply and before `_post_brain_message()`, persist the exact
timestamp using the fallback reason. On missing DB or any persistence exception,
log one bounded warning and continue without posting or reacting. Preserve usage-
limit detection, parser precedence, operator-wait behavior, chunking, notification
queue behavior, heartbeat routing, validation, and reaction timing.

### Step 5: Enforce MCP-path persistence

In `orchestrator_mcp.py`, after an approved grade but before the first Slack post,
persist the exact marked timestamp with the same fallback reason. On missing DB or
any persistence exception, return a bounded structured error and post/react to
nothing. Do not persist rejected, malformed, marker-only, or unmarked messages.
Preserve cleaned-body grading, unmarked positional posting, explicit pinning, and
post-before-reaction ordering.

### Step 6: Run focused GREEN

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_daemon.py tests/test_main_validate.py tests/test_orchestrator_mcp.py -k 'SolicitedReply or reply_marker or reply_reaction or TestPostMessageGrader or transport_acknowledgement'
```

Expected: PASS, exit 0. Removing either transport call, changing order, overwriting explicit reason, bypassing conflicts, reacting after failure, or losing exact queued thread makes a named test fail.

### Step 7: Run Task 2 regressions

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_daemon.py tests/test_main_validate.py tests/test_orchestrator_mcp.py
```

Expected: PASS, exit 0.

### Step 8: Stage Task 2

```bash
git -C /Users/roberthyatt/Code/ironclaude add -- commander/src/ironclaude/main.py commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_daemon.py commander/tests/test_main_validate.py commander/tests/test_orchestrator_mcp.py
```

Expected: Task 2 files added to existing staged work; protected files remain unstaged.

---

## Task 3: Regression, restart, fresh live acceptance, and findings

**Files:**

- Modify/stage: `docs/plans/2026-08-03-commander-actionable-work-convergence-requirements.md`
- Modify/stage: `docs/plans/2026-08-03-commander-actionable-work-convergence-design.md`
- Modify/stage: `docs/plans/2026-08-03-commander-actionable-work-convergence.md`
- Modify/stage: `docs/plans/2026-08-03-commander-actionable-work-convergence.plan.json`
- Modify/stage: `docs/plans/2026-08-03-commander-actionable-work-convergence-findings.md`

**Depends on:** Task 2

### Step 1: Recheck protected scope

```bash
shasum -a 256 /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/commander/config/ironclaude.json && git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: exact protected hashes from execution invariants; both files unstaged.

### Step 2: Run combined focused regression

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_db.py tests/test_orchestrator_mcp.py tests/test_enforcement.py tests/test_main_validate.py tests/test_slack_interface.py tests/test_codex_brain_client.py tests/test_daemon.py
```

Expected: PASS, exit 0; record measured count.

### Step 3: Run full Commander suite once

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/
```

Expected: PASS, exit 0; record measured pass/skip counts.

### Step 4: Restart existing Commander

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/ironclaude restart
```

Expected: identity-checking CLI sends SIGHUP to the sole daemon. Run outside a sandbox that blocks its internal `ps`; never start a duplicate.

### Step 5: Verify topology and startup

```bash
ps -axo pid,ppid,lstart,command | rg 'ironclaude\.main|codex app-server' && tail -n 300 /tmp/ironclaude-daemon.log | rg 'Brain SDK client started|Brain Codex client started|IronClaude Commander daemon starting|Bolt app is running' && ! tail -n 300 /tmp/ironclaude-daemon.log | rg 'orchestrator MCP inventory missing tools'
```

Expected: exactly one daemon and one attached Codex Brain app-server; fresh Slack/Brain startup; no missing-tool error.

### Step 6: Run a fresh operator-message live gate

Use one newly received, operator-authored, clearly non-actionable Slack question after restart. Do not reuse `1785801581.255109`, bot-author a substitute, or synthesize operator identity/text. Verify:

- directive `#1457` remains preserved/completed with no worker;
- exactly one immutable acknowledgment row exists for the fresh exact timestamp;
- if the Brain explicitly acknowledged first, its reason is preserved; otherwise the exact fallback reason is stored;
- no directive, worker, or worker event derives from the message;
- exactly one cleaned Brain reply appears in its containing Slack thread with no marker;
- eyes/check reactions occur on the exact operator message and check follows successful reply delivery; and
- no aging or idle-enforcement nudge appears after the applicable threshold.

### Step 7: Restart after the accepted message and recheck durability

```bash
cd /Users/roberthyatt/Code/ironclaude/commander && .venv/bin/ironclaude restart
```

After fresh startup, re-query the same exact source timestamp. Expected: the same
single immutable acknowledgment/reason, no directive/worker/event, no duplicate
Brain reply or nudge, and one daemon plus one attached Brain app-server.

### Step 8: Update findings and stage workflow artifacts

Record both implementation tasks, RED/GREEN/review evidence, regressions, both live failures and final success, restarts, protected hashes, and both mandatory successor PM loops.

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f -- docs/plans/2026-08-03-commander-actionable-work-convergence-requirements.md docs/plans/2026-08-03-commander-actionable-work-convergence-design.md docs/plans/2026-08-03-commander-actionable-work-convergence.md docs/plans/2026-08-03-commander-actionable-work-convergence.plan.json docs/plans/2026-08-03-commander-actionable-work-convergence-findings.md
```

### Step 9: Final protected-scope verification

```bash
shasum -a 256 /Users/roberthyatt/Code/ironclaude/AGENTS.md /Users/roberthyatt/Code/ironclaude/commander/config/ironclaude.json && git -C /Users/roberthyatt/Code/ironclaude status --short
```

Expected: protected hashes unchanged; both files unstaged; only authorized implementation, tests, and five workflow artifacts staged; no commit and no push.
