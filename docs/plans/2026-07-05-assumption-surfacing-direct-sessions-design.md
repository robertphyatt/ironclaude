# d1277: Assumption Surfacing in Direct User Sessions — Design

> **Created:** 2026-07-05
> **Status:** Design Complete

## Summary

d1275 recommended adding a "Surface Assumptions Before Acting" directive to `~/.ironclaude/brain/.claude/rules/behavioral.md` (Brain-specific, now directive #21) and explicitly recommended against adding it to global CLAUDE.md, reasoning that workers already get proactive assumption-surfacing structurally via PM brainstorming's `AskUserQuestion` mechanism.

Robert's follow-up (d1277): does the directive help users directly interacting with a session (human ↔ Claude Code, not Brain, not a worker)? This design answers that question and produces a research document at `docs/research/d1277-assumption-surfacing-direct-sessions.md`.

**Direct answer: conditional yes.** The directive adds value in direct sessions, but only for the surface a direct session covers that a code-change session does not — and it needs a different trigger phrase than Brain's version.

## Architecture (of the analysis)

The core finding is a scope split, not a flat yes/no:

- **Code changes in a direct session already have structural coverage.** Professional mode's brainstorm → plan → execute workflow forces `AskUserQuestion`-driven clarification before any code change — the same mechanism d1275 credited for workers. Adding the new directive here would be redundant, exactly as d1275 concluded for workers.
- **Everything else a direct session does — Q&A, code explanations, advice, and quick non-code actions — has no such gate**, regardless of whether professional mode is on (professional mode's workflow requirement is explicitly scoped to "code changes"; see `~/.claude/CLAUDE.md` line 1). This is most of direct-session usage by volume, and the only proactive-assumption-check that exists in the file is reactive ("Challenge Assumptions" #1, "Persistent Questioning" #4) — same gap d1275 found at the Brain level, just one layer down.
- This split is also why d1277 is *sharper* than d1275, not just a repeat: a wrong assumption in a direct answer ships straight to the human with **no downstream review** (no code review gate, no worker verification pass) — unlike a wrong Brain assumption, which at least gets caught by a worker's own brainstorming or a later code review.

## Research Questions

**RQ1 — Does it add genuine value in direct sessions vs. Brain/worker?**
Yes, for the ungated surface (non-code substantive answers/actions). No, for the code-change surface (already covered structurally). Net: value is real but narrower than "all direct-session interactions."

**RQ2 — Is there an existing structural mechanism, like workers' AskUserQuestion?**
Partially. Code changes get it via professional mode's brainstorming gate. Q&A/advice/explanation/quick-action requests get nothing structural — only the reactive directives (#1, #4), which fire on noticed ambiguity, not proactively.

**RQ3 — Does the Compressed Output tension (#10) apply the same way?**
It applies more directly here than at Brain. Brain's `behavioral.md` has no Compressed-Output-equivalent directive at all (Brain's directive list is 21 items, none of which govern hedging/verbosity toward Robert). The new directive would sit in the *same file* as #10 in the direct-session case, so the assumptions-≠-hedging boundary must be explicit and cross-referenced, not left implicit as it effectively was for Brain (different files, no adjacency).

**RQ4 — Should directive text differ between global and Brain versions?**
Yes. Brain's trigger — "when interpreting a new directive or reviewing a worker plan" — describes an orchestration-specific moment that doesn't exist in a direct session. The direct-session equivalent is: "before giving a substantive answer or acting on an ambiguous or underspecified request." The direct-session version also needs an explicit carve-out for the brainstorming/AskUserQuestion path, which Brain's version doesn't need (Brain has no such gate to defer to).

**RQ5 — Concrete examples**
1. *Value case:* User says "clean up this function." "Clean up" could mean readability-only refactor, or could implicitly include a bug fix. One bullet surfacing that reading prevents silent scope drift in the answer/action that follows — and this happens *before* any brainstorming gate would trigger (a one-liner request doesn't always route through full brainstorming if professional mode is off or the ask is answered inline).
2. *Noise case:* "What's the git status" / "run the tests." Routine status queries — the existing carve-out ("skip for routine operations and status queries") already exempts these, and it must carry over to the direct-session version verbatim.
3. *Already-covered case:* "Add a logout button" with professional mode ON. This already routes into brainstorming, which already forces explicit clarifying questions per-section. Stacking a prose assumptions-bullet in front of that would be redundant noise — hence the explicit carve-out in RQ4's proposed text.

## Recommendation

Add to `~/.claude/CLAUDE.md`, as a new Core Principle (#11, since the list currently ends at #10), text distinct from the Brain version:

**Brain version (existing, `behavioral.md` #21 — unchanged):**
> **21. Surface Assumptions Before Acting** — When interpreting a new directive or reviewing a worker plan, briefly state any material assumptions you're making and what information gaps could change the answer (1–3 bullets max). Skip for routine operations and status queries. Keep it brief — this is not a hedging exercise, it's a mis-assumption catch.

**Global CLAUDE.md version (new, proposed #11):**
> **11. Surface Assumptions Before Acting** — Before giving a substantive answer or acting on an ambiguous or underspecified request, briefly state any material assumptions you're making and what information could change the answer (1–3 bullets max). Skip for routine operations, status queries, and anything already covered by the brainstorming/AskUserQuestion workflow (professional mode's clarifying step already handles code changes). This is not a hedging exercise — see directive #10.

## Testing Strategy

Not applicable — this is a documentation/directive-text deliverable, not code. Verification is: the research doc exists at the specified path, answers all 5 RQs with evidence (not speculation), and gives an unambiguous recommendation with exact directive text.

## Implementation Notes

- Single deliverable: `docs/research/d1277-assumption-surfacing-direct-sessions.md`.
- Do not modify `~/.claude/CLAUDE.md` itself as part of this task — the brief asks for a research document with a recommendation, not the directive's implementation. Adding directive #11 to the live file is a separate decision for the operator to action after reading the research doc (mirrors how d1275 was research-then-separate-implementation).
- No code changes, no test changes, no other files touched.
