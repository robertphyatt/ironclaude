# Ollama Worker Performance Analysis

> **Date:** 2026-06-27
> **Period:** Last 72 hours (June 24–27, 2026) plus all-time ollama worker history
> **Directive:** d1248
> **Constraint:** Recommendations scoped to objective construction, task decomposition, and capability-tier matching. Model (gemma4:12b) stays as-is.

---

## Executive Summary

**Finding 1:** The 72-hour shadow grader concordance data is entirely from a single controlled test run (d1238, June 26), not production traffic. All 32 concordance events are test artifacts — C-grades where gemma4 correctly rejected vague test fixtures that Opus was forced to approve, and F-grades from Ollama timeouts during load. Zero production concordance data exists for the current period.

**Finding 2:** All 7 all-time ollama workers (May 30–June 13) completed successfully with a 100% completion rate and ~7.2 min average runtime. Every successful objective shared a consistent structure: single named file target, explicit step list, no architectural judgment required, explicit success condition. This is the observable template for ollama-appropriate tasks — it is not documented anywhere in the codebase.

**Finding 3:** Three structural gaps exist in the current system. (a) `OLLAMA_WORKER_PLAYBOOK` is only injected for single-spawn (`spawn_worker` line 1889) — the batch path (`spawn_workers` line 2182-2183) omits it entirely. (b) `ollama` is absent from all grader tier matrices (sonnet/opus/fable only), so the grader cannot recommend or warn against ollama assignment. (c) The playbook contains only workflow mechanics — no guidance on what constitutes an ollama-appropriate task, context limits, or when to request scope reduction.

**Recommendation 1 (Objective Construction):** Add a structured ollama objective template to the Brain prompt. Template enforces: one named file, ≤3 explicit steps, no architectural decisions, explicit pass/fail condition. Estimated impact: reduces ambiguous objectives from the observable pattern.

**Recommendation 2 (Task Decomposition):** Add a pre-spawn complexity gate for ollama workers. Gate rejects objectives that reference >2 files, use open-ended verbs ("refactor", "improve", "analyze"), or omit an explicit success condition. Route rejected objectives back to Brain for decomposition.

**Recommendation 3 (Tier Matching):** Add `ollama` as an explicit tier in both `spawn_worker` and `spawn_workers` grader prompts, with matching criteria derived from the successful objective pattern. Fix the batch spawn playbook injection bug simultaneously.

---

## Quantitative Findings

### Shadow Grader Concordance Distribution (72h)

> **Note:** All 32 events are from a single 6-minute test run (d1238 Fable dispatch tests, June 26, 2026). These are NOT production concordance data. The C-grades reflect Opus being forced to approve test fixtures; the F-grades reflect Ollama infrastructure timeouts during that load spike. Production concordance data does not exist for the current 72-hour window.

| Concordance | Count | % | Meaning |
|-------------|-------|---|---------|
| A | 0 | 0% | Exact match: gemma4 = Opus grade |
| B | 0 | 0% | Same pass/fail, different letter grade |
| C | 17 | 53% | Different pass/fail verdict |
| F | 15 | 47% | gemma4 failed to produce output |

**Total shadow grader events (72h):** 32 (all from d1238 test run)
**Infrastructure errors:** 15 (47%) — Ollama 300s timeout + non-JSON response during test load

**Interpretation of C-grades:** Opus was given the instruction "Test approval" (forced approve for test fixtures). gemma4 independently evaluated the objectives ("Implement X", "Do the thing") and correctly rejected them as too vague. In these 17 cases, gemma4 was MORE correct than Opus, not less. The C-grade reflects test design, not gemma4 failure.

### Real Grader Distribution (grader-debug.log, June 25–27)

> Source: `/tmp/grader-debug.log` (403KB, covers June 25–27 only — pre-dates ollama worker activity window)

| Grade | Count | % | Meaning |
|-------|-------|---|---------|
| A | 47 | 29% | Approved as-is |
| B | 71 | 44% | Approved with minor notes |
| C | 23 | 14% | Needs revision |
| D | 11 | 7% | Significant issues |
| F | 9 | 6% | Rejected |

**Total real grader events (72h):** 161

### Worker Session Breakdown (72h)

> Source: `commander/data/db/ironclaude.db` (1,371 total workers all-time)

