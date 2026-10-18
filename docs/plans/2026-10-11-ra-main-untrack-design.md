# roleplaying-agents: Untrack the Catalog DB on main

> **Created:** 2026-10-11
> **Status:** Design Complete

Loop: full — a git ref update on `main` (a git operation).

## Requirements

Operator, 2026-10-11, on the risk that checking out `main` silently replaces the untracked 3.65 GB `asset_catalog.db` with the tracked 6 MB version: "can't we just fix it now so that it is untracked before doing anything else?" The git prune (option A) follows afterwards.

- **U1. Gate.** `main` must track `tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` (`git ls-tree main -- <path>`). If it does not, stop: there is nothing to do.
- **U2. Build the change on `main`'s own tree, never the working checkout.**
  - Use a temporary index (`GIT_INDEX_FILE=/private/tmp/ra-main-untrack.index`): `read-tree main`, then `update-index --force-remove` the database path.
  - For the `.gitignore`, take **`main`'s** `tools/inkarnate_mcp/.gitignore` (`git show main:…`), append `asset_catalog_raw/asset_catalog.db` if it is absent, `hash-object -w` it, and `update-index --cacheinfo 100644,<blob>,tools/inkarnate_mcp/.gitignore`.
  - `diff --cached --name-status main` must show exactly `D` for the database and `M` for the `.gitignore`.
- **U3. Commit onto `main` without a checkout.** `write-tree`, then `commit-tree <tree> -p <main sha> -m "<message>"`, then `update-ref refs/heads/main <new> <old>` (compare-and-swap on the recorded old `main` SHA). The guard blocks agent commits, so the operator runs this one command. The message matches `f6f0d3134`. No push.
- **U4. Verify.**
  - `git show --stat main` lists exactly those 2 paths.
  - `git ls-tree main -- <db path>` is empty.
  - `git show main:tools/inkarnate_mcp/.gitignore` contains the line.
  - The working checkout and the database file on disk are unchanged (3,654,037,504 bytes), and `origin/main` is unchanged.
- **U5.** Record the result in `docs/reviews/2026-10-11-ra-git-history-findings.md`.

## Non-goals

No change to other branches (`rescue/*`, `preserved/*` and the rest are rarely checked out; the operator can extend this later), no checkout, no push.
