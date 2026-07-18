# v1.0.24 Adversarial Findings Remediation Design

> **Created:** 2026-07-18
> **Status:** Design Complete
> **Scope mode:** selective (all seven verified findings plus tracked Codex guidance; no Commander Codex implementation)

## Summary

This effort remediates all seven verified findings in `docs/reviews/2026-07-18-v1-0-24-commit-findings.md` and resolves the omitted root `AGENTS.md` instruction surface. The implementation remains inside the intended v1.0.24 compatibility, workflow-durability, Slack-hardening, and Boy Scout scope.

The work uses targeted causal fixes. Root `AGENTS.md` becomes tracked and receives the same scope-aware Boy Scout behavior as the repository's Claude instruction surfaces. Hook enforcement preserves complete multiline assistant text and recognizes ordinary permission-request grammar. The authentication relay closes the code-submission/re-prompt race. Operator-wait alerts link only to context that can be associated with the captured worker, falling back to a linkless alert rather than guessing. The shell test harness becomes incapable of silently passing an unexecuted assertion path, and operator-facing Slack text loses its internal annotation.

No new dependencies, cross-provider orchestration, semantic hook graders, Brain message protocols, or Commander Codex workers are introduced. After the full brainstorm → write-plans → execute-plans → review → verification workflow completes, every resulting source, test, documentation, review, design, and plan artifact will be amended into the existing local v1.0.24 commit. Nothing will be pushed.

## Root-Cause Evidence

### PKG-01 — omitted Codex guidance

Codex officially discovers and loads repository-root `AGENTS.md` guidance. The file existed before `c923949`, was neither ignored nor tracked, and the executed Boy Scout design explicitly said to preserve it as unrelated and untracked. That scope decision made the v1.0.24 changelog claim that every current behavioral surface carries the rule incorrect. The current `AGENTS.md` mirrors root `CLAUDE.md` through principle 9 but lacks the Boy Scout principle 10.

### HK-01 — multiline extraction loss

`_sad_last_assistant_text` pipes raw `jq -r` text through `tail -1`. Embedded newlines therefore become separate shell lines, and only the last physical line survives. A transcript containing `Shall we checkpoint here?\nWaiting on your answer.` extracted only `Waiting on your answer.` and classified false.

### HK-02 — incomplete proposal grammar

The shared checkpoint lexicon enumerates `shall`, `should`, `can`, `let's`, and `let me` openings. It does not recognize common forms such as `Would you like me to checkpoint here?`. Both the shared predicate and the GBTW re-arm predicate returned false for that sentence, leaving waiting-tool suppression active.

### AUTH-01 — state published after subprocess delivery

`submit_code` writes and flushes stdin before setting `_code_submitted_at`; the reader only recognizes a re-prompt when that field is non-null. A deterministic stdin double that emitted `Paste code here if prompted >` during `write()` produced `sent`, left `_needs_code` false, and made the next `tick()` return `None`.

### SLK-01 — global latest-message substitution

Every `_post_brain_message` call overwrites `_last_brain_post_ts`, including threaded replies and tactical chatter. `_maybe_capture_operator_wait` then uses that global value without checking its relationship to the classified worker. A probe posted decision context, then unrelated threaded chatter; the alert requested a permalink for the chatter timestamp.

### TEST-01 — conditional failure swallowing

The missing-transcript fixture enters its assertion body only when output is empty or exactly `CRASH`. Any other nonempty result reaches no failure branch, leaves `FAILS=0`, and exits successfully. An isolated `got=UNEXPECTED` simulation confirmed exit status 0 with zero passes and zero failures.

### SLK-02 — internal annotation in production output

The `verify_failed` Slack message contains the literal parenthetical `(review I1: no false 'previous account intact' claim.)`. It is implementation bookkeeping rather than operator recovery guidance.

## Architecture

The remediation is organized into five independent defect clusters sharing one release gate:

1. **Instruction parity:** track root `AGENTS.md`, add Boy Scout parity, and extend the propagation guard so omission fails CI.
2. **Hook enforcement:** preserve the complete final assistant text block and separate proposal grammar from prohibited-action matching while retaining one deterministic helper for both Stop-hook consumers.
3. **Authentication relay:** publish submission state before subprocess delivery and roll it back on failure under the existing synchronization boundary.
4. **Operator-wait context:** retain only fully delivered top-level Brain posts as candidates and link only when the candidate text can be associated with the captured worker.
5. **Test and output quality:** require exact shell-test execution counts and remove the internal Slack annotation.

