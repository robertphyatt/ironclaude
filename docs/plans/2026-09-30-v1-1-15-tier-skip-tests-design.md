# v1.1.15 Tier-Skip Test Coverage — Design

> **Created:** 2026-09-30
> **Status:** Design Complete
> **Scope mode:** hold
> **Requirements:** docs/plans/2026-09-30-v1-1-15-tier-skip-tests-requirements.md

## Summary

The v1.1.15 broker skips an installed-model tier that has no model: gpt-6 ships no terra, so luna one-up has to reach sol. No test currently proves that skip happens. This change adds two vitest cases to `advisor-review.test.ts` that exercise the skip and the R4 fallback. No production code changes.

## Approach

This is the only viable approach, because the code already implements R4 and only the coverage is missing. The two cases follow the same pattern as the existing one-up tests: `harness()`, override `h.deps.readFile` with a cache JSON, set `trustedRequesterModel`, and assert `payload(result).reviewer_model` plus the `-m` argv.

## Components

In `describe('runCodexAdvisorReview', …)`, add an `it.each` with two rows:
- `['gpt-6-luna', ['gpt-6-sol','gpt-6-astra'], 'gpt-6-sol']` covers skipping an empty tier.
- `['gpt-6-sol', ['gpt-6-luna'], 'gpt-6-astra']` covers a non-empty cache with nothing at or above the next tier, which triggers the built-in fallback.

## Testing Strategy

- **RED is not achievable by construction:** the implementation already exists, so these tests pass when first run. Falsifiability is proven instead by temporarily applying the collapsed-loop mutant to `advisor-review.ts`.
  - The mutant is `return newestAtTier(installed, TIER_ORDER[start]) ?? FALLBACK_CODEX_MODELS[start];`.
  - Row 1 must fail under it: the mutant returns fallback `gpt-5.6-terra` instead of `gpt-6-sol`.
  - Then revert.
  - Row 2 guards against a mutant that returns a lower installed model: removing the fallback path would throw or return `gpt-6-luna`.
- **Suite checks:** the full `advisor-review.test.ts` passes, and `tsc --noEmit` is clean.
