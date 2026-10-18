# Disk Usage: Measure and Dry-Run Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Measure current disk consumers, run `episodic-memory maintain --dry-run`, and write a findings doc with options. Nothing is changed.

**Requirements:** `docs/plans/2026-10-11-disk-usage-measure-design.md` (`## Requirements`, R1–R5).

**Architecture:** Read-only `df`, `du` and `find`, plus the episodic-memory CLI in dry-run mode. The findings doc is the only file this plan writes.

**Loop:** light — read-only measurement plus a dry-run; no code, DB write, guard, git or API change; one doc.

## Execution invariants

- Bash cwd may be `commander/`, so use absolute paths throughout.
- zsh `nomatch`: quote globs.
- No `2>/dev/null` on evidence commands, because a failed command must stay distinguishable from an empty result.
- Never delete, move, compress, gc or edit config. `--dry-run` only.

---

## Task 1: Measure, dry-run, findings

No tests required: read-only measurement and a findings doc; no executable code changes.

**Files:**
- Create: `docs/reviews/2026-10-11-disk-usage-findings.md`

**Step 1.** Run `df -h /`, then `df -h /System/Volumes/Data`. Record both.

**Step 2.** Run `du -sh /Users/roberthyatt/.config/superpowers/conversation-index /Users/roberthyatt/.config/superpowers/conversation-archive /Users/roberthyatt/Code/roleplaying-agents/.git /Users/roberthyatt/Code/roleplaying-agents/.git/objects/pack`. Then `ls -la /Users/roberthyatt/.config/superpowers/conversation-index`. Record both outputs.

**Step 3.** Run each of the following in its own command:
- `find /Users/roberthyatt/Code/roleplaying-agents/.git/objects/pack -name '*.pack' -print`, then count the lines it prints;
- `find /Users/roberthyatt/Code/roleplaying-agents/.git -maxdepth 3 -name 'tmp_pack_*' -print`;
- `find /Users/roberthyatt/Code/roleplaying-agents/.git -maxdepth 1 -name 'index.stash.*' -print`.

Record the count and every path printed.

**Step 4.** Run `find /Users/roberthyatt/Code -name 'catalog-before.sqlite3' -size +100M -exec ls -la {} +`, then `find /Users/roberthyatt -name '*.gguf' -size +500M -exec ls -la {} +`. Record both.

**Step 5.** Run `du -sh /Users/roberthyatt/* /Users/roberthyatt/.[a-zA-Z]*`, then `du -sh /Users/roberthyatt/Library/* /private/tmp`. Both globs match existing entries, so zsh `nomatch` does not abort them. Record any `du` permission errors as errors. Record the outputs sorted largest first, top 20 each.

**Step 6.** Confirm the opt-out with `cat /Users/roberthyatt/.config/superpowers/episodic-memory.json`. Expected: `{"maintain":{"autoRun":false}}`.

**Step 7.** Dry-run, then confirm nothing changed. Bash timeout 900000.

Run `ls -la /Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite`, then:

```
/Users/roberthyatt/Code/ironclaude/worker/mcp-servers/episodic-memory/cli/episodic-memory maintain --dry-run
```

Then run `ls -la /Users/roberthyatt/.config/superpowers/conversation-index/db.sqlite` again. Expected: the dry-run prints a per-phase report and makes no change, and the size and mtime of `db.sqlite` are unchanged (or changed only by a concurrent background sync's index write, which is recorded as such). Record the full output.

**Step 8.** Write `docs/reviews/2026-10-11-disk-usage-findings.md`:
- a table of each consumer: size now, size on 2026-10-07 (from `docs/reviews/2026-10-07-*.md` and memory), and the delta;
- the dry-run's per-phase output, including whether `slim` can run at the current free disk;
- options, each with its estimated reclaim, risk and reversibility:
  - (a) the episodic-memory rollout: `maintain --keep-bak`, then `autoRun` on;
  - (b) roleplaying-agents: untrack `asset_catalog.db` and `git gc`;
  - (c) prune the `probe_out` snapshots;
  - (d) prune the duplicate GGUFs;
  - (e) anything new from Step 5.

Then stage it:

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-11-disk-usage-findings.md
```

Expected: no output.