Each defect receives a regression test that fails against `c923949`. Existing public interfaces and plugin manifests remain unchanged. Ambiguous Slack context degrades to no link. Deterministic hook classification stays bounded and gains only evidenced proposal forms rather than an open-ended semantic dependency.

## Components

### 1. Codex guidance parity

`AGENTS.md` becomes a tracked root instruction file. Preserve its Codex-specific title and Plan Mode mapping, then add the same five-clause Boy Scout directive as root `CLAUDE.md`, numbered consistently as principle 10.

Add `AGENTS.md` to `DIRECT_SURFACES` in `commander/tests/test_boy_scout_directive.py`. The existing marker and exact-body assertions then verify both semantic branches: clean up within authorized scope; present evidence and request authority before material scope expansion.

### 2. Hook classification

Keep `_sad_last_assistant_text` bounded to the transcript tail, but aggregate the JSONL objects before selecting the final assistant text content block. The selected `.text` must retain internal newlines. Missing, unreadable, or malformed transcript input remains non-blocking.

Refactor the shared deterministic predicate so proposal framing and prohibited actions are explicit. Cover existing forms plus evidenced ordinary permission forms such as `Would you like me to...`, `Do you want me to...`, `Could we...`, and `May I...`. Preserve line-scoped heading, blockquote, table-row, and code-fence exclusions so documentation about the anti-pattern is not treated as a proposal.

### 3. Authentication relay

Track whether the login process's initial paste prompt has been observed. Only a subsequent paste prompt during an active submission is a rejected-code re-prompt. In `submit_code`, mark submission state under `_lock` before writing or flushing stdin. On delivery failure, roll back that attempt's submission and feedback timestamps under the same lock, but preserve a re-prompt flag concurrently published by the reader during delivery so `tick()` still requests fresh code.

Use the same lock for reader and `tick()` access to `_code_submitted_at`, `_last_feedback_ts`, and `_needs_code`. Preserve generation checks, verification retries, hard timeout, process reaping, and public return values.

### 4. Operator-wait context

Replace the timestamp-only global with an optional candidate containing both timestamp and original text. `_post_brain_message` updates the candidate only for a fully delivered top-level post; threaded replies, heartbeat chatter, and partial chunk delivery cannot replace it.

When `_maybe_capture_operator_wait` receives the grader's `worker_id`, use a candidate only if the candidate text contains that identifier as a bounded token. If the worker is `brain`, no candidate exists, the candidate is unrelated, the post is incomplete, or Slack permalink enrichment fails, retain the original alert without a link. Never produce a guessed or self-referential link.

### 5. Test and message cleanup

Give the missing-transcript shell fixture an explicit unexpected-output failure branch. Finish the script by requiring both zero failures and the exact expected pass count, so a skipped conditional cannot pass silently.

Remove the internal review annotation from the `verify_failed` Slack message while preserving the account-uncertainty statement, no-restart behavior, and instruction to run `login` again.

### 6. Release documentation and artifacts

Update README and v1.0.24 changelog wording only where the final behavior differs from current claims: tracked Codex guidance, conservative verified-context permalink behavior, and remediation of the adversarial findings. Keep direct Worker Codex support explicit and Commander Claude Code-only.

Include the adversarial findings report, this design, the implementation plan, and machine plan in the final amended commit. Update the v1.0.24 commit message to accurately summarize the completed release. Do not create a new commit, tag, or push.

## Data Flow

### Codex guidance

A repository clone contains tracked root `AGENTS.md`. Codex discovers it from the Git root and loads it before work begins. The Boy Scout propagation test reads it alongside every Claude-facing instruction surface, preventing client-specific drift.

### Stop-hook enforcement

SubagentStop supplies `transcript_path`. The extractor reads the bounded JSONL tail, collects assistant text blocks, and returns the complete final block. The classifier examines its lines, skips explicit meta surfaces, and requires proposal framing plus a prohibited checkpoint/query-offload action. A positive result reaches the existing `block_stop`. GBTW uses the same predicate to re-arm continuation grading after a waiting-tool suppression.

