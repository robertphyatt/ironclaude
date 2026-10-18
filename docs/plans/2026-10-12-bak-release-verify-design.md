# Verify the Episodic Backup Release Design

> **Created:** 2026-10-12
> **Status:** Design Complete

Loop: light — two read-only checks; result appended to an existing findings doc; nothing changed.

## Requirements

On 2026-10-12 the operator restarted the four Claude Code sessions whose episodic-memory MCP servers held the deleted 39.08 GiB `db.sqlite.bak` open. Those were PIDs 6421, 10935, 19201 and 92160, owned by Claude sessions 5578, 10531, 15296 and 8069 (`docs/reviews/2026-10-12-disk-shortfall-findings.md` §6). The operator said "they have been restarted. Try again."

- V1. `lsof +L1` shows no process still holding `db.sqlite.bak`. Any remaining holder is listed with its PID and parent chain, so it can be released.
- V2. `df -h /System/Volumes/Data` free space, compared with the 207 GiB baseline in `/private/tmp/disk-shortfall/df.txt`. A rise of about 39 GiB confirms the release. Any other difference is reported as measured, without a guessed cause.
- V3. Append `## 7. Release check (2026-10-12)` to the findings doc with V1 and V2, then stage it with `git add -f`.
- V4. Kill or change nothing.
