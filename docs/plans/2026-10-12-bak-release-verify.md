# Verify the Episodic Backup Release Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Confirm that restarting the four Claude sessions released the deleted 39 GiB `db.sqlite.bak`, and record the result.

**Requirements:** `docs/plans/2026-10-12-bak-release-verify-design.md` (V1–V4)

**Execution invariants:**
- Use literal absolute paths.
- Send each command's stderr to its own `.err` file. No `2>/dev/null`.
- Change nothing.
- `docs/` is gitignored, so stage the doc with `git -C … add -f`.

---

## Task 1: Check the release and record it (V1–V4)

No tests required: this task runs two read-only checks and appends a doc section.

**Files:**
- Modify: `docs/reviews/2026-10-12-disk-shortfall-findings.md` (append section 7)

**Step 1: Remaining holders (V1)**
```bash
cd /private/tmp/disk-shortfall && lsof +L1 > verify-lsof-L1.txt 2> verify-lsof-L1.err; echo "lsof=$?"; grep -F db.sqlite.bak verify-lsof-L1.txt; echo "bak-holders-grep=$?"
```
Expected: `bak-holders-grep=1`, meaning nothing holds the file. If it prints `0`, the matching lines name each remaining PID. Record that PID's chain with `ps -o pid=,ppid=,lstart=,tty=,command= -p <pid>`, repeating on each parent PID until reaching PPID 1.

**Step 2: Free space versus the baseline (V2)**
```bash
cd /private/tmp/disk-shortfall && df -h /System/Volumes/Data > verify-df.txt 2> verify-df.err; echo "df=$?"; cat df.txt verify-df.txt
```
Expected: the Avail figure now, compared with the 207Gi baseline in `df.txt`. Record the measured difference. A rise of about 39 GiB confirms the release.

**Step 3: Record the result (V3)**

Append `## 7. Release check (2026-10-12)` to `docs/reviews/2026-10-12-disk-shortfall-findings.md`. Include:
- the `bak-holders-grep` result, plus any remaining holders;
- Avail before and after, and the difference;
- whether the 39 GiB came back.

Cite `verify-lsof-L1.txt` and `verify-df.txt`. If the difference isn't about 39 GiB, report the number as measured and don't guess a cause.

**Step 4: Stage**
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-12-disk-shortfall-findings.md docs/plans/2026-10-12-bak-release-verify-design.md docs/plans/2026-10-12-bak-release-verify.md docs/plans/2026-10-12-bak-release-verify.plan.json
```
Expected: no output.
