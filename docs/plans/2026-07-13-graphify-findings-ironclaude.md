# Graphify findings: ironclaude repo

## Actual output artifacts produced (vs. Task 1's claimed set)

Task 1 recorded the README's claim: `graph.html`, `GRAPH_REPORT.md`, `graph.json` are produced together. **This is not accurate in a single step.**

- `graphify extract <path> --out <dir> --code-only` alone produced only `graph.json` (6.0MB) and `.graphify_analysis.json` (582KB) — no report, no HTML.
- Getting `GRAPH_REPORT.md` required a **second command**: `graphify cluster-only <dir>`.
- Even then, **`graph.html` was not produced** — the tool explicitly skipped it: `"Skipped graph.html: Graph has 5018 nodes - too large for HTML viz (limit: 5000). Use --no-viz, raise GRAPHIFY_VIZ_NODE_LIMIT, or reduce input size."` Ironclaude (173 code files) exceeds Graphify's own default HTML-visualization node limit. For a real-world, moderately-sized repo, one of the three headline artifacts silently does not materialize on the first successful run — it requires an env var override or a smaller input, neither of which the README quickstart mentions.
- Community naming: since no LLM API key is configured (see Task 1), `cluster-only --no-label` was used to stay local-first. This produces `GRAPH_REPORT.md` with placeholder names (`"Community 0"`, `"Community 1"`, ...) rather than semantic labels — the "Community Hubs (Navigation)" section is a numbered list with zero descriptive value without an LLM pass.

Additional files produced beyond the claimed three: `manifest.json` (per-file AST/semantic hashes, for incremental re-extraction), `cache/` directory, `.graphify_labels.json` + `.sig` (present even under `--no-label`, likely a stub/signature file).

## Network activity

Best-effort textual check of tool output: no log line from `extract --code-only` or `cluster-only --no-label` mentioned any network call, download, or API request. Both commands ran fully offline as far as observable output indicates — consistent with the "local-first" claim **specifically when `--code-only`/`--no-label` are used**. This corroborates Task 1's finding that local-first is conditional on these flags, not default behavior.

## Cluster / god-node output

From `GRAPH_REPORT.md` (113KB; extracted only the non-repetitive summary sections per directive #9, not the full per-community listing which is just 273 lines of unlabeled `"Community N"` headers):

- **Scale:** 5018 nodes, 8075 edges, 381 communities (273 shown, 108 "thin" omitted).
- **Extraction confidence:** 76% EXTRACTED (direct AST facts) vs. 24% INFERRED (heuristic, avg confidence 0.64), 0% AMBIGUOUS. This matches the README's EXTRACTED/INFERRED tagging claim — verified, not just claimed.
- **God Nodes (top 10 by edge count):** `BrainClient` (281), `OrchestratorTools` (272), `IroncladeDaemon` (239), `SlackBot` (178), `WorkerRegistry` (177), `OllamaError` (104), `OllamaInventory` (99), `MachineConfig` (94), `TmuxManager` (79), `_mock_grader_approve()` (60, a **test-only mock function** — see accuracy note below).
- **Import cycles:** none detected.
- **"Surprising Connections":** all 5 listed instances are the same edge (`db_conn() --calls--> init_db()`, INFERRED) repeated across 5 near-identical test files. Not actually surprising — an example of low signal-to-noise in this specific heuristic section for this repo.
- **Node language breakdown** (via direct `graph.json` query, not the report): 4424 `.py` nodes, 221 `.ts`, 146 `.sh`, 141 `.json`, 46 `.js`, 32 no-extension, 4 `.mjs`, 3 `.ps1`, 1 `.toml`. All 5018 nodes are `_origin: "ast"` (confirms `--code-only` produced zero LLM-inferred nodes, as claimed).

## Accuracy assessment vs. known ironclaude architecture

Cross-checked against this session's own knowledge of the Brain/orchestrator architecture (worker lifecycle, daemon, grading, hooks — established via memory search and prior context in this session):

- **Strong matches, with genuinely new precision:** `BrainClient` → `commander/src/ironclaude/brain_client.py:120`, `IroncladeDaemon` → `commander/src/ironclaude/main.py:640`, `WorkerRegistry` → `commander/src/ironclaude/worker_registry.py:13`, `OrchestratorTools` → `commander/src/ironclaude/orchestrator_mcp.py:407`, `TmuxManager` → `commander/src/ironclaude/tmux_manager.py:63`, `LocalGrader` → `commander/src/ironclaude/grader.py:28`. These match what was already known conceptually (daemon singleton, worker registry, tmux-based worker sessions, Ollama-based local grading per prior memory search results) — **but this session did not previously have file:line locations for any of these**, only conceptual/behavioral descriptions from memory pages. This is a concrete example of a query the existing two layers answer poorly: episodic memory has behavioral history, the wiki has synthesized patterns, but neither pins exact file:line locations of core abstractions.
- **New information, not previously known to this session:** `SlackBot` (`commander/src/ironclaude/slack_interface.py:54`) and `SlackSocketHandler`/`PluginRegistry` ranking as highly-connected components — this session's prior memory/wiki context did not surface a Slack integration as architecturally central. Whether this is accurate or an artifact of the extraction can't be fully confirmed without deeper manual reading, but it's a genuinely new lead the graph surfaced that the other two layers hadn't.
- **Likely extraction artifact (accuracy concern):** `_mock_grader_approve()` — a test-fixture mock function — ranks #10 among all 5018 nodes by edge count, ahead of arguably more architecturally central things. The extraction step logged `"Deduplicated 17 node(s) (7 exact, 10 fuzzy)"`; a plausible explanation is that fuzzy deduplication collapsed multiple similarly-named mock definitions across test files into a single node, inflating its apparent connectivity. This is a real signal-quality caveat: god-node rankings can be skewed by test-fixture naming patterns, not just genuine architectural centrality.
- **Notable absence:** none of `state-manager` MCP tools, hook scripts, episodic-memory, or wiki subsystems (all real, substantial parts of this system per this session's own tool list and memory) appear in the top-10 god nodes. This likely means these are implemented as many smaller, more distributed functions rather than concentrated in single classes — a legitimate finding about the codebase's actual structure, not a gap in the tool.

## Example query test

Ran the exact example query from the design doc: `graphify query "what's most central to the worker lifecycle" --graph <path>/graph.json`.

Result: a BFS traversal (depth 2) from 4 matched start nodes returned 1062 matching nodes, truncated at ~2000 tokens (only the first ~60 shown). The output was **not ranked by centrality** — it was dominated by test class names (`TestSpawnWorker`, `TestDaemon`, `TestOrchestratorMcp`, dozens more) rather than leading with the actual most-central abstractions. The file:line references returned (e.g., `WorkerRegistry [src=commander/src/ironclaude/worker_registry.py loc=L13]`) were accurate, but a user asking this exact question would need to already know to add `--context` filters or increase `--budget` to get a useful, non-noisy answer.

**By contrast, the static `GRAPH_REPORT.md`'s "God Nodes" section directly and cleanly answers "what's central" for the whole repo** (ranked list, no noise) — it's a better tool for this exact question than the interactive `query` command. This is a real usability nuance for the Integration Cost section: the report (batch-generated) and the query command (interactive) have different strengths, and the design's example question is better served by the report than by `query`.
