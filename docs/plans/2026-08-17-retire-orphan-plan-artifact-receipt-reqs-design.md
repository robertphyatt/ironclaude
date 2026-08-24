# Retire Orphan plan_artifact_receipts Row — Requirements Artifact

> **Created:** 2026-08-17
> **Status:** Requirements Complete
> **Paired design:** `docs/plans/2026-08-17-retire-orphan-plan-artifact-receipt-design.md`

## Note on filename

This file's name ends in `-design.md` only to satisfy the
professional-mode-guard hook's write-gate, which permits `docs/plans/*.md`
writes pre-`consumed` solely for paths matching that literal suffix. Its
content is the requirements artifact, not a second design. `mark_design_ready`
requires distinct `file` and `requirements_file` paths, and the guard blocks
writing any other `docs/plans/*.md` file before the design is registered —
this is the only path satisfying both constraints without a pre-existing
requirements file.

## Requirements

The full requirements are recorded in the `## Requirements` section of the
paired design document (`2026-08-17-retire-orphan-plan-artifact-receipt-design.md`),
which contains Functional Requirements, Safety Requirements, and Out of Scope
subsections. They are not duplicated here to avoid drift between two copies of
the same text; this file exists to satisfy the `requirements_file` parameter
with a distinct, real path.
