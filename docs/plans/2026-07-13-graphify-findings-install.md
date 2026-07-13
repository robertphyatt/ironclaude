STATUS: OK

## Install

Command: `uv tool install graphifyy` (PyPI package name is `graphifyy`, double-y; CLI binary is `graphify`).
Time: ~1.3s wall clock (resolved 30 packages, downloaded ~7.3MiB total: numpy, graphifyy, rapidfuzz, plus 27 tree-sitter grammar packages for 27+ languages including tree-sitter-python and tree-sitter (core), no dedicated GDScript grammar package observed in the dependency list).
Errors: none.
Verified version: `graphify 0.9.14` (via `graphify --version`).

Two executables installed: `graphify` (CLI) and `graphify-mcp` (an MCP server binary) — this directly substantiates the README's "integrates with Claude Code as a project knowledge source" claim; it is not just a manifest/skill-file claim, there is a real MCP server binary shipped.

## Output-flag syntax and claimed output artifacts (corrected from initial README fetch)

The initial WebFetch of the README summary was **materially wrong on two points**, confirmed against the actual `graphify --help` output:

1. **README claimed no `--output` flag exists** ("writes to a fixed directory: `graphify-out/`"). This is TRUE only for the simple `graphify .` quickstart flow. The actual CLI has a distinct `extract <path>` subcommand ("headless full extraction (AST + semantic LLM) for CI/scripts") which **does** accept `--out DIR` (default: `<path>/graphify-out/`). `extract` is the correct subcommand for this non-interactive evaluation, not the bare `.` quickstart form.
2. **README implied the tool is always local-first.** In reality, `extract` defaults to a semantic LLM pass via `--backend` (gemini|kimi|claude|openai|deepseek|ollama), auto-detected from whichever API key is set. **No API key is set in this environment** (checked: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `KIMI_API_KEY` all absent). Without `--code-only`, a default `extract` run would either fail (no backend available) or silently reach out to whichever API key happens to be configured on a given machine. **`--code-only`** ("index code (local AST, no API key) and skip doc/paper/image files") is required to get the tree-sitter-only, genuinely local-first behavior the README markets as the default. This is real Integration Cost evidence: the "local-first" claim is conditional on a specific flag, not the tool's default behavior.

Claimed output artifacts (from README, to be verified against actual runs in Tasks 2/3): `graph.html` (interactive browser graph), `GRAPH_REPORT.md` (markdown highlights/report), `graph.json` (full graph, queryable). Additional optional exports exist (`graph.svg`, `.graphml`, `cypher.txt`, Obsidian vault) but are not produced by default.

Command to be used in Tasks 2/3 (differs from the plan's original guess of `graphify analyze <path> --output <dir>` — corrected to the real syntax):
```
graphify extract <repo-path> --out <scratchpad-dir> --code-only
```

## Network activity

Best-effort textual check only (no packet-capture tooling available in this environment): the install itself (`uv tool install`) showed only PyPI package resolution/download log lines — expected package-manager network activity, not evidence of the *tool itself* phoning home. No log line from `graphify --version` or `graphify --help` mentioned any network call. The `--code-only` flag is specifically chosen for Tasks 2/3 to avoid any LLM API network activity; if `--code-only` were omitted, the tool would necessarily need network access to an LLM API (contradicting "local-first" for that mode). This distinction — local-first only under `--code-only`, network-dependent otherwise — is itself the most direct network-activity finding for the Integration Cost section.
