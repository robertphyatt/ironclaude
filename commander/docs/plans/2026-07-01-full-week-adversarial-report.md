# Adversarial Review Report — Full Week (Jun 18 – Jul 1, 2026)

> **Date:** 2026-07-01
> **Review:** B (blind, independent of v1.0.14-only round 4)
> **Base:** 28ac2ea → HEAD (153d99f)
> **Scope:** 60 files, 7831 insertions, 429 deletions
> **Versions covered:** v1.0.12 → v1.0.13 → v1.0.14
> **Method:** Diff-first, 7-category sweep (blind — no anchoring on prior review findings)

## Coverage

- Python source files reviewed: 12/12
- Python test files reviewed: 16/16
- Config/doc/script files reviewed: 15/15 (+ 10 design/research docs scanned for PII)

---

## Findings

### FW-1: Shadow grader path validation uses string `startswith` — prefix collision bypass (Severity: Important)

- **Category:** Security
- **File:** `commander/src/ironclaude/shadow_grader.py:130`
- **Evidence:**
  ```python
  if not any(abs_path.startswith(root) for root in allowed):
  ```
  Where `allowed` includes `os.path.expanduser("~")` (e.g. `/Users/roberthyatt` — no trailing slash).
- **Impact:** A path like `/Users/roberthyattevil/secret` would pass the home-directory startswith check because `"/Users/roberthyattevil/secret".startswith("/Users/roberthyatt")` is `True`. Exploitation requires a sibling directory with a prefix-matching name, which is uncommon but not impossible on multi-user systems. The `/tmp/` root has a trailing slash and is not affected.
- **Fix:** Replace `startswith` with `pathlib.Path.is_relative_to()`, or append `os.sep` to each root before comparison:
  ```python
  if not any(abs_path.startswith(root + os.sep) or abs_path == root for root in allowed):
  ```

### FW-2: Shadow grader `_validate_path` does not resolve symlinks (Severity: Minor)

- **Category:** Security
- **File:** `commander/src/ironclaude/shadow_grader.py:126`
- **Evidence:**
  ```python
  abs_path = os.path.abspath(path)
  ```
  `os.path.abspath` normalizes the path but does not follow symlinks. `os.path.realpath` would resolve them.
- **Impact:** A symlink placed at an allowed path pointing outside allowed roots bypasses the check. Requires prior write access to create the symlink, which the shadow grader does not grant. Risk is low but the fix is trivial.
- **Fix:** Use `os.path.realpath(path)` instead of `os.path.abspath(path)`.

### FW-3: Complexity gate substring bug — "unsuccessful" bypasses open-ended verb rejection (Severity: Important)

- **Category:** Logic bugs
- **File:** `commander/src/ironclaude/orchestrator_mcp.py:1751`
- **Evidence:**
  ```python
  if re.search(rf'\b{verb}\b', obj_lower) and "success" not in obj_lower:
  ```
  The check `"success" not in obj_lower` is intended to allow objectives that define success criteria. But it uses substring matching, so objectives containing "unsuccessful", "success criteria", or "no success" all bypass the gate, even though they are still open-ended.
- **Impact:** Open-ended objectives containing any form of "success" as a substring will pass the complexity gate, potentially producing low-quality Ollama worker output.
- **Fix:** Use word-boundary matching: `not re.search(r'\bsuccess\b', obj_lower)` or, more precisely, check for `"success:"` or `"success criteria"` as the intended pattern.

### FW-4: Complexity gate test encodes the substring bug as expected behavior (Severity: Important)

- **Category:** Test quality
- **File:** `commander/tests/test_orchestrator_mcp.py` — `TestOllamaComplexityGate.test_passes_open_ended_verb_with_success`
- **Evidence:** The test uses `"Success: widget built"` as an objective with an open-ended verb, and asserts it passes. This test will break if FW-3 is fixed — it validates the bug, not the intended behavior.
- **Impact:** Fixing FW-3 will require updating this test. The test currently prevents the bug from being detected as a regression.
- **Fix:** Update the test to use a properly-scoped success criterion (e.g. `"Build widget. Success: compiles without errors"`) so it tests the intended gate behavior.

### FW-5: No test coverage for `validate_safe_id` newline rejection (Severity: Minor)

- **Category:** Test quality
- **File:** `commander/src/ironclaude/protocol.py:16`
- **Evidence:**
  ```python
  _SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+\Z')
  ```
  The `$` → `\Z` fix prevents newline injection in worker/task IDs. However, zero test files reference `validate_safe_id` or `_SAFE_ID_RE`. A regression to `$` would silently reintroduce the vulnerability.
- **Impact:** No regression protection for a security-relevant fix.
- **Fix:** Add a test in `test_protocol.py` (or a new file) verifying that `validate_safe_id("valid\n")` raises `ValueError`.

