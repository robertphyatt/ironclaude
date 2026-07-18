# v1.0.24 Adversarial Findings Remediation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `ironclaude:executing-plans` to implement this plan task-by-task.

**Goal:** Remediate all seven verified v1.0.24 findings, track Codex guidance with Boy Scout parity, verify the full release, and leave the final commit amend to the main context after execution and review complete.

**Architecture:** Five targeted tasks preserve current interfaces. Four independent TDD tasks repair instruction propagation, hook classification, authentication concurrency, and operator-wait context; a dependent release task updates documentation, stages review artifacts, and runs complete verification. Ambiguous Slack context degrades to no link, and hook enforcement remains deterministic.

**Tech Stack:** Bash 3.2-compatible shell, `jq`, Python 3.11, pytest, `unittest.mock`, Markdown/JSON plugin manifests, Git.

**Design:** `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation-design.md`

**Finding source:** `docs/reviews/2026-07-18-v1-0-24-commit-findings.md`

**Release constraint:** Stage during execution; do not commit, tag, or push. The main context performs one guarded `git commit --amend` only after execution, testing-theatre audit, and final code review complete.

---

## Task 1: Track Codex Guidance and Enforce Boy Scout Parity

**Files:**
- Modify: `AGENTS.md:50-56`
- Modify: `commander/tests/test_boy_scout_directive.py:55-61`

### Step 1: Add `AGENTS.md` to the propagation regression (RED)

In `commander/tests/test_boy_scout_directive.py`, replace `DIRECT_SURFACES` with:

```python
DIRECT_SURFACES = [
    "AGENTS.md",
    "CLAUDE.md",
    "worker/CLAUDE.md",
    "commander/CLAUDE.md",
    ".claude/rules/behavioral.md",
    "commander/src/ironclaude/templates/worker_claude_md.md",
]
```

### Step 2: Run the focused test and verify failure

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_boy_scout_directive.py::test_directive_in_every_direct_behavioral_surface -q
```

Expected: FAIL containing `Missing Boy Scout Rule in AGENTS.md`.

### Step 3: Add the complete Boy Scout directive to `AGENTS.md` (GREEN)

After principle 9 and before `## Plan Mode Replacement`, add:

```markdown
10. **Boy Scout Rule — Leave It Better Than You Found It**
    - Never dismiss an evidence-backed defect because it is pre-existing, adjacent, or outside the immediate change
    - If cleanup is safe, relevant, and within the authorized task scope, fix it through the active workflow and verify the result
    - If cleanup would materially expand scope, change behavior, require destructive action, affect external systems, or require new authority, describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding
    - If cleanup is blocked or unsafe, record the finding and explain the constraint instead of suppressing it
    - Do not use this rule to justify speculative refactoring or unrequested features
```

Preserve the existing Codex-specific title and Plan Mode Replacement text.

### Step 4: Run focused parity tests

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_boy_scout_directive.py commander/tests/test_worker_claude_md_template.py -q
```

Expected: all tests pass.

### Step 5: Stage the task

Run:

```bash
git add AGENTS.md commander/tests/test_boy_scout_directive.py
```

Expected: both paths appear in `git diff --cached --name-only`; no commit is created.

---

## Task 2: Preserve Multiline Hook Text and Cover Common Proposal Grammar

**Files:**
- Modify: `worker/hooks/subagent-drift-detector.sh:11-23`
- Modify: `worker/hooks/hook-logger.sh:222-245`
- Modify: `worker/hooks/tests/test-antipattern-lexicon.sh:44-60`
- Modify: `worker/hooks/tests/test-gbtw-antipattern-override.sh:43-60`
- Modify: `worker/hooks/tests/test-sad-antipattern.sh:74-104`

### Step 1: Add hook regressions (RED)

Append these cases before the summary in `worker/hooks/tests/test-antipattern-lexicon.sh`:

```bash
check J "Would you like me to checkpoint here?"                                  true
check K "Do you want me to pause here?"                                          true
check L "Could we find a natural stopping point?"                                true
check M "May I bank progress and resume fresh?"                                  true
```

Append this case before the summary in `worker/hooks/tests/test-gbtw-antipattern-override.sh`:

```bash
check 11 executing "Would you like me to checkpoint here?"                        true
```

In `worker/hooks/tests/test-sad-antipattern.sh`, insert a multiline fixture after fixture 2 and replace the missing-path/final assertion block with:

```bash
# Fixture 3: multiline final assistant block is preserved as one text value
T3="${TMPDIR_TEST}/t3.jsonl"
MULTILINE=$(printf 'Shall we checkpoint here?\nWaiting on your answer.')
build_transcript "$T3" "$MULTILINE"
check_text_extract         3a "$T3" "$MULTILINE"
check_predicate_end_to_end 3b "$T3" true

