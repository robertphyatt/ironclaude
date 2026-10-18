# Worktree Allocate Failure Investigation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.
> **Execution mode:** inline (operator choice).

**Goal:** Find out, read-only, why workspace-manager `allocate` times out and leaves reservations stranded after the reboot; what happened to worktree `13cf1c2c`; and why the spawn swap guard used 8 GB. Write a findings note that drives the repair and v1.1.16.

**Requirements:** `docs/plans/2026-10-02-worktree-allocate-investigation-requirements.md` (I1–I6)

**Design:** `docs/plans/2026-10-02-worktree-allocate-investigation-design.md`

**Architecture:** This is a read-only forensic pass over three things: a snapshot of the workspace DB, the git internals of roleplaying-agents, and the allocate and swap-guard code paths. The only output is a findings note.

**Tech Stack:** sqlite3 (`-readonly`), git, rg, ps.

## Execution invariants

- **Read-only.** Never modify `~/.claude/ironclaude-workspaces.db`, git refs, worktrees, config or processes. Copy the DB into the scratchpad and query only the copy.
- **Absolute paths.** Shell state does not persist between steps.
- **No error suppression on evidence.** Don't add `2>/dev/null` to evidence commands, and state an empty result explicitly as empty.
- **No hard-coded answers in discovery steps.** Each step's expected result is "output captured and quoted in the findings note". Owner sessions come from the DB, not from a list written in advance.
- **Plan artifacts are force-staged.** `docs/` is gitignored, so stage with `git add -f`. No commit.

---

## Task 1: Forensic investigation and findings note (I1–I6)

**No tests required:** this is a read-only investigation. It changes no code, and its deliverable is a findings document.

**Files:**
- Create: `docs/plans/2026-10-02-worktree-allocate-findings.md`
- Scratch, outside the repo and not staged: `/private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/wsdb/`

**Step 1 (I1): Snapshot the DB.**

```bash
mkdir -p /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/wsdb && cp -p /Users/roberthyatt/.claude/ironclaude-workspaces.db /Users/roberthyatt/.claude/ironclaude-workspaces.db-wal /Users/roberthyatt/.claude/ironclaude-workspaces.db-shm /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/wsdb/
```

Expected: exit 0, and all three files are present.

**Step 2 (I2): Dump the schema and the relevant rows from the snapshot.**

```bash
sqlite3 -readonly /private/tmp/claude-502/-Users-roberthyatt-Code-ironclaude/7c7f63a0-e272-4e34-a05b-3d6ebc128c94/scratchpad/wsdb/ironclaude-workspaces.db .schema
```

Using the table and column names this prints, query:
- every row for roleplaying-agents that is not in a terminal state;
- every row matching `d1501%`;
- every row referencing `13cf1c2c`;
- every lease and lock table, in full.

Then collect the distinct owner sessions from `assignments.owner_session_id` and `primary_checkout_owners.owner_session_id`. Classify each one as **live** or **dead**:
- live means it appears in `ps`, or its transcript `~/.claude/projects/*/<id>.jsonl` has an mtime after 2026-10-02 09:57;
- dead means neither is true.

Expected: the output is captured and quoted in the findings note.

**Step 3 (I3): Record the git state.**

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents worktree list --porcelain
```

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents for-each-ref --format='%(refname) %(objectname:short) %(committerdate:iso)' refs/heads refs/ironclaude refs/tags/archive
```

For each managed ref, run `git -C /Users/roberthyatt/Code/roleplaying-agents rev-list --count <integration_target>..<ref>`, taking `integration_target` from that ref's DB row. If no row exists, compare against `main` and record that the fallback was used.

Expected: the output is captured and quoted in the findings note.

**Step 4 (I4): Work out what happened to 13cf1c2c.**

