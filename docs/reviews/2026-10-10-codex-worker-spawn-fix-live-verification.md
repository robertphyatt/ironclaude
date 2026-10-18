# Codex worker spawn fix: live verification (2026-10-10)

Plan: `docs/plans/2026-10-10-codex-worker-spawn-fix.plan.json`, Task 10 (R9). Script: `docs/reviews/2026-10-10-codex-worker-spawn-fix-live_spawn.py`. Codex: `codex-cli 0.162.0-alpha.17.2`.

## What ran, and the gaps

The script constructs the real `OrchestratorTools` from the working-tree Commander source and calls `spawn_worker("codexlive1", "claude-sonnet", <scratch repo>, <objective>)`. It uses:
- `config/ironclaude.json`, which routes workers to Codex;
- a scratch Commander DB and log dir under the session scratchpad;
- a fresh tmux server under `TMUX_TMPDIR=/private/tmp/ironclaude-codex-spawn-fix/tmux`;
- the real `TmuxManager`, `WorkerRegistry` and `WorkspaceClient`.

Gaps:
- The graders are stubbed to approve.
- The worker runs the installed plugin, so R6 (`state-activator.sh` own-marker claim) is not exercised until the plugin is redeployed.
- Professional-mode state goes to `~/.claude/ironclaude.db`, as in production. The live Commander DB was never opened.

The scratch repo is a depth-1 local clone of this repo at `d940758`. The plan's empty-commit init was blocked by the professional-mode guard (no git commit during execution). The operator approved creating the commit another way, but the guard also blocked `commit-tree`. A clone gives the workspace manager the HEAD it needs without creating any commit.

## Stripped environment

The launcher removed these names with `env -u` (names only): `TMUX`, `TMUX_PANE`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_CODE_BRIDGE_SESSION_ID`, `CLAUDE_CODE_EXECPATH`, `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_SESSION_ATTENDED`, `CLAUDE_PID`, `CLAUDE_EFFORT`.

## run.log (verbatim)

```
{"stage": "env", "removed": [], "tmux_tmpdir": "/private/tmp/ironclaude-codex-spawn-fix/tmux"}
{"stage": "import", "ironclaude_file": "/Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/__init__.py"}
Worker 'codexlive1' not ready after timeout on local but session alive — proceeding.
Last output:
  permissions: YOLO modeGPT-5.6-Terra default·/private/tmp/claude-502/-Users-roberthyatt-Code-ironc…high · /private/mp/claude-502/-User-oberthyatt-Code-ironclau
{"stage": "spawn", "seconds": 38.7, "result": "Worker 'codexlive1' spawned (claude-sonnet) in /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo. Model recommendation: claude-sonnet"}
{"stage": "worker", "worker": {"id": "codexlive1", "type": "claude-sonnet", "machine": null, "repo": "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo", "description": "Live spawn check: reply with the single word ok and stop. Do not edit files.", "tmux_session": "ic-codexlive1", "status": "running", "task_id": null, "spawned_at": "2026-10-10 21:05:12", "finished_at": null, "client": "codex", "model": "gpt-5.6-terra", "native_session_id": "01a127a1-c4a7-70d1-9f8d-06efe58b1507", "workspace_guid": "ec000a93-e6c0-4f11-a691-86aac9a86585", "workspace_repository_identity": "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo/.git", "workspace_path": "/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/codex-spawn-live/repo/.ironclaude/worktrees/ec000a93-e6c0-4f11-a691-86aac9a86585", "workspace_branch": "ironclaude/ec000a93-e6c0-4f11-a691-86aac9a86585", "workspace_base_commit": "d9407582c96cd17f5a17451fdf4d4329e409fc1c", "workspace_integration_target": "main"}}
{"stage": "pm", "native_session_id": "01a127a1-c4a7-70d1-9f8d-06efe58b1507", "row": ["on", "idle"], "is_operator_session": false}
{"stage": "pane", "objective_seen": true, "marker_seen": true}
{"stage": "kill", "session": "ic-codexlive1", "killed": true}
{"stage": "release", "lifecycle": "abandoned"}
live exit=0
```

## Database row and rollout file

- `sqlite3 -readonly ~/.claude/ironclaude.db … WHERE terminal_session='01a127a1-c4a7-70d1-9f8d-06efe58b1507'` gives `01a127a1-c4a7-70d1-9f8d-06efe58b1507|on|idle`.
- The id is not this Claude session's (`7c7f63a0-e272-4e34-a05b-3d6ebc128c94`).
- `find ~/.codex/sessions -name "*01a127a1-…*" -newer start.ref` gives exactly one file: `~/.codex/sessions/2026/10/10/rollout-2026-10-10T15-04-35-01a127a1-c4a7-70d1-9f8d-06efe58b1507.jsonl`. The bound id is therefore the worker's own Codex thread, created during this run.

## Pane facts

- The profile marker `IC_LOSSLESS_AI_MESSAGES_ACTIVE` and the objective text are both on the worker's screen (`pane-final.txt`).
- The pane log shows the skill picker (`enter insert · esc close`) after the invocation was typed, then the submitted turn (`Working … Running hooks`). This confirms the profile turn was submitted before activation.

## Readiness timeout: pre-existing defect found

`_wait_for_ready` returned False after 30 s ("not ready after timeout … proceeding"). The spawn continued because the session was alive.

- **Cause:** the raw pane log contains `>_ OpenAI Codex (v0.162.0-alpha.17.2)`, but `tmux_manager._strip_ansi` removes it.
  - Its OSC branch, `\x1b\].*?\x07` (`tmux_manager.py:20`), accepts only the BEL terminator.
  - Codex starts with OSC queries terminated by ST (`\x1b]10;?\x1b\\`), so the lazy match runs to a later BEL and swallows the banner.
- **Effect:** every Codex spawn waits the full 30 s readiness timeout, and readiness never sees the banner or anything else in the swallowed span.
- **Scope:** this predates this change and is outside this plan's files. It is recorded for a follow-up fix.

## Cleanup and leftovers

- `tmux has-session -t ic-codexlive1` exits 1 (`no server running`), and `tmux list-sessions` exits 1, so no sessions remain.
- `pgrep -lf codex-spawn-live` exits 1, so no processes remain.
- `~/.codex/config.toml:1857` has a new trust row, `[projects."…/scratchpad/codex-spawn-live/repo"]`.
- `~/.claude.json` has 1 entry mentioning `codex-spawn-live`, the `ensure_worker_trusted` entry.
- The scratch repo, its abandoned worktree, the scratch DB, logs and `pane-final.txt` remain under the session scratchpad and are not committed.

## Verdict per R9 item

| Check | Result |
|---|---|
| Professional mode `on` for the worker's own thread id, not another session's | PASS |
| Profile marker detected | PASS |
| Objective delivered | PASS |
| Cleanup by exact session name | PASS |
| Workspace released (`abandoned`) | PASS |
| Readiness detected the banner | FAIL (pre-existing `_strip_ansi` OSC defect; non-blocking, follow-up) |