# Fixture 4: missing / unreadable transcript path — helper returns empty, no crash
T4="${TMPDIR_TEST}/does-not-exist.jsonl"
got=$(_sad_last_assistant_text "$T4" || echo "CRASH")
if [ "$got" = "" ]; then
  printf 'PASS  4 (missing transcript → empty output, no crash)\n'
  PASSES=$((PASSES + 1))
else
  printf 'FAIL  4 (expected empty output, got %q)\n' "$got"
  FAILS=$((FAILS + 1))
fi

# Fixture 5: malformed JSONL fails open with empty output
T5="${TMPDIR_TEST}/malformed.jsonl"
printf '{"type":' > "$T5"
got=$(_sad_last_assistant_text "$T5" || echo "CRASH")
if [ "$got" = "" ]; then
  printf 'PASS  5 (malformed transcript → empty output, no crash)\n'
  PASSES=$((PASSES + 1))
else
  printf 'FAIL  5 (expected empty output, got %q)\n' "$got"
  FAILS=$((FAILS + 1))
fi

# Fixture 6: unreadable transcript fails open with empty output
T6="${TMPDIR_TEST}/unreadable.jsonl"
build_transcript "$T6" "Shall we checkpoint here?"
chmod 000 "$T6"
got=$(_sad_last_assistant_text "$T6" || echo "CRASH")
chmod 600 "$T6"
if [ "$got" = "" ]; then
  printf 'PASS  6 (unreadable transcript → empty output, no crash)\n'
  PASSES=$((PASSES + 1))
else
  printf 'FAIL  6 (expected empty output, got %q)\n' "$got"
  FAILS=$((FAILS + 1))
fi

EXPECTED_PASSES=9
echo
printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
if [ "$FAILS" -ne 0 ] || [ "$PASSES" -ne "$EXPECTED_PASSES" ]; then
  printf 'FAIL: expected %d completed checks\n' "$EXPECTED_PASSES"
  exit 1
fi
```

### Step 2: Run the hook regressions and verify failure

Run each command individually, stopping on the first nonzero exit:

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```

```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```

```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```

Expected: lexicon cases J-M fail, GBTW case 11 fails, and SAD fixtures 3a/3b fail because only the physical last line is returned. The new malformed and unreadable fixtures pass fail-open behavior.

### Step 3: Preserve the complete final assistant text block (GREEN)

Replace `_sad_last_assistant_text` in `worker/hooks/subagent-drift-detector.sh` with:

```bash
_sad_last_assistant_text() {
    local transcript="${1:-}"
    if [ -z "$transcript" ] || [ ! -r "$transcript" ]; then
        return 0
    fi
    tail -50 "$transcript" \
        | jq -s -r '[.[] | select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text // empty] | last // empty' 2>/dev/null
}
```

Update its comment to say “final assistant text content block,” not “content line.”

### Step 4: Separate proposal framing from checkpoint actions (GREEN)

Replace the checkpoint lexicon and `_ic_is_antipattern_proposal` in `worker/hooks/hook-logger.sh` with:

```bash
_IC_CHECKPOINT_PROPOSAL_FRAME='(shall|should|can|could|may) (we|I)|(would you like|do you want) (me|us) to|let'\''s|let me'
_IC_CHECKPOINT_ACTION='checkpoint|bank progress|bank the|resume fresh|pause here|find a (safe|natural) stopping point'
_IC_CHECKPOINT_QUESTION='safe stopping point\?|natural stopping point\?|checkpoint and (resume|continue) fresh'
_IC_QUERY_OFFLOAD_LEXICON='(you|operator) (run|paste|type|execute) (these|the|those|this) (queries|commands|sqlite|grep|bash)( yourself)?|after a ! and paste|professional mode makes this a you-action|(these|the|those) (queries|commands) (yourself|are (yours|your action))'
_IC_META_DISCUSSION='^[[:space:]]*(#|> |\|)|^```'

