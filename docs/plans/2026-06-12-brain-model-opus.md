# Brain Model: Fable → Opus Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Change brain_model from "fable" to "opus" in config/ironclaude.json so the daemon uses an available model.

**Architecture:** Single config value change. File is gitignored; requires `git add -f` to stage.

**Tech Stack:** JSON config

---

## Task 1: Update brain_model config value

**Files:**
- Modify: `config/ironclaude.json`

No tests required: pure config file with no test framework; verified by reading file after edit.

**Step 1: Edit `config/ironclaude.json` to change brain_model from "fable" to "opus"**

Use Edit tool with:
- `old_string`: `"brain_model": "fable"`
- `new_string`: `"brain_model": "opus"`

**Step 2: Force-stage the gitignored config file**

```bash
git add -f config/ironclaude.json
```

Expected: file staged successfully.

**Step 3: Stage plan and design documents**

```bash
git add docs/plans/2026-06-12-brain-model-opus-design.md docs/plans/2026-06-12-brain-model-opus.md docs/plans/2026-06-12-brain-model-opus.plan.json
```

Expected: all plan files staged.
