# Graphify Investigation: Should It Become a Third Knowledge Layer?

> **Date:** 2026-07-13
> **Status:** Complete — synthesizes the hands-on evaluation from session d1343
> **Sources:** `docs/research/2026-07-13-graphify-evaluation.md`, `~/.ironclaude/brain/wiki/graphify-evaluation.md`, `~/.ironclaude/brain/wiki/ironclaude-knowledge-stack.md`

## Background

Graphify is a CLI tool (YC S26, ~83.5k GitHub stars) that parses a codebase via tree-sitter AST and produces a queryable knowledge graph: cross-file relationships (calls, imports, inheritance), community/cluster detection (Leiden algorithm), a ranked list of the most-connected components ("god nodes"), and three output artifacts (interactive HTML graph, `GRAPH_REPORT.md`, `graph.json`). It ships `graphify-mcp`, an MCP server for native Claude Code integration.

Robert asked whether Graphify should become a third Brain knowledge layer, co-equal with the two that already exist — **episodic memory** (semantic search over past conversation archives) and the **wiki** (curated markdown pages synthesized from episodic memory, one page per concept). Two earlier directive submissions on this question (d1341, d1342) were rejected for unclear reasons before a third (d1343) was accepted. d1343 tested Graphify hands-on — installed v0.9.14 and ran it against two real repos, `ironclaude` and `roleplaying-agents` — rather than evaluating it from README claims alone. That hands-on evaluation is the sole evidentiary basis for this document; nothing below extends or re-runs it.

## 1. What Graphify provides vs. what the wiki + episodic memory already cover

| Layer | Answers well | Doesn't answer |
|---|---|---|
| **Episodic memory** | "What was decided/discussed about X historically" — past incidents, operator preferences, behavioral history across sessions | Structural/connectivity questions about current code ("what's most connected," "where exactly does X live") |
| **Wiki** | "What is the current known pattern/preference for X" — synthesized, human-curated narrative and decision rationale | Exhaustive file:line-level structural facts; requires someone to have already written the page; doesn't self-update as code changes |
| **Graphify** | Structural/connectivity facts with concrete file:line precision — most-connected components, community/cluster groupings, import-cycle detection, cross-file call/inheritance edges | Narrative "why" behind decisions; behavioral history; requires periodic re-running to stay current; usefulness is language-dependent |

Concrete example from the evaluation: the session already had conceptual knowledge (from episodic memory and prior context) that `ironclaude`'s architecture includes a daemon, a worker registry, a Brain client, and Ollama-based grading — but no file:line locations for any of it. Running Graphify against `ironclaude` and querying it directly produced exact locations (`WorkerRegistry` → `worker_registry.py:13`, `IroncladeDaemon` → `main.py:640`, `LocalGrader` → `grader.py:28`) matching the known architecture, with more file-level precision than either existing layer stores today.

## 2. Where the layers are complementary vs. redundant

