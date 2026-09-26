# v1.1.13 Findings and Pre-Existing Issues — Design

> **Created:** 2026-09-26
> **Status:** Design Complete
> **Scope mode:** hold (operator-set scope)
> **Requirements:** docs/plans/2026-09-26-v1-1-13-findings-requirements.md

## Summary

After validating the deployed v1.1.13 build, the operator said: "fix the findings and
'pre existing' issues now with a new pm loop". The scope also includes the three earlier
non-blocking observations the operator approved, and whatever the v1.1.13 Fable
adversarial review found (per the operator's instruction). That review was SOLID and
raised four observations. Everything folds into the **unpushed v1.1.13** commit (operator:
"We haven't released (pushed) v1.1.13 yet! Just keep using that!"). The version stays
1.1.13, and the commit and tag are amended.

## Items

### 1. `kill_worker` on an already-terminal worker (`orchestrator_mcp.py:6575`)

- **Guard:** at the top of `kill_worker`, read the worker row. If `status` is in
  `{"completed", "failed", "killed"}` (the terminal set in `worker_registry.py:153`), do
  not grade, do not run the seam, and do not complete the worker again. If the worker's
  tmux session still exists, kill it. Return a status saying "Worker X is already
  <status>; nothing to finalize" (and noting "(stale session killed)" when a session was
  found).
- **Honest completion status:** the "killed and marked completed" message and the
  `worker_finished` log key off a registry re-read (`status == "completed"`) instead of
  "the result dict has no `failure_phase`". With a `resolved` or `integrated` probe and a
  live session, the seam does not complete the worker, and `kill_worker` must not claim
  it did. The router's live-session downgrade only happens when the session is alive,
  but `kill_worker` kills the session before calling the seam, so in practice the session
  is dead. The re-read is still the truthful predicate.

### 2. Stale repo paths in the orphan sweep (`main.py _managed_repositories` :521-545)

- **Normalize:** a `workers.repo` value that contains `/.ironclaude/worktrees/` maps to
  its primary repo (`split(marker)[0]`), the same rule the workspace-DB branch already
  uses.
- **Skip vanished local paths:** a local entry (`machine` empty or NULL) whose path is
  not an existing directory is skipped, with a DEBUG log. It is not passed to
  `reap_orphans`, so it no longer counts as a `repo_failures` WARNING. Remote entries are
  unchanged.
- **Dedupe local entries by git common dir:** resolve each remaining local path with
  `git -C <path> rev-parse --path-format=absolute --git-common-dir`. Keep the first path
  seen for each common dir. If resolution fails, fall back to keeping the path as-is,
  which is the current behavior. This removes the triple sweep of one repository reached
  through different subdirectories.

### 3. Grader truncation must be impossible: grammar enforcement plus a generous length

The operator directive: "We should be using grammar enforcement and a liberal enough
length so this never can happen."

- **Grammar-bounded output.** Every schema passed to `grade()` must bound its output
  length through the grammar:
  - every string property carries `maxLength`;
  - every array carries `maxItems`.

  The OpenAI-compatible backend already sends `response_format: {type: json_schema}`
  (`grader.py:209-213`), and the Ollama backend sends `format: schema`. Both compile the
  schema to a sampling grammar, so the model cannot emit a longer string or array.
  - `_PROMPT_WAITING_SCHEMA` (`main.py:193-213`): `interaction_block` ≤ 4096 (matching
    the validator limit in `tmux_manager.py:204`), `question` ≤ 1024,
    `authority_text` ≤ 1024, `options` ≤ 16 items, each `value` ≤ 64 and `label` ≤ 256.
  - Every other schema-bound call is bounded the same way. These are the full set,
    enumerated during brainstorming by grepping `_SCHEMA = {`, `schema = {` and every
    `.grade(` / json_schema payload:
    - `main.py` `_BRAIN_MSG_SCHEMA` (:92) and `_AWAITING_OP_SCHEMA` (:172);
    - `brain_client.py` `_PERMISSION_SEEKING_SCHEMA` (:42);
    - `orchestrator_mcp.py` `_GRADER_VERDICT_SCHEMA` (:901), `confidence_schema` (:4618
      and :5423) and `health_schema` (:6065);
    - `shadow_grader.py` `GRADER_VERDICT_SCHEMA` (:98).
- **Every JSON-producing call path gets the floor.**
  - `grader.grade()` (`grader.py:206`) when a schema is given.
  - `shadow_grader.py`'s own schema payload (`:383`), which sets `max_tokens` to
    `self._max_tokens` (the configured value or 1024).
  - The `plan-validator.sh` hook's json_schema request (`:178-187`), which sets
    `max_tokens` to the configured value or 1024.

  Each uses `max(configured, 8192)`. The orchestrator summarizer (`orchestrator_mcp.py`
  :6358) produces prose, not JSON, so it cannot fail JSON parsing and is out of scope.
