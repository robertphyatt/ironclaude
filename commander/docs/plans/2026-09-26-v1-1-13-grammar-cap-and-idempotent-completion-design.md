# v1.1.13 Grammar Cap, Idempotent Completion and Review Fixes — Design

> **Created:** 2026-09-26
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-26-v1-1-13-grammar-cap-and-idempotent-completion-requirements.md

## Summary

Redeploying v1.1.13 (`60509a1`) broke every local grade call. On the live amd-halo llama.cpp server (b9413, gemma4-26b-a4b), a `json_schema` grammar with any `maxLength` of 2048 or more fails with HTTP 500 ("Failed to parse input"), and the model emits YAML. A bound of 1024 works, even at `max_tokens` 8192.

This was verified live against the real schemas:

| Schema | Result |
|---|---|
| Old unbounded `_PROMPT_WAITING_SCHEMA` | 200 |
| New bounded `_PROMPT_WAITING_SCHEMA` (4096/2048) | 500 |
| New schema with those fields at 64 or 1024 | 200 |
| Single-field schema, 2048, `string` or `string\|null` | 500 |

Production was rolled back: HEAD is still `60509a1`, but the working tree holds the five runtime files from `c4ec287`.

The Fable review of the v1.1.13 changes found that `kill_worker`'s new registry-status guard blocks a real path. `commit_worker` marks a worker `completed` without checking its session. A finalize without `dispose` recycles the assignment (integrated → active) and keeps the worktree, so the worker can keep working and staging reviewed work. A later `kill_worker` then says "nothing to finalize" and strands that work. The review also raised observations 3–7 below.

## Components

### 0. Restore

Restore the five working-tree files from HEAD so the loop starts from `60509a1`:
- `commander/src/ironclaude/main.py`
- `commander/src/ironclaude/orchestrator_mcp.py`
- `commander/src/ironclaude/grader.py`
- `commander/src/ironclaude/shadow_grader.py`
- `worker/hooks/plan-validator.sh`

### 1. Grammar cap at the stock llama.cpp ceiling

- **Measured (done).** Every real local grade schema was probed 3 times on the live endpoint, with `max_tokens` 8192 and thinking off. The grader spot model is `gemma4-26b-a4b` and the shadow spot model is `qwen3.8-27b`. The schemas are `_PROMPT_WAITING_SCHEMA`, `_LOCAL_VERDICT_SCHEMA`, `_LOCAL_CONFIDENCE_SCHEMA`, `_LOCAL_HEALTH_SCHEMA` and shadow `GRADER_VERDICT_SCHEMA`. Three runs, each with its own log:
  - a 64-step bisect from 1024 to 2047: 1984 passes, 2048 fails;
  - a 2000 probe: fails;
  - an exact binary search between 1984 and 2000: 1992, 1996, 1998 and 1999 pass, 2000 fails.
- **Cause.** The amd-halo box reported it: llama.cpp hardcodes `MAX_REPETITION_THRESHOLD 2000` in `llama-grammar.cpp`. It is unchanged on current master and has no runtime flag, and `maxLength` compiles to `char{0,N}`. The measured rule is exactly N < 2000.
- **Constants** (`grader.py`):
  - `LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000`;
  - `GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999`, measured;
  - `GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1`.
  The comments cite the upstream constant and the findings note. There is no margin, by operator decision.
- **Schemas.** These are already wired in the working tree. `main.py` and `orchestrator_mcp.py` import the constant and use it for every long field: `interaction_block`, `question`, `authority_text`, `feedback` and `diagnosis`. `shadow_grader.py` uses it for `feedback`. Short bounds (option `value` 128, `label` 512, enums, `reason`/`question`/`worker_id` in the awaiting-op schema) stay as they are, provided they are at most the cap.
- **Guard tests** (`test_grade_schema_bounds.py`):
  - every `maxLength` in every grade schema is at most the cap;
  - the prompt-waiting `interaction_block` equals the cap (this replaces `== 4096`);
  - the cap equals `GRAMMAR_MAXLENGTH_HIGHEST_PASS`, which equals `LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1`, which is below 2000. A bump to 2000 fails this test.

### 2. Idempotent completion (replaces the status guard)

