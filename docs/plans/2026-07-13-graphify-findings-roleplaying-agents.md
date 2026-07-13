# Graphify findings: roleplaying-agents repo

## GDScript support verdict: NOT SUPPORTED (confirmed, not assumed)

Multiple independent lines of evidence, strongest first:

1. **Isolated single-file test**: copied one real `.gd` file (`minimal_connection_test.gd`) from the repo into an empty directory and ran `graphify extract` on it alone. Result: `graph is empty — extraction produced no nodes`, with an explicit log line: `1 file(s) not classified (no supported extension or shebang), skipped: minimal_connection_test.gd`. Exit code 1.
2. **Full-repo run**: the repo has **876 real `.gd` files** (confirmed via `find`), producing **138,049 total graph nodes** — but a direct query of `graph.json` for nodes with `source_file` ending in `.gd` returns **zero**. Not one GDScript symbol made it into the graph.
3. **Dependency evidence from Task 1's install**: the `uv tool install graphifyy` log listed tree-sitter grammar packages for ~27 languages (bash, c, c-sharp, cpp, elixir, fortran, go, groovy, java, javascript, json, julia, kotlin, lua, objc, php, powershell, python, ruby, rust, scala, swift, typescript, verilog, zig) — **no `tree-sitter-gdscript` package was installed**, consistent with (1) and (2).

**This directly contradicts the README's claim of "36+ languages including GDScript."** For a Godot game project — one of the two repos the design doc named as a target use case specifically because it's GDScript-heavy — Graphify 0.9.14 provides **zero** coverage of the actual game-logic layer.

## Actual output artifacts produced (vs. Task 1's claimed set)

Same two-step pattern as the ironclaude run: `extract --code-only` alone produced only `graph.json` (large) + `.graphify_analysis.json`; `GRAPH_REPORT.md` required a second `cluster-only --no-label` command. **`graph.html` was again skipped** — this time even more decisively: `"Skipped graph.html: Graph has 138049 nodes - too large for HTML viz (limit: 5000)."` At this repo's scale, the HTML artifact is not a marginal miss; the graph is ~27x the tool's own visualization limit.

Additional non-fatal issues surfaced during extraction that are relevant to Integration Cost:
- `1 .sql file(s) contributed nothing to the graph because a dependency is missing: tree_sitter_sql not installed. Install it with: pip install "graphifyy[sql]"` — confirms language coverage is modular/opt-in and the base install does not cover everything the README's language list implies.
- ~15 `WARNING: node '<name>' ... collides with node from '<other file>' — the second node will be dropped. This is a cross-chunk ID collision caused by two files with the same name in different directories.` This occurred repeatedly in the vendored `thirdparty/llama.cpp` and `aggregate_device_poc`/`hal_plugin` C++ subtrees, where multiple files share generic names (`main.swift`, `Package.swift`). Real symbols were silently dropped, not merged — a data-loss caveat for large repos with vendored dependencies or generic filenames, not just a cosmetic warning.
- `3625 source file(s) produced zero nodes` (out of 9859 "code" files initially found) — a large fraction of files that were classified as parseable code still contributed nothing.

## Cluster / god-node output — and why it's less useful than the ironclaude result

- **Scale:** 138,049 nodes, 311,635 edges, 3588 communities (3194 shown, 394 thin omitted). Extraction confidence: 88% EXTRACTED / 12% INFERRED (avg confidence 0.78) — a *higher* EXTRACTED ratio than ironclaude's 76%, but this reflects the corpus being dominated by vendored C/C++ (very AST-friendly), not higher-quality analysis of the actual project.
- **God Nodes (top 10 by edge count):** `string` (3359), `RenderingServer` (1067), `Vector2()` (865), `Vector3()` (747), `MIN()` (623), `get$3()` (461, a mangled/dedup-artifact-looking name), `ggml_backend_opencl_context` (428), `append()` (413), `DisplayServer` (393), `Vector2i()` (375).
- **This list is qualitatively worse than ironclaude's.** Ironclaude's top-10 were specific, meaningful project classes (`BrainClient`, `WorkerRegistry`, etc.). Here, the top 10 are almost entirely generic language/engine built-ins (`string`, `append()`, `MIN()`) or Godot **engine** classes referenced from the small C++ GDExtension addon layer (`RenderingServer`, `DisplayServer`, `Vector2()`/`Vector3()`/`Vector2i()` — these come from `godot-cpp` binding headers in `addons/ai_inference_extension/`, not from actual game logic) or vendored `llama.cpp` internals (`ggml_backend_opencl_context`). **None of the actual game/campaign logic — which lives in the 876 unparsed `.gd` files — appears anywhere in the top god nodes**, because it isn't in the graph at all.
- **Import cycles:** none detected (unsurprising given the game-logic layer is entirely absent from the graph).
- Full per-community listing not read in full (3588 communities, file would be very large) per directive #9 — the God Nodes and Summary sections above are representative and sufficient to establish the quality finding.

## Network activity

Best-effort textual check, consistent with Task 1/Task 2: no log line from either `extract --code-only` or `cluster-only --no-label` indicated network activity. The `tree_sitter_sql` missing-dependency message pointed to a local `pip install` remedy, not a network fetch attempt by graphify itself.

## Quality comparison to Task 2 (ironclaude)

| | ironclaude | roleplaying-agents |
|---|---|---|
| Nodes | 5,018 | 138,049 |
| God nodes | Specific, meaningful (`BrainClient`, `WorkerRegistry`, ...) | Generic/vendor noise (`string`, `append()`, `ggml_backend_opencl_context`) |
| Coverage of the repo's actual primary logic | Good (Python/TS orchestrator code, core abstractions surfaced accurately) | **None** — GDScript (876 files, the actual game logic) entirely unparsed |
| HTML graph produced | No (5018 > 5000 limit) | No (138,049 >> 5000 limit) |
| Data-loss warnings | None | ~15 cross-chunk ID collisions silently dropping nodes |

**Bottom line for the per-repo split**: Graphify is meaningfully useful for `ironclaude` (accurate, specific architectural signal) but provides **no coverage of roleplaying-agents' actual game logic** and produces a noisier, less useful graph even for the C++/JS/Python code it does parse, due to the sheer volume of vendored third-party code drowning out project-specific signal.
