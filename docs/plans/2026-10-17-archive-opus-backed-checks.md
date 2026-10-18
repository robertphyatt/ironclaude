# Archive Abandoned Opus-Backed Checks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Keep the abandoned Opus-backed checks design as a marked record of the rejected option, delete its unexecuted plan files, and stage the record for commit.

**Requirements:** `docs/plans/2026-10-17-archive-opus-backed-checks-design.md`

**Architecture:** This changes only docs. One gitignored design doc gets a new Status line and a short "Why abandoned" section. Two untracked plan files are deleted. The design doc and this loop's docs are force-added.

**Tech Stack:** Markdown, git.

Loop: light.

**Execution invariants:**
- Shell state does not persist between steps, so every command uses absolute paths and `git -C /Users/roberthyatt/Code/ironclaude`.
- `docs/` is gitignored, so staging uses `git add -f`.
- No `2>/dev/null` on evidence commands.

---

## Task 1: Mark the design Abandoned and drop its plan files

No tests required: documentation only.

**Files:**
- Modify: `docs/plans/2026-10-16-opus-backed-checks-keep-opus-design.md:4`
- Delete: `docs/plans/2026-10-16-opus-backed-checks-keep-opus.md`
- Delete: `docs/plans/2026-10-16-opus-backed-checks-keep-opus.plan.json`
- Stage: `docs/plans/2026-10-17-archive-opus-backed-checks-design.md`, `docs/plans/2026-10-17-archive-opus-backed-checks.md`, `docs/plans/2026-10-17-archive-opus-backed-checks.plan.json`

**Step 1: Edit the design doc**

Replace line 4, `> **Status:** Design Complete`, with:

```markdown
> **Status:** Abandoned — superseded by docs/plans/2026-10-16-per-call-difficulty-tiering-design.md (never executed)

## Why abandoned

This design kept Opus as the backup for five Commander checks: the spawn pre-filter, the batch spawn pre-filter, send_to_worker, send_keys and post_message. Before it ran, the operator rejected that approach because it would overuse Opus: "I don't want to overuse opus when times haiku should be better suited for the task". The operator asked instead for every harness LLM call to be tiered by the difficulty of its task. Under that tiering these checks use the Haiku class first and, as fail-closed gates, step up one class to the Sonnet class. Only approve_plan and kill_worker stay on the Opus class. This document is kept so that "why not keep Opus as the backup?" has a recorded answer.
```

Leave the rest of the file unchanged.

**Step 2: Verify the edit**

Run:
```bash
rg -n -e "^> \*\*Status:\*\*" -e "^## Why abandoned" /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-16-opus-backed-checks-keep-opus-design.md
```

Expected: exactly two lines. Line 4 reads `> **Status:** Abandoned — superseded by docs/plans/2026-10-16-per-call-difficulty-tiering-design.md (never executed)`, and line 6 reads `## Why abandoned`. Before the edit, this command printed only `4:> **Status:** Design Complete`.

**Step 3: Delete the two plan files**

Run:
```bash
rm /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-16-opus-backed-checks-keep-opus.md /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-16-opus-backed-checks-keep-opus.plan.json
ls /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-16-opus-backed-checks-keep-opus.md /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-16-opus-backed-checks-keep-opus.plan.json /Users/roberthyatt/Code/ironclaude/docs/plans/2026-10-16-opus-backed-checks-keep-opus-design.md
```

Expected: `ls` prints "No such file or directory" for the `.md` and `.plan.json` files, lists the `-design.md`, and exits 1.

**Step 4: Stage**

Run:
```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-opus-backed-checks-keep-opus-design.md docs/plans/2026-10-17-archive-opus-backed-checks-design.md docs/plans/2026-10-17-archive-opus-backed-checks.md docs/plans/2026-10-17-archive-opus-backed-checks.plan.json
git -C /Users/roberthyatt/Code/ironclaude diff --cached --name-status
```

Expected: exactly four lines, each starting with `A` and naming the four paths above. The deleted plan files were never tracked, so they do not appear.