- **Registry.** In `worker_registry.update_worker_status`, stamp `finished_at` only when the stored status differs from the new status: `UPDATE ... SET finished_at = datetime('now') WHERE id = ? AND status IS NOT ?`, run before the status update. Other callers see no change.
- **`kill_worker`.**
  - Remove the whole `if _prior_status in ("completed", "killed"):` guard, including the `has_session` try/except.
  - Keep `_prior_status = _kw.get("status")`.
  - Log `worker_finished` only when `_completed and _prior_status != "completed"`.
  - The status line becomes "Worker X killed; already completed (no change)." when the worker was already completed, and "killed and marked completed." otherwise.
- **Unchanged.** The unknown-worker (no registry row) branch and all seam logic stay as they are.

### 3. A `None` outcome stays neutral

In `_drive_finalization_recovery`, the top-of-driver reset becomes `if terminal and outcome is not None and not counted_failure:`.

### 4. Audit event for the no-row kill

The no-registry-row branch calls `log_worker_event("WORKER_KILLED", worker_id=worker_id, pane_pid=None, had_evidence=bool(original_objective and evidence), kill_reason=evidence[:200] if evidence else None, runtime_seconds=None)` before returning.

### 5. Seam-ok status text

`state={_release.get('state') or _release.get('action') or 'unknown'}` becomes the status fragment. The label stays `state=` for continuity; its value is the state or the action.

### 6. Documentation

- CHANGELOG `## 1.1.13` `kill_worker` bullet:
  - Drop the guard description, the "session state unknown" sentence and the unconditional "— the daemon will retry —".
  - Describe idempotent completion instead: no re-stamped `finished_at`, no duplicate `worker_finished`, the seam still runs for recycled workers.
  - Keep the retry-claim sentence.
- Grammar bullets: state the measured cap and the llama.cpp reason, and drop "can no longer be truncated" absolutes.
- README "What's New in v1.1.13": the same corrections in user-facing wording.

### 7. Release gate

A live probe runs every real grade schema through `LocalGrader` with realistic prompts against the deployed config. Every result must be free of `infrastructure_error`.

## Testing Strategy

Each new test must fail against the code at `60509a1`.

- **Guard test:** the bound-≤-cap assertion fails at `60509a1` (bounds of 4096/2048 versus the cap).
- **Registry test** (real SQLite): completing an already-completed worker leaves `finished_at` unchanged; completing a running worker sets it.
- **`kill_worker`, already-completed worker:** the seam is called and no `worker_finished` is logged; the status reads "already completed (no change)".
- **`kill_worker`, recycle scenario:** the worker is `completed`, its session is alive and the seam returns an integrated success. The seam is called and the status reflects the integration.
- **Rewritten tests:** the old `TestKillWorkerAlreadyTerminal`, `TestKillWorkerGuardLivenessFailure` and the `failed`-worker guard test are rewritten or removed for the intended behavior change.
- **Driver:** a terminal `None` between counted failures preserves the count; the existing `None` reset parameter is updated.
- **No-row branch:** it emits `WORKER_KILLED`, checked with caplog on JSON log lines as in `test_orchestrator_mcp`'s `test_kill_worker_logs_pane_pid`.
- **Seam action:** a seam result `{"action": "surfaced"}` yields `state=surfaced`.
- **Gates:** full pytest with 0 failures, then the live probe (component 7).

## Implementation Notes

- This is a new effort, so it gets its own single blind plan review.
- **Revision state (after the cap-rule change).**
  - Done:
    - the bisect and findings note (reviewed B; one minor wording error, "prefix checks" where the rejecting check is `_suffix_is_prompt_chrome`);
    - the HEAD restore of all five files;
    - the constant wiring in all three schema modules and the partial test edits.
  - Remaining:
    - a findings-note addendum (exact search, upstream cause, final constants, wording fix);
    - the 1999 constant values, comment and test pin, then the regression and the live probe at 1999;
    - then components 2–7 unchanged.
  - Grammar docs cite 2000 as the failure point (not 2048) and the cap as 1999.
- The bisect runs in the execution stage, where Bash and the network are available. Logs go to the scratchpad, and the findings are recorded in the design-adjacent findings note.
- The seam owns all completion. The registry change is a data-layer idempotency, not a new completion site.
- Release: amend `60509a1` and move the tag (operator-gated), redeploy (caches plus Commander restart), run live re-validation, and push only on an explicit go.
- The vitest `onTaskUpdate` fix remains a separate follow-up loop.
