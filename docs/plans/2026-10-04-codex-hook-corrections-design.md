# Codex Hook Corrections and Premature Stop Detection

> **Created:** 2026-10-04
> **Status:** Design approved — operator selected the existing context-aware LLM judge and instructed "Proceed"
> **Loop:** full — hook enforcement, workflow state, and Git path handling
> **Scope:** selective — the approved hook and instruction corrections plus premature-stop detection
> **Authority:** proceed through planning, review, and execution for these corrections; no deployment, commits, pushes, or WIP branch deletion
> **Requirements:** `docs/plans/2026-10-04-codex-hook-corrections-requirements.md`

## Requirements

1. Repair retreat context, rename coverage, absolute path mapping, client-native skill handoffs, and exact completion restaging.
2. Resolve the experiment-containment contract before implementing the separately staged L3 design. No unanswered question about L3 blocks designing the independent corrections.
3. GBTW must detect authorized work abandoned after an announcement, including during brainstorming. Asking an optional question is not sufficient reason to stop independent work.
4. Preserve legitimate design dialogue, explicit operator pauses, required approvals, genuine missing information, completed requests, and necessary waits for active work. Never induce busywork, repeated polling, or unauthorized implementation.
5. Apply the same behavioral rules to Codex and Claude using their actual native inputs and skill-loading mechanisms.
6. Extend existing hook tests and fixtures. No new general framework, monitoring service, or review portal.

## Evidence and Root Causes

The current source contains five code/instruction defects and one design conflict. Isolated test execution remains outstanding.

The added GBTW issue has three directly observed causes in `worker/hooks/get-back-to-work-impl.sh`:

- Lines 918–924 ordinarily disable continuation grading in brainstorming. The exception at lines 132–143 detects checkpoint/query-offload proposals, not general promise-only stops.
- Lines 680–696 explicitly treat asking a question or presenting options as grade A without checking whether the answer prevents further authorized work.
- Lines 606 and 864–865 supply the last assistant message as the grading context. The continuation call has no dedicated normalized user-request/current-turn-action context.

Lines 972–979 also suppress continuation from prose such as "waiting for". That is an assertion of a wait, not proof that the wait prevents remaining independent work. Existing live-job detection remains useful evidence and should be preserved.

The two final replies in this conversation are acceptance examples: after an instruction to brainstorm, the assistant announced a design or promised to add a GBTW fix, but did not perform the available design work. A later response with an actual design section and a necessary architecture decision is different: that is the requested collaborative work itself.

Historical GBTW designs addressed false positives during real questions and active subagent waits. Those protections remain requirements. Their blanket suppression is too broad for the newly requested behavior; replaying their old plans would not implement this correction.

## Approaches

### A. Context-aware continuation grading in the existing hook — recommended

Retain the existing grader, output protocol, warning mechanism, and bounded retry behavior. Run continuation evaluation during brainstorming, provide bounded request/action context, and distinguish necessary pauses from independent work left undone.

**Pros:** addresses all three observed causes; preserves semantic judgment and legitimate dialogue; uses the existing enforcement path. **Cons:** requires small client-aware context adapters and live grading validation; adds a continuation evaluation to brainstorming stops. **Alignment:** directly addresses the operator's repeated premature-hand-back example while keeping this a focused correction.

### B. Deterministic promise-only blocker alongside current suppression

A lexical rule detects final responses such as "I will investigate" without subsequent tool activity and blocks those responses even when continuation grading is suppressed.

**Pros:** lower grading cost and predictable handling of exact phrases. **Cons:** planning and explanation can be real work without tools; tool calls can be irrelevant busywork; paraphrases evade the rule; necessary questions remain difficult to distinguish. **Alignment:** catches the surface form of the operator's example but does not reliably enforce the intended behavior. Rejected as the primary mechanism; exact phrases remain test examples, not policy.

Prompt-only tuning was also considered and rejected: a better rubric cannot affect a check that never runs during brainstorming.

## Architecture

### Existing hook and workflow corrections

