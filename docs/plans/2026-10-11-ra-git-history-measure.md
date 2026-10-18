# roleplaying-agents Git History: Measure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Measure what fills roleplaying-agents' 113 GB `.git` and write a findings doc with ranked cleanup options. Nothing is modified.

**Requirements:** `docs/plans/2026-10-11-ra-git-history-measure-design.md` (M1–M7).

**Loop:** light — read-only git inspection plus one doc.

## Execution invariants

- Read-only git only: `count-objects`, `rev-list`, `cat-file`, `for-each-ref`, `log`, `worktree list`, `remote -v`, `rev-parse`, `ls-files`, `merge-base`. No gc, prune, repack, filter, ref deletion, fetch or push.
- Use `git -C /Users/roberthyatt/Code/roleplaying-agents` throughout.
- Bash timeout 1800000 on full-history scans.
- No `2>/dev/null` on evidence commands, except where git prints the known broken-ref warnings. Record those warnings once.

## Task 1: Measure and write findings

No tests required: read-only measurement and a doc.

**Step 1 (M1).**
- `df -h /System/Volumes/Data`, which needs at least 5 GiB free for the temp files.
- `git count-objects -vH`.
- `ls -la` of `.git/objects/pack/*.pack` sorted by size, top 15, plus the count.
- `du -sh` of every `.git/*` entry, sorted largest first, so that space outside the packs is measured (`.git/lfs`, `.git/modules`, `.git/worktrees`, `.git/lost-found`, `.git/filter-repo`, `.git/logs` and so on).
- `ls -la .git/filter-repo`, to record when the earlier filter-repo rewrite ran.

**Step 2 (M2).** Largest blobs across all refs. Bash timeout 1800000:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents rev-list --objects --all > /private/tmp/ra-objects.txt
git -C /Users/roberthyatt/Code/roleplaying-agents cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(objectsize:disk) %(rest)' < /private/tmp/ra-objects.txt | awk '$1=="blob"' > /private/tmp/ra-blobs.txt
sort -k3 -n -r /private/tmp/ra-blobs.txt | head -n 40
awk '{p=$5; for(i=6;i<=NF;i++) p=p" "$i; s[p]+=$4; n[p]++} END {for (p in s) printf "%d %d %s\n", s[p], n[p], p}' /private/tmp/ra-blobs.txt | sort -n -r | head -n 30
awk '{t+=$4} END {print "total blob disk bytes", t}' /private/tmp/ra-blobs.txt
```

Record the top 40 blobs, the top 30 paths by summed on-disk size with version counts, and the total. The path keeps every field from 5 onward, so paths with spaces such as `Core Rulebook.txt` stay whole. `--all` skips objects reachable only from reflogs or not at all, so the blob total can be below the pack total. Report that gap as reflog-only or unreachable objects, not as unexplained.

**Step 3 (M3).** `git for-each-ref --format='%(refname)'`, aggregated as a count per namespace. Group by the first 3 path segments (`cut -d/ -f1-3 | sort | uniq -c`) so the codex turn-diff refs collapse into one line. Record the 2 broken-ref warnings. Run `du -sh .git/logs` and `git stash list`, recording its count.

**Step 4 (M4).** Record:
- `git rev-list --all --count`;
- the first commit date (`git log --all --reverse --format=%ci | head -n 1`) and the last;
- commits per month (`git log --all --format=%cd --date=format:%Y-%m | sort | uniq -c`);
- `git for-each-ref refs/heads --sort=-committerdate --format='%(committerdate:short) %(refname:short)'`;
- `git branch --contains f6f0d3134`;
- `git merge-base --is-ancestor f6f0d3134 main`, with its exit code.

**Step 5 (M5).** Record:
- `git remote -v`;
- `cat .gitattributes`;
- `git lfs ls-files --all`, counting the lines and recording its exit code (Bash timeout 1800000, because it walks all refs);
- `git for-each-ref refs/heads --format='%(refname:short) %(upstream:short) %(upstream:track)'`.

**Step 6 (M6).** Record:
- `git worktree list`;
- `pgrep -lx ChatGPT`;
- `ps -axo pid,command` filtered for processes whose command line contains `roleplaying-agents` (first 20 lines, truncated to 200 chars).

**Step 7 (M7).** Write `docs/reviews/2026-10-11-ra-git-history-findings.md` with all the measurements and the ranked options. Each option gives its estimated reclaim, its risk and what it needs approved. The options must include:
- an LFS-store option (`git lfs prune` and/or untracking LFS patterns), because a history rewrite plus gc does not shrink `.git/lfs`;
- options for submodules (`.git/modules`) and other non-pack stores, where those are large.

Then stage it:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-11-ra-git-history-findings.md docs/plans/2026-10-11-ra-git-history-measure-design.md docs/plans/2026-10-11-ra-git-history-measure.md docs/plans/2026-10-11-ra-git-history-measure.plan.json
```

Then `rm /private/tmp/ra-objects.txt /private/tmp/ra-blobs.txt`.
