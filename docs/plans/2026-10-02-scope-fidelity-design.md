# Scope Fidelity and Proportionality Design (v1.1.16 L1)

> **Created:** 2026-10-02
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-02-scope-fidelity-requirements.md`
> **Roadmap:** `docs/plans/2026-10-02-v1-1-16-roadmap.md` (loop L1 of 7)

## Summary

IronClaude applies the same heavyweight process to every change, and its review rules have no upper bound. Codex in particular reads the rules as minimums to exceed: it builds harnesses, receipts and review pages nobody asked for, and lets review rounds keep expanding tests.

This loop adds one shared rule, `scope-fidelity.md`, that both clients load from the plugin. It also hooks that rule into the four workflow skills and the review checklist at the points where it matters. Every stage still runs. A change sized to its risk gets proportionate artifacts, and the workflow never authorizes work beyond what was asked.

## Architecture

The chosen approach is **A: one shared rule plus targeted skill touchpoints.**

**Alternatives rejected:**
- **(B) Inline the text in every skill.** Four copies would drift apart; duplication like this is what produced the nine banner copies.
- **(C) Put it in the banner or the instruction files.** The operator ruled out changing the banner (D2), and this would need a parity edit in six or more files plus the operator's personal files.

Skills load directly from `worker/skills` in the repo, so edits take effect immediately for local sessions. The plugin cache picks them up on deploy. Codex reads the same skill and rule files from its plugin cache. One source serves both clients.

## Components

### 1. `worker/rules/scope-fidelity.md` (new)

Sections:
1. **Deliver what was asked.** The side-quest list from R1. Verification means running the planned check once and reporting its output. If verification tooling seems necessary, the agent asks the operator; it does not build it.
2. **Rigor means completing every stage, not enlarging the work.** This states the banner's intent: no stage is skipped, and no stage grows beyond the request.
3. **Light versus full loop (D3).**
   - The risk-area list.
   - What light means: a combined short requirements and design note (one file is allowed, as long as it holds the requirements section), a one-task plan, a same-tier blind plan review with no tier-up, and the normal task code review.
   - The promotion rule: past about 3 files or 150 lines, the change becomes a full loop.
   - The classification is stated in the design doc, in one line, with its reason.
   - **The label is reviewer-checked, not trusted (D8).** The blind plan reviewer confirms it, and a wrong label is MATERIAL. Task code review fails a light task whose diff touches a risk area or exceeds the size bound.
4. **Delegated authority (D5).**
   - Triggers: explicit in-session delegation phrases, or a Commander-spawned worker.
   - Under delegation, the agent records "Predicted answer: X (delegated)" in the design doc or plan and proceeds.
   - The always-ask list: push, delete, destructive, irreversible, outward-facing, or new authority.
5. **Operator bans (D7).** The path is `docs/operator-prohibitions.md`. The format is one bullet per ban, with the date and a quote of the operator's words. Append when the operator forbids a pattern, and read the file at brainstorm and plan time.
6. **Experiments (D4).** Intent only: research questions are answered with throwaway scratch scripts and promoted only on "keep". The mechanism, the experiment stage, arrives in L3. Until it lands, the light loop is used with "No tests required: experiment".

### 2. Brainstorming skill

- **New Step 0.2 "Apply scope-fidelity":**
  - read `../../rules/scope-fidelity.md` and the target repo's `docs/operator-prohibitions.md` if it exists;
  - classify the change as light or full and state the classification;
  - for a light change, the design doc may be a combined short note.
- **Step 5 (per-section approval) and Step 9 ("ready for plan?"):** under delegation, record the predicted answer and proceed.

### 3. Writing-plans skill

- **The light-loop plan shape:** a single task, with the same JSON schema.
- **A clarifier next to "Prove every verification can fail":** it applies to the checks the plan already needs, and is never a license for new verification tooling, receipts or extra suites.

### 4. Executing-plans skill

- **Step 1.5 reviewer tier:** a light-loop plan uses same-tier only; skip the tier-up judgment.
- **The `commander-choice` prompt:** under delegation, record "Yes, run" as the predicted answer.
- **The blind plan-review prompt:** in the latent-defect hunt, verify any `Loop: light` label against the risk list and size bound (D8). The materiality test gains case (e): a light label the plan does not qualify for.

### 5. Code-review skill and `worker/rules/review-checklist.md`

**Code review, Step 7 categorization:**
- Each finding is also labeled `blocks requirement` (planned behavior wrong, missing, or its planned check can't fail) or `hardening`.
- Grading counts blockers only. Hardening findings are listed under "Hardening (backlog)" and cannot raise the grade above B or reopen the task.
- The existing Critical categories map to blockers: security, broken functionality, and testing theatre.

**Checklist additions:**
- (a) A note saying that falsifiability and widened-guard findings are blockers only when they concern a requirement's own check or admitted set.
- (b) A new critical check, **"Pinned mutable count"**: an acceptance guard that hard-codes a count the task or the corpus is expected to change.
- (c) A new rule: **reviewers may not demand undeclared evidence artifacts.**
- (d) A new critical check, **"Light-loop bound"**: a task from a `Loop: light` plan whose diff touches a risk area or exceeds about 3 files or 150 lines (D8).

## Data Flow

1. The operator makes a request.
2. Brainstorming Step 0.2 reads scope-fidelity and the bans file, and classifies the change as light or full.
3. Light: a short note, a one-task plan, a same-tier review, execution, and a requirement-bound code review.
4. Full: the existing flow, with the same review bounding.
5. An operator ban at any point is appended to the bans file and read by later sessions on either client.

## Error Handling

- **Classification uncertain:** the change is full. Failing toward rigor is acceptable, because the risk list is narrow.
- **A light change grows past the size bound mid-execution:** stop and report. Promotion uses the existing retreat path; there is no new mechanism.
- **The bans file is absent:** no bans.
- **Delegation is ambiguous:** ask. That is the existing behavior.

## Testing Strategy

This is skill and rule text only. No tests required: the work contains no executable code.

The verification commands are:
- `commander/tests/test_activation_client_parity.py`, plus the full Commander suite. These check the banner and canonical bodies, which are unchanged.
- The worker skill-structure tests, wherever they exist. The plan locates them with a measured command.

## Implementation Notes

- The edits are additive and minimal. They do not rewrite existing skill logic.
- No change to banners, instruction files, templates, hooks, MCP or the Brain prompts. The Brain's `orchestrator_claude.md` "anti-trivial-fix bias" is consistent with D2 and stays.
- The operator's personal instruction files are untouched (D2).