_ic_is_antipattern_proposal() {
  local text="$1" line
  while IFS= read -r line || [ -n "$line" ]; do
    if printf '%s\n' "$line" | grep -qE "$_IC_META_DISCUSSION"; then
      continue
    fi
    if printf '%s\n' "$line" | grep -qiE "$_IC_QUERY_OFFLOAD_LEXICON"; then
      printf 'true'
      return 0
    fi
    if printf '%s\n' "$line" | grep -qiE "$_IC_CHECKPOINT_PROPOSAL_FRAME" \
       && printf '%s\n' "$line" | grep -qiE "$_IC_CHECKPOINT_ACTION"; then
      printf 'true'
      return 0
    fi
    if printf '%s\n' "$line" | grep -qiE "$_IC_CHECKPOINT_QUESTION"; then
      printf 'true'
      return 0
    fi
  done <<< "$text"
  printf 'false'
}
```

Keep the existing comments explaining line-scoped meta discussion and shared consumption by GBTW/SAD.

### Step 5: Run focused and syntax checks

Run each command individually, stopping on the first nonzero exit:

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```

```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```

```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```

```bash
bash -n worker/hooks/hook-logger.sh worker/hooks/get-back-to-work-claude.sh worker/hooks/subagent-drift-detector.sh
```

Expected: all cases pass and `bash -n` exits 0.

### Step 6: Stage the task

Run:

```bash
git add worker/hooks/hook-logger.sh worker/hooks/subagent-drift-detector.sh worker/hooks/tests/test-antipattern-lexicon.sh worker/hooks/tests/test-gbtw-antipattern-override.sh worker/hooks/tests/test-sad-antipattern.sh
```

Expected: five paths staged; no commit created.

---

## Task 3: Close the Auth Relay Submission/Re-prompt Race

**Files:**
- Modify: `commander/src/ironclaude/auth_relay.py:124-136,157-202`
- Modify: `commander/tests/test_auth_relay.py:100-130,186-207`

### Step 1: Add deterministic race and rollback regressions (RED)

Add these tests after `test_needs_code_after_reprompt` in `commander/tests/test_auth_relay.py`:

```python
    def test_reprompt_during_submit_is_not_lost(self):
        proc = FakeProc(lines=["Paste code here if prompted >\n"])
        relay = _mk(proc)
        relay.start(); _drain(relay)

        class RacingStdin:
            def write(self, value):
                reprompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(reprompt, gen=relay._gen)
                return len(value)

            def flush(self):
                pass

        proc.stdin = RacingStdin()
        assert relay.submit_code("CODE-1") == "sent"
        assert relay.tick() == {"state": "needs_code"}
        assert relay._code_submitted_at is None

    def test_delayed_initial_prompt_during_submit_is_not_a_reprompt(self):
        proc = FakeProc(lines=[])
        relay = _mk(proc)
        relay.start(); _drain(relay)

        class InitialPromptStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)

            def flush(self):
                pass

        proc.stdin = InitialPromptStdin()
        assert relay.submit_code("CODE-1") == "sent"
        assert relay.tick() is None
        assert relay._code_submitted_at is not None
```

Extend `test_submit_code_failed_on_write_error` with:

```python
        assert relay._code_submitted_at is None
        assert relay._last_feedback_ts is None
        assert relay._needs_code is False
```

Add a flush-failure regression beside the write-failure test:

```python
    def test_submit_code_failed_on_flush_rolls_back_state(self):
        class FlushFailStdin:
            def write(self, value):
                return len(value)

            def flush(self):
                raise OSError("flush failed")

        proc = FakeProc(lines=[])
        proc.stdin = FlushFailStdin()
        relay = _mk(proc)
        relay.start(); _drain(relay)

        assert relay.submit_code("CODE-1") == "failed"
        assert relay._code_submitted_at is None
        assert relay._last_feedback_ts is None
        assert relay._needs_code is False
```

Add a flush-failure race regression proving rollback preserves a concurrently observed re-prompt:

```python
    def test_submit_code_flush_failure_preserves_concurrent_reprompt(self):
        proc = FakeProc(lines=["Paste code here if prompted >\n"])
        relay = _mk(proc)
        relay.start(); _drain(relay)

        class RePromptThenFlushFailStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)

            def flush(self):
                raise OSError("flush failed")

        proc.stdin = RePromptThenFlushFailStdin()
        assert relay.submit_code("CODE-1") == "failed"
        assert relay._code_submitted_at is None
        assert relay.tick() == {"state": "needs_code"}
```

Add a second-session regression proving `start()` resets the initial-prompt marker:

