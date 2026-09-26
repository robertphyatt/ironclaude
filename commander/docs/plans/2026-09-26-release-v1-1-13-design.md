# Release v1.1.13 — Design

> **Created:** 2026-09-26
> **Status:** Design Complete
> **Scope mode:** reduction
> **Requirements:** docs/plans/2026-09-26-release-v1-1-13-requirements.md

## Summary

The operator directed: "instead of unreleased, we should have this be v1.1.13."

The resolved-assignment-completion fix is staged: the status probe reports `resolved`, and
the seam completes a dead worker. Its CHANGELOG entry currently sits under
`## [Unreleased]`. This loop turns that into the v1.1.13 release by bumping the version,
titling the CHANGELOG section, and updating the README, following the v1.1.12 precedent
(`git show --stat v1.1.12`).

## Components

1. **Version bump 1.1.12 → 1.1.13** in the five lockstep sources that
   `commander/tests/test_version_consistency.py::test_version_sources_match` checks:
   - `commander/pyproject.toml:4`: `version = "1.1.12"`
   - `worker/.claude-plugin/plugin.json:3`: `"version": "1.1.12",`
   - `worker/mcp-servers/workspace-manager/package.json:3`: `"version": "1.1.12",`
   - `.claude-plugin/marketplace.json:10`: `"version": "1.1.12",`
   - `worker/.codex-plugin/plugin.json:3`: `"version": "1.1.12+codex.20260923220118",`
     becomes `"1.1.13+codex.<UTC YYYYMMDDHHMMSS at execution>"`. The format regex
     `\+codex\.[a-z0-9-]+` accepts that timestamp.

   The version string is not bundled into `dist/`: `1.1.12` has 0 matches in `cli.js`,
   `index.js` and `hook-intent.js`. So no rebuild is needed for the bump.
2. **CHANGELOG:** `## [Unreleased]` becomes `## 1.1.13: The seam completes a dead worker
   whose assignment is already resolved`. The entry text itself does not change.
3. **README:**
   - add a new `## What's New in v1.1.13` section with one bullet for the fix;
   - demote the current `## What's New in v1.1.12` section to `### Earlier — v1.1.12`;
   - drop the `### Earlier — v1.1.11` block, since precedent keeps a single "Earlier"
     section and the CHANGELOG retains the detail.

## Testing Strategy

- `test_version_consistency.py` passes. It only proves the five sources agree, so it
  cannot tell whether the bump happened.
- A presence guard proves the bump did happen: `rg -n -F 1.1.13` lists exactly one match
  in each of the five files. An absence guard, `rg -F 1.1.12` over the same files,
  returns no output.
- The full commander pytest suite passes.

## Implementation Notes

- Commit and tag `v1.1.13` are operator-gated: professional mode must be off. No trailers.
- Push and deploy need an explicit go. The deploy installs a `1.1.13` plugin cache (the
  cache directories are versioned), refreshes the dist in it, and restarts Commander.
