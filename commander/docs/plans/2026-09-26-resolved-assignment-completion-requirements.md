# Seam Completes a Dead Worker Whose Assignment Is Already Resolved — Requirements

> **Created:** 2026-09-26
> **Status:** Operator-approved
> **Design:** docs/plans/2026-09-26-resolved-assignment-completion-design.md

## Operator directives (this session)

- Deploying v1.1.12 revealed the root cause for worker r2: `abandon failed: … Only
  unresolved managed worktrees can be abandoned`. The operator said to reactivate PM
  and "let's do this", meaning the deferred "nothing left to rescue" seam completion.
- The operator asked why this wasn't in v1.1.12. It had been explicitly deferred until
  the error became visible. v1.1.12 is pushed and tagged, so this change goes under
  `[Unreleased]`, toward v1.1.13.
- The operator chose approach A: a lifecycle-aware status probe plus a router `resolved`
  branch. This was chosen over matching abandon's error text.
- Out of scope: stale repo paths, the shared alert gate, "consecutive" semantics.

## Acceptance criteria

1. The `status` probe returns `state: 'resolved'` for `cleaned` and `abandoned`
   assignments. `integrated` and `ready_for_integration` are unchanged, and every other
   lifecycle still returns `not-ready`.
2. When the terminal seam gets `resolved`, it completes the worker only if the tmux
   session is confirmed dead, via `_complete_worker_if_session_dead`. It never calls
   finalize or abandon.
3. A live session is never completed. The daemon never calls `update_worker_status`.
4. `_abandon_rescue_worker` still refuses every state other than `not-ready`.
5. The CHANGELOG has an `[Unreleased]` entry. dist is rebuilt. The full vitest and
   commander pytest suites pass with 0 failed.
