# Reaper Robustness Fixes — Requirements (operator-approved)

> **Created:** 2026-08-24
> **Status:** Approved
> **Scope mode:** hold. Two robustness fixes surfaced by the v1.1.8 end review; fold into un-committed v1.1.8.

## Problem

Two Important robustness gaps in the v1.1.8 reaper code (`commander/src/ironclaude/main.py`):

- **I-1:** `_has_push_pending` catches `(ValueError, TypeError)` but not the `AttributeError` raised by
  `.get("phase")` on a valid-JSON **non-object** (`json.loads("null")→None`, `"[1]"→list`, `"3"→int`).
  The reap-site call at `main.py:498` is outside any per-row `try`, so the `AttributeError` aborts the
  whole candidate loop (caught only at the maintenance wrapper), and since `_is_protected`'s outer
  `except` protects the row, the abort recurs every maintenance cycle. Trigger is a corrupt/hand-edited
  disposition; the function's `except` exists precisely to defend malformed data.
- **I-2:** `self._push_pending_alerted` (`main.py:1281`) is never pruned. Because `recycleFinalized`
  reuses the same `workspace_guid` (a designed, tested flow), a second stuck-push episode on a reused
  guid is protected + counted but **never warned** (the guid stays in the set forever). Net: no daemon
  signal for the new stuck obligation.

## Requirements

R1. `_has_push_pending` MUST return `False` (not raise) for a valid-JSON non-object disposition — add an
    `isinstance(parsed, dict)` guard. Behavior for all existing inputs is unchanged.

R2. The reaper MUST re-arm its one-time WARNING: prune `push_pending_alerted` to only the guids that are
    push-pending in the current sweep, so a resolved-then-reused guid's next stuck episode warns again.
    Implement by collecting this sweep's push-pending guids and, after the candidate loop,
    `push_pending_alerted.intersection_update(seen)`.

R3. Falsifiable tests (`commander/tests/test_worktree_reaper.py`):
    - `_has_push_pending` returns False for `"null"`, `"[1]"`, `"3"` (valid JSON, non-object) — pre-fix it
      raises AttributeError; and a reaper sweep over such a row does NOT crash.
    - A second push-pending episode on the same guid (after the first was resolved/pruned) re-emits the
      WARNING — pre-fix the second episode is silent.
    Full `test_worktree_reaper.py` stays green.

## Non-goals
- No version bump (v1.1.8 already bumped, un-committed; these fold in). No CHANGELOG change required (the
  v1.1.8 reaper entry already describes the feature; these are internal robustness refinements).
- No change to the tombstone guard, Commander I-1, or the TS side.