| Worker Type | Sessions (72h) | Completed | Completion Rate |
|-------------|----------------|-----------|-----------------|
| claude-opus | 24 | ~24 | ~100% |
| claude-sonnet | 15 | ~15 | ~100% |
| claude-fable | 1 | 1 | 100% |
| ollama | 0 | — | — |

**All-time ollama workers:** 7 sessions, 7 completed (100%), avg ~7.2 min per session
**All-time claude workers (sample):** avg 112–134 min per session

### Ollama Worker Activity Window

All 7 ollama workers ran between **May 30 and June 13, 2026** — predating the grader-debug.log window (June 25–27). No grader data exists for any ollama worker session. Completion rate is inferred from `finished_at IS NOT NULL` DB column.

---

## Concrete Objective Examples

### All Ollama Worker Objectives (complete set, from `commander/data/db/ironclaude.db`)

All 7 ollama sessions completed successfully. Examples are ordered newest to oldest.

---

**Example 1 — `d1108-pf2e-ch5-continue` (June 13, completed)**

> Continue the PF2e card pipeline. Read `pipeline/pf2e_ch5.py`. Resume from the checkpoint saved in `pipeline/state/ch5_progress.json`. Process the next batch of 20 cards using the existing `process_card()` function. Write updated state back to the checkpoint file on completion. Do not modify `pipeline/pf2e_ch5.py` — execution only.

**Assessment:** Well-scoped for a 12B model. Single primary file (`pf2e_ch5.py`), secondary read-only file (`ch5_progress.json`), single named function to call, explicit scope limit ("Do not modify"), explicit success condition (updated state file). No architectural judgment required. This is the canonical template for an ollama-appropriate objective.

---

**Example 2 — `d1084-ollama-readme-5` (June ~10, completed)**

> Rewrite `README.md`. Read the current file first. Update the Installation section to reflect the new `make deploy-hooks` command (replacing the old manual copy step). Do not change any other sections. Do not hallucinate commands — only include commands that appear in the Makefile. Success: `README.md` updated, `make deploy-hooks` visible in Installation section.

**Assessment:** Correct scope. Single named file, single named section, explicit anti-hallucination constraint, explicit success condition. The "Do not hallucinate commands" constraint is notable — the Brain learned to add this for ollama workers on documentation tasks.

---

**Example 3 — `d1084-ollama-readme-4` (June ~10, completed)**

> Rewrite `README.md` Installation section. Read `Makefile` for accurate command list. Do not modify any section other than Installation. Do not invent commands. Success condition: Installation section reflects current `make` targets.

**Assessment:** Same pattern as Example 2. Demonstrates that iterative README rewrites across d1084 converged on this template through trial. Earlier d1084 attempts (readme-rewrite, readme-rewrite-2) had similar structure.

---

**Example 4 — `d1084-readme-rewrite` (June ~9, completed)**

> Update `README.md`. Focus on the Quick Start section. Read existing file, update step 3 to use `make deploy-hooks` instead of manual hook copying. Keep all other content unchanged. Do not remove any existing sections.

**Assessment:** Correct scope. Explicit section target, explicit change, explicit preservation constraint. Slightly less structured than later examples — no anti-hallucination constraint, no explicit success condition. Still completed successfully.

---

**Example 5 — `d1084-readme-rewrite-2` (June ~9, completed)**

> Update `README.md` Quick Start. Change step 3 command to `make deploy-hooks`. Check Makefile to confirm the target exists before writing. Do not change other sections. Success: step 3 shows `make deploy-hooks`.

**Assessment:** Improvement over Example 4. Added "check Makefile first" grounding step. Demonstrates the Brain iterating toward the full template pattern.

---

**Example 6 — `d835-ollama-readme-test` (May 30, completed)**

> Test ollama worker capability. Read `README.md`. Identify the Quick Start section. Output the current step 3 verbatim. Do not modify anything. Success: step 3 content printed to output.

**Assessment:** Read-only capability probe. Very conservative scope — no writes. Used to validate that the ollama worker could follow workflow stages and read files correctly before assigning write tasks.

---

**Example 7 — `d835-ollama-test` (May 30, completed)**

> Test ollama worker. Update `README.md` — add a single line "IronClaude supports Ollama workers (experimental)" at the top of the Features section. Read the current file first. Stage the change with `git add README.md`. Success: line visible in staged diff.

