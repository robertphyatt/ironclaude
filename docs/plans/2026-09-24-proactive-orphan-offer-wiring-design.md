# Proactive Orphan-Offer Wiring + Sweep Pruning Design (v1.1.12 corrective)

> **Created:** 2026-09-24
> **Status:** Design Complete
> **Parent:** docs/plans/2026-09-22-proactive-orphan-discussion-design.md (Feature A, shipped in v1.1.12 = c0b42c2, unpushed)

## Summary

A Fable adversarial review of v1.1.12 found two verified Important issues in the shipped "proactive per-orphan discussion" feature:

1. **The proactive offer can't fire.** `brain/rules/workflow.md` tells the Brain to "on your next wake, call `list_surfaced_orphans`", but the Brain is a pure message-driven loop with no autonomous wake, the daemon surfaces orphans **only** to Slack (`main.py:2019`), no daemon→Brain message carries orphan/repo context, and `list_surfaced_orphans` needs a `repository_path` the Brain can't obtain (the daemon's own Slack post is dropped by `only_operator=True`). The operator-*triggered* walkthrough works; only the autonomous offer is dead.
2. **The sweep never prunes vanished `orphan_surface` rows** (the CHANGELOG + code doc claim it does). `deleteOrphanSurface` fires only on the three reap paths; `reapAmbiguousOrphans` builds its guid set from live git state, so a fully-vanished orphan's row (branch + worktree both gone, e.g. removed out-of-band) is never visited → never pruned.

This corrective wires the proactive offer via a daemon→Brain push (approach A, the leanest option a Fable architect confirmed) and makes the sweep prune vanished rows, so both artifact claims become true. It folds into the unpushed v1.1.12 via re-squash, then re-review.

## Architecture

**Finding 2 — daemon→Brain push (approach A).** Reuse the already-wired `self.brain.send_message(...)` input path (40+ sites; direct precedent = the worker-stuck alert at `main.py:5168/5209` pushing a daemon-detected event to the Brain). When `_surface_preserved_orphans` posts to Slack on its existing persisted change-gate, it also sends the Brain one message carrying the surfaced orphans' `repository_path`(s) + ids/categories. The Brain's `workflow.md` proactive step triggers on that message. Rejected: approach B (Brain-poll on heartbeat) needs a new enumeration tool + a new heartbeat message + in-session dedup — three new pieces to A's near-zero, with no wake to hook that A doesn't already provide.

**Finding 1 — sweep prunes vanished rows.** `reapAmbiguousOrphans` already computes the live `guids` set (ambiguous worktrees + managed branches). Add a reconcile step that deletes any `orphan_surface` row for this repo whose `workspace_guid` is not in that set (branch + worktree both gone).

## Components

- **`commander/src/ironclaude/main.py` — `_surface_preserved_orphans` (~:2011-2022).** Inside the existing `if current and changed:` block, after `self.slack.post_message(...)`, add — **guarded by `if self._orphaned_unmerged_count > 0`** (already computed at :2002-2004, so a squash-merged-only set is still listed in Slack but does not proactively nag) — one `self.brain.send_message(<notice>)`. The notice: a distinctive plain tag (`PRESERVED ORPHANS SURFACED`, NOT `[ACTION REQUIRED]` — that jumps the operator queue), N-need-review count, then per `repository_path` the ids with `[category]`, and a pointer to the Resolving-Orphaned-Worktrees rule. One send per change, ids grouped by repo (never one send per repo). No `_executing_tool` gate (this fires once per set; a heartbeat-style gate would drop it permanently). `send_message` only queues; it never interrupts a Brain turn.
- **`commander/src/brain/rules/workflow.md` — "Offer proactively" step (~:279-280).** Retrigger from "on your next wake" to "when the daemon sends a `PRESERVED ORPHANS SURFACED` message": call `list_surfaced_orphans(repository_path)` once per listed repo and offer the walkthrough once (no re-nag). Note: the offer threads under the heartbeat as `[NARRATION]` (no directive ref / `[reply-to:]` needed); phrase it to avoid the operator-wait classifier (`awaiting` / `waiting for` / `your decision` etc.) — e.g. "You have N preserved orphan(s) to review — want to walk through them?". Operator-triggered step is unchanged.
- **`worker/mcp-servers/workspace-manager/src/workspace-service.ts` — `reapAmbiguousOrphans`.** After computing the live `guids` set, delete `orphan_surface` rows for the repo whose `workspace_guid` is not in `guids` (via `deleteOrphanSurface`). Read-only elsewhere is unchanged; this is the sweep, which is allowed to prune.
- **Tests:** `commander/tests/test_worktree_reaper.py` — the `_surface_preserved_orphans` fixtures (`SimpleNamespace(_orphaned_surface_state={}, slack=Mock())`, ~:984/:1007/:1020) gain `brain=Mock()` and `_orphaned_unmerged_count`; assert the Brain send fires once on a changed non-empty unmerged set, NOT on the repeat call, NOT on an empty set, and NOT on a squash-merged-only set. `worker/mcp-servers/workspace-manager/src/__tests__/workspace-service.test.ts` — a test that a surfaced row for a guid whose branch + worktree are removed out-of-band is deleted by the next `reapAmbiguousOrphans` sweep.
- **dist** rebuilt; **CHANGELOG/README/code-comment** claims (pruning + proactive offer) are now accurate — tighten wording only if needed.

## Data Flow

Daemon hourly maintenance → `reapAmbiguousOrphans` (now also prunes vanished `orphan_surface` rows) → `_reap_row_less_orphans` returns `preserved_detail` (each with `repository_path`) → `_surface_preserved_orphans`: on a changed non-empty set with `_orphaned_unmerged_count > 0`, posts to Slack AND `self.brain.send_message(PRESERVED ORPHANS SURFACED …)` → the Brain (message-driven) receives it, per `workflow.md` calls `list_surfaced_orphans(repository_path)` (returns live, non-muted orphans), and offers the walkthrough in a heartbeat-threaded narration → operator confirms per orphan → `resolve_orphan` (unchanged).

## Error Handling

- `self.brain.send_message` returns `False` if the Brain isn't running (queued, non-raising) — no new failure mode; the Slack post still lands and the heartbeat still shows the standing count.
- Dedup across a Brain restart is one-way (a push lost to a restart is not resent until the set changes) — accepted (same as the stuck alert; the heartbeat count + operator-trigger cover it). Do NOT build a resend.
- The sweep prune is inside the existing per-repo `try/except`; a delete failure logs and does not abort the sweep.

## Testing Strategy

- **pytest (`test_worktree_reaper.py`):** RED — add `brain=Mock()` + `_orphaned_unmerged_count` to the surface fixtures and assert `brain.send_message` is called exactly once on a changed unmerged set, and 0 times on repeat / empty / squash-merged-only. (Falsifiable: without the new send, the once-assertion fails; without the unmerged gate, the squash-merged-only assertion fails.)
- **vitest (`workspace-service.test.ts`):** RED — seed a surfaced orphan, remove its branch + worktree out-of-band, run `reapAmbiguousOrphans`, assert its `orphan_surface` row is now **gone** (the pre-fix sweep leaves it). (Complements the existing liveness-filter test, which asserts the row *persists* under `listSurfacedOrphans` — that read tool runs no sweep, so keep them distinct.)
- Full workspace-manager vitest + full Commander pytest (both 0 failed).

## Implementation Notes

- Ordering is safe: `run()` posts the first heartbeat before the first `_run_maintenance` orphan sweep, so `_last_heartbeat_ts` is set before any orphan narration (which would otherwise be dropped).
- **Cut (over-engineering):** no new MCP enum tool, no new daemon tick/heartbeat message, no new "offered" dedup table/field (the persisted `orphan_surface_state` gate is the dedup), no resend-on-restart, no `[ACTION REQUIRED]` prefix.
- **Interaction with the existing liveness-filter test:** the vanished-orphan liveness test (`listSurfacedOrphans` excludes a row whose orphan is gone) asserts the row still exists *within that same call* (no sweep runs). The new pruning happens only when `reapAmbiguousOrphans` runs. Keep both tests; they exercise different entry points.
- **Deploy (post-merge, operator-gated):** workspace-manager `dist/` → both plugin caches; a Commander restart picks up `main.py` + the rewritten `workflow.md` (Brain reloads rules at startup).
- Reuses Feature A's requirements artifact (`docs/plans/2026-09-22-proactive-orphan-discussion-requirements.md`); this makes R2 (proactive offer) actually functional and hardens R1 (no stale rows).
