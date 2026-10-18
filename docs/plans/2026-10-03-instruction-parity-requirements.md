# Instruction Parity (v1.1.16 L2b-2) Requirements

> **Created:** 2026-10-03. Revised the same day, after a retreat from planning.
> **Status:** Operator-approved. Next step after the operator chose "L2b-2, then L3 (Rec.)". The decisions below come from this brainstorm.
> **Roadmap:** `docs/plans/2026-10-02-v1-1-16-roadmap.md` (L2b-2; L2b-3 split out)

## Standing operator directives

- **One IronClaude.** Claude and Codex behave the same, with no client-specific behavior forks. Provider plumbing, such as tool names, is the only allowed exception.
- **Question Claude-only features.** "If there are things that we realize aren't needed to get the functionality in codex, let's talk about if we need them at all in Claude."
- **Scope fidelity.** No unrequested tooling. Every verification must be able to fail.
- **Final review gate.** "We need a final passing fable review though." A Fable end review of the full staged diff must come back CLEAN before the operator commits.
- **Safety limits.** No push. No commits by the agent. Never kill processes by pattern match.

## Brainstorm decisions

1. **Parity method: "Converge + parity test (Rec.)".** The instruction files are edited by hand to the same directive set. A test then keeps them aligned.
   - Rejected: a generator (more tooling) and text fixes alone (they would drift again).
2. **Canonical set: "behavioral.md's 15 (Rec.)".**
   - The Codex surfaces also keep "Actual Claude Fable from Codex", which is the one named exception.
   - Rejected: slimming `CLAUDE.md` down to a pointer.
3. **Global files: "Fix both identically (Rec.)".**
   - Back up first.
   - Directive 7 gets the project's delegate-by-default wording in both `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`.
4. **Codex rules: "Prune to general (Rec.)".**
   - Back up `~/.codex/rules/default.rules` first.
   - Keep the original lines 1, 2, 4, 5 and 6. Remove line 3 (a blanket `codex exec`), lines 7–17 (stale one-offs), and line 15 among them (a full-bypass config copy).
5. **Retreat from planning.** Plan drafting found three design premises contradicted by the source:
   - The worker templates are byte-exact copies of `activate-professional-mode/SKILL.md` blocks (`test_worker_claude_md_template.py:43-62`).
   - `test_activation_client_parity.py:481-487` forbids `ironclaude:search-conversations` on Codex surfaces.
   - The memory-gate arm points never fire. `mark_brainstorming` is called only after debugging (`brainstorming/SKILL.md:163`), and `mark_design_ready` sets `active_skill` to NULL.

   The operator then decided items 6–9.
6. **Templates: "Split to its own loop (Rec.)".** L2b-2 covers the three repo surfaces. A new loop, L2b-3, updates the activation skill, both templates and the pinned tests together.
7. **Search line: "Client-correct names (Rec.)".** The rule is the same on both clients; only the tool name differs.
   - Claude surfaces keep `Use the ironclaude:search-conversations agent, not raw MCP tools`.
   - Codex surfaces use ``Search with the `episodic-memory` MCP server's search capability`` (the existing wording at `worker_agents.md:35`).
   - The two global files then differ in the title and this one line.
8. **Gate history (Fable investigation, operator-requested).** It covered about 1,900 archived sessions per gate.

   | | Brainstorming gate | Writing-plans gate |
   |---|---|---|
   | Sessions where it fired | 3% | 28% |
   | Real catches | about half of firings | 10 of 93 audited incidents, 6 of them weak |
   | Firings that changed the work | about 1 in 4 | 57 of 93 audited were ceremonial |

   - Most writing-plans firings came after the plan was already written.
   - An implementation-specific search run before drafting sometimes added material: 5 of 15 brainstorm→plan pairs.
   - Re-arming on a skill re-invoke caused stale blocks.
9. **Gates:**
   - Brainstorming gate: kept on both clients, armed only when a new effort starts.
   - Writing-plans: **"Instruction only (Rec.)"**. The writing-plans gate is removed on both clients. Step 0.5 asks for an implementation-specific search before drafting, with nothing enforcing it.
10. **Design approval: "Yes, write it up (Rec.)"** for this revision.

## Requirements

### R1. Repo surfaces converge

