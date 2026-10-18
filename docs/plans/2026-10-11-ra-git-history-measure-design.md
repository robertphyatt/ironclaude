# roleplaying-agents Git History: Measure Design

> **Created:** 2026-10-11
> **Status:** Design Complete

Loop: light — read-only git inspection and one findings doc; no rewrite, gc, deletion, ref change or push.

## Requirements

Operator, 2026-10-11: "let's do a separate git cleanup after this. I'm open to untracking and cleaning up/compressing/rewriting, etc. I'm the only user of my git repo here, and I use it to keep things backed up, but I don't need backups to the beginning of this project. I'm never going to need to go back that far". Then: "I'm ready to start the cleanup".

Before choosing a rewrite approach, measure what fills `/Users/roberthyatt/Code/roleplaying-agents/.git` (113 GB; packs about 95 GB):

- **M1. Object store totals:** `git count-objects -vH`, plus the pack count and sizes.
- **M2. Largest blobs across all refs:** path, size and how many versions. Top 40 by size, plus the total size of each path's versions, top 30 paths.
- **M3. Refs:** counts per namespace (`refs/heads`, `refs/remotes`, `refs/tags`, `refs/ironclaude/*`, `refs/codex/*`, `refs/stash`); the 2 broken `refs/codex/turn-diffs/checkpoints (1)/…` refs; the reflog sizes.
- **M4. History shape:** commit count, first and last commit dates, commits per month, the branch list with last commit date, and whether `main` and the current branch share the untrack commit `f6f0d3134`.
- **M5. Remote:** `origin` URL, whether the remote is GitHub, LFS usage (`.gitattributes`, `git lfs ls-files` count), and whether the local branches are ahead or behind `origin`.
- **M6. Worktrees and active users:** `git worktree list`, and which sessions hold the checkout (Codex Desktop, Commander workers, other Claude sessions). Any rewrite must not run under an active session.
- **M7. Findings doc** `docs/reviews/2026-10-11-ra-git-history-findings.md`: the measurements, plus ranked options with the space each reclaims, its risk and what it requires. Options to cover: filter out large blobs keeping history; cut history before a date; start a fresh history from the current tree; drop the IronClaude/Codex/stash refs and reflogs, then gc. Each option states what the operator must approve: a force-push to origin, a mirror backup first, and stopping all sessions.

Nothing is modified.
