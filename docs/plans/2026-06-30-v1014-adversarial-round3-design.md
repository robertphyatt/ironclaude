# v1.0.14 Adversarial Review Round 3 Fixes Design

> **Created:** 2026-06-30
> **Status:** Design Complete

## Summary

Fix 2 bugs found during blind adversarial review round 3 of the v1.0.14 commit. Both are verified with specific line references and established correct patterns in the codebase.

## Components

### Bug 1: `resume_session` inverted `has_session` semantics

**File:** `commander/src/ironclaude/orchestrator_mcp.py:2929-2937`

**Root cause:** Round 1 fix inverted the condition from `if not self.tmux.has_session(target):` to `if self.tmux.has_session(target):` to prevent calling `kill_session` on a dead session. This fixed the crash but inverted the semantics — now it kills alive sessions and proceeds on dead ones.

**Correct pattern:** `spawn_worker` at line 1984 uses `if not self.tmux.has_session(...)` → return error (session dead). The else branch logs a warning and proceeds (session alive but slow).

**Fix:** Revert condition to `if not self.tmux.has_session(target):` and remove `self.tmux.kill_session(target)` from that branch (session is already dead, no need to kill). The error message "died before ready" is correct for this branch. The else branch already has the correct warning log.

### Bug 2: `test_orchestrator_debug_log.py` references nonexistent attribute

**File:** `commander/tests/test_orchestrator_debug_log.py:29`

**Root cause:** Test was written against the old `_GRADER_DEBUG_LOG` constant (a string path). Round 1 changed the production code to `_GRADER_DEBUG = bool(os.environ.get('GRADER_DEBUG'))` — a boolean gated by env var. The test was never updated to match.

**Fix:** Rewrite the test to:
1. Use `monkeypatch.setenv("GRADER_DEBUG", "1")` to enable debug logging
2. Reload the module-level `_GRADER_DEBUG` flag via `monkeypatch.setattr(orch_mod, "_GRADER_DEBUG", True)`
3. The production code hardcodes `/tmp/grader-debug.log` — redirect by patching `builtins.open` or use `tmp_path` with a monkeypatched path in the write calls
4. Verify debug output is written when the flag is True

Simpler approach: since the production code hardcodes the path `/tmp/grader-debug.log` in 4 `open()` calls inside `if _GRADER_DEBUG:` blocks, the test should:
1. Set `_GRADER_DEBUG = True` via monkeypatch
2. Patch the hardcoded path string by replacing it in the `open()` calls, OR just let the test write to `/tmp/grader-debug.log` and read it back (the tmp_path approach in the existing test won't work since the path is hardcoded)

Cleanest fix: extract the debug log path to a module-level constant `_GRADER_DEBUG_LOG = '/tmp/grader-debug.log'` and use it in all 4 write blocks. Then the test can monkeypatch both `_GRADER_DEBUG` (True) and `_GRADER_DEBUG_LOG` (tmp_path). This restores the attribute the test expects while keeping the env-var gating.

## Testing Strategy

- Bug 1: Existing `test_wait_for_ready_false_dead_session_returns_error` and `test_wait_for_ready_false_alive_session_proceeds` in test_orchestrator_mcp.py already test the spawn_worker pattern. The resume_session fix aligns with that pattern. Run full test suite to verify.
- Bug 2: The test itself IS the verification — fix it so it passes, then run it.

## Implementation Notes

- Bug 1 is a 2-line change (revert condition, remove kill_session call)
- Bug 2 requires: (a) add `_GRADER_DEBUG_LOG` constant back to orchestrator_mcp.py pointing to `/tmp/grader-debug.log`, (b) use it in the 4 debug write blocks, (c) update test to monkeypatch both `_GRADER_DEBUG` and `_GRADER_DEBUG_LOG`
