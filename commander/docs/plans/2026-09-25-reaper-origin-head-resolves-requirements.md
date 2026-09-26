# Reaper: Adopt origin/HEAD Only When Its Local Branch Exists — Requirements

> **Created:** 2026-09-25
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-25-reaper-origin-head-resolves-design.md

## Operator directives (this session)

- A fresh Fable adversarial review of `93bd9dc` against v1.1.12 returned HAS-ISSUES. The
  one MATERIAL finding: the reaper adopts `origin/HEAD`'s branch without checking that
  the local ref exists, which regresses `clone -b` and renamed-default repos compared
  with v1.1.12.
- Operator chose "PM loop, fold into commit". That means:
  - the reaper uses `origin/HEAD`'s branch only when the local ref resolves, and
    otherwise the primary checkout's branch;
  - add `clone -b` and renamed-default tests that fail today;
  - fix the misleading "unresolvable → null" doc wording and the long doc line at
    `workspace-service.ts:983`;
  - amend into `93bd9dc`.
- Operator approved the design as presented.
- Out of scope: review observations 1, 2 and 4.

## Acceptance criteria

1. `reapAmbiguousOrphans` targets `originHeadBranchRef(p)` only when it is non-null and
   `refResolves(p, it)` is true. Otherwise it targets
   `integrationTargetRef(primaryBranch(p))`, and a detached primary still throws.
2. A `clone -b develop` clone (`origin/HEAD` → `origin/main`, no local `main`) reaps a
   merged orphan at the `develop` tip.
3. A repo whose `origin/HEAD` points at a nonexistent `origin/master`, with the primary on
   `main`, reaps a merged orphan at the `main` tip.
4. `mergeOrphanThenReap` and `canonicalDefaultBranchRef` behavior is unchanged. All
   existing tests pass unmodified.
5. The doc comments no longer claim a missing local branch yields null. The long doc line
   is re-wrapped. The CHANGELOG reaper bullet says `origin/HEAD` is used only when its
   local branch exists.
6. The dist bundles are rebuilt. The full workspace-manager vitest suite passes with
   0 failed.
