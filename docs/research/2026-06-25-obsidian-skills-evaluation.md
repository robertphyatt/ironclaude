# Obsidian Skills Evaluation — IronClaude Brain Wiki Layer

> **Date:** 2026-06-25
> **Purpose:** Evaluate whether kepano/obsidian-skills can improve the IronClaude Brain wiki layer.

---

## 1. Obsidian Skills Architecture

### Repository Overview

`kepano/obsidian-skills` (37.9k stars, MIT license) provides agent behavioral instruction sets
compatible with Claude Code, Codex, and Open Code. It teaches agents how to operate within
the Obsidian ecosystem.

### Directory Structure

```
obsidian-skills/
├── .claude-plugin/
│   ├── plugin.json         # Marketplace discovery metadata
│   └── marketplace.json
└── skills/
    ├── obsidian-markdown/
    │   ├── SKILL.md        # Behavioral instructions
    │   └── references/     # Supplementary reference docs
    │       ├── CALLOUTS.md
    │       ├── EMBEDS.md
    │       └── PROPERTIES.md
    ├── obsidian-cli/
    │   └── SKILL.md
    ├── obsidian-bases/
    │   └── SKILL.md
    ├── json-canvas/
    │   └── SKILL.md
    └── defuddle/
        └── SKILL.md
```

### SKILL.md Format

Each skill is a markdown file with two-field YAML frontmatter:

```yaml
---
name: obsidian-cli
description: Interact with Obsidian vaults using the Obsidian CLI to read, create,
  search, and manage notes, tasks, properties, and more. Also supports plugin and
  theme development... Use when the user asks to interact with their Obsidian vault...
---
```

The `name` field is a kebab-case identifier. The `description` field is a deliberately
authored routing hint — it tells the agent when to load this skill. The body is freeform
markdown: command syntax, usage patterns, common workflows, pitfalls.

### Plugin Discovery

`.claude-plugin/plugin.json` provides marketplace metadata:

```json
{
  "name": "obsidian",
  "version": "1.0.1",
  "description": "Create and edit Obsidian vault files...",
  "author": { "name": "Steph Ango", "url": "https://stephango.com/" },
  "repository": "https://github.com/kepano/obsidian-skills",
  "keywords": ["obsidian", "markdown", "bases", "canvas", "pkm", "notes"]
}
```

### The 5 Skills

| Skill | Purpose |
|---|---|
| `obsidian-markdown` | Obsidian-flavored markdown: wikilinks, callouts, embeds, properties |
| `obsidian-cli` | CLI commands for vault read/write/search, daily notes, task management, plugin dev |
| `obsidian-bases` | Create/edit `.base` files (database views with filters, formulas, summaries) |
| `json-canvas` | Work with `.canvas` files (nodes, edges, groups) |
| `defuddle` | Extract clean markdown from web pages to reduce token usage |

### Key Characteristics

- **No search mechanism.** No index. No cross-skill linking. No query API.
- **Static content.** No update workflow — files are manually authored and maintained.
- **No knowledge synthesis.** Skills document tool interfaces, not accumulated knowledge.
- **Freeform body.** Each SKILL.md has a completely different internal structure —
  `obsidian-cli` is a command reference with bash blocks; `obsidian-bases` reads like a
  tutorial; `defuddle` is a quick-reference card. No body schema is enforced.
- **Narrow scope.** All 5 skills are Obsidian-tool-specific; not a general knowledge framework.

---

## 2. Feature Comparison Table

| Dimension | IronClaude Wiki | Obsidian Skills |
|---|---|---|
| **Content type** | Knowledge synthesis: decisions, patterns, incidents, architectural facts | Behavioral instructions: how to use specific tools (command syntax, workflows) |
| **Search mechanism** | Two-pass keyword grep: index entries first, then full-text content; returns ranked JSON array | None — no search, no index, no query API |
| **Metadata schema** | `title` + `updated` (date) in frontmatter; auto-built `index.md` table with title + 1-line summary + date | `name` + `description` (authored routing hint) in frontmatter only; no index |
| **Description field** | Auto-extracted: first sentence of body, truncated at 120 chars by `_extract_summary()` | Explicitly authored: agent writes a deliberate routing statement for each skill |
| **Body schema** | None enforced — pages vary: some start `## Overview`, some `## Problem`, some `## Pattern` | None enforced — each SKILL.md has a different internal structure |
| **Update workflow** | `wiki_write` MCP tool → validates name + content → rebuilds `index.md` → auto-commits via git | Manual file edits; no tooling |
| **Validation** | Rejects directive-prefixed names (`d123-*`), date-stamped names, placeholder content (<50 chars), garbage | None |
| **Large page handling** | Monolithic only — no decomposition mechanism | Optional `references/` subfolder for supplementary material |
| **Multi-project support** | Single `~/.ironclaude/brain/wiki/` per instance | Per-vault installation into `/.claude` |
| **Routing/discovery** | `wiki_query(keywords)` returns relevant pages via keyword grep | Agent loads skills on demand based on task context |
| **Scale** | 480+ pages, continuously growing | 5 static skills |
| **Auto-commit** | Yes — `wiki_write` runs `git add wiki/ && git commit` | No |
| **Duplicate detection** | Yes — warns on >60% keyword overlap with existing pages | No |