These three files carry the 15 canonical directive headings:
- `AGENTS.md` (Codex in this repo);
- the repo `CLAUDE.md`;
- `.claude/rules/behavioral.md`.

`AGENTS.md` also keeps "Actual Claude Fable from Codex". Directive bodies match `behavioral.md`, with these exceptions:
- each file's client-correct search line;
- Codex-correct plan-mode text in `AGENTS.md`;
- the Codex path text that `AGENTS.md` already carries, such as the advisor broker.

Specific fixes:
- `AGENTS.md:35` gets the Codex search wording.
- `AGENTS.md:72`: `EnterPlanMode`/`ExitPlanMode` are Claude Code tools, replaced by the brainstorming workflow. Codex has no such tools.
- `behavioral.md:59`: the ladder becomes `luna→terra→sol→astra`.

### R2. Parity test

One test parses the directive headings of the three repo surfaces. It asserts:
- the canonical 15 are present on each surface;
- the Fable directive appears on `AGENTS.md` only.

The test must fail against today's files.

### R3. Global files

1. Back up both files as `<file>.bak-2026-10-03`.
2. Directive 7 becomes the delegate-by-default line in both files.
3. In `~/.codex/AGENTS.md` only, the search line becomes the Codex wording.
4. Afterwards, `diff` of the two files shows only the title line and the search line.

### R4. Codex rules

1. Back up the file as `default.rules.bak-2026-10-03`.
2. Afterwards the file contains exactly the original lines 1, 2, 4, 5 and 6.

### R5. Brainstorming entry and gate, identical on both clients

**Entry.** Brainstorming Phase 1, which runs before Step 0.5's search, runs the Mandatory Direct Transition Preflight and calls `mark_brainstorming`.
- On Claude, `skill-state-bridge` has already moved the stage, so the preflight skips the call.
- On Codex, this is the entry into brainstorming.

**Arm** `memory_search_required=1` only when a new effort starts, meaning the from-stage is not `debugging` or `brainstorming`:
- `mcp-state-logger.sh` arms on a `changed:true` `mark_brainstorming` under either tool-name spelling;
- `skill-state-bridge.sh` arms only when the brainstorming transition comes from a non-debugging stage.

**Clear.** `memory-search-tracker.sh` clears the flag on the episodic-memory MCP search tool name, as measured in R7, on both clients. It keeps the Agent clear. `hooks.json` routes the search tool to the tracker. If R7 finds that the `"Task"` matcher does not receive Agent calls, the matcher must also include `Agent`.

**Message.** GBTW's block message names both clients' search paths.

**Tests.** Every hook change gets a RED→GREEN test.

### R6. Remove the writing-plans gate

On both clients:
- `skill-state-bridge.sh:49` no longer arms the flag for writing-plans;
- GBTW's gate condition covers brainstorming only.

`writing-plans/SKILL.md` Step 0.5 asks for a search before drafting, with implementation-specific queries: gotchas in the files being touched, and prior decisions on how the thing was built. It must not repeat the brainstorm query. Tests that asserted the writing-plans gate are updated.

### R7. Measure first

Measure, each with a positive control:
- Codex's PostToolUse `tool_name` for an episodic-memory search, using a headless `codex exec` scratch-repo probe;
- whether Claude's `"Task"` matcher receives Agent calls.

Afterwards, remove the scratch trust entry from `~/.codex/config.toml`. The hook tasks take their names from this evidence.

### R8. Verify and redeploy

1. Run all hook suites and the Commander suite.
2. `deploy-hooks`.
3. Copy `hooks.json` to the Claude plugin cache. It changes in this loop, so `diff` before and after.
4. `codex plugin add`, then the cache `diff -rq`.
5. Write the deploy log.
6. The operator re-trusts the new or changed Codex hooks.

### R9. Final gate

The Fable end review of the full staged diff must come back CLEAN before the operator commits.

## Out of scope

- **L2b-3 (same release):** the activation skill template blocks, `worker_claude_md.md`, `worker_agents.md`, and their pinned tests.
- **Backlog:** `worker/CLAUDE.md`, `commander/CLAUDE.md`, `commander/src/brain/rules/behavioral.md`, `~/CLAUDE.md` and `~/Code/CLAUDE.md`.
- **L3:** the experiment stage and plan amendments.