```python
    def test_new_login_resets_initial_prompt_tracking(self):
        first = FakeProc(lines=["Paste code here if prompted >\n"], returncode=0)
        second = FakeProc(lines=[])
        procs = iter([first, second])
        relay = AuthRelay(spawn=lambda: next(procs), status=lambda: "acct", now=lambda: 1000.0)

        relay.start(); _drain(relay)
        assert relay._paste_prompt_seen is True
        assert relay.tick() == {"state": "already_logged_in", "account": "acct"}

        relay.start(); _drain(relay)

        class InitialPromptStdin:
            def write(self, value):
                prompt = type("P", (), {"stdout": io.StringIO("Paste code here if prompted >\n")})()
                relay._read_loop(prompt, gen=relay._gen)
                return len(value)

            def flush(self):
                pass

        second.stdin = InitialPromptStdin()
        assert relay.submit_code("CODE-2") == "sent"
        assert relay.tick() is None
```

### Step 2: Run the focused tests and verify failure

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_auth_relay.py::TestAuthRelay::test_reprompt_during_submit_is_not_lost commander/tests/test_auth_relay.py::TestAuthRelay::test_delayed_initial_prompt_during_submit_is_not_a_reprompt commander/tests/test_auth_relay.py::TestAuthRelay::test_new_login_resets_initial_prompt_tracking commander/tests/test_auth_relay.py::TestAuthRelay::test_submit_code_failed_on_write_error commander/tests/test_auth_relay.py::TestAuthRelay::test_submit_code_failed_on_flush_rolls_back_state commander/tests/test_auth_relay.py::TestAuthRelay::test_submit_code_flush_failure_preserves_concurrent_reprompt -q
```

Expected: the rejected-code race test fails because `tick()` returns `None`; the delayed-initial-prompt test protects against fixing the false negative by creating an inverse false positive; the rollback assertions protect both write and flush failures.

### Step 3: Distinguish the initial prompt, then publish and roll back submission state under the relay lock (GREEN)

In `__init__`, add:

```python
self._paste_prompt_seen = False
```

Reset it to `False` with the other shared login state in `start()`.

Replace the reader's paste-prompt check with:

```python
                    if "Paste code here" in line:
                        if self._paste_prompt_seen and self._code_submitted_at is not None:
                            self._needs_code = True
                        self._paste_prompt_seen = True
```

Replace `submit_code` in `commander/src/ironclaude/auth_relay.py` with:

```python
    def submit_code(self, code: str) -> str:
        """'idle' if no login subprocess is live, 'sent' on write, 'failed' on write error."""
        if self._proc is None or self._proc.stdin is None:
            return "idle"
        submitted_at = self._now()
        with self._lock:
            self._code_submitted_at = submitted_at
            self._last_feedback_ts = None
            self._needs_code = False
        try:
            self._proc.stdin.write(code + "\n")
            self._proc.stdin.flush()
            return "sent"
        except Exception as exc:
            with self._lock:
                if self._code_submitted_at == submitted_at:
                    self._code_submitted_at = None
                    self._last_feedback_ts = None
            logger.warning("submit_code failed: %s", exc)
            return "failed"
```

Do not overwrite `_needs_code` in the exception path. It was set to `False` before delivery, so ordinary write/flush failures remain clean, while a reader-observed re-prompt during delivery remains `True` for `tick()`.

In `tick()`, replace the waiting-feedback block with:

```python
        with self._lock:
            if self._code_submitted_at is not None:
                now = self._now()
                since_submit = now - self._code_submitted_at
                since_feedback = (now - self._last_feedback_ts) if self._last_feedback_ts else since_submit
                if since_submit > self.WAITING_FEEDBACK_INTERVAL_S and since_feedback >= self.WAITING_FEEDBACK_INTERVAL_S:
                    self._last_feedback_ts = now
                    return {"state": "waiting"}
```

The existing reader and `_needs_code` branch already use `_lock`; preserve them unchanged.

### Step 4: Run the complete relay tests and compile check

Run each command individually, stopping on the first nonzero exit:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_auth_relay.py -q
```

```bash
commander/.venv/bin/python -m compileall -q commander/src/ironclaude/auth_relay.py
```

Expected: all relay tests pass; compileall exits 0.

### Step 5: Stage the task

Run:

```bash
git add commander/src/ironclaude/auth_relay.py commander/tests/test_auth_relay.py
```

Expected: both paths staged; no commit created.

---

## Task 4: Associate Operator-Wait Links Conservatively and Clean Slack Output