---

## 3. Problem-Solution Mapping

The June 2026 wiki quality audit (directive #1035) identified three recurring problems.
Each is evaluated against Obsidian Skills.

### Problem 1: Directive-specific logs accumulating as wiki pages

**Current state:** Brain writes pages like `heartbeat-task-extraction.md` (documents commit
`2660c9e`) or `portrait-shadow-fix.md` (documents commit `18455abeb`) — single-incident
records that decay as the codebase evolves.

**Does Obsidian Skills address this?** No. Obsidian Skills doesn't have a wiki layer at all.
Its skills are hand-authored behavioral reference docs, not a synthesis system. Adopting
its format would still require the same behavioral discipline from Brain to avoid writing
incident logs vs. reusable concepts. The existing `wiki_write` validation rules (rejecting
`d<N>-` prefixes, date-stamped names) already address this more directly than anything in
Obsidian Skills.

### Problem 2: Keyword-only search misses semantic matches

**Current state:** `wiki_query` splits on spaces and does substring matching against index
and page content. Semantically related pages with different vocabulary won't surface.

**Does Obsidian Skills address this?** Strictly worse. Obsidian Skills has no search at all.
Its skills are loaded by the agent based on task context, not queried by keyword or semantics.
Adopting Obsidian Skills format would mean replacing a functional (if limited) keyword search
with nothing.

### Problem 3: No cross-page relationship tracking

**Current state:** Wiki pages are islands. `wiki_query` surfaces related pages by keyword,
but there's no explicit link graph.

**Does Obsidian Skills address this?** No. Individual `SKILL.md` files are also islands.
The Obsidian app supports wikilinks, but the skills repo doesn't use them in skill content.
No link graph exists.

**Summary:** Obsidian Skills addresses zero of the three known wiki quality problems. The
systems operate at different abstraction levels.

---

## 4. Integration Assessment

### Scenario A: Full adoption of Obsidian Skills format

If wiki pages were converted to `SKILL.md` format:

| Current capability | After adoption |
|---|---|
| `wiki_query` two-pass search | **Lost** — no equivalent in Obsidian Skills |
| Auto-index rebuild on write | **Lost** — no index concept |
| Page name validation | **Lost** — no validation in Obsidian Skills |
| Auto-git commit on write | Preserved (keep in `wiki_write`) |
| Duplicate detection | **Lost** — no equivalent |
| Knowledge synthesis content | **Broken** — `description` field not designed for paragraph-length synthesis |

**Estimated effort:** 2-3 dev days to implement. Net result: capability regression across
all dimensions. Reject.

---

### Scenario B: Borrow the `description` frontmatter pattern

**What it is:** Obsidian Skills uses `description` as an explicitly authored routing
statement — "Use when the user asks to interact with their Obsidian vault..." — written
by the skill author to match agent task context. This is distinct from the wiki's
`_extract_summary()`, which mechanically extracts the first sentence of the body and
truncates at 120 chars.

**The gap:** Auto-extracted summaries are unreliable as routing signals. A page like
`pf2e-pipeline-player-core-status.md` has a first line of `## Status: HALTED (d1161) —
Fixing adversarial review issues` — which makes a poor `wiki_query` search signal compared
to a deliberately written summary like "PF2e Player Core pipeline status and blocking
issues as of June 2026."

**What changes:**
- Add `description` as a third frontmatter field in `wiki_write`
- Brain explicitly writes a routing-oriented description when creating a page
- `wiki_query` uses the `description` field (not just the auto-extracted first sentence)
  for index matching
- `_rebuild_wiki_index()` uses `description` over auto-extracted summary

**Changes to `wiki_tools.py`:**
- `wiki_write` signature: add `description: str` parameter (optional, falls back to
  auto-extract if not provided)
- `_parse_wiki_frontmatter`: parse `description` field
- `_rebuild_wiki_index`: use `description` if present
- Estimated: ~30 lines changed

**Value:** Meaningful — improves wiki_query precision for all pages where Brain writes
a deliberate description. Backward-compatible (existing pages degrade gracefully to
auto-extract). Low risk.

---

### Scenario C: Borrow the `references/` subfolder pattern

**What it is:** `obsidian-markdown/references/` holds `CALLOUTS.md`, `EMBEDS.md`,
`PROPERTIES.md` — supplementary reference material decomposed out of the main `SKILL.md`.
The main file stays focused; references hold the detail that matters for specific subtasks.

**Applied to the wiki:** A large page like `pf2e-rules-structuring-pipeline.md` (architecture
+ decisions + pipeline stages + known bugs) could decompose into:

```
wiki/pf2e-rules-structuring-pipeline.md              # Summary + key decisions (~200 words)
wiki/pf2e-rules-structuring-pipeline/references/
  stage-1-extraction.md
  stage-2-contamination.md
  contamination-model-decisions.md
```

**The real problem it addresses:** At 100+ lines, a page's first-sentence summary becomes
misleading — `wiki_query` returns it, but Brain must consume the entire file to find a
specific section. Decomposition lets `wiki_query` surface specific reference files rather
than a monolith.

**Tooling impact (not zero-change):**

Current `_rebuild_wiki_index()` uses `os.listdir(wiki_dir)` — top-level only. Current
`wiki_query` phase 2 also iterates top-level only. Reference subfiles are completely
invisible to both.

Changes required:
- `wiki_query` phase 2: recursive walk into `*/references/` subdirs
- `_rebuild_wiki_index`: option to include reference files as sub-entries under parent page
- `wiki_write`: support writing to `<page>/references/<ref>.md` subpaths (path validation
  must allow one level of subdirectory under wiki root)
- Design question: do reference files get their own top-level index entries, or are they
  sub-entries under the parent page? If top-level, they pollute the 480+ page index. If
  sub-entries, the index format must change.

**Estimated effort:** 1-2 dev days. Non-trivial design decision on index representation.

**Value:** Medium — addresses a real scaling problem as pages grow. But at current scale
(480 pages, unknown proportion over 100 lines), the value is conditional. Warrants a
design spike to measure actual page size distribution before committing.

---

## 5. Recommendation

### Primary verdict: Reject full adoption

Obsidian Skills and the IronClaude wiki solve different problems:

- **Obsidian Skills** = behavioral instruction sets for tool use (how agents use Obsidian)
- **IronClaude wiki** = knowledge synthesis memory (what happened, what was decided, what patterns emerged)

Full adoption would lose search, validation, auto-index, and duplicate detection with
no compensating benefit. The format differences are incidental — both use markdown with
YAML frontmatter — but the purpose difference is categorical.

### Pattern 1: Adopt `description` field (recommended, low effort)

Add an optional `description` frontmatter field to `wiki_write`. When Brain writes a new
page, it provides a deliberately authored routing statement — not just the first line of
content. This improves `wiki_query` precision for all new pages and degrades gracefully
for existing pages.

This is the most directly borrowable pattern from Obsidian Skills and addresses a real
weakness (auto-extracted summaries as routing signals).

### Pattern 2: Evaluate `references/` decomposition (deferred, medium effort)

The references pattern addresses a real scaling problem but requires a non-trivial design
decision (how reference files integrate with the index). Before building:

1. Measure actual page size distribution across the 480-page corpus
2. Identify the 10 largest pages and assess whether decomposition would help
3. Design the index representation (sub-entries vs. top-level entries)

If more than ~10% of pages exceed 150 lines, the effort is justified. Do not build
speculatively.

### What Obsidian Skills does NOT address

The wiki's actual improvement opportunities beyond the two patterns above:
- Semantic (embedding-based) search
- Cross-page relationship tracking / link graph
- Enforced body schema by page type (decision, pattern, incident, architecture)

None of these are addressed by Obsidian Skills. Each requires separate evaluation.

### Action items

| Action | Priority | Effort |
|---|---|---|
| Add `description` frontmatter field to `wiki_write` | Medium | ~30 lines |
| Measure page size distribution to assess references need | Low | 1 hour |
| Design `references/` index integration if warranted | Low, deferred | 1-2 dev days |
| Reject full Obsidian Skills adoption | — | — |
| Continue existing `wiki_write` / `wiki_query` / `wiki_delete` tooling | — | — |