**No overlap was found** between Graphify's output and existing wiki content during the cross-check against `ironclaude`'s architecture. The wiki (e.g. `project_singleton_enforcement.md`, `project_hook_deploy_architecture.md`) is synthesized narrative — decisions, gotchas, and the reasoning behind them. Graphify's output is structural — connectivity counts and file:line facts, with no narrative content. Where they'd be genuinely **complementary**: the wiki explains *why* a design choice was made; Graphify would show *what* is structurally connected to what, at a precision no existing wiki page attempts to track (import-cycle detection, for instance, isn't tracked by either current layer at all).

Where the case for adoption is weaker — closer to **redundant with what's already achievable**: the headline "gap-filling" example above (file:line locations for known components) is also directly obtainable with `Grep` in seconds, using knowledge the Brain already has from episodic memory. That specific capability doesn't justify adopting a new tool on its own. The genuinely new capability — God Nodes centrality ranking and import-cycle detection — is real but narrow: a ranked/aggregated view of information already reachable by reading the code, not a new class of question the Brain couldn't previously answer.

## 3. Concrete value to workers / Brain

**Real, if modest, value for `ironclaude`:**
- **God Nodes ranking** — a ranked list of the most-connected components, sparing a worker from having to read and mentally aggregate the whole codebase to answer "what's core here." Verified: the top 10 matched known architecture.
- **Import-cycle detection** — a binary check ("does this codebase have cycles?") that neither the wiki nor episodic memory track today. None were found in either test repo, but the check itself is new coverage.
- **Faster orientation in unfamiliar territory** — for a worker or the Brain encountering an unfamiliar part of a codebase before a refactor, a pre-generated god-node list is a plausible shortcut versus a cold Grep/Read pass.

**Limits on that value:**
- **Language-dependent, not universal.** For `roleplaying-agents` — where the primary language is GDScript (876 files) — Graphify produced **zero graph nodes**, directly contradicting the README's "36+ languages including GDScript" claim. The repo where structural understanding arguably matters most got no coverage at all.
- **The `query` interface was noisy** enough in testing that the static `GRAPH_REPORT.md`, not live querying, had to be used to get a clean answer — undercutting the "ask it a question" value proposition for interactive worker use.
- **Community/cluster labels are unlabeled** (`"Community N"` placeholders) without an LLM API key, which this environment doesn't have configured — so the clustering feature delivers no narrative value in the tested (local-first) mode.

Net: some concrete orientation value for Python/TypeScript repos like `ironclaude`, essentially none for GDScript-heavy repos like `roleplaying-agents`, and the value that does exist is a convenience layer over what Read/Grep already give a worker — not a new capability class.

## 4. Integration complexity estimate

- **Install cost: low.** `uv tool install graphifyy` (PyPI package `graphifyy`, double-y), Python 3.10+, ~1.3 seconds measured, no errors. Installs both `graphify` (CLI) and `graphify-mcp` (MCP server for Claude Code).
- **Generation cadence: two-command workflow, not one.** The bare `graphify .` shown in the README quickstart is not sufficient. Non-interactive/CI use requires `extract <path> --code-only` (produces only `graph.json`), followed by a second `cluster-only` command to produce `GRAPH_REPORT.md` and attempt `graph.html`. This had to be discovered by reading `--help` output, not the quickstart docs.
- **"Local-first" is conditional, not default.** Without `--code-only`, `extract` defaults to an LLM-backed semantic pass requiring an API key (gemini/kimi/claude/openai/deepseek/ollama) — none configured in this environment. `--code-only` is required to get genuinely local, no-API-key behavior; no evidence of unwanted network activity was found under that flag.
- **HTML artifact reliability is poor at real-world scale.** Both test repos exceeded Graphify's own default 5,000-node visualization limit (`ironclaude`: 5,018 nodes; `roleplaying-agents`: 138,049 nodes), and `graph.html` was silently skipped both times. For any moderate-or-larger repo, one of the three headline artifacts won't be produced without an environment-variable override.
- **Staleness / re-run mechanism exists but is untested.** `graphify update <path>` is provided for incremental re-extraction ("no API cost" per its own help text) — a reasonable story for keeping a graph current, but the evaluation did not test this path, so its reliability is unverified.
- **MCP integration path exists but is untested.** `graphify-mcp` substantiates the README's native Claude Code integration claim beyond documentation, but wiring it into a live Claude Code session was not tested in this evaluation.
- **Data-quality risk at scale.** In `roleplaying-agents` (dominated by vendored `llama.cpp` C++ and other third-party code), ~15 cross-chunk ID collisions were logged where generically-named files (e.g. two different `main.swift`) caused one node to be silently dropped rather than merged. One language extra (`tree-sitter-sql`) also isn't installed by default and must be added separately.

Overall integration complexity: low install cost, but real operational friction (two-command workflow, HTML unreliable at real scale, query interface noisy, staleness/MCP paths unverified) that is disproportionate to a benefit that's mostly a ranked view of information already reachable by reading the code.

## 5. Go/no-go recommendation

**Do not adopt Graphify as a third knowledge layer, for either repo tested.**

- **`ironclaude`: WEAK YES, as an occasional, manually-invoked tool — not a layer.** The god-node ranking and import-cycle detection are real, modest value, low install cost, and no evidence of unwanted network activity in the tested mode. Worth running occasionally (e.g. before a significant refactor, or periodically folded into a wiki page) as a convenience. Not worth wiring into the gate requirement or memory-first protocol — the friction (two commands, unreliable HTML at real scale, noisy query interface, unlabeled communities without an LLM key) is disproportionate to a benefit that's mostly a ranked view of information already reachable by reading the code.
- **`roleplaying-agents`: NO.** GDScript — the repo's actual primary language (876 files) and the reason it was named as an evaluation target — produced zero graph coverage. What Graphify does produce (a 138,049-node graph dominated by vendored C++ and generic identifiers) is lower quality than the `ironclaude` result and doesn't address the architectural-understanding gap the tool was being evaluated to fill.

**Revisit condition:** for `roleplaying-agents`, revisit if a future Graphify release adds GDScript support — the concrete signal to watch for is `tree-sitter-gdscript` appearing as a detected/installable dependency. For `ironclaude`, Graphify could be worth running before a major refactor for the god-node ranking; not worth automating or gating on.

## Relationship to prior evaluations

A prior evaluation (`wiki/agent-memory-techniques-evaluation.md`, d1266) rejected "graph-based memory" as requiring "infrastructure not present in any system." That rejection does **not** directly transfer to Graphify: d1266 was evaluating graph-based memory as an *agent-memory technique* — a live graph database as a retrieval substrate for conversational/behavioral memory. Graphify is a different category: a static codebase-structure analyzer producing a point-in-time snapshot, not a memory substrate for agent behavior. This evaluation also found the specific infrastructure objection empirically weaker for Graphify than d1266 assumed for the class generally — install took ~1.3 seconds with no server or database required for the `--code-only` path used throughout. d1266's underlying caution about practical cost still applies, just via a narrower, evidence-based set of costs (two-command workflow, HTML failure at scale, conditional local-first behavior) rather than the "infrastructure not present in any system" objection.

Separately: the wiki's `ironclaude-knowledge-stack.md` page carries context from before d1343 ran — two earlier directive submissions on this same question (d1341, d1342) were rejected for unclear reasons, and that page notes "do not propose or spawn Graphify integration work without a new explicit directive." d1343 **is** that new explicit directive, and this document is its outcome — the earlier rejections should be read as pre-evaluation history, not as standing guidance that contradicts the recommendation above.
