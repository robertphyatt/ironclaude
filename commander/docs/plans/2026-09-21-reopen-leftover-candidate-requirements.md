# reopen_for_edit leftover-candidate discriminator (I-1) — Requirements

> **Created:** 2026-09-21
> **Status:** Operator-approved ("Fix now (lineage 120)")
> **Source:** Final Fable end review of lineage 119 (1 MATERIAL: I-1).

## Authority

Operator directive: "Fix now (lineage 120)" for the I-1 regression the ancestry reopen-guard introduced. Folds into unpushed v1.1.11 (`8ed4807`) alongside lineage 119. Standing constraints: local tests only; no trailers; no push/deploy without explicit go; no version bump; new lineage earns its own blind plan review.

## Requirements

**R1 (I-1).** The `reopen_for_edit` interrupted-CAS guard MUST refuse ONLY a genuine this-lifecycle landed CAS, distinguished from a stale prior-lifecycle leftover candidate by whether the candidate DESCENDS from the held lock's `expected_target`. Concretely: refuse when `isAncestor(candidate, targetRef) && <lock held> && isAncestor(lock.expected_target, candidate)`. A stale leftover candidate (an ancestor of `expected_target`) MUST NOT be refused — it proceeds to a cleaning reopen (clearing the leftover candidate/freeze refs + lock, returning the row to `active`). The genuine landed-then-advanced row MUST still be refused (routed to reconcile, which completes it).
- Also fix the lineage-119 grade-B nit: rewrite the guard comment (which still says "target ref equals it") to describe the ancestry + expected_target discriminator accurately.
- **Verification:** vitest — a stale-leftover seed (`C_old` an ancestor of an advanced target `T`; lock `expected_target=T`; frozen `F`, worktree HEAD=`F`≠`C_old`; ready) where `reopen_for_edit` PROCEEDS (`finalization-reopened-for-edit`, candidate cleared) — RED against the current ancestry-only guard; AND the lineage-119 genuine-landed-then-advanced reopen-refuses test still passes.

## Non-goals

- No change to reconcile's crash branch (provably safe — the effect proof blocks false completion; its `sourceHead !== candidate` refusal of the leftover shape is correct once reopen becomes the exit).
- No change to the exact-target or genuine advanced-target completion paths (lineage 119).

## Success criteria

R1 vitest RED→GREEN; lineage-119 reopen/crash tests still pass; `dist/` rebuilt; full vitest 0 failed; full commander pytest 0 failed. Nothing committed/pushed without an explicit operator go.
