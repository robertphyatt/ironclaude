# Codex Worker Spawn Reproduction Design

> **Created:** 2026-10-06
> **Status:** Design Complete
> **Requirements:** `docs/plans/2026-10-06-codex-spawn-repro-requirements.md`
> **Loop:** light. Writes are limited to a scratchpad probe script and its logs, plus one findings note. No product changes.

## Summary

Codex worker spawns fail in two places:
- **PM activation** waits minutes for the SessionStart id file, or waits forever.
- **Profile dispatch** never submits, because the `$skill` invocation opens Codex's skill picker.

The logs show both symptoms but cannot explain the slow activation. This loop reproduces the spawn sequence in scratch tmux sessions, using the running daemon's exact command, and measures what happens.

## Approach

All work happens in the scratchpad: one probe script, run once per variant, with each run writing its own log.

1. **Prepare a scratch git repo.** Create `<scratchpad>/codex-repro/repo` with `git init` and one commit.
2. **Launch.** Run `tmux new-session -d -s icrepro-<variant> -c <repo> "<prefix>; exec codex --model <m> [--no-daemon] --dangerously-bypass-approvals-and-sandbox"`.
   - `<prefix>` is the daemon's prefix: `export IC_ROLE=worker; export IC_WORKER_ID=icrepro-<variant>; export ENABLE_STOP_REVIEW=0`.
   - `pipe-pane` writes to `<scratchpad>/codex-repro/icrepro-<variant>.pane.log`.
3. **Observe.** Poll every 5 s for up to 360 s. At each poll, record:
   - the pane PID and its descendant PIDs (via `pgrep -P`, walked recursively);
   - any `~/.claude/ironclaude-session-*.id` file newer than the launch, with its PID suffix and whether that PID is a descendant;
   - a `capture-pane -p` snapshot at 10, 30, 60, 120, 240 and 360 s, and whenever the screen changes in a way that suggests a modal.
4. **Handle modals.** If a modal blocks startup (update notice, trust prompt, hooks review), record it, then dismiss it with one keypress the way a human would, and keep timing.
5. **Test dispatch** once the session is ready, meaning the id file exists or the poll window ended. Type the `$ironclaude:write-lossless-ai-messages` invocation and send Enter, then capture. Send a second Enter, then capture after 20 s. Search the pane log for turn-start text and for `IC_LOSSLESS_AI_MESSAGES_ACTIVE`.
6. **Clean up.**
   - Record the descendant PIDs, then run `tmux kill-session -t icrepro-<variant>`.
   - Verify each recorded PID is gone. A survivor is reported, and killed only by its exact PID.
   - List the id files and IronClaude session rows the probe created.

   Variants A and B run one after the other, not concurrently.

The model comes from the Commander provider config for the Codex worker tier, read during execution.

## Error handling

- A step that fails is recorded with its output, and the run continues to cleanup.
- An evidence command never uses `2>/dev/null`.
- Foreground `sleep` is blocked, so the script runs as one background Bash invocation and the orchestrator waits for it to finish.

## Testing strategy

No tests are needed: this is an investigation. The evidence is the per-variant logs and pane snapshots. The findings note marks each conclusion as verified (measured) or unresolved.

## Final gate

None. The output is an investigation note, and the spawn fix loop that follows gets its own review and final gate.
