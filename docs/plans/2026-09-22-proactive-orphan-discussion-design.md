# Proactive Per-Orphan Discussion Design (Feature A)

> **Created:** 2026-09-22
> **Status:** Design Complete
> **Scope mode:** selective (Feature A of two; B = true orphan prevention, separate effort)

## Summary

Today the Commander daemon surfaces preserved orphaned worktrees to Slack as a flat list, and the Brain is explicitly forbidden from reading that surface or acting unprompted — it waits for the operator to reply with ids + an action (`brain/rules/workflow.md:272-284`). The operator expected a natural-language, per-orphan discussion (the Brain walking through each orphan with a recommendation) and never got one, because that flow was never built.

This feature flips the Brain's orphan handling from passive-wait to a guided, **strictly sequential** (one-at-a-time) walkthrough. The Brain can read the surfaced set, proactively **offer** to review it on its next wake, and be **triggered** by the operator at any time. It presents each orphan with a category-driven recommendation and one-line reason, collects an explicit per-orphan decision, and relays it to the unchanged `resolve_orphan` tool. Every mutation still requires the operator's explicit per-orphan consent; no safety rail changes.

Orphans are expected to be rare going forward (Feature B addresses creation), so the deliberate sequential pace is well-matched and no throughput optimization is warranted.

## Architecture

**Approach A — Brain-rule rewrite + one read tool.** Chosen over a Python recommendation engine (B) and a hybrid deterministic-suggested-action tool (C) because it is the smallest blast radius, reuses existing persistence and `resolve_orphan`, and keeps the recommendation as Brain judgment (which explains itself in natural language). The category field already carries the decision signal.

Data source: the authoritative, consent-bound `orphan_surface` table in the **workspace-manager** DB (`worker/mcp-servers/workspace-manager/src/db.ts:268-279`) — the exact rows `resolveOrphan` binds to (`workspace-service.ts:1086`). Columns: `repository_identity, workspace_guid, short_id, tip, category, surfaced_at, muted_tip`. Branch is derived as `ironclaude/<workspace_guid>`. The human "evidence" string is computed at surface time and NOT persisted — out of scope (category is the decision signal); recompute deferred.

## Components

1. **`WorkspaceService.listSurfacedOrphans(repositoryIdentity)`** (`worker/mcp-servers/workspace-manager/src/workspace-service.ts`) — read-only. Reads `orphan_surface` rows for the repo, **excludes currently-muted (kept) ids** where `tip == muted_tip`, derives `branch = ironclaude/<workspace_guid>`. Returns `[{ id, category, tip, branch, workspace_guid }]`. No writes, no new table.
2. **`list-surfaced-orphans` CLI verb** (`worker/mcp-servers/workspace-manager/src/cli.ts`) wrapping the service method (snake_case payload, mirroring `reap-orphans`).
3. **`WorkspaceClient.list_surfaced_orphans(repository_path, **transport)`** (`commander/src/ironclaude/workspace_client.py`).
4. **Orchestrator tool `list_surfaced_orphans(repository_path)`** (`commander/src/ironclaude/orchestrator_mcp.py`) — Brain-facing read tool; resolves `repository_identity` from `repository_path`; empty-safe.
5. **`brain/rules/workflow.md` "Resolving Orphaned Worktrees" rewrite** (:272-284) — replace the passive-wait rule with the proactive-offer + operator-trigger + sequential-walkthrough flow (below). `resolve_orphan` usage and all its rails carry over verbatim.

## Data Flow

- **Proactive (opportunistic on next wake):** On its normal wake/ping cycle, the Brain calls `list_surfaced_orphans`. If the set is non-empty AND it has not already offered this set, it offers **once** ("You have N preserved orphans — want to walk through them?"). **No re-nag:** if the operator does not engage, it waits for an explicit trigger rather than re-offering every wake. Kept ids are muted and drop out of the read, so the set shrinks naturally and dedup is largely implicit.
- **Operator trigger:** The operator says e.g. "review the orphans" / "let's do the orphans" at any time → same read → walkthrough.
- **Walkthrough (strictly sequential):** For each orphan in turn, the Brain presents `branch`, `category`, `tip`, its recommendation, and a one-line reason; waits for the operator's explicit decision (reap / keep / merge-then-reap, with optional `integration_target`); calls `resolve_orphan` for that single id; relays the per-id outcome; then advances to the next orphan. One orphan per exchange.

