# Makefile Dependency Freshness Check Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Auto-install missing Python packages before daemon startup so `make run` never crashes with ModuleNotFoundError after dependency changes.

**Architecture:** Prepend `$(PIP) install -qe .` to the `run` target recipe. pip is a no-op when all deps are satisfied (<1 second). The `&&` chain ensures the daemon only starts if install succeeds.

**Tech Stack:** Make, pip

---

## Task 1: Add pip install to Makefile run target

**Files:**
- Modify: `commander/Makefile:15-16`

**Step 1: Modify the run target**

Edit `commander/Makefile` line 16. Replace:

```makefile
run:
	@set -a && . ./.env && set +a && $(PYTHON) -u -m ironclaude.main 2>&1 | tee -a /tmp/ironclaude-daemon.log
```

With:

```makefile
run:
	@$(PIP) install -qe . && set -a && . ./.env && set +a && $(PYTHON) -u -m ironclaude.main 2>&1 | tee -a /tmp/ironclaude-daemon.log
```

The only change is prepending `$(PIP) install -qe . && ` to the recipe.

**Step 2: Verify the change**

Run:
```bash
head -20 commander/Makefile
```

Expected: Line 16 starts with `@$(PIP) install -qe . && set -a`.

**Step 3: Verify pip install is a no-op when deps are satisfied**

Run:
```bash
cd commander && .venv/bin/pip install -qe . 2>&1; echo "exit: $?"
```

Expected: No output (or minimal), exit code 0, completes in <2 seconds.

No tests required: Makefile recipe change with no test framework for Make targets. Manual verification via Step 2 and Step 3.

**Step 4: Stage changes**

Run:
```bash
git add commander/Makefile
```

Expected: Changes staged (professional mode blocks commit).
