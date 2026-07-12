# CHANGELOG entry for the kill_worker directive fast-path — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Document the already-staged `kill_worker` directive fast-path in the v1.0.20 CHANGELOG so the release note matches what ships.

**Architecture:** Documentation-only. Append one bullet to the existing `### Added` block under `## 1.0.20` in `CHANGELOG.md`. No code, no tests.

**Tech Stack:** Markdown.

---

## Task 1: Add the kill_worker fast-path CHANGELOG bullet

**Files:**
- Modify: `CHANGELOG.md`

**No tests required:** documentation-only CHANGELOG addition (the kill_worker code and its tests are already staged by a concurrent worker; this task adds only the release note).

**Step 1: Append the bullet to the `## 1.0.20` `### Added` block.**

In `CHANGELOG.md`, in the `## 1.0.20` section's existing `### Added` block, immediately after the "Advisor Fallback directive" bullet, add:
```markdown
- **`kill_worker` directive fast-path.** `kill_worker` takes an optional `directive_id`; when that directive's status is already `completed`, the kill is approved immediately and the inline grader is skipped (avoids a redundant Opus grade blocking cleanup of already-confirmed work). Opt-in and backward-compatible — absent `directive_id` the grade-or-warn behavior is unchanged, and a failed directive lookup falls through to grading. Note: this is a deliberate, opt-in relaxation of kill-grader enforcement — there is no worker↔directive linkage, so a caller can skip the kill-grade by pointing at any `completed` directive.
```

**Step 2: Verify the section reads correctly.**

Run:
```bash
sed -n '/## 1.0.20/,/## 1.0.19/p' CHANGELOG.md
```
Expected: the `### Added` block now contains both the Advisor Fallback bullet and the kill_worker fast-path bullet, ahead of `### Changed`.

**Step 3: Confirm version lockstep is still green (unchanged).**

Run:
```bash
cd commander && .venv/bin/python -m pytest tests/test_version_consistency.py -q -p no:cacheprovider
```
Expected: 1 passed (version still 1.0.20 across the three sources).

**Step 4: Stage.**

Run:
```bash
git add CHANGELOG.md
```