**Recommendation policy (category-driven, Brain judgment):**
- `squash-merged` / `merged-on-origin` → **reap** (work already in the default branch; safe).
- `genuinely-unmerged` → **keep** by default; offer **merge-then-reap** if the operator wants the work landed. Never auto-reap.
- `dirty` → **keep** (preserve uncommitted work); reap only via the existing explicit `dirty`-category consent path.

## Error Handling

- `list_surfaced_orphans` on an empty/unknown repo returns `[]` — the Brain simply reports "no orphans to review" (and makes no proactive offer).
- All resolution outcomes remain those `resolve_orphan` already returns (`reaped`, `kept`, `refused-changed`, `not-surfaced`, `skipped-live`, `reaped-worktree-only`, `merged-then-reaped`, `conflict`, `needs-manual-merge`, `refused-dirty`, `target-moved`, `error`) — the walkthrough relays them per-id, unchanged.
- Tip drift between the Brain's read and the operator's decision is caught by `resolve_orphan`'s existing tip-binding (`refused-changed`); the Brain re-reads and re-presents that orphan rather than forcing.
- The read tool never mutates, so a failed/duplicated read is harmless.

## Testing Strategy

- **workspace-manager (vitest):** `listSurfacedOrphans` returns surfaced rows for the repo; **excludes muted** (`tip == muted_tip`) ids; derives `branch = ironclaude/<guid>`; returns `[]` when the table is empty for the repo.
- **CLI (vitest):** `list-surfaced-orphans` verb dispatches to the service and serializes the snake_case payload.
- **orchestrator (pytest):** `list_surfaced_orphans` tool maps `repository_path` → rows, is empty-safe, and threads transport correctly (mirroring `reap-orphans`/`resolve_orphan` tests).
- **Brain rule:** the `workflow.md` rewrite is Brain *guidance* (prose); there is no behavioral harness for rule text, so it is validated by review — consistent with how the existing orphan rule is maintained. If a rule-content lint/assertion test exists for workflow.md, update it.

## Implementation Notes

- **Deploy surface:** workspace-manager change → rebuild `dist/` + copy into both plugin caches; `workflow.md` and orchestrator changes are Commander-daemon-side → a Commander restart picks them up. (The daemon runs from the working tree.)
- No new DB tables, migrations, or writes; no change to `resolve_orphan`, `reapAmbiguousOrphans`, or the daemon's own Slack surfacing.
- Keep the `resolve_orphan` rails verbatim in the rewritten rule: never infer/guess an id; per-orphan explicit consent; dirty discard only on named `dirty` category; live-worker `protected_paths`; `merge-then-reap` targets `refs/remotes/origin/HEAD` (fallback `main`) unless the operator names an `integration_target`.
- Reference points: current rule `brain/rules/workflow.md:272-284`; surface path `main.py:1992-2022`; heartbeat aggregate line `notifications.py:138`; per-orphan surface format `notifications.py:212-232`; `resolve_orphan` `orchestrator_mcp.py:4068`; `orphan_surface` schema `db.ts:268-279`; reaper `workspace-service.ts:985`.

## Out of Scope (explicit)

- Feature B / true orphan prevention (separate effort, next).
- The human "evidence" string (recompute deferred; category is the decision signal).
- Bulk/batch resolution (strict sequential chosen).
- Any change to `resolve_orphan`, the reaper, DB schema, or the daemon's own surfacing/heartbeat.
- Daemon→Brain push signalling (proactive offer is opportunistic-on-wake, no new wiring).
