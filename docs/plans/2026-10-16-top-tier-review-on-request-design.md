# Top-Tier Review on Request Design

> **Created:** 2026-10-16
> **Status:** Design Complete

Loop: light — skill instruction text plus one pytest file; no DB, guard/hook code, concurrency, git operations, or MCP schema; ~2 files / ~40 lines.

## Summary

Opus 5.5 is now close to Fable in capability, so reviews should stop escalating to Fable by default. The operator asked on 2026-10-10 that "we should have the tier up review be less eager to use fable now that opus 5.5 is a lot closer to fable than opus 5 was".

Today `executing-plans` sends an Opus session to Fable in three places: when Step 1.5 judges a plan high-blast-radius, in the HAS-ISSUES fix advisor (always), and in the Step 7.5 end review. On Codex the matching step is Sol to Astra.

This change adds one shared Top-tier rule. A client's top tier (Claude Fable, Codex Astra) is used only on explicit operator request, or after a same-tier reviewer at that step fails to produce a usable result.

## Requirements

Operator decisions, 2026-10-16:

- **R1. Scope.** The operator chose "A+B+C review ladder": the Step 1.5 plan-review tier-up, the HAS-ISSUES fix advisor, and the Step 7.5 end review. The operator did NOT choose these, and they stay unchanged:
  - the Advisor Fallback directive (CLAUDE.md, behavioral.md, the advisor-fallback skill, activate-professional-mode);
  - the memory rule requiring a final Fable end review.
- **R2. Clients.** The operator chose "Both clients: top tier on request". The rule is shared: Fable on Claude and Astra on Codex both follow it. This does not count as a client fork, since the model ladders are provider-specific plumbing.

  The operator's rationale, 2026-10-16: "open AI is trying hard to keep up with anthropic and sol got a similar boost (to 6.1) that opus had (to 5.5) in the last couple of weeks". Sol now sits as close to Astra as Opus does to Fable.
- **R3. Trigger.** The step below the top tier (Claude Opus, Codex Sol) resolves to a fresh same-tier reviewer. It escalates to the top tier only when:
  - (a) the operator explicitly requested the top tier for this effort; or
  - (b) a same-tier reviewer at that same step failed to produce a usable result (an error, empty output, or an off-task report).
- **R4. Unchanged:**
  - the ladder definitions themselves, and the lower steps (Haiku→Sonnet, Sonnet→Opus, Luna→Terra, Terra→Sol);
  - the Fable-unavailable fallback, which still applies whenever Fable is requested;
  - top-tier sessions still getting a fresh same-tier review;
  - the light-loop same-tier rule;
  - the `run_codex_advisor_review` broker code (`review_tier: "same"` already exists, `advisor-review.ts:25`);
  - the MCP gate.
- **R5.** A pytest pins the rule. It must fail if any of the three sites reverts to plain one-tier-up wording.

## Architecture

All changes are in `worker/skills/executing-plans/SKILL.md`.

1. **Step 1.5.** After the ladder lines (`:208-211`), add a **Top-tier rule** paragraph containing the R3 text. On Codex it is applied by passing `review_tier: "same"` from a Sol session.

   The blast-radius tier-up judgment (`:195-207`) gains one sentence: from Opus or Sol the judgment cannot reach the top tier, so no tier-up question is surfaced unless the operator requested it.
2. **The HAS-ISSUES fix advisor** (`:393-395`) keeps its ladder text and adds "subject to the Top-tier rule (Step 1.5)".
3. **The Step 7.5 end review** (`:848-850`) adds "subject to the Top-tier rule (Step 1.5)" to its one-tier-up dispatch.

## Testing Strategy

Add a new test to `commander/tests/test_executing_plans_skill.py`:
- It asserts the exact Top-tier rule sentence.
- It asserts the fix-advisor and end-review passages each contain the "Top-tier rule (Step 1.5)" reference, located by slicing the text between their section anchors.

Run the existing tests in that file and the full commander suite. RED comes before the skill edit.

## Error Handling

Not applicable: these are instruction-text changes. Fable unavailability keeps its existing fallback.