**Files:**
- Modify: `commander/src/ironclaude/main.py:695-699,1011-1012,1498-1532`
- Modify: `commander/tests/test_main_operator_wait.py:7-86`
- Modify: `commander/tests/test_main_validate.py:396-410,858-866`

### Step 1: Replace timestamp-only tests with evidence-bearing context tests (RED)

In both test helpers, replace initialization of `_last_brain_post_ts` with:

```python
daemon._last_brain_context = None
```

(`d._last_brain_context = None` in `test_main_validate.py`.)

Update the successful permalink test to seed:

```python
daemon._last_brain_context = ("9999.0001", "Status for d1267: choose approach A or B")
```

Retain the assertion that `get_permalink` receives `9999.0001`.

Seed the same matching candidate in `test_operator_wait_skips_update_when_permalink_returns_none`:

```python
daemon._last_brain_context = ("9999.0001", "Status for d1267: choose approach A or B")
```

This keeps the test non-vacuous: it must reach `get_permalink`, receive `None`, and skip only `update_message`.

Add these tests to `commander/tests/test_main_operator_wait.py`:

```python
    def test_operator_wait_omits_link_for_unrelated_context(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting(worker_id="d1267")
        daemon._last_brain_context = ("9999.0001", "Status for d9999: unrelated work")
        daemon.slack.post_message.return_value = "1234.5678"

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_operator_wait_for_brain_is_always_linkless(self):
        daemon = _make_daemon()
        daemon._grader.grade.return_value = _grade_awaiting(worker_id=None)
        daemon._last_brain_context = ("9999.0001", "Brain decision context")
        daemon.slack.post_message.return_value = "1234.5678"

        daemon._maybe_capture_operator_wait("Still holding, awaiting your decision")

        daemon.slack.get_permalink.assert_not_called()
        daemon.slack.update_message.assert_not_called()

    def test_threaded_chatter_does_not_replace_top_level_context(self):
        daemon = _make_daemon()
        daemon.slack.post_message.side_effect = ["decision-ts", "chatter-ts"]

        daemon._post_brain_message("Decision context for d1267")
        daemon._post_brain_message("Unrelated chatter", thread_ts="heartbeat-ts")

        assert daemon._last_brain_context == ("decision-ts", "Decision context for d1267")

    def test_partial_top_level_delivery_does_not_replace_context(self):
        daemon = _make_daemon()
        daemon._last_brain_context = ("old-ts", "Decision context for d1267")
        daemon.slack.post_message.side_effect = ["first-ts", None]

        result = daemon._post_brain_message("x" * 39001)

        assert result is None
        assert daemon._last_brain_context == ("old-ts", "Decision context for d1267")
```

Rename `test_post_brain_message_updates_last_brain_post_ts` to `test_post_brain_message_tracks_complete_top_level_context` and assert:

```python
assert daemon._last_brain_context == ("1111.2222", "hello")
```

Extend `test_login_tick_verify_failed_no_restart` in `commander/tests/test_main_validate.py` with:

```python
    assert "review I1" not in _posts(d)
```

### Step 2: Run focused tests and verify failure

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_main_operator_wait.py commander/tests/test_main_validate.py::test_login_tick_verify_failed_no_restart -q
```

Expected: failures because production still reads `_last_brain_post_ts`, threaded posts overwrite it, partial delivery records it, and Slack output contains `review I1`.

### Step 3: Store eligible context and require worker association (GREEN)

In `IroncladeDaemon.__init__`, replace `_last_brain_post_ts` with:

```python
self._last_brain_context: tuple[str, str] | None = None   # fully delivered top-level (ts, text) eligible for operator-wait links
```

In `_maybe_capture_operator_wait`, replace permalink enrichment with:

```python
            ts = self.slack.post_message(alert_body)
            candidate = self._last_brain_context
            if ts and candidate and worker_id != "brain":
                context_ts, context_text = candidate
                worker_token = re.compile(
                    rf"(?<![A-Za-z0-9_-]){re.escape(worker_id)}(?![A-Za-z0-9_-])"
                )
                if worker_token.search(context_text):
                    permalink = self.slack.get_permalink(context_ts)
                    if permalink:
                        self.slack.update_message(ts, f"{self.slack.prefix}{alert_body}\nLink: {permalink}")
```

At the end of `_post_brain_message`, replace timestamp tracking with:

```python
        if all_ok and first_ts is not None and thread_ts is None:
            self._last_brain_context = (first_ts, text)
        return first_ts if all_ok else None
