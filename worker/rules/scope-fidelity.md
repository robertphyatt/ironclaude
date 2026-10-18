# Scope Fidelity

Applies to every client (Claude and Codex) and every workflow stage. Loaded by
brainstorming, writing-plans, executing-plans, and code-review.

The workflow banner stands unchanged: every change goes through brainstorm → design →
plan → execute, with no stage skipped. This rule governs how large each stage is. Rigor
means completing every stage, not enlarging the work.

## 1. Deliver what was asked

- Build the thing the operator asked for, at the size they asked for it.
- Side quests unless the operator named them: instrumentation, evaluation or test
  harnesses, review/listening/survey pages, probe banks, receipts, hash binding,
  byte-equality proofs, independent rebuilds, and any new verification tooling.
- Verification means running the planned check once and reporting its output. If you
  believe new verification tooling is needed, ask the operator; do not build it.
- An unrequested artifact is a defect even when it is well made.

## 2. Light loop vs full loop

Classify every change in brainstorming and state the result in one line in the design doc
(`Loop: light — <why no risk area; ~N files/~M lines>` or `Loop: full — <risk area or size>`).

A change is **full** if it touches any of:
- database writes or schema/migrations
- auth, permissions, or guard/hook enforcement code
- concurrency (locks, async coordination, shared mutable state)
- git or worktree operations
- a public API or MCP tool schema

Otherwise it is **light**. A light change that grows beyond ~3 files or ~150 lines is
promoted to full. If the classification is uncertain, it is full.

A light loop still runs every stage, sized down:
- one short requirements + design note (a single `-design.md` with a `## Requirements`
  section is acceptable as the requirements artifact);
- a one-task plan (same JSON schema);
- a same-tier blind plan review — no tier-up escalation;
- the normal task-boundary code review.

If a light change outgrows its bounds mid-execution, stop and retreat to brainstorming to
reclassify; do not silently expand it.

The light label is checked, not trusted. Record it with its reason. The blind plan
reviewer verifies it against the risk list and size bound, and a wrong label is a
MATERIAL finding that forces the full loop. Task code review fails a light task whose
diff touches a risk area or exceeds the size bound (`review-checklist.md` → "Light-loop
bound").

## 3. Delegated authority

Delegation is in effect only when:
- the operator explicitly delegated in this session ("don't ask", "use your judgment",
  "proceed autonomously", or equivalent), or
- the session is a Commander-spawned worker (its Brain answers on the operator's behalf).

Under delegation, replace routine approval questions (design-section sign-offs, "ready
for the plan?", the `commander-choice` tier-up prompt, execution-mode choice) with a
recorded line — `Predicted answer: <answer> (delegated)` — in the design doc or plan, and
proceed.

Always ask, even under delegation: push, delete, destructive or irreversible actions,
outward-facing actions (external systems, publishing), and anything needing new authority.

## 4. Operator prohibitions

When the operator forbids a pattern ("stop making listening surveys"), append one bullet to
`docs/operator-prohibitions.md` in the target repository — date, the forbidden pattern,
and the operator's words quoted. Create the file if absent. Read it at the start of
brainstorming and writing-plans and honor every entry. `docs/` is writable in every
workflow stage.

## 5. Experiments

Research questions ("does CLAP rank these scenes well?", "try weighted-median") are
answered with throwaway scratch scripts whose results are reported in chat, and become
product code only when the operator says "keep it". Until the experiment stage ships,
run an experiment as a light loop whose task states "No tests required: experiment".

## 6. Review bound

Reviewers label each finding `blocks requirement` or `hardening`. Only blockers can fail a
task. Reviewers may not demand evidence artifacts the plan did not declare. See
`review-checklist.md` → "Review scope bound".
