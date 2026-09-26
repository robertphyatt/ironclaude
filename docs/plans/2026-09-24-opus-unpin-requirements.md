# Opus-Tier Unpin — Requirements

> **Created:** 2026-09-24
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-24-opus-unpin-natural-resolution-design.md

## Operator directive (verbatim)

- "since opus 5.5 came out, please make it so that we stop pinning to opus 4.8 and let it just auto resolve"
- "Let it auto resolve = let it use opus 5.5"
- "I'm trying to point out that auto resolution no longer goes to the non helpful/adhd opus 5"
- "Just find all the spots where we pin 4.8 and let it resolve naturally. Use my pm loop to do it"

## Intent

The internal Opus tier was deliberately pinned to `claude-opus-4-8` because the
bare `opus` alias used to auto-resolve to Opus 5, which was too unreliable to run
the workflow. Natural resolution now lands on Opus 5.5, which is reliable, so the
pin's premise is obsolete. Remove the pin so the Opus tier resolves naturally.

## Acceptance criteria

1. No source, config, or test file names the literal `claude-opus-4-8`; every such
   occurrence becomes the bare `opus` alias. (The historical `CHANGELOG.md`
   entry recording the original pin is the sole exception — it is left intact.)
2. The Opus tier resolves naturally via the bare `opus` alias, exactly as the
   `sonnet`, `haiku`, and `fable` tiers already do. **No explicit version id
   (e.g. `claude-opus-5-5`) is introduced anywhere.**
3. The `provider_config.EXPECTED_MODELS` validator and every config file / test
   fixture that must match it change in lockstep, so config parsing still
   succeeds and the exact-match guard is not loosened.
4. The `[1m]` 1M-context beta path is preserved: the Brain/grader still launch the
   Opus tier as `opus[1m]` (the substitution turns `claude-opus-4-8[1m]` into
   `opus[1m]`). `_MODELS_NEEDING_1M_BETA` is not changed by this work.
5. The `README.md` rationale that justified the pin is rewritten to describe
   natural resolution; a new `CHANGELOG.md` entry records this change.
6. Full commander pytest suite passes.

## Non-goals

- Reworking `_MODELS_NEEDING_1M_BETA` / the `[1m]` beta detection (a separate
  future change, triggered only if natural resolution becomes a native-1M Opus
  that rejects the beta — verified by the operator-gated pre-deploy check).
- Any re-architecture of model/tier selection.
- Feature B (orphan prevention) — a separate later loop.
- Changing the `codex` tier model ids (`gpt-5.6-*` / `gpt-6-astra`) — unrelated.