```

### Step 4: Remove the internal Slack annotation (GREEN)

Replace the `verify_failed` post text with:

```python
self.slack.post_message("Signed in, but I couldn't confirm the account after retries — the switch may or may not have completed. Not restarting; send `login` again to be sure.")
```

### Step 5: Run focused and compile checks

Run each command individually, stopping on the first nonzero exit:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_main_operator_wait.py commander/tests/test_main_validate.py -q
```

```bash
commander/.venv/bin/python -m compileall -q commander/src/ironclaude/main.py
```

Expected: all focused tests pass; compileall exits 0.

### Step 6: Stage the task

Run:

```bash
git add commander/src/ironclaude/main.py commander/tests/test_main_operator_wait.py commander/tests/test_main_validate.py
```

Expected: three paths staged; no commit created.

---

## Task 5: Align Release Documentation, Stage Artifacts, and Verify v1.0.24

**Depends on:** Tasks 1-4

**Files:**
- Modify: `README.md:22-31`
- Modify: `CHANGELOG.md:16-40`
- Stage existing: `docs/reviews/2026-07-18-v1-0-24-commit-findings.md`
- Stage existing: `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation-design.md`
- Stage existing: `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.md`
- Stage existing: `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.plan.json`

**No executable-code test is required for the prose edits.** Existing behavior and release verification suites validate the documented claims.

### Step 1: Update README release bullets

In `README.md`'s v1.0.24 list:

- Extend the Boy Scout bullet to state that tracked root `AGENTS.md` carries the Codex repository guidance and is covered by the propagation guard.
- Replace the operator-wait wording with: `Operator-wait alerts link only to a fully delivered top-level Brain message verified to reference the same worker; otherwise the alert is posted without a potentially misleading link.`
- Extend the hook bullet to state that complete multiline assistant blocks and common permission-request forms are covered.
- Keep the direct Worker Codex / Commander Claude Code-only compatibility note unchanged.

### Step 2: Update CHANGELOG v1.0.24 claims

Make these exact semantic updates under v1.0.24:

```markdown
- **Scope-aware Boy Scout Rule.** Every current and generated behavioral-instruction surface, including tracked root `AGENTS.md` for Codex repository guidance, rejects “pre-existing” as a reason for silence: clean up evidence-backed defects within authorized scope; otherwise describe the finding, evidence, proposed cleanup scope, and risk and ask permission. Blocked or unsafe findings are recorded rather than suppressed. A propagation guard covers every listed surface.
```

Replace the operator-wait Changed bullet with:

```markdown
- **Operator-wait links now require matching decision context.** Commander retains only fully delivered top-level Brain posts as link candidates and adds a permalink only when the candidate references the same worker extracted from the wait. Threaded chatter, partial deliveries, unrelated workers, and missing context produce a linkless alert rather than a misleading link.
```

Add Fixed bullets that state:

```markdown
- **Workflow-avoidance enforcement preserves multiline text and ordinary proposal grammar.** SubagentStop now classifies the complete final assistant text block, and the shared deterministic predicate recognizes common permission forms such as “Would you like me…”, “Do you want me…”, “Could we…”, and “May I…” while retaining line-scoped documentation exemptions.
- **Slack login no longer loses an immediate rejected-code re-prompt.** Submission state is published before stdin delivery under the relay synchronization boundary and rolled back on delivery failure, so a concurrent CLI re-prompt reliably asks the operator for a fresh code.
- **Hook regression harness fails on missing assertion paths.** The SubagentStop shell test rejects unexpected output and requires its exact expected pass count instead of allowing an unentered conditional to exit successfully.
```

### Step 3: Run focused remediation verification

Run each command individually, stopping on the first nonzero exit:

```bash
commander/.venv/bin/python -m pytest commander/tests/test_boy_scout_directive.py commander/tests/test_worker_claude_md_template.py commander/tests/test_auth_relay.py commander/tests/test_main_operator_wait.py commander/tests/test_main_validate.py -q
```

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```

```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```

```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```

Expected: all focused tests pass with no warnings or hidden errors.

### Step 4: Run syntax, plugin, and version checks

Run each command individually, stopping on the first nonzero exit:

```bash
bash -n worker/hooks/hook-logger.sh worker/hooks/get-back-to-work-claude.sh worker/hooks/subagent-drift-detector.sh
```

```bash
commander/.venv/bin/python -m compileall -q commander/src/ironclaude/auth_relay.py commander/src/ironclaude/main.py commander/src/ironclaude/slack_interface.py
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_version_consistency.py -q
```

```bash
python3 /Users/roberthyatt/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py worker
```

Expected: every command exits 0. Any failure stops the task; do not mutate marketplace configuration or add a cachebuster during source verification.

### Step 5: Run every hook test

Run these exact tests individually, stopping on the first nonzero exit:

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```

