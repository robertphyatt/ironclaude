# Adversarial Review: v1.0.24 Commit

> **Date:** 2026-07-18
> **Scope:** 41 files — `.claude-plugin/marketplace.json`, `.claude/rules/behavioral.md`, `CHANGELOG.md`, `CLAUDE.md`, `README.md`, `commander/CLAUDE.md`, `commander/pyproject.toml`, `commander/src/brain/orchestrator_claude.md`, `commander/src/brain/rules/behavioral.md`, `commander/src/ironclaude/auth_relay.py`, `commander/src/ironclaude/main.py`, `commander/src/ironclaude/slack_interface.py`, `commander/src/ironclaude/templates/worker_claude_md.md`, `commander/tests/test_advisor_fallback_directive.py`, `commander/tests/test_auth_relay.py`, `commander/tests/test_boy_scout_directive.py`, `commander/tests/test_kill_orphan_workers.py`, `commander/tests/test_main_operator_wait.py`, `commander/tests/test_main_validate.py`, `commander/tests/test_slack_commands.py`, `commander/tests/test_version_consistency.py`, `commander/tests/test_wiki_server.py`, `commander/tests/test_worker_claude_md_template.py`, `docs/plans/2026-07-18-boy-scout-rule-and-hermetic-tests-design.md`, `docs/plans/2026-07-18-boy-scout-rule-and-hermetic-tests.md`, `docs/plans/2026-07-18-boy-scout-rule-and-hermetic-tests.plan.json`, `worker/.claude-plugin/plugin.json`, `worker/.codex-plugin/plugin.json`, `worker/CLAUDE.md`, `worker/hooks/get-back-to-work-claude.sh`, `worker/hooks/hook-logger.sh`, `worker/hooks/hooks.json`, `worker/hooks/subagent-drift-detector.sh`, `worker/hooks/tests/test-antipattern-lexicon.sh`, `worker/hooks/tests/test-gbtw-antipattern-override.sh`, `worker/hooks/tests/test-sad-antipattern.sh`, `worker/skills/activate-professional-mode/SKILL.md`, `worker/skills/brainstorming/SKILL.md`, `worker/skills/code-review/SKILL.md`, `worker/skills/executing-plans/SKILL.md`, `worker/skills/workflow-durability/SKILL.md`
> **Criteria:** broad — Broken code, integration bugs, immersion breakers, DRY violations, clumsy architecture, data flow issues, dead code, error handling failures
> **Method:** Sequential single-context read in dependency order. Cross-reference map built across all files. Each reported behavior was confirmed from exact source context and a focused runtime probe or Git-object check; focused existing tests and syntax checks were then run unchanged.

---

## Systemic Pattern: Lossy Text Classification at Enforcement Boundaries

`HK-01` and `HK-02` share one root problem: hook enforcement does not classify the complete language surface it claims to cover. One path discards all but the final physical line of a multiline assistant block; the shared predicate recognizes only a small set of grammatical openings. Both failures become false negatives precisely where the surrounding Stop-hook logic suppresses its general continuation check. The tests exercise recognized phrases, but not realistic variations or multiline placement.

---

## Important (Immersion-Breaking / Significant Bugs)

### PKG-01 — `CHANGELOG.md:27` — The release omits an existing Codex instruction surface

The release states that every current and generated behavioral-instruction surface carries the Boy Scout rule:

```markdown
- **Scope-aware Boy Scout Rule.** Every current and generated behavioral-instruction surface now rejects “pre-existing” as a reason for silence: clean up evidence-backed defects within authorized scope; otherwise describe the finding, evidence, proposed cleanup scope, and risk and ask permission. Blocked or unsafe findings are recorded rather than suppressed.
```

That claim is contradicted by the repository state. `AGENTS.md` existed before the release commit, is not ignored, remains untracked, is absent from both `c923949` and its parent, and contains no Boy Scout directive:

```text
?? AGENTS.md
commit_has_AGENTS_exit=128
parent_has_AGENTS_exit=128
created=2026-07-18 15:35:33 -0500
committed=2026-07-18T19:25:50-05:00
```

Impact chain: release assembly excludes the repository's Codex-specific behavioral instructions → clones and installs derived from `c923949` cannot receive that root instruction surface → Codex contributors do not receive the promised Boy Scout behavior there → the release claim and cross-client behavioral parity are false.

---

### HK-01 — `worker/hooks/subagent-drift-detector.sh:15-22` — Multiline assistant text is truncated before classification

```bash
_sad_last_assistant_text() {
    local transcript="${1:-}"
    if [ -z "$transcript" ] || [ ! -r "$transcript" ]; then
        return 0
    fi
    tail -50 "$transcript" \
        | jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text // empty' 2>/dev/null \
        | tail -1
}
```

