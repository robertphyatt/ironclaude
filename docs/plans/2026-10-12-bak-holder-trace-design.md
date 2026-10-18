# Trace the Owners of the Held-Open Episodic Backup Design

> **Created:** 2026-10-12
> **Status:** Design Complete

Loop: light — read-only process listing; one short findings section appended to an existing doc; nothing killed or changed.

## Requirements

The 2026-10-12 disk-shortfall investigation (`docs/reviews/2026-10-12-disk-shortfall-findings.md`) found a deleted 39.08 GiB `db.sqlite.bak` still held open by 4 `node …/episodic-memory/cli/mcp-server-wrapper.js` processes: PIDs 6421, 10935, 19201 and 92160. The operator chose "Trace owners, then decide (Rec.)": find which session owns each process, report it, and then the operator decides between restarting the sessions or killing exact PIDs.

- O1. For each of the 4 PIDs, confirm it is still alive and still holds the `.bak` (`lsof -p <pid>`, filtered to `db.sqlite.bak`).
- O2. Walk each PID's parent chain up to `launchd` (PID 1). For each ancestor record the PID, PPID, start time, TTY and command, using `ps -o pid,ppid,lstart,tty,command`.
- O3. Identify the owning session: a Claude Code process with its tty and cwd (`lsof -a -d cwd -p <pid>`), a Codex or ChatGPT.app process, a tmux pane (match against `tmux list-panes -a -F '#{pane_pid} #{session_name} #{pane_current_path}'`), or an orphan reparented to `launchd`.
- O4. Append a "Holder owners" section to `docs/reviews/2026-10-12-disk-shortfall-findings.md`. It gives, for each PID, the owner, whether killing it would affect a live session, and the safest way to release it, as operator decisions only. Re-stage with `git add -f`.
- O5. Kill, signal or change nothing. Read-only `ps`, `lsof` and `tmux list-panes` only.
