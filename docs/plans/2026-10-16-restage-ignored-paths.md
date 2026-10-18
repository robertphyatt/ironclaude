# Re-stage Gitignored Paths Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Make the cumulative re-stage script stage plan files under gitignored directories without ever newly tracking an ignored file.

**Requirements:** `docs/plans/2026-10-16-restage-ignored-paths-design.md` (§Requirements R1–R3)

**Architecture:** The script's final staging loop handles each path by its state:
- **In the index already** (the script's `tracked` set): stage it with `git add -f`.
- **Untracked and not ignored:** stage it with plain `git add`.
- **Untracked and ignored** (`git check-ignore -q` exits 0): skip it and print a stderr notice.

The prose of the item gains one sentence describing this. The hook test gains a scenario with a gitignored directory.

**Tech Stack:** Python heredoc embedded in Markdown skill text; bash hook test; pytest.

Loop: full. This touches git staging, though it is small: about 2 files and 30 lines.

## Execution invariants

- Use literal absolute paths, and `git -C /Users/roberthyatt/Code/ironclaude`.
- Run pytest with `PYTHONUNBUFFERED=1` and `commander/.venv/bin/python`, from `/Users/roberthyatt/Code/ironclaude/commander`.
- Never stash, commit or push. `docs/` is gitignored, so stage plan files with `git add -f`.
- The Python block in `SKILL.md` keeps its 3-space list indentation (the hook test matches `^   # BEGIN …`) and its BEGIN/END markers.
- Execution subagents must not use the Skill tool.

**Verified at planning time:**

| What | Where / fact |
|---|---|
| Staging loop (uncommitted Step 5 placement) | `worker/skills/executing-plans/SKILL.md:673-677`, `   for relative in sorted(paths):` followed by `subprocess.run([... "add", "--", relative], check=True)`. The `tracked` set is built from `git ls-files -z`, which also lists newly staged files. |
| Item prose | `SKILL.md:610-624`. It contains the line `   optional missing files are skipped. Validate every entry before staging and` (`:622`). |
| Hook test | `worker/hooks/tests/test-skill-handoffs-restaging.sh` has a helper `git(root, *args, check=True)` (`:40-43`) and `run_snippet(root, entries)`, which writes `root.parent / "plan.json"` and runs the dedented snippet (`:70-76`). The Python heredoc ends with the line `PY` (`:139`). |
| `make test-hooks` | `Makefile:40-44` runs `worker/hooks/tests/test-*.sh`, among others. |
| Failure measured on 2026-10-16 | Running the script against this repo's plan: `git add -- docs/plans/2026-10-16-restage-at-wave-boundary-design.md` printed "The following paths are ignored by one of your .gitignore files: docs" and exited 1, although that file was already staged (`A`). |

---

## Task 1: Force-stage only already-tracked paths; skip ignored untracked ones

TDD.

**Files:**
- Modify: `worker/hooks/tests/test-skill-handoffs-restaging.sh` (insert before the final `PY` line, `:139`)
- Modify: `worker/skills/executing-plans/SKILL.md` (`:622`, `:673-677`)
- Stage only: `docs/plans/2026-10-16-restage-ignored-paths-design.md`, `docs/plans/2026-10-16-restage-ignored-paths.md`, `docs/plans/2026-10-16-restage-ignored-paths.plan.json`

### Step 1: Test (RED)

In `worker/hooks/tests/test-skill-handoffs-restaging.sh`, insert this block (column 0, inside the Python heredoc) directly before the final line `PY`:
```python

with tempfile.TemporaryDirectory(prefix="skill-handoff-restaging-ignored-") as temp:
    base = Path(temp)
    root = base / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text("ignored/\n")
    (root / "ignored").mkdir()
    (root / "ignored" / "tracked.md").write_text("old")
    (root / "ignored" / "unlisted.md").write_text("old")
    git(root, "add", "--", ".gitignore")
    git(root, "add", "-f", "--", "ignored/tracked.md", "ignored/unlisted.md")
    git(root, "commit", "-qm", "base")
    (root / "ignored" / "tracked.md").write_text("new")
    (root / "ignored" / "unlisted.md").write_text("new")
    (root / "ignored" / "never-tracked.md").write_text("secret")
    result = run_snippet(root, ["ignored/tracked.md", "ignored/never-tracked.md"])
    assert result.returncode == 0, result.stderr
    staged = git(root, "diff", "--cached", "--name-only", "-z").stdout.split(b"\0")
    assert set(staged) - {b""} == {b"ignored/tracked.md"}, staged
    assert b"ignored/never-tracked.md" not in git(root, "ls-files", "-z").stdout
    assert "ignored/never-tracked.md" in result.stderr, result.stderr
    print("PASS gitignored allowed files: tracked staged with -f, never-tracked skipped with notice, unlisted untouched")
```
RED run:
```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-handoffs-restaging.sh
```
Expected:
- The existing `PASS …` lines print, then the script ends with an `AssertionError` from `assert result.returncode == 0, result.stderr`, and the shell exit code is non-zero.
- The stderr shown contains "paths are ignored by one of your .gitignore files". Plain `git add` refuses a tracked file inside an ignored directory, as measured on 2026-10-16.

Record the measured result.

### Step 2: Implementation (GREEN) in `worker/skills/executing-plans/SKILL.md`

1. Replace these five lines (`:673-677`):
```
   for relative in sorted(paths):
       subprocess.run(
           ["git", "--literal-pathspecs", "-C", str(root), "add", "--", relative],
           check=True,
       )
```
with:
```
   for relative in sorted(paths):
       if relative in tracked:
           # Already in the index: -f refreshes it even under a gitignored directory.
           command = ["git", "--literal-pathspecs", "-C", str(root), "add", "-f", "--", relative]
       else:
           # check-ignore takes literal paths and rejects pathspec magic, so no
           # --literal-pathspecs here (it exits 128 with that flag).
           ignored = subprocess.run(
               ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
           ).returncode
           if ignored == 0:
               # Never newly track an ignored file (a secret, say); only an explicit
               # plan step's `git add -f` may bring one into the index.
               print(f"skipped gitignored untracked allowed_files entry: {relative}", file=sys.stderr)
               continue
           if ignored != 1:
               raise RuntimeError(f"git check-ignore failed for {relative!r}")
           command = ["git", "--literal-pathspecs", "-C", str(root), "add", "--", relative]
       subprocess.run(command, check=True)
```
2. Replace the prose line `   optional missing files are skipped. Validate every entry before staging and` (`:622`) with these three lines:
```
   optional missing files are skipped. Entries already in the index are staged with
   `git add -f`, so gitignored plan files (such as `docs/plans/*`) refresh; an untracked
   entry that git ignores is skipped with a notice and never force-added. Validate every entry before staging and
```

### Step 3: GREEN plus regression

```bash
bash /Users/roberthyatt/Code/ironclaude/worker/hooks/tests/test-skill-handoffs-restaging.sh > /private/tmp/claude-502/ri-hook.out 2>&1; echo "hook=$?"; tail -n 2 /private/tmp/claude-502/ri-hook.out; grep -c "BEGIN EXACT ALLOWED FILES RESTAGING" /Users/roberthyatt/Code/ironclaude/worker/skills/executing-plans/SKILL.md; make -C /Users/roberthyatt/Code/ironclaude test-hooks > /private/tmp/claude-502/ri-hooks-all.out 2>&1; echo "hooks_all=$?"; tail -n 2 /private/tmp/claude-502/ri-hooks-all.out; cd /Users/roberthyatt/Code/ironclaude/commander && PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/ -q > /private/tmp/claude-502/ri-suite.out 2>&1; echo "suite=$?"; tail -n 3 /private/tmp/claude-502/ri-suite.out
```
Expected:
- `hook=0`, with the last line `PASS gitignored allowed files: tracked staged with -f, never-tracked skipped with notice, unlisted untouched`;
- the marker count is `1`;
- `hooks_all=0`;
- `suite=0`, with 0 failed.

Any other failure must be shown pre-existing by running it in a temporary `git worktree` of `HEAD` (never `git stash`), or else fixed.

### Step 4: Stage

```bash
git -C /Users/roberthyatt/Code/ironclaude add worker/skills/executing-plans/SKILL.md worker/hooks/tests/test-skill-handoffs-restaging.sh; git -C /Users/roberthyatt/Code/ironclaude add -f docs/plans/2026-10-16-restage-ignored-paths-design.md docs/plans/2026-10-16-restage-ignored-paths.md docs/plans/2026-10-16-restage-ignored-paths.plan.json
```
Expected: no output.