```bash
bash worker/hooks/tests/test-bash-readonly-guard.sh
```

```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```

```bash
bash worker/hooks/tests/test-gbtw-inflight.sh
```

```bash
bash worker/hooks/tests/test-gbtw-waiting.sh
```

```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```

Expected: all six scripts exit 0; capture every reported result count.

### Step 6: Run the complete Commander suite

Run:

```bash
commander/.venv/bin/python -m pytest commander/tests -q
```

Expected: exit 0 with no failed tests. Preserve any existing intentional skip and report its identity.

### Step 7: Audit changed tests for testing theatre

Inspect these exact files:

```text
commander/tests/test_boy_scout_directive.py
commander/tests/test_auth_relay.py
commander/tests/test_main_operator_wait.py
commander/tests/test_main_validate.py
worker/hooks/tests/test-antipattern-lexicon.sh
worker/hooks/tests/test-gbtw-antipattern-override.sh
worker/hooks/tests/test-sad-antipattern.sh
```

Verify: no skips; every test has an unconditional behavioral assertion; shell checks increment exactly one counter or exit nonzero; mocks drive the real production method; RED evidence exists for each behavior. Record the audit result in the execution summary.

### Step 8: Stage documentation and ignored workflow artifacts

Run:

```bash
git add README.md CHANGELOG.md
git add -f docs/reviews/2026-07-18-v1-0-24-commit-findings.md docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation-design.md docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.md docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.plan.json
```

Expected: all release and workflow artifacts staged; no commit created.

### Step 9: Inspect the complete staged change

Run each command individually, stopping on the first nonzero exit:

```bash
git diff --cached --check
```

```bash
git diff --cached --stat
```

```bash
git status --short
```

```bash
git rev-parse HEAD
```

Expected: diff check exits 0; HEAD is still `c923949edf1a92219996653043e5b603e1eb99d7`; no unexpected path is staged; nothing has been pushed.

---

## Task 6: Final Review and Release Handoff

**Depends on:** Task 5

**Files:**
- Read/review: every staged remediation path
- Stage existing: `docs/reviews/2026-07-18-v1-0-24-commit-findings.md`
- Stage existing: `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation-design.md`
- Stage existing: `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.md`
- Stage existing: `docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.plan.json`

**No production files are modified in this task.** It is the final review and handoff gate before execution completes.

### Step 1: Run the final code review

Invoke `ironclaude:code-review` against the complete staged remediation and require a passing grade. Review every finding closure against the adversarial report and approved design. Any finding reopens the relevant task; do not proceed to release handoff until review passes.

### Step 2: Re-run finding-specific probes

Run each exact command individually, stopping on the first nonzero exit:

```bash
bash worker/hooks/tests/test-sad-antipattern.sh
```

```bash
bash worker/hooks/tests/test-antipattern-lexicon.sh
```

```bash
bash worker/hooks/tests/test-gbtw-antipattern-override.sh
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_auth_relay.py::TestAuthRelay::test_reprompt_during_submit_is_not_lost commander/tests/test_auth_relay.py::TestAuthRelay::test_delayed_initial_prompt_during_submit_is_not_a_reprompt commander/tests/test_auth_relay.py::TestAuthRelay::test_new_login_resets_initial_prompt_tracking commander/tests/test_auth_relay.py::TestAuthRelay::test_submit_code_flush_failure_preserves_concurrent_reprompt -q
```

```bash
commander/.venv/bin/python -m pytest commander/tests/test_main_operator_wait.py::TestOperatorWaitPermalink::test_operator_wait_omits_link_for_unrelated_context commander/tests/test_main_operator_wait.py::TestOperatorWaitPermalink::test_operator_wait_for_brain_is_always_linkless commander/tests/test_main_operator_wait.py::TestPostBrainMessageTracksLastTs::test_threaded_chatter_does_not_replace_top_level_context commander/tests/test_main_operator_wait.py::TestPostBrainMessageTracksLastTs::test_partial_top_level_delivery_does_not_replace_context -q
```

Expected: SAD reports 9 passes; proposal cases J-M are true; GBTW reports 11 true cases; all four auth race/reset/rollback tests pass; all four Slack association tests pass.