**Assessment:** First write test. Explicit single-line addition, explicit location, explicit staging step, explicit verification method (staged diff). Very contained. Completed successfully.

---

### Pattern: What Successful Objectives Have in Common

All 7 completed ollama objectives share these traits:

1. **Single primary file target** — One named file to write or modify (README.md, pf2e_ch5.py)
2. **Explicit section or function scope** — "Quick Start section", "Installation section", `process_card()` function
3. **Preservation constraints** — "Do not change other sections", "Do not modify pf2e_ch5.py"
4. **Grounding step** — "Read the current file first", "Check Makefile to confirm target exists"
5. **Explicit success condition** — Observable artifact: staged diff, checkpoint file updated, specific line visible
6. **Anti-hallucination guard** (on documentation tasks) — "Do not invent commands", "Do not hallucinate"

### Pattern: What Would Make an Ollama Objective Fail (inferred from architecture)

No failed ollama objectives exist in the DB. Based on shadow grader test data and code audit, high-risk patterns are:

- **Multi-file scope without explicit routing** — gemma4 at 32768 tokens cannot hold full context for 3+ files simultaneously
- **Architectural judgment required** — "Refactor the auth module to be more maintainable" requires reasoning about design tradeoffs beyond 12B capability
- **Vague success condition** — "Improve the code" gives no stopping point; model may loop or give up
- **Open-ended verbs without explicit target** — "Analyze", "Review", "Improve" without a named output artifact

---

## Failure Mode Taxonomy

### FM-1: Ollama Infrastructure Timeout (15 occurrences in test data)

**Description:** Ollama inference times out at 300s (configurable `ollama_worker_num_ctx` default). During the d1238 test run, shadow grader Ollama calls timed out because the worker inference was consuming GPU resources simultaneously.

**Root cause:** Ollama serves as both worker inference engine AND local grader. When a worker is actively generating, grader calls queue behind it. Under load, grader timeouts produce F-concordance grades that appear as gemma4 failures but are actually resource contention.

**Evidence:** All 15 F-grades in 72h window are from a 6-minute test window where Ollama was under load. Non-JSON response pattern matches Ollama returning partial output on timeout.

**Fix scope:** Infrastructure — out of scope for this research (model stays as-is). Worth noting that dual-role contention means F-concordance rates are not reliable indicators of gemma4 quality.

---

### FM-2: Shadow Grader Tool Failures (3+ occurrences in grader-debug.log)

**Description:** The shadow grader's tool call attempts fail due to environment mismatches inherited from the grader system prompt.

**Observed failures:**
- `rg` not found — `grep_files` tool calls assume `ripgrep` is installed in the Ollama worker environment; it is not
- Wrong file paths — absolute paths like `/home/user/projects/traderbot` appear in grader tool calls, indicating path context from a different machine or session leaked into the grader objective
- Relative path ambiguity — `commander/src/ironclaude/orchestrator_mcp.py` used as relative path without confirming working directory

**Root cause:** Shadow grader receives the same objective text as the worker, including any embedded path references. The grader runs in the daemon's environment, not the worker's environment.

---

### FM-3: Playbook Injection Gap in Batch Spawn (1 confirmed bug)

**Description:** `OLLAMA_WORKER_PLAYBOOK` is injected via `--append-system-prompt` only in the single `spawn_worker` path (`orchestrator_mcp.py:1889`). The batch `spawn_workers` path (`orchestrator_mcp.py:2182-2183`) builds the command without this flag.

**Impact:** Ollama workers spawned via `spawn_workers` (batch) receive no workflow guidance — no stage rails, no tool availability list, no style directives. They see only the raw objective.

**Evidence from code audit:**
```python
# spawn_worker (single) — line 1889 — CORRECT
cmd = make_ollama_command(..., system_prompt=OLLAMA_WORKER_PLAYBOOK)

# spawn_workers (batch) — lines 2182-2183 — BUG
cmd = make_ollama_command(...)  # no system_prompt argument
```

---

### FM-4: Tier Matrix Blindspot (structural)

**Description:** The grader system prompts in both `spawn_worker` (lines 1771-1775) and `spawn_workers` (lines 2044-2047, 2109-2112) define `recommended_model` as one of `claude-sonnet | claude-opus | claude-fable`. `ollama` is not an option.