**Retreat context:** use the successful transition's destination when updating stage-dependent active-skill context. A retreat to brainstorming selects brainstorming; a retreat to debugging selects systematic debugging. A same-target transition remains mutation-free. Failure responses never change context. Returning to the same effort does not automatically become a new effort or re-arm unrelated gates.

**Rename coverage:** dirty-path collection includes both the source deletion and destination addition for a rename. Copy records do not invent a source deletion. Preserve literal path handling; do not broaden matching to Git pathspecs or globs.

**Path agreement:** drift and scoped commits use the same contract for absolute entries: resolve existing ancestor directories, preserve the final component as the Git entry, and constrain the result to the effective repository. Preserve managed-worktree restrictions on symlink components. Relative entries remain exact, canonical paths. Test internal directory aliases, aliases of the root, deleted leaves, and targets outside the repository. An unreadable path must not silently masquerade as a proven external path.

**Codex handoff:** instructions name each client's actual skill-loading mechanism. Codex reads and applies the next SKILL.md and performs its required MCP transitions; Claude invokes its Skill tool. Both must follow the same planning/review sequence. Remove hard gates that demand an unavailable tool without weakening the requirement to use the next skill.

**Restaging:** handle absolute and relative allowed entries separately, normalize repository membership, and stage only the explicit allowed set. Account for tracked deletions without requiring the working-tree file to exist; do not introduce broad staging of directories or unrelated changes. Keep independent staged work untouched.

### GBTW decision rule

The question is: **Has the assistant reached a legitimate stopping point for the user's current request?** It is not: "Did a tool run?" or "Does a sentence contain future tense?"

Grade D/F only when evidence identifies unfinished authorized work and an available next action that does not require further authority or a missing decision. A block names that specific action. A bare promise to act, a voluntary checkpoint, or an optional question does not establish completion.

Grade A/B for a completed request, an explicit operator pause, or a necessary decision/approval/input that prevents the remaining work. A substantive brainstorming section followed by a material design choice is valid progress and a legitimate pause when proceeding would commit to that choice. The grader must not demand implementation during brainstorming.

An unanswered question is evaluated by dependency: what work requires its answer, and what independent work remains? Apply the same reasoning to live subagents or external waits. Mere presence of a question, a live job, or the words "awaiting" must not exempt unrelated unfinished work. Existing deterministic task/review gates retain their authority.

## Data Flow

1. Use the trusted Stop event's identity and workflow state; retain the existing Codex/Claude output wrappers.
2. Normalize a bounded slice of the existing transcript into the current user request, current-turn assistant actions and tool outcomes, pending questions/waits, and final assistant text. Prefer explicit event fields where available. Parse actual provider records; never infer missing activity from absence in a truncated window.
3. Mark unavailable or truncated evidence explicitly. Keep request text separate from tool output, and fence all conversation content as data rather than grader instructions.
4. Pass this context only to the continuation check. Other grading checks retain their current inputs unless a demonstrated dependency requires a change.
5. Remove blanket brainstorming and prose-only waiting suppression for continuation. Feed legitimate wait evidence into the decision instead of treating it as automatic exemption. An old `execution_complete` stage must not exempt a new explicit user request from assessment.
6. Interpret the existing grade/reason response through the current block/warn path. A block must identify remaining work; a generic demand to "keep working" is insufficient.

Context extraction is a small hook helper, not a new task ledger or session database. Existing Codex transcript fixtures and parser conventions establish that provider records can be normalized, but the exact Stop-visible record availability must be verified before claiming live enforcement.

## Error Handling

Preserve existing bounded retry protection. This change must not create infinite continuation loops or force a model to fabricate activity. Missing transcript context and validator failures remain explicit verification limits; do not label a fallback as proof that the request was completed.

An apparent blocker must be assessed against the user's authorization and the next action. A blocked test command is not a reason to ask the operator to execute it; use the sanctioned investigation workflow when runtime validation is required. Source inspection and design work may continue independently.

## Testing Strategy