### Step 3: Verify the final staged tree

Run each command individually:

```bash
git diff --cached --check
```

```bash
git diff --cached --name-only
```

```bash
git rev-parse HEAD
```

Expected: no whitespace errors, only authorized paths staged, and HEAD remains `c923949edf1a92219996653043e5b603e1eb99d7`.

### Step 4: Complete execution without committing

Submit Task 6 for its task-boundary review. Once that review passes, allow executing-plans to reach `execution_complete`. Do not run `git commit`, create a tag, reinstall, or push while Task 6 is active.

### Step 5: Main-context-only handoff

After execution is complete, the main context must perform the exact guarded amend, amended-tree verification, ahead/remote check, and separate reinstall boundary in the following Post-Execution Release Action. This deferred handoff is part of the machine plan source of truth but is intentionally not executed inside an active plan task.

---

## Post-Execution Release Action — Main Context Only

Do not perform this section from an executing-plan task. Complete all task review gates, testing-theatre audit, final code review, and execution-complete transition first.

### Release Step 1: Confirm the amend target and staged scope

Run:

```bash
git rev-parse HEAD
git status --short
git diff --cached --name-only
```

Expected: HEAD is exactly `c923949edf1a92219996653043e5b603e1eb99d7`, every intended remediation/artifact path is staged, and no unrelated path is staged.

### Release Step 2: Amend v1.0.24 with the final message

Run only after Release Step 1 passes:

```bash
git commit --amend \
  -m "v1.0.24: workflow durability, Codex compatibility, and reliability hardening" \
  -m "Add durable workflow enforcement and native direct-mode OpenAI Codex packaging; track Codex AGENTS.md guidance with scope-aware Boy Scout parity; preserve multiline workflow-avoidance detection; close Commander login and operator-context races; and keep restricted-runner tests hermetic." \
  -m "Verification: complete Commander suite, every hook test, focused regression and propagation tests, testing-theatre audit, version consistency, and Codex plugin validation." \
  -m "Commander remains Claude Code-only. Commit amended locally; not pushed."
```

Expected: one amended v1.0.24 commit replaces `c923949`; no new second commit exists.

### Release Step 3: Verify the amended tree and no-push state

Run:

```bash
git log -1 --format=fuller
git status --short
git diff HEAD^ --check
git rev-list --left-right --count origin/main...HEAD
git status -sb
```

Expected: commit message matches the release; worktree is clean; HEAD is one local commit ahead of `origin/main` (subject to the repository's pre-existing remote state); no push occurs.

Verify that all formerly untracked workflow artifacts are committed in the amended tree:

```bash
git ls-tree -r --name-only HEAD -- AGENTS.md docs/reviews/2026-07-18-v1-0-24-commit-findings.md docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation-design.md docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.md docs/plans/2026-07-18-v1-0-24-adversarial-findings-remediation.plan.json
```

Expected: all five paths are printed.

Verify the amended Git objects contain the central remediation markers:

```bash
git show HEAD:AGENTS.md | rg -q "Boy Scout Rule"
```

```bash
git show HEAD:worker/hooks/subagent-drift-detector.sh | rg -q "jq -s -r"
```

```bash
git show HEAD:commander/src/ironclaude/auth_relay.py | rg -q "_paste_prompt_seen"
```

```bash
git show HEAD:commander/src/ironclaude/main.py | rg -q "_last_brain_context"
```

Expected: every command exits 0, proving verification is against committed objects rather than only the pre-amend index or worktree.

### Release Step 4: Defer reinstall to the requested post-change boundary

Do not hand-edit marketplace configuration and do not add a Codex cachebuster to the source-controlled release. Current `ironclaude` marketplace is configured from `https://github.com/robertphyatt/ironclaude.git`, so this unpushed local amendment cannot be reinstalled through it yet. This task must stop after committed-object verification without pushing or reinstalling.

After a separately authorized deployment makes v1.0.24 available from that configured Git marketplace, run each exact command individually:

```bash
codex plugin marketplace upgrade ironclaude
```

```bash
codex plugin add ironclaude@ironclaude
```

```bash
codex plugin list
```

Expected: `ironclaude@ironclaude` reports `installed, enabled`, version `1.0.24`, from the refreshed `ironclaude` marketplace snapshot. Then start a new Codex task so updated skills and MCP tools load. If the version remains `1.0.23`, stop—the deployment is not visible, and reinstall must not be represented as successful.
