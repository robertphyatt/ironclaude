# Graphify Evaluation (d1343)

> **Date:** 2026-07-13
> **Method:** Hands-on testing — installed Graphify 0.9.14 and ran it against both `ironclaude` and `roleplaying-agents`, evidence sourced from actual tool output, not README claims. Full findings: `docs/plans/2026-07-13-graphify-findings-install.md`, `docs/plans/2026-07-13-graphify-findings-ironclaude.md`, `docs/plans/2026-07-13-graphify-findings-roleplaying-agents.md`.

## 1. Layer comparison

| Layer | Answers well | Doesn't answer |
|---|---|---|
| **Episodic memory** | "What was decided/discussed about X historically," past incidents, operator preferences, behavioral history across sessions | Structural/connectivity questions about current code ("what's most connected," "where exactly does X live") |
| **Wiki** | "What is the current known pattern/preference for X" — synthesized, human-curated, high-signal narrative and decision rationale | Exhaustive file:line-level structural facts; requires someone to have already written the page; doesn't self-update as code changes |
| **Graphify** | Structural/connectivity facts with concrete file:line precision — most-connected components ("god nodes"), community/cluster groupings, import-cycle detection, cross-file call/inheritance edges tagged EXTRACTED vs. INFERRED with confidence | Narrative "why" behind decisions; behavioral history; requires periodic re-running to stay current; usefulness is language-dependent (verified below) |

Concrete example of the gap Graphify fills: this session already had conceptual knowledge — from episodic memory and prior context — that ironclaude's architecture includes a daemon, a worker registry, a Brain client, and Ollama-based grading. It did **not** have file:line locations for any of these. Running Graphify against `ironclaude` and querying it directly produced exact locations (`WorkerRegistry` → `commander/src/ironclaude/worker_registry.py:13`, `IroncladeDaemon` → `commander/src/ironclaude/main.py:640`, `LocalGrader` → `commander/src/ironclaude/grader.py:28`, etc.) that matched the known architecture and added precision neither of the other two layers currently store.

## 2. Gap analysis

