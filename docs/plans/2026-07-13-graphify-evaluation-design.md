# Graphify Evaluation (d1343) Design

> **Created:** 2026-07-13
> **Status:** Design Complete

## Summary

Directive #1343 asks whether Graphify (a tree-sitter-based codebase knowledge-graph CLI) should be adopted as a third knowledge layer for the IronClaude Brain, alongside the existing episodic memory (conversation search) and wiki (synthesized pages) layers. This is a research task, not a feature build: the deliverable is a written recommendation document at `docs/research/2026-07-13-graphify-evaluation.md`, evaluated via hands-on testing rather than documentation review, because directive #2 (Verify with Evidence) requires claims to be tested, not taken from Graphify's README.

Notably, a related prior evaluation (d1266, `wiki/agent-memory-techniques-evaluation.md`) already rejected "graph-based memory" as a category, reasoning it "requires infrastructure not present in any system." Graphify is a local CLI with no server/DB, so this evaluation must explicitly reconcile whether that prior rejection still applies or was scoped to a different kind of tool. Silent inconsistency between the two documents is treated as a failure condition.

## Architecture (Methodology)

Single ordered pipeline (not parallelized), because each step gates whether the next is worth doing:

1. Install Graphify locally per its README — records actual setup cost as measured effort, not estimate.
2. Run against `/Users/roberthyatt/Code/ironclaude` (Python/TS), output directed to the scratchpad directory (not into the repo, to avoid polluting `git status` given known concurrent-worker-edit risk in this repo). Inspect HTML graph, markdown report, JSON against known repo architecture.
3. Run against `/Users/roberthyatt/Code/roleplaying-agents` (Godot/GDScript), same output-to-scratchpad approach. Specifically verify the claimed-but-unconfirmed GDScript parsing support.
4. Cross-reference `wiki/agent-memory-techniques-evaluation.md` for document format/rigor consistency with prior tool evaluations.
5. Write the analysis doc, sourcing every claim from an observed artifact (install log, graph output, existing wiki page) rather than Graphify's marketing copy.
6. Stage the doc with `git add` (per success criteria — Brain commits, not this workflow).

## Components (Document Structure)

`docs/research/2026-07-13-graphify-evaluation.md` — 5 sections, 1:1 with the directive's required sections:

1. **Layer comparison** — table: episodic memory / wiki / Graphify × {what it answers, what it can't}, grounded in observed Graphify output.
2. **Gap analysis** — specific architectural/structural queries the Brain currently answers poorly, tested directly against Graphify's actual output. Includes explicit d1266 reconciliation subsection.
3. **Overlap assessment** — whether Graphify's cluster/god-node output duplicates anything existing wiki pages already encode.
4. **Integration cost** — measured install/run effort, staleness question (re-run cadence as workers commit continuously), and what Claude-Code/MCP integration would require per the README's claim.
5. **Recommendation** — yes/no/conditional, split per-repo if `ironclaude` and `roleplaying-agents` results diverge (e.g., if GDScript support is weaker than Python/TS).

## Error Handling (Contingencies)

- **Install fails**: one reasonable troubleshooting pass, then treat persistent failure itself as a finding for section 4 (integration cost is prohibitively high) rather than a blocker to work around indefinitely.
- **GDScript parsing silently fails or produces empty/garbage output**: document as "claimed but not functional for GDScript," directly informing the per-repo recommendation split.
- **Tool contacts network despite "local-first" claim**: flag explicitly as a discrepancy against its own documentation, not silently ignored.
- **Output too large to read directly**: apply directive #9 (Handle Large Files with Decomposition) — grep/extract god-node and cluster summaries rather than declaring output unreadable.

## Testing Strategy (Validation)

- Every claim in the final doc must trace to an observed artifact (Graphify output, install log, existing wiki page) — nothing sourced from README claims alone.
- Advisor review before staging: call `advisor()` on the drafted recommendation specifically to pressure-test it against the gathered evidence. If advisor is unavailable, dispatch a top-tier subagent (Fable if available, else Opus) per the Advisor Fallback rule in this repo's `.claude/rules/behavioral.md`.
- The doc must explicitly address d1266's "graph-based memory" rejection — state whether it still applies to Graphify or explain why Graphify differs — rather than leaving the two evaluations silently inconsistent.

## Implementation Notes

- No wiki summary page in this pass (per user decision) — success criteria only require the docs/research file; the Brain writes any wiki summary during its own post-directive ingest.
- Repos under test: `/Users/roberthyatt/Code/ironclaude` and `/Users/roberthyatt/Code/roleplaying-agents`. Read-only with respect to both — Graphify's own output goes to scratchpad, not into either repo.
- This directive appears to be a re-submission of an earlier attempt (#1339) at the same research question; no output file existed yet at either plausible location when checked, so this is not duplicate work product, just a duplicate ask.