- **Generous token budget.** On the OpenAI backend a schema-bound call sets
  `max_tokens = max(configured openai.max_tokens or 0, GRADER_SCHEMA_MIN_MAX_TOKENS)`
  with `GRADER_SCHEMA_MIN_MAX_TOKENS = 8192`. The configured value can raise the budget
  but never lower it below the floor. 8192 tokens comfortably exceeds the largest output
  any bounded schema allows: the prompt-waiting worst case is roughly 9–10k characters,
  about 2.5–3k tokens. Calls without a schema keep their current budget. The Ollama
  backend is unchanged: it already sends `num_predict: -1`, which is unlimited.
- **Guard test.** A test walks every schema passed to `grade()` and fails if any
  string property lacks `maxLength` or any array lacks `maxItems`. A future unbounded
  field fails CI-free local tests instead of truncating in production.

### 4. Separate once-only gate for the terminal-failure surface (`main.py` driver)

- Add a new set, `self._finalize_failure_alerted`, used only by the terminal-failure
  surface in `_drive_finalization_recovery`, in place of the shared
  `_finalize_recovery_alerted`. It is pruned in the same two places as
  `_finalize_failure_count`: the non-running sweep and the new-marker re-arm.
- An earlier drift, conflict or no-mode alert therefore no longer suppresses a later
  "dead worker's terminal finalize keeps failing" alert, or the other way round.

### 5. "Consecutive" really means consecutive (`main.py` driver)

- At the top of `_drive_finalization_recovery`, a terminal outcome that is **not** a
  counted failure resets the count: `self._finalize_failure_count.pop(worker_id, None)`.
  A counted failure is a dict whose truthy `failure_phase` is not `"finalization"`.
  Non-counted outcomes include `None`, a success, and a `finalization` phase in any mode.
- Non-terminal outcomes neither count nor reset, which is unchanged.

### 6. Test gap: detached primary with a usable origin/HEAD (`workspace-service.test.ts`)

- New vitest case. Use `repository()`, set `origin/HEAD` to `refs/remotes/origin/main`
  (with `refs/remotes/origin/main` present) and a local `main`, create an orphan at
  `main`, then `checkout --detach`. `reapAmbiguousOrphans` does not throw and reaps the
  orphan.

### 7. `_classify_finalization_failure` has no `resolved` arm (`orchestrator_mcp.py:3106-3140`)

- Mirror the `integrated` arm there: when the post-failure status probe returns
  `resolved`, complete the worker only if its session is dead
  (`_complete_worker_if_session_dead`) and return the status. Do not run the integrated
  cleanup, because the row is already cleaned. This removes the one false "needs
  operator intervention" alert that follows a finalize whose response was lost.

### 8. Wording: `not-ready` is not "strictly an active row"

- Reword the `integration.ts` status-branch comment and the committed `## 1.1.13`
  CHANGELOG entry. The accurate statement: `not-ready` now means any lifecycle other than
  integrated, ready-for-integration, or resolved (cleaned/abandoned), such as `active`,
  `reserved` or `materialized`.

### 9. `package-lock.json` version drift

- `worker/mcp-servers/workspace-manager/package-lock.json` says `"version": "1.1.4"` in
  two places (top level and `packages[""]`). Set both to `1.1.13`, and add package-lock
  to `test_version_consistency.py` (both fields) so it cannot drift again.

### 10. vitest `onTaskUpdate` RPC timeout (bounded investigation)

- Capture a full `npx vitest run` log. Identify the test file or files running when
  `Timeout calling "onTaskUpdate"` fires. Working hypothesis: long synchronous git
  subprocess sequences block the fork worker past vitest's RPC timeout.
- Apply the smallest config or test-setup fix that removes the error, and verify with
  two clean full runs.
- If no clear fix is found within the task, write the findings into the plan's findings
  note without changing product code. Recording the cause is then the deliverable.

## Testing Strategy

- pytest:
  - `kill_worker` terminal guard (no grade, no seam, no re-completion; a stale session
    is killed);
  - the honest status for a `resolved` result with a live session;
  - `_managed_repositories` normalize, skip and dedupe, against a temporary DB and
    real temporary git repos;
  - `grade()` sends the per-call `max_tokens`;
  - `_detect_worker_prompt` passes 4096;
  - the separate alert gate;
  - the consecutive-count reset;
  - the `_classify_finalization_failure` resolved arm;
  - version consistency including package-lock.
- vitest: the detached plus usable origin/HEAD reap test, and the vitest-noise fix.
- Each new test must fail against the current code.

## Implementation Notes

- Release: fold into the unpushed v1.1.13 commit `c4ec287`, amend it, and move the local
  tag (operator-gated). Update the README "What's New in v1.1.13" and the CHANGELOG
  `## 1.1.13` section.
- Redeploy at the same version: `claude plugin update` would do nothing at an unchanged
  version, so copy the rebuilt dist into both 1.1.13 caches, as v1.1.12 was deployed, and
  restart Commander.
- The seam still owns all completion: the daemon adds no `update_worker_status` calls.
