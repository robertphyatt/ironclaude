# d1277: Assumption Surfacing in Direct Sessions — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Produce the d1277 research document answering whether "surface assumptions before acting" should extend to direct user sessions, staged at `docs/research/d1277-assumption-surfacing-direct-sessions.md`.

**Architecture:** Single documentation deliverable. Content is fully specified by the design doc (`docs/plans/2026-07-05-assumption-surfacing-direct-sessions-design.md`) — this plan transcribes that analysis into the final research doc format the operator requested (direct answer, RQ1-5, recommendation with both directive texts side by side).

**Tech Stack:** Markdown only. No code, no tests (pure documentation, no executable surface).

---

## Task 1: Write d1277 research document

**Files:**
- Create: `docs/research/d1277-assumption-surfacing-direct-sessions.md`

**No tests required:** pure documentation deliverable — no executable code, no test framework applies.

**Step 1: Write the research document**

Create `docs/research/d1277-assumption-surfacing-direct-sessions.md` with:
- Frontmatter (title, updated date, description) matching the style of `~/.ironclaude/brain/wiki/assumption-surfacing-directive.md`
- Direct answer to Robert's question
- Analysis of RQ1-RQ5 (content drawn from the design doc's "Research Questions" section)
- Final recommendation section with both directive texts (Brain's existing #21, and the new proposed global CLAUDE.md #11) shown side by side
- Status line noting this is research, pending operator decision on whether to add directive #11 to `~/.claude/CLAUDE.md`

**Step 2: Verify content completeness**

Read the file back and confirm all 5 research questions are answered and both directive texts are present verbatim as specified in the design doc.

**Step 3: Stage changes**

Run:
```bash
git add -f docs/research/d1277-assumption-surfacing-direct-sessions.md
```

Expected: Change staged (professional mode blocks commit; docs/ is gitignored by default so `-f` is required, matching the existing tracked files under docs/research/).

---