Extend existing tests with cases that can distinguish the corrected behavior from the current implementation:

- Execution → retreat → equal-target brainstorming resume selects brainstorming context without replaying the transition.
- Rename to an allowed destination still reports an unallowed source deletion; an allowed rename passes; copies do not report false deletions.
- Absolute internal aliases agree across drift and commit handling; out-of-repository targets cannot expand the allowed set.
- Restaging handles absolute/relative files and tracked deletions while preserving unrelated staged work.
- Both clients have an executable skill handoff, with no Codex dependency on Claude's Skill tool.
- Exact promise-only examples from this conversation block even during brainstorming.
- An optional experiment-containment question does not justify abandoning independent fixes.
- A substantive design section plus a necessary architectural choice passes.
- A completed analysis with no tool calls passes; unrelated tool calls do not make an otherwise empty promise pass.
- Explicit pauses, necessary approvals, and genuine waits pass when no independent authorized work remains.
- Completed or failed jobs do not count as live waits. Discussion quoting an anti-pattern is not itself an anti-pattern.
- Missing/truncated context and failed grading cannot produce a claim of verified completion or an unbounded retry loop.

Use existing hook fixtures for routing and response-shape assertions. Exercise representative pass/fail examples with the configured validator, reporting actual results rather than treating mock grades as semantic validation. Verify both the shared implementation and Codex's native Stop response. No new test harness is proposed.

## Experiment Design Boundary

The current L3 documents promise product preservation but specify only new-dirty-path detection. The recommendation is to protect pre-existing work as well. However, a post-hoc content comparison can detect damage; it cannot prevent damage, and Git ignores are not a complete inventory of product data. Do not describe any post-hoc drift detector as a filesystem sandbox.

Before L3 implementation, choose between a detection-only contract with explicit limits and actual write containment. That choice needs a separate grounded design; it does not delay the hook and instruction fixes above. No experiment stage is implemented in this correction draft.

## Implementation Notes

Primary existing surfaces: `worker/hooks/get-back-to-work-impl.sh`, `hook-logger.sh`, `mcp-state-logger.sh`, `execution-drift-check.sh`, associated hook tests, and the brainstorming/executing-plans skill instructions. Reuse existing path contracts from workspace-manager; preserve client identity and managed-worktree authority boundaries.

The WIP branch is excluded from implementation. It remains a stale, separately inventoried branch; no deletion or merge is authorized by this design.

## Settled Design Decision

Operator approved Approach A: "The fix is to run the existing judge at those stops, give it the user’s request and relevant turn activity, and ask: Does authorized work remain that the assistant can do now? I agree, that's the right approach. Proceed."

Use the existing LLM judge; do not implement the rejected phrase detector. Proceed through the normal plan and review gates. Approval does not claim implementation or validation is complete. The separate L3 containment decision remains open and is not a dependency of this correction effort.


## Exact-path and retry completion safeguards

The same approved correctness scope includes two boundary corrections. Preserve ordinary baseline serialization and exact path matching. A current dirty Git path containing LF cannot be safely represented by the existing line-based comparison; return a distinct unsupported-path result and block submit/Stop visibly, even if split fragments are allowed or baseline evidence is missing. This is a conservative limit, not a claim that newline paths are supported. Check both rename paths and only the changed copy destination. Do not delete or rename user files automatically.

The Codex wrapper must use the implementation retry precedence: non-null stop_hook_active takes precedence; otherwise positive loop_count means continuation. Preserve existing output protocol, initial one-retry behavior and terminal response on continued implementation failure. No persistent state or new runtime is needed.

Alternatives: migrating baseline storage to a new encoded format would broaden this fix and require compatibility handling; defer that redesign. Silently failing open on an unrepresentable path would retain the bypass, so reject it. These source repairs remain inside authorized hook correctness work, with no deployment, configuration change, commit, push, or WIP disposition.

The unsupported-path guard runs before baseline/allowed reads inside the existing eligible drift check. Existing root/task/professional-mode gates and the bounded Stop escape retain precedence.
