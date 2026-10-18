# Re-plan Progress and Experiment Stage Design (v1.1.16 L3)

> **Created:** 2026-10-04
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-04-replan-progress-experiment-stage-requirements.md`
> **Loop:** full. This loop changes the state machine, the state-manager tools, the hooks and the skills.

## Summary

L3 removes the two main ways the workflow makes simple work expensive, and keeps every scope boundary in place.

**Re-planning loses no progress.** When a plan misses a file, the agent still has to send the scope change back through design and plan review. The agent can never widen its own `allowed_files`. What changes is the cost: tasks that were already completed and are unchanged carry over, so they are not redone.

**Research gets an experiment stage.** It writes only inside a scratch folder, and a drift check on exit makes sure no product file was touched.

## Approach

1. **Carry-over in `create_plan`.** Carry-over is a pure function. It compares the new plan with the lineage's snapshot of the previous execution:
   - A task carries over only if it has an identical canonical definition, its snapshot status is `review_passed`, and every task it depends on also carries over.
   - The review rules do not change. A retreat inherits the lineage's one blind review. A changed plan needs `advisor-remediated` at its new hash, using the fix-advisor path already in executing-plans.
2. **Experiment stage.** A new `experimenting` stage is added.
   - **Entry:** `mark_experiment(note)` from `idle` or `execution_complete`.
   - **Exit:** `end_experiment`, which returns to `idle`.
   - **Writes:** the file guard admits writes only under `<effective root>/.ironclaude/scratch/`.
   - **Drift check:** a baseline is taken at entry and checked at exit and at Stop. This reuses `execution-drift-check.sh`'s effective-root resolver and its dirty-path logic.

**Rejected alternatives:**
- Self-amending `allowed_files`, because it empties the boundary.
- Operator-approved amendments, because they interrupt the operator for every missed file.
- Scratch space outside the repository, because it can't import project code.
- Entry for the operator only.

## Components

1. **Carry-over (state-manager TypeScript).**
   - Where the retreat snapshot is stored and read back is confirmed while writing the plan; `get_plan_history` and the plan history table are the candidates.
   - The canonical task definition uses a stable key order, so that key order cannot defeat the identity check. A memory note records a past hash/key-order issue.
   - After reload, `current_wave` and the wave computation skip waves that are fully complete.
2. **Re-plan text in executing-plans.** It describes the path for a missing file and says that self-amendment is not available.
3. **Experiment stage.**
   - The state machine adds the stage and its transitions, and records `experiment_note` and its timestamps on the session.
   - Two new tools, `mark_experiment` and `end_experiment`, follow the existing transition-result shape (`changed`, `from`, `to`).
   - `end_experiment` refuses while drift exists. The hook enforces this, or the tool reports the drift check's result; the plan chooses which by following existing patterns.
4. **Guard and hooks.**
   - `professional-mode-guard.sh` gets an `experimenting` branch that admits only scratch paths for write tools, including Codex `apply_patch`.
   - The baseline is recorded when `mark_experiment` succeeds, through a PostToolUse hook entry.
   - The drift block runs at `end_experiment` (PreToolUse) and at Stop (GBTW).
   - GBTW grading in `experimenting` makes no workflow demands, apart from the drift block.
5. **Skill text.**
   - Add an experiment-mode section to the brainstorming skill and to `scope-fidelity.md §5`. It covers when to use the stage, how to enter and exit it, scratch-only writes, reporting in chat, and promotion only on "keep it".
   - Remove the interim light-loop experiment text.
6. **Ignore.** `.ironclaude/` must be git-ignored in projects that use experiments. The plan verifies the current mechanism, either activation or `.git/info/exclude`, and adds `.ironclaude/scratch/` only if it is not already covered.

## Error Handling

- The drift baseline and check fail open and log the failure (no git repository, no baseline, an unresolvable root), consistent with L2b-1.
- The guard fails closed for non-scratch writes in `experimenting`.
- Carry-over is conservative. Any mismatch or missing snapshot resets the task to pending.

## Testing Strategy

**State-manager tests (vitest):**
- identical and dependent tasks carry over;
- a changed task resets, and so do the tasks that depend on it;
- with no snapshot, everything resets;
- after a carry-over, the review gate still requires `advisor-remediated` for a changed hash;
- `mark_experiment` and `end_experiment` transitions, including the valid source stages and a required one-line note.

**Hook tests:**
- a write to scratch is allowed, a write elsewhere is blocked, and Codex `apply_patch` is covered;
- the baseline is recorded at entry;
- `end_experiment` and Stop block when drift exists and pass when it doesn't;
- the checks fail open.

**Release checks:** rebuild, run all suites, redeploy (caches and restarts), then the final end review, which the operator set to Opus 5.5 for this loop.