### Authentication relay

The reader records the first paste prompt as the initial input request. `submit_code` then publishes submission-active state under the relay lock before writing and flushing the code. A later prompt is therefore classified as a rejected-code re-prompt even if it arrives during `write()`, while an initial prompt that was delayed until submission is still ignored. If delivery fails without a re-prompt, the pre-delivery false flag remains false; if a re-prompt arrived before failure, rollback clears submission timestamps but preserves that reader evidence. `tick()` consumes `_needs_code` once and pauses waiting feedback until a fresh code arrives.

### Operator-wait context

A fully delivered top-level Brain post records text and Slack timestamp. Threaded replies and heartbeat chatter do not become candidates. When the grader identifies an operator wait and worker ID, the daemon checks whether the stored text contains that exact token. A match produces a permalink; missing evidence produces a linkless alert.

### Release completion

After regressions, full suites, testing-theatre audit, and final code review pass, all authorized artifacts are staged. Confirm HEAD is still `c923949`, exit execution state, and amend the complete staged tree into v1.0.24. Verify the resulting commit tree and ahead/remote state without pushing.

## Error Handling and Safety

- Hook extraction remains fail-open for absent, unreadable, or malformed transcript data. Valid multiline text must not be degraded.
- Meta exclusions remain line-scoped so one documentation line cannot suppress a genuine proposal elsewhere in the response.
- Auth distinguishes the first paste prompt from later rejection prompts; write or flush failure rolls back the in-memory submission timestamps before returning `failed` without erasing a concurrently observed rejection prompt; stale-reader generation protection remains intact.
- Failed Slack posts, partial chunk delivery, missing or unrelated candidates, permalink lookup failure, and message-update failure all degrade to the original linkless alert.
- `AGENTS.md` preserves Codex-specific guidance; parity testing applies to the Boy Scout contract rather than copying Claude-specific Plan Mode text.
- No capability skips, environment-dependent test bypasses, destructive Git operations, push, or tag creation are allowed.
- Before the terminal amend, verify HEAD is exactly the expected v1.0.24 commit. A moved HEAD is a blocker, not permission to amend another commit.

## Testing Strategy

Every behavioral fix begins with a regression that fails against current `c923949` before production code changes:

- `test_boy_scout_directive.py`: fail while `AGENTS.md` lacks the complete directive; pass only after it is tracked and updated.
- Hook tests: reproduce multiline truncation and common modal proposal forms across the shared predicate, GBTW, and SubagentStop integration.
- `test_auth_relay.py`: use deterministic racing stdin doubles that emit either the initial prompt or a later re-prompt during `write()`, including a re-prompt followed by flush failure, proving both sides of the classification and rollback boundaries.
- `test_main_operator_wait.py`: cover matching-worker links, unrelated-worker omission, threaded-chatter exclusion, partial-delivery exclusion, and no-context fallback.
- `test_main_validate.py`: verify clean operator-facing `verify_failed` text.
- `test-sad-antipattern.sh`: fail on unexpected output and require the exact expected pass count.

Testing-theatre rules apply to all changed tests: no conditional assertion paths, swallowed failures, capability skips, or mock-only assertions that bypass the production decision path.

Verification widens in layers:

1. Focused Python and hook regressions.
2. Bash syntax checks and Python compilation.
3. Entire hook test suite.
4. Complete Commander pytest suite.
5. Version consistency and Codex plugin validation.
6. Testing-theatre audit of changed tests.
7. Final code review against this design and the adversarial report.
8. Git-object verification that every authorized artifact and fix is present in the amended v1.0.24 tree.

## Implementation Notes

- Preserve direct Worker support for Claude Code and OpenAI Codex.
- Preserve the explicit boundary that Commander orchestrates Claude Code sessions only.
- Use deterministic shell/Python tests; do not add LLM-backed runtime enforcement or new dependencies.
- Do not weaken the Boy Scout authorization boundary into permission for unrelated cleanup.
- Keep human-facing README, changelog, design, plan, and commit-message prose in normal English.
- Complete the full PM workflow before the terminal amend.
- Amend all completed changes into v1.0.24 at the end. Do not push.
