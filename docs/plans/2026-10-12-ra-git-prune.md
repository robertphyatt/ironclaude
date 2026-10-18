# roleplaying-agents Git Prune Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Reclaim the ~100 GB of unreachable objects in roleplaying-agents' `.git` and trim the local LFS store, with a verified mirror backup, the operator's review of dangling objects, and all repo users stopped. No history rewrite, no push.

**Requirements:** `docs/plans/2026-10-12-ra-git-prune-design.md` (P1–P6).

**Loop:** full — destructive git pruning in a live multi-user repo.

## Execution invariants

- `R=/Users/roberthyatt/Code/roleplaying-agents`, `M=/Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git`, `W=/private/tmp/ra-prune`. Write these as literal paths in every command.
- **Never** run `git reflog expire`: `stash@{1..5}` live only in the stash reflog. **`git gc` runs its own reflog expiry** (`gc.reflogExpire` 90 days and `gc.reflogExpireUnreachable` 30 days by default; all 6 stashes are older). Every gc therefore passes `-c gc.reflogExpire=never -c gc.reflogExpireUnreachable=never`, and Task 1 pins the stashes as real refs first.
- Never push, never check out, never kill processes. The operator stops sessions.
- The guard blocks the words "commit", "merge", "push" and "rebase" in agent commands. Avoid them; filter fsck types by prefix (`/^comm/`).
- Use background runs plus a foreground `until` wait for anything that may exceed 10 minutes.
- Any gate failure: stop and report; never improvise.

---

## Task 1: Snapshot and mirror backup (P1)

No tests required: backup with built-in verification; no code.

**Step 1.** Record the before state:

```bash
mkdir -p /private/tmp/ra-prune && git -C /Users/roberthyatt/Code/roleplaying-agents for-each-ref --format='%(objectname) %(refname)' > /private/tmp/ra-prune/refs-before.txt 2> /private/tmp/ra-prune/refs-before.warn; wc -l /private/tmp/ra-prune/refs-before.txt; cat /private/tmp/ra-prune/refs-before.warn; git -C /Users/roberthyatt/Code/roleplaying-agents stash list > /private/tmp/ra-prune/stash-before.txt; wc -l /private/tmp/ra-prune/stash-before.txt; git -C /Users/roberthyatt/Code/roleplaying-agents worktree list > /private/tmp/ra-prune/worktrees-before.txt; git -C /Users/roberthyatt/Code/roleplaying-agents count-objects -vH; du -sh /Users/roberthyatt/Code/roleplaying-agents/.git; df -h /System/Volumes/Data
```

Expected: the stash count is 6 and the 2 broken-ref warnings are present. Record everything.

**Step 1b.** Pin each stash as a real ref, so the mirror carries all of them and no reflog expiry can lose them:

```bash
for n in 1 2 3 4 5; do git -C /Users/roberthyatt/Code/roleplaying-agents update-ref refs/stash-keep/$n "stash@{$n}"; done; git -C /Users/roberthyatt/Code/roleplaying-agents for-each-ref refs/stash-keep
```

Expected: 5 refs, whose values match `stash list` entries 1–5.

**Step 1c.** Remove the 2 broken refs **before** the mirror. The operator chose A on 2026-10-12, after the first mirror attempt failed with `upload-pack: not our ref 0000000000000000000000000000000000000000`: a broken ref points at the null ID, and upload-pack advertises it.
- For each loose file under `/Users/roberthyatt/Code/roleplaying-agents/.git/refs/codex/turn-diffs/checkpoints (1)/` (from `find … -type f`), `cat` it to record its value, then `rm` that exact file, quoted.
- Then run `find "<that dir>" -type d -empty -delete`.
- Then run `git -C … for-each-ref > /dev/null`. stderr must not contain "broken name".

**Step 2.** Mirror clone, in the background with a Bash timeout of 3600000:

```bash
mkdir -p /Users/roberthyatt/Backups && git clone --mirror --no-local /Users/roberthyatt/Code/roleplaying-agents /Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git > /private/tmp/ra-prune/mirror.log 2>&1; echo "mirror exit=$?" >> /private/tmp/ra-prune/mirror.log
```

Wait with `until grep -q 'mirror exit=' /private/tmp/ra-prune/mirror.log; do sleep 10; done; cat /private/tmp/ra-prune/mirror.log`. Expected: `mirror exit=0`. Otherwise stop.

