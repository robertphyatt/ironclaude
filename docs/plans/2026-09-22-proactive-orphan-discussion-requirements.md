# Proactive Per-Orphan Discussion — Requirements (Feature A)

> **Created:** 2026-09-22
> **Status:** Operator-approved (derived from the approved design + brainstorm decisions)
> **Design:** docs/plans/2026-09-22-proactive-orphan-discussion-design.md

Derived, operator-reviewed contract for Feature A. The original authority is the operator's directives and the brainstorm dialogue (four AskUserQuestion decisions: initiation = proactive-offer + operator-trigger; granularity = strictly sequential; architecture = Approach A rule + read tool; proactive trigger = opportunistic-on-wake). Where this document and that dialogue conflict, the dialogue wins.

## Functional requirements

- **R1 — Read tool.** A new read-only tool `list_surfaced_orphans(repository_path)` returns the currently-surfaced orphans for the repo as `[{ id, category, tip, branch, workspace_guid }]`, sourced from the workspace-manager `orphan_surface` table. It **excludes currently-muted (kept) ids** — rows whose `muted_tip` equals the current `tip`. `branch` is derived as `ironclaude/<workspace_guid>`. It is strictly read-only: no writes, no new tables, no migrations. Empty/unknown repo → empty list.

- **R2 — Initiation (both).** The Brain both proactively offers AND honors an operator trigger. Proactive is **opportunistic on the Brain's next wake**: on wake the Brain calls `list_surfaced_orphans`; if the set is non-empty and it has not already offered this set, it offers **once** to walk through them. **No re-nag** — if the operator does not engage, the Brain waits for an explicit trigger rather than re-offering each wake. No new daemon→Brain signal is added.

- **R3 — Strictly sequential walkthrough.** One orphan per exchange. For each orphan the Brain presents `branch`, `category`, `tip`, its recommendation, and a one-line reason; waits for the operator's explicit decision; calls `resolve_orphan` for that single id; relays the per-id outcome; then advances to the next orphan.

- **R4 — Recommendation policy (category-driven, Brain judgment).**
  - `squash-merged` / `merged-on-origin` → recommend **reap** (work already on the default branch).
  - `genuinely-unmerged` → recommend **keep** by default; offer **merge-then-reap** if the operator wants the work landed. Never auto-reap.
  - `dirty` → recommend **keep** (preserve uncommitted work); reap only via the existing explicit dirty-category consent.

- **R5 — Safety rails preserved verbatim.** All mutations still go through `resolve_orphan` with its existing behavior: per-orphan explicit operator consent; tip-bound ids; a `dirty` discard requires the operator to name the `dirty` category; live-worker `protected_paths` are honored; ids are never inferred or guessed; `merge-then-reap` targets `refs/remotes/origin/HEAD` (fallback `main`) unless the operator names an `integration_target`. The walkthrough only reads, presents, and relays the operator's explicit per-orphan decision.

- **R6 — Rule rewrite.** The `brain/rules/workflow.md` "Resolving Orphaned Worktrees" section is rewritten from passive-wait to the R2/R3/R4 flow, preserving every `resolve_orphan` rail from R5 verbatim.

## Non-functional / boundary requirements

- **R7 — No change** to `resolve_orphan` / `resolveOrphan`, `reapAmbiguousOrphans`, the daemon's own Slack surfacing (`_surface_preserved_orphans` / `format_orphaned_orphans`), the heartbeat aggregate line, the `orphan_surface` schema, or any migration.
- **R8 — Tests.** Unit coverage for `listSurfacedOrphans` (returns surfaced rows; excludes muted; derives branch; empty-safe), the CLI verb dispatch, and the orchestrator `list_surfaced_orphans` tool (repo→rows, empty-safe, transport-correct). The `workflow.md` rewrite is Brain guidance prose and is validated by review, not a behavioral unit test.

## Out of scope

- Feature B / true orphan prevention (separate effort).
- The human "evidence" string (computed at surface time, not persisted; recompute deferred — `category` is the decision signal).
- Bulk/batch resolution (strictly sequential chosen).
- Any new daemon→Brain push signalling (proactive offer is opportunistic-on-wake).
