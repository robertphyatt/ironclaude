# Redeploy Local Main Design

> **Created:** 2026-10-02
> **Status:** Design Complete
> **Loop:** light. The loop changes no code: it runs the existing deploy and restart commands. It touches no DB writes, auth or guard code, concurrency, git/worktree operations, or API/MCP schema. The plan has 1 task, and its only written artifact is the log `docs/plans/2026-10-02-redeploy-local-main-log.md`.

## Requirements

The operator's words: "redeploy (including restarting the commander and codex)", then "Redeploy now, push later". No push.

- **R1.** Redeploy the current local `main` (`d3dae6a`) to both clients.
  - Claude:
    - Hooks go to `~/.claude/ironclaude-hooks` and the Claude plugin cache through `make deploy-hooks`.
    - Skills and the state-manager bundle need no install, because Claude loads them from the repo.
  - Codex:
    - Refresh the plugin cache with `codex plugin add ironclaude@ironclaude`.
    - The cache must match `worker/` (`diff -rq` clean, ignoring `node_modules`, `__pycache__` and `.DS_Store`).
- **R2.** Restart Commander.
  - Stop it with `make -C commander stop`.
  - Relaunch it detached, with the Claude Code environment variables stripped. Use the recorded command.
  - Verify that a new daemon PID and a new Brain PID are running, and that the launch log has no `Traceback` or `ERROR`.
- **R3.** Fully restart Codex (ChatGPT.app) so it loads the refreshed cache.
  - Quit it gracefully first.
  - Kill only exact, verified PIDs if a process survives the quit. Never pattern-kill.
  - Relaunch and verify that the main process and the app-server are running.
- **R4.** No push. No commits. No source edits.

## Design

The redeploy is one task with ordered steps:
1. deploy the hooks;
2. refresh the Codex cache and diff it against `worker/`;
3. restart Commander and verify;
4. quit ChatGPT.app, verify the processes have exited, relaunch, and verify again;
5. write a short log at `docs/plans/2026-10-02-redeploy-local-main-log.md`.

## Error Handling

If any step fails, stop and report it. Do not retry destructively. If a ChatGPT.app process survives the graceful quit, record its PID and command, then kill that exact PID.

## Testing Strategy

No tests required: these are operational commands. Verification is each step's own check: the `diff -rq` result, the new PIDs, and a clean launch log.