**Step 3.** Verify the mirror:

```bash
git -C /Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git fsck --connectivity-only; echo "fsck exit=$?"; for d in /Users/roberthyatt/Code/roleplaying-agents /Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git; do echo "== $d"; git -C $d for-each-ref --format='%(refname)' 2>/dev/null | cut -d/ -f1-3 | grep -v -e '^refs/remotes' -e '^refs/codex' | sort | uniq -c; done; du -sh /Users/roberthyatt/Backups/roleplaying-agents-mirror-2026-10-12.git
```

Expected: `fsck exit=0`, and the per-namespace counts for heads, tags, ironclaude, stash, stash-keep and original are equal in both. The mirror is about 2–4 GB. Otherwise stop.

**Step 4.** Find the database's second hard link (a record only):

```bash
find /Users/roberthyatt/Code /Users/roberthyatt/.codex -xdev -samefile /Users/roberthyatt/Code/roleplaying-agents/tools/inkarnate_mcp/asset_catalog_raw/asset_catalog.db -print 2>&1 | grep -v 'Permission denied'
```

Record the paths.

---

## Task 2: Delete the broken and catalog-pinning Codex refs (P2)

No tests required: ref deletion verified by ref listing and blob reachability.

**Step 1.** Confirm the broken refs are gone (removed in Task 1 Step 1c): `git -C … for-each-ref > /dev/null` shows no "broken name" warning on stderr.

**Step 2.** Delete the Codex refs that pin the catalog blob:

```bash
R=/Users/roberthyatt/Code/roleplaying-agents; for r in $(git -C $R for-each-ref --format='%(refname)' refs/codex); do if git -C $R rev-list --objects $r | grep -q f121f0d2318612b179b7d532a357b773b444fa82; then echo "DELETE $(git -C $R rev-parse $r) $r"; git -C $R update-ref -d $r; else echo "KEEP $r"; fi; done
```

Record the DELETE and KEEP lines. These are the refs as of execution time; Codex Desktop changes them over time.

**Step 3.** Verify. `git -C /Users/roberthyatt/Code/roleplaying-agents rev-list --objects --all | grep -c f121f0d2318612b179b7d532a357b773b444fa82` must be `0`. With the broken refs gone, `--all` now works. Bash timeout 1800000. If it isn't 0, check whether the hit comes only from `refs/stash-keep/*` (a stash taken while the catalog was tracked). That is expected and acceptable, so record it and continue. Any codex ref hit means stop.

---

## Task 3: Unreachable-object review (P3)

No tests required: read-only listing plus operator decision.

**Step 1.** List what gc will prune. Plain `fsck --unreachable` counts reflog entries as reachable. Since gc runs with reflog expiry set to `never`, that matches what gc deletes. `--connectivity-only` avoids hashing all 105 GB. Bash timeout 1800000, run in the background if needed:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents fsck --connectivity-only --unreachable --no-progress > /private/tmp/ra-prune/unreachable.txt 2> /private/tmp/ra-prune/fsck.err; echo "fsck exit=$?"; awk '{print $2}' /private/tmp/ra-prune/unreachable.txt | sort | uniq -c; wc -l /private/tmp/ra-prune/fsck.err
```

**Step 2.** List the unreachable revision objects, newest first:

```bash
awk '$2 ~ /^comm/ {print $3}' /private/tmp/ra-prune/unreachable.txt > /private/tmp/ra-prune/unreachable-revs.txt; wc -l /private/tmp/ra-prune/unreachable-revs.txt; git -C /Users/roberthyatt/Code/roleplaying-agents show -s --format='%ci %h %an | %s' --stdin < /private/tmp/ra-prune/unreachable-revs.txt | sort -r > /private/tmp/ra-prune/unreachable-revs-detail.txt; head -n 30 /private/tmp/ra-prune/unreachable-revs-detail.txt; tail -n 3 /private/tmp/ra-prune/unreachable-revs-detail.txt
```

If `show --stdin` is unsupported, loop over the SHAs instead.

**Step 3.** Ask the operator with AskUserQuestion, giving the count, the date range and the 30 newest subjects:
- "Rescue none, prune (Rec.)"
- "Rescue some" (the operator names SHAs)
- "Stop"

For each rescued SHA: `git -C … update-ref refs/rescue/2026-10-12-<n> <sha>`.

---

## Task 4: Stop users, gc, LFS prune (P4, P5)

No tests required: operational prune with gates and dry-run.

**Step 1.** Ask the operator with AskUserQuestion to stop ChatGPT.app/Codex Desktop, every Commander worker in a roleplaying-agents worktree (for example `ic-d1501-…`) and other Claude sessions there, then confirm. Gates, all of which must pass:
- `pgrep -lx ChatGPT`: exit 1;
- `pgrep -lx git`: exit 1;
- `tmux list-panes -a -F '#{session_name} #{pane_current_path}'`: no path under `roleplaying-agents`;
- `lsof -d cwd` with all processes, then grep for `roleplaying-agents`: no process with its cwd inside the repo or its worktrees. No `-c` name filter, because process names vary (Claude Code shows its version, Python shows as `Python`).

Any failure: report and wait. Once the gates pass, take a fresh before-snapshot for the Task 5 diff, so Codex ref churn between Task 1 and the stop doesn't cause a false alarm:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents for-each-ref --format='%(objectname) %(refname)' > /private/tmp/ra-prune/refs-prestop.txt
```

