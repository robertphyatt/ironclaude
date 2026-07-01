# Adversarial Review Report — v1.0.14 (Round 4)

> **Date:** 2026-07-01
> **Base:** fd6fd1d → HEAD (c65755f)
> **Scope:** 33 files, 3188 insertions, 56 deletions
> **Method:** Diff-first, 7-category sweep (blind — no anchoring on rounds 1-3)

## Coverage

- Python files reviewed: 13/13 (orchestrator_mcp.py, brain_client.py, cli.py, protocol.py, shadow_grader.py, tmux_manager.py, wiki_tools.py, pyproject.toml + 5 test files)
- Config/doc files reviewed: 20/20 (marketplace.json, plugin.json, ironclaude.json, behavioral.md, CHANGELOG.md, README.md, 7 plan files, 3 research files)

## Findings

### AR4-1: Complexity gate "success" substring admits false negatives (Severity: Important)

- **Category:** Logic bugs
- **File:** `src/ironclaude/orchestrator_mcp.py:1751`
- **Evidence:**
  ```python
  if re.search(rf'\b{verb}\b', obj_lower) and "success" not in obj_lower:
      return False, f"Open-ended verb '{verb}' without explicit success condition"
  ```
- **Impact:** The guard intends to require an explicit success condition (e.g., `"Success: all tests pass"`) when open-ended verbs like `refactor`, `analyze`, `optimize` appear. But `"success" not in obj_lower` is a bare substring test. Any word _containing_ "success" — `"unsuccessful"`, `"successor"`, `"successfully"` — satisfies the check, bypassing the gate without a real success condition.
  
  Example: `"Refactor auth.py — currently unsuccessful at handling edge cases"` passes the gate because `"unsuccessful"` contains `"success"`, even though no explicit success condition is defined.
- **Test gap:** `test_passes_open_ended_verb_with_success` (line 5917) only tests the happy path with a real `"Success:"` prefix — no test covers substring false negatives.
- **Fix:** Replace substring check with a pattern that requires `success` as a standalone condition marker, e.g., `re.search(r'\bsuccess\s*:', obj_lower)` to match `"Success:"` but not `"unsuccessful"`.

---

### AR4-2: TestCallGraderBatch stale after grader polling rewrite (Severity: Important)

- **Category:** Test quality
- **File:** `tests/test_orchestrator_mcp.py:4979, 4992`
- **Evidence:**
  ```python
  # Line 4979 — mocks the OLD API:
  tools.tmux.read_log_tail.side_effect = ["", f"{delimiter}\n{array_json}"]
  
  # But production code now calls:
  # _call_grader → _do_grader_send_and_poll → self.tmux.capture_pane()
  ```
- **Impact:** v1.0.14 rewrote `_do_grader_send_and_poll` (lines 632-735) to use `capture_pane` with `rfind`-based delimiter detection, removing `read_log_tail` from the grading path. `TestCallGraderBatch` still mocks `read_log_tail`, which the production code no longer calls. The mock is never consumed; the unmocked `capture_pane` returns a MagicMock default, causing the polling loop to spin until timeout. The new `TestDoGraderSendAndPoll` class (added in v1.0.14) correctly tests the new implementation, but the old batch tests were not updated.
- **Fix:** Update `TestCallGraderBatch` to mock `capture_pane` instead of `read_log_tail`, matching the v1.0.14 grader polling implementation.

---

### AR4-3: No regression test for validate_safe_id newline fix (Severity: Important)

- **Category:** Test quality (security regression gap)
- **File:** `src/ironclaude/protocol.py:16`
- **Evidence:**
  ```python
  # Changed from $ to \Z:
  _SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+\Z')
  ```
  In Python regex, `$` matches before a trailing newline (`"abc\n"` matches `^[a-zA-Z0-9_-]+$`), while `\Z` matches only at the absolute end. This closes a newline-injection bypass in `validate_safe_id`, which gates the new `rename_session` and `adopt_session` methods.
- **Impact:** The fix is correct and important. However, zero tests exist for `validate_safe_id` anywhere in the test suite (confirmed via `grep -rn validate_safe_id tests/`). A future regression (e.g., reverting to `$`) would go undetected.
- **Fix:** Add test asserting `validate_safe_id("valid-id\n")` raises `ValueError`, and `validate_safe_id("valid-id")` passes.

---

### AR4-4: Normal timeout tests unreachable production state (Severity: Minor)

- **Category:** Test quality
- **File:** `tests/test_brain_client.py` (test_normal_timeout_fires_without_executing_tool)
- **Evidence:** The normal timeout condition in `needs_restart()` (line 759-762) requires:
  1. `_executing_tool == False`
  2. `_last_message_time > _last_response_time`
  3. Elapsed time exceeds `timeout_seconds`

  In production:
  - `send_message()` sets `_last_message_time` then `_executing_tool = True` (lines 704-705)
  - SDK messages set `_last_response_time` to current time (line 658)
  - Text response sets `_executing_tool = False` (line 674)

  After a complete send→response cycle: `_last_response_time > _last_message_time`, so condition 2 is False.
  During a send (before response): `_executing_tool = True`, so `needs_restart` returns at line 746.
  After `restart()`: both reset to 0.0, condition 2 is False.
  
  The condition `_executing_tool == False AND _last_message_time > _last_response_time` cannot occur in normal production flow. The test verifies the branch works if triggered artificially, but doesn't test a reachable state.
- **Impact:** Low — the branch acts as a defensive safety net. The test isn't wrong, but its value as a regression guard is limited since the state it tests can't be produced by the production code path.
- **Fix:** Consider documenting this as a defensive-only branch, or removing the test in favor of the `_executing_tool = True` path tests which cover the reachable timeout behaviors.

## Categories with No Findings

- **Security:** `$`→`\Z` fix is correct. No command injection, path traversal, or secrets exposure in the diff. Input validation present on session adoption paths.
- **PII/personal data:** Zero matches for `roberthyatt`, `robert.p.hyatt`, `@gmail`, or user-specific IPs in committed code or config. Research docs reference `/tmp/` log paths but these are documentation of runtime paths, not committed secrets.
- **Code quality:** New methods are well-structured. `_do_grader_send_and_poll` is large (~100 lines) but coherent — single responsibility, clear control flow.
- **Dead code:** No unreachable branches, unused imports, or unused functions introduced in this diff.
- **Hardcoded values:** `/tmp/grader-debug.log` is gated behind `GRADER_DEBUG` env var and extracted to `_GRADER_DEBUG_LOG` constant. `/tmp/ic-daemon.pid` in cli.py mirrors the daemon constant. No ungated hardcoded paths.

## Summary

4 findings: 0 Critical, 3 Important, 1 Minor. Round 4 found substantive issues — the complexity gate substring bug (AR4-1) is a real logic error in a guardrail, and the stale batch tests (AR4-2) mask whether the grader rewrite works correctly for batch mode. The missing security regression test (AR4-3) leaves a known-good fix unprotected.