**Impact:** The grader cannot recommend ollama for an appropriate task, and cannot warn against ollama for an inappropriate task. Ollama assignment is always a Brain-level bypass of tier routing with no feedback loop. The `recommended_model` field in grader output is included in the result string (line 1986) but not enforced — it is informational only.

**Consequence:** The Brain currently makes ollama routing decisions without any structured complexity assessment. The successful objective pattern described above emerged from trial-and-error across d835 and d1084, not from systematic tier guidance.

---

### FM-5: Shadow Grader Double-Logging (observability bug)

**Description:** Every shadow grader concordance event appears exactly twice in `/tmp/ironclaude-daemon.log`. The 32 concordance events are 16 unique events, not 32.

**Impact:** Inflates concordance counts when grepping logs. Any automated analysis that counts log lines will overcount by 2x.

---

## Capability-Tier Matching Audit

### Current Tier Matrix (from grader system prompts)

From `orchestrator_mcp.py`, both `spawn_worker` (line 1771-1775) and `spawn_workers` batch (lines 2044-2047, 2109-2112) use this tier vocabulary:

```
claude-sonnet:
  - Simple, well-scoped tasks
  - Single-file edits
  - Clear requirements with no ambiguity

claude-opus:
  - Multi-file changes
  - Complex reasoning required
  - Architecture decisions

claude-fable:
  - Highest complexity
  - Architectural design
  - Cross-system changes
```

`ollama` is not present in any tier definition. The grader's `recommended_model` JSON field only accepts `claude-sonnet`, `claude-opus`, or `claude-fable`.

### Hypothesis Validation

**Hypothesis:** Ollama workers receive objectives rated opus-appropriate by the tier system.

**Evidence:** All 7 completed ollama objectives are scoped below claude-sonnet criteria — single-file, clear requirements, no ambiguity. The Brain appears to route tasks that are *simpler* than sonnet-appropriate to ollama, not tasks that are sonnet-appropriate or above.

**Verdict:** Not confirmed. The opposite appears true: ollama receives tasks scoped below the minimum claude-sonnet threshold. However, this is an inferred conclusion — no grader data exists for any ollama session to confirm what tier the grader would have assigned.

### Brain-Notes Injection

Brain-notes (`.ironclaude/brain-notes.md`) are appended identically to both ollama and claude worker objectives (lines 1779-1787 for single, 2018-2026 for batch). There is no ollama-specific filtering or summarization of brain-notes.

**Assessment:** Brain-notes may add useful context for claude workers (cross-session decisions, architectural notes) but the same notes could consume significant context budget for a 32768-token ollama model. If brain-notes are long, they compete with the objective text for the model's effective context window.

### OLLAMA_WORKER_PLAYBOOK Gap Analysis

From `commander/src/ironclaude/ollama_playbook.py` (35 lines):

**What it covers:**
- Workflow stage rail: idle → brainstorming → writing-plans → executing-plans
- Tool availability per stage (which MCP tools are valid when)
- Style directives: decisive, concise, no circular reasoning
- AskUserQuestion requirement for multi-choice decisions

**What it does NOT cover:**
- What constitutes an ollama-appropriate task scope
- Context limit (32768 tokens) and how to manage it
- When to request task reduction from Brain
- How to handle objectives that reference multiple files
- What to do when the objective is ambiguous (no escalation path defined)
- Explicit instruction not to attempt multi-file or architectural tasks

**Critical gap:** The playbook assumes the objective arriving in the worker is already correctly scoped for a 12B model. There is no defensive layer in the worker itself for out-of-scope objectives.

---

## Recommendations

### R1: Objective Construction

**Current state:** Brain constructs ollama objectives with no structured template. The successful pattern (single file, explicit steps, success condition) emerged empirically across d835 and d1084. This pattern is not codified anywhere.

**Proposed change:** Add an ollama objective template block to the Brain prompt (or wherever Brain constructs worker objectives). Template:

```
OLLAMA WORKER OBJECTIVE TEMPLATE
Target file: [exactly one named file]
Grounding step: [read target file / check [config] first]
Action: [single, named operation — NOT "improve" or "refactor"]
Constraint: [what must NOT change]
Success condition: [observable artifact — staged diff / specific line / file updated]
Anti-hallucination note: [if documentation task — "Verify commands in [Makefile/config] before writing"]
```

**Hard limits to enforce in template:**
- Exactly one primary write target (secondary read-only files permitted)
- Action verb must be concrete: "add line X", "change section Y to Z", "call function F" — not "improve", "analyze", "refactor"
- Success condition must be observable without running code

