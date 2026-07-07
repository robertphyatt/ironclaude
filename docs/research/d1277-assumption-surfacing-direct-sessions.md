---
title: Assumption Surfacing in Direct User Sessions — Follow-up to d1275 (d1277)
updated: 2026-07-05
description: Extends d1275's assumption-surfacing directive analysis to direct user sessions (human interacting with Claude Code directly, not Brain, not a worker).
---

## Robert's Question (d1277)

> "would this help users that are directly interacting with a session? Or do we need to account for that?"

Context: d1275 recommended adding a "Surface Assumptions Before Acting" directive to `~/.ironclaude/brain/.claude/rules/behavioral.md` (Brain-specific, now directive #21), and explicitly recommended **against** adding it to global CLAUDE.md — reasoning that workers already get this behavior structurally, via PM brainstorming's `AskUserQuestion` mechanism.

"Direct user sessions" = a human using Claude Code or Claude directly — not the Brain orchestrator, not a worker. These sessions load `~/.claude/CLAUDE.md` and `~/.ironclaude/CLAUDE.md`.

## Direct Answer

**Conditional yes.** The directive adds genuine value in direct sessions, but not uniformly across everything a direct session does — and if added, it needs a trigger phrase distinct from Brain's version. A flat "yes, same as Brain" or flat "no, same reasoning as workers" both miss the actual shape of the gap.

## Analysis

### RQ1 — Does it add genuine value in direct sessions, as opposed to Brain or worker sessions?

Split answer:
- **For code-change requests in a direct session:** no incremental value. Professional mode's brainstorm → plan → execute workflow already forces `AskUserQuestion`-driven clarification before any code change — structurally identical to the mechanism d1275 credited for workers. This document was itself produced through that exact gate.
- **For everything else a direct session does** — answering questions, explaining code, giving advice, or acting on a quick, ambiguous one-liner — there is no structural gate at all, regardless of whether professional mode is on. Professional mode's workflow requirement is explicitly scoped to "code changes" (see `~/.claude/CLAUDE.md`, opening line), not to conversation in general. This is the majority of direct-session interaction by volume, and it currently relies only on the *reactive* directives — "Challenge Assumptions" (#1) and "Persistent Questioning" (#4) — which activate only once ambiguity is noticed. That is the same class of gap d1275 identified at the Brain level: not that ambiguity goes unaddressed once seen, but that the model can act confidently on an assumption it never noticed making.
- One respect in which the direct-session case is **more** consequential than the Brain case: a Brain mis-assumption is expensive (costs a 60-90 minute worker cycle) but still gets a second look — the worker's own brainstorming, or a later code review, can catch it. A wrong assumption baked into a direct conversational answer ships straight to the human as the terminal output of the turn, with no downstream verification pass of any kind.

### RQ2 — Is there an existing structural mechanism, like workers' AskUserQuestion?

Partially, and only for the code-change subset. Professional mode's brainstorming gate supplies it there. For Q&A, explanation, advice, and ad hoc non-code requests, no structural mechanism exists — only the reactive directives noted above. The directive fills a real (if narrower-than-Brain's) gap on that uncovered surface.

Episodic memory search turned up no prior discussion of applying assumption-surfacing selectively by session type; historical practice has been to apply the *existing* core directives (Challenge Assumptions, Persistent Questioning, Search Before Guessing, etc.) uniformly across direct, worker, and orchestrator sessions. That precedent is some evidence in favor of extending coverage rather than leaving direct sessions out — but it doesn't settle *how* the new directive should be scoped, since none of those prior directives were the proactive, pre-action kind d1275/d1277 are about.

### RQ3 — Does the "Compressed Output" (#10) tension apply the same way here as it did for Brain?

It applies more directly. Brain's `behavioral.md` has no Compressed-Output-equivalent directive — Brain's 21 directives don't govern verbosity/hedging toward Robert at all. In the direct-session case, the new directive would sit in the *same file* (`~/.claude/CLAUDE.md`) as directive #10, immediately below it in the numbered list. That co-location means the assumptions-≠-hedging boundary needs to be explicit and self-referencing, not merely implied by separation into different files as was effectively the case for Brain. The distinction itself is unchanged from d1275: assumptions are factual unknowns ("I'm reading this as X — if you meant Y, the answer changes"); hedging is probability disclaimers ("this might work but I'm not sure"). Only the first is useful; the new directive's text needs to say so explicitly and point at #10.

### RQ4 — Should the directive text differ between the global and Brain versions?

Yes. Brain's trigger — "when interpreting a new directive or reviewing a worker plan" — names an orchestration-specific moment (reading an instruction from Robert, or reviewing a worker's proposed plan) that has no equivalent in a direct session. The direct-session trigger needs to be: **"before giving a substantive answer or acting on an ambiguous or underspecified request."** That covers the actual uncovered surface identified in RQ1 (Q&A, explanations, advice, quick actions) without re-describing Brain's orchestration context.

The direct-session version also needs something Brain's doesn't: an explicit carve-out for work already routed through the brainstorming/`AskUserQuestion` gate. Without it, a direct session with professional mode on would double up — a prose "here are my assumptions" bullet stacked in front of a brainstorming step that already asks the same clarifying question structurally. Brain has no equivalent gate to defer to, so its version doesn't need this carve-out.

### RQ5 — Concrete examples

1. **Value case:** User asks to "clean up this function." "Clean up" could mean a pure readability refactor, or could implicitly include fixing a bug noticed along the way. A one-line assumption ("reading this as style-only, not touching logic — say so if you want the bug fixed too") heads off silent scope drift, and this fires *before* any brainstorming gate would necessarily trigger — a short one-liner like this doesn't always route through full brainstorming, especially if professional mode is off or the request is answered inline as a quick edit.
2. **Noise case:** "What's the git status" / "run the tests." Routine status queries. The existing carve-out — "skip for routine operations and status queries" — must carry over verbatim to the direct-session version; these are exactly the case where a proactive assumptions-bullet would be pure noise.
3. **Already-covered case:** "Add a logout button," professional mode ON. This routes into brainstorming immediately, which already asks explicit clarifying questions per section (predict + `AskUserQuestion`, one at a time). Stacking a prose assumptions-bullet in front of that flow would just be redundant chatter ahead of the real question — this is the case RQ4's carve-out exists to prevent.

## Recommendation

**Add to `~/.claude/CLAUDE.md`** as a new Core Principle — **#11** (the list currently ends at #10) — with text distinct from Brain's version. Do not modify Brain's existing directive.

### Side by side

**Brain version (existing, `~/.ironclaude/brain/.claude/rules/behavioral.md`, directive #21 — unchanged):**

> **21. Surface Assumptions Before Acting** — When interpreting a new directive or reviewing a worker plan, briefly state any material assumptions you're making and what information gaps could change the answer (1–3 bullets max). Skip for routine operations and status queries. Keep it brief — this is not a hedging exercise, it's a mis-assumption catch.

**Global CLAUDE.md version (new, proposed as Core Principle #11):**

> **11. Surface Assumptions Before Acting** — Before giving a substantive answer or acting on an ambiguous or underspecified request, briefly state any material assumptions you're making and what information could change the answer (1–3 bullets max). Skip for routine operations, status queries, and anything already covered by the brainstorming/AskUserQuestion workflow (professional mode's clarifying step already handles code changes). This is not a hedging exercise — see directive #10.

## Status

Research complete. This document is a recommendation only. Adding directive #11 to the live `~/.claude/CLAUDE.md` (and, if desired, the corresponding project-level CLAUDE.md copies) is a separate implementation decision for the operator, mirroring how d1275 was research-then-separate-implementation.
