# Re-plan Progress and Experiment Stage (v1.1.16 L3) Requirements

> **Created:** 2026-10-04
> **Status:** Operator-approved. The operator chose "L3, then L2b-3 (Rec.)". The decisions below come from this brainstorm.
> **Roadmap:** `docs/plans/2026-10-02-v1-1-16-roadmap.md` (L3)

## Standing operator directives

- **One IronClaude.** Claude and Codex get one IronClaude with identical behavior. No client-specific forks.
- **Scope fidelity.** No unrequested tooling, and every verification must be able to fail.
- **Final review gate.** The standing rule is: "We need a final passing fable review though." **For L3 the operator changed the reviewer: "Let's do an opus 5.5 gate this time."** So L3's final end review of the full staged diff runs on **Opus 5.5** and must come back CLEAN before the operator commits.
- **Git limits.** No push. No commits by the agent. Commits go through `/commit`.
- **L1 decision D4** (`docs/plans/2026-10-02-scope-fidelity-requirements.md:28-31`):
  - Experiments are throwaway scripts with no plan, tests or review.
  - Their results are reported in chat.
  - They become product code only when the operator says "keep it".
  - Running one requires an experiment stage, which this loop (L3) delivers.

## Brainstorm decisions

1. **The operator rejected self-amendment.** The question asked was what an in-execution amendment may change. The operator answered: "Ok but if we allow changes to the allowed files, what's the point of allowed files? Won't that mean the ai can just change it whenever, for it's convenience?"
   - **Agreed point:** if the agent can widen its own `allowed_files` unilaterally, the list stops being a boundary. Someone other than the authoring agent must approve any scope change.
2. **Scope change: "Cheap re-plan (Rec.)".**
   - No self-amendment. A scope change still goes back through design and plan review.
   - What changes is the cost:
     - completed tasks' results and grades survive a retreat followed by a re-plan;
     - the revised plan's review checks only what changed (the existing inherited-review plus fix-advisor `advisor-remediated` path);
     - finished tasks are not redone.
   - **Rejected:** amending with operator approval (it interrupts the operator for every missed file); leaving things as they are.
   - **Precedent:** in June 2026 an agent edited `wave_tasks.allowed_files` directly with SQL to avoid a progress-losing retreat (episodic memory, `61d49ddb`). L3 removes the need for that.
3. **Experiment containment: "Repo scratch + drift check (Rec.)".** A new `experimenting` stage on both clients:
   - It is entered with a one-line note.
   - Edit and Write are allowed only under a gitignored `.ironclaude/scratch/` in the effective root. Bash is allowed.
   - On exit, a drift check blocks if any product file changed since entry.
   - No plan, TDD or review. Results are reported in chat. Promotion happens only on "keep it", through a normal loop.
   - **Rejected:** a scratch directory outside the repo, which can't easily import project code; entry restricted to the operator only.
4. **Design approval: "Yes, write it up (Rec.)"** — one loop covering both parts.

## Current behavior (verified 2026-10-03 and 10-04)

- **Retreat from `executing`.** `retreat` (`state-machine.ts:576-643`):
  - snapshots progress to plan history;
  - nulls `plan_json`, `current_wave` and `plan_name`;
  - unconsumes the design;
  - sets `inherit_review=1`, which keeps the plan lineage (`:146-150`).
- **`create_plan`** (`write-tools.ts:694`, `:708-737`):
  - accepts these stages: `design_ready`, `design_marked_for_use`, `plan_ready`, `plan_marked_for_use`, `final_plan_prep`;
  - on every call it deletes all `wave_tasks` rows, clears review grades and recreates Wave 1 as pending.
- **Review per lineage.** Each lineage gets one blind review, bound to the plan hash (`:775-808`, `:1481-1502`). A changed plan in the same lineage needs `advisor-remediated` at the new hash. The executing-plans skill (Step 1.5) already prescribes the fix advisor for "a passing canonical verdict whose hash does not match the current plan".
- **No experiment stage.** The stages are listed in `state-machine.ts:109-136`.
- **File guard.** It blocks writes outside `executing` and checks `allowed_files` during executing (`professional-mode-guard.sh:624`, `:751-810`). It has no scratch exemption.
- **Interim experiment text.** Until now the scope-fidelity rule said to "run an experiment as a light loop whose task states 'No tests required: experiment'" (`worker/rules/scope-fidelity.md §5`).

## Requirements

**R1. Carry-over on re-plan.** When `create_plan` loads a plan into a session whose lineage has a snapshot from an earlier execution:
- Carry over a task as `review_passed` (with its grade) when both hold:
  - in that snapshot it was `review_passed`, with an identical canonical definition (`id`, `name`, `description`, `allowed_files`, `depends_on`, `steps`);
  - every task in its transitive `depends_on` is also carried over.
- Reset every other task to pending.
- Execution resumes at the first wave that isn't complete.
- The review rules don't change: there is still one blind review per lineage, and a changed plan still needs `advisor-remediated`.
- No tool lets an agent change `allowed_files` other than through a reviewed plan.

**R2. Skill text for re-planning.** `executing-plans` (and `writing-plans` where relevant) states:
- the re-plan path for a missing file: retreat, revise, then the fix advisor and `advisor-remediated`;
- that completed unchanged tasks carry over;
- that self-amendment is not available.

**R3. Experiment stage.**
- New stage `experimenting`.
- New MCP tool `mark_experiment(note)` enters it, from `idle` or `execution_complete`. The note is required and is a single line.
- New MCP tool `end_experiment` returns to `idle`.
- Both work identically on both clients.
- Both are recorded in the audit log.

**R4. Containment.**
- **Scratch-only writes.** In `experimenting`, the file guard lets Edit, Write, MultiEdit and NotebookEdit target only paths under `<effective root>/.ironclaude/scratch/` (and Codex `apply_patch` edits, to the same paths). Bash is allowed.
- **Baseline.** On entry, a product-path baseline is recorded (dirty paths in the effective root, excluding `.ironclaude/`).
- **Exit checks.** `end_experiment`, and Stop while in `experimenting`, block when new dirty paths have appeared outside `.ironclaude/scratch/`. The message names those paths.
- **Fail open.** The checks fail open, logged, as the drift check does.
- **Not git-tracked.** `.ironclaude/scratch/` must not be git-tracked. The plan verifies how projects ignore `.ironclaude/`.

**R5. Experiment skill text.**
- A short experiment-mode section, in the brainstorming skill and in `scope-fidelity.md §5`, replaces the interim light-loop text. It covers:
  - when to experiment: research or probe questions;
  - how to enter and exit;
  - scratch-only;
  - report in chat;
  - promote only on "keep it".
- Activation and instruction parity are untouched; that is L2b-3.

**R6. Tests.**
- Each item above gets a RED→GREEN test:
  - state-manager vitest: carry-over, the new stage transitions and the tools;
  - hook tests: scratch-only guard, entry baseline, exit and Stop drift block, fail-open.
- All suites pass.

**R7. Deploy and gate.**
- Rebuild the state-manager bundle.
- Run all suites.
- Deploy hooks; refresh the Claude plugin cache (`claude plugin update`) and the Codex cache (`codex plugin add`); restart Commander and ChatGPT.
- The final **Opus 5.5** end review must be CLEAN before the operator runs `/commit`. This follows the operator's choice for L3.

## Out of scope

- Amending a plan in place.
- L2b-3 (activation templates).
- Mid-experiment promotion tooling. Promotion is an ordinary loop.
- Experiments inside Commander-managed worktrees beyond what the effective-root logic already gives.