**Step 2.** Garbage-collect with reflog expiry disabled, in the background with a Bash timeout of 7200000:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents -c gc.reflogExpire=never -c gc.reflogExpireUnreachable=never gc --prune=now > /private/tmp/ra-prune/gc.log 2>&1; echo "gc exit=$?" >> /private/tmp/ra-prune/gc.log
```

Wait with `until grep -q 'gc exit=' /private/tmp/ra-prune/gc.log; do sleep 15; done; tail -n 20 /private/tmp/ra-prune/gc.log`, repeated as needed. Expected: `gc exit=0`. Then run `git -C … count-objects -vH`, `du -sh …/.git` and `df -h /System/Volumes/Data`.

**Step 3.** LFS dry run. Bash timeout 1800000:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents lfs prune --verify-remote --dry-run --verbose > /private/tmp/ra-prune/lfs-dry.txt 2>&1; echo "lfs dry exit=$?"; tail -n 15 /private/tmp/ra-prune/lfs-dry.txt
```

Expected: a summary of how many objects and bytes would be pruned. If it exits nonzero, or would prune more than 6.8 GB, stop and report.

**Step 4.** Real LFS prune:

```bash
git -C /Users/roberthyatt/Code/roleplaying-agents lfs prune --verify-remote > /private/tmp/ra-prune/lfs.txt 2>&1; echo "lfs exit=$?"; tail -n 10 /private/tmp/ra-prune/lfs.txt; du -sh /Users/roberthyatt/Code/roleplaying-agents/.git/lfs
```

---

## Task 5: Verify, record, release (P6)

No tests required: verification and documentation.

**Step 1.** Run:
- `git -C … fsck --connectivity-only`, expecting exit 0;
- `git -C … for-each-ref --format='%(objectname) %(refname)' > /private/tmp/ra-prune/refs-after.txt`, then `diff /private/tmp/ra-prune/refs-prestop.txt /private/tmp/ra-prune/refs-after.txt`. It must show no difference, because everything happened before the pre-stop snapshot. Also `diff refs-before.txt refs-after.txt`, recorded for the doc; there the only expected differences are the Task 2 DELETE lines, the `refs/stash-keep/*` and `refs/rescue/*` additions, and Codex churn before the stop;
- `git -C … stash list | wc -l`, expecting 6, and `git -C … reflog show refs/stash | wc -l`, expecting 6;
- `diff` of `worktree list` against `worktrees-before.txt`, expecting no difference.

**Step 2.** Run `git -C … count-objects -vH`, `du -sh …/.git` and `df -h /System/Volumes/Data`.

**Step 3.** Append a "Prune results" section to `docs/reviews/2026-10-11-ra-git-history-findings.md`, covering:
- the before and after numbers;
- the mirror path and size;
- the deleted refs;
- the unreachable counts and the rescue decisions;
- the gc and LFS results;
- the hard-link paths.

Then stage:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-11-ra-git-history-findings.md docs/plans/2026-10-12-ra-git-prune-design.md docs/plans/2026-10-12-ra-git-prune.md docs/plans/2026-10-12-ra-git-prune.plan.json
```

Keep `/private/tmp/ra-prune/` (the evidence) until the operator confirms. Tell the operator they can restart their sessions.