`jq -r` emits embedded newlines from `.text`, so `tail -1` selects the last physical line rather than the last assistant text block. A probe with `"Shall we checkpoint here?\nWaiting on your answer."` extracted only `Waiting on your answer.` and classified it as `false`.

Impact chain: a subagent puts the prohibited proposal anywhere except the final physical line → extraction removes the proposal → `_ic_is_antipattern_proposal` receives only the trailing line → the SubagentStop hook does not call `block_stop` → the prohibited checkpoint/query-offload proposal reaches the parent despite the release's five-stage enforcement claim.

---

### HK-02 — `worker/hooks/hook-logger.sh:225-245` — The shared lexicon misses ordinary permission-request grammar

```bash
_IC_CHECKPOINT_LEXICON='(shall|should|can|let'\''s|let me) (we |I )?(checkpoint|bank progress|bank the|resume fresh|pause here|find a (safe|natural) stopping point)|safe stopping point\?|natural stopping point\?|checkpoint and (resume|continue) fresh'
_IC_QUERY_OFFLOAD_LEXICON='(you|operator) (run|paste|type|execute) (these|the|those|this) (queries|commands|sqlite|grep|bash)( yourself)?|after a ! and paste|professional mode makes this a you-action|(these|the|those) (queries|commands) (yourself|are (yours|your action))'
# Meta-discussion prefixes: heading `#`, blockquote `> `, table row `|`, code fence ```
# NOTE: bare `- ` bullets are NOT meta (option bullets like `- Yes / - No` must not escape).
_IC_META_DISCUSSION='^[[:space:]]*(#|> |\|)|^```'

# _ic_is_antipattern_proposal MULTI_LINE_TEXT
# Echoes 'true' if any line matches an anti-pattern lexicon AND is not meta-discussion.
# Echoes 'false' otherwise. Used by both get-back-to-work-claude.sh and
# subagent-drift-detector.sh via source-and-call.
_ic_is_antipattern_proposal() {
  local text="$1"
  if printf '%s\n' "$text" \
    | grep -iE "$_IC_CHECKPOINT_LEXICON|$_IC_QUERY_OFFLOAD_LEXICON" \
    | grep -vE "$_IC_META_DISCUSSION" \
    | grep -q .; then
    printf 'true'
  else
    printf 'false'
  fi
}
```

The action-frame alternatives require `shall`, `should`, `can`, `let's`, or `let me`. A focused probe classified `Would you like me to checkpoint here?` as `false`; `_gbtw_should_rearm_check executing` also returned `false` for the same sentence.

The missed classification controls whether a previously suppressed continuation check is restored:

```bash
    if [ "$_BG_JOB_ACTIVE" = "true" ]; then
        FIRE_CONTINUATION="false"
        log_hook "GET-BACK-TO-WORK" "Suppressed" "continuation check — waiting tool detected in last 3 turns (Monitor/TaskOutput/ScheduleWakeup/AskUserQuestion/run_in_background)"
        if [ "$(_gbtw_should_rearm_check "$WORKFLOW_STAGE" "$RECENT_CONTEXT")" = "true" ]; then
            FIRE_CONTINUATION="true"
            log_hook "GET-BACK-TO-WORK" "Suppression-override" "anti-pattern proposal detected — continuation check re-armed (bg-tool suppression)"
        fi
    fi
```

Impact chain: an agent asks permission with common wording outside the enumerated openings → predicate returns `false` → an `AskUserQuestion` or other waiting-tool event suppresses continuation grading → the deterministic override stays disabled → users can receive the exact workflow-avoidance proposal this release claims to block.

---

### AUTH-01 — `commander/src/ironclaude/auth_relay.py:85-99,124-133` — A fast rejected-code re-prompt is lost in a submission race

```python
                    self._buf.append(line)
                    if self._url is None:
                        m = _URL_RE.search(line)
                        if m:
                            self._url = m.group(0)
                    if self._code_submitted_at is not None and "Paste code here" in line:
                        self._needs_code = True   # CLI re-prompted after a code was already sent
```

```python
        try:
            self._proc.stdin.write(code + "\n")
            self._proc.stdin.flush()
            self._code_submitted_at = self._now()
            self._last_feedback_ts = None
            return "sent"
```

The reader recognizes a re-prompt only after `_code_submitted_at` is set, but `submit_code` sets it after writing and flushing to the subprocess. A deterministic stdin double that synchronously emitted `Paste code here if prompted >` during `write()` produced `submit: sent`, `_needs_code: False`, and `next tick: None`.

