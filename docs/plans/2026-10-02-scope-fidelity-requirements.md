# Scope Fidelity and Proportionality: Requirements (v1.1.16 L1)

> **Created:** 2026-10-02
> **Status:** Operator-approved in brainstorming
> **Roadmap:** `docs/plans/2026-10-02-v1-1-16-roadmap.md`

## Problem

Under IronClaude, Codex (and Claude) turn simple requests into very large efforts, and they take on side quests the operator never asked for.

**Thread 019fc3b0.** The request "fix your repair code" took 20 hours:
- 10 hours were spent idle on an approval question while the operator was away.
- Tests grew from 7 to 258 across successive review rounds.
- The work spawned three extra PM loops.

**Thread 019f7d26.** The operator redirected the agent 12 times. Unrequested work included:
- listening and survey pages, which the operator had forbidden three times;
- 20.7k lines of evaluation harnesses;
- a 41 KB runner, built because an acceptance guard hard-coded a track count.

## Operator decisions (authoritative)

1. **D1: one IronClaude.** The fixes go into shared plugin rules and skills that both clients load. There are no client-specific behavior forks.
2. **D2: the banner stays word for word**, in every copy, including the operator's personal files. The banner forbids getting around the brainstorm → design → plan → execute stages. Proportionality lives in a new shared rule, not in the banner.
3. **D3: the light-loop gate uses touched-path risk first.**
   - A change is light unless it touches any of these: DB writes or schema; auth, permission or guard code; concurrency; git or worktree operations; a public API or MCP schema.
   - Size is a secondary sanity check: a change that grows past about 3 files or 150 lines is promoted to the full loop.
4. **D4: experiments are scratch-only and promoted on "keep".**
   - An experiment is a throwaway script with no plan, tests or review. Its result is reported in chat.
   - It becomes product code only when the operator says "keep it".
   - Running it requires an *experiment stage*, which is delivered in L3. L1 only states the rule's intent and adds no experiment-mode skill text, because skills load live from the repo.
5. **D5: answers are auto-resolved only under explicit delegation.**
   - Routine approval questions are replaced by a recorded predicted answer only in two cases: after explicit in-session delegation (for example "don't ask", "use your judgment", "proceed autonomously"), or for a Commander-spawned worker.
   - Push, delete, destructive, irreversible and outward-facing choices always ask.
6. **D6: review grading is bound to the requirements.**
   - Each finding is labeled either `blocks requirement` or `hardening`.
   - Only blockers may grade C/D/F and reopen a task. Hardening findings are listed and recorded in a backlog note.
   - Reviewers may not demand evidence artifacts the plan did not declare.
   - An acceptance guard that pins a count the task is expected to change is a defect.
8. **D8: the light label is checked by a reviewer, not trusted.**
   - This follows the operator's earlier rulings (2026-01-20 and 2026-03-10) that agents misjudge what is "trivial" and abuse exemptions.
   - The agent records `Loop: light` together with its reason.
   - The blind plan reviewer must confirm the label. A wrong label is a MATERIAL finding, and it forces the full loop.
   - Task code review fails any light task whose diff touches a risk area or exceeds the size bound.
7. **D7: operator bans are persisted.** When the operator forbids a pattern, the agent appends it to `docs/operator-prohibitions.md` in the target repo; the guard already allows `docs/` writes in any stage. The agent reads that file at brainstorm and plan time.

## Requirements

- **R1.** Add a new shared rule file, `worker/rules/scope-fidelity.md`, encoding D2–D7.
  - It must state that verification means running the planned check once and reporting its output.
  - It must state that harnesses, instrumentation, review or listening pages, probe banks, receipts, hash binding, independent rebuilds and new verification tooling are side quests unless the operator named them.
- **R2.** The brainstorming skill applies scope-fidelity:
  - it classifies the change as light or full (D3);
  - it reads the bans file (D7);
  - under delegation (D5), its section approvals and the "ready for plan" prompt record a predicted answer instead of asking.
- **R3.** The writing-plans skill defines the light-loop plan shape (one task, a combined short requirements/design note allowed). It states that "Prove every verification can fail" applies to the planned checks and never authorizes new verification tooling.
- **R4.** The executing-plans skill:
  - for a light-loop plan, uses a same-tier blind review only, with no tier-up escalation;
  - under the `commander-choice` tier-up prompt, records a predicted answer when delegation is in effect (D5).
- **R5.** The code-review skill and `worker/rules/review-checklist.md` implement D6. The checklist also implements the code-review half of D8: a light-loop task whose diff touches a risk area or exceeds the size bound is a blocker.
- **R4a.** The executing-plans blind plan-review prompt implements the plan-review half of D8: the reviewer verifies any `Loop: light` label against the risk list and size bound, and treats a wrong label as MATERIAL.
- **R6.** Out of scope:
  - banners, `AGENTS.md` and `CLAUDE.md`, worker templates, the parity test, hooks, MCP servers, the Brain prompts, and personal instruction files;
  - experiment-mode skill text, which is deferred to L3.
- **R7.** Existing Commander and worker test suites that cover skill and rule text must still pass.

## Success criteria

- A two-file, low-risk change runs one task with a same-tier review and no tier-up prompt.
- A reviewer's hardening finding cannot reopen a task.
- A pattern the operator bans is written to `docs/operator-prohibitions.md` and is honored by later sessions on both clients.
