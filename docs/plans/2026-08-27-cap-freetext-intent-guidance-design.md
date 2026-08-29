# QW-CAP — Free-Text Git-Verb Intent Guidance — Design

> **Created:** 2026-08-27
> **Status:** Design Complete
> **Roadmap:** docs/plans/2026-08-24-post-v117-roadmap.md (QW-CAP)
> **Origin:** project_commit_and_push_freetext_intent_gap (reported v1.1.6, verified v1.1.7).

## Summary

Under professional mode, when the operator types free-text "commit and push", the agent calls the
`commit_and_push` MCP tool and is refused: "Direct Git operation requires a matching human intent".
Root cause (VERIFIED): the human intent is minted ONLY when the operator types the EXACT rendered
slash-command `/commit-and-push` as their literal prompt (`worker/hooks/state-activator.sh:54-55`);
free-text cannot carry it — BY DESIGN, the non-forgeable-human-signal invariant (the "never push
without an explicit per-action go" rule, crystallized after an agent inferred push authority from a
bundled "commit and push" and pushed a public repo prematurely). The problem is NOT the gate — it is
that the agent hits the dead-end LATE (after staging) with no earlier signal, because the only surface
it reads on the free-text path is the thin tool description ("Consume exact human intent and perform
direct <verb> authority"), which names the concept but not the mint path or the correct response.

This is a guidance/UX fix ONLY. It changes two surfaces the free-text path actually reads; it does NOT
touch the intent-mint gate and does NOT promote free-text to intent.

## Constraints

- **HC1 — mint gate byte-unchanged.** `state-activator.sh:53-72` is not touched. Free-text is never
  promoted to a human intent (security: the agent could paraphrase/infer/hallucinate a git verb from
  ambiguous prose and thereby manufacture push authority — [[feedback_never_push_without_explicit_go]]).
- **HC2 — no confirmation-click path.** "Should a confirmation click mint intent?" is out of scope
  here — it is M7c's C4a shared attested-confirmation primitive.
- **HC3 — refusal-string append, not rewrite.** 18 test assertions (git-authority.test.ts ×15,
  workspace-service.test.ts ×3) match the `matching human intent` substring; vitest `.toThrow(string)`
  is a substring match, so preserving the existing text as a PREFIX keeps them green. (The plan verifies
  none use an anchored regex or exact `toBe`.)
- **HC4 — doc filenames avoid the verb words.** Plan/design doc filenames must not contain "push" or
  "commit" — the execution_complete bash guard regex matches those as PATH substrings and would block
  `git add`. (This doc and the plan use "cap-freetext-intent-guidance".)

## Architecture (the two surfaces — Fable leverage-ranked)

1. **Tool description — DOMINANT (index.ts:122-137).** The shared generated description for
   `commit`/`commit_and_push`/`push` is the ONLY surface guaranteed in the agent's working set at the
   exact moment it decides whether to call the tool on a free-text request (tool schemas persist every
   turn, unlike one-shot skill/banner text). Extend it so the agent, on prose, routes to the form
   instead of calling the tool.
2. **Refusal-string append — DETERMINISTIC BACKSTOP (git-authority.ts:556, 612).** Fires only after a
   wrong call, but delivery is 100% guaranteed and names the remedy in-band, making the residual case
   self-recovering instead of a dead-end.

**CUT (Fable, ~zero leverage):** a line in the `/activate-professional-mode` output. It is read once at
session start — dozens of turns and possibly a compaction from the decision point — the same
"loaded-once, invisible-at-the-decision" evanescence as the SKILL.md text that already failed. Its
operator-education value is duplicated by surfaces 1+2 (the agent's own reply hands over the exact
`/<verb>` string). Surfaces 1+2 fully close the loop; the banner catches no third path.

## Components / Data Flow

- **`worker/mcp-servers/workspace-manager/src/index.ts:122-137`** — the `['commit','commit_and_push','push']`
  map. Extend the `description` template (one edit covers all three verbs) to add, after the existing
  sentence: intent exists ONLY when the operator typed the rendered `/<verb>` form as their literal
  prompt this turn; free-text prose does not carry it; on a prose request, REPLY with the `/<verb>` form
  for the operator to type rather than calling this tool (it will refuse); after the verb completes,
  carry forward any remaining instruction from the prose (the compound-prompt tail).
- **`worker/mcp-servers/workspace-manager/src/git-authority.ts:556, 612`** — both refusal sites (the
  assigned and unassigned paths) share `'Direct Git operation requires a matching human intent'`. APPEND
  the remedy so the message reads e.g. `'Direct Git operation requires a matching human intent — the
  operator must invoke the rendered git form (/commit, /commit-and-push, or /push) as their literal
  prompt; free-text prose does not mint intent.'` The `matching human intent` prefix is preserved.

Flow after the fix: operator types prose "commit and push" → the agent, reading the tool description,
does NOT call the tool; it replies with the `/commit-and-push` form for the operator to type, and notes
it will handle the rebuild/restart tail after. If the agent calls anyway, the refusal names the exact
remedy in-band → self-recovers. No dead-end either way. The gate is unchanged; authority still traces
only to the operator's real keystroke.

## Error Handling

The only behavioral surface is the refusal string itself (now self-explaining). No new failure modes:
the classifier/gate is untouched; this is pure text on two existing surfaces.

## Testing Strategy

- **Tool description:** a test asserting the published `commit_and_push` (and `commit`, `push`) tool
  description CONTAINS the key new guidance substrings (e.g. "does not carry" / "reply with" the form)
  — proves the dominant surface actually ships the guidance. This is a light presence assertion; its
  falsifier is deleting the sentence.
- **Refusal append:** a test asserting the refused error message CONTAINS both the preserved prefix
  `matching human intent` AND the new remedy substring (e.g. "does not mint intent"). Confirms the
  append shipped AND the prefix survived. Run the full suite to confirm the 18 existing substring
  assertions stay green.
- **Mint-gate untouched:** a git-level assertion (or a diff check in the plan) that
  `state-activator.sh` is byte-unchanged — the HC1 falsifier.
- No new never-push / never-lose-work surface is introduced (nothing to falsify there).

## Implementation Notes

- Two source files: `index.ts` + `git-authority.ts` (both bundle into the MCP dist — rebuild on deploy).
  Plus the two test files (`git-authority.test.ts`, and a tool-description assertion — likely
  `tool-dispatch.test.ts` which already inspects the tool list).
- `state-activator.sh` is NOT in the change set (HC1).
- Deploy like M6/M7b: rebuild dist (Claude loads from repo), and — because this changes the MCP —
  the Codex cache needs a reinstall + cachebuster if Codex is used.
- One small loop (2 code files + tests); no schema, no gate change, no push.
