# v1.0.14 Adversarial Round 3 Bug Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Fix 2 bugs found in v1.0.14 adversarial review round 3: inverted has_session semantics in resume_session, and broken test referencing nonexistent attribute.

**Architecture:** Two independent bug fixes in orchestrator_mcp.py. Task 1 fixes control flow in resume_session. Task 2 extracts a module-level constant and updates the test to match.

**Tech Stack:** Python (pytest)

---

## Task 1: Fix resume_session inverted has_session semantics

No tests required: Bug fix aligns resume_session with the identical spawn_worker pattern at line 1984. The not-ready path in resume_session has no direct unit tests — the fix is a 2-line change matching an established, tested pattern.

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:2932-2937`

**Step 1: Fix the condition and remove dead kill_session call**

At line 2932, change:
```python
            if self.tmux.has_session(target):
                self.tmux.kill_session(target)
                return {"error": f"Worker '{worker_id}' died before ready.\nLast output:\n{log_tail}"}
```

To:
```python
            if not self.tmux.has_session(target):
                return {"error": f"Worker '{worker_id}' died before ready.\nLast output:\n{log_tail}"}
```

This matches spawn_worker at line 1984: `if not self.tmux.has_session(session_name, ssh_host=ssh_host):` — session is dead, return error. The else branch (session alive but slow) already has the correct warning log.

**Step 2: Run tests to verify no regressions**

Run:
```bash
cd commander && python -m pytest tests/test_orchestrator_mcp.py -v -x
```

Expected: All tests pass.

**Step 3: Stage changes**

Run:
```bash
git add commander/src/ironclaude/orchestrator_mcp.py
```

Expected: Changes staged.

---

## Task 2: Extract _GRADER_DEBUG_LOG constant and fix debug log test

No separate RED/GREEN cycle: the test file IS the thing being fixed. The test currently references a nonexistent attribute and would raise AttributeError. Fix makes it testable against the real production gating.

**Files:**
- Modify: `commander/src/ironclaude/orchestrator_mcp.py:55-56,663,695,707,734`
- Modify: `commander/tests/test_orchestrator_debug_log.py:29`

**Step 1: Add _GRADER_DEBUG_LOG constant to orchestrator_mcp.py**

After line 55 (`_GRADER_DEBUG = bool(os.environ.get('GRADER_DEBUG'))`), add:
```python
_GRADER_DEBUG_LOG = '/tmp/grader-debug.log'
```

**Step 2: Replace hardcoded paths with constant in 4 debug blocks**

Replace all 4 occurrences of `'/tmp/grader-debug.log'` with `_GRADER_DEBUG_LOG`:

Line 663:
```python
                    with open(_GRADER_DEBUG_LOG, 'a') as _dbg:
```

Line 695:
```python
                        with open(_GRADER_DEBUG_LOG, 'a') as _dbg:
```

Line 707:
```python
                            with open(_GRADER_DEBUG_LOG, 'a') as _dbg:
```

Line 734:
```python
                                with open(_GRADER_DEBUG_LOG, 'a') as _dbg:
```

**Step 3: Fix test to monkeypatch both _GRADER_DEBUG and _GRADER_DEBUG_LOG**

In `commander/tests/test_orchestrator_debug_log.py`, line 29, replace:
```python
        monkeypatch.setattr(orch_mod, "_GRADER_DEBUG_LOG", str(debug_log))
```

With:
```python
        monkeypatch.setattr(orch_mod, "_GRADER_DEBUG", True)
        monkeypatch.setattr(orch_mod, "_GRADER_DEBUG_LOG", str(debug_log))
```

The test already creates `debug_log = tmp_path / "grader-debug.log"` and passes it via monkeypatch. Adding `_GRADER_DEBUG = True` ensures the debug blocks execute. The `_GRADER_DEBUG_LOG` monkeypatch now targets a real attribute.

**Step 4: Run the debug log test**

Run:
```bash
cd commander && python -m pytest tests/test_orchestrator_debug_log.py -v
```

Expected: 1 test passes.

**Step 5: Run full test suite**

Run:
```bash
cd commander && python -m pytest tests/ -v -x
```

Expected: All tests pass.

**Step 6: Stage changes**

Run:
```bash
git add commander/src/ironclaude/orchestrator_mcp.py commander/tests/test_orchestrator_debug_log.py
```

Expected: Changes staged.
