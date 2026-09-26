# v1.1.13 Grammar Cap, Idempotent Completion and Review Fixes — Requirements

> **Created:** 2026-09-26
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-26-v1-1-13-grammar-cap-and-idempotent-completion-design.md

## Origin

The operator asked to commit, squash and redeploy v1.1.13, restart Commander, validate that it works, and have Fable review the changes against the previous v1.1.13 in parallel.

- The commit was made: `60509a1` (local tag `v1.1.13`, unpushed).
- Live validation found a production regression: every local grade call returned HTTP 500.
- The Fable review returned HAS-ISSUES, with one Important finding and several observations.
- The operator then chose "Roll back, then fix in a PM loop". HEAD stays at `60509a1`, but the working tree holds the five runtime files from `c4ec287`, and Commander is running that code.

## Operator directives

- "Roll back, then fix in a PM loop".
- Duplicate completion: "A: idempotent completion (Recommended)". Remove the `kill_worker` status guard. `update_worker_status` stamps `finished_at` only when the status actually changes, and `kill_worker` logs `worker_finished` only if the worker was not already `completed`. The seam always runs, so a recycled worker's new work integrates.
- Grammar cap:
  - The operator asked: "Um won't 1024 not be enough? Let's just set it to 2048".
  - The agent pushed back: 2048 is the exact value measured to fail.
  - The operator chose "Find the real ceiling (Recommended)". Bisect 1024–2047 against the live server using the real schemas. The cap is the highest N that passes every run, minus one step of margin. It becomes a shared constant, pinned by a guard test.
- **Cap revision (mid-execution, supersedes "minus one step of margin"):**
  - The first bisect measured HIGHEST_PASS 1984 and FIRST_FAIL 2048, giving a cap of 1920. The operator then asked to "talk with the AMD Halo box about this" before any cap shipped.
  - The box reported that `MAX_REPETITION_THRESHOLD 2000` is llama.cpp's upstream default, hardcoded in `llama-grammar.cpp`, unchanged on current master, with no runtime flag. `maxLength` becomes a `char{0,N}` grammar rule, so every stock llama.cpp user hits the same wall.
  - The operator asked "Why not set it to 2k?". A live probe showed 2000 fails: HTTP 500 on every request run, both models.
  - The operator chose "Pin exact max first". A binary search found that 1992, 1996, 1998 and 1999 pass 15/15 on both models and 2000 fails. The rule is exactly N < 2000.
  - The operator then chose "1999 (Recommended)": the cap is `LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1` = 1999, with no extra margin.
- Design section 1 was approved with "Yes, continue", and section 2 with "Yes, write the design".
- Standing constraints:
  - The work folds into the unpushed v1.1.13, which is amended and re-tagged by the operator.
  - The seam owns all completion. The daemon never calls `update_worker_status`.
  - No push or deploy without an explicit go.
  - Commit messages carry no trailers.
  - `git stash` is never used.
  - The operator's earlier grader directive still applies: "grammar enforcement and a liberal enough length so this never can happen".

## Acceptance criteria

1. The loop starts by restoring `main.py`, `orchestrator_mcp.py`, `grader.py`, `shadow_grader.py` and `worker/hooks/plan-validator.sh` from HEAD. This was done in the first execution pass. The working tree keeps that restore plus the constant wiring, and `plan-validator.sh` matches HEAD.
2. Bisect logs record the HTTP status on the live llama.cpp server for each N probed, with 3 runs of every real grade schema:
   - the 64-step bisect from 1024 to 2047;
   - the 2000 probe;
   - the exact binary search from 1984 to 2000.
   The findings note records the exact ceiling (highest pass 1999, first fail 2000) and the upstream `MAX_REPETITION_THRESHOLD` explanation.
3. The cap follows the upstream constant:
   - `grader.py` defines `LLAMA_CPP_MAX_REPETITION_THRESHOLD = 2000`, `GRAMMAR_MAXLENGTH_HIGHEST_PASS = 1999` (measured), and `GRAMMAR_MAX_STRING_LENGTH = LLAMA_CPP_MAX_REPETITION_THRESHOLD - 1`.
   - Every `maxLength` in every local grade schema is at most `GRAMMAR_MAX_STRING_LENGTH`. The long fields (`interaction_block`, `question`, `authority_text`, `feedback`, `diagnosis`) use it directly.
   - A guard test fails if any bound exceeds the cap, or if the cap differs from the measured highest pass or from threshold - 1.
   - The constant's comment cites the llama.cpp limit and the measured evidence.
4. Duplicate completion is fixed in an idempotent way:
   - `update_worker_status` changes `finished_at` only when the status actually changes.
   - `kill_worker` has no registry-status guard. It logs `worker_finished` only when the worker's status before the kill was not `completed`.
   - The seam runs for completed workers too. A worker completed through `commit_worker` recycling, with its session alive, reaches the seam's integrate or release path.
5. In `_drive_finalization_recovery`, a terminal `None` outcome neither counts nor resets `_finalize_failure_count`. Other non-counted terminal outcomes still reset it.
6. The no-registry-row branch of `kill_worker` emits the `WORKER_KILLED` audit event.
7. The `kill_worker` status text for a successful seam that did not complete the worker reports the seam's `state`, or its `action` when there is no `state`.
8. The documentation (CHANGELOG `## 1.1.13` and README "What's New in v1.1.13"):
   - no longer claims "unreachable remote host → session state unknown";
   - no longer contains an unconditional "— the daemon will retry —";
   - no longer over-claims that truncation cannot happen;
   - describes idempotent completion and the measured cap.
9. Gates:
   - New tests fail before their change and pass after it.
   - The full commander pytest suite runs with 0 failures.
   - A live probe of every real grade schema through `LocalGrader` against the deployed server returns no `infrastructure_error`.
