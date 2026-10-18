# Codex Worker Spawn Reproduction Requirements

> **Created:** 2026-10-06
> **Status:** Operator-approved

## Operator directives (verbatim)

- "please have an opus sub agent look at what in the hell is going on with the ironclaude commander? I switched it to codex a few days ago do to usage issues and it should have been working but hasn't"
- Sequencing: "Spawn fix first (Rec.)". The handoff fix loop (L-H1) is parked; its design is saved at `docs/plans/2026-10-06-handoff-recovery-fixes-design.md`.
- Investigation: "Run repro loop (Rec.)".

## What we already know

Verified from logs and source:

- **Dispatch never submits.** Codex worker dispatch types `$ironclaude:write-lossless-ai-messages` and presses Enter once (`tmux_manager.py:363-375`, `communication_profiles.py:81`). Codex then shows its skill picker with the footer `enter insert · esc close`, and no turn is submitted.
- **Activation is slow or never finishes.** Professional-mode (PM) activation polls for `~/.claude/ironclaude-session-<pid>.id` across the pane's process tree for up to 300 s (`orchestrator_mcp.py:2470-2494`). For spawn 1 the id file appeared after about 4 min; for spawn 3 it never appeared. No Codex rollout file was created for either worker.
- **The running daemon launches workers without `--no-daemon`.** Its command is `exec codex --model <m> --dangerously-bypass-approvals-and-sandbox`, prefixed with `export IC_ROLE=worker; export IC_WORKER_ID=<id>; export ENABLE_STOP_REVIEW=0;`. The `--no-daemon` flag exists only in the staged source.

## Requirements

- **R1. Reproduce the launch.** Launch scratch Codex sessions in tmux with the exact command the running daemon uses:
  - variant A: no `--no-daemon`;
  - variant B: `--no-daemon`.

  Both variants run in a scratch git repo under the session scratchpad. Use the model the Commander's provider config selects for Codex workers.
- **R2. Measure what happens at startup.** For each variant, record:
  - the time from launch until the id file appears;
  - which PID wrote the id file, and whether that PID is a descendant of the pane process;
  - any startup modal or notice (update prompt, trust or hooks review) and whether it blocks;
  - pane snapshots at fixed intervals;
  - whether the id file appears only after a turn is submitted. The probe rescans after each dispatch Enter and after one plain prompt.
- **R3. Test dispatch.** In a ready session, send `$ironclaude:write-lossless-ai-messages` and Enter, then capture the pane. Send a second Enter, then capture again. Record whether a turn starts and whether `IC_LOSSLESS_AI_MESSAGES_ACTIVE` appears. Then submit one plain prompt (`Reply with the single word ok. Use no tools.`), capture the pane after 45 s, and rescan for id files.
- **R4. Stay safe.**
  - Touch no Commander process, Commander DB or `roleplaying-agents` state.
  - Never pattern-kill. Remove sessions only with `tmux kill-session -t <exact name>`, then confirm no process created by the probe remains, by exact PID.
  - Record any state the scratch sessions create:
    - session rows and id files;
    - `[projects.*]` and `[hooks.state.*]` rows added to `~/.codex/config.toml`;
    - any codex process outside the pane tree that outlives `kill-session`. Such processes are listed, not killed.
- **R5. Write a findings note** at `docs/reviews/2026-10-06-codex-spawn-repro-findings.md`. It records:
  - the measured timings;
  - the root cause of the slow or missing activation, verified or marked unresolved;
  - the dispatch submission behaviour;
  - inputs for the spawn-fix design.

## Out of scope

- Any product code change. The fix is a separate loop.
- Cleaning up the orphaned reservations in `roleplaying-agents`. That belongs to the fix loop, done through Commander tools.
