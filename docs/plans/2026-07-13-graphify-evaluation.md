# Graphify Evaluation (d1343) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Produce an evidence-based recommendation document at `docs/research/2026-07-13-graphify-evaluation.md` on whether to adopt Graphify as a third knowledge layer, based on hands-on testing against both `ironclaude` and `roleplaying-agents`.

**Architecture:** Sequential pipeline — install Graphify, run it against both repos with output directed to scratchpad (never into the repos themselves), inspect the actual output as evidence, write the 5-section analysis doc, then adversarially review the recommendation before staging. A persistent install failure is not a dead end: it propagates through the task graph as a valid "NOT TESTED" finding and becomes direct evidence for a "don't adopt, integration cost too high" recommendation.

**Tech Stack:** Graphify CLI (tree-sitter based, exact install method TBD from its README), Bash, scratchpad filesystem.

**No TDD:** This plan produces no executable code — Graphify is a third-party tool being evaluated, and the deliverable is an analysis document. Per the TDD rule's own exception, tasks below document "No tests required" with reasoning instead of a red/green cycle.

**Revision history:**
- Round 1 fixed a Critical concurrent-write race (Tasks 2/3 sharing one findings file), a hardcoded session-specific scratchpad path, a missing wiki-cross-reference step, and several minor gaps.
- Round 2 fixed a Critical structural gap: on install failure, the original task graph had no path to actually producing the deliverable (Tasks 2/3 required `exit code 0` from a tool that was never installed, so Task 4 could never run). Fixed by making install failure a propagated "NOT TESTED" finding rather than a dead end, adding binary-name substitution caveats to the JSON `command` fields (not just the markdown prose), and switching the wiki-page reference to an absolute path (the Read tool doesn't expand `~`).
- Round 3 fixed a real bug: the `$SCRATCHPAD_DIR` variable set in a "resolve" step would not survive into the next Bash step's `command` (shell state doesn't persist between Bash invocations, only the working directory does) — merged resolve+run into a single step with direct literal-path substitution instead. Also fixed a garbled/JSON-vs-markdown-inconsistent recommendation string ("no, adopt" → "no, don't adopt"), sequenced Task 3 after Task 2 to match the design's explicit "not parallelized" pipeline (was incorrectly same-wave), added a fallback for "tool cannot be located at all" (not just "install command fails"), and added an HTML-graph existence check the design called out as an output artifact but no step inspected. Also caught and fixed a self-referential `depends_on` cycle introduced by that same round's edit (Task 2 briefly depended on itself instead of Task 1).
- Round 4 fixed three real Important gaps: Task 1 now captures the output-flag syntax and claimed output artifact types that Tasks 2/3 reference but Task 1 never gathered; Tasks 2/3 no longer hard-gate success on Graphify producing exactly "HTML + markdown + JSON" (that was an unverified README claim embedded as a pass/fail condition, contradicting the design's own "don't trust README claims" rule) — actual output is now recorded as an observation instead; and the "network activity observed" check is now explicitly best-effort (a textual check of the tool's own output/logs, since no packet-capture tooling is available), rather than an unspecified mechanical requirement. Also replaced the fragile substring-matched `INSTALL FAILED` sentinel with a structured first-line marker (`STATUS: OK` / `STATUS: INSTALL_FAILED`) to eliminate false-positive risk from success-path prose containing those words.

---

## Task 1: Install Graphify and verify it runs

**Files:**
- Create: `docs/plans/2026-07-13-graphify-findings-install.md`

**No tests required:** Tooling installation, not executable code — there is nothing to unit test. Verification is a live `--version`/`--help` invocation, captured as Step 3 below.

**Step 1: Fetch install instructions**

Use WebFetch on `https://github.com/Graphify-Labs/graphify` (or its README raw URL if that resolves better) to determine:
- The exact install command (npm/pip/cargo/go install/binary download)
- Any prerequisite runtime (Node, Python, Rust, Go) and minimum version
- The CLI's actual command name and core subcommand for "analyze a directory"
- The exact output-directory flag syntax (e.g. `--output`, `-o`, `--out-dir`)
- What output artifact(s) the README documents producing — record this as a **claim to verify**, not an assumption. Do not assume "HTML graph + markdown report + JSON" specifically; Tasks 2 and 3 will record what actually gets produced, and any mismatch with this claim is itself Integration Cost evidence.

If this URL 404s or otherwise doesn't resolve, use WebSearch for "Graphify Labs code knowledge graph tree-sitter github" to locate the correct repository before proceeding — do not silently abandon the task on a bad URL. **If the tool/repository cannot be located at all after the WebSearch attempt**, treat this identically to an install failure: skip directly to Step 4 and record `STATUS: INSTALL_FAILED` there.

Expected: install command, CLI invocation syntax, output-flag syntax, and claimed output artifact types identified from the README text — or a determination that the tool cannot be located at all.

**Step 2: Run the install command**

Run the exact command identified in Step 1 (e.g., `npm install -g graphify-cli` or `pip install graphify` — substitute the real command found).

Expected: install completes with no error. If it fails, do one troubleshooting pass (check prerequisite runtime version, retry). **If it still fails after that, this is a valid completion state for this step** — proceed to Step 4 and record the failure as the finding. Do not treat this as a blocking task failure; the design requires a persistent install failure to become deliverable evidence, not a dead end.

**Step 3: Verify the CLI is runnable**

Run:
```bash
graphify --version
```
(substitute actual binary name from Step 1 if different)

Skip this step entirely if Step 2 recorded a persistent install failure — go directly to Step 4.

Expected: a version string printed, not a "command not found" error (or this step is skipped per the failure branch).

**Step 4: Record install evidence**

Create `docs/plans/2026-07-13-graphify-findings-install.md`. **The first line must be exactly** `STATUS: OK` (install succeeded and verified) or `STATUS: INSTALL_FAILED` (install failed persistently, or the tool couldn't be located at all). This exact first-line marker — not a substring match — is what Tasks 2 and 3 check to short-circuit their own live-testing steps; a structured marker avoids false positives from success-path prose that happens to contain the words "install failed."

Below that first line, record:
- If `STATUS: OK`: exact commands run, time taken, the verified version string, the output-flag syntax and claimed output artifact types from Step 1, and a best-effort note on whether the tool's own output/logs mentioned any network call (HTTP request, DNS lookup, "downloading", "fetching", telemetry) despite Graphify's "local-first" claim. This is a textual check of the tool's own output, not a definitive network-level test — no packet-capture tooling is available in this environment. If nothing is mentioned, record "no network activity mentioned in tool output" rather than asserting none occurred.
- If `STATUS: INSTALL_FAILED`: the error details.

Expected: `docs/plans/2026-07-13-graphify-findings-install.md` exists, first line is exactly `STATUS: OK` or `STATUS: INSTALL_FAILED`, and the body contains the evidence described above.

---

## Task 2: Run Graphify against ironclaude and extract findings

**Depends on:** Task 1

**Files:**
- Create: `docs/plans/2026-07-13-graphify-findings-ironclaude.md`
- Graphify's own output goes to this session's scratchpad directory (not a repo file)

**No tests required:** This task runs a third-party CLI and reads its output — there is no code under test.

**Step 1: Check install status**

Read the first line of `docs/plans/2026-07-13-graphify-findings-install.md`. If it is exactly `STATUS: INSTALL_FAILED`, skip directly to Step 4: create `docs/plans/2026-07-13-graphify-findings-ironclaude.md` containing exactly `NOT TESTED -- Graphify install failed, see docs/plans/2026-07-13-graphify-findings-install.md`, and stop this task here. Otherwise continue to Step 2.

**Step 2: Run Graphify against the ironclaude repo**

Identify this session's scratchpad directory from the "Scratchpad Directory" section of the *current* session's system prompt, and run Graphify with `--output` pointed at that literal absolute path + `/graphify-ironclaude`, substituted **directly into the command text** — not via a shell environment variable, since shell state does not persist between separate Bash invocations (only the working directory does). Never reuse a scratchpad path from a previous session's transcript, plan document, or memory. Substitute the actual binary name/analyze subcommand/output flag discovered in Task 1 Step 1 if different:
```bash
graphify analyze /Users/roberthyatt/Code/ironclaude --output <this-session's-literal-scratchpad-path>/graphify-ironclaude
```

Expected: exit code 0. Record what output file(s)/artifact(s) were actually produced in the output directory as an observation — this is **not** a pass/fail gate on matching HTML+markdown+JSON specifically, since that's an unverified README claim (per the design's "don't trust README claims" rule). If the actual output differs from what Task 1 recorded as claimed, that discrepancy is itself Integration Cost evidence.

**Step 3: Inspect the output and cross-check**

Inspect whatever output was actually produced in the previous step. If a markdown report exists, extract its "clusters" and "god nodes" sections (per directive #9, don't read the whole file if large). If any graph/visualization file exists, confirm its existence and note its basic size — full interactive inspection isn't required, but it must not be silently skipped as an artifact. Compare the reported clusters/god-nodes against what's already known about ironclaude's architecture (state-manager MCP, hooks, worker lifecycle, episodic memory, wiki) from this session's own context and the wiki pages already read. Note any places the graph is accurate, wrong, or missing something obvious.

Expected: identifiable list of architectural clusters and highest-connectivity nodes if a report exists, existence of any graph/visualization artifact confirmed, plus an accuracy assessment vs. known architecture.

**Step 4: Record findings**

Create `docs/plans/2026-07-13-graphify-findings-ironclaude.md` and record: cluster list, god-node list, accuracy assessment vs. known architecture, any specific example query (e.g., "what's most central to the worker lifecycle") that the graph output answers correctly or incorrectly, the actual output artifacts produced (vs. what Task 1 recorded as claimed), and a best-effort note on any network activity mentioned in the tool's own output during this run. (If Step 1 short-circuited here, this file instead contains the `NOT TESTED` line per Step 1.)

Expected: `docs/plans/2026-07-13-graphify-findings-ironclaude.md` exists with either full findings or the `NOT TESTED` line.

---

## Task 3: Run Graphify against roleplaying-agents and verify GDScript support

**Depends on:** Task 2 (sequenced after, not parallel — matches the design's "single ordered pipeline, not parallelized," and avoids two concurrent Graphify processes potentially colliding on a shared cache/config dir)

**Files:**
- Create: `docs/plans/2026-07-13-graphify-findings-roleplaying-agents.md`
- Graphify's own output goes to this session's scratchpad directory (not a repo file)

**No tests required:** Same as Task 2 — running and reading a third-party tool's output, no code under test.

**Step 1: Check install status**

Read the first line of `docs/plans/2026-07-13-graphify-findings-install.md`. If it is exactly `STATUS: INSTALL_FAILED`, skip directly to Step 4: create `docs/plans/2026-07-13-graphify-findings-roleplaying-agents.md` containing exactly `NOT TESTED -- Graphify install failed, see docs/plans/2026-07-13-graphify-findings-install.md`, and stop this task here. Otherwise continue to Step 2.

**Step 2: Run Graphify against the roleplaying-agents repo**

Identify this session's scratchpad directory from the "Scratchpad Directory" section of the *current* session's system prompt, and run Graphify with `--output` pointed at that literal absolute path + `/graphify-roleplaying-agents`, substituted **directly into the command text** — not via a shell environment variable, since shell state does not persist between separate Bash invocations. Never reuse a scratchpad path from a previous session's transcript, plan document, or memory. Substitute the actual binary name/analyze subcommand/output flag discovered in Task 1 Step 1 if different:
```bash
graphify analyze /Users/roberthyatt/Code/roleplaying-agents --output <this-session's-literal-scratchpad-path>/graphify-roleplaying-agents
```

Expected: exit code 0. Record what output file(s)/artifact(s) were actually produced as an observation — not a pass/fail gate on matching a specific claimed set.

**Step 3: Verify GDScript was actually parsed, and inspect clusters/god-nodes**

Check whatever report/JSON output exists for evidence that `.gd` files were included as parsed nodes — not silently skipped. Per directive #9, grep for `.gd` file references or a language breakdown rather than reading the whole file. If the tool has verbose/debug output, check it for "unsupported extension" or parse-error messages related to GDScript. Also extract the clusters and god-nodes sections if a report exists (same as Task 2 Step 3), and confirm any graph/visualization output file exists.

Expected: either (a) confirmed GDScript nodes present with real call/inheritance edges, or (b) confirmed absence/failure — both are valid findings, but must be determined from the actual output, not assumed. Plus an identifiable cluster/god-node list and any graph/visualization artifact's existence confirmed.

**Step 4: Record findings**

Create `docs/plans/2026-07-13-graphify-findings-roleplaying-agents.md` and record: whether GDScript parsing actually worked, cluster/god-node list for this repo, how it compares in quality/accuracy to the ironclaude result from Task 2, the actual output artifacts produced (vs. what Task 1 recorded as claimed), and a best-effort note on any network activity mentioned in the tool's own output during this run. (If Step 1 short-circuited here, this file instead contains the `NOT TESTED` line per Step 1.) This directly determines whether the final recommendation needs a per-repo split.

Expected: `docs/plans/2026-07-13-graphify-findings-roleplaying-agents.md` exists with either full findings or the `NOT TESTED` line.

---

## Task 4: Write the analysis document

**Depends on:** Task 3 (transitively covers Tasks 1 and 2, since 3 depends on 2 depends on 1)

**Files:**
- Create: `docs/research/2026-07-13-graphify-evaluation.md`

**No tests required:** This is the research deliverable itself — a markdown document, not executable code.

**Step 1: Read the findings files**

Read `docs/plans/2026-07-13-graphify-findings-install.md`, `docs/plans/2026-07-13-graphify-findings-ironclaude.md`, and `docs/plans/2026-07-13-graphify-findings-roleplaying-agents.md` in full. Note the `STATUS:` first line of the install findings file and whether either per-repo findings file is the `NOT TESTED` short-circuit.

Expected: all Task 1-3 findings loaded as evidence for the draft, including whether either or both repos were `NOT TESTED` due to install failure.

**Step 2: Read the wiki page for format/rigor cross-reference**

Read `/Users/roberthyatt/.ironclaude/brain/wiki/agent-memory-techniques-evaluation.md` (the d1266 evaluation summary, confirmed at this path during brainstorming — use the absolute path, not a tilde-prefixed one, since the Read tool does not expand `~`). Confirm the exact wording of the "graph-based memory requires infrastructure not present in any system" rejection, and note the prior document's format/rigor for consistency with this one.

Expected: d1266's rejection rationale confirmed verbatim (not from memory/recollection), and format/rigor noted.

**Step 3: Draft the document**

Write `docs/research/2026-07-13-graphify-evaluation.md` with these 5 sections, each sourced from the recorded findings in Tasks 1-3 and the wiki page read above (not from Graphify's README claims):

1. **Layer comparison** — table: episodic memory / wiki / Graphify × {what it answers, what it can't}, using the Task 2/3 findings as the Graphify column's evidence.
2. **Gap analysis** — specific architectural/structural queries the Brain currently answers poorly, tested against the actual Task 2/3 output. Include a subsection explicitly reconciling d1266's rejection (quoting the wording confirmed in Step 2): state whether that rationale still applies to Graphify (a local CLI with no server/DB) or explain why it doesn't, using the measured install cost from Task 1 as evidence either way.
3. **Overlap assessment** — whether Graphify's cluster/god-node output (Task 2/3) duplicates anything already encoded in existing wiki pages.
4. **Integration cost** — the measured install/run effort from Task 1, plus the network-activity notes from Tasks 1-3 against the "local-first" claim, the staleness question (how often would this need re-running given repos change constantly), and what a Claude Code/MCP integration would require per the README's claim (verify or flag as unverified).
5. **Recommendation** — yes/no/conditional. If Task 3 found GDScript support meaningfully weaker than Task 2's Python/TS result, split the recommendation per-repo rather than giving one blanket answer.

**If Task 1's findings file has first line `STATUS: INSTALL_FAILED`:** sections 1-3 note that live testing could not be performed, and the Recommendation section is "no, don't adopt" based on the install failure itself as direct Integration Cost evidence — this is a legitimate, evidence-based conclusion, not an incomplete evaluation.

**Step 4: Self-check against the design doc's validation strategy**

Before moving to Task 5, re-read the draft and confirm every claim traces to a Task 1-3 finding, an install log line, or the wiki page read in Step 2 — not to unverified README text. Fix any sentence that doesn't.

---

## Task 5: Adversarial review, revise, and stage

**Depends on:** Task 4

**Files:**
- Modify: `docs/research/2026-07-13-graphify-evaluation.md`

**No tests required:** Review and staging of a document, not executable code.

**Step 1: Call advisor**

Call `advisor()` on the drafted recommendation, framed to specifically pressure-test the recommendation against the gathered evidence (not a style/grammar pass) — in particular whether the d1266 reconciliation is convincing and whether the per-repo split (if any) is justified by the Task 2/3 findings.

If `advisor()` is unavailable, dispatch a subagent via the `Agent` tool with `model=fable` if available, otherwise `model=opus`, given the same context and an adversarial-review-only prompt, per the Advisor Fallback rule in `.claude/rules/behavioral.md`.

**Step 2: Apply warranted revisions**

Edit `docs/research/2026-07-13-graphify-evaluation.md` to address any valid concerns raised. If a concern is raised but not valid (e.g., contradicted by directly observed tool output from Task 1-3), do not silently agree — push back with the recorded evidence, per directive #8 (No Sycophantic Responses), and only revise if the evidence actually supports the concern.

**Step 3: Stage the document**

Run:
```bash
git add -f docs/research/2026-07-13-graphify-evaluation.md
```
(force-add required: `docs/` is gitignored in this repo per established convention)

Expected: `git status --short docs/research/2026-07-13-graphify-evaluation.md` shows `A  docs/research/2026-07-13-graphify-evaluation.md`. Professional mode blocks commit — the Brain commits separately.
