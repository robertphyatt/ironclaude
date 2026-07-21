# Plan-Authoring Fidelity Design

> **Created:** 2026-07-21
> **Status:** Design Complete
> **Ships as:** v1.0.26
> **Requirements:** `docs/plans/2026-07-19-multi-provider-v1-1-requirements.md` (MP-W02, MP-W10, MP-R07)
> **Unblocks:** `docs/plans/2026-07-19-multi-provider-v1-1-foundation.md` (provider foundation blind review)

## Summary

The v1.1.0 provider-foundation blind review returned HAS-ISSUES on two fidelity findings that no plan revision could repair. This design resolves both by correcting a design that drifted stricter than its own requirement, and by closing two genuine holes in the plan-authoring contract.

Finding one: the human plan embeds a six-round prior-review obligations table and a reviewer-drift audit. Because the human plan is a mandatory blind-reviewer input, every reviewer receives prior findings and fix rationale. A fresh reviewer confirmed this empirically — it reported receiving six rounds of prior findings through the plan itself. This violates MP-W02 and rejected-approach MP-R07.

Finding two: the plan pair does not satisfy the v1.1 design's canonical-PlanJson/byte-parity contract. Investigation showed the v1.1 design is the artifact at fault, not the plan. MP-W10 requires human and machine plans to be "semantically identical"; the v1.1 design unilaterally escalated that to byte parity with a deterministic renderer, and the already-shipped anti-flailing design explicitly scoped that renderer out as "an instruction-and-test contract, not a new renderer." Amending the v1.1 design restores fidelity to operator guidance rather than weakening it.

## Architecture

Three independent concerns, no shared machinery. Consistent with the shipped anti-flailing precedent: instruction-and-test contracts, not new subsystems. No new MCP tool, no schema change, no renderer, no `dist` rebuild.

### Why the design is amended rather than the plan

`MP-W10` (requirements ledger line 62) reads: *"human and machine plans must remain semantically identical."* Line 72 assigns MP-W10 to the anti-flailing prerequisite loop, which implemented it as a semantic parity checklist in `writing-plans` Step 5.6 and explicitly excluded canonical rendering infrastructure (anti-flailing design lines 30, 87). That loop shipped in v1.0.25.

The v1.1 design's lines 106–108 instead demand a canonical PlanJson, a deterministic Markdown renderer, and byte parity. That is stricter than the operator's requirement and contradicts a shipped, approved design. Correcting it is fidelity restoration, not scope reduction.

### Why grounding is an instruction, not a validator

MP-W10's other half — *"Plans are grounded in inspected live code"* — is unimplemented. The shipped `writing-plans` skill contains zero occurrences of "inspect". This is the defect that produced two real failures during v1.0.25 authoring: a fabricated symbol (`computeRuntimeFingerprint`, which does not exist; the real export is `captureRuntimeFingerprintFromPaths` at `runtime-fingerprint.ts:44`) and fabricated SQL columns (`design_path`/`created_at` versus production `design_file`/`registered_at`).

An automated validator was considered and rejected on feasibility: `allowed_files` legitimately contains files that do not yet exist (foundation Task 2 *creates* `provider_config.py`), and the plan schema does not distinguish create-from-modify, so path resolution would false-positive on every new file. Symbol resolution across bash, Python, and TypeScript is a large multi-language build.

Enforcement already exists on the review side. The v1.0.25 recalibrated reviewer prompt includes archetype 2 — *"symbols, columns, APIs, fixtures, or files that do not exist as written in the current source"* — with mandatory source verification. That archetype caught all three real defects found today. The instruction closes the authoring side; the archetype supplies the teeth.

## Components

### (a) Design amendment — `docs/plans/2026-07-19-multi-provider-v1-1-design.md`

Five locations restate byte-parity/canonical-renderer language as the shipped semantic-parity contract:

| Location | Drifted content to restate |
|---|---|
| line 106 | "PlanJson is the single canonical plan representation… schema rejects duplicated semantic mirrors" |
| line 107 | "deterministic rendering… validate byte parity… never hand-edit both representations" |
| line 108 | "deterministic parity compares actual human bytes with a fresh render" |
| Testing Strategy (~line 303) | "a revision mutates one canonical machine object and regenerates human Markdown exactly once" |
| Acceptance criterion 19 (~line 417) | "automated byte-parity checks prove the human plan is the deterministic rendering" |

Each becomes semantic parity as verified by `writing-plans` Step 5.6: requirement/design coverage, task IDs and dependencies, allowed files, ordered steps and commands, tests and expected results. The live-source grounding clause in line 108 is retained — it is a real MP-W10 obligation and is implemented by (b).

