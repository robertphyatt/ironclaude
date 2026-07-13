# Graphify Investigation Synthesis Document Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Produce `docs/graphify-investigation.md`, synthesizing the already-completed Graphify evaluation (research doc + two wiki pages) into a single document answering five specific questions about whether Graphify should become a third IronClaude knowledge layer.

**Architecture:** Single-pass synthesis over three existing, final source documents. No new research, no re-running Graphify. Read sources, map content onto the five required questions, write one markdown file, stage it.

**Tech Stack:** Markdown only. No code, no tests (pure documentation task).

---

## Task 1: Write docs/graphify-investigation.md

**Files:**
- Create: `docs/graphify-investigation.md`

**No tests required:** Pure documentation synthesis — no executable code, no test framework applies. Correctness is verified by manual cross-check against source documents (Step 3 below), not automated tests.

**Step 1: Read source documents (already read once during brainstorming; re-read for the writing pass to keep steps self-contained)**

Read in full:
- `docs/research/2026-07-13-graphify-evaluation.md` — primary source, contains layer comparison table, gap analysis, overlap assessment, integration cost, recommendation.
- `~/.ironclaude/brain/wiki/graphify-evaluation.md` — per-repo verdict table, "why not a layer" bullet list, when-to-revisit conditions.
- `~/.ironclaude/brain/wiki/ironclaude-knowledge-stack.md` — current two-layer architecture description (episodic memory + wiki), decision context/history (d1341/d1342 rejections, d1343 evaluation).

Expected: all three files read successfully; no new facts needed beyond what's already in these sources.

**Step 2: Write `docs/graphify-investigation.md`**

Structure the document around the five required questions. Content must be drawn from the source documents — no new claims, no independent analysis. Use this outline:

```markdown
# Graphify Investigation: Should It Become a Third Knowledge Layer?

> **Date:** 2026-07-13
> **Status:** Complete — synthesizes hands-on evaluation from session d1343
> **Sources:** `docs/research/2026-07-13-graphify-evaluation.md`, `wiki/graphify-evaluation.md`, `wiki/ironclaude-knowledge-stack.md`

## Background

[2-3 sentences: what Graphify is, why it was evaluated — third-layer question alongside episodic memory + wiki, evaluated hands-on against ironclaude and roleplaying-agents repos.]

## 1. What Graphify provides vs. what the wiki + episodic memory already cover

[Layer comparison table from research doc section 1: episodic memory answers, wiki answers, Graphify answers — each column's "answers well" / "doesn't answer".]

## 2. Where the layers are complementary vs. redundant

[From research doc section 3 (Overlap assessment): no overlap found — wiki is narrative "why", Graphify is structural "what's connected to what". From gap analysis: complementary where Graphify fills structural/connectivity gaps neither other layer tracks (import cycles, god-node ranking); redundant/no-better-than-Grep where file:line lookup is the only claimed benefit.]

## 3. Concrete value to workers / Brain

[From research doc: God Nodes ranking as file:line-precise centrality view; import-cycle detection (not tracked today); explicitly note what's NOT genuinely new — file:line lookup is already achievable via Grep in seconds. Note the GDScript/roleplaying-agents gap as a concrete limit on value.]

## 4. Integration complexity estimate

[From research doc section 4: install cost (~1.3s, `uv tool install graphifyy`), two-command workflow (extract + cluster-only) vs. quickstart's single command, --code-only requirement for local-first behavior, HTML artifact failure above 5,000 nodes (both test repos exceeded this), graphify-mcp shipped but not tested, `graphify update` for incremental re-extraction not tested.]

## 5. Go/no-go recommendation

[Direct quote/paraphrase of the source recommendation: do NOT adopt as a third knowledge layer for either repo. ironclaude: WEAK YES as an occasional, manually-invoked tool — not a layer. roleplaying-agents: NO — zero GDScript coverage. Revisit condition: future Graphify release adding tree-sitter-gdscript support.]

## Relationship to prior evaluations

[Brief note on d1266 (agent-memory-techniques) non-transfer, per research doc section 2 reconciliation.]
```

Write the actual file with real content drawn from the three sources (the outline above is a skeleton, not literal text to insert) — expand each bracketed section into full prose/tables using the source material's actual findings, numbers, and file:line examples.

Expected: `docs/graphify-investigation.md` exists, all five questions addressed with content traceable to the three source files, recommendation matches source recommendation exactly (no adopt/reject flip).

**Step 3: Self-check against sources**

Re-read the newly written `docs/graphify-investigation.md` alongside the three source files. Confirm:
- No claim in the new doc contradicts a source document
- The recommendation section matches the source recommendation (do not adopt as a layer; ironclaude weak-yes as occasional tool; roleplaying-agents no)
- All five required questions have a clearly-labeled section with substantive content (not placeholder text)

Expected: no discrepancies found.

**Step 4: Stage changes**

`docs/` is gitignored in this repo; plan/review/doc artifacts are staged with `-f` by established convention.

Run:
```bash
git add -f docs/graphify-investigation.md
```

Expected: `docs/graphify-investigation.md` staged (professional mode blocks commit).

---