Impact chain: the CLI rejects a code and re-prompts before the submitting thread records its timestamp → reader sees `_code_submitted_at is None` and discards the state transition → no later line restores `_needs_code` → Slack reports the code as submitted but never asks for a replacement → the operator waits until periodic feedback or hard timeout instead of completing sign-in.

---

### SLK-01 — `commander/src/ironclaude/main.py:1498-1507,1521-1532` — Operator alerts link to unrelated later Brain traffic

```python
        if self._operator_wait_alerted.get(worker_id) != question:
            self._operator_wait_alerted[worker_id] = question
            operator_name = self.config.get("operator_name", "Operator")
            alert_body = f"⏳ *Waiting on {operator_name}:* `{worker_id}` — {_escape_mrkdwn(question) or '(awaiting your reply)'}"
            ts = self.slack.post_message(alert_body)
            if ts and self._last_brain_post_ts:
                permalink = self.slack.get_permalink(self._last_brain_post_ts)
                if permalink:
                    self.slack.update_message(ts, f"{self.slack.prefix}{alert_body}\nLink: {permalink}")
```

```python
        for i in range(0, len(text), _BRAIN_POST_CHUNK):
            chunk = text[i:i + _BRAIN_POST_CHUNK]
            ts = self.slack.post_message(f"*Brain:* {chunk}", thread_ts=thread_ts)
            if first_ts is None and ts is not None:
                first_ts = ts
            if ts is None:
                all_ok = False
        if first_ts is not None:
            self._last_brain_post_ts = first_ts
        return first_ts if all_ok else None
```

Every Brain post updates one global timestamp, including unrelated tactical chatter posted in a heartbeat thread. A probe posted decision context at `decision-ts`, then unrelated chatter at `chatter-ts`, then captured the wait; `get_permalink` was called with `chatter-ts`.

Impact chain: decision context is posted → any later Brain reply or tactical message overwrites the global timestamp → the waiting classifier captures the original decision request → alert links the unrelated latest message → the operator is sent to the wrong context and may answer without the information the release claims to preserve.

---

## Quality (DRY / Architecture)

### TEST-01 — `worker/hooks/tests/test-sad-antipattern.sh:89-104` — The missing-transcript test silently passes unexpected output

```bash
# Fixture 3: missing / unreadable transcript path — helper returns empty, no crash
T3="${TMPDIR_TEST}/does-not-exist.jsonl"
got=$(_sad_last_assistant_text "$T3" || echo "CRASH")
if [ "$got" = "" ] || [ "$got" = "CRASH" ]; then
  if [ "$got" = "" ]; then
    printf 'PASS  3 (missing transcript → empty output, no crash)\n'
    PASSES=$((PASSES + 1))
  else
    printf 'FAIL  3 (missing transcript → helper CRASHED under set -u)\n'
    FAILS=$((FAILS + 1))
  fi
fi

echo
printf 'Results: %d pass, %d fail\n' "$PASSES" "$FAILS"
[ "$FAILS" -eq 0 ]
```

There is no outer `else`. If the helper regresses to any unexpected nonempty output other than `CRASH`, neither counter changes and the final assertion still succeeds because `FAILS` remains zero.

Impact chain: helper violates its documented empty-output contract → fixture enters no branch → test records neither a pass nor a failure → script exits successfully → CI cannot prevent that regression. The focused test run reported `5 pass, 0 fail`, but the counting scheme does not assert that all five named checks executed successfully.

---

## Informational

### SLK-02 — `commander/src/ironclaude/main.py:1011-1012` — Operator-facing text exposes an opaque internal annotation

```python
            elif st == "verify_failed":
                self.slack.post_message("Signed in, but I couldn't confirm the account after retries — the switch may or may not have completed. Not restarting; send `login` again to be sure. (review I1: no false 'previous account intact' claim.)")
```

The parenthetical is an internal engineering annotation rather than operator guidance. It exposes an unexplained identifier in production Slack output and makes an otherwise clear recovery message look unfinished.

---

## Summary

**Total findings: 7** (0 critical, 5 important, 1 quality, 1 informational)

**Highest-priority fixes:**
1. `AUTH-01` (`commander/src/ironclaude/auth_relay.py`) — a realistic inter-thread ordering loses the only signal that a replacement login code is required.
2. `SLK-01` (`commander/src/ironclaude/main.py`) — the release's decision-context link is not causally associated with the decision and can direct the operator to unrelated traffic.
3. `PKG-01` (`CHANGELOG.md` / omitted `AGENTS.md`) — the release excludes an existing Codex instruction surface while claiming complete behavioral-surface coverage.
4. `HK-01` / `HK-02` (`worker/hooks`) — two independent false-negative paths bypass the new workflow-avoidance enforcement.