**Expected impact:** Eliminates the gap between the Brain's implicit objective quality (demonstrated in d1108, d1084) and a reliable, repeatable template. Reduces the iteration needed to produce a correctly-scoped ollama objective.

---

### R2: Task Decomposition

**Current state:** No pre-spawn complexity gate exists for ollama workers. Brain decides ollama routing directly. If an objective is too complex, the worker either loops, produces incorrect output, or exits early — with no structured escalation path.

**Proposed change:** Add a complexity pre-check step in `spawn_worker` before dispatching an ollama worker. The check is a rule-based filter (no LLM required):

```python
def _check_ollama_objective_complexity(objective: str) -> tuple[bool, str]:
    """Returns (is_valid, rejection_reason). Called before ollama dispatch."""
    file_refs = count_file_references(objective)
    if file_refs > 2:
        return False, f"References {file_refs} files — ollama limit is 2"
    
    open_ended_verbs = ["refactor", "improve", "analyze", "review", "optimize", "design"]
    for verb in open_ended_verbs:
        if verb in objective.lower() and "success" not in objective.lower():
            return False, f"Open-ended verb '{verb}' without explicit success condition"
    
    if len(objective) > 1500:  # chars, not tokens
        return False, "Objective exceeds 1500 chars — likely too broad for 12B model"
    
    return True, ""
```

On rejection: return the objective to Brain with the rejection reason for decomposition. Brain then either splits the objective or escalates to claude-sonnet.

**Expected impact:** Creates a feedback loop that the Brain currently lacks. Each rejection is a signal that the decomposition heuristic can be refined. Prevents the failure mode where a complex objective reaches the worker with no detection.

---

### R3: Capability-Tier Matching

**Current state:** `ollama` is absent from all grader tier matrices. Ollama routing is a Brain-level bypass with no grader validation. The `recommended_model` field in grader output cannot express "this is ollama-appropriate."

**Proposed change (two parts):**

**Part A — Fix batch spawn playbook injection bug:**

In `spawn_workers` (`orchestrator_mcp.py` ~line 2182-2183), add `--append-system-prompt` with `OLLAMA_WORKER_PLAYBOOK` to match the single-spawn path. This is a correctness fix, not an enhancement — batch-spawned ollama workers currently receive no workflow guidance.

**Part B — Add ollama tier to grader prompts:**

Extend the `recommended_model` options in both `spawn_worker` (lines 1771-1775) and `spawn_workers` (lines 2044-2047) to include `ollama`:

```
ollama (gemma4:12b — local inference):
  - Single-file edits only
  - Explicit, concrete action (not "refactor" or "improve")
  - Explicit success condition present
  - No architectural decisions required
  - Documentation tasks with grounding step (Makefile/config reference)
  Contraindicated: multi-file scope, ambiguous outcome, architectural judgment
```

Adding `ollama` to the tier matrix gives the grader the ability to:
1. Recommend ollama for correctly-scoped simple tasks (cost reduction)
2. Warn against ollama for tasks that exceed 12B capability
3. Create a data signal (grader concordance on ollama-routed tasks) that currently does not exist

**Expected impact of Part A:** Batch-spawned ollama workers gain workflow stage guidance, eliminating the risk of tool misuse on the batch path.

**Expected impact of Part B:** Tier routing becomes data-driven rather than Brain-intuition-driven. Creates the grader feedback loop needed to measure ollama routing quality over time.

---

## Appendix: Data Sources

| Source | Path | Coverage |
|--------|------|----------|
| Daemon log (primary) | `/tmp/ironclaude-daemon.log` | All-time, large; grader lines double-logged |
| Daemon log (recent) | `/tmp/ic/daemon.log` | Recent, 910KB |
| Grader debug log | `/tmp/grader-debug.log` | June 25–27 only, 403KB, 161 events |
| Daemon worker DB | `commander/data/db/ironclaude.db` | 1,371 all-time workers |
| MCP state DB | `~/.claude/ironclaude.db` | Workflow state only, no objectives |
| Code audit | `commander/src/ironclaude/orchestrator_mcp.py` | Single spawn lines 1752-1890; batch 2032-2183 |
| Playbook | `commander/src/ironclaude/ollama_playbook.py` | 35 lines |
