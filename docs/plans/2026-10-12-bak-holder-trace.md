# Trace the Owners of the Held-Open Episodic Backup Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** For each of the four `node` processes holding the deleted 39 GiB `db.sqlite.bak` (PIDs 6421, 10935, 19201 and 92160), find which session owns it, and record it for the operator's restart-or-kill decision.

**Requirements:** `docs/plans/2026-10-12-bak-holder-trace-design.md` (O1–O5)

**Architecture:** One read-only task. It walks each PID's parent chain with `ps`, adds each process's cwd from `lsof`, and matches against tmux panes. The output files go to `/private/tmp/disk-shortfall/` and are named `trace-*`.

**Execution invariants:**
- Literal absolute paths throughout.
- No `2>/dev/null`: each step's stderr goes to its own `.err` file.
- No kill, signal or change of any kind. Only `ps`, `lsof` and `tmux list-panes` are used.
- `docs/` is gitignored, so docs are staged with `git -C … add -f`.

---

## Task 1: Trace the owners and record them (O1–O5)

No tests required: this is a read-only process listing whose only output is a findings section.

**Files:**
- Modify: `docs/reviews/2026-10-12-disk-shortfall-findings.md`, by appending a "Holder owners" section

**Step 1: Are the holders still alive and still holding the file? (O1)**
```bash
cd /private/tmp/disk-shortfall && for p in 6421 10935 19201 92160; do echo "== $p"; lsof -p "$p" 2> "trace-lsof-$p.err" | grep -F db.sqlite.bak; echo "grep=$?"; done > trace-holders.txt; cat trace-holders.txt
```
Expected: each PID shows either a `db.sqlite.bak` line with `grep=0`, or `grep=1` with the `.err` file explaining it. Either way it's recorded. A PID that has gone away is reported as having freed its hold.

**Step 2: Parent chains (O2)**
```bash
cd /private/tmp/disk-shortfall && for p in 6421 10935 19201 92160; do echo "== chain from $p"; c=$p; while [ -n "$c" ] && [ "$c" -gt 1 ]; do ps -o pid=,ppid=,lstart=,tty=,command= -p "$c" 2>> trace-ps.err; c=$(ps -o ppid= -p "$c" 2>> trace-ps.err | tr -d ' '); done; done > trace-chains.txt; cat trace-chains.txt
```
Expected: each chain runs up to a parent of 1. A `node` process whose parent is 1 is an orphan.

**Step 3: Working directories and tmux panes (O3)**
```bash
cd /private/tmp/disk-shortfall && for p in $(awk '/^ *[0-9]/{print $1}' trace-chains.txt | sort -u); do lsof -a -d cwd -p "$p" -Fn 2>> trace-cwd.err | sed -n "s/^n/$p cwd=/p"; done > trace-cwd.txt; tmux list-panes -a -F '#{pane_pid} #{session_name} #{pane_current_path}' > trace-tmux.txt 2> trace-tmux.err; echo "tmux=$?"; cat trace-cwd.txt trace-tmux.txt
```
Expected: each ancestor's cwd is recorded. Any chain PID that matches a tmux `pane_pid` names its tmux session.

**Step 4: Write the "Holder owners" section (O4)**

Append a section headed `## 6. Holder owners (2026-10-12)` to `docs/reviews/2026-10-12-disk-shortfall-findings.md`. It has one row per PID with these columns:
- still holding (yes or no);
- the owning top-level process (Claude Code, Codex or ChatGPT.app, tmux session, or orphan under `launchd`) with its PID and start time;
- its cwd;
- whether a live interactive session would be affected.

Then list the release options for each PID, as operator decisions only:
- end or restart the owning session;
- kill the exact `node` PID, if it is an orphan or a session that doesn't need the tool.

Never suggest a pattern kill. Cite the `trace-*` files.

**Step 5: Stage the findings**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-12-disk-shortfall-findings.md docs/plans/2026-10-12-bak-holder-trace-design.md docs/plans/2026-10-12-bak-holder-trace.md docs/plans/2026-10-12-bak-holder-trace.plan.json
```
Expected: no output.
