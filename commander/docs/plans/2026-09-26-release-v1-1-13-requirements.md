# Release v1.1.13 — Requirements

> **Created:** 2026-09-26
> **Status:** Operator-approved (directive)
> **Design:** docs/plans/2026-09-26-release-v1-1-13-design.md

## Operator directives (this session)

- "Hold up, instead of unreleased, we should have this be v1.1.13". This applies to the
  resolved-assignment-completion fix.
- Standing constraints:
  - no push or deploy without an explicit go;
  - no commit trailers;
  - local tests only.

## Acceptance criteria

1. The version is `1.1.13` in all five lockstep sources. The Codex version uses the
   `+codex.<timestamp>` suffix with a fresh timestamp. `test_version_consistency.py`
   passes. No `1.1.12` remains in those five files.
2. The CHANGELOG `## [Unreleased]` heading becomes `## 1.1.13: <title>`, and the entry
   text is unchanged.
3. The README has `## What's New in v1.1.13` (the fix), then `### Earlier — v1.1.12` (the
   former v1.1.12 bullets). The `### Earlier — v1.1.11` block is removed.
4. The full commander pytest suite passes with 0 failed.
