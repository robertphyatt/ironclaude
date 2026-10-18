# Codex Worker Spawn Reproduction v2 Design

> **Created:** 2026-10-07
> **Status:** Design Complete
> **Loop:** light. Writes go only to a scratchpad probe and its logs, plus one findings note. No product code.

## Requirements

Operator directive: "Re-run probe (Rec.)". This follows the v1 findings in `docs/reviews/2026-10-06-codex-spawn-repro-findings.md`. The v1 probe stalled on Codex 0.160.0 modals that the daemon-mirroring matcher did not recognise.

- **Q1. Get past every startup modal and reach a real Codex prompt.** Handle each modal by its exact text, as captured in the v1 snapshots:

  | Modal | Snapshot text | Response | Note |
  |---|---|---|---|
  | Trust | `Folder access` / `Trust this folder?` / `› 1. Trust and continue` | `1` | In variant A, option 2 is "Back to Agent Command Center"; in B it is "Quit". |
  | Hooks | `Hooks need review` / `2. Trust all and continue` | `2` | |
  | Resume picker | `Working directory · resume` / `2. Use current directory (...)` | `2` | Record that it appeared, because production panes never showed it. |
  | Unknown | anything else | none | Snapshot it and record it. |

  Readiness requires both of the following, checked for up to 90 s:
  - the `>_ OpenAI Codex` banner is on screen;
  - the `› Ask Codex to do anything` input line is visible, and no modal marker is on the current screen. Modal markers: `Folder access`, `Hooks need review`, `Working directory · resume`.
- **Q2. Time the id file.** After readiness, poll `~/.claude/ironclaude-session-*.id` for 300 s, which is the daemon's own window. Record:
  - `id_at` and `id_pid` for in-tree writers, meaning PIDs in the pane's process tree;
  - `any_at` and `any_pid` for the first writer of any kind, with its `ps` line.
- **Q3. Test dispatch at a real prompt.**
  1. Send `$ironclaude:write-lossless-ai-messages`, wait 0.2 s, then Enter.
  2. Snapshot after 10 s. Record whether the skill picker (`enter insert`) is showing.
  3. Send a second Enter and snapshot after 30 s.
  4. Count `IC_LOSSLESS_AI_MESSAGES_ACTIVE` in both the snapshot and `pane.log`.
  5. Send one plain prompt and snapshot after 45 s.

  Rescan for id files after each of steps 2, 3 and 5.
- **Q4. Compare launch modes.** Run variant B (`--no-daemon`, the Commander's current local worker command, `orchestrator_mcp.py:564-571` via `:641-642`) and then variant A (daemon mode, as on the remote-machine path at `:769`), one after the other. Each uses a fresh scratch repo, `repo-v2-<V>`, so that trust state from one run cannot carry into the other.
- **Q1a. Modals can appear after readiness.** The modal handler runs on every check in every phase, not only during readiness. If the same modal is still up on the next check after its digit, the probe sends Enter once and logs it.
- **Q3a. Snapshot the picker before Enter.** In Q3, take a snapshot after typing the skill text and before Enter 1 (the picker closes on Enter). After Enter 1, record whether the composer still holds the unsubmitted `$ironclaude:write-lossless-ai-messages` mention.
- **Q2a. Poll fast at first.** During the first 60 s after readiness, poll for id files every 1 s, then every 5 s.
- **Q5. Stay safe, as in v1.**
  - No Commander, Commander DB or `roleplaying-agents` access, other than read-only `sqlite3 -readonly`.
  - Clean up only with `tmux kill-session -t <exact name>`.
  - Kill a survivor only by its exact PID, and only if the probe launched it.
  - Codex processes may be listed by pattern but never killed by pattern.
  - No commits or pushes to the project repo.
  - Disclosed side effects:
    - Trust rows and hooks state are persisted in `~/.codex/config.toml`. This time they are expected, and each run logs them.
    - `session-init` runs its usual maintenance.
    - Session rows are written to `ironclaude.db`.
- **Q6. Write a findings note** at `docs/reviews/2026-10-07-codex-spawn-repro-v2-findings.md`. Mark each result VERIFIED or UNRESOLVED. Include inputs for the spawn-fix design.

## Approach

Reuse the v1 probe, `<scratchpad>/codex-repro/probe.sh`, as `probe-v2.sh`, with three changes:
- Phase 1 becomes a modal-aware readiness loop. It checks the current screen, `capture-pane -p` with no scrollback, and sends the responses above.
- The scratch repo path is per variant.
- Output goes to `run-v2-<V>`.

Phases 2, 3 and 4 are unchanged from v1. Those are the in-tree and any-provenance id-file polls, the rescans, and cleanup.

## Final gate

None. This is an investigation note; the spawn-fix loop has its own review gates.