**Queries Graphify answers that the current two layers answer poorly or not at all** (verified against `ironclaude`'s actual output, not hypothetical):
- "What are the most-connected/core components of this codebase?" — answered directly and accurately by the God Nodes section of `GRAPH_REPORT.md` (verified: top 10 matched known architecture, with new file:line precision).
- "Does this codebase have import cycles?" — answered directly (none detected for either repo tested); this is not information the wiki or episodic memory track at all today.
- "What files/functions call into X?" — partially answered via `graphify query`, though the interface itself is noisy (see Integration Cost).

**This gap is only filled for the languages Graphify actually supports.** For `roleplaying-agents`, the equivalent and arguably more important question — "what's the architecture of the actual game logic?" — is **not** answered, because the game logic is written in GDScript and Graphify produced zero GDScript nodes (confirmed empirically; see the roleplaying-agents findings file). The gap Graphify fills is real but repo-dependent, not universal.

### Reconciliation with d1266

The prior evaluation (`wiki/agent-memory-techniques-evaluation.md`, d1266) rejected "graph-based memory" with this exact wording: *"Most techniques were REJECT — either already implemented (episodic memory, RAG retrieval) or not applicable to the architectures (graph-based memory requires infrastructure not present in any system)."*

**This rejection does not directly transfer to Graphify, for two reasons, one confirmed by this evaluation and one by category:**

1. **Different problem category.** d1266 evaluated "graph-based memory" as an *agent-memory technique* — using a graph as a retrieval substrate for conversational/behavioral memory (the kind of architecture that needs a live graph database and an update pipeline tied to every interaction). Graphify is not that: it's a static codebase-structure analyzer that produces a point-in-time snapshot of code structure, not a memory substrate for agent behavior or conversation history. The rejection was scoped to a different kind of system.
2. **The specific infrastructure objection is empirically weaker for Graphify than d1266 assumed for the class generally.** This evaluation measured Graphify's actual infrastructure cost: install via `uv tool install graphifyy` took ~1.3 seconds, requires only Python 3.10+, and needs no server or database for the `--code-only` (AST-only) extraction path that was used throughout this evaluation. That is a low bar, not the kind of "infrastructure not present in any system" d1266 was pointing at.

**However, d1266's underlying caution about practical cost does still apply, just differently than expected.** The real costs this evaluation surfaced are: a two-command workflow to get the full artifact set (see below), an HTML artifact that silently fails to generate above 5,000 nodes (both real repos tested exceeded this), and a "local-first" claim that is conditional on specific flags rather than the tool's default behavior. These are genuine — just not the "requires infrastructure not present in any system" objection d1266 raised. d1266's specific rejection reasoning does not apply to Graphify; a narrower, evidence-based set of costs does.

## 3. Overlap assessment

No overlap was found between Graphify's output and existing wiki content during the cross-check against `ironclaude`'s architecture. The wiki pages (e.g., `project_singleton_enforcement.md`, `project_hook_deploy_architecture.md`) are synthesized narrative — decisions, gotchas, and the reasoning behind them. Graphify's output is structural — connectivity counts and file:line facts, with no narrative content. The two are complementary: the wiki explains *why* a design choice was made; Graphify shows *what* is structurally connected to what, with more file-level precision than any existing wiki page attempts to track. Adopting Graphify would not duplicate or conflict with any existing wiki page's purpose.

## 4. Integration cost

**Install:** Low. `uv tool install graphifyy` (PyPI package `graphifyy`, double-y), Python 3.10+ required, ~1.3 seconds measured, no errors. Installs two binaries: `graphify` (CLI) and `graphify-mcp` (an MCP server), substantiating the README's Claude Code integration claim beyond a documentation-only claim — though this evaluation did not go on to test wiring `graphify-mcp` into a live Claude Code session, which remains unverified.

**Running:** Higher friction than the README quickstart implies. The command needed for non-interactive/CI use (`extract <path> --code-only`) is not the bare `graphify .` shown in the quickstart, and required reading `--help` output to discover. Getting the full claimed artifact set requires **two commands**, not one: `extract` produces only `graph.json`; a second `cluster-only` command is required to produce `GRAPH_REPORT.md` and attempt `graph.html`.

**"Local-first" is conditional, not default.** Without `--code-only`, `extract` defaults to an LLM-backed semantic pass requiring an API key (gemini/kimi/claude/openai/deepseek/ollama) — none of which are configured in this environment. `--code-only` was required throughout this evaluation to get genuinely local, no-API-key behavior. A best-effort check of tool output found no evidence of network activity under `--code-only`/`--no-label`, corroborating that the local-first claim holds specifically under those flags.

**HTML artifact reliability is poor at real-world scale.** Both repos tested exceeded Graphify's own default 5,000-node visualization limit (ironclaude: 5,018 nodes; roleplaying-agents: 138,049 nodes) and `graph.html` was skipped both times. For any repo of moderate size or larger, the interactive HTML graph — one of the three headline artifacts — will not be produced without an environment-variable override or restricting the input.

**Staleness / re-run cost:** The tool provides `graphify update <path>` specifically for incremental re-extraction ("no API cost" per its own help text), which is a reasonable story for keeping a graph current as a repo changes — but this evaluation did not test the incremental-update path itself, so its actual reliability is unverified.

**Data-quality risks observed at scale:** In `roleplaying-agents` (dominated by vendored `llama.cpp` C++ and other third-party code), ~15 cross-chunk ID collisions were logged where two files shared a generic name (e.g., `main.swift`, `Package.swift` in different directories) and one node was silently dropped rather than merged. One language extra (`tree-sitter-sql`) is not installed by default and must be added separately.

**Language coverage gap (repo-specific):** GDScript — the primary language of `roleplaying-agents` (876 files) — produced zero graph nodes, directly contradicting the README's "36+ languages including GDScript" claim. See the Gap Analysis section and the roleplaying-agents findings file for the three independent confirmations of this.

## 5. Recommendation

**The question this evaluation was asked is specifically whether Graphify should become a third *knowledge layer* — co-equal with episodic memory and the wiki, queried as part of the memory-first protocol on every directive. The honest answer to that specific question is no, for both repos.** What survived hands-on testing is a narrower, still-useful thing: an occasional analysis tool for Python/TypeScript repos, not a layer.

**What's genuinely new vs. already available, for `ironclaude`:** Before recommending adoption of anything, it's worth being precise about what `--code-only` mode (the only mode tested, since no LLM API key is configured) actually delivers versus what a Grep/Read pass already gives the Brain today:
- **Already achievable without Graphify:** the headline example in this doc — file:line locations for known components — is also directly obtainable with `Grep` in seconds. That specific capability doesn't justify adopting a new tool on its own.
- **Genuinely new:** the God Nodes centrality ranking (which components are *most* connected, ranked, without having to read and mentally aggregate the whole codebase) and import-cycle detection. These are real, but modest — a convenience/ranking capability, not a new class of question the Brain couldn't previously answer by reading code.
- **Not delivered in the tested (local-first) mode:** the interactive HTML graph failed on *both* repos tested (5,018 and 138,049 nodes both exceed the 5,000-node default limit) — this isn't an edge case, it failed on every repo this evaluation touched. Community *labels* are unlabeled `"Community N"` placeholders without an LLM API key, which this environment doesn't have. The `query` interface was noisy enough in testing that the static report, not the query command, had to be used to get a clean answer.

Weighed against that thin residual value, the two-command workflow and the maintenance cost of periodic re-runs, this evaluation's recommendation is:

- **`ironclaude`: WEAK YES, as an occasional tool — not a layer.** The god-node ranking and import-cycle detection are real, modest value, low install cost (~1.3s), and no evidence of unwanted network activity in the tested mode. Worth running occasionally (e.g., before a significant refactor, or periodically folded into wiki pages) as a convenience. Not worth wiring into the gate requirement or memory-first protocol — the friction (two commands, HTML unreliable at any real scale, noisy query interface, unlabeled communities without an LLM key) is disproportionate to a benefit that's mostly a ranked view of information already reachable by reading the code.

- **`roleplaying-agents`: NO.** GDScript — the repo's actual primary language (876 files) and the reason this repo was named as a target in the design doc — produced zero graph coverage. What Graphify does produce (a 138,049-node graph dominated by vendored C++ and generic identifiers) is lower-quality than the ironclaude result and does not address the architectural-understanding gap the tool was being evaluated to fill here.

**Overall:** Do not adopt Graphify as a third knowledge layer. At most, treat it as an occasional, manually-invoked analysis tool for `ironclaude` and other primarily Python/TypeScript repos — not for GDScript-based repos like `roleplaying-agents`. Revisit the roleplaying-agents recommendation if a future Graphify release adds GDScript support; the missing `tree-sitter-gdscript` dependency observed in this evaluation is the concrete signal to watch for.
