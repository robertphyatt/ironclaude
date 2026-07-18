---
name: workflow-durability
description: Read when about to propose "checkpoint / bank progress / resume fresh / find a safe stopping point" mid-execution, when about to ask the operator to run read-only queries the current stage blocks Bash for, or when the stop-hook rejects a message pointing at this skill. Answers "artifacts are durable — the correct move is a workflow surface, not a hand-back."
---

# Workflow Durability

## Purpose

Answer one question: *"I'm worried about context / can't run Bash right now / this task is long — should I checkpoint or ask the operator to do X?"* Answer: **no** — because IronClaude's artifacts are durable and the correct move is a workflow surface, not a hand-back to the operator.

## When to Invoke

Read this skill when about to say (or when the stop-hook rejects a message containing) any of:

- "Shall we checkpoint / bank progress / resume fresh"
- "Find a safe / natural stopping point"
- "Pause here for now"
- "You (the operator) run these queries / paste this output / execute these commands" — as a way to work around a stage-blocked Bash
- Any framing where a stage restriction or context concern becomes a reason to hand work back to the operator

## What Makes Artifacts Durable

- **Plan state** — `docs/plans/*.plan.json` on disk; MCP `create_plan` reloads from JSON; `mark_plan_ready` re-registers after edits. Interrupted mid-execute = the plan file is intact and re-invoking `/executing-plans` resumes.
- **Task state** — the MCP state manager tracks `workflow_stage`, `current_wave`, and per-task `status` in `~/.claude/ironclaude.db`. Re-entry reads `get_resume_state`.
- **Design state** — `docs/plans/*-design.md` on disk; `mark_design_ready` re-registers.
- **Compaction is normal infrastructure.** Session compaction re-inflates from these artifacts + episodic memory. It is not a data-loss event and does not require self-service pausing.

## The Two Anti-Patterns (Named)

### AP-1: Checkpoint anxiety

Proposing to pause mid-execution "for safety" when no operator request or step failure prompted it. **Forbidden.** Pauses are operator-initiated via the `plan-interruption` skill. The plan file *is* the checkpoint.

### AP-2: Query offloading

Asking the operator to run a read-only Bash / sqlite / grep query the model itself cannot run because the current stage (`idle`, `brainstorming`, `plan_ready`, `final_plan_prep`) blocks Bash. **Forbidden.** The correct move is an **investigation PM loop**: brainstorm → tiny read-only plan → execute stage unblocks Bash → the model runs its own queries → produces a findings note.

## Correct Workflow Moves (Decision Table)

| Situation | Correct move |
|---|---|
| Operator asks to pause | `plan-interruption` skill |
| Step fails; need to debug | `executing-plans` Step 6 (Handle failures) |
| Bash blocked, need read-only queries to verify state | **Investigation PM loop** (brainstorm → tiny plan → execute stage unblocks Bash → run queries → findings note) |
| Bash blocked, need to write code | The existing PM loop — that IS the workflow, no separate loop needed |
| Context "feels long" | Do nothing; artifacts survive |
| Long task in progress | Keep going; artifacts survive |

## The Stop-Hook Contract

The `diligently_finished_work` rubric in `worker/hooks/get-back-to-work-claude.sh` treats an active proposal to checkpoint or offload as **grade D/F**. If you land on this skill because a Stop-hook reject cited it, the fix is to **continue the actual work** — not to re-propose the pause in different words.

The rubric distinguishes **PROPOSING** (D/F) from **DESCRIBING** the anti-pattern in a design doc / skill discussion / review report (A). Meta-discussion of the pattern is fine; active proposals to pause or hand off are not.

## Cross-References

- `plan-interruption` — the correct path for operator-initiated pauses
- `executing-plans` Step 6 — the correct path for step-failure handling
- `brainstorming` — the entry point for an investigation PM loop
