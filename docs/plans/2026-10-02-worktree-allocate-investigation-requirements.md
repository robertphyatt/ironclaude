# Worktree Allocate Failure — Investigation Requirements

> **Created:** 2026-10-02
> **Scope mode:** hold (investigation only — no repair)

## Operator directives

- "the ironclaude commander said it was blocked because of swap memory and wanted me to reboot. I rebooted and now it says it can't figure out the git worktrees and that it is a mess."
- "1) WTF, I thought we fixed this?! 2) I need you to untangle the mess it made so that it isn't block anymore 3) looks like we need to look at a v1.1.16 to figure out what in the hell happened here and fix it properly so that ironclaude always works correctly and that the whole concept of worktree management never gets tripped over"
- "can you see the ironclaude logs?"
- **Swap guard (relayed note).** The operator relayed a note that the swap guard counts swap *used*. macOS rarely releases swap until a reboot, so the block will recur. The note asks v1.1.16 to consider checking memory pressure instead. The operator also asked: "is there a way to nudge macOS to let go of swap".
- **Memory leak.** "it seems like we have a memory leak then, which we need to investigate after your current investigation".
- **Execution mode.** The operator approved a read-only investigation first and chose Inline execution.

## Facts (verified 2026-10-02, read-only)

- **Before the reboot:** Brain log `20261002-151820` shows the spawn being rejected with "swap 36.5GB used (max 8.0GB)".
- **After the reboot:** Commander restarted at 09:57 (`/tmp/ic/daemon.log`).
  - From 10:03, workspace-manager `allocate` timed out at 60s for `/Users/roberthyatt/Code/roleplaying-agents`.
  - Retries collided with orphaned worker-id reservations (`d1501-r1`, `d1501-r1-rev`).
  - An operator_wait was recorded at 10:12.
  - Git itself is healthy: `git status` ran in 2.86s.
- **IronClaude worktrees:** `roleplaying-agents/.ironclaude/worktrees/` is empty, with mtime 10:07.
- **Git's worktree records:** `.git/worktrees/` holds only 3 Codex-owned worktrees (`~/.codex/worktrees/{a535,8735,8859}`), and all of them exist.
- **Possible lost worktree:** before the reboot, a live Claude session (`claude --model claude-opus-4-8`, started Sep 12) had its cwd at `roleplaying-agents/.ironclaude/worktrees/13cf1c2c-2344-4e35-8533-8b37a41f89f5`. That worktree is now gone.
- **Workspace DB:** `~/.claude/ironclaude-workspaces.db` was last written at 10:07. Older `.bak-d1475` and `.safebak` copies exist.
- **Assignment query:** `list_active_assignments` returns `[]`, but it is scoped to this session only.

## Requirements (investigation)

- **I1.** Snapshot the workspace DB, including its `-wal` and `-shm` files, to a timestamped copy before any analysis.
- **I2.** Dump the DB read-only:
  - the schema;
  - all nonterminal rows for roleplaying-agents;
  - rows matching `d1501*`;
  - rows referencing `13cf1c2c`;
  - lease and lock tables.

  Derive the set of owner sessions from the DB itself (`assignments.owner_session_id`, `primary_checkout_owners.owner_session_id`). Do not hard-code a list. Classify each owner as live or dead, using `ps` plus whether its transcript (`~/.claude/projects/*/<id>.jsonl`) was written after the 09:57 restart.
- **I3.** Record the git state of roleplaying-agents:
  - `git worktree list --porcelain`;
  - `git for-each-ref` over `refs/heads`, `refs/ironclaude` and `refs/tags/archive`, so that older namespaces such as `preserved/` and `rescue/` show up without being guessed.

  For ahead-counts, compare against each row's `integration_target`, and against `base_commit` for diffstats. Fall back to `main` only when no row exists, and say so. The primary checkout's HEAD is not `main`.
- **I4.** Determine what happened to worktree `13cf1c2c`: who removed it, when, and whether any work was lost or preserved.
  - **Evidence to use:**
    - the mtimes of its recovery and frozen ref files (`ls -laT`), since `refs/ironclaude` has no reflogs;
    - the tail of the last transcript under `~/.claude/projects/-Users-roberthyatt-Code-roleplaying-agents--ironclaude-worktrees-13cf1c2c-*`;
    - mentions in the live transcripts `d855cc6f` and `7b03ab92`;
    - the matching DB rows.
  - For each recovery or frozen ref, show what work it holds (`git log --oneline <integration_target>..<ref>`, `git diff --stat <base_commit> <ref>`).
  - Test the hypothesis that it was handled on Oct 1 around 07:13, before the reboot, rather than assuming it was lost at the reboot.
- **I5.** Determine why `allocate` takes longer than 60s:
  - the code path, and where the 60s timeout is enforced;
  - the steps allocate runs;
  - repo size and checkout cost, measured without mutating anything;
  - what happens to the reservation and the partial worktree after the caller times out.
- **I6.** Write a findings note covering the root cause, a repair plan for item 2, and candidate v1.1.16 scope for item 3. Then answer item 1 with evidence. The note must include all of the following:
  - **(a) Swap guard.** A v1.1.16 scope item evaluating the swap guard:
    - `commander/src/ironclaude/orchestrator_mcp.py:2834-2843` blocks spawns when swap *used* exceeds `max_swap_used_gb` (default 8.0, `config.py:36`).
    - A memory-pressure guard already exists at `:2844-2857`.
    - The note must weigh dropping the swap threshold, or making it advisory, in favour of the pressure check.
    - It must state how to release swap without a reboot as an open question, not as an asserted mechanism. The test is `sysctl vm.swapusage` before and after closing the processes with the most resident memory.
  - **(b) Repair ordering.** The repair plan must stop Commander (`make -C /Users/roberthyatt/Code/ironclaude/commander stop`) before any DB, ref or worktree mutation. It may relaunch Commander only after `allocate` is shown to complete, detached, and with the Claude Code environment variables unset.
  - **(c) Memory leak.** A pointer to the separate memory-leak investigation the operator asked for next. Before the reboot, `ps` showed repeated duplicate MCP server sets under one ChatGPT app-server.

## Constraints

- No mutation of the DB, git refs, worktrees or processes in this loop.
- The Codex worktrees are not IronClaude's to touch.
- Kill processes only by exact PID, and only in the later repair loop.
