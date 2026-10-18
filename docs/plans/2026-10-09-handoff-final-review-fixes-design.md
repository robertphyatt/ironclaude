# Handoff Final-Review Fixes (L-H1b) Design

> **Created:** 2026-10-09
> **Status:** Design Complete
> **Parent requirements:** `docs/plans/2026-10-06-handoff-recovery-fixes-requirements.md` (H6a, H7, H7a, H7b)
> **Parent plan:** `docs/plans/2026-10-09-handoff-recovery-fixes.md` (all 17 tasks complete, staged)

Loop: full — process-lifecycle proofs and worker recovery paths in the Commander handoff controller.

## Requirements

Operator decisions: "Fix M1+M2 (Rec.)" and design "Yes (Rec.)" (2026-10-09). Both requirements make already-approved H6a/H7 behavior reachable in production; no new operator decisions.

- **R1 (M1).** Once a target session may occupy the worktree — the handoff phase is `owner_transferred`, `runtime_registered` or `continuation_dispatched`, or the evidence records `bootstrap_stopped`, `resume_intent` or `target_session` — a recorded `bootstrap_process` counts as gone only when every recorded process is proved absent by PID plus start time (`NativeProcessAdapter.proved_absent`). A launch-intent-only bootstrap counts as gone when its own tmux session is absent. Worktree quiescence is not required in these states (the target legitimately runs there). Before a target can occupy the worktree, the existing `verify_stopped` proof (including quiescence) is unchanged.
- **R2 (M1).** `_stop_bootstrap` does not try to stop a bootstrap that `_bootstrap_proven_gone` already proves gone; it records the proof and succeeds.
- **R3 (M2).** At `runtime_registered` with pause reason `continuation_dispatch_unproved`, when tmux positively reports the recorded `target_session` absent, recovery routes to the preserve path (`_preserve_after_stop` → `_release_to_target`: adopt target runtime, `finish_handoff_after_target`, release `released_to_target` with preserve) instead of replaying the dispatch. A tmux error keeps the pause and retries. A present session keeps the existing replay.
- **R4.** Tests use the real `NativeProcessAdapter` with fake psutil and tmux and a recorded `bootstrap_process`: post-transfer preserve and `abort_handoff` succeed with a gone bootstrap and a live target process in the worktree; a still-live bootstrap PID refuses; R3 has positive (absent → released, preserve, no second `send_keys`), present (replay) and error (held) cases.

## Architecture

`worker_handoff.py` only (plus tests): `_bootstrap_proven_gone` gains a target-may-occupy branch; `_stop_bootstrap` short-circuits on a proven-gone bootstrap; `retry_paused_preflight` (or `_post_transfer_cleanup`) routes the dead-target dispatch case to `_preserve_after_stop`. No schema, workspace-manager or MCP changes.

## Error Handling

Every new branch fails closed: AccessDenied or an unreadable identity is unproved (holds); a tmux error holds; only positive proofs of absence release.

## Testing Strategy

TDD per R4, then the affected suites and the full Commander pytest, then a final Fable review of the whole staged handoff before commit.
