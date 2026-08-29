# Reaper Robustness Fixes Design

> **Created:** 2026-08-24
> **Status:** Design Complete
> **Requirements:** docs/plans/2026-08-24-reaper-robustness-requirements.md

## Summary

Two Important robustness gaps in the v1.1.8 reaper code (`commander/src/ironclaude/main.py`), surfaced
by the v1.1.8 blind end review, fold into un-committed v1.1.8. Both are small, line-verified defects.

## Architecture

Localized hardening of two existing functions — no new components, no behavior change on the happy path.

## Components / Data Flow — the defects and fixes

**I-1 — `_has_push_pending` AttributeError on valid-JSON non-object.**
`json.loads("null")→None`, `"[1]"→list`, `"3"→int` all parse, then `.get("phase")` raises
`AttributeError`, which `except (ValueError, TypeError)` does not catch. The reap-site call at
`main.py:498` is outside any per-row `try`, so the error aborts the whole candidate loop (caught only at
the maintenance wrapper `:1653`); `_is_protected`'s outer `except` keeps the row protected, so the abort
recurs every cycle. **Fix:** in `_has_push_pending`, after `parsed = json.loads(...)`, return
`isinstance(parsed, dict) and parsed.get("phase") in (...)`. A non-dict now returns `False`, no throw.

**I-2 — one-time WARNING never re-arms.**
`self._push_pending_alerted` (`main.py:1281`) is only ever added to, never pruned. `recycleFinalized`
reuses the same `workspace_guid` (tested flow), so a second stuck-push episode on a reused guid is
protected + counted but never warned. **Fix:** collect this sweep's push-pending guids in a local
`seen_push_pending` set; after the candidate loop, `push_pending_alerted.intersection_update(seen_push_pending)`
— drop guids no longer push-pending (re-arm), mirroring `_message_aging_alerted`'s re-arm.

## Error Handling
- I-1: fail-closed — a malformed disposition is treated as "no obligation" (`False`), the safe reading
  for a teardown guard (a corrupt disposition should not indefinitely block reaping via a crash-loop).
- I-2: pruning is idempotent; a consistently-stuck guid stays in `seen` every sweep (never re-warned);
  only a resolved guid drops out.

## Testing Strategy (`commander/tests/test_worktree_reaper.py`)
- `_has_push_pending` returns `False` for `"null"`, `"[1]"`, `"3"` (valid JSON, non-object) — pre-fix
  raises `AttributeError`; and a reaper sweep over such a row completes without crashing (counts returned).
- A second push-pending episode on the same guid after the first was pruned re-emits the WARNING — model
  on the existing `test_push_pending_row_is_preserved_and_warned_exactly_once`: two sweeps with the
  disposition nulled (resolution) between them + the same reused guid re-stuck; pre-fix the second is silent.
- Full `test_worktree_reaper.py` stays green.

## Implementation Notes
- No version bump (v1.1.8 already bumped, un-committed) and no CHANGELOG change (the v1.1.8 reaper entry
  already covers the feature; these are internal refinements).
- Requirements mirror: see the requirements file for R1 (isinstance guard), R2 (re-arm), R3 (tests).
