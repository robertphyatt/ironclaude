# roleplaying-agents: Untrack the Catalog DB on main — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** `main` stops tracking `tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db` and ignores it, via a commit built from `main`'s own tree. There is no checkout and no push.

**Requirements:** `docs/plans/2026-10-11-ra-main-untrack-design.md` (U1–U5).

**Loop:** full — a git ref update.

## Execution invariants

- Use `R=/Users/roberthyatt/Code/roleplaying-agents`, written as a literal path in every command.
- Never check out anything, never touch the working tree or the real index, and never push.
- The guard blocks the words "commit", "merge", "push" and "rebase" in agent Bash commands. Agent commands avoid them; the operator runs the one `commit-tree` plus `update-ref` command.

## Task 1: Untrack on main

No tests required: a git plumbing operation verified by ref and tree inspection.

**Step 1 (U1 gate).** Run:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents rev-parse main
git -C /Users/roberthyatt/Code/roleplaying-agents ls-tree main -- tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db
git -C /Users/roberthyatt/Code/roleplaying-agents rev-parse origin/main
```

Record OLD_MAIN and ORIGIN_MAIN. Expected: the `ls-tree` line shows a blob (recorded on the current branch as `2785c415…`). If it is empty, stop: `main` doesn't track it.

**Step 2 (U2, `.gitignore`).** Run:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents show main:tools/inkarnate_mcp/.gitignore > /private/tmp/ra-main-gitignore
grep -n -x 'asset_catalog_raw/asset_catalog.db' /private/tmp/ra-main-gitignore
```

If grep prints nothing, append the line with `printf 'asset_catalog_raw/asset_catalog.db\n' >> /private/tmp/ra-main-gitignore`. Check whether the file ends with a newline first (`tail -c 1`); if it doesn't, prepend `\n`. Then:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents hash-object -w /private/tmp/ra-main-gitignore
```

Record it as GI_BLOB.

**Step 3 (U2, temp index).** Run:

```bash
GIT_INDEX_FILE=/private/tmp/ra-main-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents read-tree main
GIT_INDEX_FILE=/private/tmp/ra-main-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents update-index --force-remove tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db
GIT_INDEX_FILE=/private/tmp/ra-main-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents update-index --cacheinfo 100644,<GI_BLOB>,tools/inkarnate_mcp/.gitignore
GIT_INDEX_FILE=/private/tmp/ra-main-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents diff --cached --name-status main
```

Expected: exactly `M tools/inkarnate_mcp/.gitignore` and `D tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`. Anything else: stop.

**Step 4 (U3).** Run:

```bash
GIT_INDEX_FILE=/private/tmp/ra-main-untrack.index git -C /Users/roberthyatt/Code/roleplaying-agents write-tree
```

Record it as NEW_TREE. Then give the operator this exact single command, with the literal values filled in, to run with the `!` prefix:

```
! cd /Users/roberthyatt/Code/roleplaying-agents && N=$(git commit-tree <NEW_TREE> -p <OLD_MAIN> -m "Stop tracking the 3.65 GB inkarnate asset catalog database" -m "Every Codex Desktop turn-diff snapshot re-stored the modified database and left hundreds of MB of tmp packs behind. The file stays on disk and is now ignored.") && git update-ref refs/heads/main $N <OLD_MAIN> && git log -1 --stat main
```

`update-ref` with the old value is a compare-and-swap: it fails if `main` moved in the meantime.

**Step 5 (U4 verify).** Run:
- `git -C … show --stat --format='%h %s' main`: exactly the 2 paths, `asset_catalog.db | Bin 6033408 -> 0 bytes` and `.gitignore | 1 +`;
- `git -C … rev-parse main^`, which must equal OLD_MAIN;
- `git -C … ls-tree main -- tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db`, which must be empty;
- `git -C … show main:tools/inkarnate_mcp/.gitignore`, which must contain the line;
- `git -C … rev-parse origin/main`, which must equal ORIGIN_MAIN;
- `ls -la …/asset_catalog_raw/asset_catalog.db`, which must show 3654037504 bytes;
- `git -C … rev-parse --abbrev-ref HEAD`, which must still be `portrait-gen-structural-sclera-gate`.

Then `rm /private/tmp/ra-main-untrack.index /private/tmp/ra-main-gitignore`.

**Step 6 (U5).** Append a "main untrack" section to `docs/reviews/2026-10-11-ra-git-history-findings.md` and stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-11-ra-git-history-findings.md docs/plans/2026-10-11-ra-main-untrack-design.md docs/plans/2026-10-11-ra-main-untrack.md docs/plans/2026-10-11-ra-main-untrack.plan.json
```