### (b) Grounding instruction — `worker/skills/writing-plans/SKILL.md`

Add an explicit grounding requirement to the plan-construction phase: before writing a task, inspect the current implementation, tests, schemas, and call sites that constrain it. Every file path, function name, signature, DB column, config key, and command the plan asserts must be verified against current source and not inferred from design prose, an earlier plan, or a summary. Speculative replacement snippets are not implementation authority.

### (c) Review-history prohibition — `worker/skills/writing-plans/SKILL.md` and `worker/skills/executing-plans/SKILL.md`

Plan artifacts must not contain prior-review findings, verdicts, fix rationale, reviewer-drift audits, or round-by-round obligation tables. MP-W02 governs what a reviewer *receives*; because the human plan is a mandatory reviewer input, content inside the plan reaches the reviewer regardless. That closes the hole.

This content already has durable homes and needs no new storage: `tier_up_reviews` rows (verdict, reviewer model, plan hash), `retreat` reasons, and workflow-private session state. The `executing-plans` mirror lands in step 8's plan-regeneration guidance, so a coherent regeneration cannot carry history forward — the exact mechanism by which `foundation.md:45-69` accumulated six rounds.

### (d) Tests — `commander/tests/test_writing_plans_skill.py`, `commander/tests/test_executing_plans_skill.py`

Phrase assertions matching the existing pattern in both suites.

### (e) Release — v1.0.26

Skills are shipped artifacts. Claude loads live from the repo, but Codex installs a snapshot copy, so Codex requires a version bump and reinstall to receive the change. Bump the four sources `test_version_consistency.py` requires to agree, add a CHANGELOG entry, then dual-client reinstall with both-caches verification.

## Data Flow

Authoring, after this change:

```
writing-plans
  → read design + requirements
  → GROUNDING (new): inspect live source; verify every asserted
    file, symbol, signature, column, key, command
  → write task contracts from verified facts
  → Step 5.6 semantic parity audit (human ↔ machine)
  → PROHIBITION (new): plan artifacts carry no review history
  → mark_plan_ready

executing-plans, on HAS-ISSUES
  → verify findings against live source
  → holistic invariant audit
  → plan-only defect: regenerate coherently
      PROHIBITION (new): regeneration carries no prior findings forward
  → brand-new blind reviewer receives four clean artifacts
```

## Error Handling

- A plan asserting an unverifiable symbol is caught by reviewer archetype 2, which requires source-verified evidence for any MATERIAL finding.
- A plan containing review history is caught by the new skill-contract test at build time, and by a reviewer noticing contaminated input at review time.
- Amending the v1.1 design changes a registered design artifact. The foundation plan's `design_file` path is unchanged, so its binding survives; its next blind review reads the corrected wording.
- If the skill edits break a pinned phrase assertion, the existing suites fail before staging. Wording drift is meant to fail the build.
- Version drift across the four sources fails `test_version_consistency.py`.

## Testing Strategy

No new test files. Both skill-contract suites exist and use phrase assertions.

**`test_writing_plans_skill.py`**
- Grounding instruction present, including "verified rather than inferred" intent
- Review-history prohibition present
- Negative: skill does not instruct authors to record review-round tables in plan artifacts

**`test_executing_plans_skill.py`**
- Plan regeneration must not carry prior-review content forward (step 8 mirror)

**Regression gates:** commander suite (2003 baseline), state-manager vitest (134), hook suite (44), and `test_version_consistency.py` at 1.0.26.

**Behavioral acceptance, stated honestly.** No unit test proves an instruction changes authoring behavior. The real acceptance is the next foundation review: under this contract it should surface neither an invented-symbol finding nor review-history contamination. **Escalation trigger:** if the next authored plan still produces an invented-symbol MATERIAL finding, that is the evidence to build the automated validator (rejected here on feasibility) rather than assuming the instruction worked.

## Implementation Notes

**Follow-up obligation, explicitly out of scope.** Stripping `docs/plans/2026-07-19-multi-provider-v1-1-foundation.md:45-69` belongs to the foundation loop's own revision, performed under the rule this design establishes. Recording it here so it is not lost: that plan cannot pass a clean blind review until those sections are removed.

**Sequencing.** This loop unblocks the provider foundation. Order: ship v1.0.26 → revise the foundation plan under the new rule → re-run its blind review → execute.

**What this design deliberately does not do:** build a renderer, add canonical-PlanJson storage, add byte-parity tooling, add an automated symbol validator, add an MCP tool, change the PlanJson schema, or rebuild `dist`. Each was considered and rejected above on requirement fidelity, feasibility, or YAGNI.
