# v1.1.15 Tier-Skip Test Coverage — Requirements

> **Created:** 2026-09-30
> **Scope mode:** hold

## Operator directives

- "Squash all the v1.1.15 commits into a single commit and then do an adversarial review with fable".
- The Fable review returned HAS-ISSUES with one Important finding. The operator chose "Fix first, then squash (Rec.)".

## Facts (verified)

- `resolveCodexReviewer` is at `worker/mcp-servers/state-manager/src/tools/advisor-review.ts:230-240`. Starting from the next tier, it loops upward and returns the first tier's newest installed model. If no tier has an installed model, it returns `FALLBACK_CODEX_MODELS[start]`.
- Every existing one-up test in `advisor-review.test.ts` uses a cache that already has a model at the next tier: `LEGACY_CACHE`, `GPT6_CACHE`, and the gpt-7-sol case. The fallback tests make `readFile` throw.
- Consequence: collapsing the loop to `newestAtTier(installed, TIER_ORDER[start]) ?? FALLBACK_CODEX_MODELS[start]` would pass the whole suite.
- v1.1.15 requirement R4: "`one-up` means the newest installed model at the next tier above the requester's. If that tier has none, move up again … If nothing qualifies, use the built-in fallback's model for that tier." This means the fallback naming an uninstalled model is intended behavior.
- Episodic memory holds no prior decision on this topic.

## Requirements

- **T1.** Add a test: requester `gpt-6-luna` with an installed cache of `['gpt-6-sol','gpt-6-astra']` (no terra). The expected reviewer is `gpt-6-sol`. This test must fail if the tier-skip loop is removed.
- **T2.** Add a test: requester `gpt-6-sol` with an installed cache of `['gpt-6-luna']`. The cache is non-empty, but nothing is installed at or above astra. The expected reviewer is the built-in fallback `gpt-6-astra`. This pins R4.
- **T3.** Tests only. No change to `advisor-review.ts` or to the bundle.

## Non-goals

- The review's non-blocking observations stay out of scope: the skill's "today" example, the `visibility` field, and `CODEX_CLI_PATH` normalization.
- No squash or commit in this effort. The operator performs the squash afterwards.