Gather the evidence:
- **Ref file timestamps.** `ls -laT /Users/roberthyatt/Code/roleplaying-agents/.git/refs/ironclaude/recovery /Users/roberthyatt/Code/roleplaying-agents/.git/refs/ironclaude/finalization`. This is the only timing evidence available, because these refs have no reflogs.
- **The last transcript in that worktree.** `ls -laT /Users/roberthyatt/.claude/projects/-Users-roberthyatt-Code-roleplaying-agents--ironclaude-worktrees-13cf1c2c-2344-4e35-8533-8b37a41f89f5`. Then search the tail of its newest transcript for `13cf1c2c`, `recovery`, `rescue`, `abandon` and `close_out`.
- **Other sessions' transcripts.** Search the `d855cc6f` and `7b03ab92` transcripts for `13cf1c2c`.
- **The DB rows** for 13cf1c2c from Step 2.

For each 13cf1c2c recovery or frozen ref, show what work it holds:
- `git -C /Users/roberthyatt/Code/roleplaying-agents log --oneline <integration_target>..<ref>`
- `git -C /Users/roberthyatt/Code/roleplaying-agents diff --stat <base_commit> <ref>`

Test this hypothesis: the worktree was closed out and snapshotted around Oct 1 07:13, before the reboot.

Conclude who removed it, when, and whether its work survives.

Expected: an evidence-backed conclusion in the findings note.

**Step 5 (I5): Find out why allocate times out, and why the swap guard used 8 GB.**

For allocate:
- Read `/Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/workspace_client.py`, which sets `timeout: int = 60` and passes it to `subprocess.run`.
- Read `reserveWorkerWorktree` and `materializeReservedAssignment` in `/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/workspace-manager/src/workspace-service.ts`.
- Measure the repo:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents count-objects -vH
```

- Also check the size of `ls-files`, `.gitattributes` and LFS, `.git/hooks`, and the shared-resource config.

For the swap guard:
- Read `/Users/roberthyatt/Code/ironclaude/commander/src/ironclaude/orchestrator_mcp.py:2830-2860`, and where `max_swap_used_gb` is loaded in `config.py`.
- Find the local config the daemon actually reads, and check whether it contains `max_swap_used_gb: 50` and `spawn_block_pressure_level: 4`. Explain why the Brain log reported "max 8.0GB".

Determine:
- the order of allocate's steps;
- which step is slow;
- what happens to the reservation and to the partial worktree after the timeout kills the process;
- why the 8 GB swap cap was in effect.

Expected: an evidence-backed conclusion in the findings note.

**Step 6 (I6): Write `docs/plans/2026-10-02-worktree-allocate-findings.md`.**

It must contain:
- the timeline;
- the root cause;
- what happened to 13cf1c2c;
- the evidence quoted;
- an answer to "WTF, I thought we fixed this".

It must also contain a **repair plan** with this ordering:
1. Stop Commander first, with `make -C /Users/roberthyatt/Code/ironclaude/commander stop`.
2. Make any DB, ref or worktree changes.
3. Relaunch Commander detached, with the Claude Code environment variables unset, and only once allocate is shown to complete.

And it must give the **candidate v1.1.16 scope**:
- **(a) Swap guard.** `orchestrator_mcp.py:2834-2857`: weigh dropping or downgrading the swap-used threshold in favour of the pressure check. Treat releasing swap without a reboot as an open question, to be tested with `sysctl vm.swapusage`.
- **(b) Stranded reservations and allocate timeout.** Fixes for both.
- **(c) Memory-leak pointer.** A pointer to the separate memory-leak investigation, noting the duplicate MCP server sets seen under one ChatGPT app-server.

Then stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-02-worktree-allocate-investigation-requirements.md docs/plans/2026-10-02-worktree-allocate-investigation-design.md docs/plans/2026-10-02-worktree-allocate-investigation.md docs/plans/2026-10-02-worktree-allocate-investigation.plan.json docs/plans/2026-10-02-worktree-allocate-findings.md
```

Expected: exit 0.