### FW-6: Plan JSON contains absolute user home paths (Severity: Minor)

- **Category:** PII/personal data
- **File:** `docs/plans/2026-06-20-shadow-grader.plan.json`
- **Evidence:** 20 instances of `/Users/roberthyatt/Code/ironclaude/commander` in step `command` fields. Other plan JSONs in this diff range do not contain absolute paths.
- **Impact:** Username exposed in committed files. Plan JSONs are in the gitignored `docs/` directory but are force-added via `git add -f`. Low severity — plan JSONs are internal artifacts.
- **Fix:** Replace absolute paths with relative paths (`cd commander && ...`) or `$(pwd)` in future plan commands. The existing committed file can be cleaned up or left as-is since it's a historical artifact.

### FW-7: `_chat_via_fallback` timeout inconsistency (Severity: Minor)

- **Category:** Code quality
- **File:** `commander/src/ironclaude/ollama_client.py:84`
- **Evidence:**
  Primary path (line 60): `timeout = (self._connect_timeout, self._timeout)` — tuple with separate connect/read timeouts.
  Fallback path (line 84): `timeout=self._timeout` — single integer used for both connect and read.
- **Impact:** Functional inconsistency. The fallback path uses the read timeout for both phases, which may cause slower connection failure detection on the fallback URL. Not a crash — `requests.post` accepts both int and tuple.
- **Fix:** Use the same tuple format: `timeout=(self._connect_timeout, self._timeout)`.

### FW-8: `cli.py` duplicates PID_FILE constant (Severity: Minor)

- **Category:** Code quality
- **File:** `commander/src/ironclaude/cli.py:9`
- **Evidence:**
  ```python
  _PID_FILE = "/tmp/ic-daemon.pid"
  ```
  Duplicated from `daemon.py`. The commit message documents this as intentional (avoiding daemon module import), but creates maintenance risk if the path changes in one place.
- **Impact:** If the PID file path changes in `daemon.py` but not `cli.py` (or vice versa), `ironclaude restart` will silently fail to find the daemon.
- **Fix:** Extract the constant to a shared lightweight module (e.g. `constants.py`), or add a comment cross-referencing the duplication source.

### FW-9: `list_claude_sessions` hardcodes `"gemma4:9b"` as summarization fallback (Severity: Minor)

- **Category:** Hardcoded values
- **File:** `commander/src/ironclaude/orchestrator_mcp.py:2794,2799`
- **Evidence:**
  ```python
  summarization_model = "gemma4:9b"
  ...
  summarization_model = self._ollama_cfg_cache.get("summarization_model", "gemma4:9b")
  ```
  The model name appears twice — once as the initial default and once as the config fallback.
- **Impact:** If the Ollama model setup changes (e.g. model renamed or removed), this fallback won't update. A config override path exists via `summarization_model`, mitigating the risk.
- **Fix:** Extract to a module-level constant: `_DEFAULT_SUMMARIZATION_MODEL = "gemma4:9b"`.

---

## Categories with No Findings

- **Dead code** — No unreachable branches, unused imports, or dead functions detected in the diff. All new code paths are exercised by tests or runtime flows.

---

## Summary

| Severity | Count | Finding IDs |
|----------|-------|-------------|
| Important | 3 | FW-1, FW-3, FW-4 |
| Minor | 6 | FW-2, FW-5, FW-6, FW-7, FW-8, FW-9 |
| Critical | 0 | — |

**9 findings total.** Three Important-severity findings cluster around two themes: (1) the shadow grader's path validation uses string prefix matching instead of proper path containment (FW-1), and (2) the complexity gate's substring-based "success" check creates a bypass that is encoded as expected behavior in the test suite (FW-3, FW-4). Six Minor findings cover test coverage gaps, code quality inconsistencies, a PII leak in plan files, and hardcoded values.

**Overlap with v1.0.14-only round 4 (Review A):** FW-3 corresponds to AR4-1, FW-5 corresponds to AR4-3 from the prior review. These were independently rediscovered through blind diff reading, confirming their validity. FW-1, FW-2, FW-4, FW-6, FW-7, FW-8, and FW-9 are new findings not present in Review A.

**Positive patterns observed:**
- PII sanitization: "kandice" hostname and `/mnt/c/Users/rober/` paths removed from tests (v1.0.13)
- `conftest.py` autouse fixture prevents IC_* env leaking into tests
- `professional-mode-guard.sh` rewrite closes redirection bypasses (`> <`) across all non-executing stages
- `bash-readonly-guard.sh` shared predicate lib with 36-assertion DB-free unit test
- `test_version_consistency.py` guards against version drift across 3 declaration sites
- Shadow grader has thorough test coverage (20+ tests) including format enforcement, tool calling, and path traversal rejection
