# v1.1.13 Findings and Pre-Existing Issues — Requirements

> **Created:** 2026-09-26
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-26-v1-1-13-findings-design.md

## Operator directives (this session)

- "let's fix the findings and 'pre existing' issues now with a new pm loop". This covers
  the three findings from validating deployed v1.1.13: the `kill_worker` re-completion,
  stale repo paths, and grader truncation.
- "Whatever fable finds if it finds anything should be added to the pm loop". The Fable
  v1.1.13 review was SOLID with four observations. Its observations 1–3 are items 7–9.
  Observation 4 (vitest noise) is item 10.
- Carry-over observations: "Yes, include all three". These are items 4–6.
- vitest onTaskUpdate timeout: "Include a bounded investigation".
- Grader: "We should be using grammar enforcement and a liberal enough length so this
  never can happen".
- Release: "We haven't released (pushed) v1.1.13 yet! Just keep using that!" The work
  folds into the unpushed v1.1.13 commit, which is amended and re-tagged.
- Standing constraints:
  - no push or deploy without an explicit go;
  - no commit trailers;
  - local tests only;
  - the seam owns all completion.

## Acceptance criteria

1. `kill_worker` on a worker that is already completed, failed or killed does no grading,
   runs no seam, performs no status update and logs no `worker_finished`. It kills a
   leftover session if one exists. The completion status message and the
   `worker_finished` log reflect the worker's actual registry status after the seam runs.
2. `_managed_repositories` maps worktree-path repos to their primary repo, skips local
   paths that do not exist, and removes duplicate local paths that share a git common
   dir. Remote entries are unchanged.
3. Truncated grader JSON cannot happen (operator: "grammar enforcement and a liberal
   enough length so this never can happen"). Every schema passed to `grade()` bounds
   every string with `maxLength` and every array with `maxItems`, so the grammar limits
   output length. Schema-bound OpenAI calls request
   `max_tokens >= 8192` (`max(config, 8192)`). A test fails if any grade schema has an
   unbounded string or array.
4. The terminal-failure surface uses its own once-only set, pruned in the same places as
   its counter.
5. A terminal outcome that is not a counted failure resets the consecutive-failure count.
6. A vitest case proves that a detached primary with a usable origin/HEAD still reaps.
7. `_classify_finalization_failure` handles `resolved` by completing the worker only if
   its session is dead. It never cleans up or abandons.
8. The comment and the CHANGELOG no longer call `not-ready` "strictly an active row".
9. `package-lock.json` reads `1.1.13` in both places, and the version-consistency test
   covers it.
10. The vitest `onTaskUpdate` timeout is either fixed (two clean full runs) or its root
    cause is written down.
11. The full commander pytest and workspace-manager vitest suites pass with 0 failed. The
    dist is rebuilt. The v1.1.13 CHANGELOG and README cover all of the above.
